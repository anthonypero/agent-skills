'use strict';

// Setup / first-run provisioning — agent-driven, probe-and-degrade (tech-requirements
// §6.6, §10 q4, §8). The Node engine behind `install.sh` (one-command bootstrap) and
// `annotate setup` (repair). `install.sh` runs `npm install` first, then delegates the
// rest here; `annotate setup` (bin/annotate) dispatches straight here.
//
// What it brings up, attempting everything automatically (§6.6):
//   1. the data-dir tree (~/.annotate), mode 0700 (§6.3)
//   2. locate/validate the unpacked extension load dir (the package's extension/, §6.4)
//   3. PROVISION a `--load-extension`-honoring browser (§7): explicit -> an already-
//      downloaded Chrome for Testing in a cache -> a suitable system Chromium -> else
//      DOWNLOAD Chrome for Testing via @puppeteer/browsers. Branded Google Chrome is
//      NEVER auto-accepted (it dropped CLI extension-loading in Chrome 137/142, §7) —
//      it only surfaces in the degraded manual checklist.
//   4. generate the runtime config ~/.annotate/runtime.json — ABSOLUTE paths, the T5
//      contract the launch script + server read VERBATIM (§6.6, §8)
//   5. the dedicated --user-data-dir profile (§6.4)
//   6. start the lazy-singleton server (§6.1 step 6) if not already up
//   7. the LOAD PROBE (§6.6, proven in S0): launch the browser with --load-extension at a
//      throwaway probe page; the content script POSTs /loaded; setup polls GET /loaded for
//      a NEW heartbeat (delta over a baseline) within a timeout. The heartbeat GATES
//      pass-vs-degrade.
//
// PROBE-AND-DEGRADE (§6.6, §10 q4): on a heartbeat -> PASS. On timeout -> DEGRADE to a
// near-mechanical checklist COMPUTED BY PROBING the actual failure (browser missing /
// unrunnable / branded; profile unwritable; extension manifest invalid; server down;
// else the residual "load it manually via chrome://extensions") — never a hardcoded
// generic message. S0 residuals honored: the dev-mode Preferences seed is DROPPED (it
// does not persist and is not needed — --load-extension loads+enables on CfT regardless),
// and the heartbeat (not host-side chrome://extensions screenshotting, which is TCC-
// blocked) is the load-probe of record.
//
// SEAMS (for the runnable gate + offline/CI):
//   ANNOTATE_CFT            explicit browser binary (highest priority)
//   ANNOTATE_BROWSER_CACHE  extra cache dir(s) (path-list) to scan for an installed CfT
//                           BEFORE downloading — the gate points this at .spike/cache so
//                           tests reuse the S0 download instead of re-fetching ~358MB
//   ANNOTATE_HEADLESS=1     launch the probe browser headless=new (default headful, S0)
//   ANNOTATE_PROBE_TIMEOUT  probe heartbeat timeout, seconds (default 30)
//   ANNOTATE_NO_DOWNLOAD=1  forbid the CfT download (offline test of the degrade path)

const fs = require('node:fs');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');

const P = require('./protocol.js');
const { create } = require('./create.js');

const PKG_ROOT = path.join(__dirname, '..');
const PKG_EXTENSION = path.join(PKG_ROOT, 'extension');
const SERVER_JS = path.join(__dirname, 'server.js');
const BIN_ANNOTATE = path.join(PKG_ROOT, 'bin', 'annotate');
const DEFAULT_PORT = 7878;
const PROBE_SESSION = '__setup_probe__';
const CONSENT_FILE = 'config.json';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// Consent / preference config (v2 punch-list #10 — first-run browser-install gate).
// `~/.annotate/config.json` is the user's DURABLE install decision, DISTINCT from the
// machine-generated `runtime.json` (which may be regenerated). It is the source of truth
// for whether the ~358MB Chrome-for-Testing download is allowed. Recognized shapes:
//   {}                                            absent/undecided -> gate fires, NO download
//   { "browser":"cft", "consented":true }         CfT download approved -> proceed
//   { "browser":"system", "path":"<chrome>" }     use the user's own browser, NO download
//   { "declined":true }                           opted out -> skill stays dormant, no re-prompt
// The PRIMARY gate is the SKILL.md behavioral contract (the LLM stops + asks first); this
// file + the --download-cft / ANNOTATE_CONFIRM_DOWNLOAD guard is the belt-and-suspenders so
// a bare install.sh can never silently pull a browser.
// ---------------------------------------------------------------------------

