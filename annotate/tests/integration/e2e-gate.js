'use strict';

// T8 capstone end-to-end gate — drives the WHOLE annotate loop on REAL artifacts through
// the REAL `bin/annotate` CLI, the REAL Node server (started by the CLI's lazy singleton),
// and the REAL MV3 extension in the provisioned Chrome for Testing (driven over CDP as the
// human). Where the per-round mechanics are proven by extension-gate.js / image-gate.js,
// THIS gate proves the FULL LOOP the host assistant runs from SKILL.md:
//
//   present  ->  (human) anchor + submit  ->  (agent) consume the {source,snapshot,feedback}
//   bundle the CLI prints  ->  edit `source`  ->  RE-PRESENT (mint a new round)  ->  accept.
//
// Demonstrated across THREE real formats (Markdown plan, code file, image) — exceeding the
// "two real formats" exit bar — plus the three load-bearing invariants:
//
//   * ANTI-LAZINESS (§2.7): accept is head-checked. After a re-launch mints a newer round the
//     human never saw, an accept of the round they DID see is rejected 409 stale-head — a
//     fire-and-forget accept of an unseen round is structurally impossible.
//   * REVERT + "N rounds ago" (§5.4): a /feedback with revertTarget=<older guid> sets the
//     round's `snapshot` pointer; the CLI's poll resolves it; the agent reads that OLDER
//     snapshot OFF DISK and recovers exactly what the artifact looked like N rounds ago.
//   * COLD RESUME (§2.3): a FRESH node process (no shared memory) reconstructs the entire
//     round history — statuses + the revert basis — purely off disk.
//
// The agent side is the REAL CLI invoked exactly as SKILL.md prescribes (mode flags, --session,
// consume stdout bundle, re-launch). The human side is real dispatched clicks/gestures in CfT.
// `--no-open` is used because THIS gate supplies the human's CfT itself (with remote debugging),
// rather than letting the CLI open an undriveable headful window — the documented headless-gate
// seam (bin/annotate header). The real --load-extension browser-open is proven by S0 + T6/T7.
//
// RUN (the Bash sandbox blocks loopback HTTP + needs a real browser — run with sandbox OFF):
//   node tests/integration/e2e-gate.js
// Env: ANNOTATE_CFT=<CfT binary> (else auto-found under .spike/cache); ANNOTATE_HEADLESS=1.

const fs = require('node:fs');
const os = require('node:os');
const net = require('node:net');
const path = require('node:path');
const { spawn } = require('node:child_process');

const { PKG_ROOT, sleep, waitFor, findCft, launchCft, connectPage } = require('./cdp-harness.js');
const P = require(path.join(PKG_ROOT, 'server', 'protocol.js'));

const BIN = path.join(PKG_ROOT, 'bin', 'annotate');
const PROTO = path.join(PKG_ROOT, 'server', 'protocol.js');
const IMG_FIXTURE = path.join(PKG_ROOT, 'tests', 'fixtures', 'sample.png');
const DEBUG_PORT = 9346;

// ---- real artifact bodies (not the tiny test fixtures) ------------------------
const PLAN_MD = `# Rollout plan: feedback ingestion service

## Goals

Stand up the ingestion path that turns raw review submissions into durable round files.
The service must be idempotent under client retries and never lose an in-flight submit.

## Phases

1. Accept the submit bundle over loopback HTTP behind an origin + token check.
2. Validate every anchor against the JSON schema before any disk write.
3. Splice the feedback array and flip the round to submitted, atomically.

## Risks

A double-clicked submit could splice twice; a stale tab could write a superseded round.
Both are handled without a lock by the head-staleness check and the per-round nonce.
`;

const SERVICE_JS = `'use strict';

// Resolve the head round for an artifact: the latest round whose descriptor exists.
function resolveHead(rounds) {
  const ready = rounds.filter((r) => r.descriptorWritten);
  if (ready.length === 0) return null;
  ready.sort((a, b) => (a.guid < b.guid ? -1 : 1));
  return ready[ready.length - 1];
}

// Apply a submit to the head round. Returns the new status.
function applySubmit(round, anchors) {
  if (round.status !== 'pending') throw new Error('cannot splice a closed round');
  round.feedback = anchors;
  round.status = 'submitted';
  return round.status;
}

module.exports = { resolveHead, applySubmit };
`;

