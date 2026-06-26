'use strict';

// Server + file-protocol integration tests (tech-requirements §2.2, §2.4, §5.1, §5.4,
// §5.5, §6.3, §6.6; T4). Drives the running server over Node `fetch` AND asserts the
// on-disk round-file protocol directly. Run with: npm test
//
// Coverage maps to the T4 exit gate:
//   - full round-trip POST /feedback splices feedback + flips status to submitted (on disk)
//   - POST /accept is head-checked: stale head OR already-accepted -> 409
//   - missing/bad token OR foreign Origin (CSRF) -> 403
//   - invalid anchor body -> 400
//   - replayed nonce is idempotent (no double-apply)
//   - a crashed/interrupted write leaves the prior round file intact (atomic temp+rename)

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { start, loadRuntime } = require('../server/server.js');
const { create } = require('../server/create.js');
const P = require('../server/protocol.js');

const FIX = path.join(__dirname, 'fixtures');

let srv;
let dataDir;

test.before(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-srv-'));
  srv = await start({ dataDir, port: 0 });
});

test.after(async () => {
  if (srv) await srv.close();
  if (dataDir) fs.rmSync(dataDir, { recursive: true, force: true });
});

// ---- helpers ---------------------------------------------------------------

const BASE = () => srv.url;

function post(p, body, headers = {}) {
  return fetch(BASE() + p, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
}

// Create a fresh round (unique session each time -> test isolation in one dataDir).
function freshRound(srcFile = 'sample.md') {
  return create({ dataDir, source: path.join(FIX, srcFile) });
}

const tokenHeader = (token) => ({ 'X-Annotate-Token': token });

function anchorComment(id = 'a1', line = 3) {
  return { id, type: 'comment', anchor: { kind: 'source', line }, comment: 'tighten this' };
}

function feedbackBundle(r, over = {}) {
  return {
    session: r.session,
    artifact: r.artifact,
    head: r.guid,
    anchors: [anchorComment()],
    revertTarget: null,
    screenshot: null,
    nonce: 'nonce-' + Math.random().toString(36).slice(2),
    ...over,
  };
}

// ===========================================================================
// create.js — IDs, layout, 0700 dirs, snapshot copy, 4-field stub, token
// ===========================================================================

test('create.js writes the 4-field pending stub, copies the snapshot, mints a token', () => {
  const r = freshRound('sample.md');

  // Round descriptor: EXACTLY the four §5.1 fields, pending stub.
  const round = P.readJSON(r.roundFile);
  assert.deepEqual(Object.keys(round).sort(), ['feedback', 'snapshot', 'source', 'status']);
  assert.equal(round.status, 'pending');
  assert.equal(round.snapshot, null);
  assert.deepEqual(round.feedback, []);
  assert.equal(round.source, path.join(FIX, 'sample.md'));

  // Snapshot byte-copy exists alongside (written BEFORE the stub, §6.1).
  assert.ok(r.snapshot && r.snapshot.startsWith(r.guid + '-snapshot'));
  assert.ok(P.exists(path.join(r.roundDir, r.snapshot)));

  // Per-session token sidecar (session.json), not in the descriptor.
  assert.equal(typeof r.token, 'string');
  assert.equal(P.readToken(P.sessionDir(dataDir, r.session)), r.token);
});

test('create.js: data dir + session/artifact/round dirs are 0700 (§6.3)', () => {
  const r = freshRound();
  for (const d of [dataDir, P.sessionDir(dataDir, r.session), P.artifactDir(dataDir, r.session, r.artifact), r.roundDir]) {
    const mode = fs.statSync(d).mode & 0o777;
    assert.equal(mode, 0o700, `${d} should be 0700, got ${mode.toString(8)}`);
  }
});

test('create.js: same source re-resolves to the same artifact id; head resolution is ls-order', () => {
  const session = 'sess-reopen';
  const a = create({ dataDir, source: path.join(FIX, 'sample.md'), session });
  const b = create({ dataDir, source: path.join(FIX, 'sample.md'), session });
  assert.equal(a.artifact, b.artifact, 'same source -> same artifact id (rounds accumulate)');

  // Head is the latest round by ls-order with a written descriptor (§6.3).
  const head = P.resolveHead(P.artifactDir(dataDir, session, a.artifact));
  assert.equal(head, b.guid > a.guid ? b.guid : a.guid);
});

// ===========================================================================
// GET routes — render, head, static, runtime
// ===========================================================================

test('GET /<session>/<artifact> renders the head snapshot with data-src-line (T2)', async () => {
  const r = freshRound('sample.md');
  const res = await fetch(`${BASE()}/${r.session}/${r.artifact}`);
  assert.equal(res.status, 200);
  assert.match(res.headers.get('content-type') || '', /text\/html/);
  const html = await res.text();
  assert.match(html, /data-src-line=/);
});

test('GET /<session>/<artifact>/head returns the head guid + status + changeToken', async () => {
  const r = freshRound();
  const res = await fetch(`${BASE()}/${r.session}/${r.artifact}/head`);
  assert.equal(res.status, 200);
  const j = await res.json();
  assert.equal(j.head, r.guid);
  assert.equal(j.status, 'pending');
  assert.ok(j.changeToken.includes(r.guid));
});

test('GET /static/* serves extension assets with a path-traversal guard', async () => {
  const ok = await fetch(`${BASE()}/static/submit.js`);
  assert.equal(ok.status, 200);
  assert.match(ok.headers.get('content-type') || '', /javascript/);

  const traverse = await fetch(`${BASE()}/static/../package.json`);
  assert.ok(traverse.status === 403 || traverse.status === 404, 'traversal must not escape extension/');
});

test('loadRuntime reads the §6.6 runtime.json (port + browser path/kind)', () => {
  const cfg = loadRuntime(path.join(FIX, 'runtime.json'));
  assert.equal(cfg.port, 7878);
  assert.equal(cfg.browser.kind, 'cft');
  assert.ok(cfg.browser.path.length > 0);
});

// ===========================================================================
// POST /feedback — the full §5.5 round-trip
// ===========================================================================

test('round-trip: POST /feedback splices feedback + flips status to submitted (on disk)', async () => {
  const r = freshRound();
  const bundle = feedbackBundle(r, { anchors: [anchorComment('a1', 3), anchorComment('a2', 5)] });
  const res = await post('/feedback', bundle, tokenHeader(r.token));
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { status: 'submitted', head: r.guid });

  // Verify ON DISK: feedback spliced verbatim, status flipped.
  const round = P.readJSON(r.roundFile);
  assert.equal(round.status, 'submitted');
  assert.deepEqual(round.feedback.map((f) => f.id), ['a1', 'a2']);
  assert.equal(round.feedback[0].comment, 'tighten this');

  // The nonce sidecar was written (idempotency record), NOT a 5th descriptor field.
  assert.ok(P.exists(P.nonceIn(r.roundDir, r.guid)));
  assert.deepEqual(Object.keys(round).sort(), ['feedback', 'snapshot', 'source', 'status']);
});

