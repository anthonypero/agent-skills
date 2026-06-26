'use strict';

// T6a integration gate — drives the REAL extension in the provisioned Chrome for Testing
// (S0) against the REAL annotate server and asserts the §6.4 exit gate end-to-end:
//
//   1. the extension LOADS in CfT          -> the content-script heartbeat reaches POST /loaded
//   2. config discovery                    -> the server-injected #annotate-config carries
//                                             {session,artifact,head,token}; content.js builds the chrome
//   3. click a rendered line -> anchor     -> clicking a [data-src-line] node opens the composer
//                                             with the correct §5.2 source/line anchor
//   4. submit round-trips                  -> compose + Send POSTs the §5.5 bundle (token in
//                                             X-Annotate-Token); the server flips the round to
//                                             `submitted` ON DISK with the feedback spliced
//   5. accept is head-checked              -> Accept POSTs /accept (200 accepted on disk);
//                                             a stale head -> 409
//
// Driven via the Chrome DevTools Protocol over Node's built-in WebSocket (Node 22+). CDP
// Runtime.evaluate runs in the PAGE MAIN WORLD, which cannot see the content script's JS
// globals (MV3 isolated world) — so the gate drives the UI exclusively through the SHARED DOM
// (real .click()/value, reading the [data-*] result attributes content.js stamps) and asserts
// the outcome ON DISK. This is exactly how a human's clicks would drive it, minus the pixels.
//
// RUN (the Bash sandbox blocks loopback HTTP — run with the sandbox disabled):
//   node tests/integration/extension-gate.js
// Optional: ANNOTATE_CFT=<path to CfT binary>  (else auto-found under .spike/cache)
//           ANNOTATE_HEADLESS=1                (run headless=new instead of headful)

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, execSync } = require('node:child_process');

const PKG_ROOT = path.join(__dirname, '..', '..');
const { start } = require(path.join(PKG_ROOT, 'server', 'server.js'));
const { create } = require(path.join(PKG_ROOT, 'server', 'create.js'));
const P = require(path.join(PKG_ROOT, 'server', 'protocol.js'));

const EXT_DIR = path.join(PKG_ROOT, 'extension');
const FIXTURE = path.join(PKG_ROOT, 'tests', 'fixtures', 'sample.md');
const SERVER_PORT = 7991;
const DEBUG_PORT = 9344;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(label, fn, { timeout = 30000, interval = 200 } = {}) {
  const deadline = Date.now() + timeout;
  let last;
  for (;;) {
    try {
      last = await fn();
      if (last) return last;
    } catch (e) {
      last = e;
    }
    if (Date.now() >= deadline) throw new Error(`timeout waiting for ${label} (last: ${JSON.stringify(last)})`);
    await sleep(interval);
  }
}

function findCft() {
  if (process.env.ANNOTATE_CFT && fs.existsSync(process.env.ANNOTATE_CFT)) return process.env.ANNOTATE_CFT;
  const cache = path.join(PKG_ROOT, '.spike', 'cache');
  const out = execSync(`find "${cache}" -type f -name 'Google Chrome for Testing' 2>/dev/null | head -1`)
    .toString()
    .trim();
  if (!out) throw new Error(`no Chrome for Testing under ${cache} — run @puppeteer/browsers install chrome@stable`);
  return out;
}

// ---- minimal CDP client over the built-in WebSocket --------------------------
function cdpConnect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
    let nextId = 1;
    const send = (method, params) =>
      new Promise((res, rej) => {
        const id = nextId++;
        pending.set(id, { res, rej });
        ws.send(JSON.stringify({ id, method, params: params || {} }));
      });
    const evaluate = async (expression) => {
      const r = await send('Runtime.evaluate', {
        expression,
        awaitPromise: true,
        returnByValue: true,
      });
      if (r.exceptionDetails) {
        throw new Error('eval exception: ' + JSON.stringify(r.exceptionDetails.exception || r.exceptionDetails));
      }
      return r.result.value;
    };
    ws.addEventListener('open', () => resolve({ ws, send, evaluate, close: () => ws.close() }));
    ws.addEventListener('error', () => reject(new Error('CDP websocket error: ' + wsUrl)));
    ws.addEventListener('message', (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) rej(new Error(JSON.stringify(msg.error)));
        else res(msg.result);
      }
    });
  });
}

