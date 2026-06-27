'use strict';

// Setup-engine unit tests (tech-requirements §6.6, §10 q4; T7). HERMETIC: covers the pure,
// non-network logic of server/setup.js — runtime.json generation (ABSOLUTE paths, the T5
// contract), browser classification (the §7 --load-extension gate), extension-dir
// validation, target resolution, and the PROBE-COMPUTED degrade checklist. The real
// browser+server+heartbeat exit gate lives in tests/integration/setup-gate.js (run with the
// sandbox disabled, since loopback HTTP is blocked in the test sandbox). Run: npm test
//
// classifyBrowser is exercised against tiny FAKE --version scripts (not a real browser) so
// these stay fast, deterministic, and independent of the throwaway .spike CfT download.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const S = require('../server/setup.js');

const PKG_ROOT = path.join(__dirname, '..');
const PKG_EXTENSION = path.join(PKG_ROOT, 'extension');

let tmpRoot;
test.before(() => {
  tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-setup-'));
});
test.after(() => {
  if (tmpRoot) fs.rmSync(tmpRoot, { recursive: true, force: true });
});

// A fake browser binary whose `--version` prints `banner` (lets us classify without a
// real Chromium download).
function fakeBrowser(name, banner) {
  const p = path.join(tmpRoot, name);
  fs.writeFileSync(p, `#!/bin/sh\necho "${banner}"\n`);
  fs.chmodSync(p, 0o755);
  return p;
}

// ===========================================================================
// runtime.json (§6.6 / §8) — the generated setup<->launch-script<->server contract
// ===========================================================================

test('buildRuntime: generates ABSOLUTE paths + the T5 fields the launch script consumes', () => {
  const dataDir = path.join(tmpRoot, 'data');
  const rt = S.buildRuntime({
    port: 7878,
    dataDir,
    extensionDir: PKG_EXTENSION,
    profileDir: path.join(dataDir, 'chrome-profile'),
    browser: { path: '/abs/cft/chrome', kind: 'cft', buildId: '150.0.7871.24' },
  });
  assert.equal(rt.port, 7878);
  assert.equal(rt.paths.data, path.resolve(dataDir));
  assert.equal(rt.paths.extension, path.resolve(PKG_EXTENSION));
  assert.equal(rt.paths.profile, path.resolve(path.join(dataDir, 'chrome-profile')));
  assert.ok(path.isAbsolute(rt.paths.data) && path.isAbsolute(rt.paths.extension) && path.isAbsolute(rt.paths.profile), 'all paths absolute');
  assert.equal(rt.browser.path, '/abs/cft/chrome');
  assert.equal(rt.browser.kind, 'cft');
  assert.equal(rt.cftBuildId, '150.0.7871.24', 'cft buildId pinned for reproducibility (§6.6)');
});

test('buildRuntime: a non-cft browser pins no cftBuildId', () => {
  const rt = S.buildRuntime({
    port: 9000,
    dataDir: tmpRoot,
    extensionDir: PKG_EXTENSION,
    profileDir: path.join(tmpRoot, 'p'),
    browser: { path: '/usr/bin/chromium', kind: 'chromium', buildId: '120.0' },
  });
  assert.equal(rt.cftBuildId, '');
  assert.equal(rt.browser.kind, 'chromium');
});

test('writeRuntime round-trips through launch.js config (the launch script reads it VERBATIM)', () => {
  const dataDir = path.join(tmpRoot, 'rt-data');
  const runtimePath = path.join(dataDir, 'runtime.json');
  const rt = S.buildRuntime({
    port: 7878,
    dataDir,
    extensionDir: PKG_EXTENSION,
    profileDir: path.join(dataDir, 'chrome-profile'),
    browser: { path: '/abs/cft', kind: 'cft', buildId: '150.0.0.0' },
  });
  S.writeRuntime(runtimePath, rt);
  const onDisk = JSON.parse(fs.readFileSync(runtimePath, 'utf8'));
  assert.deepEqual(onDisk, rt);
  // the launch script reads these exact keys (launch.js cmdConfig)
  assert.ok(onDisk.port != null && onDisk.paths.data && onDisk.paths.extension && onDisk.paths.profile && 'path' in onDisk.browser && 'kind' in onDisk.browser);
});

// ===========================================================================
// Browser classification (§7) — does a binary honor --load-extension?
// ===========================================================================

test('classifyBrowser: Chrome for Testing -> cft, honors --load-extension', () => {
  const c = S.classifyBrowser(fakeBrowser('cft', 'Google Chrome for Testing 150.0.7871.24'));
  assert.equal(c.kind, 'cft');
  assert.equal(c.honors, true);
});

test('classifyBrowser: Chromium -> chromium, honors', () => {
  const c = S.classifyBrowser(fakeBrowser('chromium', 'Chromium 120.0.6099.109'));
  assert.equal(c.kind, 'chromium');
  assert.equal(c.honors, true);
});

test('classifyBrowser: branded Google Chrome -> branded, does NOT honor (§7 lockdown)', () => {
  const c = S.classifyBrowser(fakeBrowser('chrome', 'Google Chrome 149.0.7827.200'));
  assert.equal(c.kind, 'branded');
  assert.equal(c.honors, false);
});

test('classifyBrowser: a missing binary -> null', () => {
  assert.equal(S.classifyBrowser(path.join(tmpRoot, 'nope')), null);
});

// ===========================================================================
// Extension load dir validation (§6.4) — the source of the "manifest invalid" item
// ===========================================================================

