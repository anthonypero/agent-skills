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

// Shared CfT + CDP machinery (factored into cdp-harness.js so the T6b image-gate.js EXTENDS
// the same harness — see that file's header).
const { PKG_ROOT, sleep, waitFor, findCft, launchCft, connectPage } = require('./cdp-harness.js');
const { start } = require(path.join(PKG_ROOT, 'server', 'server.js'));
const { create } = require(path.join(PKG_ROOT, 'server', 'create.js'));
const P = require(path.join(PKG_ROOT, 'server', 'protocol.js'));

const FIXTURE = path.join(PKG_ROOT, 'tests', 'fixtures', 'sample.md');
const SERVER_PORT = 7991;
const DEBUG_PORT = 9344;

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
    child = launchCft({ cft, profileDir, debugPort: DEBUG_PORT, url });

    // 3. find the page target + connect CDP
    cdp = await connectPage(DEBUG_PORT, `/${round.session}/${round.artifact}`);

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

    // GATE 3 — §K click-to-select-innermost: CLICK a rendered block -> the lock bubble parks
    // at the click with up/down traversal + a comment button; clicking the bubble's comment
    // button opens the composer with the correct §5.2 anchor. (The v1 hover->floating-icon
    // affordance was removed in §K — moving toward it was unreachable.) Target a <p> so we
    // exercise the plain block/line path (innermost stop == its own line).
    const clicked = await cdp.evaluate(`(() => {
      const block = document.querySelector('.annotate-render p[data-src-line]') ||
                    document.querySelector('.annotate-render [data-src-line]');
      if (!block) return { err: 'no data-src-line node' };
      const srcLine = parseInt(block.getAttribute('data-src-line'), 10);
      const r = block.getBoundingClientRect();
      block.dispatchEvent(new MouseEvent('click', { clientX: r.left + 5, clientY: r.top + 5, button: 0, bubbles: true, cancelable: true, view: window }));
      const bubble = document.querySelector('.annotate-lock-bubble');
      const commentBtn = document.querySelector('.annotate-lock-comment');
      if (commentBtn) commentBtn.click(); // the lock bubble's comment button opens the composer
      const c = document.querySelector('.annotate-composer');
      return {
        srcLine,
        lockShown: !!bubble,
        composerOpened: !!c,
        anchorKind: c && c.getAttribute('data-anchor-kind'),
        anchorLine: c && c.getAttribute('data-anchor-line'),
      };
    })()`);
    ok(
      'click a line -> lock bubble -> comment -> source/line anchor (matches data-src-line)',
      clicked.lockShown && clicked.composerOpened && clicked.anchorKind === 'source' && Number(clicked.anchorLine) === clicked.srcLine,
      `line ${clicked.srcLine} -> lock=${clicked.lockShown}; anchor {kind:${clicked.anchorKind}, line:${clicked.anchorLine}}`
    );

    // compose a comment + Add (real DOM), then Send (real button)
    // §B: a staged comment now LIVES as a semi-transparent on-canvas PIN (+ a sidebar row),
    // NOT the old right-margin card. Count the pin in its new home.
    const composed = await cdp.evaluate(`(() => {
      const ta = document.querySelector('.annotate-composer-input');
      if (!ta) return { err: 'no composer input' };
      ta.value = 'integration: this guard clause is unreachable';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('.annotate-add').click();
      return {
        pins: document.querySelectorAll('.annotate-comment-pin').length,
        sidebarRows: document.querySelectorAll('.annotate-sidebar-item').length,
      };
    })()`);
    ok('Add -> a staged comment is pinned on the canvas (+ a sidebar row)',
      composed.pins === 1 && composed.sidebarRows === 1, `pins=${composed.pins}; rows=${composed.sidebarRows}`);

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
