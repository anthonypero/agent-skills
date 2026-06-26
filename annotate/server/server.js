'use strict';

// Local HTTP server — the single disk authority (tech-requirements §2.2, §5.4, §5.5,
// §6.3, §6.6). Serves the chrome + target, renders text artifacts via render.js (T2),
// and OWNS every round-file mutation. Loopback-bound; Origin/Host + per-session-token
// checked on state-changing routes; round dirs 0700 (§6.3 transport security).
//
// Routes (§6.3):
//   GET  /<session>/<artifact>            -> render the head round's snapshot (T2)
//   GET  /<session>/<artifact>/head       -> { head, status, changeToken } (auto-advance poll)
//   GET  /<session>/<artifact>/snapshot   -> raw snapshot bytes (image viewer / static)
//   GET  /resolve?url=<live-url>          -> { session, artifact, head } (live-page map, §6.4)
//   GET  /static/*                        -> extension assets (static serving, §6.3)
//   GET  /loaded                          -> load-probe heartbeat ack (setup probe, §6.6)
//   POST /loaded                          -> extension load-probe ping (setup probe, §6.6)
//   POST /feedback                        -> splice the §5.5 bundle into the head round
//   POST /<session>/<artifact>/accept     -> head-checked finalize -> accepted
//
// Success/error shapes (§5.5):
//   /feedback : 200 {status:'submitted',head} | 409 {error:'stale-head',head} | 400 | 403
//   /accept   : 200 {status:'accepted'}       | 409 {error,head}              | 403
//
// CONFIG (§6.6): the server reads runtime.json (port, paths.data, browser{path,kind},
// cftBuildId). T7 generates the real one; tests inject `config`/`dataDir` directly.

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const Ajv = require('ajv');

const P = require('./protocol.js');
const { render } = require('./render.js');

const PKG_ROOT = path.join(__dirname, '..');
const EXTENSION_DIR = path.join(PKG_ROOT, 'extension');
const FEEDBACK_SCHEMA = path.join(PKG_ROOT, 'schemas', 'feedback.schema.json');

const HOST = '127.0.0.1';
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1', '[::1]', '0:0:0:0:0:0:0:1']);
const MAX_BODY = 32 * 1024 * 1024; // 32 MB — room for a base64 viewport screenshot

const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.ico']);
const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.bmp': 'image/bmp',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
};

// ---------------------------------------------------------------------------
// runtime.json (§6.6)
// ---------------------------------------------------------------------------