function consentPath(dataDir) {
  return path.join(dataDir, CONSENT_FILE);
}

function readConsent(dataDir) {
  const p = consentPath(dataDir);
  if (!P.exists(p)) return {};
  try {
    const c = P.readJSON(p);
    return c && typeof c === 'object' ? c : {};
  } catch {
    return {}; // a malformed config.json is treated as undecided -> gate fires
  }
}

// ---------------------------------------------------------------------------
// Browser classification — the §7 gate: does a binary honor --load-extension?
// Classify by its --version banner: "… for Testing …" -> cft (honors);
// "Chromium …" -> chromium (honors); "Google Chrome …" (NOT "for Testing") ->
// branded (does NOT honor; only the manual fallback). Anything that won't even
// answer --version is unrunnable.
// ---------------------------------------------------------------------------

function classifyBrowser(binPath) {
  if (!binPath || !P.exists(binPath)) return null;
  let version;
  try {
    version = execFileSync(binPath, ['--version'], { timeout: 8000, encoding: 'utf8' }).trim();
  } catch {
    return null; // present but unrunnable (corrupt / wrong arch / no exec bit)
  }
  let kind;
  if (/for testing/i.test(version)) kind = 'cft';
  else if (/chromium/i.test(version)) kind = 'chromium';
  else if (/google chrome/i.test(version)) kind = 'branded';
  else if (/(microsoft edge|brave)/i.test(version)) kind = 'chromium'; // Chromium-family, honor --load-extension
  else kind = 'unknown';
  return { kind, version, honors: kind === 'cft' || kind === 'chromium' };
}

// ---------------------------------------------------------------------------
// Extension load dir validation (§6.4) — also the source of the "extension
// manifest invalid" degrade item, COMPUTED BY PROBING the real manifest.
// ---------------------------------------------------------------------------

function validateExtensionDir(extDir) {
  if (!extDir || !P.exists(extDir)) return { ok: false, reason: 'directory not found' };
  const mf = path.join(extDir, 'manifest.json');
  if (!P.exists(mf)) return { ok: false, reason: 'manifest.json missing' };
  let m;
  try {
    m = JSON.parse(fs.readFileSync(mf, 'utf8'));
  } catch {
    return { ok: false, reason: 'manifest.json is not valid JSON' };
  }
  if (m.manifest_version !== 3) {
    return { ok: false, reason: `manifest_version is ${JSON.stringify(m.manifest_version)} (need 3)` };
  }
  if (!Array.isArray(m.content_scripts) || m.content_scripts.length === 0) {
    return { ok: false, reason: 'no content_scripts (the load probe needs one to heartbeat)' };
  }
  return { ok: true };
}

// ---------------------------------------------------------------------------
// Profile writability probe (§6.6) — the source of the "profile unwritable" item.
// ---------------------------------------------------------------------------

