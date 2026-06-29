'use strict';

// Unit tests for the T6a extension pure logic: Annotate.config (config discovery + the real
// fetch-backed sinks that replace T3's stand-in) — tech-requirements §6.4, §5.5, §6.6.
// The DOM/event wiring in content.js is browser-bound and covered by the integration gate
// (tests/integration/extension-gate.js); these cover everything testable without a browser.

const test = require('node:test');
const assert = require('node:assert/strict');
const { parseHTML } = require('linkedom');

const config = require('../extension/config.js');

function docWithConfig(cfg) {
  const json = JSON.stringify(cfg);
  const { document } = parseHTML(
    `<!doctype html><html><body><div class="annotate-target"></div>` +
      `<script type="application/json" id="annotate-config">${json}</script></body></html>`
  );
  return document;
}

// A fake fetch that records calls and returns a canned response.
function fakeFetch(response) {
  const calls = [];
  const f = async (url, opts) => {
    calls.push({ url, opts });
    return {
      status: response.status,
      ok: response.status >= 200 && response.status < 300,
      async json() {
        if (response.throwJson) throw new Error('no json');
        return response.json;
      },
    };
  };
  f.calls = calls;
  return f;
}

// ---------------------------------------------------------------------------
// readPageConfig / resolveContext
// ---------------------------------------------------------------------------

test('readPageConfig parses the injected #annotate-config blob', () => {
  const doc = docWithConfig({ session: 's1', artifact: 'plan', head: 'g1', token: 'tok' });
  const cfg = config.readPageConfig(doc);
  assert.equal(cfg.session, 's1');
  assert.equal(cfg.artifact, 'plan');
  assert.equal(cfg.head, 'g1');
  assert.equal(cfg.token, 'tok');
});

test('readPageConfig returns null when the blob is absent', () => {
  const { document } = parseHTML('<!doctype html><html><body></body></html>');
  assert.equal(config.readPageConfig(document), null);
});

test('readPageConfig returns null on malformed JSON or missing required ids', () => {
  const { document: bad } = parseHTML(
    '<!doctype html><html><body><script type="application/json" id="annotate-config">{not json}</script></body></html>'
  );
  assert.equal(config.readPageConfig(bad), null);
  const { document: partial } = parseHTML(
    '<!doctype html><html><body><script type="application/json" id="annotate-config">{"session":"s"}</script></body></html>'
  );
  assert.equal(config.readPageConfig(partial), null); // no artifact
});

test('resolveContext: served page -> origin from location, ids+token from config', () => {
  const doc = docWithConfig({ session: 's1', artifact: 'plan', head: 'g1', token: 'tok' });
  const ctx = config.resolveContext({ document: doc, location: { origin: 'http://127.0.0.1:7878', href: 'http://127.0.0.1:7878/s1/plan' } });
  assert.equal(ctx.mode, 'served');
  assert.equal(ctx.origin, 'http://127.0.0.1:7878');
  assert.equal(ctx.session, 's1');
  assert.equal(ctx.artifact, 'plan');
  assert.equal(ctx.head, 'g1');
  assert.equal(ctx.token, 'tok');
});

test('resolveContext: no config -> live mode with the default annotate origin', () => {
  const { document } = parseHTML('<!doctype html><html><body></body></html>');
  const ctx = config.resolveContext({ document, location: { origin: 'http://localhost:3000', href: 'http://localhost:3000/app' } });
  assert.equal(ctx.mode, 'live');
  assert.equal(ctx.origin, config.DEFAULT_ORIGIN);
  assert.equal(ctx.token, null);
  assert.equal(ctx.href, 'http://localhost:3000/app');
});

// ---------------------------------------------------------------------------
// per-view control gating: which chrome controls apply to which view kind.
// These pure predicates back the chrome-bar enable/disable state that the
// browser-bound buildChrome()/reflectShotToggle() consume (dogfood fixes B + C).
// ---------------------------------------------------------------------------

test('widthApplies: reading-width control is live on markdown AND code, inert elsewhere', () => {
  // FIX B: the width toggle now sets the code soft-wrap column too (not markdown-only).
  assert.equal(config.widthApplies('markdown'), true);
  assert.equal(config.widthApplies('code'), true);
  // full-bleed views — the control stays inert (greyed, no cycle).
  assert.equal(config.widthApplies('image'), false);
  assert.equal(config.widthApplies('struct'), false);
  assert.equal(config.widthApplies('csv'), false);
  assert.equal(config.widthApplies('frontend'), false);
  assert.ok(config.WIDTH_VIEWS instanceof Set);
  assert.deepEqual([...config.WIDTH_VIEWS].sort(), ['code', 'markdown']);
});

