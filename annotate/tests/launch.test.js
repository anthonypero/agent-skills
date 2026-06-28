'use strict';

// Launch-script tests (tech-requirements §6.1; T5). Shells out to the real POSIX
// `bin/annotate` against a temp data dir + a test runtime.json, with the browser-open
// step stubbed (--no-open / ANNOTATE_OPEN_CMD). The human's submit is simulated by the
// test POSTing /feedback to the server the script started. Run with: npm test
//
// Coverage maps to the T5 exit gate:
//   - `annotate <file> --wait` creates the round + stub, starts the server, blocks, and
//     prints the {source,snapshot,feedback} bundle on STDOUT when a submit lands
//   - `annotate poll <s>/<a>` blocks on the EXISTING head and mints NO new round
//   - `--wait` timeout exits non-zero with the nudge
//   - the lazy singleton does not start a second server when one is already up
//   - render mode rides in the opened URL (?render=…) via the injectable open seam

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const net = require('node:net');
const path = require('node:path');
const { spawn } = require('node:child_process');

const P = require('../server/protocol.js');

const PKG_ROOT = path.join(__dirname, '..');
const BIN = path.join(PKG_ROOT, 'bin', 'annotate');
const FIX = path.join(__dirname, 'fixtures');
const SAMPLE = path.join(FIX, 'sample.md');

let dataDir;
let runtimePath;
let port;

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

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

// Spawn bin/annotate; collect stdout/stderr; expose a `done` promise.
function run(args, extraEnv = {}) {
  const child = spawn(BIN, args, {
    env: { ...process.env, ANNOTATE_RUNTIME: runtimePath, ...extraEnv },
  });
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (d) => (stdout += d));
  child.stderr.on('data', (d) => (stderr += d));
  const done = new Promise((resolve) => {
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
  return { child, done };
}

async function waitForServer(timeoutMs = 8000) {
  const t0 = Date.now();
  for (;;) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/loaded`);
      if (res.ok) {
        await res.text();
        return;
      }
    } catch {
      /* not up yet */
    }
    if (Date.now() - t0 > timeoutMs) throw new Error('server never came up');
    await delay(100);
  }
}

function findRound(session, artifact = 'sample') {
  const aDir = P.artifactDir(dataDir, session, artifact);
  const head = P.resolveHead(aDir);
  if (!head) return null;
  return { session, artifact, guid: head, token: P.readToken(P.sessionDir(dataDir, session)) };
}

async function waitForRound(session, artifact = 'sample', timeoutMs = 8000) {
  const t0 = Date.now();
  for (;;) {
    const r = findRound(session, artifact);
    if (r && r.token) return r;
    if (Date.now() - t0 > timeoutMs) throw new Error('round never appeared on disk');
    await delay(80);
  }
}

async function postFeedback(r, anchors) {
  const body = {
    session: r.session,
    artifact: r.artifact,
    head: r.guid,
    anchors: anchors || [{ id: 'a1', type: 'comment', anchor: { kind: 'source', line: 3 }, comment: 'tighten this' }],
    revertTarget: null,
    screenshot: null,
    nonce: 'n-' + Math.random().toString(36).slice(2),
  };
  return fetch(`http://127.0.0.1:${port}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Annotate-Token': r.token },
    body: JSON.stringify(body),
  });
}

function roundCount(session, artifact = 'sample') {
  return P.listRoundGuids(P.artifactDir(dataDir, session, artifact)).length;
}

// ---------------------------------------------------------------------------

test.before(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-launch-'));
  port = await freePort();
  runtimePath = path.join(dataDir, 'runtime.json');
  fs.writeFileSync(
    runtimePath,
    JSON.stringify({
      port,
      paths: { data: dataDir, extension: path.join(dataDir, 'extension'), profile: path.join(dataDir, 'chrome-profile') },
      browser: { path: '', kind: '' },
      cftBuildId: '',
    })
  );
});

test.after(async () => {
  // Kill the singleton server the script started (nohup keeps it alive past the script).
  try {
    const pid = Number(fs.readFileSync(path.join(dataDir, 'server.pid'), 'utf8').trim());
    if (pid) process.kill(pid);
  } catch {
    /* already gone */
  }
  await delay(150);
  if (dataDir) fs.rmSync(dataDir, { recursive: true, force: true });
});

// ===========================================================================