function profileWritable(profileDir) {
  try {
    P.ensureDir(profileDir, 0o700);
    const probe = path.join(profileDir, `.write-probe-${P.randSuffix(6)}`);
    fs.writeFileSync(probe, 'ok');
    fs.unlinkSync(probe);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Browser provisioning (§6.6 order): explicit -> cache(s) -> system Chromium ->
// download CfT. Branded Google Chrome is skipped here (reachable only via the
// degraded manual path). Returns { path, kind, buildId, source, honors, error }.
// ---------------------------------------------------------------------------

function pathListEnv(name) {
  const v = process.env[name];
  if (!v) return [];
  return v.split(path.delimiter).map((s) => s.trim()).filter(Boolean);
}

// An already-downloaded CfT under a @puppeteer/browsers cache dir (the .spike/cache
// reuse path the gate leans on). Lazy-requires @puppeteer/browsers.
async function findInstalledCft(cacheDir) {
  if (!cacheDir || !P.exists(cacheDir)) return null;
  let browsers;
  try {
    browsers = require('@puppeteer/browsers');
  } catch {
    return null;
  }
  try {
    const installed = await browsers.getInstalledBrowsers({ cacheDir });
    for (const b of installed) {
      if (b.browser !== browsers.Browser.CHROME) continue;
      const cls = classifyBrowser(b.executablePath);
      if (cls && cls.kind === 'cft') return { path: b.executablePath, buildId: b.buildId };
    }
  } catch {
    /* unreadable cache -> not found */
  }
  return null;
}

function findSystemChromium() {
  // Absolute, well-known Chromium locations + PATH names. Branded Chrome paths are
  // deliberately absent — a branded binary found via PATH is classified and skipped.
  const candidates = [
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/snap/bin/chromium',
    '/usr/lib/chromium/chromium',
    '/usr/lib/chromium-browser/chromium-browser',
  ];
  for (const name of ['chromium', 'chromium-browser']) {
    for (const dir of pathListEnv('PATH')) {
      candidates.push(path.join(dir, name));
    }
  }
  for (const c of candidates) {
    if (!P.exists(c)) continue;
    const cls = classifyBrowser(c);
    if (cls && cls.honors) return { path: c, kind: cls.kind, version: cls.version };
  }
  return null;
}

async function downloadCft(cacheDir) {
  let browsers;
  try {
    browsers = require('@puppeteer/browsers');
  } catch (e) {
    return { path: null, kind: null, buildId: null, source: 'download', honors: false, error: `@puppeteer/browsers unavailable (run npm install): ${e.message}` };
  }
  try {
    const platform = browsers.detectBrowserPlatform();
    // No linux-arm64 CfT build is published yet (§6.6) — install() will reject there;
    // the structured error degrades to the system-Chromium / branded checklist.
    const buildId = await browsers.resolveBuildId(browsers.Browser.CHROME, platform, 'stable');
    P.ensureDir(cacheDir, 0o755);
    const installed = await browsers.install({ browser: browsers.Browser.CHROME, buildId, cacheDir, platform });
    return { path: installed.executablePath, kind: 'cft', buildId, source: 'download', honors: true };
  } catch (e) {
    return { path: null, kind: null, buildId: null, source: 'download', honors: false, error: `CfT download failed (offline / proxied / no build for this platform): ${e.message}` };
  }
}

async function provisionBrowser(opts) {
  const dataDir = opts.dataDir;
  const explicit = opts.browserPath || process.env.ANNOTATE_CFT || null;
  if (explicit) {
    const cls = classifyBrowser(explicit);
    if (cls && cls.honors) return { path: explicit, kind: cls.kind, buildId: cls.version, source: 'explicit', honors: true };
    return { path: explicit, kind: cls ? cls.kind : 'unknown', buildId: cls ? cls.version : null, source: 'explicit', honors: false };
  }

  const caches = [path.join(dataDir, 'cache'), ...(opts.browserCacheDirs || []), ...pathListEnv('ANNOTATE_BROWSER_CACHE')];
  for (const c of caches) {
    const found = await findInstalledCft(c);
    if (found) return { path: found.path, kind: 'cft', buildId: found.buildId, source: `cache:${c}`, honors: true };
  }

  const sys = findSystemChromium();
  if (sys) return { path: sys.path, kind: sys.kind, buildId: sys.version, source: 'system', honors: true };

  if (opts.noDownload || process.env.ANNOTATE_NO_DOWNLOAD) {
    return { path: null, kind: null, buildId: null, source: 'none', honors: false, error: 'no browser found and download disabled' };
  }
  // v2 punch-list #10 — the CfT DOWNLOAD is the ONLY gated step (reusing an explicit /
  // cached / system browser above happened without a gate, because no download occurs).
  // Require explicit confirmation (config consent OR the --download-cft / ANNOTATE_CONFIRM_
  // DOWNLOAD seam, threaded in as opts.confirmDownload) so a bare install.sh cannot silently
  // pull ~358MB.
  if (!opts.confirmDownload) {
    return {
      path: null,
      kind: null,
      buildId: null,
      source: 'download',
      honors: false,
      needsConsent: true,
      error: 'Chrome for Testing (~358MB) download not yet authorized',
    };
  }
  return downloadCft(path.join(dataDir, 'cache'));
}

// ---------------------------------------------------------------------------
// runtime.json (§6.6, §8) — the generated setup<->launch-script<->server contract.
// ABSOLUTE paths only (the launch script reads paths.data / paths.extension /
// paths.profile VERBATIM, no ~ expansion — launch.js cmdConfig).
// ---------------------------------------------------------------------------

function writeRuntime(runtimePath, runtime) {
  P.ensureDir(path.dirname(runtimePath), 0o700);
  P.atomicWriteJSON(runtimePath, runtime);
  return runtime;
}

function buildRuntime({ port, dataDir, extensionDir, profileDir, browser }) {
  return {
    port,
    paths: {
      data: path.resolve(dataDir),
      extension: path.resolve(extensionDir),
      profile: path.resolve(profileDir),
    },
    browser: { path: browser.path || '', kind: browser.kind || '' },
    cftBuildId: browser.kind === 'cft' && browser.buildId ? browser.buildId : '',
  };
}

// ---------------------------------------------------------------------------
// Server lifecycle (§6.1 step 6 / §6.6) — reuse a running annotate server, else
// spawn `node server/server.js <runtime.json>` detached so it persists past setup.
// ---------------------------------------------------------------------------

async function loadedInfo(port) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/loaded`, { cache: 'no-store' });
    if (!res.ok) return { up: true, ours: false, count: null };
    const j = await res.json();
    return { up: true, ours: typeof j.count !== 'undefined', count: j.count };
  } catch {
    return { up: false, ours: false, count: null };
  }
}

async function ensureServer({ runtimePath, port, dataDir }) {
  const probe = await loadedInfo(port);
  if (probe.up && probe.ours) return { reused: true, up: true, pid: null, port };
  if (probe.up && !probe.ours) {
    return { reused: false, up: false, pid: null, port, foreign: true };
  }

  P.ensureDir(dataDir, 0o700);
  const logPath = path.join(dataDir, 'server.log');
  const out = fs.openSync(logPath, 'a');
  const child = spawn(process.execPath, [SERVER_JS, runtimePath], {
    detached: true,
    stdio: ['ignore', out, out],
  });
  child.unref();
  try {
    fs.writeFileSync(path.join(dataDir, 'server.pid'), `${child.pid}\n`);
  } catch {
    /* best-effort pidfile */
  }

  // readiness budget ~12s
  for (let i = 0; i < 60; i++) {
    const info = await loadedInfo(port);
    if (info.up && info.ours) return { reused: false, up: true, pid: child.pid, port };
    await sleep(200);
  }
  return { reused: false, up: false, pid: child.pid, port, logPath };
}

// ---------------------------------------------------------------------------
// The LOAD PROBE (§6.6 / S0) — launch the browser with --load-extension at a
// throwaway probe page and wait for a NEW /loaded heartbeat (delta over baseline).
// ---------------------------------------------------------------------------

function launchProbeBrowser({ browserPath, profileDir, extensionDir, url }) {
  const args = [
    `--user-data-dir=${profileDir}`,
    `--load-extension=${extensionDir}`,
    `--disable-extensions-except=${extensionDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    // v2 punch-list #8b — suppress the CfT "only for automated testing" infobar during the
    // throwaway load probe too (belt-and-suspenders). NOT --kiosk: the probe is a quick,
    // killed-after-heartbeat launch, not the user-facing review window, so fullscreen would
    // be inappropriate (and conflicts with --headless=new below).
    '--test-type',
    '--no-service-autorun',
    '--disable-background-networking',
  ];
  if (process.env.ANNOTATE_HEADLESS) args.push('--headless=new');
  args.push(url);
  return spawn(browserPath, args, { stdio: 'ignore' });
}

async function runLoadProbe({ dataDir, port, browserPath, profileDir, extensionDir, timeoutMs }) {
  // A throwaway probe round so the content script (matches http://127.0.0.1/*) injects on
  // a real served page and fires its /loaded heartbeat. Cleaned up afterward.
  const probeSrc = path.join(dataDir, '.setup-probe.md');
  fs.writeFileSync(probeSrc, '# annotate setup probe\n\nLoad-probe page. Safe to ignore.\n');
  let round;
  try {
    round = create({ dataDir, source: probeSrc, session: PROBE_SESSION, artifact: 'probe' });
  } catch (e) {
    try { fs.unlinkSync(probeSrc); } catch {}
    return { loaded: false, error: `could not create probe round: ${e.message}`, childLaunched: false };
  }

  const baseline = (await loadedInfo(port)).count;
  const base = typeof baseline === 'number' ? baseline : 0;
  const url = `http://127.0.0.1:${port}/${PROBE_SESSION}/${round.artifact}`;

  let child = null;
  try {
    child = launchProbeBrowser({ browserPath, profileDir, extensionDir, url });
  } catch (e) {
    cleanupProbe(dataDir, probeSrc);
    return { loaded: false, error: `browser failed to launch: ${e.message}`, childLaunched: false };
  }

  let childExited = false;
  child.on('exit', () => { childExited = true; });

  let loaded = false;
  let finalCount = base;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const info = await loadedInfo(port);
    if (typeof info.count === 'number') finalCount = info.count;
    if (typeof info.count === 'number' && info.count > base) {
      loaded = true;
      break;
    }
    // The browser died before heartbeating -> no heartbeat is coming; stop waiting out
    // the full timeout (one final poll above already had its chance to see a late ping).
    if (childExited) break;
    await sleep(500);
  }

  try { child.kill('SIGTERM'); } catch {}
  await sleep(300);
  try { if (child.exitCode == null) child.kill('SIGKILL'); } catch {}
  cleanupProbe(dataDir, probeSrc);

  return { loaded, baseline: base, finalCount, childLaunched: true, url };
}

