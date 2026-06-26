'use strict';

// File protocol + low-level helpers shared by create.js (creation phase) and
// server.js (mutation phase) — tech-requirements §2.2, §2.4, §4 (on-disk layout),
// §5.1, §5.3, §6.1, §6.3.
//
// This module is the single place the on-disk shape lives:
//   ~/.annotate/                              <- data root, mode 0700 (§6.3)
//     runtime.json                            <- generated config (§6.6, read by server.js)
//     url-map.json                            <- live-page URL -> {session,artifact} (§5.3/§6.4)
//     <session>/                              <- one agent run (§4), mode 0700
//       session.json                          <- { token } per-session auth token (§6.3), 0600
//       <artifact>/                           <- one reviewed file (§4), mode 0700
//         <guid>/                             <- one round; <guid> = <timestamp>-<8char>
//           <guid>-snapshot.<ext>             <- frozen byte copy; written BEFORE the stub
//           <guid>-round.json                 <- the 4-field descriptor (§5.1); LAST write
//           <guid>-screenshot.png             <- viewport PNG when captured (§5.3)
//           <guid>-nonce                      <- last-applied idempotency nonce (sidecar, §5.5)
//
// NONCE STORAGE (the open spec gap the T4 brief flags): §5.1 fixes the round
// descriptor at an EXACT 4-field set and round.schema.json enforces
// additionalProperties:false, so the nonce CANNOT live in the descriptor without
// breaking schema validation. It is persisted in a per-round SIDECAR file
// `<guid>-nonce` instead — a disk record (so idempotency survives a server restart,
// honoring §2.3 "disk is the source of truth") that leaves the §5.1 descriptor
// untouched. See server.js POST /feedback.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

// ---------------------------------------------------------------------------
// IDs — §9 GUID convention: <timestamp>-<8char>, the timestamp a fixed-width,
// lexically-sortable encoding so `ls` order is chronological with zero file reads.
// ---------------------------------------------------------------------------