test('POST /feedback revert: revertTarget sets the snapshot pointer (§5.4)', async () => {
  const r = freshRound();
  const priorGuid = '20200101T000000000-deadbeef';
  const res = await post('/feedback', feedbackBundle(r, { revertTarget: priorGuid }), tokenHeader(r.token));
  assert.equal(res.status, 200);
  const round = P.readJSON(r.roundFile);
  assert.equal(round.snapshot, priorGuid, 'snapshot pointer set to the revert target');
  assert.equal(round.status, 'submitted');
});

test('POST /feedback writes a base64 screenshot to <guid>-screenshot.png (never inlined)', async () => {
  const r = freshRound();
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47]).toString('base64');
  const res = await post('/feedback', feedbackBundle(r, { screenshot: png }), tokenHeader(r.token));
  assert.equal(res.status, 200);
  assert.ok(P.exists(P.screenshotIn(r.roundDir, r.guid)));
  const round = P.readJSON(r.roundFile);
  assert.equal(round.screenshot, undefined, 'screenshot is a sibling file, not a descriptor field');
});

// ---- head-staleness (§5.5) -------------------------------------------------

test('POST /feedback with a stale head -> 409 + the current head', async () => {
  const r = freshRound();
  const res = await post('/feedback', feedbackBundle(r, { head: 'totally-stale-guid' }), tokenHeader(r.token));
  assert.equal(res.status, 409);
  const j = await res.json();
  assert.equal(j.error, 'stale-head');
  assert.equal(j.head, r.guid);
  // File untouched.
  assert.equal(P.readJSON(r.roundFile).status, 'pending');
});