function loadRuntime(runtimePath) {
  const config = P.readJSON(runtimePath);
  return config;
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

function sendJSON(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

function sendHTML(res, code, html) {
  res.writeHead(code, {
    'Content-Type': 'text/html; charset=utf-8',
    'Content-Length': Buffer.byteLength(html),
  });
  res.end(html);
}

function sendBytes(res, code, buf, contentType) {
  res.writeHead(code, { 'Content-Type': contentType, 'Content-Length': buf.length });
  res.end(buf);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(Object.assign(new Error('payload-too-large'), { code: 'TOO_LARGE' }));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

// ---------------------------------------------------------------------------
// Security — §6.3 transport security. State-changing routes require:
//   (1) an Origin (if present) on a loopback origin,
//   (2) a Host on a loopback host (defeats DNS-rebinding),
//   (3) a valid per-session token in X-Annotate-Token.
// A visited site (evil.com) carries Origin: https://evil.com -> rejected; it also
// can't know the per-session token. The loopback Origin allowlist still permits a
// legitimate live dev page (http://localhost:3000) whose content script POSTs here.
// ---------------------------------------------------------------------------

function hostnameOf(value) {
  if (!value) return null;
  // Origin is a full URL; Host is "<host>[:port]". Normalize both to a hostname.
  if (value.includes('://')) {
    try {
      return new URL(value).hostname;
    } catch {
      return null;
    }
  }
  // strip a trailing :port (but keep bracketed IPv6 intact)
  if (value.startsWith('[')) return value.slice(0, value.indexOf(']') + 1);
  const i = value.lastIndexOf(':');
  return i > 0 ? value.slice(0, i) : value;
}

function originOk(originHeader) {
  if (!originHeader) return true; // absent (non-browser client / same-origin GET): defer to Host+token
  const h = hostnameOf(originHeader);
  return h != null && LOOPBACK_HOSTS.has(h);
}

function hostOk(hostHeader) {
  if (!hostHeader) return true;
  const h = hostnameOf(hostHeader);
  return h != null && LOOPBACK_HOSTS.has(h);
}

// Returns null if authorized, else the 403 reason string.
function authorize(req, dataDir, session) {
  if (!originOk(req.headers['origin'])) return 'bad-origin';
  if (!hostOk(req.headers['host'])) return 'bad-host';
  const expected = P.readToken(P.sessionDir(dataDir, session));
  const got = req.headers['x-annotate-token'];
  if (!expected || !got || got !== expected) return 'bad-token';
  return null;
}

// ---------------------------------------------------------------------------
// Rendering the GET <session>/<artifact> page
// ---------------------------------------------------------------------------

function wrapDocument(inner) {
  return (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<title>annotate</title></head><body>' +
    inner +
    '</body></html>'
  );
}

// Config injection (§6.4 "server discovery / config acquisition" — the T6a integration
// crux). The content script runs in an MV3 ISOLATED world (shares the DOM, not the page's
// JS globals), so config is delivered as a DOM-readable JSON <script> the content script
// reads via document.getElementById('annotate-config').textContent.
//
// SECURITY (preserves §6.3): the per-session token is embedded ONLY in pages served from
// the loopback origin for that session. A cross-origin attacker (evil.com) cannot READ the
// body of a cross-origin GET to our loopback server (same-origin policy makes the response
// opaque — no CORS headers are sent), so the token is exposed only to same-origin loopback
// clients (the extension), which are exactly the trusted ones. The Origin/Host/token checks
// on the mutation routes are UNCHANGED — this only delivers the token §5.5 already says is
// "carried in the URL or an X-Annotate-Token header" to its legitimate same-origin holder.
function configScript(cfg) {
  // Escape `<` so the JSON can never break out of the <script> element.
  const json = JSON.stringify(cfg).replace(/</g, '\\u003c');
  return `<script type="application/json" id="annotate-config">${json}</script>`;
}

function injectIntoDoc(html, tag) {
  if (/<\/body>/i.test(html)) return html.replace(/<\/body>/i, `${tag}</body>`);
  if (/<\/head>/i.test(html)) return html.replace(/<\/head>/i, `${tag}</head>`);
  return html + tag;
}

function renderHeadPage(dataDir, session, artifact, query) {
  const aDir = P.artifactDir(dataDir, session, artifact);
  const head = P.resolveHead(aDir);
  if (!head) return { code: 404, html: wrapDocument('<p>annotate: no rounds for this artifact.</p>') };

  const rDir = path.join(aDir, head);
  const snapPath = P.findSnapshot(rDir, head);

  // The content-script config (§6.4): session/artifact/head + the per-session token the
  // extension carries on its mutation POSTs (§5.5). Read server-side; injected DOM-readable.
  const token = P.readToken(P.sessionDir(dataDir, session)) || '';
  const cfgTag = configScript({ session, artifact, head, token });

  // Live page (no byte-copy snapshot, §5.3): serve a minimal attach placeholder.
  if (!snapPath) {
    return {
      code: 200,
      html: wrapDocument(
        `<div class="annotate-live" data-session="${session}" data-artifact="${artifact}" data-head="${head}">` +
          '<p>annotate: live-page round (no snapshot file). The extension attaches in place.</p></div>' +
          cfgTag
      ),
    };
  }

  const ext = path.extname(snapPath).toLowerCase();

  // Image artifacts get a viewer over the raw snapshot bytes (§6.3); spatial anchors
  // are owned by the content-script image adapter (§6.4), not the renderer.
  if (IMAGE_EXTS.has(ext)) {
    const src = `/${encodeURIComponent(session)}/${encodeURIComponent(artifact)}/snapshot`;
    return {
      code: 200,
      html: wrapDocument(
        `<div class="annotate-render annotate-image"><img src="${src}" alt="annotate snapshot" ` +
          `data-head="${head}" style="max-width:100%;height:auto"></div>` +
          cfgTag
      ),
    };
  }

  // Text / structured artifact -> the position-preserving renderer (T2). The render
  // mode (code vs frontend) is NOT in the 4-field descriptor; the launch script passes
  // it as ?render=render-as-frontend|render-as-code (see SPEC-GAP note in the report).
  const renderMode = query.get('render') || undefined;
  const { html } = render(snapPath, renderMode);
  if (renderMode === 'render-as-frontend') {
    // pass-through: a full HTML doc the browser executes; weave config into the doc.
    return { code: 200, html: injectIntoDoc(html, cfgTag) };
  }
  return {
    code: 200,
    html: wrapDocument(`<div class="annotate-target" data-head="${head}">${html}</div>${cfgTag}`),
  };
}

// ---------------------------------------------------------------------------
// POST /feedback — the §5.5 contract. Sole writer; atomic (§2.4).
// ---------------------------------------------------------------------------

function makeFeedbackHandler(dataDir, validateAnchor) {
  return async function feedback(req, res) {
    let body;
    try {
      const raw = await readBody(req);
      body = JSON.parse(raw.toString('utf8'));
    } catch (e) {
      if (e && e.code === 'TOO_LARGE') return sendJSON(res, 413, { error: 'payload-too-large' });
      return sendJSON(res, 400, { error: 'invalid-json' });
    }

    const { session, artifact, head: believedHead, anchors, revertTarget, screenshot, nonce } = body || {};
    if (!session || !artifact || !nonce) {
      return sendJSON(res, 400, { error: 'missing-fields' });
    }

    // (1) Auth FIRST (§6.3): a forged cross-origin / token-less POST learns nothing.
    const denial = authorize(req, dataDir, session);
    if (denial) return sendJSON(res, 403, { error: 'forbidden', reason: denial });

    const aDir = P.artifactDir(dataDir, session, artifact);
    const head = P.resolveHead(aDir);

    // (2) Head-staleness check (§5.5): the believed head must be the current head.
    if (!head || believedHead !== head) {
      return sendJSON(res, 409, { error: 'stale-head', head });
    }

    const rDir = path.join(aDir, head);
    const roundFile = P.roundJsonIn(rDir, head);

    // (3) Idempotency (§5.5): a replayed nonce returns the prior 200 WITHOUT
    //     re-splicing. This precedes the pending check because a submit does not
    //     advance the head — after the first apply the round is `submitted`, so a
    //     genuine replay would otherwise be rejected as non-pending.
    const noncePath = P.nonceIn(rDir, head);
    if (P.exists(noncePath)) {
      try {
        if (fs.readFileSync(noncePath, 'utf8').trim() === String(nonce)) {
          return sendJSON(res, 200, { status: 'submitted', head });
        }
      } catch {
        /* unreadable sidecar -> fall through and treat as not-yet-applied */
      }
    }

    let round;
    try {
      round = P.readJSON(roundFile);
    } catch {
      return sendJSON(res, 409, { error: 'stale-head', head });
    }

    // (4) Non-pending head (a DIFFERENT prior submit, or accepted): cannot splice onto
    //     a closed round (§5.5) -> 409.
    if (round.status !== 'pending') {
      return sendJSON(res, 409, { error: 'stale-head', head });
    }

    // (5) Schema-validate every anchor (§5.5) -> 400 without touching the file.
    const list = Array.isArray(anchors) ? anchors : [];
    for (const item of list) {
      if (!validateAnchor(item)) {
        return sendJSON(res, 400, { error: 'invalid-anchor', details: validateAnchor.errors });
      }
    }

    // (6) Splice + write screenshot + set snapshot pointer (revert) + flip status.
    if (screenshot != null) {
      try {
        P.atomicWriteFile(P.screenshotIn(rDir, head), Buffer.from(String(screenshot), 'base64'));
      } catch {
        /* a bad screenshot must not corrupt the round; the anchors still apply */
      }
    }
    round.feedback = list;
    if (revertTarget != null) round.snapshot = revertTarget; // §5.4 pointer-set (null leaves it null)
    round.status = 'submitted';
    P.atomicWriteJSON(roundFile, round); // atomic (§2.4)

    // (7) Record the applied nonce LAST, so a crash before this point re-applies
    //     cleanly rather than silently dropping the submit.
    P.atomicWriteFile(noncePath, `${nonce}\n`);

    return sendJSON(res, 200, { status: 'submitted', head });
  };
}

// ---------------------------------------------------------------------------
// POST /<session>/<artifact>/accept — head-checked finalize (§5.5, §6.3, §2.7).
// Allowed from `pending` (accept-on-first-look) OR `submitted`. 409 on a stale head
// OR an already-`accepted` (already-resolved) round.
// ---------------------------------------------------------------------------

function makeAcceptHandler(dataDir) {
  return async function accept(req, res, session, artifact) {
    let body = {};
    try {
      const raw = await readBody(req);
      if (raw.length) body = JSON.parse(raw.toString('utf8'));
    } catch (e) {
      if (e && e.code === 'TOO_LARGE') return sendJSON(res, 413, { error: 'payload-too-large' });
      return sendJSON(res, 400, { error: 'invalid-json' });
    }

    const denial = authorize(req, dataDir, session);
    if (denial) return sendJSON(res, 403, { error: 'forbidden', reason: denial });

    const aDir = P.artifactDir(dataDir, session, artifact);
    const head = P.resolveHead(aDir);
    if (!head || body.head !== head) {
      return sendJSON(res, 409, { error: 'stale-head', head });
    }

    const roundFile = P.roundJsonIn(path.join(aDir, head), head);
    let round;
    try {
      round = P.readJSON(roundFile);
    } catch {
      return sendJSON(res, 409, { error: 'stale-head', head });
    }

    // Already finalized -> 409 (already-resolved). Accept-on-first-look is allowed
    // from `pending`, so pending/submitted both proceed (§5.5, §2.7).
    if (round.status === 'accepted') {
      return sendJSON(res, 409, { error: 'already-accepted', head });
    }

    round.status = 'accepted';
    P.atomicWriteJSON(roundFile, round);
    return sendJSON(res, 200, { status: 'accepted', head });
  };
}

// ---------------------------------------------------------------------------
// GET helpers
// ---------------------------------------------------------------------------

function headInfo(dataDir, session, artifact, res) {
  const aDir = P.artifactDir(dataDir, session, artifact);
  const head = P.resolveHead(aDir);
  if (!head) return sendJSON(res, 404, { error: 'no-head' });
  const roundFile = P.roundJsonIn(path.join(aDir, head), head);
  let status = 'pending';
  let mtimeMs = 0;
  try {
    status = P.readJSON(roundFile).status;
    mtimeMs = fs.statSync(roundFile).mtimeMs;
  } catch {
    /* leave defaults */
  }
  // A cheap change token: detects both a head advance (guid change) AND an in-place
  // pending->submitted flip on the same guid (status + mtime), per §6.3.
  const changeToken = `${head}:${status}:${Math.round(mtimeMs)}`;
  return sendJSON(res, 200, { head, status, changeToken });
}

function snapshotBytes(dataDir, session, artifact, res) {
  const aDir = P.artifactDir(dataDir, session, artifact);
  const head = P.resolveHead(aDir);
  if (!head) return sendJSON(res, 404, { error: 'no-head' });
  const snapPath = P.findSnapshot(path.join(aDir, head), head);
  if (!snapPath) return sendJSON(res, 404, { error: 'no-snapshot' });
  const ext = path.extname(snapPath).toLowerCase();
  const ct = CONTENT_TYPES[ext] || 'application/octet-stream';
  return sendBytes(res, 200, fs.readFileSync(snapPath), ct);
}

function serveStatic(rel, res) {
  // Static serving of extension assets (§6.3), with a path-traversal guard.
  const target = path.normalize(path.join(EXTENSION_DIR, rel));
  if (target !== EXTENSION_DIR && !target.startsWith(EXTENSION_DIR + path.sep)) {
    return sendJSON(res, 403, { error: 'forbidden' });
  }
  let buf;
  try {
    buf = fs.readFileSync(target);
  } catch {
    return sendJSON(res, 404, { error: 'not-found' });
  }
  const ct = CONTENT_TYPES[path.extname(target).toLowerCase()] || 'application/octet-stream';
  return sendBytes(res, 200, buf, ct);
}

// ---------------------------------------------------------------------------
// Request router
// ---------------------------------------------------------------------------

function makeHandler(opts) {
  const dataDir = opts.dataDir;
  // The load-probe heartbeat (§6.6): the extension POSTs /loaded on injection; setup
  // polls GET /loaded. Kept in-memory — it is a liveness signal, not durable state.
  const loadedPings = [];

  const ajv = new Ajv({ allErrors: true, strict: false });
  const validateAnchor = ajv.compile(P.readJSON(FEEDBACK_SCHEMA));

  const feedbackHandler = makeFeedbackHandler(dataDir, validateAnchor);
  const acceptHandler = makeAcceptHandler(dataDir);

  return function handler(req, res) {
    let parsed;
    try {
      parsed = new URL(req.url, `http://${HOST}`);
    } catch {
      return sendJSON(res, 400, { error: 'bad-url' });
    }
    const pathname = decodeURIComponent(parsed.pathname);
    const method = req.method;

    Promise.resolve()
      .then(() => route(req, res, method, pathname, parsed.searchParams))
      .catch((err) => {
        if (!res.headersSent) sendJSON(res, 500, { error: 'internal', message: String(err && err.message) });
      });

    function route(req, res, method, pathname, query) {
      // --- load-probe heartbeat (§6.6) ---
      if (pathname === '/loaded') {
        if (method === 'POST') {
          loadedPings.push(Date.now());
          return sendJSON(res, 200, { ok: true });
        }
        if (method === 'GET') {
          return sendJSON(res, 200, { loaded: loadedPings.length > 0, count: loadedPings.length });
        }
        return sendJSON(res, 405, { error: 'method-not-allowed' });
      }

      // --- static assets (§6.3) ---
      if (method === 'GET' && pathname.startsWith('/static/')) {
        return serveStatic(pathname.slice('/static/'.length), res);
      }

      // --- live-page URL resolution (§6.4) ---
      if (method === 'GET' && pathname === '/resolve') {
        const url = query.get('url');
        const m = url ? P.resolveUrl(dataDir, url) : null;
        if (!m) return sendJSON(res, 404, { error: 'unknown-url' });
        const head = P.resolveHead(P.artifactDir(dataDir, m.session, m.artifact));
        return sendJSON(res, 200, { session: m.session, artifact: m.artifact, head });
      }

      // --- POST /feedback (§5.5) ---
      if (method === 'POST' && pathname === '/feedback') {
        return feedbackHandler(req, res);
      }

      // --- /<session>/<artifact>[/sub] routes ---
      const parts = pathname.split('/').filter(Boolean);

      if (parts.length === 3 && parts[2] === 'accept' && method === 'POST') {
        return acceptHandler(req, res, parts[0], parts[1]);
      }
      if (parts.length === 3 && parts[2] === 'head' && method === 'GET') {
        return headInfo(dataDir, parts[0], parts[1], res);
      }
      if (parts.length === 3 && parts[2] === 'snapshot' && method === 'GET') {
        return snapshotBytes(dataDir, parts[0], parts[1], res);
      }
      if (parts.length === 2 && method === 'GET') {
        const out = renderHeadPage(dataDir, parts[0], parts[1], query);
        return sendHTML(res, out.code, out.html);
      }

      return sendJSON(res, 404, { error: 'not-found' });
    }
  };
}

// ---------------------------------------------------------------------------
// Lifecycle — §6.1/§6.6: a lazy singleton, started by the launch script, on the
// configured port. T7 supplies runtime.json; tests inject config/dataDir/port.
// ---------------------------------------------------------------------------

function start(opts = {}) {
  let config = opts.config;
  if (!config && opts.runtimePath) config = loadRuntime(opts.runtimePath);
  config = config || {};

  const dataDir = opts.dataDir || (config.paths && config.paths.data) || P.defaultDataDir();
  const port = opts.port != null ? opts.port : config.port != null ? config.port : 7878;

  P.ensureDir(dataDir, 0o700);

  const server = http.createServer(makeHandler({ dataDir, config }));

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, HOST, () => {
      server.removeListener('error', reject);
      const actualPort = server.address().port;
      resolve({
        server,
        host: HOST,
        port: actualPort,
        url: `http://${HOST}:${actualPort}`,
        dataDir,
        config,
        close: () => new Promise((r) => server.close(() => r())),
      });
    });
  });
}

module.exports = {
  start,
  makeHandler,
  loadRuntime,
  HOST,
};

// Allow `node server/server.js [runtime.json]` to start the server directly.
if (require.main === module) {
  const runtimePath = process.argv[2] || path.join(P.defaultDataDir(), 'runtime.json');
  start({ runtimePath })
    .then((s) => {
      // eslint-disable-next-line no-console
      console.log(`annotate server listening on ${s.url} (data: ${s.dataDir})`);
    })
    .catch((err) => {
      // eslint-disable-next-line no-console
      console.error(`annotate server failed to start: ${err && err.message}`);
      process.exit(1);
    });
}