test('shouldCaptureScreenshot / VISUAL_VIEWS: the camera is disabled on source views (FIX C basis)', () => {
  // FIX C: the camera button is ENABLED (and may show the active-blue FILL) only on a visual
  // view; on a source view it is disabled and must NEVER capture nor read active — even with the
  // default-ON toggle. The button state keys off VISUAL_VIEWS membership.
  assert.equal(config.VISUAL_VIEWS.has('image'), true);
  assert.equal(config.VISUAL_VIEWS.has('markdown'), true);
  assert.equal(config.VISUAL_VIEWS.has('frontend'), true);
  assert.equal(config.VISUAL_VIEWS.has('code'), false);
  assert.equal(config.VISUAL_VIEWS.has('struct'), false);
  assert.equal(config.VISUAL_VIEWS.has('csv'), false);
  // default toggle ON, but a source view still never captures (the disabled camera does nothing).
  assert.equal(config.shouldCaptureScreenshot('code', true), false);
  assert.equal(config.shouldCaptureScreenshot('struct', true), false);
  assert.equal(config.shouldCaptureScreenshot('csv', true), false);
  // visual views capture when the toggle is on, and respect an explicit off.
  assert.equal(config.shouldCaptureScreenshot('markdown', true), true);
  assert.equal(config.shouldCaptureScreenshot('image', true), true);
  assert.equal(config.shouldCaptureScreenshot('markdown', false), false);
});

// ---------------------------------------------------------------------------
// makeFeedbackSink — the real /feedback POST (token in X-Annotate-Token)
// ---------------------------------------------------------------------------

test('makeFeedbackSink POSTs the §5.5 bundle to /feedback with the token header', async () => {
  const ctx = { origin: 'http://127.0.0.1:7878', session: 's1', artifact: 'plan' };
  const f = fakeFetch({ status: 200, json: { status: 'submitted', head: 'g1' } });
  const sink = config.makeFeedbackSink(ctx, f);
  const bundle = {
    body: { session: 's1', artifact: 'plan', head: 'g1', anchors: [], revertTarget: null, screenshot: null, nonce: 'n1' },
    headers: { 'Content-Type': 'application/json', 'X-Annotate-Token': 'tok' },
  };
  const resp = await sink(bundle);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url, 'http://127.0.0.1:7878/feedback');
  assert.equal(f.calls[0].opts.method, 'POST');
  assert.equal(f.calls[0].opts.headers['X-Annotate-Token'], 'tok');
  assert.deepEqual(JSON.parse(f.calls[0].opts.body), bundle.body);
  assert.equal(resp.httpStatus, 200);
  assert.equal(resp.status, 'submitted');
  assert.equal(resp.head, 'g1');
});

test('makeFeedbackSink surfaces a 409 stale-head response', async () => {
  const ctx = { origin: 'http://127.0.0.1:7878' };
  const f = fakeFetch({ status: 409, json: { error: 'stale-head', head: 'g2' } });
  const sink = config.makeFeedbackSink(ctx, f);
  const resp = await sink({ body: {}, headers: {} });
  assert.equal(resp.httpStatus, 409);
  assert.equal(resp.error, 'stale-head');
  assert.equal(resp.head, 'g2');
});

// ---------------------------------------------------------------------------
// postAccept — head-checked accept
// ---------------------------------------------------------------------------

test('postAccept POSTs the believed head + token to /<s>/<a>/accept', async () => {
  const ctx = { origin: 'http://127.0.0.1:7878', session: 's1', artifact: 'plan', token: 'tok' };
  const f = fakeFetch({ status: 200, json: { status: 'accepted', head: 'g1' } });
  const resp = await config.postAccept(ctx, 'g1', f);
  assert.equal(f.calls[0].url, 'http://127.0.0.1:7878/s1/plan/accept');
  assert.equal(f.calls[0].opts.headers['X-Annotate-Token'], 'tok');
  assert.deepEqual(JSON.parse(f.calls[0].opts.body), { head: 'g1' });
  assert.equal(resp.httpStatus, 200);
  assert.equal(resp.status, 'accepted');
});

test('postAccept surfaces a 409 on a stale head', async () => {
  const ctx = { origin: 'http://127.0.0.1:7878', session: 's1', artifact: 'plan', token: 'tok' };
  const f = fakeFetch({ status: 409, json: { error: 'stale-head', head: 'g9' } });
  const resp = await config.postAccept(ctx, 'g1', f);
  assert.equal(resp.httpStatus, 409);
  assert.equal(resp.error, 'stale-head');
});

// ---------------------------------------------------------------------------
// sendHeartbeat / fetchHead
// ---------------------------------------------------------------------------

test('sendHeartbeat POSTs /loaded and never throws on failure', async () => {
  const ctx = { origin: 'http://127.0.0.1:7878' };
  const f = fakeFetch({ status: 200, json: { ok: true } });
  await config.sendHeartbeat(ctx, f);
  assert.equal(f.calls[0].opts.method, 'POST');
  assert.match(f.calls[0].url, /\/loaded\?ext=annotate/);

  const boom = async () => {
    throw new Error('network down');
  };
  const r = await config.sendHeartbeat(ctx, boom); // must swallow
  assert.equal(r, null);
});

test('fetchHead returns the /head payload', async () => {
  const ctx = { origin: 'http://127.0.0.1:7878', session: 's1', artifact: 'plan' };
  const f = fakeFetch({ status: 200, json: { head: 'g1', status: 'pending', changeToken: 'g1:pending:0' } });
  const info = await config.fetchHead(ctx, f);
  assert.equal(f.calls[0].url, 'http://127.0.0.1:7878/s1/plan/head');
  assert.equal(info.head, 'g1');
  assert.equal(info.status, 'pending');
});