test('POST /feedback onto an already-submitted head (different nonce) -> 409', async () => {
  const r = freshRound();
  assert.equal((await post('/feedback', feedbackBundle(r), tokenHeader(r.token))).status, 200);
  // Second submit, SAME head guid (a submit does not advance the head) but a NEW nonce.
  const res = await post('/feedback', feedbackBundle(r), tokenHeader(r.token));
  assert.equal(res.status, 409, 'cannot splice onto a closed (submitted) round');
});

// ---- auth / CSRF (§6.3) ----------------------------------------------------

test('POST /feedback with a missing token -> 403', async () => {
  const r = freshRound();
  const res = await post('/feedback', feedbackBundle(r)); // no X-Annotate-Token
  assert.equal(res.status, 403);
  assert.equal(P.readJSON(r.roundFile).status, 'pending', 'file untouched on 403');
});

test('POST /feedback with a wrong token -> 403', async () => {
  const r = freshRound();
  const res = await post('/feedback', feedbackBundle(r), tokenHeader('not-the-token'));
  assert.equal(res.status, 403);
});

test('CSRF: POST /feedback from a foreign Origin -> 403 (even with a valid token)', async () => {
  const r = freshRound();
  const res = await post('/feedback', feedbackBundle(r), {
    ...tokenHeader(r.token),
    Origin: 'https://evil.example',
  });
  assert.equal(res.status, 403, 'a visited website must not be able to forge feedback');
  assert.equal(P.readJSON(r.roundFile).status, 'pending');

  // A loopback Origin (a legitimate live dev page) with a valid token is allowed.
  const ok = await post('/feedback', feedbackBundle(r), {
    ...tokenHeader(r.token),
    Origin: 'http://localhost:3000',
  });
  assert.equal(ok.status, 200);
});

// ---- schema validation (§5.5) ----------------------------------------------

test('POST /feedback with an invalid anchor body -> 400 (file untouched)', async () => {
  const r = freshRound();
  // An edit anchor missing the conditionally-required `replacement`.
  const bad = { id: 'a1', type: 'edit', anchor: { kind: 'source', line: 3 }, original: 'x' };
  const res = await post('/feedback', feedbackBundle(r, { anchors: [bad] }), tokenHeader(r.token));
  assert.equal(res.status, 400);
  assert.equal(P.readJSON(r.roundFile).status, 'pending');
});

// ---- idempotency (§5.5) ----------------------------------------------------

test('replayed nonce is idempotent: second identical POST does not double-apply', async () => {
  const r = freshRound();
  const bundle = feedbackBundle(r, { anchors: [anchorComment('a1', 3)] });

  const first = await post('/feedback', bundle, tokenHeader(r.token));
  assert.equal(first.status, 200);

  // Replay the EXACT same bundle (same nonce). Returns the prior 200, no re-splice.
  const second = await post('/feedback', bundle, tokenHeader(r.token));
  assert.equal(second.status, 200);
  assert.deepEqual(await second.json(), { status: 'submitted', head: r.guid });

  const round = P.readJSON(r.roundFile);
  assert.equal(round.feedback.length, 1, 'feedback applied exactly once');
  assert.deepEqual(round.feedback.map((f) => f.id), ['a1']);
});

// ===========================================================================
// POST /accept — head-checked finalize (§5.5, §6.3, §2.7)
// ===========================================================================

test('accept from a pending head (accept-on-first-look) -> 200 accepted (on disk)', async () => {
  const r = freshRound();
  const res = await post(`/${r.session}/${r.artifact}/accept`, { head: r.guid }, tokenHeader(r.token));
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { status: 'accepted', head: r.guid });
  assert.equal(P.readJSON(r.roundFile).status, 'accepted');
});

