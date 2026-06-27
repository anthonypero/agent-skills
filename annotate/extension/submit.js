'use strict';

// Submit engine — collection-level serialization, the disjoint-range check, the §5.5
// POST bundle producer, and the stand-in feedback sink seam (tech-requirements §5.2,
// §5.5, §6.4, §8 build-order note).
//
// Pipeline a content script (T6a) runs on "send":
//   drafts (bubble.toFeedback() outputs) -> assignIds (a1..aN, §5.2)
//     -> checkDisjointEdits (reject overlapping EDITs; comments may overlap, §5.2/§6.4)
//     -> buildBundle (the §5.5 /feedback request body + auth header) -> sink(bundle)
//
// THE SINK SEAM (§8 build order step 2): there is no server yet (step 3). submitFeedback
// posts the bundle to an injected `sink(bundle) -> response` function. In T3 the sink is a
// stand-in (makeStandInSink below / the test stub) that just records the bundle and mirrors
// the §5.5 200 shape. In T6a the sink becomes `fetch(origin + '/feedback', {method:'POST',
// headers: bundle.headers, body: JSON.stringify(bundle.body)})` against the real server —
// no other call site changes.
//
// MODULE STRATEGY: every extension module here (dom.js, code.js, bubble.js, submit.js) is a
// dependency-free UMD-ish file: `require()`-able by these Node tests now, and loadable as an
// MV3 content script in T6a where each attaches to a shared `globalThis.Annotate.<name>`
// namespace (Annotate.dom / .code / .bubble / .submit). The MV3 manifest lists them in the
// content_scripts `js` array (any order — they have no inter-module load deps); content.js
// orchestrates by calling Annotate.dom.anchorFromElement(...), Annotate.bubble.createBubble(...),
// Annotate.submit.submitFeedback(...). No bundler required.

