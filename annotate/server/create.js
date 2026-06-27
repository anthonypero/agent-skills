'use strict';

// Round-creation helper (tech-requirements §6.1 steps 1-5, §2.2, §5.1).
//
// The launch script (`bin/annotate`, T5) invokes this synchronously during the
// CREATION phase to derive the session / artifact / round ids and write the round
// descriptor stub. It runs once and exits, so it does NOT violate the single-writer
// discipline (§2.2): the long-lived server owns the MUTATION phase; this helper owns
// the one creation write per round. JSON-safe encoding of `source` lives here (not in
// shell) per §3.
//
// Interface:
//   create({ dataDir, source, session, artifact, url }) -> {
//     dataDir, session, artifact, guid, roundDir, roundFile,
//     snapshot, source, url, token
//   }
//
//   - dataDir   : data root (defaults to ~/.annotate). Created 0700 (§6.3).
//   - source    : absolute/relative path to the real project file (the descriptor
//                 `source` pointer). null/omitted for an unowned live page (§5.1).
//   - session   : explicit session id (§6.1 --session) or omitted to generate one.
//   - artifact  : explicit artifact id, or omitted to derive from the source basename
//                 (collision -> short hash of the abs path, §6.1 step 2).
//   - url       : a live-page URL to register in the URL->artifact map (§5.3/§6.4).
//
// WRITE ORDER (load-bearing for head resolution, §6.3): the snapshot byte-copy is
// written FIRST, the descriptor stub LAST — so the stub's presence on disk proves the
// round is fully materialized and the head never advances onto a half-created round.

const fs = require('node:fs');
const path = require('node:path');

const P = require('./protocol.js');

// Read the `source` recorded in an existing artifact's most recent round, to decide
// whether a basename collision is the SAME source (reuse the id) or a different one
// (disambiguate). Returns the absolute source string, or undefined.
function existingArtifactSource(aDir) {
  const guids = P.listRoundGuids(aDir);
  if (!guids.length) return undefined;
  try {
    const round = P.readJSON(path.join(aDir, guids[0], `${guids[0]}-round.json`));
    return round.source == null ? null : round.source;
  } catch {
    return undefined;
  }
}

// §6.1 step 2: artifact id = source basename without extension; on a within-session
// basename collision against a DIFFERENT source, disambiguate with a short hash of the
// absolute path (deterministic -> re-opening the same source resolves to the same id).
function resolveArtifactId(dataDir, session, absSource) {
  const baseId = path.basename(absSource, path.extname(absSource)) || 'artifact';
  const baseDir = P.artifactDir(dataDir, session, baseId);
  if (!P.exists(baseDir)) return baseId;
  const prior = existingArtifactSource(baseDir);
  // Same source (or an empty/ambiguous prior) -> reuse; different source -> hash.
  if (prior === undefined || prior === absSource) return baseId;
  return `${baseId}-${P.shortHash(absSource)}`;
}

// Generated session FALLBACK id (§6.1 step 1; v2 punch-list #6b). DISTINCT from the
// round-GUID shape (<timestamp>-<8char>) so a session id is never mistaken for a
// round/version id — the `sess-` prefix is the marker (the round GUID stays the version
// unit and is untouched). Prefer an explicit --session (the harness session id, passed by
// SKILL.md / picked up from CLAUDE_CODE_SESSION_ID in bin/annotate); this is only the
// no-id fallback. Keeps a sortable timestamp for debuggability, behind the prefix.
function makeSessionId(d = new Date()) {
  return `sess-${P.makeTimestamp(d)}-${P.randSuffix(6)}`;
}

// A live page has no file basename; derive a stable id from the URL.
function artifactIdFromUrl(url) {
  try {
    const u = new URL(url);
    const last = u.pathname.split('/').filter(Boolean).pop();
    const slug = (last || u.hostname).replace(/[^a-zA-Z0-9._-]/g, '-');
    return `${slug || 'live'}-${P.shortHash(url)}`;
  } catch {
    return `live-${P.shortHash(url)}`;
  }
}

function create(opts = {}) {
  const dataDir = opts.dataDir || P.defaultDataDir();
  P.ensureDir(dataDir, 0o700);

  // 1. Session id (§6.1 step 1). Explicit --session wins (the harness session id);
  //    otherwise a DISTINCT `sess-`-prefixed fallback (NOT the round-GUID shape, #6b).
  const session = opts.session || makeSessionId();
  const sDir = P.sessionDir(dataDir, session);
  P.ensureDir(sDir, 0o700);
  const token = P.ensureToken(sDir); // per-session auth token (§6.3)

  // Normalize the source path (the descriptor `source`, §5.1). null for a live page.
  const absSource = opts.source != null ? path.resolve(opts.source) : null;
  const url = opts.url != null ? opts.url : null;

  // 2. Artifact id (§6.1 step 2).
  let artifact = opts.artifact;
  if (!artifact) {
    if (absSource) artifact = resolveArtifactId(dataDir, session, absSource);
    else if (url) artifact = artifactIdFromUrl(url);
    else throw new Error('create requires a `source` path or a `url` to derive an artifact id');
  }
  const aDir = P.artifactDir(dataDir, session, artifact);
  P.ensureDir(aDir, 0o700);

  // 3. Round guid + folder (§6.1 step 3).
  const guid = P.makeGuid();
  const rDir = P.roundDir(dataDir, session, artifact, guid);
  P.ensureDir(rDir, 0o700);

  // 4. Snapshot: byte-for-byte copy FIRST (§6.1 step 4). A live page (no source file)
  //    has no byte copy — the extension harvests elementContext + screenshot (§5.3).
  let snapshot = null;
  if (absSource && P.exists(absSource)) {
    const ext = path.extname(absSource);
    const snapName = `${guid}-snapshot${ext}`;
    fs.copyFileSync(absSource, path.join(rDir, snapName));
    snapshot = snapName;
  }

  // 5. The one creation write: the round stub (§6.1 step 5, §5.1) — exactly four
  //    fields. snapshot: null === "this round's own snapshot" (resolve snapshot ?? guid).
  //    Written LAST and atomically (§2.4).
  const stub = { source: absSource, snapshot: null, status: 'pending', feedback: [] };
  const roundFile = P.roundJsonIn(rDir, guid);
  P.atomicWriteJSON(roundFile, stub);

  // URL->artifact registration for live pages (§5.3/§6.4).
  if (url) P.registerUrl(dataDir, url, { session, artifact });

  return {
    dataDir,
    session,
    artifact,
    guid,
    roundDir: rDir,
    roundFile,
    snapshot, // basename of the snapshot file, or null for a live page
    source: absSource,
    url,
    token,
  };
}

module.exports = { create, resolveArtifactId, artifactIdFromUrl, makeSessionId };
