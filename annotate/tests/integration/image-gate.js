'use strict';

// T6b integration gate — EXTENDS the T6a harness (cdp-harness.js) to drive the REAL
// extension in the provisioned Chrome for Testing against the REAL annotate server and
// assert the §6.4 image/screenshot/auto-advance exit gate end-to-end:
//
//   A. IMAGE ANCHORS  — click -> normalized §5.2 spatial POINT; drag -> normalized BOX
//                       (0-1, NO crop, §5.3); Send round-trips BOTH spatial anchors to disk.
//   A5. SCREENSHOT     — on the IMAGE (visual) view, a gated viewport screenshot is captured
//                       + stored as <guid>-screenshot.png (default-on toggle).
//   A6. AUTO-ADVANCE   — after submit, a NEW head loads (the page follows the head).
//   A7. PRESERVE-UNSENT— a NEW head while an UNSENT draft is staged does NOT silently reload;
//                       it warns + preserves the draft (the §6.4 anti-drop rule).
//   A9. DISCARD        — the explicit "Discard & view new round" escape hatch advances.
//   A10. STOP@ACCEPTED — Accept finalizes the round AND stops the auto-advance poll: a later
//                       new head does NOT pull the accepted tab forward.
//   B.  GATED OFF      — on a CODE (source) view the screenshot is NOT captured/stored.
//
// Drives the UI through the SHARED DOM (real dispatched mouse events / .click() / values,
// reading the [data-*] attributes content.js stamps) and asserts ON DISK — exactly how a
// human's clicks would drive it, minus the pixels.
//
// RUN (the Bash sandbox blocks loopback HTTP — run with the sandbox disabled):
//   node tests/integration/image-gate.js
// Env: ANNOTATE_CFT=<CfT binary> (else auto-found under .spike/cache); ANNOTATE_HEADLESS=1.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { PKG_ROOT, sleep, waitFor, findCft, launchCft, connectPage } = require('./cdp-harness.js');
const { start } = require(path.join(PKG_ROOT, 'server', 'server.js'));
const { create } = require(path.join(PKG_ROOT, 'server', 'create.js'));
const P = require(path.join(PKG_ROOT, 'server', 'protocol.js'));

const IMG_FIXTURE = path.join(PKG_ROOT, 'tests', 'fixtures', 'sample.png');
const JS_FIXTURE = path.join(PKG_ROOT, 'tests', 'fixtures', 'sample.js');
const SERVER_PORT = 7992;
const DEBUG_PORT = 9345;

// ---- DOM-driving expressions (run in the page main world via CDP) -------------

const READY = `document.documentElement.getAttribute('data-annotate-ready')`;
const CONFIG_HEAD = `(() => { const e = document.getElementById('annotate-config'); if (!e) return null; try { return JSON.parse(e.textContent).head; } catch (_) { return null; } })()`;
const SHOT_ACTIVE = `(() => { const b = document.getElementById('annotate-chrome'); return b && b.getAttribute('data-screenshot-active'); })()`;

// Dispatch a real mouse gesture on the rendered image. Same start/end -> a click (point);
// distinct -> a drag (box). Fractions are 0-1 of the image's bounding rect.
function gestureExpr(fx0, fy0, fx1, fy1) {
  return `(() => {
    const img = document.querySelector('.annotate-image img');
    if (!img) return { err: 'no image' };
    const r = img.getBoundingClientRect();
    const cx0 = r.left + r.width * ${fx0}, cy0 = r.top + r.height * ${fy0};
    const cx1 = r.left + r.width * ${fx1}, cy1 = r.top + r.height * ${fy1};
    const mk = (type, cx, cy) => new MouseEvent(type, { clientX: cx, clientY: cy, button: 0, bubbles: true, cancelable: true, view: window });
    img.dispatchEvent(mk('mousedown', cx0, cy0));
    window.dispatchEvent(mk('mousemove', cx1, cy1));
    window.dispatchEvent(mk('mouseup', cx1, cy1));
    const c = document.querySelector('.annotate-composer');
    return {
      kind: c && c.getAttribute('data-anchor-kind'),
      point: c && c.getAttribute('data-anchor-point'),
      box: c && c.getAttribute('data-anchor-box'),
      rect: { w: Math.round(r.width), h: Math.round(r.height) },
    };
  })()`;
}

function addCommentExpr(text) {
  // §B: a staged comment now LIVES as a semi-transparent on-canvas PIN (the old right-margin
  // card is gone). `cards` counts pins in their NEW home so the existing assertions hold.
  return `(() => {
    const ta = document.querySelector('.annotate-composer-input');
    if (!ta) return { err: 'no composer input' };
    ta.value = ${JSON.stringify(text)};
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('.annotate-add').click();
    return { cards: document.querySelectorAll('.annotate-comment-pin').length, composerOpen: !!document.querySelector('.annotate-composer') };
  })()`;
}