test('annotate <file> --wait: creates the round + stub, blocks, prints {source,snapshot,feedback} on submit', async () => {
  const session = 'wait-1';
  const h = run([SAMPLE, '--wait', '--no-open', '--session', session]);

  const r = await waitForRound(session);
  // The 4-field pending stub is on disk (the one creation write, §2.2/§5.1).
  const round = P.readJSON(P.roundJsonPath(P.artifactDir(dataDir, session, 'sample'), r.guid));
  assert.deepEqual(Object.keys(round).sort(), ['feedback', 'snapshot', 'source', 'status']);
  assert.equal(round.status, 'pending');

  await waitForServer();
  const res = await postFeedback(r);
  assert.equal(res.status, 200);

  const { code, stdout } = await h.done;
  assert.equal(code, 0, 'exits 0 once the submit lands');

  const bundle = JSON.parse(stdout); // STDOUT carries ONLY the bundle
  assert.deepEqual(Object.keys(bundle).sort(), ['feedback', 'snapshot', 'source']);
  assert.equal(bundle.source, SAMPLE);
  assert.equal(bundle.snapshot, r.guid, 'null pointer resolves to the round own guid (§5.4)');
  assert.equal(bundle.feedback.length, 1);
  assert.equal(bundle.feedback[0].id, 'a1');
});

test('annotate poll <s>/<a>: blocks on the EXISTING head and mints NO new round', async () => {
  const session = 'poll-1';

  // Seed one round with --no-wait (opens + returns immediately, leaves the server up).
  const seed = await run([SAMPLE, '--no-wait', '--no-open', '--session', session]).done;
  assert.equal(seed.code, 0);
  assert.equal(roundCount(session), 1, 'one round after the --no-wait seed');

  const r = findRound(session);
  assert.ok(r, 'seed round exists');

  const h = run(['poll', `${session}/sample`, '--no-open']);
  await waitForServer();
  // give poll a beat to attach to the existing head before the submit lands
  await delay(300);
  const res = await postFeedback(r);
  assert.equal(res.status, 200);

  const { code, stdout } = await h.done;
  assert.equal(code, 0);
  const bundle = JSON.parse(stdout);
  assert.equal(bundle.feedback.length, 1);
  assert.equal(bundle.source, SAMPLE);

  assert.equal(roundCount(session), 1, 'poll minted NO new round');
});

test('poll bundle surfaces on-disk attachmentPaths[] for MULTIPLE attached images (v2.6 §5)', async () => {
  const session = 'attach-poll';

  // Seed a round + server (leaves the singleton up).
  const seed = await run([SAMPLE, '--no-wait', '--no-open', '--session', session]).done;
  assert.equal(seed.code, 0);
  const r = findRound(session);
  assert.ok(r, 'seed round exists');
  await waitForServer();

  // Upload TWO attachments into the round folder ON SELECT (POST /attach); each gets a unique name.
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47]).toString('base64');
  async function upload(name) {
    const up = await fetch(`http://127.0.0.1:${port}/${r.session}/${r.artifact}/attach`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Annotate-Token': r.token },
      body: JSON.stringify({ head: r.guid, data: png, mime: 'image/png', name }),
    });
    assert.equal(up.status, 200);
    return (await up.json()).filename;
  }
  const stored1 = await upload('illustration-1.png');
  const stored2 = await upload('illustration-2.png');
  assert.match(stored1, /-attach-1\.png$/);
  assert.match(stored2, /-attach-2\.png$/);

  const rDir = path.join(P.artifactDir(dataDir, r.session, r.artifact), r.guid);
  assert.ok(P.exists(path.join(rDir, stored1)), 'attachment 1 copied into the round dir on select');
  assert.ok(P.exists(path.join(rDir, stored2)), 'attachment 2 copied into the round dir on select');

  // Poll, then submit a comment that REFERENCES BOTH stored filenames via the plural array.
  const h = run(['poll', `${session}/sample`, '--no-open']);
  await waitForServer();
  await delay(300);
  const res = await postFeedback(r, [
    { id: 'a1', type: 'comment', anchor: { kind: 'source', line: 3 }, comment: 'see attached', attachments: [stored1, stored2] },
  ]);
  assert.equal(res.status, 200);

  const { code, stdout } = await h.done;
  assert.equal(code, 0);
  const bundle = JSON.parse(stdout);
  assert.deepEqual(bundle.feedback[0].attachments, [stored1, stored2], 'feedback item keeps both stored filenames');
  assert.deepEqual(
    bundle.feedback[0].attachmentPaths,
    [path.join(rDir, stored1), path.join(rDir, stored2)],
    'poll resolves absolute on-disk attachmentPaths[] under the round dir, in order'
  );
  for (const p of bundle.feedback[0].attachmentPaths) {
    assert.ok(P.exists(p), 'each surfaced path points at a real file');
  }
});