// ---- DOM-driving expressions (run in the page main world via CDP) -------------
const READY = `document.documentElement.getAttribute('data-annotate-ready')`;
const CONFIG_HEAD = `(() => { const e = document.getElementById('annotate-config'); if (!e) return null; try { return JSON.parse(e.textContent).head; } catch (_) { return null; } })()`;
const LAST_SUBMIT = `document.getElementById('annotate-chrome').getAttribute('data-last-submit')`;
const LAST_ACCEPT = `document.getElementById('annotate-chrome').getAttribute('data-last-accept')`;

function addCommentExpr(text) {
  return `(() => {
    const ta = document.querySelector('.annotate-composer-input');
    if (!ta) return { err: 'no composer input' };
    ta.value = ${JSON.stringify(text)};
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('.annotate-add').click();
    return { cards: document.querySelectorAll('.annotate-card').length };
  })()`;
}

const clickLineExpr = `(() => {
  const line = document.querySelector('.annotate-render [data-src-line]');
  if (!line) return { err: 'no data-src-line node' };
  const srcLine = parseInt(line.getAttribute('data-src-line'), 10);
  line.click();
  const c = document.querySelector('.annotate-composer');
  return { srcLine, kind: c && c.getAttribute('data-anchor-kind'), line: c && c.getAttribute('data-anchor-line') };
})()`;

function gestureExpr(fx0, fy0, fx1, fy1) {
  return `(() => {
    const img = document.querySelector('.annotate-image img');
    if (!img) return { err: 'no image' };
    const r = img.getBoundingClientRect();
    const mk = (type, fx, fy) => new MouseEvent(type, { clientX: r.left + r.width*fx, clientY: r.top + r.height*fy, button: 0, bubbles: true, cancelable: true, view: window });
    img.dispatchEvent(mk('mousedown', ${fx0}, ${fy0}));
    window.dispatchEvent(mk('mousemove', ${fx1}, ${fy1}));
    window.dispatchEvent(mk('mouseup', ${fx1}, ${fy1}));
    const c = document.querySelector('.annotate-composer');
    return { kind: c && c.getAttribute('data-anchor-kind'), point: c && c.getAttribute('data-anchor-point'), box: c && c.getAttribute('data-anchor-box') };
  })()`;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(0, '127.0.0.1', () => {
      const p = s.address().port;
      s.close(() => resolve(p));
    });
  });
}