test('accept from a submitted head -> 200 accepted', async () => {
  const r = freshRound();
  assert.equal((await post('/feedback', feedbackBundle(r), tokenHeader(r.token))).status, 200);
  const res = await post(`/${r.session}/${r.artifact}/accept`, { head: r.guid }, tokenHeader(r.token));
  assert.equal(res.status, 200);
  assert.equal(P.readJSON(r.roundFile).status, 'accepted');
});

test('accept with a stale head -> 409', async () => {
  const r = freshRound();
  const res = await post(`/${r.session}/${r.artifact}/accept`, { head: 'stale' }, tokenHeader(r.token));
  assert.equal(res.status, 409);
  assert.equal((await res.json()).error, 'stale-head');
  assert.equal(P.readJSON(r.roundFile).status, 'pending');
});

test('accept an already-accepted head -> 409 (already-resolved)', async () => {
  const r = freshRound();
  assert.equal((await post(`/${r.session}/${r.artifact}/accept`, { head: r.guid }, tokenHeader(r.token))).status, 200);
  const again = await post(`/${r.session}/${r.artifact}/accept`, { head: r.guid }, tokenHeader(r.token));
  assert.equal(again.status, 409);
  assert.equal((await again.json()).error, 'already-accepted');
});

test('accept with a missing/foreign token -> 403', async () => {
  const r = freshRound();
  assert.equal((await post(`/${r.session}/${r.artifact}/accept`, { head: r.guid })).status, 403);
  const csrf = await post(`/${r.session}/${r.artifact}/accept`, { head: r.guid }, {
    ...tokenHeader(r.token),
    Origin: 'https://evil.example',
  });
  assert.equal(csrf.status, 403);
  assert.equal(P.readJSON(r.roundFile).status, 'pending');
});

// ===========================================================================
// Live-page URL registration (§5.3/§6.4)
// ===========================================================================

test('live-page round: source=null, URL registered + resolvable via GET /resolve', async () => {
  const url = 'http://localhost:5173/dashboard';
  const r = create({ dataDir, url, session: 'sess-live' });
  assert.equal(r.source, null, 'unowned live page -> source null (§5.1)');
  assert.equal(r.snapshot, null, 'no byte-copy snapshot for a live page (§5.3)');
  assert.equal(P.readJSON(r.roundFile).source, null);

  const res = await fetch(`${BASE()}/resolve?url=${encodeURIComponent(url)}`);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { session: 'sess-live', artifact: r.artifact, head: r.guid });
});

// ===========================================================================
// Atomic writes (§2.4) — a crashed write leaves the prior file intact
// ===========================================================================

test('atomic temp+rename: an interrupted write leaves the prior round file intact', () => {
  const r = freshRound();
  const original = fs.readFileSync(r.roundFile, 'utf8');

  // Simulate a crash DURING the rename step (the atomic commit point). The prior file
  // must survive untouched, and no partial temp file may be left at the destination.
  const realRename = fs.renameSync;
  fs.renameSync = () => {
    throw new Error('simulated crash mid-rename');
  };
  let threw = false;
  try {
    P.atomicWriteJSON(r.roundFile, { source: null, snapshot: null, status: 'submitted', feedback: [] });
  } catch {
    threw = true;
  } finally {
    fs.renameSync = realRename;
  }

  assert.ok(threw, 'the interrupted write surfaced the error');
  assert.equal(fs.readFileSync(r.roundFile, 'utf8'), original, 'prior round file is byte-for-byte intact');

  // No leftover temp file in the round dir (it is cleaned up on a failed rename).
  const leftovers = fs.readdirSync(r.roundDir).filter((n) => n.endsWith('.tmp'));
  assert.deepEqual(leftovers, [], 'no orphaned temp file remains');

  // The writer still works normally after the simulated crash.
  P.atomicWriteJSON(r.roundFile, { source: null, snapshot: null, status: 'submitted', feedback: [] });
  assert.equal(P.readJSON(r.roundFile).status, 'submitted');
});