function clamp01ok(v) {
  return typeof v === 'number' && v >= 0 && v <= 1;
}

async function main() {
  const checks = [];
  const ok = (name, cond, detail) => {
    checks.push({ name, pass: !!cond, detail });
    console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
  };

  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-img-gate-'));
  const profileDir = path.join(dataDir, 'chrome-profile');
  const cft = findCft();
  let srv = null;
  let child = null;
  let cdp = null;

  // round-file path for a (session, artifact, guid)
  const roundFileOf = (s, a, g) => P.roundJsonIn(path.join(P.artifactDir(dataDir, s, a), g), g);
  const shotPathOf = (s, a, g) => P.screenshotIn(path.join(P.artifactDir(dataDir, s, a), g), g);

  async function waitRound(guid, label, timeout = 14000) {
    return waitFor(label, async () => {
      const head = await cdp.evaluate(CONFIG_HEAD);
      const ready = await cdp.evaluate(READY);
      return head === guid && ready === '1' ? { head } : null;
    }, { timeout });
  }

  try {
    srv = await start({ dataDir, port: SERVER_PORT });

    // R1: the first IMAGE round.
    const r1 = create({ dataDir, source: IMG_FIXTURE });
    const SESSION = r1.session;
    const ARTIFACT = r1.artifact;
    const imgUrl = `http://127.0.0.1:${SERVER_PORT}/${SESSION}/${ARTIFACT}`;
    console.log(`server ${srv.url}\nimage url ${imgUrl}\nround R1 ${r1.guid}\ncft ${cft}\n`);

    child = launchCft({ cft, profileDir, debugPort: DEBUG_PORT, url: imgUrl });
    cdp = await connectPage(DEBUG_PORT, `/${SESSION}/${ARTIFACT}`);
    await waitRound(r1.guid, 'content ready on image round R1');

    // ---- A1: image view + screenshot ACTIVE (visual view, toggle default-on) ----
    const view = await cdp.evaluate(`(() => ({
      isImage: !!document.querySelector('.annotate-image img'),
      badge: (document.querySelector('.annotate-badge') || {}).textContent,
      shotActive: (() => { const b = document.getElementById('annotate-chrome'); return b && b.getAttribute('data-screenshot-active'); })(),
    }))()`);
    ok('image view detected (.annotate-image), screenshot gated ACTIVE (visual + default-on)',
      view.isImage && view.badge === 'IMG' && view.shotActive === '1',
      `badge=${view.badge}; shotActive=${view.shotActive}`);

    // ---- A2: CLICK -> normalized spatial POINT anchor ----
    const point = await cdp.evaluate(gestureExpr(0.3, 0.4, 0.3, 0.4)); // no travel -> point
    const pv = point.point ? JSON.parse(point.point) : null;
    ok('click on image -> §5.2 spatial POINT anchor, normalized 0-1',
      point.kind === 'spatial' && Array.isArray(pv) && pv.length === 2 && pv.every(clamp01ok) && !point.box,
      `kind=${point.kind}; point=${point.point}; rect=${JSON.stringify(point.rect)}`);
    const addP = await cdp.evaluate(addCommentExpr('image: this region needs more contrast'));
    ok('point anchor staged as a pin (§B on-canvas presence)', addP.cards === 1 && !addP.composerOpen, `pins=${addP.cards}`);

    // ---- A3: DRAG -> normalized spatial BOX anchor (no crop, just coords) ----
    const box = await cdp.evaluate(gestureExpr(0.2, 0.25, 0.7, 0.8)); // real travel -> box
    const bv = box.box ? JSON.parse(box.box) : null;
    ok('drag on image -> §5.2 spatial BOX anchor [x,y,w,h], normalized 0-1, w/h>0',
      box.kind === 'spatial' && Array.isArray(bv) && bv.length === 4 && bv.every(clamp01ok) && bv[2] > 0 && bv[3] > 0 && !box.point,
      `kind=${box.kind}; box=${box.box}`);
    const addB = await cdp.evaluate(addCommentExpr('image: crop tighter around this region'));
    ok('box anchor staged as a second pin (§B on-canvas presence)', addB.cards === 2, `pins=${addB.cards}`);

    // ---- A4: Send -> BOTH spatial anchors land on disk ----
    await cdp.evaluate(`document.querySelector('.annotate-send').click()`);
    const submitR1 = await waitFor('R1 submit result', async () =>
      (await cdp.evaluate(`document.getElementById('annotate-chrome').getAttribute('data-last-submit')`)) || null);
    const diskR1 = JSON.parse(fs.readFileSync(roundFileOf(SESSION, ARTIFACT, r1.guid), 'utf8'));
    const kinds = diskR1.feedback.map((f) => f.anchor && f.anchor.kind);
    const hasPoint = diskR1.feedback.some((f) => f.anchor && Array.isArray(f.anchor.point) && f.anchor.point.every(clamp01ok));
    const hasBox = diskR1.feedback.some((f) => f.anchor && Array.isArray(f.anchor.box) && f.anchor.box.length === 4 && f.anchor.box.every(clamp01ok));
    ok('Send round-trips BOTH spatial anchors to disk (point + box, all normalized)',
      submitR1 === 'submitted' && diskR1.status === 'submitted' && diskR1.feedback.length === 2 &&
        kinds.every((k) => k === 'spatial') && hasPoint && hasBox,
      `status=${diskR1.status}; kinds=${JSON.stringify(kinds)}; point=${hasPoint}; box=${hasBox}`);

    // ---- A5: a viewport screenshot was captured + stored (gated ON for the image view) ----
    const shotR1 = shotPathOf(SESSION, ARTIFACT, r1.guid);
    const shotExists = await waitFor('R1 screenshot file', async () => (fs.existsSync(shotR1) ? fs.statSync(shotR1) : null), { timeout: 8000 }).catch(() => null);
    let shotIsPng = false, shotSize = 0;
    if (shotExists) {
      const buf = fs.readFileSync(shotR1);
      shotSize = buf.length;
      shotIsPng = buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47;
    }
    ok('gated screenshot captured + stored as <guid>-screenshot.png on the IMAGE view',
      !!shotExists && shotIsPng && shotSize > 100, `exists=${!!shotExists}; png=${shotIsPng}; bytes=${shotSize}`);

    // ---- A6: AUTO-ADVANCE after submit — a NEW head loads (sent drafts do not block) ----
    const r2 = create({ dataDir, source: IMG_FIXTURE, session: SESSION, artifact: ARTIFACT });
    let advanced = true;
    try { await waitRound(r2.guid, 'auto-advance to new head R2', 9000); } catch (e) { advanced = false; }
    ok('auto-advance: a NEW head loads after submit (the page follows the head)', advanced, `R2=${r2.guid}`);

    // ---- A7/A8: PRESERVE-UNSENT — a new head while a draft is staged does NOT reload ----
    await cdp.evaluate(`window.__t6b = 'R2'`); // main-world sentinel; a reload wipes it
    await cdp.evaluate(gestureExpr(0.5, 0.5, 0.5, 0.5)); // point -> composer
    const staged = await cdp.evaluate(addCommentExpr('UNSENT work in progress'));
    const r3 = create({ dataDir, source: IMG_FIXTURE, session: SESSION, artifact: ARTIFACT });
    await sleep(3600); // >= 3 poll cycles (1s)
    const preserve = await cdp.evaluate(`(() => ({
      sentinel: window.__t6b || null,
      head: (() => { const e = document.getElementById('annotate-config'); try { return JSON.parse(e.textContent).head; } catch (_) { return null; } })(),
      pendingAdvance: (document.getElementById('annotate-chrome') || {}).getAttribute ? document.getElementById('annotate-chrome').getAttribute('data-pending-advance') : null,
      warnBanner: !!document.querySelector('.annotate-advance-warn'),
      cards: document.querySelectorAll('.annotate-comment-pin').length, // §B: staged comment now lives as a pin
    }))()`);
    ok('preserve-unsent: a new head does NOT silently reload while a draft is staged (warns + keeps it)',
      preserve.sentinel === 'R2' && preserve.head === r2.guid && preserve.pendingAdvance === r3.guid && preserve.warnBanner && preserve.cards >= 1,
      `sentinel=${preserve.sentinel}; head==R2=${preserve.head === r2.guid}; pendingAdvance=${preserve.pendingAdvance === r3.guid}; banner=${preserve.warnBanner}; cards=${preserve.cards}; staged=${staged.cards}`);

    // ---- A9: explicit DISCARD advances to the deferred head ----
    await cdp.evaluate(`document.querySelector('.annotate-advance-discard').click()`);
    let discarded = true;
    try { await waitRound(r3.guid, 'discard -> advance to R3', 9000); } catch (e) { discarded = false; }
    ok('discard & view new round: the explicit escape hatch advances to the deferred head', discarded, `R3=${r3.guid}`);

    // ---- A10/A11: STOP-AT-ACCEPTED — Accept finalizes AND stops the poll ----
    await cdp.evaluate(`document.querySelector('.annotate-accept').click()`);
    const acceptRes = await waitFor('R3 accept result', async () =>
      (await cdp.evaluate(`document.getElementById('annotate-chrome').getAttribute('data-last-accept')`)) || null);
    const diskR3 = JSON.parse(fs.readFileSync(roundFileOf(SESSION, ARTIFACT, r3.guid), 'utf8'));
    const roundStatusAttr = await cdp.evaluate(`document.getElementById('annotate-chrome').getAttribute('data-round-status')`);
    ok('accept finalizes the round on disk + marks data-round-status accepted',
      acceptRes === 'accepted' && diskR3.status === 'accepted' && roundStatusAttr === 'accepted',
      `ui=${acceptRes}; disk=${diskR3.status}; attr=${roundStatusAttr}`);

    await cdp.evaluate(`window.__t6b = 'R3-accepted'`);
    const r4 = create({ dataDir, source: IMG_FIXTURE, session: SESSION, artifact: ARTIFACT });
    await sleep(3600); // >= 3 poll cycles — but the poll is stopped, so nothing should move
    const afterAccept = await cdp.evaluate(`(() => ({
      sentinel: window.__t6b || null,
      head: (() => { const e = document.getElementById('annotate-config'); try { return JSON.parse(e.textContent).head; } catch (_) { return null; } })(),
    }))()`);
    ok('polling STOPS at accepted: a later new head does NOT pull the accepted tab forward',
      afterAccept.sentinel === 'R3-accepted' && afterAccept.head === r3.guid,
      `sentinel=${afterAccept.sentinel}; head-still-R3=${afterAccept.head === r3.guid}; R4=${r4.guid}`);

    // ---- B: CODE view — the screenshot is GATED OFF (no capture, no file) ----
    const codeRound = create({ dataDir, source: JS_FIXTURE });
    const codeUrl = `http://127.0.0.1:${SERVER_PORT}/${codeRound.session}/${codeRound.artifact}`;
    await cdp.send('Page.navigate', { url: codeUrl });
    await waitRound(codeRound.guid, 'content ready on CODE round');

    const codeView = await cdp.evaluate(`(() => ({
      isCode: !!document.querySelector('pre.annotate-code'),
      shotActive: (() => { const b = document.getElementById('annotate-chrome'); return b && b.getAttribute('data-screenshot-active'); })(),
    }))()`);
    ok('code view: screenshot gated OFF (data-screenshot-active=0 on a source view)',
      codeView.isCode && codeView.shotActive === '0', `isCode=${codeView.isCode}; shotActive=${codeView.shotActive}`);

    // §A icon-only: hover the code line to reveal the comment icon, then click ONLY the icon
    // (a code line is a plain block — no §C heading split). The v1 click-the-line path is gone.
    const codeClick = await cdp.evaluate(`(() => {
      const line = document.querySelector('.annotate-render [data-src-line]');
      if (!line) return { err: 'no line' };
      const r = line.getBoundingClientRect();
      line.dispatchEvent(new MouseEvent('mousemove', { clientX: r.left + 5, clientY: r.top + 5, button: 0, bubbles: true, cancelable: true, view: window }));
      const icon = document.querySelector('.annotate-comment-affordance');
      if (icon) icon.click();
      const c = document.querySelector('.annotate-composer');
      return { iconShown: !!icon, kind: c && c.getAttribute('data-anchor-kind'), line: c && c.getAttribute('data-anchor-line') };
    })()`);
    await cdp.evaluate(addCommentExpr('code: this line'));
    await cdp.evaluate(`document.querySelector('.annotate-send').click()`);
    const submitCode = await waitFor('code submit result', async () =>
      (await cdp.evaluate(`document.getElementById('annotate-chrome').getAttribute('data-last-submit')`)) || null);
    const diskCode = JSON.parse(fs.readFileSync(roundFileOf(codeRound.session, codeRound.artifact, codeRound.guid), 'utf8'));
    ok('code view: source/line anchor submits to disk', codeClick.kind === 'source' && submitCode === 'submitted' && diskCode.status === 'submitted',
      `anchorKind=${codeClick.kind}; submit=${submitCode}; disk=${diskCode.status}`);

    await sleep(500); // let any (gated-off) capture attempt settle before asserting absence
    const codeShot = shotPathOf(codeRound.session, codeRound.artifact, codeRound.guid);
    ok('code view: NO screenshot captured/stored (gated off on a source view)', !fs.existsSync(codeShot),
      `screenshot exists=${fs.existsSync(codeShot)}`);

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