function makeTimestamp(d = new Date()) {
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return (
    `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `T${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}${p(d.getMilliseconds(), 3)}`
  );
}

function randSuffix(n = 8) {
  return crypto.randomBytes(Math.ceil(n / 2)).toString('hex').slice(0, n);
}

function makeGuid(d = new Date()) {
  return `${makeTimestamp(d)}-${randSuffix(8)}`;
}

// Short deterministic hash of the absolute source path, for artifact-id collision
// disambiguation (§6.1 step 2): stateless, so re-opening the same source always
// resolves to the SAME artifact id (rounds accumulate) — unlike a sequential counter.
function shortHash(s) {
  return crypto.createHash('sha1').update(String(s)).digest('hex').slice(0, 6);
}

// ---------------------------------------------------------------------------
// Path helpers (all rooted at an explicit dataDir, so the layout is injectable
// for tests and the real ~/.annotate is just the default).
// ---------------------------------------------------------------------------

function defaultDataDir() {
  return path.join(os.homedir(), '.annotate');
}

function sessionDir(dataDir, session) {
  return path.join(dataDir, session);
}
function artifactDir(dataDir, session, artifact) {
  return path.join(dataDir, session, artifact);
}
function roundDir(dataDir, session, artifact, guid) {
  return path.join(dataDir, session, artifact, guid);
}
function roundJsonPath(aDir, guid) {
  return path.join(aDir, guid, `${guid}-round.json`);
}
function roundJsonIn(rDir, guid) {
  return path.join(rDir, `${guid}-round.json`);
}
function screenshotIn(rDir, guid) {
  return path.join(rDir, `${guid}-screenshot.png`);
}
function nonceIn(rDir, guid) {
  return path.join(rDir, `${guid}-nonce`);
}
function tokenPath(sDir) {
  return path.join(sDir, 'session.json');
}
function urlMapPath(dataDir) {
  return path.join(dataDir, 'url-map.json');
}

// ---------------------------------------------------------------------------
// Atomic writes — §2.4: every mutation is written to a temp path in the SAME dir
// and rename()d into place. A crashed write leaves the prior file intact; a reader
// always sees a whole, valid document.
// ---------------------------------------------------------------------------

function ensureDir(dir, mode = 0o700) {
  fs.mkdirSync(dir, { recursive: true, mode });
  // recursive mkdir does not always re-chmod an existing dir; enforce the mode.
  try {
    fs.chmodSync(dir, mode);
  } catch {
    /* best-effort on platforms that ignore mode */
  }
}

function atomicWriteFile(dest, data, mode) {
  const dir = path.dirname(dest);
  const tmp = path.join(dir, `.${path.basename(dest)}.${randSuffix(8)}.tmp`);
  fs.writeFileSync(tmp, data, mode != null ? { mode } : undefined);
  try {
    fs.renameSync(tmp, dest); // atomic on POSIX within one filesystem
  } catch (e) {
    try {
      fs.unlinkSync(tmp);
    } catch {
      /* ignore */
    }
    throw e;
  }
}

function atomicWriteJSON(dest, obj, mode) {
  atomicWriteFile(dest, `${JSON.stringify(obj, null, 2)}\n`, mode);
}

function readJSON(p) {
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

function exists(p) {
  try {
    fs.accessSync(p);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Round / head resolution — §6.3: the head is the latest round by `ls`-order
// whose descriptor stub (<guid>-round.json) has been written. The descriptor is
// the LAST creation write (snapshot first, §6.1), so its presence guarantees the
// round is fully materialized — a half-created round is not yet eligible.
// ---------------------------------------------------------------------------

function listRoundGuids(aDir) {
  let entries;
  try {
    entries = fs.readdirSync(aDir, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .filter((guid) => exists(path.join(aDir, guid, `${guid}-round.json`)))
    .sort(); // lexical sort === chronological (fixed-width timestamp prefix)
}

function resolveHead(aDir) {
  const guids = listRoundGuids(aDir);
  return guids.length ? guids[guids.length - 1] : null;
}

function findSnapshot(rDir, guid) {
  let names;
  try {
    names = fs.readdirSync(rDir);
  } catch {
    return null;
  }
  const name = names.find((n) => n.startsWith(`${guid}-snapshot`));
  return name ? path.join(rDir, name) : null;
}

// ---------------------------------------------------------------------------
// Per-session auth token — §6.3: a per-session token, minted at launch (creation
// phase, by create.js) and read by the server to authorize mutation routes. Stored
// in a per-session sidecar (session.json), NOT in the 4-field round descriptor.
// ---------------------------------------------------------------------------

function ensureToken(sDir) {
  const tp = tokenPath(sDir);
  if (exists(tp)) {
    try {
      return readJSON(tp).token;
    } catch {
      /* fall through and re-mint */
    }
  }
  const token = crypto.randomBytes(24).toString('hex');
  atomicWriteJSON(tp, { token }, 0o600);
  return token;
}

function readToken(sDir) {
  try {
    return readJSON(tokenPath(sDir)).token;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Live-page URL registration — §5.3/§6.4: a live page served from a foreign origin
// can't carry annotate's /<session>/<artifact> path, so the launch script registers
// the URL at round creation and the server holds the URL -> {session,artifact} map.
// ---------------------------------------------------------------------------

function registerUrl(dataDir, url, mapping) {
  const mp = urlMapPath(dataDir);
  let map = {};
  if (exists(mp)) {
    try {
      map = readJSON(mp);
    } catch {
      map = {};
    }
  }
  map[url] = { session: mapping.session, artifact: mapping.artifact };
  atomicWriteJSON(mp, map);
}

function resolveUrl(dataDir, url) {
  const mp = urlMapPath(dataDir);
  if (!exists(mp)) return null;
  try {
    const map = readJSON(mp);
    return map[url] || null;
  } catch {
    return null;
  }
}

module.exports = {
  // ids
  makeTimestamp,
  randSuffix,
  makeGuid,
  shortHash,
  // paths
  defaultDataDir,
  sessionDir,
  artifactDir,
  roundDir,
  roundJsonPath,
  roundJsonIn,
  screenshotIn,
  nonceIn,
  tokenPath,
  urlMapPath,
  // io
  ensureDir,
  atomicWriteFile,
  atomicWriteJSON,
  readJSON,
  exists,
  // rounds
  listRoundGuids,
  resolveHead,
  findSnapshot,
  // auth
  ensureToken,
  readToken,
  // url map
  registerUrl,
  resolveUrl,
};