function cleanupProbe(dataDir, probeSrc) {
  try { fs.unlinkSync(probeSrc); } catch {}
  try { fs.rmSync(P.sessionDir(dataDir, PROBE_SESSION), { recursive: true, force: true }); } catch {}
}

// ---------------------------------------------------------------------------
// The degrade checklist — COMPUTED BY PROBING the real failure (§6.6, §10 q4),
// never hardcoded. Each item is a near-mechanical step keyed to a probed cause.
// ---------------------------------------------------------------------------

function computeDegradeChecklist(ctx) {
  const items = [];

  // (1) browser — present? runnable? honors --load-extension?
  if (!ctx.browser || !ctx.browser.path || !P.exists(ctx.browser.path)) {
    const why = ctx.browser && ctx.browser.error ? ` (${ctx.browser.error})` : '';
    items.push({
      id: 'browser-missing',
      message:
        `No usable --load-extension browser was provisioned${why}. ` +
        `Install Chrome for Testing (setup auto-downloads it when online) or a system Chromium, then re-run \`annotate setup\`.`,
    });
  } else {
    const cls = classifyBrowser(ctx.browser.path);
    if (!cls) {
      items.push({
        id: 'browser-unrunnable',
        message: `Browser at ${ctx.browser.path} did not answer --version (corrupt or wrong architecture). Re-provision with \`annotate setup\`.`,
      });
    } else if (cls.kind === 'branded') {
      items.push({
        id: 'branded-chrome',
        message:
          `Found branded Google Chrome (${cls.version}), which can no longer CLI-load extensions (removed in Chrome 137/142). ` +
          `Either install Chrome for Testing, or open chrome://extensions in the annotate profile, enable Developer Mode, and Load Unpacked -> ${ctx.extensionDir}.`,
      });
    }
  }

  // (2) profile writable?
  if (!profileWritable(ctx.profileDir)) {
    items.push({
      id: 'profile-unwritable',
      message: `The dedicated profile dir is not writable: ${ctx.profileDir}. Fix its permissions (chmod u+rwx) or choose another --user-data-dir.`,
    });
  }

  // (3) extension load dir valid? (this is what names a broken manifest as the cause)
  const ev = validateExtensionDir(ctx.extensionDir);
  if (!ev.ok) {
    items.push({
      id: 'extension-invalid',
      message: `The extension load dir is invalid (${ev.reason}): ${ctx.extensionDir}. Restore a valid MV3 extension/manifest.json and re-run \`annotate setup\`.`,
    });
  }

  // (4) server reachable?
  if (ctx.serverUp === false) {
    items.push({
      id: 'server-down',
      message: `The annotate server is not answering on 127.0.0.1:${ctx.port}. See ${path.join(ctx.dataDir, 'server.log')} and re-run \`annotate setup\`.`,
    });
  }
  if (ctx.serverForeign) {
    items.push({
      id: 'port-held',
      message: `Port ${ctx.port} is held by a non-annotate process. Stop it or set a different "port" in ${ctx.runtimePath}, then re-run \`annotate setup\`.`,
    });
  }

  // (5) residual: every mechanical check passed but no heartbeat -> manual load is the
  //     one remaining human step (the per-environment residual §10 q4 expects).
  if (items.length === 0) {
    items.push({
      id: 'manual-load',
      message:
        `The browser launched and every mechanical check passed, but the extension never sent its load heartbeat. ` +
        `Open chrome://extensions in the annotate profile (--user-data-dir=${ctx.profileDir}), enable Developer Mode, ` +
        `Load Unpacked -> ${ctx.extensionDir}, then re-run \`annotate setup\` to re-probe.`,
    });
  }

  return items;
}