async function main() {
  const checks = [];
  const ok = (name, cond, detail) => {
    checks.push({ name, pass: !!cond });
    console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
  };

  const cft = findCft();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-e2e-data-'));
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-e2e-work-'));
  const profileDir = path.join(dataDir, 'chrome-profile');
  const port = await freePort();
  const SESSION = `e2e-${P.makeGuid()}`;

  // The generated setup<->launch-script<->server contract (§6.6). ABSOLUTE paths; reuse the
  // .spike CfT so this gate downloads nothing. The CLI reads it via ANNOTATE_RUNTIME.
  const runtimePath = path.join(dataDir, 'runtime.json');
  fs.writeFileSync(
    runtimePath,
    JSON.stringify(
      {
        port,
        paths: { data: dataDir, extension: path.join(PKG_ROOT, 'extension'), profile: profileDir },
        browser: { path: cft, kind: 'cft' },
        cftBuildId: '',
      },
      null,
      2
    )
  );

  // Real artifacts in a real working dir.
  const planPath = path.join(workDir, 'rollout-plan.md');
  const codePath = path.join(workDir, 'feedback-service.js');
  const imgPath = path.join(workDir, 'architecture-diagram.png');
  fs.writeFileSync(planPath, PLAN_MD);
  fs.writeFileSync(codePath, SERVICE_JS);
  fs.copyFileSync(IMG_FIXTURE, imgPath);
  const planV1Bytes = fs.readFileSync(planPath); // "what it looked like at round 1"

  const ENV = { ...process.env, ANNOTATE_RUNTIME: runtimePath };
  const ANNOTATE_TIMEOUT = '120s';

  // Run the REAL CLI exactly as the host assistant would. Returns the child + a done() promise
  // resolving { code, stdout (the bundle), stderr (diagnostics) } when it exits.
  function spawnAnnotate(args) {
    // cwd = the real working dir, so the bare artifact basenames resolve and create.js
    // records each `source` as its absolute path (path.resolve against this cwd).
    const child = spawn('sh', [BIN, ...args], { env: ENV, cwd: workDir });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => (stdout += d));
    child.stderr.on('data', (d) => (stderr += d));
    let resolveDone;
    const donePromise = new Promise((r) => (resolveDone = r));
    child.on('close', (code) => resolveDone({ code, stdout, stderr }));
    return { child, done: () => donePromise };
  }

  const aDirOf = (artifact) => P.artifactDir(dataDir, SESSION, artifact);
  async function waitNewHead(artifact, prevHead, label, timeout = 20000) {
    return waitFor(label, async () => {
      const h = P.resolveHead(aDirOf(artifact));
      return h && h !== prevHead ? h : null;
    }, { timeout });
  }
  async function waitServerUp() {
    return waitFor('server up (GET /loaded)', async () => {
      try {
        const r = await fetch(`http://127.0.0.1:${port}/loaded`);
        return r.ok ? true : null;
      } catch {
        return null;
      }
    }, { timeout: 15000 });
  }

  let cdp = null;
  let child = null; // the CfT process
  const cliChildren = [];

  // Wait until the CfT tab is showing the round we expect (after navigate OR auto-advance).
  async function waitTabHead(guid, label, timeout = 16000) {
    return waitFor(label, async () => {
      const ready = await cdp.evaluate(READY);
      const head = await cdp.evaluate(CONFIG_HEAD);
      return ready === '1' && head === guid ? head : null;
    }, { timeout });
  }
  async function send() {
    await cdp.evaluate(`document.querySelector('.annotate-send').click()`);
    return waitFor('submit landed', async () => (await cdp.evaluate(LAST_SUBMIT)) || null, { timeout: 12000 });
  }
  async function accept() {
    await cdp.evaluate(`document.querySelector('.annotate-accept').click()`);
    return waitFor('accept landed', async () => (await cdp.evaluate(LAST_ACCEPT)) || null, { timeout: 12000 });
  }

  function postJSON(pathname, body, token) {
    return fetch(`http://127.0.0.1:${port}${pathname}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Annotate-Token': token },
      body: JSON.stringify(body),
    });
  }

  try {
    // =====================================================================================
    // FORMAT A — MARKDOWN PLAN: full loop V1(submit) -> V2(submit) -> V3(accept) [3 rounds]
    // =====================================================================================
    console.log('\n=== FORMAT A: Markdown plan — full present/submit/re-present/accept loop ===');

    // --- Round V1: present -> human comments on a line -> submit -> agent consumes ---------
    let a = spawnAnnotate(['rollout-plan.md', '--wait', '--no-open', '--session', SESSION, '--timeout', ANNOTATE_TIMEOUT, '--interval', '1']);
    cliChildren.push(a.child);
    const v1 = await waitNewHead('rollout-plan', null, 'plan V1 round on disk');
    await waitServerUp();
    child = launchCft({ cft, profileDir, debugPort: DEBUG_PORT, url: `http://127.0.0.1:${port}/${SESSION}/rollout-plan` });
    cdp = await connectPage(DEBUG_PORT, `/${SESSION}/rollout-plan`);
    await waitTabHead(v1, 'CfT shows plan V1');
    const mdClick = await cdp.evaluate(clickLineExpr);
    ok('A1 markdown: click a rendered line -> §5.2 source/line anchor (anchor type 1 of 3)',
      mdClick.kind === 'source' && Number(mdClick.line) === mdClick.srcLine,
      `line ${mdClick.srcLine} -> {kind:${mdClick.kind}, line:${mdClick.line}}`);
    await cdp.evaluate(addCommentExpr('Tighten the Goals section — state the idempotency guarantee up front.'));
    const subV1 = await send();
    const r1 = await a.done();
    const bundleV1 = JSON.parse(r1.stdout);
    const diskV1 = P.readJSON(P.roundJsonIn(path.join(aDirOf('rollout-plan'), v1), v1));
    // The CLI records `source` as the realpath; compare resolved paths (macOS /var -> /private/var).
    const sourceMatches = fs.realpathSync(bundleV1.source) === fs.realpathSync(planPath);
    ok('A2 markdown: CLI returns the {source,snapshot,feedback} bundle; round `submitted` on disk',
      r1.code === 0 && subV1 === 'submitted' && diskV1.status === 'submitted' && diskV1.feedback.length === 1 &&
        sourceMatches && bundleV1.snapshot === v1 && bundleV1.feedback.length === 1,
      `cli=${r1.code}; disk=${diskV1.status}; source=${sourceMatches}; bundle.snapshot=${bundleV1.snapshot === v1}`);

    // AGENT CONSUMES: edit the real `source` (never the snapshot) to address the comment.
    fs.writeFileSync(planPath, PLAN_MD.replace('## Goals\n', '## Goals\n\n_Idempotency: every submit is safe to retry; no in-flight feedback is ever lost._\n'));

    // --- Round V2: RE-PRESENT -> tab auto-advances -> human comments again -> submit -------
    a = spawnAnnotate(['rollout-plan.md', '--wait', '--no-open', '--session', SESSION, '--timeout', ANNOTATE_TIMEOUT, '--interval', '1']);
    cliChildren.push(a.child);
    const v2 = await waitNewHead('rollout-plan', v1, 'plan V2 round on disk');
    await waitTabHead(v2, 'CfT AUTO-ADVANCES plan V1 -> V2 (the re-presented response round)');
    ok('A3 markdown: re-present mints a new round AND the tab auto-advances to it (loop re-presents)',
      v2 !== v1, `V1=${v1.slice(-8)} V2=${v2.slice(-8)}`);
    await cdp.evaluate(clickLineExpr);
    await cdp.evaluate(addCommentExpr('Good — now add a one-line summary of the nonce mechanism to Risks.'));
    const subV2 = await send();
    await a.done();
    const diskV2 = P.readJSON(P.roundJsonIn(path.join(aDirOf('rollout-plan'), v2), v2));
    const snapV1Differs = !fs.readFileSync(P.findSnapshot(path.join(aDirOf('rollout-plan'), v2), v2)).equals(planV1Bytes);
    ok('A4 markdown: the response round (V2) carries the human feedback; its snapshot reflects the agent edit',
      subV2 === 'submitted' && diskV2.status === 'submitted' && diskV2.feedback.length === 1 && snapV1Differs,
      `V2.status=${diskV2.status}; snapshot-changed=${snapV1Differs}`);

    // AGENT CONSUMES again.
    fs.appendFileSync(planPath, '\nThe nonce makes a double-clicked submit idempotent: a replay returns the prior 200.\n');

    // --- Round V3: RE-PRESENT -> human LOOKS -> ACCEPT (head-checked) ----------------------
    a = spawnAnnotate(['rollout-plan.md', '--wait', '--no-open', '--session', SESSION, '--timeout', ANNOTATE_TIMEOUT, '--interval', '1']);
    cliChildren.push(a.child);
    const v3 = await waitNewHead('rollout-plan', v2, 'plan V3 round on disk');
    await waitTabHead(v3, 'CfT auto-advances V2 -> V3 (final response round)');
    const accV3 = await accept();
    const r3 = await a.done();
    const diskV3 = P.readJSON(P.roundJsonIn(path.join(aDirOf('rollout-plan'), v3), v3));
    ok('A5 markdown: human ACCEPTS the re-presented round -> `accepted` on disk; CLI unblocks',
      accV3 === 'accepted' && diskV3.status === 'accepted' && r3.code === 0,
      `accept=${accV3}; disk=${diskV3.status}; cli=${r3.code}`);
    ok('A6 markdown: round files transitioned present->submitted->accepted across 3 rounds (V1,V2 submitted; V3 accepted)',
      diskV1.status === 'submitted' && diskV2.status === 'submitted' && diskV3.status === 'accepted');

    // =====================================================================================
    // FORMAT B — CODE FILE: full loop round1(submit, code-line anchor) -> round2(accept)
    // =====================================================================================
    console.log('\n=== FORMAT B: Code file — full loop with a code-line anchor ===');
    let b = spawnAnnotate(['feedback-service.js', '--as-code', '--wait', '--no-open', '--session', SESSION, '--timeout', ANNOTATE_TIMEOUT, '--interval', '1']);
    cliChildren.push(b.child);
    const c1 = await waitNewHead('feedback-service', null, 'code round 1 on disk');
    await cdp.send('Page.navigate', { url: `http://127.0.0.1:${port}/${SESSION}/feedback-service` });
    await waitTabHead(c1, 'CfT shows code round 1');
    const codeClick = await cdp.evaluate(clickLineExpr);
    ok('B1 code: click a rendered line -> §5.2 source/line anchor (anchor type 2 of 3 — the PR-review bridge)',
      codeClick.kind === 'source' && Number(codeClick.line) === codeClick.srcLine,
      `line ${codeClick.srcLine} -> {kind:${codeClick.kind}, line:${codeClick.line}}`);
    await cdp.evaluate(addCommentExpr('applySubmit should also record the idempotency nonce before flipping status.'));
    const subC1 = await send();
    const rc1 = await b.done();
    const diskC1 = P.readJSON(P.roundJsonIn(path.join(aDirOf('feedback-service'), c1), c1));
    ok('B2 code: submit round-trips; CLI bundle carries the code-line feedback; round `submitted`',
      rc1.code === 0 && subC1 === 'submitted' && diskC1.status === 'submitted' && diskC1.feedback[0].anchor.kind === 'source' &&
        typeof diskC1.feedback[0].anchor.line === 'number',
      `disk=${diskC1.status}; anchor=${JSON.stringify(diskC1.feedback[0].anchor)}`);

    // AGENT CONSUMES: real source edit addressing the comment.
    fs.writeFileSync(codePath, SERVICE_JS.replace('  round.status = \'submitted\';', '  round.nonce = anchors.nonce; // record idempotency nonce before the flip\n  round.status = \'submitted\';'));

    let b2 = spawnAnnotate(['feedback-service.js', '--as-code', '--wait', '--no-open', '--session', SESSION, '--timeout', ANNOTATE_TIMEOUT, '--interval', '1']);
    cliChildren.push(b2.child);
    const c2 = await waitNewHead('feedback-service', c1, 'code round 2 on disk');
    await waitTabHead(c2, 'CfT auto-advances code round 1 -> 2 (re-presented response)');
    const accC2 = await accept();
    const rc2 = await b2.done();
    const diskC2 = P.readJSON(P.roundJsonIn(path.join(aDirOf('feedback-service'), c2), c2));
    ok('B3 code: present->submitted->accepted across 2 rounds incl. the re-presented response round',
      accC2 === 'accepted' && diskC2.status === 'accepted' && rc2.code === 0 && diskC1.status === 'submitted',
      `r1=${diskC1.status}; r2=${diskC2.status}`);

    // =====================================================================================
    // FORMAT C — IMAGE: full loop round1(point+box spatial anchors + screenshot) -> accept
    // =====================================================================================
    console.log('\n=== FORMAT C: Image — full loop with spatial point + box anchors ===');
    let im = spawnAnnotate(['architecture-diagram.png', '--wait', '--no-open', '--session', SESSION, '--timeout', ANNOTATE_TIMEOUT, '--interval', '1']);
    cliChildren.push(im.child);
    const i1 = await waitNewHead('architecture-diagram', null, 'image round 1 on disk');
    await cdp.send('Page.navigate', { url: `http://127.0.0.1:${port}/${SESSION}/architecture-diagram` });
    await waitTabHead(i1, 'CfT shows image round 1');
    const pt = await cdp.evaluate(gestureExpr(0.3, 0.4, 0.3, 0.4));
    const ptv = pt.point ? JSON.parse(pt.point) : null;
    ok('C1 image: click -> §5.2 spatial POINT anchor, normalized 0-1 (anchor type 3 of 3)',
      pt.kind === 'spatial' && Array.isArray(ptv) && ptv.length === 2 && ptv.every((n) => n >= 0 && n <= 1),
      `point=${pt.point}`);
    await cdp.evaluate(addCommentExpr('image: this node label is too low-contrast.'));
    const bx = await cdp.evaluate(gestureExpr(0.2, 0.25, 0.7, 0.8));
    const bxv = bx.box ? JSON.parse(bx.box) : null;
    ok('C2 image: drag -> §5.2 spatial BOX anchor [x,y,w,h], normalized 0-1, no crop',
      bx.kind === 'spatial' && Array.isArray(bxv) && bxv.length === 4 && bxv.every((n) => n >= 0 && n <= 1) && bxv[2] > 0 && bxv[3] > 0,
      `box=${bx.box}`);
    await cdp.evaluate(addCommentExpr('image: crop tighter around this subsystem.'));
    const subI1 = await send();
    const ri1 = await im.done();
    const diskI1 = P.readJSON(P.roundJsonIn(path.join(aDirOf('architecture-diagram'), i1), i1));
    const kinds = diskI1.feedback.map((f) => f.anchor && f.anchor.kind);
    const hasPoint = diskI1.feedback.some((f) => Array.isArray(f.anchor.point));
    const hasBox = diskI1.feedback.some((f) => Array.isArray(f.anchor.box));
    ok('C3 image: Send round-trips BOTH spatial anchors (point + box) to disk; round `submitted`',
      ri1.code === 0 && subI1 === 'submitted' && diskI1.status === 'submitted' && diskI1.feedback.length === 2 &&
        kinds.every((k) => k === 'spatial') && hasPoint && hasBox,
      `disk=${diskI1.status}; kinds=${JSON.stringify(kinds)}; point=${hasPoint} box=${hasBox}`);
    const shotPath = P.screenshotIn(path.join(aDirOf('architecture-diagram'), i1), i1);
    const shotOk = await waitFor('image screenshot file', async () => (fs.existsSync(shotPath) ? fs.statSync(shotPath) : null), { timeout: 8000 }).catch(() => null);
    ok('C4 image: a gated viewport screenshot was captured + stored (the visual leg, default-on)',
      !!shotOk && shotOk.size > 100, `exists=${!!shotOk}; bytes=${shotOk && shotOk.size}`);

    // AGENT CONSUMES: the image consumer regenerates addressing the spatial feedback (re-present).
    let im2 = spawnAnnotate(['architecture-diagram.png', '--wait', '--no-open', '--session', SESSION, '--timeout', ANNOTATE_TIMEOUT, '--interval', '1']);
    cliChildren.push(im2.child);
    const i2 = await waitNewHead('architecture-diagram', i1, 'image round 2 on disk');
    await waitTabHead(i2, 'CfT auto-advances image round 1 -> 2 (re-presented response)');
    const accI2 = await accept();
    const ri2 = await im2.done();
    const diskI2 = P.readJSON(P.roundJsonIn(path.join(aDirOf('architecture-diagram'), i2), i2));
    ok('C5 image: present->submitted->accepted across 2 rounds incl. the re-presented response round',
      accI2 === 'accepted' && diskI2.status === 'accepted' && ri2.code === 0 && diskI1.status === 'submitted',
      `r1=${diskI1.status}; r2=${diskI2.status}`);

    // =====================================================================================
    // REVERT + "what did it look like N rounds ago" (§5.4) — read OFF DISK
    // =====================================================================================
    console.log('\n=== REVERT: thread an older snapshot into a new round; recover it off disk ===');
    const token = P.readToken(P.sessionDir(dataDir, SESSION));
    // Open a fresh markdown round (V4) fire-and-forget — no CfT needed for the seam.
    const v4spawn = spawnAnnotate(['rollout-plan.md', '--no-wait', '--no-open', '--session', SESSION]);
    cliChildren.push(v4spawn.child);
    await v4spawn.done();
    const v4 = await waitNewHead('rollout-plan', v3, 'plan V4 (revert round) on disk');
    // The §5.5 wire contract the version UI WILL send (the UI control is a deferred Could-Have;
    // the server `snapshot`-pointer seam is built in v1): revertTarget = V1's guid.
    const revRes = await postJSON('/feedback', {
      session: SESSION, artifact: 'rollout-plan', head: v4,
      anchors: [{ id: 'r1', type: 'comment', anchor: { kind: 'source', line: 3 }, comment: 'Revert: the original Goals framing read better.' }],
      revertTarget: v1, screenshot: null, nonce: `e2e-revert-${Date.now()}`,
    }, token);
    const revBody = await revRes.json();
    const diskV4 = P.readJSON(P.roundJsonIn(path.join(aDirOf('rollout-plan'), v4), v4));
    ok('R1 revert: /feedback with revertTarget=<V1> sets the round `snapshot` pointer to V1 (§5.4 value-swap)',
      revRes.status === 200 && revBody.status === 'submitted' && diskV4.snapshot === v1,
      `http=${revRes.status}; V4.snapshot==V1=${diskV4.snapshot === v1}`);
    // The CLI's poll resolves the snapshot pointer in the bundle it hands the agent.
    const pollA = spawnAnnotate(['poll', `${SESSION}/rollout-plan`, '--timeout', '30s', '--interval', '1']);
    cliChildren.push(pollA.child);
    const pollR = await pollA.done();
    const pollBundle = JSON.parse(pollR.stdout);
    ok('R2 revert: `annotate poll` hands the agent a bundle whose `snapshot` resolves to V1 (not V4)',
      pollR.code === 0 && pollBundle.snapshot === v1,
      `bundle.snapshot==V1=${pollBundle.snapshot === v1}`);
    // The agent reads THAT snapshot off disk — recovering exactly the round-1 content (3 rounds ago).
    const v1SnapOnDisk = fs.readFileSync(P.findSnapshot(path.join(aDirOf('rollout-plan'), pollBundle.snapshot), pollBundle.snapshot));
    ok('R3 revert: the agent reads the V1 snapshot OFF DISK and recovers "what it looked like 3 rounds ago"',
      v1SnapOnDisk.equals(planV1Bytes),
      `recovered ${v1SnapOnDisk.length} bytes == original V1 ${planV1Bytes.length} bytes`);

    // =====================================================================================
    // ANTI-LAZINESS (§2.7) — a fire-and-forget accept of an unseen round is impossible
    // =====================================================================================
    console.log('\n=== ANTI-LAZINESS: accept is head-checked; an unseen newer round cannot be finalized ===');
    // The human reviewed V4. The agent re-launches (mints V5) BEFORE the human clicks accept.
    const v5spawn = spawnAnnotate(['rollout-plan.md', '--no-wait', '--no-open', '--session', SESSION]);
    cliChildren.push(v5spawn.child);
    await v5spawn.done();
    const v5 = await waitNewHead('rollout-plan', v4, 'plan V5 (unseen newer round) on disk');
    // The human's tab still believes V4 is head -> their accept carries head=V4.
    const staleAccept = await postJSON(`/${SESSION}/rollout-plan/accept`, { head: v4 }, token);
    const staleBody = await staleAccept.json();
    const v4After = P.readJSON(P.roundJsonIn(path.join(aDirOf('rollout-plan'), v4), v4));
    ok('L1 anti-laziness: accepting the round the human SAW (V4) after V5 was minted -> 409 stale-head',
      staleAccept.status === 409 && staleBody.error === 'stale-head' && staleBody.head === v5,
      `http=${staleAccept.status}; err=${staleBody.error}; head->V5=${staleBody.head === v5}`);
    ok('L2 anti-laziness: the stale accept left V4 UNFINALIZED (a fire-and-forget accept is structurally prevented)',
      v4After.status !== 'accepted', `V4.status=${v4After.status}`);

    // =====================================================================================
    // COLD RESUME (§2.3) — a FRESH process reconstructs the whole history off disk
    // =====================================================================================
    console.log('\n=== COLD RESUME: a fresh process reconstructs the round history purely off disk ===');
    const reconstructSrc = `
      const P = require(process.env.PROTO);
      const path = require('node:path');
      const fs = require('node:fs');
      const aDir = P.artifactDir(process.env.DATA, process.env.SESS, process.env.ART);
      const guids = P.listRoundGuids(aDir);
      const hist = guids.map((g, i) => {
        const r = P.readJSON(P.roundJsonIn(path.join(aDir, g), g));
        const snap = P.findSnapshot(path.join(aDir, g), g);
        return { v: i + 1, guid: g, status: r.status, snapshotPtr: r.snapshot, snapshotBytes: snap ? fs.statSync(snap).size : 0 };
      });
      process.stdout.write(JSON.stringify(hist));
    `;
    const cold = await new Promise((resolve) => {
      const c = spawn(process.execPath, ['-e', reconstructSrc], {
        env: { ...process.env, PROTO, DATA: dataDir, SESS: SESSION, ART: 'rollout-plan' },
      });
      let out = '';
      c.stdout.on('data', (d) => (out += d));
      c.on('close', () => resolve(out));
    });
    const hist = JSON.parse(cold);
    const statuses = hist.map((h) => h.status);
    const v4Entry = hist.find((h) => h.guid === v4);
    ok('CR1 cold resume: a fresh process reconstructs all 5 rounds off disk with correct statuses',
      hist.length === 5 && statuses[0] === 'submitted' && statuses[1] === 'submitted' && statuses[2] === 'accepted' &&
        statuses[3] === 'submitted' && statuses[4] === 'pending',
      JSON.stringify(statuses));
    ok('CR2 cold resume: the revert basis is recovered off disk too (V4.snapshotPtr -> V1)',
      v4Entry && v4Entry.snapshotPtr === v1,
      `V4.snapshotPtr==V1=${v4Entry && v4Entry.snapshotPtr === v1}`);
    console.log('reconstructed history (fresh process):', hist.map((h) => `V${h.v}:${h.status}${h.snapshotPtr ? ' (rev->' + h.snapshotPtr.slice(-8) + ')' : ''}`).join('  '));

    const failed = checks.filter((c) => !c.pass);
    console.log(`\n${failed.length ? 'E2E GATE FAILED' : 'E2E GATE PASSED'} — ${checks.length - failed.length}/${checks.length} checks passed`);
    return failed.length === 0 ? 0 : 1;
  } finally {
    try { if (cdp) cdp.close(); } catch {}
    try { if (child) child.kill('SIGTERM'); } catch {}
    for (const c of cliChildren) { try { c.kill('SIGTERM'); } catch {} }
    await sleep(300);
    try { if (child && !child.killed) child.kill('SIGKILL'); } catch {}
    // The CLI started the server detached (nohup) — kill it via the recorded pid.
    try {
      const pid = parseInt(fs.readFileSync(path.join(dataDir, 'server.pid'), 'utf8').trim(), 10);
      if (pid) process.kill(pid, 'SIGTERM');
    } catch {}
    await sleep(200);
    try { fs.rmSync(dataDir, { recursive: true, force: true }); } catch {}
    try { fs.rmSync(workDir, { recursive: true, force: true }); } catch {}
  }
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('E2E GATE ERROR:', err && err.stack ? err.stack : err);
    process.exit(2);
  });