async function main() {
  const checks = [];
  const ok = (name, cond, detail) => {
    checks.push({ name, pass: !!cond, detail });
    console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
  };

  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-gate-'));
  const profileDir = path.join(dataDir, 'chrome-profile');
  const cft = findCft();
  let srv = null;
  let child = null;
  let cdp = null;

  try {
    // 1. real server + a real markdown round
    srv = await start({ dataDir, port: SERVER_PORT });
    const round = create({ dataDir, source: FIXTURE });
    const aDir = P.artifactDir(dataDir, round.session, round.artifact);
    const roundFile = P.roundJsonPath(aDir, round.guid);
    const url = `http://127.0.0.1:${SERVER_PORT}/${round.session}/${round.artifact}`;
    console.log(`server ${srv.url}  round ${round.guid}\nurl ${url}\ncft ${cft}\n`);

    // 2. launch CfT + the unpacked extension + remote debugging at the served URL
    const args = [
      `--user-data-dir=${profileDir}`,
      `--load-extension=${EXT_DIR}`,
      `--disable-extensions-except=${EXT_DIR}`,
      `--remote-debugging-port=${DEBUG_PORT}`,
      '--remote-allow-origins=*',
      '--no-first-run',
      '--no-default-browser-check',
      '--no-service-autorun',
      '--disable-background-networking',
    ];
    if (process.env.ANNOTATE_HEADLESS) args.push('--headless=new');
    args.push(url);
    child = spawn(cft, args, { stdio: 'ignore' });

    // 3. find the page target + connect CDP
    const target = await waitFor('CfT page target', async () => {
      const res = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
      const list = await res.json();
      return list.find((t) => t.type === 'page' && t.url && t.url.indexOf(`/${round.session}/${round.artifact}`) >= 0);
    });
    cdp = await cdpConnect(target.webSocketDebuggerUrl);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');

    // 4. wait for the content script to initialize (it stamps documentElement)
    await waitFor('content script ready', async () => {
      return (await cdp.evaluate(`document.documentElement.getAttribute('data-annotate-ready')`)) === '1';
    });

    // GATE 1 — heartbeat reached POST /loaded (proves the extension loaded + ran)
    const loaded = await waitFor('heartbeat', async () => {
      const j = await (await fetch(`http://127.0.0.1:${SERVER_PORT}/loaded`)).json();
      return j.count > 0 ? j : null;
    });
    ok('extension loaded (heartbeat at /loaded)', loaded.count > 0, `count=${loaded.count}`);

    // GATE 2 — config discovery + chrome injected
    const env = await cdp.evaluate(`(() => {
      const cfgEl = document.getElementById('annotate-config');
      const cfg = cfgEl ? JSON.parse(cfgEl.textContent) : null;
      return {
        cfg,
        hasChrome: !!document.getElementById('annotate-chrome'),
        hasAccept: !!document.querySelector('.annotate-accept'),
        hasSend: !!document.querySelector('.annotate-send'),
      };
    })()`);
    ok('config injected (session/artifact/head/token)', env.cfg && env.cfg.session === round.session && env.cfg.artifact === round.artifact && env.cfg.head === round.guid && !!env.cfg.token, JSON.stringify({ ...env.cfg, token: env.cfg && env.cfg.token ? '<' + env.cfg.token.length + ' chars>' : null }));
    ok('Annotate chrome present (accept + send controls)', env.hasChrome && env.hasAccept && env.hasSend);

    // GATE 3 — click a rendered line -> the composer opens with the correct §5.2 anchor
    const clicked = await cdp.evaluate(`(() => {
      const line = document.querySelector('.annotate-render [data-src-line]');
      if (!line) return { err: 'no data-src-line node' };
      const srcLine = parseInt(line.getAttribute('data-src-line'), 10);
      line.click();
      const c = document.querySelector('.annotate-composer');
      return {
        srcLine,
        composerOpened: !!c,
        anchorKind: c && c.getAttribute('data-anchor-kind'),
        anchorLine: c && c.getAttribute('data-anchor-line'),
      };
    })()`);
    ok(
      'click on a line -> source/line anchor (matches data-src-line)',
      clicked.composerOpened && clicked.anchorKind === 'source' && Number(clicked.anchorLine) === clicked.srcLine,
      `clicked line ${clicked.srcLine} -> anchor {kind:${clicked.anchorKind}, line:${clicked.anchorLine}}`
    );

    // compose a comment + Add (real DOM), then Send (real button)
    const composed = await cdp.evaluate(`(() => {
      const ta = document.querySelector('.annotate-composer-input');
      if (!ta) return { err: 'no composer input' };
      ta.value = 'integration: this guard clause is unreachable';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('.annotate-add').click();
      return { cards: document.querySelectorAll('.annotate-card').length };
    })()`);
    ok('Add -> a margin comment card is staged', composed.cards === 1, `cards=${composed.cards}`);

    // GATE 4 — Send -> the server flips the round to submitted ON DISK
    await cdp.evaluate(`document.querySelector('.annotate-send').click()`);
    const submitResult = await waitFor('submit result attr', async () => {
      const v = await cdp.evaluate(`document.getElementById('annotate-chrome').getAttribute('data-last-submit')`);
      return v || null;
    });
    const diskAfterSubmit = JSON.parse(fs.readFileSync(roundFile, 'utf8'));
    ok(
      'submit round-trips -> round flipped `submitted` on disk',
      submitResult === 'submitted' && diskAfterSubmit.status === 'submitted' && diskAfterSubmit.feedback.length === 1,
      `ui=${submitResult}; disk.status=${diskAfterSubmit.status}; feedback=${diskAfterSubmit.feedback.length}; anchor=${JSON.stringify(diskAfterSubmit.feedback[0] && diskAfterSubmit.feedback[0].anchor)}`
    );
    ok(
      'submitted feedback carries the §5.2 comment + id',
      diskAfterSubmit.feedback[0] && diskAfterSubmit.feedback[0].id === 'a1' && diskAfterSubmit.feedback[0].type === 'comment' && typeof diskAfterSubmit.feedback[0].comment === 'string',
      JSON.stringify(diskAfterSubmit.feedback[0])
    );

    // GATE 5a — Accept (head-checked, from submitted) -> accepted on disk
    await cdp.evaluate(`document.querySelector('.annotate-accept').click()`);
    const acceptResult = await waitFor('accept result attr', async () => {
      const v = await cdp.evaluate(`document.getElementById('annotate-chrome').getAttribute('data-last-accept')`);
      return v || null;
    });
    const diskAfterAccept = JSON.parse(fs.readFileSync(roundFile, 'utf8'));
    ok(
      'accept (head-checked) -> round `accepted` on disk',
      acceptResult === 'accepted' && diskAfterAccept.status === 'accepted',
      `ui=${acceptResult}; disk.status=${diskAfterAccept.status}`
    );

    // GATE 5b — a STALE head is rejected 409 (advance the head, then accept the old one)
    const round2 = create({ dataDir, source: FIXTURE, session: round.session, artifact: round.artifact });
    const staleRes = await fetch(`http://127.0.0.1:${SERVER_PORT}/${round.session}/${round.artifact}/accept`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Annotate-Token': round.token },
      body: JSON.stringify({ head: round.guid }), // the OLD (now stale) head
    });
    const staleBody = await staleRes.json();
    ok(
      'accept with a stale head -> 409 stale-head',
      staleRes.status === 409 && staleBody.error === 'stale-head' && staleBody.head === round2.guid,
      `status=${staleRes.status}; body=${JSON.stringify(staleBody)}`
    );

    const failed = checks.filter((c) => !c.pass);
    console.log(`\n${failed.length ? 'GATE FAILED' : 'GATE PASSED'} — ${checks.length - failed.length}/${checks.length} checks passed`);
    return failed.length === 0 ? 0 : 1;
  } finally {
    try { if (cdp) cdp.close(); } catch (e) {}
    try { if (child) child.kill('SIGTERM'); } catch (e) {}
    await sleep(300);
    try { if (child && !child.killed) child.kill('SIGKILL'); } catch (e) {}
    try { if (srv) await srv.close(); } catch (e) {}
    try { fs.rmSync(dataDir, { recursive: true, force: true }); } catch (e) {}
  }
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('GATE ERROR:', err && err.stack ? err.stack : err);
    process.exit(2);
  });
