'use strict';

// DOM anchor adapter (tech-requirements §6.4, "DOM adapter" — built first, leverage
// order DOM -> code/line -> image).
//
// Turns a clicked DOM node into a §5.2 `anchor` object by walking UP to the nearest
// anchorable ancestor and reading whichever position attribute the renderer (§6.2)
// stamped onto it. The renderer's stamped attributes ARE the anchor map (no separate
// anchorMap structure exists — a resolved §6.2 design decision), so this adapter is the
// whole click->anchor serializer for rendered text / structured / tabular views:
//
//   data-src-line  (el.dataset.srcLine) -> { kind:'source', line:N }      Markdown blocks, code lines
//   data-key-path  (el.dataset.keyPath) -> { kind:'source', keyPath:'…' } JSON / YAML / TOML nodes
//   data-cell      (el.dataset.cell)    -> { kind:'source', cell:'C3' }   CSV / TSV cells
//
// Only ONE of the three is ever present per rendered format, so `closest` over the
// union selector deterministically finds the nearest anchorable region regardless of
// format. We read via getAttribute/hasAttribute (equivalent to the `dataset` mapping,
// but presence-correct: `data-key-path=""` — the structured ROOT — is a real keyPath
// whose value is falsy, so it must be detected by attribute presence, not truthiness).
//
// MODULE STRATEGY (see submit.js header): unit-tested via require() in Node now; in T6a
// this same file loads as an MV3 content script and attaches to globalThis.Annotate.dom.

(function (root) {
  'use strict';

  // The three position attributes the §6.2 renderer stamps. Order is irrelevant for
  // matching (only one kind exists per format) but fixed for determinism.
  const ANCHORABLE_SELECTOR = '[data-src-line], [data-key-path], [data-cell]';

  // Walk from the clicked node to the nearest anchorable element (self or ancestor).
  function nearestAnchorable(el) {
    if (!el || typeof el.closest !== 'function') return null;
    return el.closest(ANCHORABLE_SELECTOR);
  }

  // Click target -> §5.2 anchor object (kind:'source'), or null if nothing anchorable
  // is in the ancestor chain (e.g. clicking the bare render wrapper).
  function anchorFromElement(el) {
    const node = nearestAnchorable(el);
    if (!node) return null;

    // data-src-line: 1-based integer string -> anchor.line (Markdown block / code line).
    if (node.hasAttribute('data-src-line')) {
      return { kind: 'source', line: parseInt(node.getAttribute('data-src-line'), 10) };
    }
    // data-key-path: dotted path with bracketed indices -> anchor.keyPath. Root = "".
    if (node.hasAttribute('data-key-path')) {
      return { kind: 'source', keyPath: node.getAttribute('data-key-path') };
    }
    // data-cell: spreadsheet address (e.g. "C3") -> anchor.cell.
    if (node.hasAttribute('data-cell')) {
      return { kind: 'source', cell: node.getAttribute('data-cell') };
    }
    return null;
  }

  // Convenience for the live click path: resolve the element under (x,y) via the page's
  // own document.elementFromPoint (the §6.4 "real-inspector" primitive) and serialize it.
  // Thin wrapper over anchorFromElement; the heavy lifting is the DOM walk above.
  function anchorFromPoint(doc, x, y) {
    if (!doc || typeof doc.elementFromPoint !== 'function') return null;
    return anchorFromElement(doc.elementFromPoint(x, y));
  }

  const api = { ANCHORABLE_SELECTOR, nearestAnchorable, anchorFromElement, anchorFromPoint };

  if (typeof module === 'object' && module.exports) {
    module.exports = api; // Node (tests now)
  } else {
    root.Annotate = root.Annotate || {};
    root.Annotate.dom = api; // MV3 content script (T6a)
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
