'use strict';

// Comment/edit bubble — the per-annotation state machine (tech-requirements §6.4, §5.2;
// seed §9).
//
// One bubble composes ONE annotation on a clicked/selected anchor. The toggle's state
// IS the §5.2 `type` field (seed §9):
//   - default COMMENT (intent — the agent decides the fix): carries a `comment` string.
//   - toggled to EDIT (literal replacement): pre-fills the highlighted ORIGINAL span into
//     the edit box so the human edits FROM it — `replacement` is seeded equal to `original`,
//     then the human edits `replacement`. The agent applies it and surfaces consequences.
//
// The bubble emits a §5.2 feedback item (no `id` — ids are assigned monotonically at
// submit/serialization time by submit.js so the disjoint-range check can reference them,
// §5.2). createBubble(anchor, opts) where:
//   anchor:            the §5.2 anchor from the dom/code/image adapter
//   opts.selectedText: the highlighted original span captured at click (-> `original`)
//   opts.original:     alias for selectedText (explicit)
//   opts.comment:      initial comment text (optional)
//   opts.elementContext: optional live-page per-element harvest (§5.2; passed through)
//
// MODULE STRATEGY (see submit.js header): require()-able now; loads as an MV3 content
// script in T6a (attaches to globalThis.Annotate.bubble).

(function (root) {
  'use strict';

  const DEFAULT_TYPE = 'comment';

  function createBubble(anchor, opts) {
    opts = opts || {};

    // The highlighted original span, captured up front at click time. The type toggle
    // decides whether it is emitted (edit) or ignored (comment).
    const selected =
      opts.original != null ? String(opts.original)
        : opts.selectedText != null ? String(opts.selectedText)
          : '';

    const state = {
      anchor: anchor || null,
      type: DEFAULT_TYPE,
      comment: opts.comment != null ? String(opts.comment) : '',
      original: selected,
      replacement: '',
      elementContext: opts.elementContext || null,
      // v2.2 §I: the stored filename of a user-attached image (copied into the round folder
      // on select). null = no attachment. Distinct from the tool's auto-capture screenshot.
      attachment: opts.attachment != null && opts.attachment !== '' ? String(opts.attachment) : null,
    };

    let api; // forward ref for chaining

    function setType(t) {
      if (t !== 'comment' && t !== 'edit') {
        throw new Error('bubble type must be "comment" or "edit"');
      }
      const prev = state.type;
      state.type = t;
      // On the first switch into EDIT, pre-fill the edit box from the highlighted
      // original span (seed §9, §6.4) so the human edits from it.
      if (t === 'edit' && prev !== 'edit' && state.replacement === '') {
        state.replacement = state.original;
      }
      return api;
    }

    function toggle() {
      return setType(state.type === 'comment' ? 'edit' : 'comment');
    }

    function setComment(s) { state.comment = s == null ? '' : String(s); return api; }
    function setOriginal(s) { state.original = s == null ? '' : String(s); return api; }
    function setReplacement(s) { state.replacement = s == null ? '' : String(s); return api; }
    function setAnchor(a) { state.anchor = a; return api; }
    // v2.2 §I: record / clear the user-image attachment (the server-stored filename). null or
    // '' clears it. Independent of the comment/edit type — an attachment may ride either.
    function setAttachment(name) { state.attachment = name == null || name === '' ? null : String(name); return api; }

    // A bubble is submittable when its required §5.2 fields are non-empty for its type.
    // (replacement MAY equal original — an intentional "keep as-is" edit; only emptiness
    // is incompleteness.)
    function isComplete() {
      if (!state.anchor) return false;
      if (state.type === 'comment') return state.comment.length > 0;
      return state.original.length > 0 && state.replacement.length > 0;
    }

    // Emit the §5.2 feedback item. `id` is optional (assigned at submit time, §5.2);
    // pass it through when a caller already knows it.
    function toFeedback(id) {
      const item = id != null ? { id } : {};
      item.type = state.type;
      item.anchor = state.anchor;
      if (state.type === 'comment') {
        item.comment = state.comment;
      } else {
        item.original = state.original;
        item.replacement = state.replacement;
      }
      if (state.elementContext) item.elementContext = state.elementContext;
      if (state.attachment) item.attachment = state.attachment; // v2.2 §I
      return item;
    }

    api = {
      get type() { return state.type; },
      get comment() { return state.comment; },
      get original() { return state.original; },
      get replacement() { return state.replacement; },
      get anchor() { return state.anchor; },
      get attachment() { return state.attachment; },
      setType, toggle, setComment, setOriginal, setReplacement, setAnchor, setAttachment,
      isComplete, toFeedback,
    };
    return api;
  }

  const out = { createBubble, DEFAULT_TYPE };

  if (typeof module === 'object' && module.exports) {
    module.exports = out; // Node (tests now)
  } else {
    root.Annotate = root.Annotate || {};
    root.Annotate.bubble = out; // MV3 content script (T6a)
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