test('validateExtensionDir: the shipped package extension is valid', () => {
  const v = S.validateExtensionDir(PKG_EXTENSION);
  assert.equal(v.ok, true, v.reason);
});

test('validateExtensionDir: invalid-JSON manifest is detected (names the cause)', () => {
  const d = path.join(tmpRoot, 'bad-json');
  fs.mkdirSync(d);
  fs.writeFileSync(path.join(d, 'manifest.json'), '{ not valid json');
  const v = S.validateExtensionDir(d);
  assert.equal(v.ok, false);
  assert.match(v.reason, /not valid JSON/);
});

test('validateExtensionDir: wrong manifest_version is detected', () => {
  const d = path.join(tmpRoot, 'bad-mv');
  fs.mkdirSync(d);
  fs.writeFileSync(path.join(d, 'manifest.json'), JSON.stringify({ manifest_version: 2, content_scripts: [{}] }));
  const v = S.validateExtensionDir(d);
  assert.equal(v.ok, false);
  assert.match(v.reason, /manifest_version/);
});

test('validateExtensionDir: a missing dir is detected', () => {
  const v = S.validateExtensionDir(path.join(tmpRoot, 'ghost'));
  assert.equal(v.ok, false);
  assert.match(v.reason, /not found/);
});

// ===========================================================================
// Target resolution — dataDir / runtimePath / port precedence
// ===========================================================================

test('resolveTargets: --data-dir -> runtime.json under it, default port + profile', () => {
  const dd = path.join(tmpRoot, 'targets-a');
  const t = S.resolveTargets({ dataDir: dd });
  assert.equal(t.dataDir, path.resolve(dd));
  assert.equal(t.runtimePath, path.join(path.resolve(dd), 'runtime.json'));
  assert.equal(t.port, 7878);
  assert.equal(t.profileDir, path.join(path.resolve(dd), 'chrome-profile'));
  assert.equal(t.extensionDir, path.resolve(PKG_EXTENSION));
});

test('resolveTargets: an existing runtime.json supplies dataDir + port', () => {
  const dd = path.join(tmpRoot, 'targets-b');
  fs.mkdirSync(dd, { recursive: true });
  const rp = path.join(dd, 'runtime.json');
  fs.writeFileSync(rp, JSON.stringify({ port: 9191, paths: { data: dd } }));
  const t = S.resolveTargets({ runtimePath: rp });
  assert.equal(t.dataDir, path.resolve(dd));
  assert.equal(t.port, 9191);
  assert.equal(t.runtimePath, rp);
});

test('resolveTargets: explicit opts override (port / extension / profile)', () => {
  const dd = path.join(tmpRoot, 'targets-c');
  const t = S.resolveTargets({ dataDir: dd, port: 8080, extensionDir: '/x/ext', profileDir: '/x/prof' });
  assert.equal(t.port, 8080);
  assert.equal(t.extensionDir, path.resolve('/x/ext'));
  assert.equal(t.profileDir, path.resolve('/x/prof'));
});

// ===========================================================================
// The degrade checklist — COMPUTED BY PROBING (§6.6, §10 q4), never hardcoded
// ===========================================================================

function base(extra) {
  return Object.assign(
    {
      browser: { path: fakeBrowser('cft-ok-' + Math.random().toString(36).slice(2), 'Google Chrome for Testing 150.0.0.0'), kind: 'cft', honors: true },
      profileDir: fs.mkdtempSync(path.join(tmpRoot, 'prof-')),
      extensionDir: PKG_EXTENSION,
      port: 7878,
      dataDir: tmpRoot,
      runtimePath: path.join(tmpRoot, 'runtime.json'),
      serverUp: true,
      serverForeign: false,
    },
    extra || {}
  );
}

test('checklist: a broken extension manifest is named as the cause (extension-invalid)', () => {
  const badExt = path.join(tmpRoot, 'broken-ext');
  fs.mkdirSync(badExt, { recursive: true });
  fs.writeFileSync(path.join(badExt, 'manifest.json'), '{ broken');
  const items = S.computeDegradeChecklist(base({ extensionDir: badExt }));
  const it = items.find((i) => i.id === 'extension-invalid');
  assert.ok(it, 'extension-invalid item present');
  assert.match(it.message, new RegExp(badExt.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'names the actual extension dir');
});

test('checklist: a missing browser is named (browser-missing)', () => {
  const items = S.computeDegradeChecklist(base({ browser: { path: null, error: 'no browser found and download disabled' } }));
  assert.ok(items.find((i) => i.id === 'browser-missing'));
});

test('checklist: a branded Chrome is named with the chrome://extensions step (branded-chrome)', () => {
  const branded = fakeBrowser('branded-x', 'Google Chrome 149.0.7827.200');
  const items = S.computeDegradeChecklist(base({ browser: { path: branded, kind: 'branded', honors: false } }));
  const it = items.find((i) => i.id === 'branded-chrome');
  assert.ok(it, 'branded-chrome item present');
  assert.match(it.message, /chrome:\/\/extensions/);
});

test('checklist: a foreign-held port is named (port-held)', () => {
  const items = S.computeDegradeChecklist(base({ serverForeign: true }));
  assert.ok(items.find((i) => i.id === 'port-held'));
});

test('checklist: all mechanical checks pass but no heartbeat -> the residual manual-load step', () => {
  const items = S.computeDegradeChecklist(base());
  assert.equal(items.length, 1, 'exactly the residual item');
  assert.equal(items[0].id, 'manual-load');
  assert.match(items[0].message, /Load Unpacked/);
});