// ---------------------------------------------------------------------------
// Orchestrator
// ---------------------------------------------------------------------------

function resolveTargets(opts) {
  // dataDir / runtimePath precedence (see header). opts win for programmatic callers
  // (the gate); flags/env for the CLI.
  const dataArg = opts.dataDir || process.env.ANNOTATE_DATA_DIR || null;
  const runtimeArg = opts.runtimePath || process.env.ANNOTATE_RUNTIME || null;

  let dataDir;
  let runtimePath;
  if (dataArg) {
    dataDir = path.resolve(dataArg);
    runtimePath = runtimeArg ? path.resolve(runtimeArg) : path.join(dataDir, 'runtime.json');
  } else if (runtimeArg) {
    runtimePath = path.resolve(runtimeArg);
    let fromCfg = null;
    if (P.exists(runtimePath)) {
      try {
        const c = P.readJSON(runtimePath);
        if (c.paths && c.paths.data) fromCfg = c.paths.data;
      } catch {
        /* ignore */
      }
    }
    dataDir = fromCfg ? path.resolve(fromCfg) : path.dirname(runtimePath);
  } else {
    dataDir = P.defaultDataDir();
    runtimePath = path.join(dataDir, 'runtime.json');
  }

  let port = opts.port != null ? Number(opts.port) : null;
  if (port == null && P.exists(runtimePath)) {
    try {
      const c = P.readJSON(runtimePath);
      if (c.port != null) port = Number(c.port);
    } catch {
      /* ignore */
    }
  }
  if (port == null || !Number.isFinite(port)) port = DEFAULT_PORT;

  const extensionDir = path.resolve(opts.extensionDir || PKG_EXTENSION);
  const profileDir = path.resolve(opts.profileDir || path.join(dataDir, 'chrome-profile'));
  return { dataDir, runtimePath, port, extensionDir, profileDir };
}

