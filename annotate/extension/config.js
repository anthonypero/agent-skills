'use strict';

// Config discovery + server-channel wiring (tech-requirements §6.4 "server discovery",
// §5.5 wire contracts, §6.6 load-probe; T6a).
//
// THE INTEGRATION CRUX: how the content script obtains { session, artifact, head, token,
// origin }. The server injects a DOM-readable config blob into every served artifact page
// (server.js configScript) because an MV3 content script runs in an ISOLATED world — it
// shares the DOM with the page but NOT the page's JS globals, so config can't ride a
// `window.__x` global; it must be a DOM node the content script reads. This module parses
// that node and builds the real fetch-backed sinks that replace T3's stand-in.
//
// MODULE STRATEGY (matches dom/code/bubble/submit, see submit.js header): a dependency-free
// UMD-ish file — require()-able by Node unit tests now, and loadable as an MV3 content
// script where it attaches to globalThis.Annotate.config. Every network call takes an
// injectable `fetchImpl` so the pure parsing/URL logic is unit-testable without a browser.

(function (root) {
  'use strict';

  const CONFIG_ID = 'annotate-config';
  // Live-page default annotate origin (the fixed runtime.json port, §6.6). Setup (T7) may
  // template a non-default port in; served pages never use this (they use location.origin).
  const DEFAULT_ORIGIN = 'http://127.0.0.1:7878';

  // Screenshot gating (§6.4): capture is "gated by what is DISPLAYED, not a per-format
  // allowlist". A viewport screenshot adds the VISUAL leg only where the snapshot does not
  // already capture the visual presentation — a rendered visual view (an image, a
  // Markdown-rendered-to-HTML doc, a live frontend). Source-coordinate views (code, the
  // structured/CSV data renders) ARE their own faithful record, so they never capture.
  // (Per the T6b task contract the image view captures; §6.4's prose additionally excludes
  // images — flagged as a SPEC-GAP in the build log. The task/exit-gate wins here.)
  const VISUAL_VIEWS = new Set(['image', 'markdown', 'frontend']);

  // shouldCaptureScreenshot(viewKind, toggleOn) -> boolean. The persistent toggle
  // (chrome.storage.local, default ON) gates ON TOP of the view check; a non-visual view
  // never captures regardless of the toggle (the toggle is "inert on non-visual views").
  function shouldCaptureScreenshot(viewKind, toggleOn) {
    if (!VISUAL_VIEWS.has(viewKind)) return false;
    return toggleOn !== false; // default-on: only an explicit false suppresses it
  }

  // Views where the reading-width control applies. PROSE (markdown) gets a centered max-width
  // reading column; CODE gets a soft-wrap column — the width sets the WRAP column, so long
  // source lines reflow (CSS-only, no horizontal scroll, no inserted hard breaks, one
  // data-src-line per line preserved). The structured/CSV/image views stay full-bleed (the
  // width control is inert there). Pure + injectable so the per-view enable/disable + the
  // onCycleWidth guard are unit-testable without a browser (mirrors shouldCaptureScreenshot).
  const WIDTH_VIEWS = new Set(['markdown', 'code']);
  function widthApplies(viewKind) {
    return WIDTH_VIEWS.has(viewKind);
  }

  function pickFetch(fetchImpl) {
    if (fetchImpl) return fetchImpl;
    if (typeof fetch !== 'undefined') return fetch;
    return null;
  }

  // Parse the server-injected <script type="application/json" id="annotate-config">.
  // Returns the parsed object (must carry session+artifact), or null when absent/invalid.
  function readPageConfig(doc) {
    if (!doc || typeof doc.getElementById !== 'function') return null;
    const el = doc.getElementById(CONFIG_ID);
    if (!el) return null;
    const text = el.textContent != null ? el.textContent : el.text;
    if (!text) return null;
    try {
      const cfg = JSON.parse(text);
      if (cfg && cfg.session && cfg.artifact) return cfg;
    } catch (e) {
      /* malformed -> treat as no config */
    }
    return null;
  }

  // Resolve the runtime context the content script operates with.
  //   served : an annotate-served loopback artifact page — origin = the page origin,
  //            session/artifact/head/token come from the injected config. (The exit-gate path.)
  //   live   : a foreign-origin live page with no injected config — session/artifact/head are
  //            discoverable via GET <origin>/resolve?url=, but the per-session TOKEN is not yet
  //            deliverable to a foreign origin (flagged SPEC-GAP; live-page submit is follow-up).
  function resolveContext(env) {
    env = env || {};
    const doc = env.document || (typeof document !== 'undefined' ? document : null);
    const loc = env.location || (typeof location !== 'undefined' ? location : null);
    const cfg = readPageConfig(doc);
    if (cfg) {
      return {
        mode: 'served',
        origin: (loc && loc.origin) || cfg.origin || null,
        session: cfg.session,
        artifact: cfg.artifact,
        head: cfg.head || null,
        token: cfg.token || null,
      };
    }
    return {
      mode: 'live',
      origin: env.annotateOrigin || DEFAULT_ORIGIN,
      session: null,
      artifact: null,
      head: null,
      token: null,
      href: loc && loc.href ? loc.href : null,
    };
  }

  // For a live page: ask the server to map the live URL -> {session,artifact,head}
  // (§6.4 launch-registered URL map). Mutates+returns the context. Token stays null
  // (the flagged gap). fetchImpl injectable.
  async function discoverLiveContext(ctx, fetchImpl) {
    const f = pickFetch(fetchImpl);
    if (!f || !ctx.origin || !ctx.href) return ctx;
    try {
      const res = await f(ctx.origin + '/resolve?url=' + encodeURIComponent(ctx.href), { cache: 'no-store' });
      if (!res.ok) return ctx;
      const m = await res.json();
      ctx.session = m.session;
      ctx.artifact = m.artifact;
      ctx.head = m.head;
    } catch (e) {
      /* best-effort */
    }
    return ctx;
  }

  // The REAL feedback sink (T6a) that replaces T3's stand-in: POST <origin>/feedback with
  // the §5.5 body and the token in X-Annotate-Token (bundle.headers already carries it).
  // Normalizes the response to { httpStatus, ...json } so callers branch on status/error.
  function makeFeedbackSink(ctx, fetchImpl) {
    const f = pickFetch(fetchImpl);
    return async function sink(bundle) {
      const res = await f(ctx.origin + '/feedback', {
        method: 'POST',
        headers: bundle.headers,
        body: JSON.stringify(bundle.body),
        cache: 'no-store',
      });
      let json = null;
      try {
        json = await res.json();
      } catch (e) {
        /* non-JSON error body */
      }
      return Object.assign({ httpStatus: res.status }, json || {});
    };
  }

  // POST <origin>/<session>/<artifact>/accept — head-checked (§5.5, §2.7). `believedHead`
  // is the head the human looked at; the server 409s if it is stale.
  async function postAccept(ctx, believedHead, fetchImpl) {
    const f = pickFetch(fetchImpl);
    const url =
      ctx.origin + '/' + encodeURIComponent(ctx.session) + '/' + encodeURIComponent(ctx.artifact) + '/accept';
    const res = await f(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Annotate-Token': ctx.token },
      body: JSON.stringify({ head: believedHead }),
      cache: 'no-store',
    });
    let json = null;
    try {
      json = await res.json();
    } catch (e) {
      /* ignore */
    }
    return Object.assign({ httpStatus: res.status }, json || {});
  }

  // v2.2 §I — upload a user-attached image so the server COPIES it into the round folder
  // ON SELECT. POST <origin>/<session>/<artifact>/attach with the base64 bytes + mime/name
  // and the per-session token in X-Annotate-Token (head-checked like /feedback + /accept).
  // payload = { head, data (base64, no data: prefix), mime, name }. Returns
  // { httpStatus, ok, filename } — the caller stamps `filename` on the staged comment.
  async function uploadAttachment(ctx, payload, fetchImpl) {
    const f = pickFetch(fetchImpl);
    const url =
      ctx.origin + '/' + encodeURIComponent(ctx.session) + '/' + encodeURIComponent(ctx.artifact) + '/attach';
    const res = await f(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Annotate-Token': ctx.token },
      body: JSON.stringify({
        head: payload.head,
        data: payload.data,
        mime: payload.mime || '',
        name: payload.name || '',
      }),
      cache: 'no-store',
    });
    let json = null;
    try {
      json = await res.json();
    } catch (e) {
      /* non-JSON error body */
    }
    return Object.assign({ httpStatus: res.status }, json || {});
  }

  // One-time load-probe heartbeat (§6.6, proven in S0): POST <origin>/loaded so setup can
  // confirm the extension injected + ran. Best-effort; never throws.
  function sendHeartbeat(ctx, fetchImpl) {
    const f = pickFetch(fetchImpl);
    if (!f || !ctx || !ctx.origin) return Promise.resolve(null);
    return f(ctx.origin + '/loaded?ext=annotate&ts=' + Date.now(), { method: 'POST', cache: 'no-store' }).catch(
      function () {
        return null;
      }
    );
  }

  // GET <origin>/<session>/<artifact>/head — the auto-advance poll (§6.4). Returns
  // { head, status, changeToken } or null.
  async function fetchHead(ctx, fetchImpl) {
    const f = pickFetch(fetchImpl);
    const url =
      ctx.origin + '/' + encodeURIComponent(ctx.session) + '/' + encodeURIComponent(ctx.artifact) + '/head';
    const res = await f(url, { cache: 'no-store' });
    if (!res.ok) return null;
    return res.json();
  }

  const api = {
    CONFIG_ID,
    DEFAULT_ORIGIN,
    VISUAL_VIEWS,
    shouldCaptureScreenshot,
    WIDTH_VIEWS,
    widthApplies,
    readPageConfig,
    resolveContext,
    discoverLiveContext,
    makeFeedbackSink,
    uploadAttachment,
    postAccept,
    sendHeartbeat,
    fetchHead,
  };

  if (typeof module === 'object' && module.exports) {
    module.exports = api; // Node (unit tests)
  } else {
    root.Annotate = root.Annotate || {};
    root.Annotate.config = api; // MV3 content script
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