(function (root) {
  'use strict';

  const ID_PREFIX = 'a';

  // §5.5 body fields the EXTENSION runtime context fills (page/session-derived) and the
  // per-session auth token (minted at launch, §5.5/§6.3). Clearly-named placeholders so a
  // missing value is obvious in a bundle rather than silently undefined; T6a injects the
  // real values from the page's runtime config.
  const PLACEHOLDER = {
    session: '<SESSION_ID>',
    artifact: '<ARTIFACT_ID>',
    head: '<HEAD_GUID>',
    token: '<SESSION_TOKEN>',
  };

  // A fresh per-submit idempotency key (§5.5 `nonce`). crypto.randomUUID exists in both
  // Node 18+ and the browser; the fallback is adequate for idempotency keying only.
  function makeNonce() {
    const c = (typeof globalThis !== 'undefined' && globalThis.crypto) || null;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    return 'nonce-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  // Assign monotonic within-submit ids a1..aN (§5.2). Returns NEW objects (id first); any
  // pre-existing id is replaced so ordering is canonical.
  function assignIds(drafts) {
    return (drafts || []).map((d, i) => {
      const id = ID_PREFIX + (i + 1);
      const rest = Object.assign({}, d);
      delete rest.id;
      return Object.assign({ id }, rest);
    });
  }

  // ---- overlap predicates (the "same source range" test, §5.2) ---------------------

  // Half-open numeric interval overlap, for reserved charRange [start, end].
  function intervalsOverlap(a, b) {
    return a[0] < b[1] && b[0] < a[1];
  }

  // keyPath A overlaps B iff equal OR one is an ancestor of the other — editing a
  // container ('user') rewrites the SAME source bytes as editing a descendant
  // ('user.name'), so they conflict; siblings ('user.name' vs 'user.roles') are disjoint.
  function isPrefixPath(parent, child) {
    if (parent === '') return true; // the document root contains every node
    if (!child.startsWith(parent)) return false;
    const next = child.charAt(parent.length);
    return next === '' || next === '.' || next === '['; // path-boundary, not 'user' vs 'username'
  }
  function keyPathOverlap(p, q) {
    return p === q || isPrefixPath(p, q) || isPrefixPath(q, p);
  }

  // Normalized rectangle / point overlap (forward-compat: spatial EDITs are schema-valid).
  function boxesOverlap(a, b) {
    return a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
  }
  function pointInBox(p, box) {
    return p[0] >= box[0] && p[0] <= box[0] + box[2] && p[1] >= box[1] && p[1] <= box[1] + box[3];
  }

  // Inclusive 1-based line span for a source anchor: a single `line` is [N,N]; a v2
  // section/document `lineRange` is [start,end]. Returns null for non-line source anchors.
  function lineSpan(a) {
    if (Array.isArray(a.lineRange)) return [a.lineRange[0], a.lineRange[1]];
    if (a.line != null) return [a.line, a.line];
    return null;
  }

  function sourceOverlap(a, b) {
    // v2 #2: if either side is a line RANGE (section / whole-document), compare inclusive
    // line spans. A line-range can only overlap another line-based source anchor.
    if (Array.isArray(a.lineRange) || Array.isArray(b.lineRange)) {
      const sa = lineSpan(a);
      const sb = lineSpan(b);
      if (sa && sb) return sa[0] <= sb[1] && sb[0] <= sa[1];
      return false;
    }
    if (a.line != null && b.line != null) {
      if (Array.isArray(a.charRange) && Array.isArray(b.charRange)) {
        return a.line === b.line && intervalsOverlap(a.charRange, b.charRange);
      }
      return a.line === b.line; // v1: a line anchor's range IS that single line
    }
    if (a.keyPath != null && b.keyPath != null) return keyPathOverlap(a.keyPath, b.keyPath);
    if (a.cell != null && b.cell != null) return a.cell === b.cell;
    if (a.line == null && b.line == null && Array.isArray(a.charRange) && Array.isArray(b.charRange)) {
      return intervalsOverlap(a.charRange, b.charRange);
    }
    return false; // different source sub-fields can't co-occur in one artifact
  }

  function spatialOverlap(a, b) {
    if (Array.isArray(a.box) && Array.isArray(b.box)) return boxesOverlap(a.box, b.box);
    if (Array.isArray(a.box) && Array.isArray(b.point)) return pointInBox(b.point, a.box);
    if (Array.isArray(b.box) && Array.isArray(a.point)) return pointInBox(a.point, b.box);
    if (Array.isArray(a.point) && Array.isArray(b.point)) return a.point[0] === b.point[0] && a.point[1] === b.point[1];
    return false;
  }

  // Conservative: two text anchors overlap only when their quote is identical.
  function textOverlap(a, b) {
    return a.quote != null && a.quote === b.quote;
  }

  // Do two §5.2 anchors cover an overlapping region? Only same-kind anchors can overlap.
  function anchorsOverlap(a, b) {
    if (!a || !b || a.kind !== b.kind) return false;
    if (a.kind === 'source') return sourceOverlap(a, b);
    if (a.kind === 'spatial') return spatialOverlap(a, b);
    if (a.kind === 'text') return textOverlap(a, b);
    return false;
  }

  // The disjoint-range check (§5.2, §6.4): across all type:'edit' items in one submit,
  // no two may overlap the same source range. Comments are exempt (may overlap each other
  // AND edits). Returns { ok, conflicts:[{a,b}] } with the conflicting EDIT ids.
  function checkDisjointEdits(items) {
    const edits = (items || []).filter((it) => it && it.type === 'edit');
    const conflicts = [];
    for (let i = 0; i < edits.length; i++) {
      for (let j = i + 1; j < edits.length; j++) {
        if (anchorsOverlap(edits[i].anchor, edits[j].anchor)) {
          conflicts.push({ a: edits[i].id, b: edits[j].id });
        }
      }
    }
    return { ok: conflicts.length === 0, conflicts };
  }

  // ---- §5.5 POST /feedback bundle producer -----------------------------------------

  // Produce the EXACT §5.5 request: body { session, artifact, head, anchors, revertTarget,
  // screenshot, nonce } + auth via the X-Annotate-Token header (the token rides the header
  // per §5.5, NOT the body). The token slot is present here so T6a only injects values.
  function buildBundle(opts) {
    opts = opts || {};
    const body = {
      session: opts.session != null ? opts.session : PLACEHOLDER.session,
      artifact: opts.artifact != null ? opts.artifact : PLACEHOLDER.artifact,
      head: opts.head != null ? opts.head : PLACEHOLDER.head,
      anchors: opts.feedback || opts.anchors || [],
      revertTarget: opts.revertTarget != null ? opts.revertTarget : null,
      screenshot: opts.screenshot != null ? opts.screenshot : null,
      nonce: opts.nonce != null ? opts.nonce : makeNonce(),
    };
    const token = opts.token != null ? opts.token : PLACEHOLDER.token;
    const headers = { 'Content-Type': 'application/json', 'X-Annotate-Token': token };
    return { body, headers, token };
  }

  // Orchestrate one submit. `params.drafts` = bubble.toFeedback() outputs; `params.context`
  // = the §5.5 runtime fields (session/artifact/head/revertTarget/screenshot/nonce/token);
  // `params.sink` = the (stand-in now, fetch-backed in T6a) sink(bundle) -> response.
  // Returns the disjoint failure WITHOUT calling the sink, or { ok, bundle, items, response }.
  async function submitFeedback(params) {
    params = params || {};
    const items = assignIds(params.drafts || params.feedback || []);
    const disjoint = checkDisjointEdits(items);
    if (!disjoint.ok) {
      return { ok: false, error: 'overlapping-edits', conflicts: disjoint.conflicts };
    }
    const bundle = buildBundle(Object.assign({}, params.context || {}, { feedback: items }));
    const sink = params.sink;
    if (typeof sink !== 'function') {
      throw new Error('submitFeedback requires a sink(bundle) function');
    }
    const response = await sink(bundle);
    return { ok: true, bundle, items, response };
  }

  // Reference stand-in sink (§8 build-order step 2): records bundles and mirrors the §5.5
  // 200 response shape. NO server. T6a replaces this with a fetch('/feedback') sink.
  function makeStandInSink() {
    const received = [];
    const sink = async (bundle) => {
      received.push(bundle);
      return { status: 'submitted', head: bundle.body.head };
    };
    sink.received = received;
    return sink;
  }

  const api = {
    ID_PREFIX, PLACEHOLDER,
    makeNonce, assignIds,
    anchorsOverlap, checkDisjointEdits,
    buildBundle, submitFeedback, makeStandInSink,
  };

  if (typeof module === 'object' && module.exports) {
    module.exports = api; // Node (tests now)
  } else {
    root.Annotate = root.Annotate || {};
    root.Annotate.submit = api; // MV3 content script (T6a)
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