async function runSetup(opts = {}) {
  const log = opts.log || ((m) => process.stderr.write(`${m}\n`));
  const { dataDir, runtimePath, port, extensionDir, profileDir } = resolveTargets(opts);

  log(`annotate setup: data dir ${dataDir} (port ${port})`);

  // (1) data tree, 0700 (§6.3)
  P.ensureDir(dataDir, 0o700);

  // (1b) consent / preference gate (v2 punch-list #10). config.json is the user's durable
  // install decision, DISTINCT from the generated runtime.json.
  const consent = readConsent(dataDir);
  if (consent.declined === true) {
    // Opted out -> stay dormant; do NOT provision/download/probe and do NOT re-prompt.
    log('annotate setup: DORMANT — consent declined in ~/.annotate/config.json; not provisioning a browser.');
    log('annotate setup: delete that file (or replace it with a browser choice) to enable annotate.');
    return { verdict: 'declined', dataDir, runtimePath, port, extensionDir, profileDir, browser: null, consent, checklist: [] };
  }
  // Choice (c): point at the user's own already-installed browser -> use it, NO download.
  let browserPath = opts.browserPath;
  if (!browserPath && consent.browser === 'system' && consent.path) {
    browserPath = consent.path;
    log(`annotate setup: using your own browser from config.json -> ${browserPath}`);
  }
  // The CfT download is allowed only by an explicit confirm seam OR a consented config.
  const confirmDownload =
    opts.confirmDownload === true ||
    !!process.env.ANNOTATE_CONFIRM_DOWNLOAD ||
    (consent.browser === 'cft' && consent.consented === true);

  // (2) extension load dir (§6.4) — validate now; a bad one still proceeds to probe so
  // the degrade checklist can name it (and so a good browser is still provisioned).
  const extValid = validateExtensionDir(extensionDir);
  if (extValid.ok) log(`annotate setup: extension load dir ${extensionDir}`);
  else log(`annotate setup: WARNING extension load dir invalid (${extValid.reason}): ${extensionDir}`);

  // (3) provision a --load-extension-honoring browser (§7), with the #10 download gate
  const browser = await provisionBrowser({
    dataDir,
    browserPath,
    browserCacheDirs: opts.browserCacheDirs,
    noDownload: opts.noDownload,
    confirmDownload,
  });
  if (browser.path && browser.honors) log(`annotate setup: browser ${browser.kind} ${browser.buildId || ''} (${browser.source}) -> ${browser.path}`);
  else log(`annotate setup: WARNING no auto-usable browser${browser.error ? ` — ${browser.error}` : browser.kind === 'branded' ? ' — branded Chrome cannot CLI-load extensions' : ''}`);

  // (3b) #10 download gate fired: no reusable browser exists and the download is unconfirmed.
  // Surface the one-line "how to confirm" and STOP — do NOT write runtime.json, start the
  // server, or download. (A bare install.sh thus cannot silently pull a browser.)
  if (browser.needsConsent) {
    log('annotate setup: CONSENT REQUIRED — no system Chromium or cached Chrome for Testing was found,');
    log('annotate setup: so enabling annotate needs a one-time ~358MB Chrome-for-Testing download under ~/.annotate/.');
    log('annotate setup: re-run with --download-cft (or ANNOTATE_CONFIRM_DOWNLOAD=1) to confirm the download, OR');
    log('annotate setup: point at an existing browser via ~/.annotate/config.json {"browser":"system","path":"<chrome/chromium>"}.');
    return { verdict: 'needs-consent', dataDir, runtimePath, port, extensionDir, profileDir, browser, consent, checklist: [] };
  }

  // (4) profile dir, 0700 (§6.4)
  P.ensureDir(profileDir, 0o700);

  // (5) generate runtime.json — ABSOLUTE paths (§6.6 / §8)
  const runtime = buildRuntime({ port, dataDir, extensionDir, profileDir, browser });
  writeRuntime(runtimePath, runtime);
  log(`annotate setup: wrote ${runtimePath}`);

  // make the launch script executable (§6.6), best-effort.
  try { fs.chmodSync(BIN_ANNOTATE, 0o755); } catch {}

  // (6) server (lazy singleton, §6.1)
  const server = await ensureServer({ runtimePath, port, dataDir });
  if (server.up) log(`annotate setup: server ${server.reused ? 'reused' : 'started'} on 127.0.0.1:${port}`);
  else if (server.foreign) log(`annotate setup: ERROR port ${port} held by a non-annotate process`);
  else log(`annotate setup: ERROR server did not come up (see ${server.logPath || path.join(dataDir, 'server.log')})`);

  // (7) load probe — only attempt a launch when we actually have a honoring browser AND a
  // running server; otherwise go straight to the (probed) degrade checklist.
  // opts.probeTimeoutMs is already in ms (programmatic callers); ANNOTATE_PROBE_TIMEOUT is
  // in seconds (the CLI/env seam) -> *1000.
  const timeoutMs = opts.probeTimeoutMs != null ? opts.probeTimeoutMs : (Number(process.env.ANNOTATE_PROBE_TIMEOUT) || 30) * 1000;
  let probe = { loaded: false, attempted: false };
  if (browser.path && browser.honors && server.up && profileWritable(profileDir) && extValid.ok) {
    log(`annotate setup: load probe — launching ${browser.kind} with --load-extension (timeout ${Math.round(timeoutMs / 1000)}s)…`);
    probe = await runLoadProbe({ dataDir, port, browserPath: browser.path, profileDir, extensionDir, timeoutMs: Math.max(2000, timeoutMs) });
    probe.attempted = true;
    log(`annotate setup: load probe ${probe.loaded ? 'HEARTBEAT received' : 'no heartbeat'} (count ${probe.baseline} -> ${probe.finalCount})`);
  } else {
    log('annotate setup: load probe skipped (no honoring browser / server down / unusable profile or extension)');
  }

  const verdict = probe.loaded ? 'pass' : 'degrade';
  const result = {
    verdict,
    dataDir,
    runtimePath,
    runtime,
    port,
    extensionDir,
    profileDir,
    browser,
    consent,
    server,
    probe,
    checklist: [],
  };

  if (verdict === 'pass') {
    log('annotate setup: READY — server up, profile created, extension confirmed loaded (heartbeat).');
  } else {
    const checklist = computeDegradeChecklist({
      browser,
      profileDir,
      extensionDir,
      port,
      dataDir,
      runtimePath,
      serverUp: server.up,
      serverForeign: !!server.foreign,
    });
    result.checklist = checklist;
    log(`annotate setup: DEGRADED — ${checklist.length} manual step(s) (computed by probing):`);
    checklist.forEach((it, i) => log(`  ${i + 1}. [${it.id}] ${it.message}`));
  }

  return result;
}

