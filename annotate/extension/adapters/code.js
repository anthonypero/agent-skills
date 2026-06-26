'use strict';

// Code / line anchor adapter (tech-requirements §6.4, "Code/line adapter" — built
// second, the highest-leverage adapter: the bulletproof PR-review bridge where the
// line number IS the anchor).
//
// The §6.2 code render is:
//   <pre class="annotate-code"><code class="hljs">
//     <span class="annotate-line" data-src-line="N">…hljs spans…</span>
//   </code></pre>
// A human clicks anywhere on a syntax-highlighted line — possibly on an inner hljs
// <span> (a keyword, a string) that carries NO data-src-line. This adapter resolves any
// such click to the whole `.annotate-line` wrapper and reads its line number, so the
// affordance is "click the line, get the line" rather than "click the exact char span".
//
// dom.js's generic anchorFromElement ALSO handles this (the union selector catches the
// line wrapper via its data-src-line), so this adapter is the focused specialization
// §6.4 calls out, used by content.js (T6a) to add code-view-specific UX (whole-line
// hover highlight, gutter clicks). Kept dependency-free; attaches to Annotate.code.

(function (root) {
  'use strict';

  const CODE_VIEW_SELECTOR = 'pre.annotate-code';
  const LINE_SELECTOR = '.annotate-line';

  // Are we inside a code-as-code render (vs Markdown / structured / live frontend)?
  function isCodeView(el) {
    return !!(el && typeof el.closest === 'function' && el.closest(CODE_VIEW_SELECTOR));
  }

  // Resolve any click target (incl. an inner hljs span) to its whole-line wrapper.
  function lineElementFor(el) {
    if (!el || typeof el.closest !== 'function') return null;
    return el.closest(LINE_SELECTOR);
  }

  // Click on a rendered code line -> §5.2 source/line anchor, or null if the target is
  // not within a code line.
  function anchorFromCodeNode(el) {
    const line = lineElementFor(el);
    if (!line) return null;
    return { kind: 'source', line: parseInt(line.getAttribute('data-src-line'), 10) };
  }

  const api = { CODE_VIEW_SELECTOR, LINE_SELECTOR, isCodeView, lineElementFor, anchorFromCodeNode };

  if (typeof module === 'object' && module.exports) {
    module.exports = api; // Node (tests now)
  } else {
    root.Annotate = root.Annotate || {};
    root.Annotate.code = api; // MV3 content script (T6a)
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
