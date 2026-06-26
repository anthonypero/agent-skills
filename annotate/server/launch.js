'use strict';

// Launch-side Node helper for `bin/annotate` (POSIX sh) — tech-requirements §6.1, §3.
//
// The shell entrypoint owns mode/timeout/flow but delegates every JSON-safe step to
// this helper (per §3 "the script delegates JSON-safe work … to a bundled Node helper"):
//   - reading the generated runtime.json (§6.6)
//   - id derivation + the ONE creation write (snapshot + 4-field stub) via create.js (§2.2)
//   - probing the lazy-singleton server (§6.1 step 6)
//   - the blocking poll that reads the return bundle {source,snapshot,feedback} off disk
//     (§6.1 step 8, §5.4 — the resolved `snapshot` pointer is computed here, not in shell)
//
// All subcommands print machine-readable output the shell parses. Newline-delimited KV
// commands (`config`, `create`) end with a `.` SENTINEL line so a trailing EMPTY value
// (e.g. an unset browser path) keeps its newline through `$(…)` trailing-newline stripping.
//
// Subcommands:
//   config   --runtime <path>
//       -> port / dataDir / extension / profile / browserPath / browserKind / "."
//   create   --data-dir <d> [--source <p>] [--url <u>] [--session <s>] [--artifact <a>]
//       -> session / artifact / guid / roundFile / roundDir / snapshot / source / token / dataDir / "."
//   serverup --port <n>
//       -> exit 0 = an annotate server answers (reuse) | 3 = port held by a foreigner | 4 = down
//   wait     --data-dir <d> --session <s> --artifact <a> [--guid <g>] --timeout <secs> [--interval <secs>]
//       -> blocks until the (head | --guid) round leaves `pending`, prints {source,snapshot,feedback},
//          exit 0; on timeout exit 124 (the shell maps that to the §6.1 nudge + non-zero exit).

const http = require('node:http');
const path = require('node:path');

const P = require('./protocol.js');
const { create } = require('./create.js');

const PKG_ROOT = path.join(__dirname, '..');

// --- tiny `--key value` / `--flag` parser -----------------------------------
function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (t.slice(0, 2) === '--') {
      const key = t.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.slice(0, 2) === '--') {
        a[key] = true;
      } else {
        a[key] = next;
        i++;
      }
    }
  }
  return a;
}

function out(values) {
  // Each value on its own line, then a `.` sentinel (see header note).
  process.stdout.write(values.map((v) => (v == null ? '' : String(v))).join('\n') + '\n.\n');
}