// ---------------------------------------------------------------------------
// CLI — `install` (from install.sh, after npm install) and `setup` (repair entry,
// from bin/annotate). Both run the same engine. Tiny --key value parser.
// ---------------------------------------------------------------------------

function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (t.slice(0, 2) === '--') {
      const key = t.slice(2);
      const eq = key.indexOf('=');
      if (eq >= 0) {
        a[key.slice(0, eq)] = key.slice(eq + 1);
      } else {
        const next = argv[i + 1];
        if (next === undefined || next.slice(0, 2) === '--') a[key] = true;
        else { a[key] = next; i++; }
      }
    }
  }
  return a;
}

async function main() {
  const mode = process.argv[2] === 'install' || process.argv[2] === 'setup' ? process.argv[2] : 'setup';
  const a = parseArgs(process.argv.slice(process.argv[2] === mode ? 3 : 2));
  const opts = {};
  if (a['data-dir']) opts.dataDir = a['data-dir'];
  if (a.runtime) opts.runtimePath = a.runtime;
  if (a.port) opts.port = a.port;
  if (a.extension) opts.extensionDir = a.extension;
  if (a.profile) opts.profileDir = a.profile;
  if (a['probe-timeout']) opts.probeTimeoutMs = Number(a['probe-timeout']) * 1000;
  if (a['no-download']) opts.noDownload = true;
  if (a['download-cft']) opts.confirmDownload = true; // #10 explicit CfT-download confirmation

  const result = await runSetup(opts);
  // Exit codes: 0 = full pass OR a deliberately-declined (dormant) install; 3 = consent
  // required (the #10 gate fired — re-run with --download-cft or write config.json); 2 =
  // degraded-with-checklist (completed, human steps needed; already printed). A hard error
  // throws before here -> non-zero via catch.
  let code;
  if (result.verdict === 'pass' || result.verdict === 'declined') code = 0;
  else if (result.verdict === 'needs-consent') code = 3;
  else code = 2;
  process.exit(code);
}

module.exports = {
  runSetup,
  resolveTargets,
  buildRuntime,
  writeRuntime,
  classifyBrowser,
  validateExtensionDir,
  profileWritable,
  provisionBrowser,
  computeDegradeChecklist,
  findInstalledCft,
  findSystemChromium,
  ensureServer,
  runLoadProbe,
  consentPath,
  readConsent,
  PKG_EXTENSION,
  PROBE_SESSION,
  CONSENT_FILE,
};

if (require.main === module) {
  main().catch((err) => {
    process.stderr.write(`annotate setup: FATAL ${err && err.stack ? err.stack : err}\n`);
    process.exit(1);
  });
}