test('poll still resolves a LEGACY singular attachment to attachmentPaths[] (back-compat)', async () => {
  const session = 'attach-poll-legacy';

  const seed = await run([SAMPLE, '--no-wait', '--no-open', '--session', session]).done;
  assert.equal(seed.code, 0);
  const r = findRound(session);
  assert.ok(r, 'seed round exists');
  await waitForServer();

  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47]).toString('base64');
  const up = await fetch(`http://127.0.0.1:${port}/${r.session}/${r.artifact}/attach`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Annotate-Token': r.token },
    body: JSON.stringify({ head: r.guid, data: png, mime: 'image/png', name: 'legacy.png' }),
  });
  assert.equal(up.status, 200);
  const stored = (await up.json()).filename;

  const rDir = path.join(P.artifactDir(dataDir, r.session, r.artifact), r.guid);

  const h = run(['poll', `${session}/sample`, '--no-open']);
  await waitForServer();
  await delay(300);
  // Old client shape: singular `attachment` (deprecated alias) — must still resolve.
  const res = await postFeedback(r, [
    { id: 'a1', type: 'comment', anchor: { kind: 'source', line: 3 }, comment: 'see attached', attachment: stored },
  ]);
  assert.equal(res.status, 200);

  const { code, stdout } = await h.done;
  assert.equal(code, 0);
  const bundle = JSON.parse(stdout);
  assert.deepEqual(
    bundle.feedback[0].attachmentPaths,
    [path.join(rDir, stored)],
    'a legacy singular attachment resolves into the attachmentPaths[] array'
  );
  assert.ok(P.exists(bundle.feedback[0].attachmentPaths[0]), 'the surfaced path points at a real file');
});

test('annotate <file> --wait --timeout: times out non-zero with the hand-off nudge', async () => {
  const session = 'timeout-1';
  const { code, stderr } = await run([SAMPLE, '--wait', '--timeout', '1', '--no-open', '--session', session]).done;
  assert.notEqual(code, 0, 'a timed-out --wait exits non-zero');
  assert.match(stderr, /review still open|re-run to collect feedback/);
});

test('render mode rides in the opened URL (?render=…) via the injectable open seam', async () => {
  const session = 'render-seam';
  const openlog = path.join(dataDir, 'openlog.txt');
  // ANNOTATE_OPEN_CMD is a program (argv prefix) that receives the URL as its last arg;
  // a tiny capture script stands in for the browser and records the URL it was opened at.
  const capture = path.join(dataDir, 'capture.sh');
  fs.writeFileSync(capture, '#!/bin/sh\nprintf "%s\\n" "$1" >> "$OPENLOG"\n');
  fs.chmodSync(capture, 0o755);

  const seed = await run([SAMPLE, '--no-wait', '--as-code', '--session', session], {
    ANNOTATE_OPEN_CMD: capture,
    OPENLOG: openlog,
  }).done;
  assert.equal(seed.code, 0);

  // open cmd is backgrounded -> poll for the captured URL line
  let line = '';
  for (let i = 0; i < 50 && !line; i++) {
    try {
      line = fs.readFileSync(openlog, 'utf8').trim();
    } catch {
      /* not yet */
    }
    if (!line) await delay(100);
  }
  assert.match(line, new RegExp(`/${session}/sample\\?render=render-as-code$`), 'URL carries the render mode');
  assert.match(line, new RegExp(`^http://127\\.0\\.0\\.1:${port}/`), 'URL points at the runtime port');
});

test('lazy singleton: a second launch reuses the running server (no second process)', async () => {
  const session = 'singleton';

  const first = await run([SAMPLE, '--no-wait', '--no-open', '--session', session]).done;
  assert.equal(first.code, 0);
  const pid1 = fs.readFileSync(path.join(dataDir, 'server.pid'), 'utf8').trim();
  assert.ok(pid1, 'a server pid was recorded');

  const second = await run([SAMPLE, '--no-wait', '--no-open', '--session', session]).done;
  assert.equal(second.code, 0);
  assert.match(second.stderr, /reusing/, 'second launch reports reusing the existing server');

  const pid2 = fs.readFileSync(path.join(dataDir, 'server.pid'), 'utf8').trim();
  assert.equal(pid2, pid1, 'pidfile unchanged -> no second server was started');
});