function fail(msg) {
  process.stderr.write(`annotate-helper: ${msg}\n`);
  process.exit(1);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --- config: read runtime.json (§6.6), fall back to defaults when absent ----
function cmdConfig(args) {
  const runtime = args.runtime && args.runtime !== true ? args.runtime : null;
  let cfg = {};
  if (runtime && P.exists(runtime)) {
    try {
      cfg = P.readJSON(runtime);
    } catch (e) {
      fail(`unreadable runtime.json (${runtime}): ${e.message}`);
    }
  }
  const paths = cfg.paths || {};
  // Read paths VERBATIM (no ~ expansion) to stay byte-identical with server.js's own
  // dataDir resolution (config.paths.data) — a generated runtime.json carries absolute
  // paths (§6.6, T7), so the two never disagree.
  const dataDir = paths.data || P.defaultDataDir();
  const port = cfg.port != null ? cfg.port : 7878;
  const extension = paths.extension || path.join(PKG_ROOT, 'extension');
  const profile = paths.profile || path.join(dataDir, 'chrome-profile');
  const browser = cfg.browser || {};
  out([port, dataDir, extension, profile, browser.path || '', browser.kind || '']);
}

// --- create: the one creation write (snapshot + 4-field stub) via create.js --
function cmdCreate(args) {
  const opts = { dataDir: args['data-dir'] };
  if (args.source && args.source !== true) opts.source = args.source;
  if (args.url && args.url !== true) opts.url = args.url;
  if (args.session && args.session !== true) opts.session = args.session;
  if (args.artifact && args.artifact !== true) opts.artifact = args.artifact;
  let r;
  try {
    r = create(opts);
  } catch (e) {
    fail(`create failed: ${e.message}`);
    return;
  }
  out([r.session, r.artifact, r.guid, r.roundFile, r.roundDir, r.snapshot || '', r.source || '', r.token, r.dataDir]);
}

// --- serverup: is OUR server already listening on this port? (§6.1 lazy singleton) ---
function cmdServerup(args) {
  const port = Number(args.port);
  if (!Number.isFinite(port) || port <= 0) fail('serverup requires --port <n>');
  // GET /loaded is an annotate-specific route returning { loaded, count }; using it as
  // the identity probe lets us tell OUR server (reuse) from a foreign process on the port.
  const req = http.get({ host: '127.0.0.1', port, path: '/loaded', timeout: 1500 }, (res) => {
    let data = '';
    res.on('data', (c) => (data += c));
    res.on('end', () => {
      let ours = false;
      try {
        const j = JSON.parse(data);
        ours = res.statusCode === 200 && typeof j.count !== 'undefined';
      } catch {
        /* not JSON -> not us */
      }
      process.exit(ours ? 0 : 3); // 0 reuse, 3 foreign-on-port
    });
  });
  req.on('timeout', () => {
    req.destroy();
    process.exit(3); // open but unresponsive -> treat as a foreign holder
  });
  req.on('error', () => process.exit(4)); // ECONNREFUSED / unreachable -> down, start one
}

// --- wait: block until a submit lands, then print {source,snapshot,feedback} --
async function cmdWait(args) {
  const dataDir = args['data-dir'];
  const session = args.session;
  const artifact = args.artifact;
  if (!dataDir || !session || !artifact) fail('wait requires --data-dir, --session, --artifact');
  const fixedGuid = args.guid && args.guid !== true ? args.guid : null;
  const timeoutSecs = Number(args.timeout);
  const timeoutMs = (Number.isFinite(timeoutSecs) ? Math.max(0, timeoutSecs) : 300) * 1000;
  const intervalSecs = Number(args.interval);
  const intervalMs = Math.max(100, (Number.isFinite(intervalSecs) ? intervalSecs : 1) * 1000);

  const aDir = P.artifactDir(dataDir, session, artifact);
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    // --guid pins the round we just created (--wait); without it, resolve the CURRENT
    // head each pass (the `poll` subcommand — never mints a round, §6.1).
    const guid = fixedGuid || P.resolveHead(aDir);
    if (guid) {
      let round = null;
      try {
        round = P.readJSON(P.roundJsonPath(aDir, guid));
      } catch {
        /* stub not yet readable -> keep polling */
      }
      // Unblock when the round leaves `pending` (submitted via /feedback, or accepted
      // on first look — both mean the human acted, §5.1/§5.5).
      if (round && round.status && round.status !== 'pending') {
        const resolvedSnapshot = round.snapshot != null ? round.snapshot : guid; // snapshot ?? own_guid (§5.4)
        const bundle = { source: round.source, snapshot: resolvedSnapshot, feedback: round.feedback || [] };
        process.stdout.write(JSON.stringify(bundle, null, 2) + '\n');
        process.exit(0);
      }
    }
    if (Date.now() >= deadline) process.exit(124); // timeout -> shell prints the §6.1 nudge
    await sleep(intervalMs);
  }
}

// --- dispatch ----------------------------------------------------------------
function main() {
  const sub = process.argv[2];
  const args = parseArgs(process.argv.slice(3));
  switch (sub) {
    case 'config':
      return cmdConfig(args);
    case 'create':
      return cmdCreate(args);
    case 'serverup':
      return cmdServerup(args);
    case 'wait':
      return cmdWait(args);
    default:
      fail(`unknown subcommand: ${sub == null ? '(none)' : sub}`);
  }
}

main();
