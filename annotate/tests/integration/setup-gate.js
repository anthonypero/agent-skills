'use strict';

// T7 setup / load-probe exit gate (tech-requirements §6.6, §10 q4). Drives the REAL setup
// engine (server/setup.js) against the provisioned Chrome for Testing (reused from .spike to
// avoid a ~358MB re-download) and proves probe-and-degrade end-to-end:
//
//   GATE 1 (clean dir, one command brings the stack up):
//     runSetup on a FRESH temp ~/.annotate-style dir ->
//       - runtime.json generated with ABSOLUTE paths (the launch-script contract)
//       - the lazy-singleton server is UP on the configured port
//       - the dedicated --user-data-dir profile is created
//       - the extension is CONFIRMED LOADED via its /loaded heartbeat
//       => verdict: pass
//
//   GATE 2 (the probe is REAL, not hardcoded):
//     runSetup with a deliberately-INERT but structurally-valid extension (loads, never
//     heartbeats) -> the heartbeat times out -> verdict: degrade, with a PROBED checklist
//     whose residual item is `manual-load` (browser launched, all mechanical checks passed,
//     but no heartbeat). Proves the load probe gates pass-vs-degrade.
//
//   GATE 3 (the checklist names the real mechanical cause):
//     a broken-manifest extension dir -> degrade with `extension-invalid` naming the dir
//     (computed by probing the manifest, never a generic message).
//
// The Bash sandbox blocks loopback HTTP (false ECONNREFUSED) — RUN WITH THE SANDBOX DISABLED:
//   node tests/integration/setup-gate.js
// Optional: ANNOTATE_CFT=<CfT binary> (else auto-found under .spike/cache); ANNOTATE_HEADLESS=1.

const fs = require('node:fs');
const os = require('node:os');
const net = require('node:net');
const path = require('node:path');

const { PKG_ROOT, findCft } = require('./cdp-harness.js');
const S = require(path.join(PKG_ROOT, 'server', 'setup.js'));

const PKG_EXTENSION = path.join(PKG_ROOT, 'extension');

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

function killPid(pid) {
  if (!pid) return;
  try { process.kill(pid, 'SIGTERM'); } catch {}
}

// A structurally-VALID MV3 extension whose content script does NOTHING (no heartbeat).
// validateExtensionDir passes, so setup LAUNCHES the browser — and the heartbeat never
// comes, exercising the real heartbeat-timeout degrade path.
function makeInertExtension(dir) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'manifest.json'),
    JSON.stringify(
      {
        manifest_version: 3,
        name: 'annotate-inert-probe',
        version: '0.0.1',
        content_scripts: [{ matches: ['http://127.0.0.1/*'], js: ['noop.js'], run_at: 'document_idle' }],
      },
      null,
      2
    )
  );
  fs.writeFileSync(path.join(dir, 'noop.js'), '// intentionally inert: never POSTs /loaded\n');
}

// A broken-manifest extension dir (invalid JSON) — the mechanical-cause case.
function makeBrokenExtension(dir) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'manifest.json'), '{ this is not valid json');
}

async function main() {
  const checks = [];
  const ok = (name, cond, detail) => {
    checks.push({ name, pass: !!cond });
    console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
  };

  const cft = findCft();
  console.log(`cft ${cft}\n`);

  const cleanups = [];

  try {
    // ===================== GATE 1 — clean dir -> full stack up =====================
    {
      const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-setup-good-'));
      const port = await freePort();
      const res = await S.runSetup({ dataDir, port, browserPath: cft, extensionDir: PKG_EXTENSION, probeTimeoutMs: 40000 });
      cleanups.push(() => killPid(res.server && res.server.pid));
      cleanups.push(() => fs.rmSync(dataDir, { recursive: true, force: true }));

      ok('verdict: pass (extension heartbeat confirmed on a clean dir)', res.verdict === 'pass', `probe.loaded=${res.probe.loaded}, count ${res.probe.baseline}->${res.probe.finalCount}`);
      ok('server is UP on the configured port', res.server.up === true, `reused=${res.server.reused} pid=${res.server.pid}`);
      ok('dedicated --user-data-dir profile created', fs.existsSync(res.profileDir), res.profileDir);

      const rtOnDisk = JSON.parse(fs.readFileSync(res.runtimePath, 'utf8'));
      const abs = path.isAbsolute(rtOnDisk.paths.data) && path.isAbsolute(rtOnDisk.paths.extension) && path.isAbsolute(rtOnDisk.paths.profile);
      ok('runtime.json generated with ABSOLUTE paths the launch script consumes', abs && rtOnDisk.port === port && rtOnDisk.browser.kind === 'cft', JSON.stringify({ port: rtOnDisk.port, kind: rtOnDisk.browser.kind, data: rtOnDisk.paths.data }));

      // independent HTTP confirmation the server answers /loaded with a recorded heartbeat
      let live = null;
      try {
        live = await (await fetch(`http://127.0.0.1:${port}/loaded`)).json();
      } catch (e) { /* sandbox? */ }
      ok('GET /loaded confirms the recorded heartbeat (independent check)', live && live.count > 0, JSON.stringify(live));
    }

    // ============ GATE 2 — inert extension loads but never heartbeats -> degrade ============
    {
      const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-setup-inert-'));
      const port = await freePort();
      const inertExt = path.join(dataDir, 'inert-ext');
      makeInertExtension(inertExt);
      const res = await S.runSetup({ dataDir, port, browserPath: cft, extensionDir: inertExt, probeTimeoutMs: 9000 });
      cleanups.push(() => killPid(res.server && res.server.pid));
      cleanups.push(() => fs.rmSync(dataDir, { recursive: true, force: true }));

      ok('verdict: degrade (a valid-but-inert extension never heartbeats — the probe is REAL)', res.verdict === 'degrade', `probe.attempted=${res.probe.attempted}, loaded=${res.probe.loaded}`);
      ok('the probe actually LAUNCHED the browser (heartbeat-gated, not a pre-check skip)', res.probe.attempted === true && res.probe.childLaunched === true);
      const residual = res.checklist.find((i) => i.id === 'manual-load');
      ok('degrade checklist residual is `manual-load` (browser launched, no heartbeat)', !!residual, residual && residual.message.slice(0, 80));
    }

    // ============ GATE 3 — broken manifest -> degrade names the mechanical cause ============
    {
      const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'annotate-setup-broken-'));
      const port = await freePort();
      const brokenExt = path.join(dataDir, 'broken-ext');
      makeBrokenExtension(brokenExt);
      const res = await S.runSetup({ dataDir, port, browserPath: cft, extensionDir: brokenExt, probeTimeoutMs: 6000 });
      cleanups.push(() => killPid(res.server && res.server.pid));
      cleanups.push(() => fs.rmSync(dataDir, { recursive: true, force: true }));

      ok('verdict: degrade (broken manifest)', res.verdict === 'degrade');
      const it = res.checklist.find((i) => i.id === 'extension-invalid');
      ok('checklist names `extension-invalid` and the actual dir (computed by probing the manifest)', !!it && it.message.indexOf(brokenExt) >= 0, it && it.message.slice(0, 90));
    }

    const failed = checks.filter((c) => !c.pass);
    console.log(`\n${failed.length ? 'GATE FAILED' : 'GATE PASSED'} — ${checks.length - failed.length}/${checks.length} checks passed`);
    return failed.length === 0 ? 0 : 1;
  } finally {
    for (const c of cleanups) {
      try { c(); } catch {}
    }
  }
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('GATE ERROR:', err && err.stack ? err.stack : err);
    process.exit(2);
  });
