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

  // How many times `needle` occurs in `haystack` (overlapping-tolerant: steps by 1 each hit).
  function countOccurrences(haystack, needle) {
    if (!needle) return 0;
    let count = 0;
    let from = 0;
    let i;
    while ((i = haystack.indexOf(needle, from)) >= 0) {
      count++;
      from = i + 1;
    }
    return count;
  }

  // v2.4 §B.5 — the DOM-FREE half of the sub-line text-anchor producer. Given a block's plain
  // text (`blockText`, typically `block.textContent`) and the RENDERED selected text (`quote`,
  // what content.js captures as `String(sel).trim()`), decide whether the selection is a STRICT
  // sub-span of the block and, if so, slice the quote's surrounding context.
  //
  //   - returns { kind:'text', quote, context:{ before, after } } for a strict sub-span; or
  //   - returns null to SIGNAL "keep the source anchor" (empty quote, a whole-block selection
  //     where quote === blockText.trim(), or a quote that isn't a literal sub-span of the block).
  //
  // `before`/`after` are 32 chars on each side of the quote, WIDENED (in 32-char steps, capped at
  // the block bounds) until `before + quote + after` is unique within the block — so the §C
  // resolver can re-find the exact occurrence later. The live Range/TreeWalker work (deciding the
  // selection stays within ONE block, cloning the range) stays in content.js (browser-only).
  function textAnchorFromSelectionText(blockText, quote) {
    if (blockText == null || quote == null) return null;
    const text = String(blockText);
    const q = String(quote).trim();
    if (!q) return null;
    if (q === text.trim()) return null; // whole-block selection -> keep the source anchor
    const idx = text.indexOf(q);
    if (idx < 0) return null; // not a literal sub-span (e.g. soft markdown mismatch) -> keep source
    const end = idx + q.length;
    let pad = 32;
    let before = text.slice(Math.max(0, idx - pad), idx);
    let after = text.slice(end, end + pad);
    // Widen the context window until before+quote+after is UNIQUE in the block (or it already
    // spans the whole block, which is trivially unique).
    while (
      countOccurrences(text, before + q + after) > 1 &&
      (idx - pad > 0 || end + pad < text.length)
    ) {
      pad += 32;
      before = text.slice(Math.max(0, idx - pad), idx);
      after = text.slice(end, end + pad);
    }
    return { kind: 'text', quote: q, context: { before: before, after: after } };
  }

  const api = {
    ANCHORABLE_SELECTOR,
    nearestAnchorable,
    anchorFromElement,
    anchorFromPoint,
    textAnchorFromSelectionText,
  };

  if (typeof module === 'object' && module.exports) {
    module.exports = api; // Node (tests now)
  } else {
    root.Annotate = root.Annotate || {};
    root.Annotate.dom = api; // MV3 content script (T6a)
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
