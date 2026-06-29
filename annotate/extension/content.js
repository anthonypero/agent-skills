'use strict';

// Content-script orchestrator (tech-requirements §6.4; T6a). Wires the T3 logic modules
// (Annotate.dom / .code / .bubble / .submit) and the T6a config module (Annotate.config)
// into a live review surface over the server-rendered artifact:
//
//   on load   -> resolve { session, artifact, head, token, origin } from the server-injected
//                config (Annotate.config), fire the one-time /loaded heartbeat (§6.6 / S0),
//                inject the REQUIRED top "Annotate chrome" bar (accept + send + format badge +
//                Copy + expand + reserved version slot), and start the 1s head auto-advance poll.
//   click     -> §K click-to-select-innermost + DOM-traversal: a plain click LOCKS the
//                innermost semantic stop under the pointer and parks a comment bubble AT the
//                click (nothing to hover-chase — this replaces the old, unreachable
//                hover→floating-icon block affordance). A live selection box outlines the
//                current level; the bubble's up/down arrows (or ArrowUp/ArrowDown) traverse
//                the DOM stop chain (line/block -> list -> section -> document; cell -> row ->
//                table -> section -> document), re-deriving the §5.2 anchor at each level.
//                Native selection + the browser context menu are left untouched.
//   select    -> a comment icon appears next to a text selection; transient (the icon
//                disappears if the selection collapses before the icon is clicked).
//   comment   -> open the comment/edit composer (openComposerAt — a reusable
//                open-at-an-arbitrary-screen-point path) on the locked level's / selection's
//                §5.2 anchor.
//   add       -> a saved draft becomes a SEMI-TRANSPARENT, type-colored ICON PIN at its anchor
//                (§B; the comment-bubble / edit-pen glyph, ON the anchored words; rides with
//                the content as it scrolls; CSS grows the full marker on hover) PLUS a row in
//                the toggleable comment SIDEBAR. The old right-margin rail is gone. Clicking a
//                pin opens the composer to EDIT that annotation in place.
//   send      -> Annotate.submit.submitFeedback with the REAL fetch sink (token in
//                X-Annotate-Token) -> POST /feedback -> the server flips the round to submitted.
//   accept    -> Annotate.config.postAccept (head-checked) -> the server flips to accepted.
//
// This file does the DOM/event wiring only (browser-bound; covered by the integration gate,
// tests/integration/extension-gate.js). The pure logic it leans on is unit-tested elsewhere
// (config.js -> tests/extension.test.js; the T3 modules -> tests/engine.test.js).
//
// TESTABILITY CONTRACT (all DOM-readable from the page main world, since a content script's
// JS globals are NOT visible cross-world — only the shared DOM is): documentElement gains
// [data-annotate-ready="1"] once initialized; the chrome is #annotate-chrome with .annotate-accept
// and .annotate-send; a click on a [data-src-line] node opens .annotate-composer carrying
// [data-anchor-kind]/[data-anchor-line]; .annotate-composer-input + .annotate-add compose a draft
// (-> a .annotate-comment-pin[data-anchor-line] on the canvas + a .annotate-sidebar-item row,
// §B); after send/accept the chrome carries
// [data-last-submit] / [data-last-accept] result attributes. The integration gate drives these
// via real DOM clicks and reads the result attributes.

(function (root) {
  'use strict';

  const A = root.Annotate || {};
  if (!A.config) {
    // Dependencies not present (the module list mis-ordered, or running outside the extension):
    // nothing to wire. Fail quiet rather than throw on an arbitrary page.
    return;
  }

  const doc = document;
  const fetchImpl = typeof fetch !== 'undefined' ? fetch : null;
  // MV3 content scripts may call a SUBSET of the chrome.* APIs (runtime messaging +
  // storage). Guarded so this file is inert on a non-extension page / under Node.
  const chromeApi = typeof chrome !== 'undefined' ? chrome : null;

  let ctx = null;
  let feedbackSink = null;
  const drafts = []; // §5.2 items (no id; ids assigned at submit by submit.js)
  let pending = null; // { bubble, anchor, element } while a composer is open
  // Fix #1: the composer keeps its TARGET visible while open — { nodes:[], range, element,
  // words } or null. A region anchor gets an outline node; a text selection gets word nodes.
  let composerTarget = null;
  let revertTarget = null; // the §5.5 revertTarget; null until the version UI (T6b) sets it
  let submitted = false;
  let lastHeadInfo = null;
  let imageDetach = null; // image-adapter teardown (image views only)
  let pollTimer = null; // the auto-advance poll interval (stopped on accept, §6.4)
  let deferredHead = null; // a new head awaiting in-progress work to clear (preserve-unsent)
  let screenshotToggle = true; // gated screenshot on/off (chrome.storage.local, default on)
  let widthPreset = 'comfortable'; // #3 reading-width preset (chrome.storage.local)
  // §K: the active click-to-select lock — { levels, idx, box, bubble, label, ... } or null.
  // A click locks the innermost semantic stop under the pointer; the bubble's up/down arrows
  // traverse `levels` and the `box` overlay outlines the current one. This replaces the old
  // (unreachable) hover→floating-icon block affordance + its hoverEl/hoverEls/hoverMode state.
  let selLock = null;
  let viewKind = null; // cached detectView().kind (the view can't change without a reload)
  // §B: every saved comment becomes an entry { item, pin, listItem, anchorEl }. The pins ride
  // the canvas; the listItems fill the sidebar; both share one bidirectional-sync registry.
  const comments = [];
  let sidebarOpen = false; // §B sidebar open/closed — a pin-click's behavior depends on this
  let pinRaf = null; // rAF token throttling pin repositioning on scroll/resize/reflow
  const SIDEBAR_W = 320; // §B sidebar width (px); the shrink/overlay layouts key off this

  // #3 reading-width presets: a small cycle the width control steps through. Applied as a
  // max-width on the Markdown reading column (inert on code/image/wide-table — full-bleed).
  const WIDTH_PRESETS = ['comfortable', 'wide', 'full'];
  const WIDTH_LABELS = { comfortable: 'Comfortable', wide: 'Wide', full: 'Full' };

  // Inline SVG icons (#4) — Font Awesome Pro 7 Sharp Light, fill=currentColor so the active
  // state is just a CSS color. Never an <img> (prefer-inline-svg-icons).
  const ICON_CAMERA =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M224 96L416 96L448 160L576 160L576 544L64 544L64 160L192 160L224 96zM448 192L428.2 192C424.7 185 414 163.6 396.2 128L243.8 128C226 163.6 215.3 185 211.8 192L96 192L96 512L544 512L544 192L448 192zM320 240C381.9 240 432 290.1 432 352C432 413.9 381.9 464 320 464C258.1 464 208 413.9 208 352C208 290.1 258.1 240 320 240zM400 352C400 307.8 364.2 272 320 272C275.8 272 240 307.8 240 352C240 396.2 275.8 432 320 432C364.2 432 400 396.2 400 352z"/></svg>';
  const ICON_WIDTH =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M64 176L64 480L32 480L32 160L64 160L64 176zM608 176L608 480L576 480L576 160L608 160L608 176zM518.6 320L507.3 331.3L427.3 411.3L416 422.6L393.4 400L404.7 388.7L457.4 336L182.7 336L235.4 388.7L246.7 400L224.1 422.6L212.8 411.3L132.8 331.3L121.5 320L132.8 308.7L212.8 228.7L224.1 217.4L246.7 240C246.1 240.6 224.7 262 182.7 304L457.4 304L404.7 251.3L393.4 240L416 217.4L427.3 228.7L507.3 308.7L518.6 320z"/></svg>';
  // §A: the ONE comment affordance — a speech-bubble icon (comment.svg Sharp Light). It is
  // the ONLY way to start a comment (hover a block, or select text, then click THIS icon).
  // No "+", no "Highlight to add a comment" / "BLOCK" / "SECTION" text labels anywhere.
  const ICON_COMMENT =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M152.6 443.4C150.9 447.6 140.1 474.1 120.2 522.9L206.1 493.4L217.6 489.5L228.8 494.1C256.6 505.6 287.4 512.1 320 512.1C445.7 512.1 544 417.1 544 304.1C544 191.1 445.7 96 320 96C194.3 96 96 191 96 304C96 350.6 112.5 393.8 140.7 428.7L152.6 443.4zM104.2 562.2L64 576C71.4 557.9 88.7 515.4 115.8 448.8C83.3 408.6 64 358.4 64 304C64 171.5 178.6 64 320 64C461.4 64 576 171.5 576 304C576 436.5 461.4 544 320 544C283.2 544 248.1 536.7 216.5 523.6L104.2 562.2z"/></svg>';
  // §B (pin pass v2): the SOLID/filled speech bubble — the RESTING comment-pin glyph. Same
  // silhouette + 640 grid as ICON_COMMENT (this is that bubble's outer contour, filled), so the
  // resting solid glyph and the hover white-OUTLINED glyph register as the same shape. The
  // outlined ICON_COMMENT is the hover/background glyph; this solid one is rest.
  const ICON_COMMENT_SOLID =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M104.2 562.2L64 576C71.4 557.9 88.7 515.4 115.8 448.8C83.3 408.6 64 358.4 64 304C64 171.5 178.6 64 320 64C461.4 64 576 171.5 576 304C576 436.5 461.4 544 320 544C283.2 544 248.1 536.7 216.5 523.6L104.2 562.2z"/></svg>';
  // §F: the doc-level add-comment affordance mounted at the TOP OF THE SIDEBAR (comment-plus
  // Sharp Light) — creates a whole-document comment ({kind:'source', lineRange:[1, lastLine]}).
  const ICON_COMMENT_PLUS =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M145.5 460.9L152.6 443.4L140.7 428.7C112.5 393.8 96 350.6 96 304C96 191 194.3 96 320 96C445.7 96 544 191 544 304C544 417 445.7 512 320 512C287.4 512 256.6 505.6 228.8 494L217.6 489.4L206.1 493.3L120.2 522.8L145.4 460.8zM64 576C78.8 570.9 129.6 553.4 216.5 523.6C248.1 536.7 283.2 544 320 544C461.4 544 576 436.5 576 304C576 171.5 461.4 64 320 64C178.6 64 64 171.5 64 304C64 358.4 83.3 408.6 115.8 448.8C88.7 515.5 71.4 557.9 64 576zM304 392L336 392L336 320L408 320L408 288L336 288L336 216L304 216L304 288L232 288L232 320L304 320L304 392z"/></svg>';
  // §B pin glyph for EDITS — a pen (filled) so an edit pin reads distinctly from a comment
  // pin's speech bubble. fill=currentColor so the resting pin recolors to the edit/yellow type
  // color and the hover/flash marker recolors to white. (NEW for the pin pass — no edit-icon
  // constant existed; FA-style pen on its own 512 grid, like ICON_PAPERCLIP.)
  const ICON_PEN =
    '<svg class="annotate-icon" viewBox="0 0 512 512" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M362.7 19.3L314.3 67.7 444.3 197.7 492.7 149.3c25-25 25-65.5 0-90.5L453.3 19.3c-25-25-65.5-25-90.5 0zm-71 71L58.6 323.5c-10.4 10.4-18 23.3-22.2 37.4L1 481.2C-1.5 489.7 .8 498.8 7 505s15.3 8.5 23.7 6.1l120.3-35.4c14.1-4.2 27-11.8 37.4-22.2L421.7 220.3 291.7 90.3z"/></svg>';
  // §F: the reading-width control is now ICON-ONLY and cycles ALL THREE presets, showing the
  // icon for the CURRENT preset: comfortable(compact) = compress, wide = expand, full = the
  // arrows-left-right-to-line (<->) — all Sharp Light inline SVG.
  const ICON_COMPRESS =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M256 112L256 96L224 96L224 224L96 224L96 256L256 256L256 112zM112 384L96 384L96 416L224 416L224 544L256 544L256 384L112 384zM416 112L416 96L384 96L384 256L544 256L544 224L416 224L416 112zM400 384L384 384L384 544L416 544L416 416L544 416L544 384L400 384z"/></svg>';
  const ICON_EXPAND =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M240 96L256 96L256 128L128 128L128 256L96 256L96 96L240 96zM96 400L96 384L128 384L128 512L256 512L256 544L96 544L96 400zM528 96L544 96L544 256L512 256L512 128L384 128L384 96L528 96zM512 400L512 384L544 384L544 544L384 544L384 512L512 512L512 400z"/></svg>';
  // current-preset -> icon (the button shows the icon for the state it is IN).
  const WIDTH_ICONS = { comfortable: ICON_COMPRESS, wide: ICON_EXPAND, full: ICON_WIDTH };
  // §K traversal arrows on the lock bubble (chevron up/down, Sharp Light): up = broaden to the
  // parent stop, down = narrow back toward the clicked innermost.
  const ICON_CARET_UP =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M320 246.6L308.7 257.9L116.7 449.9L139.3 472.6L320 292L500.7 472.6L523.3 449.9L331.3 257.9L320 246.6z"/></svg>';
  const ICON_CARET_DOWN =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M320 393.4L331.3 382.1L523.3 190.1L500.7 167.4L320 348L139.3 167.4L116.7 190.1L308.7 382.1L320 393.4z"/></svg>';
  // §L semantic chrome-action icons (Sharp Light, fill=currentColor so the active FILL just
  // recolors the glyph): a check for Accept (green), a paper-plane for Send (orange) and a
  // trash can for the new Clear (red). Authored on the same 640 grid as the icons above.
  const ICON_CHECK =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M494 176L256 420L144 306L122 328L256 458L517 197L494 176z"/></svg>';
  const ICON_PAPER_PLANE =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M544 96L96 304L288 352L336 544L544 96z"/></svg>';
  const ICON_TRASH =
    '<svg class="annotate-icon" viewBox="0 0 640 640" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M268 160L268 128L372 128L372 160L356 160L356 144L284 144L284 160L268 160zM120 162L520 162L520 192L120 192L120 162zM170 212L196 540L444 540L470 212L170 212z"/></svg>';
  // §I: the user-image ATTACH affordance — a paperclip (distinct from ICON_CAMERA, which is the
  // tool's auto-capture screenshot toggle). fill=currentColor so the icon-only button recolors.
  const ICON_PAPERCLIP =
    '<svg class="annotate-icon" viewBox="0 0 448 512" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M364.2 83.8c-24.4-24.4-64-24.4-88.4 0l-184 184c-42.1 42.1-42.1 110.3 0 152.4s110.3 42.1 152.4 0l152-152c10.9-10.9 28.7-10.9 39.6 0s10.9 28.7 0 39.6l-152 152c-64 64-167.6 64-231.6 0s-64-167.6 0-231.6l184-184c46.3-46.3 121.3-46.3 167.6 0s46.3 121.3 0 167.6l-176 176c-28.6 28.6-75 28.6-103.6 0s-28.6-75 0-103.6l144-144c10.9-10.9 28.7-10.9 39.6 0s10.9 28.7 0 39.6l-144 144c-6.7 6.7-6.7 17.7 0 24.4s17.7 6.7 24.4 0l176-176c24.4-24.4 24.4-64 0-88.4z"/></svg>';

  // ---------------------------------------------------------------------------
  // small DOM helpers
  // ---------------------------------------------------------------------------

  function el(tag, attrs, children) {
    const node = doc.createElement(tag);
    if (attrs) {
      for (const k of Object.keys(attrs)) {
        if (k === 'class') node.className = attrs[k];
        else if (k === 'text') node.textContent = attrs[k];
        else if (k.slice(0, 2) === 'on' && typeof attrs[k] === 'function') {
          node.addEventListener(k.slice(2), attrs[k]);
        } else if (attrs[k] != null) {
          node.setAttribute(k, attrs[k]);
        }
      }
    }
    (children || []).forEach((c) => node.appendChild(typeof c === 'string' ? doc.createTextNode(c) : c));
    return node;
  }

  function inUi(node) {
    return !!(node && typeof node.closest === 'function' && node.closest('.annotate-ui'));
  }

  // Wrap a trusted constant inline-SVG string (#4) in a span. The markup is a build-time
  // constant (never page/user input), so innerHTML here is safe.
  function svgIcon(markup) {
    const span = doc.createElement('span');
    span.className = 'annotate-icon-wrap';
    span.innerHTML = markup;
    return span;
  }

  function setStatus(msg) {
    const s = doc.querySelector('.annotate-status');
    if (s) s.textContent = msg;
  }

  // §I: read a picked File as a data URL (used both for the inline preview thumbnail and to
  // derive the base64 bytes the attach upload sends). Browser-only (FileReader); content.js
  // is not unit-tested in Node (the server/CLI legs are).
  function readFileAsDataURL(file) {
    return new Promise(function (resolve, reject) {
      const fr = new FileReader();
      fr.onload = function () { resolve(fr.result); };
      fr.onerror = function () { reject(fr.error || new Error('read-failed')); };
      fr.readAsDataURL(file);
    });
  }

  // §L hover labels: every chrome button surfaces its name as a styled tooltip on hover
  // (the CSS `.annotate-btn[data-label]:hover::after` reads data-label). Set data-label AND
  // aria-label together so the visible tooltip and the accessible name stay in sync. We do
  // NOT set `title` on these (a native tooltip would double up with the styled one).
  function setBtnLabel(btn, text) {
    if (!btn) return;
    btn.setAttribute('data-label', text);
    btn.setAttribute('aria-label', text);
  }

  // NOTE: named chromeBar (NOT chrome) on purpose — a local `chrome()` would hoist and
  // SHADOW the global `chrome` extension API across this whole module, silently breaking
  // chromeApi.runtime (screenshot capture) and chromeApi.storage (toggle persistence).
  function chromeBar() {
    return doc.getElementById('annotate-chrome');
  }

  // ---------------------------------------------------------------------------
  // view detection (format badge + which adapter)
  // ---------------------------------------------------------------------------

  function detectView() {
    if (doc.querySelector('pre.annotate-code')) return { kind: 'code', badge: 'CODE' };
    if (doc.querySelector('.annotate-markdown')) return { kind: 'markdown', badge: 'MD' };
    if (doc.querySelector('.annotate-struct')) return { kind: 'struct', badge: 'DATA' };
    if (doc.querySelector('.annotate-csv')) return { kind: 'csv', badge: 'CSV' };
    if (doc.querySelector('.annotate-image')) return { kind: 'image', badge: 'IMG' };
    return { kind: 'frontend', badge: 'WEB' };
  }

  // Click target -> §5.2 anchor, using the code/line adapter inside a code view, else the
  // generic DOM adapter (which also reads data-key-path / data-cell). null when nothing
  // anchorable is under the click.
  function anchorFor(target) {
    if (A.code && A.code.isCodeView(target)) {
      return A.code.anchorFromCodeNode(target);
    }
    return A.dom ? A.dom.anchorFromElement(target) : null;
  }

  function anchorLabel(anchor) {
    if (!anchor) return '';
    if (anchor.lineRange) return 'Lines ' + anchor.lineRange[0] + '–' + anchor.lineRange[1];
    if (anchor.line != null) return 'Line ' + anchor.line;
    if (anchor.keyPath != null) return anchor.keyPath === '' ? '(root)' : anchor.keyPath;
    if (anchor.cell != null) return 'Cell ' + anchor.cell;
    if (anchor.point) return 'Point';
    if (anchor.box) return 'Region';
    return anchor.kind || '';
  }

  // ---------------------------------------------------------------------------
  // §K anchoring granularity — click-to-select-innermost + DOM-traversal stop chain.
  // A click locks the innermost SEMANTIC stop under the pointer; the lock bubble's up/down
  // arrows broaden/narrow along the stop chain, re-deriving a line-range anchor at each:
  //   prose:  line/block ([data-src-line]) -> list (<ul>/<ol>) -> section
  //           (<section class="annotate-section" data-src-line-range>) -> document
  //   table:  cell ([data-cell]) -> row (<tr>) -> table (<table>) -> section -> document
  // Intermediate multi-line stops the renderer doesn't stamp a range on (<ul>, <tr>, <table>)
  // derive it at traversal time as min..max of their descendant [data-src-line] (falling back
  // to the nearest enclosing section). DECIDED: rendered-doc DOM only — live-frontend
  // (data-as-frontend / keypath) traversal is deferred, so the lock is gated off there.
  // ---------------------------------------------------------------------------

  // Largest stamped source line in the rendered view — the document's last line for the
  // trailing-section / whole-document range (the renderer stamps a block's FIRST line, so
  // this is the last block's start line; precise enough for v1, noted in the report).
  function maxSourceLine() {
    let max = 0;
    const nodes = doc.querySelectorAll('.annotate-render [data-src-line]');
    nodes.forEach(function (n) {
      const v = parseInt(n.getAttribute('data-src-line'), 10);
      if (Number.isFinite(v) && v > max) max = v;
    });
    return max;
  }

  // §C / v2.7: the range anchor derives DIRECTLY from a render.js wrapper's data-src-line-range
  // ("N-M") — a markdown <section class="annotate-section"> OR a code-view
  // <div class="annotate-code-block">; both carry the same attribute and are treated
  // identically. null if the element carries no well-formed data-src-line-range.
  function sectionAnchorFromWrapper(section) {
    if (!section) return null;
    const m = /^(\d+)-(\d+)$/.exec(section.getAttribute('data-src-line-range') || '');
    if (!m) return null;
    return { kind: 'source', lineRange: [parseInt(m[1], 10), parseInt(m[2], 10)] };
  }

  // ---- §K stop chain --------------------------------------------------------

  // Grouping rows that exist only to bracket a table's head/body — they carry a data-src-line
  // but aren't a level a human means to comment on, so the chain skips straight from a <tr> to
  // its <table> (matching the spec's cell -> row -> table, not ...-> tbody -> table).
  const SKIP_STOP_TAGS = { THEAD: 1, TBODY: 1, TFOOT: 1, COLGROUP: 1 };

  // Is `node` a SEMANTIC traversal stop (a level the up/down arrows pause on)?
  function isTraversalStop(node) {
    if (!node || node.nodeType !== 1 || typeof node.matches !== 'function') return false;
    if (SKIP_STOP_TAGS[node.tagName]) return false;
    if (node.hasAttribute('data-src-line-range')) return true; // range wrapper: md <section> OR code-block <div>
    if (node.hasAttribute('data-cell')) return true; // CSV / table cell
    if (node.hasAttribute('data-key-path')) return true; // rendered struct (JSON/YAML/TOML) node — leaf or container
    if (node.hasAttribute('data-src-line')) return true; // md line / block / li / ul / tr / table
    const tag = node.tagName;
    return tag === 'UL' || tag === 'OL' || tag === 'TR' || tag === 'TABLE'; // list / row / table w/o a stamped line
  }

  // Inclusive [min,max] of `node`'s OWN + descendant data-src-line values, or null if none are
  // present. This is the traversal-time range derivation for the intermediate stops the
  // renderer doesn't stamp a range on (<ul>, <tr>, <table> => the whole list / row / table).
  function lineRangeFor(node) {
    let min = Infinity;
    let max = -Infinity;
    const consider = function (raw) {
      const v = parseInt(raw, 10);
      if (Number.isFinite(v)) {
        if (v < min) min = v;
        if (v > max) max = v;
      }
    };
    if (node.hasAttribute && node.hasAttribute('data-src-line')) consider(node.getAttribute('data-src-line'));
    if (typeof node.querySelectorAll === 'function') {
      node.querySelectorAll('[data-src-line]').forEach(function (d) { consider(d.getAttribute('data-src-line')); });
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
    return [min, max];
  }

  // Re-derive the §5.2 anchor for one traversal stop. Sections read their stamped range; CSV
  // cells their address; everything else a line-range from descendant lines (collapsing to a
  // single {line} when start === end, so a leaf block stays a line anchor). Falls back to the
  // nearest enclosing section when an element carries no derivable line; null if even that fails.
  function anchorForLevel(node) {
    // A range wrapper (md <section> or code-block <div>) reports its DECLARED
    // data-src-line-range LABEL, not a descendant-derived extent.
    if (node.hasAttribute && node.hasAttribute('data-src-line-range')) {
      return sectionAnchorFromWrapper(node);
    }
    if (node.hasAttribute && node.hasAttribute('data-cell')) {
      return { kind: 'source', cell: node.getAttribute('data-cell') };
    }
    // Rendered struct (JSON/YAML/TOML): nested data-key-path containers + leaves climb
    // leaf -> object/array -> root ("") — the keyPath model has no line range. (Checked before
    // the line-range path; only the LIVE --as-frontend keypath view is deferred, gated in onDocClick.)
    if (node.hasAttribute && node.hasAttribute('data-key-path')) {
      return { kind: 'source', keyPath: node.getAttribute('data-key-path') };
    }
    const range = lineRangeFor(node);
    if (range) {
      return range[0] === range[1]
        ? { kind: 'source', line: range[0] }
        : { kind: 'source', lineRange: range };
    }
    const section = typeof node.closest === 'function' ? node.closest('[data-src-line-range]') : null;
    if (section) return sectionAnchorFromWrapper(section);
    return null;
  }

  // A stable key for an anchor, so consecutive stops that resolve to the SAME anchor (e.g. a
  // single-item <ul> and its lone <li>, both [10,10]) collapse to one level on the chain.
  function anchorKey(a) {
    if (!a) return '';
    if (a.lineRange) return 'r' + a.lineRange[0] + '-' + a.lineRange[1];
    if (a.line != null) return 'l' + a.line;
    if (a.cell != null) return 'c' + a.cell;
    if (a.keyPath != null) return 'k' + a.keyPath;
    return a.kind || '';
  }

  // Build the ordered traversal chain for a clicked target: innermost stop first, each broader
  // stop next, the whole document last. Each level = { el, anchor }. Stops with no derivable
  // anchor (e.g. a CSV <tr>/<table> with no source line and no section) — and stops whose
  // anchor duplicates the one just below — are dropped, so the arrows never land on a dead or
  // no-op level. Empty when the click isn't inside an anchorable rendered region.
  function traversalLevels(target) {
    const render = doc.querySelector('.annotate-render');
    if (!render) return [];
    const levels = [];
    let lastKey = null;
    let node = target && target.nodeType === 1 ? target : (target ? target.parentElement : null);
    while (node && node !== doc.body && node !== doc.documentElement) {
      if (render.contains(node) && isTraversalStop(node)) {
        const anchor = anchorForLevel(node);
        const key = anchorKey(anchor);
        if (anchor && key !== lastKey) {
          levels.push({ el: node, anchor: anchor });
          lastKey = key;
        }
      }
      if (node === render) break;
      node = node.parentElement;
    }
    // Whole-document stop — the same anchor as the §F "Comment on doc" action. Skipped for views
    // with no source lines (e.g. CSV), where [1, 0] would be a meaningless range, and folded
    // away when it duplicates a section that already spans the whole document.
    const last = maxSourceLine();
    if (last > 0 && anchorKey({ lineRange: [1, last] }) !== lastKey) {
      levels.push({ el: render, anchor: { kind: 'source', lineRange: [1, last] }, whole: true });
    }
    return levels;
  }

  // ---------------------------------------------------------------------------
  // the REQUIRED top "Annotate chrome" bar (§6.4 + REFS.md)
  // ---------------------------------------------------------------------------

  function buildChrome() {
    const view = detectView();
    viewKind = view.kind; // cache for the hover hot path
    const widthApplies = A.config.widthApplies(view.kind); // #3: prose reading column + code wrap column

    // §F #3 + #4: the reading-width control is ICON-ONLY now — no "Wide" text label. It cycles
    // ALL THREE presets (compact -> wide -> full -> compact) and shows the icon for the current
    // state (compress / expand / arrows-left-right-to-line). On MARKDOWN it sets the prose
    // reading column; on CODE (dogfood fix) it sets the soft-wrap column. Inert (full-bleed) on
    // image/struct/csv.
    // §L: the width control is a non-semantic interactive button — its hover-outline + active
    // FILL use the unified blue accent. Where the width feature applies (markdown/code) it is a
    // PERSISTENTLY-engaged control (§2: "its active mode reads as a FILL; KEEP that selected-
    // state indication") so it carries .annotate-active (blue fill) and the icon shows WHICH
    // preset; on the other views it is inert (full-bleed), greyed and not filled.
    const widthBtn = el('button', {
      class: 'annotate-btn annotate-width annotate-icon-btn' +
        (widthApplies ? ' annotate-active' : ' annotate-btn-inert'),
      type: 'button',
      'data-label': widthApplies ? widthTitle() : 'Reading width (full-bleed for this view)',
      'aria-label': widthApplies ? widthTitle() : 'Reading width (full-bleed for this view)',
      onclick: onCycleWidth,
    }, [svgIcon(WIDTH_ICONS[widthPreset])]);

    // §F #4 + §L: screenshot toggle = the CAMERA ICON ONLY (no "Screenshot" word); the active
    // (on) state is the unified-blue FILL (reflectShotToggle), with a hover label.
    const shotBtn = el('button', {
      class: 'annotate-btn annotate-shot-toggle annotate-icon-btn',
      type: 'button',
      'data-label': 'Attach a viewport screenshot on send (visual views only)',
      'aria-label': 'Attach a viewport screenshot on send (visual views only)',
      onclick: onToggleShot,
    }, [svgIcon(ICON_CAMERA)]);

    // §F: the SINGLE top-bar comment-bubble — it REPLACES the old "Comment on doc" text button
    // and the interim §B `.annotate-sidebar-toggle`, consolidated into one icon whose job is to
    // open/close the §B comment sidebar. The whole-document comment action now lives at the top
    // of that sidebar (mountSidebarDocAdd), not in the top bar.
    const sidebarBtn = el('button', {
      class: 'annotate-btn annotate-sidebar-toggle annotate-icon-btn',
      type: 'button',
      'data-label': 'Toggle the comment sidebar',
      'aria-label': 'Toggle the comment sidebar',
      'aria-pressed': 'false',
      onclick: function () { toggleSidebar(); },
    }, [svgIcon(ICON_COMMENT)]);

    // §L: the NEW Clear control — RED trash icon, ICON-ONLY, shown only while UNSENT drafts are
    // staged (reflectActionEmphasis toggles .annotate-clear-hidden). Clears every staged draft
    // after a native confirm (same mechanism as the §G Accept guard). Starts hidden.
    const clearBtn = el('button', {
      class: 'annotate-btn annotate-clear annotate-icon-btn annotate-clear-hidden',
      type: 'button',
      'data-label': 'Clear staged comments',
      'aria-label': 'Clear staged comments',
      onclick: function () { clearDrafts(); },
    }, [svgIcon(ICON_TRASH)]);

    // §L: Accept + Send are now ICON + hover-label (green check / orange paper-plane) like the
    // rest of the chrome; reflectActionEmphasis drives their semantic lit/muted state. The
    // class names + click handlers + result attributes are UNCHANGED (gate contract).
    const actions = [
      // Reserved slot for the deferred revert/version dropdown (PRD §6 — seam only in v1).
      el('div', { class: 'annotate-version-slot', title: 'version history (coming soon)', text: 'v ▾' }),
      sidebarBtn,
      widthBtn,
      shotBtn,
      clearBtn,
      el('button', {
        class: 'annotate-btn annotate-send annotate-icon-btn',
        type: 'button',
        'data-label': 'Send feedback (0)',
        'aria-label': 'Send feedback (0)',
        onclick: function () { send(); },
      }, [svgIcon(ICON_PAPER_PLANE)]),
      el('button', {
        class: 'annotate-btn annotate-accept annotate-icon-btn',
        type: 'button',
        'data-label': 'Accept',
        'aria-label': 'Accept',
        onclick: function () { accept(); },
      }, [svgIcon(ICON_CHECK)]),
    ].filter(Boolean);

    const bar = el('div', { id: 'annotate-chrome', class: 'annotate-ui annotate-chrome' }, [
      el('div', { class: 'annotate-brand' }, [
        el('span', { class: 'annotate-logo', text: 'annotate' }),
        el('span', { class: 'annotate-badge', text: view.badge }),
        el('span', { class: 'annotate-title', text: ctx.artifact }),
      ]),
      el('div', { class: 'annotate-actions' }, actions),
    ]);
    doc.body.appendChild(bar);
    doc.body.classList.add('annotate-has-chrome');
    // #7: theme the rendered content (Markdown/code/struct/csv/image) but NEVER a
    // --as-frontend page (detectView -> 'frontend'), whose own CSS we must not touch.
    if (view.kind !== 'frontend') doc.body.classList.add('annotate-themed');

    // #9: status line is now a fixed bottom FOOTER (a body child, not under the top chrome).
    doc.body.appendChild(
      el('div', { class: 'annotate-ui annotate-status', text: 'Ready — click to select a block (↑/↓ to broaden/narrow), or select text to annotate' })
    );

    // §B: the comment SIDEBAR (supersedes the old right-margin rail + the accordion idea).
    // Closed by default; the top-bar comment-bubble (and, later, §F) toggles it.
    buildSidebar();

    applyWidth(); // reflect the current preset on <body> + the control label
    // Stamp the screenshot gating SYNCHRONOUSLY here, while the view is first set up and
    // BEFORE init() sets data-annotate-ready="1". reflectShotToggle() derives data-screenshot
    // /data-screenshot-active from the current view + the default toggle (screenshotToggle is
    // true at module load), so an image view reads data-screenshot-active="1" the instant the
    // chrome exists. loadShotToggle() then REFINES this asynchronously from chrome.storage —
    // but that async storage callback must NOT be what first publishes the attribute, or a
    // reader gated on data-annotate-ready (e.g. the image-gate) can win the race and read null.
    reflectShotToggle();
    reflectActionEmphasis(); // §G.1: initial Accept/Send emphasis (Accept primary while empty)
  }

  // The send count now lives in the Send button's hover label (it is icon-only, §L) — never
  // textContent (that would wipe the inline SVG, the same trap noted for the camera toggle).
  // reflectActionEmphasis() writes the label + drives the semantic lit/muted scheme.
  function updateSendCount() {
    reflectActionEmphasis();
  }

  // §G.1 + §L semantic emphasis. Exactly ONE of Accept / Send is "lit" (carries its semantic
  // FILL) at a time; the other is muted. Keyed off UNSENT drafts (`!submitted && drafts>0`):
  //   - nothing unsent  -> Accept is GREEN-lit (primary, approve-as-is); Send is muted.
  //   - unsent staged    -> Send is ORANGE-lit (primary, submit them); Accept is muted AND
  //                         confirm-guarded (accept() still prompts) so a stray click can't
  //                         finalize the round with 0 feedback; the RED Clear button appears.
  // Once sent, the drafts are no longer at risk -> Accept returns to green-lit (a normal
  // Send -> Accept flow never traps). This supersedes the old blue-primary + dashed-guard look.
  function reflectActionEmphasis() {
    const acc = doc.querySelector('.annotate-accept');
    const snd = doc.querySelector('.annotate-send');
    const clr = doc.querySelector('.annotate-clear');
    const n = drafts.length;
    const guard = !submitted && n > 0;
    if (acc) {
      acc.classList.toggle('annotate-lit-accept', !guard); // green fill when primary
      acc.classList.toggle('annotate-accept-guarded', guard); // muted + confirm-guarded
      setBtnLabel(acc, guard
        ? 'Accept — discards ' + n + ' unsent comment' + (n === 1 ? '' : 's')
        : 'Accept this round as-is');
    }
    if (snd) {
      snd.classList.toggle('annotate-lit-send', guard); // orange fill when primary
      snd.classList.toggle('annotate-muted', !guard);
      setBtnLabel(snd, 'Send feedback (' + n + ')');
    }
    if (clr) {
      clr.classList.toggle('annotate-clear-hidden', !guard); // visible only with unsent drafts
      setBtnLabel(clr, 'Clear ' + n + ' staged comment' + (n === 1 ? '' : 's'));
    }
  }

  // §L Clear: drop ALL staged (unsent) drafts after a native confirm (the SAME mechanism the
  // §G Accept guard uses, for consistency). Removes each draft's on-canvas pin + sidebar row
  // (+ any spatial image marker), tears down a transient composer/lock, then re-renders the
  // count/emphasis + sidebar-empty state. Nothing here is gate-driven (the gates never click
  // Clear), so the confirm() is gate-safe.
  function clearDrafts() {
    const n = drafts.length;
    if (!n) return;
    const noun = 'staged comment' + (n === 1 ? '' : 's');
    const confirmFn = typeof root.confirm === 'function' ? root.confirm.bind(root) : null;
    if (confirmFn && !confirmFn('Clear ' + n + ' ' + noun + "? This can't be undone.")) {
      setStatus(n + ' ' + noun + ' kept.');
      return;
    }
    drafts.length = 0;
    comments.forEach(function (entry) {
      unwrapEntryMarks(entry); // v2.5 a4 — restore the wrapped text before dropping the entry
      if (entry.pin && entry.pin.parentNode) entry.pin.remove();
      if (entry.listItem && entry.listItem.parentNode) entry.listItem.remove();
    });
    comments.length = 0;
    // v2.5 a3 — clear any region outline left from a pin that was hovered when Clear fired.
    const hovered = doc.querySelectorAll('.annotate-render [data-annotate-hover]');
    for (let i = 0; i < hovered.length; i++) hovered[i].removeAttribute('data-annotate-hover');
    // Spatial anchors also leave a persistent region marker over the image (§B addDraft).
    const markers = doc.querySelectorAll('.annotate-marker');
    for (let i = 0; i < markers.length; i++) markers[i].remove();
    closeComposer();
    clearLock();
    updateSidebarEmpty();
    updateSendCount();
    setStatus('Cleared ' + n + ' ' + noun + '.');
  }

  // §F: "Copy" is GONE — native browser selection (⌘C / right-click → Copy) works now that
  // click-anywhere-to-comment is removed (§A) and nothing hijacks the selection / context menu.

  // ---------------------------------------------------------------------------
  // §F #3 reading-width preset — an ICON-ONLY control cycling all three max-width presets
  // over the Markdown reading column AND (dogfood fix) the CODE soft-wrap column; persisted
  // per-user (chrome.storage.local); inert on image/struct/csv (full-bleed). The button shows
  // the icon for the CURRENT preset.
  // ---------------------------------------------------------------------------

  function widthTitle() {
    return 'Reading width: ' + WIDTH_LABELS[widthPreset] + ' — click to cycle presets';
  }

  // Swap the width control's icon to the current preset's icon (no text label, §F). Replaces
  // the icon span's children in place so the button element + its click handler are preserved.
  function refreshWidthIcon() {
    const btn = doc.querySelector('.annotate-width');
    if (!btn) return;
    const wrap = btn.querySelector('.annotate-icon-wrap');
    if (wrap) wrap.innerHTML = WIDTH_ICONS[widthPreset];
    if (A.config.widthApplies(detectView().kind)) setBtnLabel(btn, widthTitle()); // §L hover label
  }

  function applyWidth() {
    doc.body.setAttribute('data-annotate-width', widthPreset);
    refreshWidthIcon();
    schedulePinReposition(); // §B: a width change reflows the column -> pins must re-track
  }

  function persistWidth() {
    if (chromeApi && chromeApi.storage && chromeApi.storage.local) {
      try {
        chromeApi.storage.local.set({ widthPreset: widthPreset });
      } catch (e) {
        /* best-effort persistence */
      }
    }
  }

  function loadWidth() {
    if (!chromeApi || !chromeApi.storage || !chromeApi.storage.local) {
      applyWidth();
      return;
    }
    try {
      chromeApi.storage.local.get({ widthPreset: 'comfortable' }, function (items) {
        if (!(chromeApi.runtime && chromeApi.runtime.lastError) && items && WIDTH_PRESETS.indexOf(items.widthPreset) >= 0) {
          widthPreset = items.widthPreset;
        }
        applyWidth();
      });
    } catch (e) {
      applyWidth();
    }
  }

  function onCycleWidth() {
    if (!A.config.widthApplies(detectView().kind)) {
      setStatus('Reading width applies to Markdown / code views');
      return;
    }
    const i = WIDTH_PRESETS.indexOf(widthPreset);
    widthPreset = WIDTH_PRESETS[(i + 1) % WIDTH_PRESETS.length];
    persistWidth();
    applyWidth();
    setStatus('Reading width: ' + WIDTH_LABELS[widthPreset]);
  }

  // #2 document affordance: open the composer on a whole-document line range.
  function onDocComment() {
    const view = detectView();
    if (view.kind !== 'markdown' && view.kind !== 'code') {
      setStatus('Whole-document comments apply to Markdown / code views');
      return;
    }
    const last = maxSourceLine() || 1;
    clearLock();
    openComposer({ anchor: { kind: 'source', lineRange: [1, last] }, element: null, selectedText: '' });
  }

  // ---------------------------------------------------------------------------
  // gated viewport screenshot (§6.4) — content.js -> background.js captureVisibleTab
  // ---------------------------------------------------------------------------

  // Reflect the toggle state onto the button + a DOM-readable attr (the gate reads it),
  // and note for the current view whether a capture would actually fire (gating is view-
  // dependent, so the toggle is shown "inert" on a non-visual view).
  function reflectShotToggle() {
    const btn = doc.querySelector('.annotate-shot-toggle');
    const bar = chromeBar();
    const view = detectView();
    // A screenshot only makes sense on a VISUAL view (image/markdown/frontend). On a source
    // view (code/struct/csv) a viewport screenshot is pointless, so the camera is DISABLED.
    const visual = !!(A.config.VISUAL_VIEWS && A.config.VISUAL_VIEWS.has(view.kind));
    const willCapture = A.config.shouldCaptureScreenshot(view.kind, screenshotToggle);
    if (btn) {
      // #4 + §L: camera icon stays; on a visual view "on" = the unified-blue FILL
      // (.annotate-active). DOGFOOD FIX C: the active-blue FILL must NEVER appear on a non-visual
      // view — the old code applied .annotate-active from screenshotToggle alone (default ON), so
      // the camera read as the most prominent/enabled button in the code view even though it does
      // nothing. Gate .annotate-active on `visual`; on a non-visual view show the shared
      // disabled affordance (.annotate-btn-inert = greyed + cursor:default, same as the inert
      // width control) + aria-disabled, and onToggleShot() no-ops. Toggle classes, never
      // textContent (that would wipe the inline SVG).
      btn.classList.toggle('annotate-active', visual && !!screenshotToggle);
      btn.classList.toggle('annotate-btn-inert', !visual);
      btn.classList.toggle('annotate-shot-inert', !visual);
      btn.setAttribute('aria-disabled', visual ? 'false' : 'true');
      btn.setAttribute('aria-pressed', screenshotToggle ? 'true' : 'false');
      setBtnLabel(btn, visual
        ? 'Screenshot on send: ' + (screenshotToggle ? 'on' : 'off')
        : 'Screenshot not available for this view');
    }
    if (bar) {
      bar.setAttribute('data-screenshot', screenshotToggle ? 'on' : 'off');
      bar.setAttribute('data-screenshot-active', willCapture ? '1' : '0');
    }
  }

  function loadShotToggle() {
    if (!chromeApi || !chromeApi.storage || !chromeApi.storage.local) {
      reflectShotToggle();
      return;
    }
    try {
      chromeApi.storage.local.get({ screenshotEnabled: true }, function (items) {
        if (!(chromeApi.runtime && chromeApi.runtime.lastError) && items) {
          screenshotToggle = items.screenshotEnabled !== false;
        }
        reflectShotToggle();
      });
    } catch (e) {
      reflectShotToggle();
    }
  }

  function onToggleShot() {
    // FIX C: the camera is DISABLED on a non-visual (source) view — a click does nothing
    // (no toggle, no persisted change), mirroring the inert width control's onCycleWidth guard.
    if (!(A.config.VISUAL_VIEWS && A.config.VISUAL_VIEWS.has(detectView().kind))) {
      setStatus('Screenshot capture applies to visual views only');
      return;
    }
    screenshotToggle = !screenshotToggle;
    if (chromeApi && chromeApi.storage && chromeApi.storage.local) {
      try {
        chromeApi.storage.local.set({ screenshotEnabled: screenshotToggle });
      } catch (e) {
        /* best-effort persistence */
      }
    }
    reflectShotToggle();
    setStatus('Screenshot capture ' + (screenshotToggle ? 'on' : 'off'));
  }

  // Resolve the base64 viewport PNG to attach to the submit bundle, or null when gated off
  // (non-visual view OR toggle off) or capture is unavailable. Never throws — a failed
  // capture degrades to no-screenshot, the anchors still submit.
  async function captureScreenshot() {
    const view = detectView();
    if (!A.config.shouldCaptureScreenshot(view.kind, screenshotToggle)) return null;
    if (!chromeApi || !chromeApi.runtime || !chromeApi.runtime.sendMessage) return null;
    return new Promise(function (resolve) {
      try {
        chromeApi.runtime.sendMessage({ type: 'annotate-capture' }, function (resp) {
          if (chromeApi.runtime.lastError || !resp || !resp.ok) return resolve(null);
          resolve(resp.screenshot || null);
        });
      } catch (e) {
        resolve(null);
      }
    });
  }

  // ---------------------------------------------------------------------------
  // composer (click/selection -> comment/edit on one anchor)
  // ---------------------------------------------------------------------------

  function closeComposer() {
    const c = doc.querySelector('.annotate-composer');
    if (c) c.remove();
    clearComposerTarget(); // Fix #1: drop the target highlight when the composer closes/cancels
    clearMarkActive(); // v2.5 a4: drop the inline-mark active highlight too
    pending = null;
  }

  // ---- Fix #1: keep the comment/edit TARGET visible while the composer is open --------------
  // A REGION anchor (the §K lock levels: block / line / list / section / table) gets a persistent
  // OUTLINE over the anchored element. A real TEXT SELECTION (text-span anchor: inline edit /
  // text-selection comment) gets a highlighter fill over the selected WORDS — the words ARE the
  // target. Word highlight is RENDERED-DOC ONLY: gated off for the live --as-frontend view (which
  // keeps just the element outline) and for the image view (whose adapter owns its own region
  // markers). The overlays are page-positioned (so they ride the content on scroll, exactly like
  // the §K box) and torn down by closeComposer.
  function clearComposerTarget() {
    if (!composerTarget) return;
    composerTarget.nodes.forEach(function (n) { if (n && n.parentNode) n.remove(); });
    composerTarget = null;
  }

  // One page-positioned highlighter rect per client-rect of the selected range, each clipped to
  // its scroll-container ancestors (same §H.2 clamp as the §K box, so a selection inside a
  // scrolled table wrap stays inside it). [] when the range yields nothing drawable.
  function composerWordNodes(range) {
    if (!range || typeof range.getClientRects !== 'function') return [];
    const cac = range.commonAncestorContainer;
    const host = cac && (cac.nodeType === 1 ? cac : cac.parentElement);
    const sx = root.scrollX || 0;
    const sy = root.scrollY || 0;
    const rects = range.getClientRects();
    const nodes = [];
    for (let i = 0; i < rects.length; i++) {
      const c = clipRectToScrollAncestors(host, rects[i]);
      if (c.width <= 0 || c.height <= 0) continue;
      const w = el('div', { class: 'annotate-ui annotate-composer-word' });
      w.style.left = (c.left + sx) + 'px';
      w.style.top = (c.top + sy) + 'px';
      w.style.width = c.width + 'px';
      w.style.height = c.height + 'px';
      nodes.push(w);
    }
    return nodes;
  }

  // A single page-positioned OUTLINE over the anchored element (clipped like the §K box).
  function composerBoxNodes(element) {
    if (!element || typeof element.getBoundingClientRect !== 'function') return [];
    // visualRectFor: a re-opened lineRange anchor can resolve (via elementForAnchor) to a
    // display:contents code-block wrapper — union its line spans rather than collapse to 0×0.
    const r = clipRectToScrollAncestors(element, visualRectFor(element));
    if (r.width <= 0 || r.height <= 0) return [];
    const sx = root.scrollX || 0;
    const sy = root.scrollY || 0;
    const box = el('div', { class: 'annotate-ui annotate-composer-target' });
    box.style.left = (r.left + sx) + 'px';
    box.style.top = (r.top + sy) + 'px';
    box.style.width = r.width + 'px';
    box.style.height = r.height + 'px';
    return [box];
  }

  function showComposerTarget(opts) {
    clearComposerTarget();
    if (viewKind === 'image') return; // the image adapter owns its own region markers
    const range = opts.targetRange || null;
    const element = opts.element || null;
    // Word highlight only for a real selection on a RENDERED doc — never the live frontend page.
    const wantWords = !!(range && viewKind !== 'frontend');
    let nodes = wantWords ? composerWordNodes(range) : [];
    let words = nodes.length > 0;
    if (!nodes.length) { nodes = composerBoxNodes(element); words = false; } // fall back to the region outline
    if (!nodes.length) return;
    // v2.4 §E.1 — in EDIT mode the composing highlight is YELLOW: tag each overlay node with
    // `annotate-edit` (the nodes live on doc.body, not under the card, so a card-descendant
    // selector can't reach them; the parallel ui.css agent styles the class). mode is stashed on
    // composerTarget so repositionComposerTarget re-applies it when it rebuilds the nodes.
    if (opts.mode === 'edit') nodes.forEach(function (n) { n.classList.add('annotate-edit'); });
    nodes.forEach(function (n) { doc.body.appendChild(n); });
    composerTarget = { nodes: nodes, range: range, element: element, words: words, mode: opts.mode || null };
  }

  // Re-place the target highlight on scroll/resize (re-clips against scroll containers, like
  // repositionLock). The composer card is page-positioned so it already rides the content; this
  // just keeps the highlight clipped correctly. Drops it if the anchored element vanished.
  function repositionComposerTarget() {
    if (!composerTarget) return;
    if (composerTarget.element && !doc.contains(composerTarget.element)) { clearComposerTarget(); return; }
    composerTarget.nodes.forEach(function (n) { if (n && n.parentNode) n.remove(); });
    const nodes = composerTarget.words
      ? composerWordNodes(composerTarget.range)
      : composerBoxNodes(composerTarget.element);
    // v2.4 §E.1 — the rebuilt nodes are fresh, so re-apply the edit-mode color tag.
    if (composerTarget.mode === 'edit') nodes.forEach(function (n) { n.classList.add('annotate-edit'); });
    nodes.forEach(function (n) { doc.body.appendChild(n); });
    composerTarget.nodes = nodes;
  }

  // v2.4 §E.2 — re-color the LIVE composer-target overlay when the composer toggles comment<->edit.
  // The yellow look is the `annotate-edit` class on each overlay node (they sit on doc.body, not
  // under the card, so a card-descendant CSS rule can't reach them). Also update composerTarget.mode
  // so a later reposition keeps the right color.
  function recolorComposerTarget(mode) {
    if (!composerTarget) return;
    composerTarget.mode = mode;
    const edit = mode === 'edit';
    composerTarget.nodes.forEach(function (n) {
      if (n && n.classList) n.classList.toggle('annotate-edit', edit);
    });
  }

  // Open the comment/edit composer for an anchor. `selectedText` seeds the edit box.
  function openComposer(opts) {
    closeComposer();
    clearLock(); // a composer supersedes the transient §K click-to-select lock
    removeSelectionIcon(); // ...and the transient selection affordance
    const anchor = opts.anchor;
    const bubble = A.bubble.createBubble(anchor, { selectedText: opts.selectedText || '' });
    pending = { bubble, anchor, element: opts.element || null };

    const input = el('textarea', {
      class: 'annotate-composer-input',
      rows: '3',
      placeholder: 'Add a comment (intent — the agent decides the fix)…',
    });
    const replInput = el('textarea', {
      class: 'annotate-composer-repl',
      rows: '3',
      placeholder: 'Replacement text…',
    });
    replInput.value = bubble.original || '';

    const toggleBtn = el('button', {
      class: 'annotate-btn annotate-toggle',
      type: 'button',
      text: 'Switch to Edit',
      title: 'Switch between a comment and a suggested edit',
    });
    const card = el('div', {
      class: 'annotate-ui annotate-composer',
      'data-anchor-kind': anchor.kind,
    }, []);
    if (anchor.line != null) card.setAttribute('data-anchor-line', String(anchor.line));
    if (anchor.lineRange != null) card.setAttribute('data-anchor-line-range', JSON.stringify(anchor.lineRange));
    if (anchor.keyPath != null) card.setAttribute('data-anchor-keypath', anchor.keyPath);
    if (anchor.cell != null) card.setAttribute('data-anchor-cell', anchor.cell);
    if (anchor.point != null) card.setAttribute('data-anchor-point', JSON.stringify(anchor.point));
    if (anchor.box != null) card.setAttribute('data-anchor-box', JSON.stringify(anchor.box));

    function renderMode() {
      card.setAttribute('data-mode', bubble.type);
      toggleBtn.textContent = bubble.type === 'comment' ? 'Switch to Edit' : 'Switch to Comment';
      input.style.display = bubble.type === 'comment' ? '' : 'none';
      replInput.style.display = bubble.type === 'edit' ? '' : 'none';
    }
    toggleBtn.addEventListener('click', function () {
      bubble.toggle();
      if (bubble.type === 'edit') replInput.value = bubble.replacement || bubble.original || '';
      renderMode();
      recolorComposerTarget(bubble.type); // v2.4 §E.2 — edit -> yellow overlay, comment -> blue
    });

    function commitDraft() {
      if (bubble.type === 'comment') bubble.setComment(input.value);
      else bubble.setReplacement(replInput.value);
      if (!bubble.isComplete()) {
        setStatus(bubble.type === 'comment' ? 'Enter a comment first' : 'Enter a replacement first');
        return;
      }
      const item = bubble.toFeedback();
      // §B: reopened from a pin / sidebar row -> update that comment in place (no duplicate);
      // otherwise it is a brand-new draft. A new draft from a text selection carries its cloned
      // Range (opts.targetRange) so its pin sits ON the words; an edit-in-place keeps the
      // entry's existing range.
      if (opts.editEntry) updateEntry(opts.editEntry, item);
      else addDraft(item, opts.targetRange || null);
      closeComposer();
    }

    // a3: the primary button reads "Save" when REOPENED for an existing registered entry
    // (opts.editEntry — the pin / inline-mark / sidebar-Edit reopen path, which commitDraft
    // routes through updateEntry above), and "Add" for a brand-new annotation. Click behavior is
    // unchanged; only the label (and its hover title) is mode-aware.
    const isEditingEntry = !!(opts && opts.editEntry);
    const addBtn = el('button', {
      class: 'annotate-btn annotate-primary annotate-add',
      type: 'button',
      text: isEditingEntry ? 'Save' : 'Add',
      title: isEditingEntry ? 'Save your changes to this annotation' : 'Add this annotation to the round',
    });
    addBtn.addEventListener('click', commitDraft);

    // §J: in the composer, Enter submits/adds the comment; Shift+Enter inserts a newline.
    // Wired on BOTH textareas (comment + edit/replacement) so the keybinding is consistent
    // whichever field is active. `isComposing` guards against committing mid-IME-composition.
    function onComposerKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        commitDraft();
      }
    }
    input.addEventListener('keydown', onComposerKeydown);
    replInput.addEventListener('keydown', onComposerKeydown);

    const cancelBtn = el('button', {
      class: 'annotate-btn annotate-cancel',
      type: 'button',
      text: 'Cancel',
      title: 'Close without saving',
      onclick: closeComposer,
    });

    // §I: per-comment USER-IMAGE attach control — a NEW `.annotate-attach-*` namespace,
    // distinct from `.annotate-shot-toggle` (the tool's auto-capture). A hidden file input is
    // triggered by a visible paperclip button; on select the bytes are COPIED into the round
    // folder immediately (before submit) and the stored filename is stamped on the bubble.
    const attachInput = el('input', {
      type: 'file',
      accept: 'image/*',
      multiple: 'multiple', // a2: a single pick may carry MANY images (the change handler loops)
      class: 'annotate-attach-input',
    });
    const attachBtn = el('button', { class: 'annotate-btn annotate-attach-btn annotate-icon-btn', type: 'button' });
    attachBtn.appendChild(svgIcon(ICON_PAPERCLIP));
    setBtnLabel(attachBtn, 'Attach an image to this comment');
    // v2.6 §3: a GALLERY of attachments (one `.annotate-attach-item` each) — a comment may carry
    // MANY images. The paperclip ALWAYS adds more (never consumed by an existing attachment); each
    // upload APPENDS a fresh item (the v2.5 single-slot overwrite bug is gone).
    const attachGallery = el('div', { class: 'annotate-attach-gallery' });
    const attachRow = el('div', { class: 'annotate-attach-row' }, [attachBtn, attachGallery, attachInput]);

    // Reflect the bubble's attachment list onto the composer for the image-gate test hooks:
    // `data-attachments` (comma-joined) + `data-attachment-count`; `data-attachment` stays the
    // FIRST filename for back-compat with any external reader.
    function syncAttachData() {
      const names = bubble.attachments;
      attachRow.classList.toggle('annotate-has-attach', names.length > 0);
      card.setAttribute('data-attachment-count', String(names.length));
      if (names.length) {
        card.setAttribute('data-attachments', names.join(','));
        card.setAttribute('data-attachment', names[0]);
      } else {
        card.removeAttribute('data-attachments');
        card.removeAttribute('data-attachment');
      }
    }
    // APPEND one attachment: record it on the bubble and add a gallery item (thumb + ellipsized
    // name + a `×` remove). previewSrc is the just-read dataURL on upload; on reopen-from-disk
    // there are no inline bytes -> a filename-only `.annotate-attach-no-thumb` item.
    function appendAttachment(filename, previewSrc, label) {
      if (bubble.attachments.indexOf(filename) !== -1) { syncAttachData(); return; } // dedupe: model + DOM in lockstep
      bubble.addAttachment(filename);
      const thumb = el('img', { class: 'annotate-attach-thumb', alt: 'attachment preview' });
      if (previewSrc) thumb.src = previewSrc;
      else thumb.removeAttribute('src'); // re-opened from disk: filename only, no inline bytes
      const name = el('span', { class: 'annotate-attach-name', text: label || filename });
      const remove = el('button', { class: 'annotate-btn annotate-attach-remove annotate-icon-btn', type: 'button', text: '×' });
      setBtnLabel(remove, 'Remove attachment');
      const item = el('div', { class: 'annotate-attach-item' }, [thumb, name, remove]);
      if (!previewSrc) item.classList.add('annotate-attach-no-thumb');
      remove.addEventListener('click', function () {
        // Per-item remove drops the REFERENCE only — it does NOT delete the server file. Orphan
        // cleanup is out of scope: an unreferenced attachment is simply invisible to the loop
        // (see server/server.js). The human can still re-pick to re-reference it.
        bubble.removeAttachment(filename);
        if (item.parentNode) item.parentNode.removeChild(item);
        syncAttachData();
      });
      attachGallery.appendChild(item);
      syncAttachData();
    }

    attachBtn.addEventListener('click', function () { attachInput.click(); });
    attachInput.addEventListener('change', async function () {
      // a2: the input is `multiple`, so one pick may carry MANY files. Upload + appendAttachment
      // EACH, in selection order (sequential so order is preserved), reusing the per-file
      // copy-into-round-folder + dedupe flow. value is reset at the END so the SAME file(s) can be
      // re-picked and the next change fires.
      const files = attachInput.files ? Array.prototype.slice.call(attachInput.files) : [];
      if (!files.length) return;
      let ok = 0;
      let failed = 0;
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        setStatus(files.length > 1 ? 'Attaching image ' + (i + 1) + ' of ' + files.length + '…' : 'Attaching image…');
        let dataUrl;
        try {
          dataUrl = await readFileAsDataURL(file);
        } catch (e) {
          failed++;
          continue;
        }
        const comma = dataUrl.indexOf(',');
        const b64 = comma >= 0 ? dataUrl.slice(comma + 1) : '';
        let res;
        try {
          // ON SELECT: copy the file into the round folder now (the explicit user preference).
          res = await A.config.uploadAttachment(
            ctx,
            { head: ctx.head, data: b64, mime: file.type || '', name: file.name || '' },
            fetchImpl
          );
        } catch (e) {
          res = { error: String(e && e.message) };
        }
        if (res && res.httpStatus === 200 && res.filename) {
          appendAttachment(res.filename, dataUrl, file.name || res.filename);
          ok++;
        } else {
          failed++;
        }
      }
      attachInput.value = ''; // reset so the SAME file(s) can be re-picked and the next change fires
      if (ok && !failed) setStatus(ok === 1 ? 'Image attached' : ok + ' images attached');
      else if (ok && failed) setStatus(ok + ' attached, ' + failed + ' failed');
      else setStatus(failed === 1 ? 'Could not attach the image' : 'Could not attach ' + failed + ' images');
    });

    card.appendChild(el('div', { class: 'annotate-composer-head' }, [
      el('span', { class: 'annotate-anchor-label', text: anchorLabel(anchor) }),
      toggleBtn,
    ]));
    card.appendChild(input);
    card.appendChild(replInput);
    card.appendChild(attachRow);
    card.appendChild(el('div', { class: 'annotate-composer-foot' }, [cancelBtn, addBtn]));

    // §B edit-in-place: when reopened from a saved comment's pin / sidebar row, seed the
    // composer with the existing content (and switch to the edit field for an edit item) so
    // the human edits FROM what they wrote.
    if (opts.editEntry) {
      const it = opts.editEntry.item;
      if (it.type === 'edit') {
        bubble.setType('edit');
        if (it.replacement != null) replInput.value = it.replacement;
      } else {
        bubble.setType('comment');
        input.value = it.comment || '';
      }
      // §I/v2.6: re-surface existing attachments (filename only — the inline preview bytes aren't
      // re-fetched off disk; the human can Remove + re-pick to change them). Tolerate the legacy
      // singular `attachment` by wrapping it.
      const seedAttachments = it.attachments || (it.attachment ? [it.attachment] : []);
      for (const nm of seedAttachments) appendAttachment(nm, null, nm);
    }

    // §A: when initiated from the comment icon, the composer opens AT THE ICON
    // (opts.point — a viewport {x,y}); otherwise it falls back to the top-right placement
    // (the whole-document "Comment on doc" path, which has no on-canvas origin).
    if (opts.point) positionAtPoint(card, opts.point);
    else positionNear(card, opts.element);
    doc.body.appendChild(card);
    renderMode();
    // Fix #1: keep the target visible for the whole time the composer is open (region outline,
    // or word highlight for a text selection). focus() below clears the native selection — our
    // overlay persists independent of it, so the human keeps seeing what they're commenting on.
    // v2.4 §E.1 — pass the current mode so an EDIT composer opens with the yellow overlay.
    showComposerTarget(Object.assign({}, opts, { mode: bubble.type }));
    input.focus();
  }

  // §A reusable open-at-an-arbitrary-screen-point entry. A later agent (§B) reuses this to
  // reopen the composer in place at a saved comment's pin: openComposerAt({x,y}, {anchor,...}).
  // `point` is in VIEWPORT (clientX/clientY) coordinates.
  function openComposerAt(point, opts) {
    openComposer(Object.assign({}, opts, { point: point }));
  }

  // The viewport point of a floating affordance icon (its bottom-left), used to open the
  // composer right at the icon the user just clicked.
  function iconPoint(node) {
    const r = node.getBoundingClientRect();
    return { x: r.left, y: r.bottom };
  }

  // Place the composer at a viewport point (converted to page coords for position:absolute),
  // nudged just below-right and clamped so the 320px card stays within the viewport width.
  function positionAtPoint(card, point) {
    const vw = doc.documentElement.clientWidth || 1024;
    const cardW = 320;
    let vx = point.x + 8;
    if (vx + cardW > vw - 8) vx = Math.max(8, vw - cardW - 8);
    const vy = Math.max(60, point.y + 8);
    card.style.position = 'absolute';
    card.style.left = vx + (root.scrollX || 0) + 'px';
    card.style.top = vy + (root.scrollY || 0) + 'px';
  }

  function positionNear(card, element) {
    let top = 80;
    let right = 24;
    if (element && typeof element.getBoundingClientRect === 'function') {
      const r = element.getBoundingClientRect();
      top = Math.max(72, r.top + (root.scrollY || 0));
    }
    card.style.position = 'absolute';
    card.style.top = top + 'px';
    card.style.right = right + 'px';
  }

  // ---------------------------------------------------------------------------
  // §B drafts -> semi-transparent PINS (on canvas) + a toggleable comment SIDEBAR.
  // Saving a comment registers an entry; the entry owns a pin (canvas presence + in-place
  // edit) and a sidebar row (the list + navigation), wired for bidirectional sync.
  // ---------------------------------------------------------------------------

  function addDraft(item, targetRange) {
    drafts.push(item);
    registerComment(item, targetRange); // §B: pin (canvas) + sidebar row, in one registry entry
    // A committed spatial anchor also leaves the image-adapter's region marker over the image.
    if (item.anchor && item.anchor.kind === 'spatial' && A.image && A.image.placeMarker) {
      A.image.placeMarker(doc, item.anchor, root);
    }
    updateSendCount();
    setStatus(drafts.length + ' annotation' + (drafts.length === 1 ? '' : 's') + ' staged — Send when ready');
  }

  // Stamp the §5.2 anchor onto a DOM node as data-anchor-* attrs (shared by pins + sidebar
  // rows; the gates read these to locate a staged comment in its NEW home).
  function setAnchorAttrs(node, a) {
    if (!a) return;
    node.setAttribute('data-anchor-kind', a.kind);
    if (a.line != null) node.setAttribute('data-anchor-line', String(a.line));
    if (a.lineRange != null) node.setAttribute('data-anchor-line-range', JSON.stringify(a.lineRange));
    if (a.keyPath != null) node.setAttribute('data-anchor-keypath', a.keyPath);
    if (a.cell != null) node.setAttribute('data-anchor-cell', a.cell);
    if (a.point != null) node.setAttribute('data-anchor-point', JSON.stringify(a.point));
    if (a.box != null) node.setAttribute('data-anchor-box', JSON.stringify(a.box));
  }

  // ---- reverse map: a §5.2 anchor -> the rendered element it points at (for pin placement
  // + scroll-into-view). document/code anchors resolve to a stamped node; spatial anchors
  // resolve to the image (positioned by the normalized point). ----

  function renderRoot() {
    return doc.querySelector('.annotate-render') || doc.body;
  }
  function lineEl(n) {
    return doc.querySelector('.annotate-render [data-src-line="' + n + '"]');
  }
  function keyPathEl(kp) {
    const nodes = doc.querySelectorAll('.annotate-render [data-key-path]');
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].getAttribute('data-key-path') === kp) return nodes[i];
    }
    return null;
  }
  // v2.4 §C.1 — re-find a `text` anchor's quote under the render root and return a live Range over
  // it. Walk every text node (TreeWalker SHOW_TEXT), concatenate them into one string with a
  // [node, globalStart, globalEnd] map, find every index of anchor.quote, then DISAMBIGUATE by the
  // occurrence whose preceding text endsWith(context.before) AND following text
  // startsWith(context.after) — falling back to the first occurrence when context is empty/missing.
  // The chosen [start,end] is mapped back to (node, offset) pairs that may span multiple text nodes
  // (the quote can cross inline <em>/<code>). Returns the Range, or null if the quote isn't found.
  function rangeForTextAnchor(anchor) {
    if (!anchor || anchor.kind !== 'text' || !anchor.quote) return null;
    const rootEl = renderRoot();
    if (!rootEl || typeof doc.createTreeWalker !== 'function') return null;
    const SHOW_TEXT = (root.NodeFilter && root.NodeFilter.SHOW_TEXT) || 4;
    const walker = doc.createTreeWalker(rootEl, SHOW_TEXT, null);
    let full = '';
    const map = []; // { node, start, end } per text node, in document order
    let node;
    while ((node = walker.nextNode())) {
      // Only rendered content counts — skip text inside the annotate UI (composer / sidebar / pins).
      const pe = node.parentElement;
      if (pe && typeof pe.closest === 'function' && pe.closest('.annotate-ui')) continue;
      const t = node.nodeValue || '';
      if (!t) continue;
      map.push({ node: node, start: full.length, end: full.length + t.length });
      full += t;
    }
    const q = anchor.quote;
    const ctx = anchor.context || {};
    const before = ctx.before || '';
    const after = ctx.after || '';
    let chosen = -1;
    let firstHit = -1;
    let from = 0;
    let i;
    while ((i = full.indexOf(q, from)) >= 0) {
      if (firstHit < 0) firstHit = i;
      const preOk = !before || full.slice(0, i).endsWith(before);
      const postOk = !after || full.slice(i + q.length).startsWith(after);
      if (preOk && postOk) { chosen = i; break; }
      from = i + 1;
    }
    if (chosen < 0) chosen = firstHit; // context didn't disambiguate -> first occurrence
    if (chosen < 0) return null; // quote not present in the rendered content
    const startGlobal = chosen;
    const endGlobal = chosen + q.length;
    let startNode = null;
    let startOffset = 0;
    let endNode = null;
    let endOffset = 0;
    for (let k = 0; k < map.length; k++) {
      const m = map[k];
      if (startNode === null && startGlobal >= m.start && startGlobal < m.end) {
        startNode = m.node;
        startOffset = startGlobal - m.start;
      }
      if (endGlobal > m.start && endGlobal <= m.end) {
        endNode = m.node;
        endOffset = endGlobal - m.start;
      }
    }
    if (!startNode || !endNode) return null;
    try {
      const range = doc.createRange();
      range.setStart(startNode, startOffset);
      range.setEnd(endNode, endOffset);
      return range;
    } catch (e) {
      return null;
    }
  }

  function elementForAnchor(a) {
    if (!a) return null;
    if (a.lineRange != null) {
      // §C / v2.7: any range wrapper first (md <section> OR code-block <div>, both carry
      // data-src-line-range), else the line/heading at the start line.
      return (
        doc.querySelector('[data-src-line-range="' + a.lineRange[0] + '-' + a.lineRange[1] + '"]') ||
        lineEl(a.lineRange[0])
      );
    }
    if (a.line != null) return lineEl(a.line);
    if (a.keyPath != null) return keyPathEl(a.keyPath);
    if (a.cell != null) return doc.querySelector('.annotate-render [data-cell="' + a.cell + '"]');
    if (a.kind === 'spatial') return doc.querySelector('.annotate-image img') || renderRoot();
    // v2.4 §C.2 — a `text` anchor resolves to the element enclosing its re-found quote (used for
    // scroll-into-view + as the originEl fallback). null when the quote can't be re-found.
    if (a.kind === 'text') {
      const r = rangeForTextAnchor(a);
      if (!r) return null;
      const cac = r.commonAncestorContainer;
      return cac.nodeType === 1 ? cac : cac.parentElement;
    }
    return null;
  }

  // ---- pins ----

  // Register a saved comment: build its pin + sidebar row, push the entry, lay out the pin.
  // `targetRange` (a cloned Range from a real TEXT selection) is kept so the pin can sit ON
  // the selected words rather than the line's far-left gutter; null for block/line anchors.
  function registerComment(item, targetRange) {
    const entry = { item: item, pin: null, listItem: null, anchorEl: null, originEl: null, targetRange: targetRange || null, markSpans: [] };
    entry.anchorEl =
      item.anchor.kind === 'spatial' ? (doc.querySelector('.annotate-image img') || null) : elementForAnchor(item.anchor);
    // v2.4 §C.4 — a `text` anchor re-finds its quote on render: resolve the range ONCE here (so a
    // pin reopened without a live selection still sits on the words) and stash the enclosing block
    // as originEl — the graceful-degradation fallback for the pin/box once a DOM mutation hides
    // the quote (a live-session concern only; there is no server-side feedback hydration path).
    if (item.anchor && item.anchor.kind === 'text') {
      if (!entry.targetRange) entry.targetRange = rangeForTextAnchor(item.anchor);
      entry.originEl = elementForAnchor(item.anchor);
    }
    entry.pin = makePin(entry);
    doc.body.appendChild(entry.pin);
    entry.listItem = makeSidebarItem(entry);
    appendSidebarItem(entry.listItem);
    comments.push(entry);
    schedulePinReposition();
    // v2.5 a4 — an inline `text` anchor shows AS A MARK (not a pin): wrap its resolved range now.
    if (item.anchor && item.anchor.kind === 'text') wrapEntryMarks(entry);
    return entry;
  }

  // ---------------------------------------------------------------------------
  // v2.5 a4 — INLINE MARKS for `text` anchors. An inline comment/edit is shown by WRAPPING its
  // resolved range in <span class="annotate-mark"> (+ .annotate-mark-edit for an edit) right in
  // the rendered DOM — NOT a floating pin. The span rides the text on scroll for free, is
  // naturally clickable, and underlines naturally. ui.css gives it a LIGHT resting tint that
  // intensifies to the full compose-style fill on hover / when active. The range can CROSS inline
  // nodes (e.g. a quote spanning <em>/<code>), so we wrap PER TEXT-NODE fragment
  // (range.surroundContents would THROW on a multi-node range); only the FIRST fragment gets
  // .annotate-mark--lead, so the ::before icon renders ONCE at the start.
  // ---------------------------------------------------------------------------

  // Wrap every text-node fragment of `range` in its own .annotate-mark span, returning the spans
  // in document order. Splits the start/end text nodes at the offsets; skips text already inside
  // the annotate UI or an existing mark (no nesting). `extraClass` adds the edit color variant.
  function markRange(range, extraClass) {
    if (!range) return [];
    const startNode = range.startContainer;
    const startOff = range.startOffset;
    const endNode = range.endContainer;
    const endOff = range.endOffset;
    if (!startNode || !endNode || startNode.nodeType !== 3 || endNode.nodeType !== 3) return [];
    const cac = range.commonAncestorContainer;
    const rootEl = cac.nodeType === 1 ? cac : cac.parentElement;
    if (!rootEl) return [];
    // Collect the text nodes from start to end (inclusive), in document order.
    const targets = [];
    if (startNode === endNode) {
      targets.push(startNode);
    } else {
      const SHOW_TEXT = (root.NodeFilter && root.NodeFilter.SHOW_TEXT) || 4;
      const walker = doc.createTreeWalker(rootEl, SHOW_TEXT, null);
      let n;
      let on = false;
      while ((n = walker.nextNode())) {
        if (n === startNode) on = true;
        if (on) targets.push(n);
        if (n === endNode) break;
      }
    }
    const spans = [];
    for (let i = 0; i < targets.length; i++) {
      let node = targets[i];
      const pe = node.parentElement;
      if (!pe || (pe.closest && (pe.closest('.annotate-ui') || pe.closest('.annotate-mark')))) continue;
      const len = (node.nodeValue || '').length;
      let from = node === startNode ? startOff : 0;
      let to = node === endNode ? endOff : len;
      if (from < 0) from = 0;
      if (to > len) to = len;
      if (from >= to) continue;
      // carve out exactly [from, to): split the tail first (keeps `from` valid), then the head.
      if (to < len) node.splitText(to);
      if (from > 0) node = node.splitText(from);
      const span = doc.createElement('span');
      span.className = 'annotate-mark' + (extraClass ? ' ' + extraClass : '');
      node.parentNode.replaceChild(span, node);
      span.appendChild(node);
      spans.push(span);
    }
    if (spans.length) spans[0].classList.add('annotate-mark--lead');
    return spans;
  }

  // (Re)wrap an inline `text` entry's marks. Unwraps any existing spans FIRST (so marks never nest
  // or double — e.g. after an edit that flips comment<->edit and changes the color class),
  // re-resolves a FRESH range against the current DOM, wraps it, wires each span to reopen the
  // composer, then refreshes entry.targetRange from the post-wrap DOM (splitText moved the boundary
  // nodes). No-op when the quote can't be re-found. Block anchors have no marks.
  function wrapEntryMarks(entry) {
    if (!entry || !entry.item || !entry.item.anchor || entry.item.anchor.kind !== 'text') return;
    unwrapEntryMarks(entry);
    const range = rangeForTextAnchor(entry.item.anchor);
    if (!range) { entry.markSpans = []; return; }
    const isEdit = entry.item.type === 'edit';
    const spans = markRange(range, isEdit ? 'annotate-mark-edit' : null);
    // a4: inline marks reuse the SAME native-`title` affordance the block pins use (makePin),
    // word-for-word — hovering the highlight explains it's a saved, clickable annotation. Set
    // `title` only (NOT aria-label) so the quoted text stays the span's accessible name.
    const markTip = 'Saved ' + entry.item.type + ' — click to edit';
    spans.forEach(function (span) {
      span.setAttribute('title', markTip);
      // swallow mousedown so a click on the mark never collapses a selection / reaches the page.
      span.addEventListener('mousedown', function (e) { e.preventDefault(); });
      span.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        openComposerForEntry(entry, markPoint(span));
      });
      // group-hover: lighting ANY fragment intensifies ALL fragments of this entry (+ lead icon).
      span.addEventListener('mouseenter', function () { setEntryMarkHover(entry, true); });
      span.addEventListener('mouseleave', function () { setEntryMarkHover(entry, false); });
    });
    entry.markSpans = spans;
    // splitText invalidated the old boundary nodes — re-resolve so the cached range + the compose
    // overlay redraw against the wrapped DOM.
    entry.targetRange = rangeForTextAnchor(entry.item.anchor) || entry.targetRange;
  }

  // Unwrap an entry's mark spans: lift each span's text back out and normalize the parent so the
  // resolver re-walks intact text. Safe with no spans.
  function unwrapEntryMarks(entry) {
    if (!entry) return;
    const spans = entry.markSpans;
    entry.markSpans = [];
    if (!spans || !spans.length) return;
    const parents = [];
    spans.forEach(function (span) {
      const parent = span.parentNode;
      if (!parent) return;
      while (span.firstChild) parent.insertBefore(span.firstChild, span);
      parent.removeChild(span);
      if (parents.indexOf(parent) < 0) parents.push(parent);
    });
    parents.forEach(function (p) { if (p && typeof p.normalize === 'function') p.normalize(); });
  }

  // Toggle the active (full-fill) state on every mark of one entry while its composer is open.
  function setEntryMarkActive(entry, on) {
    if (!entry || !entry.markSpans) return;
    entry.markSpans.forEach(function (span) {
      if (span && span.classList) span.classList.toggle('annotate-mark--active', !!on);
    });
  }

  // Group-hover (dogfood-8 a1): toggle the strong-alpha --hover state on EVERY fragment of one
  // entry — wired to mouseenter/mouseleave of any single fragment — so hovering one chunk of a
  // quote split across <em>/<code> lights the whole mark + its lead icon (a CSS :hover can only
  // reach the hovered fragment). Mirrors setEntryMarkActive; --hover and --active share CSS.
  function setEntryMarkHover(entry, on) {
    if (!entry || !entry.markSpans) return;
    entry.markSpans.forEach(function (span) {
      if (span && span.classList) span.classList.toggle('annotate-mark--hover', !!on);
    });
  }

  // Drop the active state from every inline mark (called when any composer closes).
  function clearMarkActive() {
    const rootEl = renderRoot();
    if (!rootEl || typeof rootEl.querySelectorAll !== 'function') return;
    const active = rootEl.querySelectorAll('.annotate-mark--active');
    for (let i = 0; i < active.length; i++) active[i].classList.remove('annotate-mark--active');
  }

  // Viewport bottom-left of a mark span (mirrors pinPoint), where its reopened composer mounts.
  function markPoint(node) {
    const r = node.getBoundingClientRect();
    return { x: r.left, y: r.bottom };
  }

  // The semi-transparent, type-colored ICON pin (§B). RESTING = just the SOLID/filled glyph
  // (solid comment bubble for a comment, the filled pen for an edit) in the type color; CSS grows
  // the full colored marker on hover and swaps to the white OUTLINED glyph on top. Both glyphs are
  // stacked in the pin and toggled by CSS (:hover / .annotate-pin-flash) — no JS hover state. The
  // GLYPH set differs by type, so updateEntry rebuilds the pin when the type changes.
  function makePin(entry) {
    const isEdit = entry.item.type === 'edit';
    // rest = solid; hover = outlined. Edits use the pen for both (it reads fine as the white
    // outlined glyph on the colored background); comments swap solid-bubble -> outline-bubble.
    const restIcon = svgIcon(isEdit ? ICON_PEN : ICON_COMMENT_SOLID);
    restIcon.classList.add('annotate-pin-glyph', 'annotate-pin-glyph-rest');
    const hoverIcon = svgIcon(isEdit ? ICON_PEN : ICON_COMMENT);
    hoverIcon.classList.add('annotate-pin-glyph', 'annotate-pin-glyph-hover');
    const pin = el('div', {
      class: 'annotate-ui annotate-comment-pin annotate-comment-pin-' + entry.item.type,
      role: 'button',
      title: 'Saved ' + entry.item.type + ' — click to edit',
      'aria-label': 'Saved ' + entry.item.type + ' — click to edit',
    }, [restIcon, hoverIcon]);
    setAnchorAttrs(pin, entry.item.anchor);
    // v2.5 a4 — a `text` anchor is shown by its INLINE MARK, not a floating pin: keep the pin
    // element (so the entry's edit/flash plumbing stays uniform) but never render it. Belt-and-
    // braces with anchorViewportPoint returning null for text (which would hide it anyway), this
    // avoids a one-frame flash at the page origin before the first reposition tick.
    if (entry.item.anchor && entry.item.anchor.kind === 'text') pin.style.display = 'none';
    // Swallow mousedown so a pin-click never collapses a selection / reaches the page.
    pin.addEventListener('mousedown', function (e) { e.preventDefault(); });
    pin.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      onPinClick(entry);
    });
    // v2.5 a3 — hovering a BLOCK pin outlines its anchored region BEFORE you click (the
    // composer-target overlay already shows it once clicked). Inline (text) marks show at rest +
    // intensify on their own hover, and the image adapter owns spatial markers — both excluded.
    pin.addEventListener('mouseenter', function () { setPinRegionHover(entry, true); });
    pin.addEventListener('mouseleave', function () { setPinRegionHover(entry, false); });
    return pin;
  }

  // §B pin-click (pin pass 2026-06-27) — ALWAYS open the full composer to EDIT this annotation,
  // populated with its existing content, in edit mode (the same composer the sidebar "Edit"
  // button opens). Opens in place at the pin. (Previously this branched on the sidebar's
  // open/closed state and only revealed the row when open — that reveal path is retired.)
  // §B reopen (pin OR inline mark) — open the full composer to EDIT this annotation in place,
  // populated with its content, at `point`. Factored out of onPinClick so the v2.5 a4 inline-mark
  // click reuses the EXACT same path. Re-supplies a targetRange so the reopened composer redraws
  // the per-word overlay (composerWordNodes) rather than the whole-block box; for a `text` anchor
  // with no live range cached, re-find the quote; non-text anchors yield null and keep the box.
  function openComposerForEntry(entry, point) {
    openComposerAt(point, {
      anchor: entry.item.anchor,
      element: entry.anchorEl,
      editEntry: entry,
      selectedText: entry.item.type === 'edit' ? (entry.item.original || entry.item.replacement || '') : '',
      targetRange: entry.targetRange || rangeForTextAnchor(entry.item.anchor),
    });
    // v2.5 a4 — light this entry's inline marks while its composer is open (cleared by
    // closeComposer). No-op for block anchors (they have no marks).
    setEntryMarkActive(entry, true);
  }

  function onPinClick(entry) {
    openComposerForEntry(entry, pinPoint(entry.pin));
  }

  function pinPoint(pin) {
    const r = pin.getBoundingClientRect();
    return { x: r.left, y: r.bottom };
  }

  // The viewport point where an entry's pin should sit (recomputed every reposition tick, so
  // it tracks scroll/reflow). spatial -> the normalized point over the image (box -> its
  // top-left corner). text-anchored (a real selection) -> floats just ABOVE the selected WORDS
  // (range leading edge, nudged up). block-level anchors (line / li / section / header /
  // document / cell) -> OUTSIDE the block in the left gutter/margin (v2: NOT on the words).
  function anchorViewportPoint(entry) {
    const a = entry.item.anchor;
    if (a.kind === 'spatial') {
      const target = doc.querySelector('.annotate-image img') || renderRoot();
      if (!target) return null;
      const r = target.getBoundingClientRect();
      const p = a.point || (a.box ? [a.box[0], a.box[1]] : [0, 0]);
      return { x: r.left + p[0] * r.width, y: r.top + p[1] * r.height };
    }
    // v2.5 a4 — a `text` anchor NO LONGER draws a floating pin: its on-canvas affordance + click
    // target is the INLINE MARK (.annotate-mark, wrapped over the resolved range at register time;
    // see wrapEntryMarks). Returning null makes repositionPins hide the pin, so an inline
    // comment/edit reads as the mark only. (makePin also display:none's a text pin up front.)
    if (a.kind === 'text') return null;
    // Text-anchored selection that resolved to a SOURCE anchor (whole-block select): float the pin
    // just ABOVE the selected words — the cloned range's leading edge nudged up ~one pin height so
    // it sits over the words, not on them (v2). DISPLAY-only nudge. The cloned range stays live as
    // long as its nodes do; getBoundingClientRect re-reads current scroll position.
    if (entry.targetRange) {
      try {
        const rr = entry.targetRange.getBoundingClientRect();
        if (rr && (rr.width > 0 || rr.height > 0)) return { x: rr.left, y: rr.top - 22 };
      } catch (e) { /* range invalidated by a DOM change -> fall back to the anchor element */ }
    }
    let elx = entry.anchorEl;
    if (!elx || !doc.contains(elx)) {
      elx = elementForAnchor(a);
      entry.anchorEl = elx;
    }
    if (!elx) return null;
    // Block-level anchor (line / li / section / header / document / cell): pin sits OUTSIDE the
    // block in the left gutter/margin — ~22px left of the block's leading edge, clamped to the
    // viewport (v2 revert: NOT on/behind the words). Rides the content as it scrolls.
    // visualRectFor: a code-block (lineRange) anchor is display:contents — use its line-span
    // union so the gutter pin sits at the block's top-left, not at the page origin.
    const r = visualRectFor(elx);
    if (!r) return null;
    return { x: Math.max(2, r.left - 22), y: r.top };
  }

  // Lay every pin out in viewport coords; hide a pin whose anchor scrolled under the top
  // chrome / the status footer. Pins are position:fixed, so they track on scroll/reflow.
  function repositionPins() {
    if (!comments.length) return;
    const topBand = 52; // top chrome height
    const bottomBand = 30; // status footer height
    const vh = doc.documentElement.clientHeight || root.innerHeight || 768;
    comments.forEach(function (entry) {
      const pin = entry.pin;
      if (!pin) return;
      const pt = anchorViewportPoint(entry);
      if (!pt || pt.y < topBand - 6 || pt.y > vh - bottomBand) {
        pin.style.display = 'none';
        return;
      }
      pin.style.display = '';
      pin.style.left = Math.round(pt.x) + 'px';
      pin.style.top = Math.round(pt.y) + 'px';
    });
  }

  // rAF-throttle repositioning (scroll/resize/reflow can fire in bursts).
  function schedulePinReposition() {
    if (pinRaf != null) return;
    const raf = root.requestAnimationFrame || function (cb) { return root.setTimeout(cb, 16); };
    pinRaf = raf(function () {
      pinRaf = null;
      repositionPins();
    });
  }

  // Edit-in-place commit: replace an entry's §5.2 item (from the reopened composer) without
  // creating a duplicate; refresh its pin + sidebar row.
  function updateEntry(entry, newItem) {
    const di = drafts.indexOf(entry.item);
    if (di >= 0) drafts[di] = newItem;
    else drafts.push(newItem);
    entry.item = newItem;
    // Rebuild the pin so its GLYPH (comment bubble vs edit pen) + type class + anchor attrs all
    // follow a possibly-changed type. entry.targetRange is untouched, so it stays on its words.
    const freshPin = makePin(entry);
    if (entry.pin && entry.pin.parentNode) entry.pin.parentNode.replaceChild(freshPin, entry.pin);
    else doc.body.appendChild(freshPin);
    entry.pin = freshPin;
    const fresh = makeSidebarItem(entry);
    if (entry.listItem && entry.listItem.parentNode) entry.listItem.parentNode.replaceChild(fresh, entry.listItem);
    entry.listItem = fresh;
    // v2.5 a4 — re-wrap inline marks: the type may have flipped (comment<->edit), changing the
    // mark color class; wrapEntryMarks unwraps the old spans first so marks never nest/double.
    if (entry.item.anchor && entry.item.anchor.kind === 'text') wrapEntryMarks(entry);
    updateSendCount();
    setStatus('Comment updated');
    schedulePinReposition();
  }

  // ---------------------------------------------------------------------------
  // §B comment sidebar — the list + navigation (what the margin was really for). Lists every
  // staged comment of this round (+ any already on the round — none are delivered to the
  // content script in v1; this reflects the staged drafts). Toggles open/closed; in
  // document/code views it SHRINKS the canvas, on a live (frontend) page it OVERLAYS + makes
  // the canvas horizontally pannable so content under it can be slid out.
  // ---------------------------------------------------------------------------

  function buildSidebar() {
    // §F: the whole-document comment action moved OUT of the top bar and into this slot at the
    // top of the sidebar — an "add comment" affordance that creates a document-level comment
    // ({kind:'source', lineRange:[1, lastLine]}). Markdown / code views only (where a whole-doc
    // source range is meaningful); other views leave the slot empty.
    const docSlot = el('div', { class: 'annotate-sidebar-doc-slot' }, []);
    if (viewKind === 'markdown' || viewKind === 'code') {
      docSlot.appendChild(el('button', {
        class: 'annotate-btn annotate-sidebar-doc-add',
        type: 'button',
        title: 'Comment on the whole document',
        'aria-label': 'Comment on the whole document',
        onclick: onDocComment,
      }, [svgIcon(ICON_COMMENT_PLUS), el('span', { class: 'annotate-sidebar-doc-add-label', text: 'Comment on document' })]));
    }
    const sidebar = el('div', { id: 'annotate-sidebar', class: 'annotate-ui annotate-sidebar', 'data-open': '0' }, [
      el('div', { class: 'annotate-sidebar-head' }, [
        el('span', { class: 'annotate-sidebar-title', text: 'Comments' }),
        docSlot,
        el('button', {
          class: 'annotate-sidebar-close',
          type: 'button',
          title: 'Close the comment sidebar',
          'aria-label': 'Close',
          text: '✕',
          onclick: function () { closeSidebar(); },
        }),
      ]),
      el('div', {
        class: 'annotate-sidebar-empty',
        text: 'No comments yet — click a block to select it (↑/↓ to change scope), or select text, then comment.',
      }),
      el('div', { id: 'annotate-sidebar-list', class: 'annotate-sidebar-list' }, []),
    ]);
    doc.body.appendChild(sidebar);
    applySidebarLayout(); // closed by default
  }

  function isSidebarOpen() { return sidebarOpen; }
  function openSidebar() {
    if (sidebarOpen) return;
    sidebarOpen = true;
    applySidebarLayout();
    reflectSidebarToggle();
  }
  function closeSidebar() {
    if (!sidebarOpen) return;
    sidebarOpen = false;
    applySidebarLayout();
    reflectSidebarToggle();
  }
  function toggleSidebar() {
    if (sidebarOpen) closeSidebar();
    else openSidebar();
  }

  function reflectSidebarToggle() {
    const btn = doc.querySelector('.annotate-sidebar-toggle');
    if (btn) {
      btn.classList.toggle('annotate-active', sidebarOpen);
      btn.setAttribute('aria-pressed', sidebarOpen ? 'true' : 'false');
    }
  }

  // By-mode layout (decided 2026-06-27): document/code -> shrink the canvas (reflow the
  // reading column left); live UI (frontend) -> overlay (no reflow) + a horizontal pan spacer
  // so content hidden under the overlay can be slid out from under it.
  function applySidebarLayout() {
    const mode = viewKind === 'frontend' ? 'overlay' : 'shrink';
    doc.body.setAttribute('data-sidebar-mode', mode);
    doc.body.classList.toggle('annotate-sidebar-open', sidebarOpen);
    const sb = doc.getElementById('annotate-sidebar');
    if (sb) {
      sb.classList.toggle('annotate-sidebar-open', sidebarOpen);
      sb.setAttribute('data-open', sidebarOpen ? '1' : '0');
    }
    if (sidebarOpen && mode === 'overlay') addPanSpacer();
    else removePanSpacer();
    schedulePinReposition();
  }

  // The pan spacer extends the document's scroll width by the sidebar width so a frontend
  // page becomes horizontally scrollable — sliding right reveals content that the fixed
  // overlay was covering (explicit user requirement). Removed when not in overlay mode.
  function addPanSpacer() {
    if (doc.querySelector('.annotate-pan-spacer')) return;
    const spacer = el('div', { class: 'annotate-ui annotate-pan-spacer', 'aria-hidden': 'true' }, []);
    spacer.style.width = SIDEBAR_W + 'px';
    doc.body.appendChild(spacer);
  }
  function removePanSpacer() {
    const s = doc.querySelector('.annotate-pan-spacer');
    if (s) s.remove();
  }

  function appendSidebarItem(li) {
    const list = doc.getElementById('annotate-sidebar-list');
    if (list) list.appendChild(li);
    updateSidebarEmpty();
  }
  function updateSidebarEmpty() {
    const empty = doc.querySelector('.annotate-sidebar-empty');
    if (empty) empty.style.display = comments.length ? 'none' : '';
  }

  function makeSidebarItem(entry) {
    const item = entry.item;
    const li = el('div', {
      class: 'annotate-sidebar-item annotate-sidebar-item-' + item.type,
      role: 'button',
      title: 'Scroll this comment’s anchor into view',
    }, []);
    setAnchorAttrs(li, item.anchor);
    li.appendChild(el('div', { class: 'annotate-sidebar-item-head' }, [
      el('span', { class: 'annotate-sidebar-item-type', text: item.type }),
      el('span', { class: 'annotate-sidebar-item-anchor', text: anchorLabel(item.anchor) }),
      el('button', {
        // NOTE: NOT `annotate-sidebar-item-edit` — that token collides with the edit-TYPE
        // entry div (`annotate-sidebar-item-<type>`), which would bleed button styling onto
        // edit cards. The per-entry Edit BUTTON owns its own class.
        class: 'annotate-sidebar-edit-btn',
        type: 'button',
        title: 'Edit this comment',
        text: 'Edit',
        onclick: function (e) { e.stopPropagation(); editFromSidebar(entry); },
      }),
    ]));
    const bodyText = item.type === 'comment' ? (item.comment || '') : '→ ' + (item.replacement || '');
    li.appendChild(el('div', { class: 'annotate-sidebar-item-body', text: bodyText }));
    // §B sidebar -> canvas: clicking the row scrolls its anchor into view + highlights it.
    li.addEventListener('click', function () { scrollToAnchor(entry); });
    return li;
  }

  // §B sidebar -> canvas navigation: scroll the anchor into view + flash the pin and region.
  function scrollToAnchor(entry) {
    const elx = resolveAnchorEl(entry);
    if (elx && typeof elx.scrollIntoView === 'function') {
      elx.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
    }
    flashRegion(elx);
    // pins track via the scroll listener; flash the target pin once the scroll has begun.
    root.setTimeout(function () {
      schedulePinReposition();
      flashPin(entry.pin);
    }, 80);
  }

  function resolveAnchorEl(entry) {
    const a = entry.item.anchor;
    if (a.kind === 'spatial') {
      entry.anchorEl = doc.querySelector('.annotate-image img') || entry.anchorEl;
      return entry.anchorEl;
    }
    if (!entry.anchorEl || !doc.contains(entry.anchorEl)) entry.anchorEl = elementForAnchor(a);
    return entry.anchorEl;
  }

  // Edit from the sidebar row ("edit there") — reopen the composer at the pin if it is on
  // screen, else next to the row.
  function editFromSidebar(entry) {
    schedulePinReposition();
    let point;
    if (entry.pin && entry.pin.style.display !== 'none') {
      point = pinPoint(entry.pin);
    } else if (entry.listItem) {
      const r = entry.listItem.getBoundingClientRect();
      point = { x: Math.max(8, r.left - 8), y: r.top };
    } else {
      point = { x: 80, y: 80 };
    }
    openComposerAt(point, {
      anchor: entry.item.anchor,
      element: entry.anchorEl,
      editEntry: entry,
      selectedText: entry.item.type === 'edit' ? (entry.item.original || entry.item.replacement || '') : '',
      // v2.4 §D — same as onPinClick: re-supply the targetRange so the per-word overlay redraws.
      targetRange: entry.targetRange || rangeForTextAnchor(entry.item.anchor),
    });
  }

  function flashPin(pin) {
    if (!pin) return;
    pin.classList.add('annotate-pin-flash');
    root.setTimeout(function () { pin.classList.remove('annotate-pin-flash'); }, 1600);
  }
  function flashRegion(elx) {
    if (!elx || !elx.classList) return;
    elx.classList.add('annotate-region-flash');
    root.setTimeout(function () { elx.classList.remove('annotate-region-flash'); }, 1600);
  }

  // v2.5 a3 — toggle a persistent region OUTLINE on the block an entry's pin anchors, while the
  // pin is hovered (ui.css styles [data-annotate-hover]). Block anchors only: inline `text` marks
  // carry their own at-rest highlight, and spatial anchors are owned by the image adapter.
  function setPinRegionHover(entry, on) {
    const a = entry.item.anchor;
    if (!a || a.kind === 'text' || a.kind === 'spatial') return;
    const elx = resolveAnchorEl(entry);
    if (!elx || typeof elx.setAttribute !== 'function') return;
    if (on) elx.setAttribute('data-annotate-hover', '1');
    else elx.removeAttribute('data-annotate-hover');
  }

  // ---------------------------------------------------------------------------
  // §A text-selection affordance — the floating comment icon next to a SELECTION (block-level
  // commenting moved to the §K click-to-select lock below). The browser context menu + native
  // selection are never hijacked; onDocClick only acts on a plain (non-selecting) click.
  // ---------------------------------------------------------------------------

  // Build a floating comment-icon affordance (the speech bubble, ICON_COMMENT). `onOpen`
  // is called with the icon's screen point when it is clicked.
  function commentAffordance(extraClass, title, onOpen) {
    const icon = el('div', {
      class: 'annotate-ui annotate-comment-affordance ' + extraClass,
      title: title,
      role: 'button',
      'aria-label': title,
    }, [svgIcon(ICON_COMMENT)]);
    // Swallow mousedown so clicking the icon doesn't collapse the active text selection
    // (and doesn't reach the page); only `click` acts.
    icon.addEventListener('mousedown', function (e) { e.preventDefault(); });
    icon.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      onOpen(iconPoint(icon));
    });
    return icon;
  }

  // ---- text-selection affordance (§A #3): the comment icon next to a selection ----

  function removeSelectionIcon() {
    const p = doc.querySelector('.annotate-sel-affordance');
    if (p) p.remove();
  }

  function onMouseUp(ev) {
    if (inUi(ev.target)) return;
    const sel = root.getSelection ? root.getSelection() : null;
    removeSelectionIcon();
    if (!sel || sel.isCollapsed) return;
    const text = String(sel).trim();
    if (!text) return;
    clearLock(); // §M: one affordance at a time — an active text selection supersedes the §K lock
    let r;
    try { r = sel.getRangeAt(0); } catch (e) { return; }
    // v2.4 §B.1 — wrong-host fix: derive the host from the Range's REAL start text node, not
    // sel.anchorNode. Browsers set anchorNode to the container <ul> at a block boundary, so the
    // old host landed the line anchor + box on the wrong node (render.js stamps data-src-line on
    // BOTH the <ul> and each <li>). The start text node's parent is the true innermost element.
    const sc = r.startContainer;
    const host = sc && (sc.nodeType === 3 ? sc.parentElement : sc);
    if (!host || inUi(host)) return;
    // The enclosing anchorable block (the element anchorFor() would resolve `host` to).
    const blockSel = (A.dom && A.dom.ANCHORABLE_SELECTOR) || '[data-src-line], [data-key-path], [data-cell]';
    const block = typeof host.closest === 'function' ? host.closest(blockSel) : null;
    // v2.4 §B.2 — a STRICT sub-span of ONE block becomes a quote-based `text` anchor; otherwise
    // (whole-block, multi-block, or structural) keep the §K source anchor from anchorFor(host).
    let anchor = null;
    if (block && block.contains(r.endContainer) && A.dom && A.dom.textAnchorFromSelectionText) {
      // §B.3 — before/after context comes purely from the block text (textContent already includes
      // any inline <em>/<code> children the selection legitimately spans).
      anchor = A.dom.textAnchorFromSelectionText(block.textContent || '', text);
    }
    if (!anchor) anchor = anchorFor(host); // fall back to the source line / keyPath / cell anchor
    if (!anchor) return;
    // §B.4 — element:block drives the fallback box + the pin gutter; targetRange (cloned in
    // showSelectionIcon) drives the live word-highlight.
    showSelectionIcon(sel, anchor, text, block || host);
  }

  function showSelectionIcon(sel, anchor, text, element) {
    let rect;
    // Fix #1: clone the selection NOW (before the composer's focus() collapses it) so the
    // composer can word-highlight exactly these words while it is open.
    let range = null;
    try {
      const live = sel.getRangeAt(0);
      rect = live.getBoundingClientRect();
      range = live.cloneRange();
    } catch (e) {
      rect = element.getBoundingClientRect();
    }
    const icon = commentAffordance('annotate-sel-affordance', 'Comment on the selected text', function (point) {
      removeSelectionIcon();
      openComposerAt(point, { anchor: anchor, element: element, selectedText: text, targetRange: range });
    });
    icon.style.position = 'absolute';
    icon.style.top = Math.max(56, rect.top + (root.scrollY || 0) - 4) + 'px';
    icon.style.left = rect.right + (root.scrollX || 0) + 6 + 'px';
    doc.body.appendChild(icon);
  }

  // Transience (§A #3): if the selection is cleared/collapsed (a click elsewhere, a new
  // selection) WITHOUT the icon being clicked, the icon disappears.
  function onSelectionChange() {
    if (!doc.querySelector('.annotate-sel-affordance')) return;
    const sel = root.getSelection ? root.getSelection() : null;
    if (!sel || sel.isCollapsed || !String(sel).trim()) removeSelectionIcon();
  }

  // ---------------------------------------------------------------------------
  // §K click-to-select-innermost + DOM-traversal lock (replaces the old, unreachable
  // hover→floating-icon block affordance). A click LOCKS the innermost stop under the
  // pointer and parks a comment bubble right AT the click (nothing to hover-chase): a live
  // selection box outlines the current level, and the bubble's up/down arrows (or ArrowUp/
  // ArrowDown while it has focus) broaden/narrow along the DOM stop chain — up = parent,
  // down = back toward the clicked innermost. The bubble's speech-bubble opens the composer
  // on the CURRENT level's re-derived anchor. NOTE (§J): plain Enter is claimed by the
  // composer textareas, so traverse is arrows + buttons only — never Enter.
  // ---------------------------------------------------------------------------

  function clearLock() {
    if (!selLock) return;
    if (selLock.box && selLock.box.parentNode) selLock.box.remove();
    if (selLock.bubble && selLock.bubble.parentNode) selLock.bubble.remove();
    selLock = null;
  }

  // v2.7 fix: the on-screen rectangle to draw a visual (selection box / composer outline / pin
  // gutter) from a level's / anchor's element. Normally this is just el.getBoundingClientRect().
  // BUT a code-BLOCK wrapper (`.annotate-code-block`) is `display:contents` — it is layout-neutral
  // by design (it must not disturb the <pre> pre-wrap reflow), so it generates NO box and its own
  // getBoundingClientRect() collapses to a 0×0 rect at the page origin (0,0). Drawing an overlay
  // from that rect produced the ~1px blue square at the top-left corner. For such a boxless
  // element, synthesize the rect from the UNION of its descendant `.annotate-line` spans (which DO
  // have boxes): top of the first line through the bottom of the last, left..right spanning the
  // line column exactly like a single-line / section selection. Pure: depends only on `elx`, so it
  // takes any object exposing getBoundingClientRect() + querySelectorAll('.annotate-line').
  function visualRectFor(elx) {
    if (!elx || typeof elx.getBoundingClientRect !== 'function') return null;
    const r = elx.getBoundingClientRect();
    // A `display:contents` element (code-block wrapper) — detected by the known class OR a
    // collapsed 0×0 rect — has no box of its own; fall back to the union of its line spans.
    const boxless =
      (elx.classList && elx.classList.contains('annotate-code-block')) ||
      (r.width <= 0 && r.height <= 0);
    if (!boxless || typeof elx.querySelectorAll !== 'function') return r;
    const lines = elx.querySelectorAll('.annotate-line');
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (let i = 0; i < lines.length; i++) {
      const lr = lines[i].getBoundingClientRect();
      if (lr.width <= 0 && lr.height <= 0) continue; // skip a line that itself has no box
      if (lr.left < left) left = lr.left;
      if (lr.top < top) top = lr.top;
      if (lr.right > right) right = lr.right;
      if (lr.bottom > bottom) bottom = lr.bottom;
    }
    if (!Number.isFinite(left)) return r; // no usable descendant lines -> the original rect
    return { left: left, top: top, right: right, bottom: bottom, width: right - left, height: bottom - top };
  }

  // §H.2: a selected level can be larger than a scroll-container it lives inside — most notably
  // the whole-<table> stop, which (after §H.1) is content-width and scrolls horizontally INSIDE a
  // capped .annotate-table-wrap. Drawing the selection box from the element's full
  // getBoundingClientRect() would run the outline PAST the visible wrap (off the capped edge /
  // off-screen). So intersect the element's viewport rect with every CLIPPING ancestor (overflow
  // auto/scroll/hidden on that axis) up to — but not including — <body>, per axis. A level with no
  // clipping ancestor that crops it (line / section / document, whose wrappers are overflow:visible)
  // intersects to its own rect unchanged, so those boxes look exactly as before. This is a DRAWING
  // clamp only — the element's real box (used to derive the table anchor) is untouched.
  function clipRectToScrollAncestors(elx, r) {
    let left = r.left, top = r.top, right = r.right, bottom = r.bottom;
    let p = elx && elx.parentElement;
    while (p && p !== doc.body && p !== doc.documentElement) {
      let cs = null;
      try { cs = root.getComputedStyle ? root.getComputedStyle(p) : null; } catch (e) { cs = null; }
      if (cs) {
        const clipX = /^(auto|scroll|hidden)$/.test(cs.overflowX);
        const clipY = /^(auto|scroll|hidden)$/.test(cs.overflowY);
        if (clipX || clipY) {
          const pr = p.getBoundingClientRect();
          if (clipX) { if (pr.left > left) left = pr.left; if (pr.right < right) right = pr.right; }
          if (clipY) { if (pr.top > top) top = pr.top; if (pr.bottom < bottom) bottom = pr.bottom; }
        }
      }
      p = p.parentElement;
    }
    return { left: left, top: top, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
  }

  // Place the selection box over the current level's element (page coords, so it rides the
  // content on scroll) and keep the bubble parked at its locked page point. The element can
  // vanish on a reflow/advance — drop the lock if so.
  function repositionLock() {
    if (!selLock) return;
    const level = selLock.levels[selLock.idx];
    const elx = level && level.el;
    if (!elx || !doc.contains(elx)) { clearLock(); return; }
    // visualRectFor: a code-block level is display:contents (no box) — union its line spans.
    const r = clipRectToScrollAncestors(elx, visualRectFor(elx));
    const sx = root.scrollX || 0;
    const sy = root.scrollY || 0;
    selLock.box.style.left = (r.left + sx) + 'px';
    selLock.box.style.top = (r.top + sy) + 'px';
    selLock.box.style.width = Math.max(0, r.width) + 'px';
    selLock.box.style.height = Math.max(0, r.height) + 'px';
    // The bubble stays at the locked page point (clamped below the top chrome) — it never
    // chases the region, so it is always reachable regardless of which level is selected.
    selLock.bubble.style.left = selLock.pagePoint.x + 'px';
    selLock.bubble.style.top = Math.max(56, selLock.pagePoint.y) + 'px';
  }

  // Select level `idx` of the locked chain: update the box overlay and the
  // arrow enabled/disabled state (top = document, bottom = clicked innermost).
  function setLockLevel(idx) {
    if (!selLock) return;
    const n = selLock.levels.length;
    if (!n) { clearLock(); return; }
    selLock.idx = Math.max(0, Math.min(n - 1, idx));
    const atTop = selLock.idx >= n - 1; // broadest (document) — can't go up
    const atBottom = selLock.idx <= 0; // innermost clicked — can't go down
    selLock.upBtn.disabled = atTop;
    selLock.downBtn.disabled = atBottom;
    selLock.upBtn.classList.toggle('annotate-lock-disabled', atTop);
    selLock.downBtn.classList.toggle('annotate-lock-disabled', atBottom);
    repositionLock();
  }

  // up (+1) = broaden to the parent stop; down (-1) = narrow toward the clicked innermost.
  function stepLock(delta) {
    if (selLock) setLockLevel(selLock.idx + delta);
  }

  // Open the composer on the currently selected level. Seeds the edit box with the element's
  // text only for a single line/block or a cell (a multi-line range would be unwieldy).
  function commentCurrentLevel() {
    if (!selLock) return;
    const level = selLock.levels[selLock.idx];
    const anchor = level.anchor;
    const elx = level.el;
    const seed = (anchor.line != null || anchor.cell != null) && elx
      ? ((elx.innerText || elx.textContent) || '').trim()
      : '';
    const r = selLock.bubble.getBoundingClientRect();
    const point = { x: r.left, y: r.bottom };
    clearLock(); // openComposer also clears, but do it before so the box is gone immediately
    openComposerAt(point, { anchor: anchor, element: elx, selectedText: seed });
  }

  // Lock onto `levels[idx]` with the bubble parked at viewport `point`. Click-to-lock folded
  // in: the bubble persists (movement-independent) until the human comments, traverses away,
  // re-clicks elsewhere, presses Escape, or clicks outside the rendered content.
  function openLock(levels, idx, point) {
    clearLock();
    removeSelectionIcon(); // §M: locking clears any stale inline text-selection affordance
    const box = el('div', { class: 'annotate-ui annotate-sel-box' });
    const upBtn = el('button', {
      class: 'annotate-lock-arrow annotate-lock-up', type: 'button',
      title: 'Broaden selection (parent)', 'aria-label': 'Broaden selection to the parent',
    }, [svgIcon(ICON_CARET_UP)]);
    const downBtn = el('button', {
      class: 'annotate-lock-arrow annotate-lock-down', type: 'button',
      title: 'Narrow selection (child)', 'aria-label': 'Narrow selection toward the click',
    }, [svgIcon(ICON_CARET_DOWN)]);
    const commentBtn = el('button', {
      class: 'annotate-lock-comment', type: 'button',
      title: 'Comment on this selection', 'aria-label': 'Comment on this selection',
    }, [svgIcon(ICON_COMMENT)]);
    const bubble = el('div', {
      class: 'annotate-ui annotate-lock-bubble', tabindex: '0', role: 'group',
      'aria-label': 'Comment target — up/down arrows change the level',
    }, [upBtn, downBtn, commentBtn]);
    // Swallow mousedown so interacting with the bubble never collapses a selection / reaches
    // the page; the click handlers (below) act, and onDocClick ignores .annotate-ui targets.
    bubble.addEventListener('mousedown', function (e) { e.preventDefault(); });
    upBtn.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); stepLock(1); });
    downBtn.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); stepLock(-1); });
    commentBtn.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); commentCurrentLevel(); });
    // ArrowUp/ArrowDown traverse (NOT Enter — §J reserves it for the composer); Escape dismisses.
    // Scoped to the bubble (it is focused on open) so the page's own arrow-key scroll is left
    // alone the moment focus leaves the bubble.
    bubble.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowUp') { e.preventDefault(); stepLock(1); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); stepLock(-1); }
      else if (e.key === 'Escape') { e.preventDefault(); clearLock(); }
    });

    const pagePoint = { x: (point ? point.x : 0) + (root.scrollX || 0), y: (point ? point.y : 0) + (root.scrollY || 0) };
    selLock = {
      levels: levels, idx: idx, box: box, bubble: bubble,
      upBtn: upBtn, downBtn: downBtn, commentBtn: commentBtn, pagePoint: pagePoint,
    };
    doc.body.appendChild(box);
    doc.body.appendChild(bubble);
    setLockLevel(idx);
    // Focus the bubble so the arrow keys work immediately (without a prior click on it).
    try { bubble.focus({ preventScroll: true }); } catch (e) { /* older engines: buttons still work */ }
  }

  // §K: a click selects the innermost eligible element and locks the traversal bubble there.
  // Skips our own UI, an in-progress text selection (the selection affordance owns that), an
  // open composer, and the live-frontend / image views (frontend traversal is deferred this
  // round; the image adapter owns image clicks). A click outside the rendered content — or on
  // nothing anchorable — dismisses the lock.
  function onDocClick(ev) {
    if (viewKind === 'image' || viewKind === 'frontend') return;
    const t = ev.target;
    if (inUi(t)) return; // our chrome / composer / pins / lock bubble handle their own clicks
    if (pending) return; // a composer is open
    const sel = root.getSelection ? root.getSelection() : null;
    if (sel && !sel.isCollapsed && String(sel).trim()) return; // text selection -> onMouseUp owns it
    const render = doc.querySelector('.annotate-render');
    if (!render || !render.contains(t)) { clearLock(); return; }
    const levels = traversalLevels(t);
    if (!levels.length) { clearLock(); return; }
    // §M: re-clicking the already-locked element toggles the lock OFF; a click on a DIFFERENT
    // element re-locks there. Compare innermost stops (stable across up/down traversal).
    if (selLock && selLock.levels[0] && levels[0] && selLock.levels[0].el === levels[0].el) {
      clearLock();
      return;
    }
    openLock(levels, 0, { x: ev.clientX, y: ev.clientY }); // 0 = the innermost stop
  }

  function wireInteractions() {
    // §A inline text-selection commenting (UNCHANGED): the comment icon next to a selection.
    doc.addEventListener('mouseup', onMouseUp, false);
    doc.addEventListener('selectionchange', onSelectionChange, false);
    // §K: a click locks the innermost stop + a traversable comment bubble (replaces the old
    // hover→floating-icon block affordance). The page's native selection / context menu stay
    // intact — onDocClick only acts on a plain (non-selecting) click on anchorable content.
    doc.addEventListener('click', onDocClick, false);
    // The lock's box + bubble are placed in page coords; reposition them on scroll/reflow so
    // they keep tracking the content (the box also self-clears if its element disappears).
    root.addEventListener('scroll', repositionLock, true);
    // §B: pins are position:fixed in viewport coords and must re-track the content as it
    // scrolls / the layout reflows.
    root.addEventListener('scroll', schedulePinReposition, true);
    // Fix #1: the composer's target highlight re-clips against scroll containers on scroll/reflow.
    root.addEventListener('scroll', repositionComposerTarget, true);
    root.addEventListener('resize', repositionLock);
    root.addEventListener('resize', schedulePinReposition);
    root.addEventListener('resize', repositionComposerTarget);
    // Image view (§6.4 leverage order DOM -> code -> image): the dom/code adapters return
    // null on an image (no source position), so the image adapter owns its click/drag ->
    // §5.2 spatial anchors and opens the composer directly.
    const view = detectView();
    if (view.kind === 'image' && A.image) {
      imageDetach = A.image.attach(doc, {
        root: root,
        onAnchor: function (anchor, imgEl) {
          openComposer({ anchor: anchor, element: imgEl, selectedText: '' });
        },
      });
    }
  }

  // ---------------------------------------------------------------------------
  // submit + accept (the round-trip)
  // ---------------------------------------------------------------------------

  async function send() {
    const bar = chromeBar();
    if (!drafts.length) {
      setStatus('Nothing to send — add an annotation first');
      if (bar) bar.setAttribute('data-last-submit', 'empty');
      return { error: 'empty' };
    }
    setStatus('Sending…');
    // Gated viewport screenshot (§6.4): captured on a visual view when the toggle is on,
    // null on a non-visual (source/code/structured) view or when the toggle is off.
    const screenshot = await captureScreenshot();
    let result;
    try {
      result = await A.submit.submitFeedback({
        drafts: drafts.slice(),
        context: {
          session: ctx.session,
          artifact: ctx.artifact,
          head: ctx.head,
          token: ctx.token,
          revertTarget: revertTarget,
          screenshot: screenshot, // base64 PNG or null (§5.5); server decodes to <guid>-screenshot.png
        },
        sink: feedbackSink,
      });
    } catch (e) {
      setStatus('Submit failed: ' + (e && e.message));
      if (bar) bar.setAttribute('data-last-submit', 'error');
      return { error: 'exception' };
    }
    if (!result.ok) {
      // client-side disjoint-edit rejection (§5.2/§6.4) — never reached the server
      setStatus('Overlapping edits: ' + JSON.stringify(result.conflicts));
      if (bar) bar.setAttribute('data-last-submit', 'overlapping-edits');
      return result;
    }
    const resp = result.response || {};
    if (resp.status === 'submitted') {
      submitted = true;
      setStatus('Submitted — feedback returned to the agent');
      if (bar) bar.setAttribute('data-last-submit', 'submitted');
      doc.body.classList.add('annotate-submitted');
      reflectActionEmphasis(); // §G.1: drafts are sent now -> Accept returns to primary
    } else if (resp.httpStatus === 409 || resp.error === 'stale-head') {
      // §G.2: a 409 stale-head is a "newer round" ONLY when the returned head differs from
      // the one being viewed. A 409 on the SAME head means THIS round was finalized
      // (accepted, or already submitted) — NOT a new round; do not raise the false
      // "newer round" banner (which trapped the staged comments + blocked Send).
      if (resp.head && resp.head !== ctx.head) {
        setStatus('This round was superseded — advancing to the new round');
        if (bar) bar.setAttribute('data-last-submit', 'stale');
        await maybeAdvance(resp.head);
      } else {
        setStatus('This round is already finalized — it can no longer take new comments.');
        if (bar) bar.setAttribute('data-last-submit', 'closed');
      }
    } else {
      setStatus('Submit error (' + (resp.httpStatus || '?') + '): ' + (resp.error || 'unknown'));
      if (bar) bar.setAttribute('data-last-submit', 'error');
    }
    return resp;
  }

  async function accept() {
    const bar = chromeBar();
    // §G.1: Accept = approve the round AS-IS (no feedback). With UNSENT staged comments it
    // would silently discard them — the dogfood trap (the user meant Send). Require an
    // explicit confirm in that state; Send is the de-emphasized primary action (see
    // reflectActionEmphasis). Already-submitted drafts are NOT "unsent" (the round is closed
    // to them), so a normal Send -> Accept is unaffected and never prompts.
    if (!submitted && drafts.length > 0) {
      const n = drafts.length;
      const noun = 'staged comment' + (n === 1 ? '' : 's');
      const confirmFn = typeof root.confirm === 'function' ? root.confirm.bind(root) : null;
      if (confirmFn && !confirmFn(
        'Accept approves this round as-is and DISCARDS ' + n + ' ' + noun + ' you have not sent.\n\n' +
        'Click Cancel to keep ' + (n === 1 ? 'it' : 'them') + ' (then use “Send feedback” to submit), or OK to discard and accept.'
      )) {
        setStatus(n + ' ' + noun + ' kept — use “Send feedback” to submit ' + (n === 1 ? 'it' : 'them') + '.');
        if (bar) bar.setAttribute('data-last-accept', 'cancelled');
        return { cancelled: true };
      }
    }
    setStatus('Accepting…');
    let resp;
    try {
      resp = await A.config.postAccept(ctx, ctx.head, fetchImpl);
    } catch (e) {
      setStatus('Accept failed: ' + (e && e.message));
      if (bar) bar.setAttribute('data-last-accept', 'error');
      return { error: 'exception' };
    }
    if (resp.status === 'accepted') {
      setStatus('Accepted — version finalized');
      if (bar) bar.setAttribute('data-last-accept', 'accepted');
      // accepted is terminal for this round: mark it and STOP the auto-advance poll now
      // (deterministic — don't wait for the next /head poll to observe the flip). §6.3/§6.4.
      reflectStatus('accepted');
    } else if (resp.httpStatus === 409 || resp.error === 'stale-head') {
      setStatus('Cannot accept — the round advanced since you looked');
      if (bar) bar.setAttribute('data-last-accept', 'stale');
    } else {
      setStatus('Accept error (' + (resp.httpStatus || '?') + ')');
      if (bar) bar.setAttribute('data-last-accept', 'error');
    }
    return resp;
  }

  // ---------------------------------------------------------------------------
  // head auto-advance poll (§6.4) — every 1s: load a NEW head, but WARN + PRESERVE any
  // in-progress (unsent) annotations rather than silently dropping them; reflect an
  // in-place pending->submitted/accepted status flip without a reload; STOP polling once
  // the head is accepted (terminal, §6.3).
  // ---------------------------------------------------------------------------

  async function pollHead() {
    let info;
    try {
      info = await A.config.fetchHead(ctx, fetchImpl);
    } catch (e) {
      return;
    }
    if (!info) return;
    lastHeadInfo = info;
    if (info.head && info.head !== ctx.head) {
      maybeAdvance(info.head, info.status);
    } else if (info.status && info.status !== 'pending') {
      reflectStatus(info.status);
    }
  }

  function reflectStatus(status) {
    const bar = chromeBar();
    if (bar) bar.setAttribute('data-round-status', status);
    if (status === 'accepted') {
      doc.body.classList.add('annotate-accepted');
      stopPolling(); // accepted is terminal for this round — stop the poll (§6.3/§6.4)
    }
  }

  function hasUnsentWork() {
    // Already-submitted work is no longer "unsent" (the round is closed; it cannot be
    // re-submitted) — so post-submit the tab auto-advances freely. Only an open composer
    // or un-submitted drafts block the advance (§6.4 preserve-UNSENT, not preserve-sent).
    if (submitted) return false;
    return !!(pending || drafts.length);
  }

  // A new head appeared. If there is unsent in-progress work, DO NOT reload (that would
  // drop it, §6.4) — surface a persistent warning + a "Discard & view new round" control
  // and remember the target; otherwise load the new round.
  function maybeAdvance(newHead, newStatus) {
    // §G.2: a "newer round" requires a GENUINELY new head id distinct from the one being
    // viewed. A falsy head, or a head equal to the current one (e.g. a 409 from an Accept /
    // already-submitted of THIS round), is NOT a newer round — never advance or raise the
    // preserve-unsent banner for it (that spuriously trapped staged comments + blocked Send).
    if (!newHead || newHead === ctx.head) return;
    if (hasUnsentWork()) {
      deferredHead = newHead;
      warnPendingAdvance(newHead);
      return;
    }
    // An accepted new head: load it so the human sees the terminal/accepted state, then the
    // next poll reflects `accepted` and stops polling.
    deferredHead = null;
    root.location.reload();
    void newStatus;
  }

  // Render (once) the preserve-unsent warning banner with an explicit discard-and-advance
  // escape hatch. Idempotent — repeated polls just refresh the target.
  function warnPendingAdvance(newHead) {
    const bar = chromeBar();
    if (bar) bar.setAttribute('data-pending-advance', newHead || '1');
    setStatus('A newer round is ready — your unsent annotations are preserved. Send them, or discard to advance.');
    let banner = doc.querySelector('.annotate-advance-warn');
    if (!banner) {
      banner = el('div', { class: 'annotate-ui annotate-advance-warn' }, [
        el('span', {
          class: 'annotate-advance-msg',
          text: 'A newer round is ready. Your unsent annotations are kept here.',
        }),
        el('button', {
          class: 'annotate-btn annotate-advance-discard',
          type: 'button',
          text: 'Discard & view new round',
          onclick: function () {
            discardAndAdvance();
          },
        }),
      ]);
      doc.body.appendChild(banner);
    }
  }

  function clearPendingAdvance() {
    const banner = doc.querySelector('.annotate-advance-warn');
    if (banner) banner.remove();
    const bar = chromeBar();
    if (bar) bar.removeAttribute('data-pending-advance');
  }

  // Explicit human action: drop the in-progress work and load the deferred new head.
  function discardAndAdvance() {
    drafts.length = 0;
    closeComposer();
    clearPendingAdvance();
    deferredHead = null;
    root.location.reload();
  }

  function startPolling() {
    pollTimer = root.setInterval(pollHead, 1000);
  }

  function stopPolling() {
    if (pollTimer != null) {
      root.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // ---------------------------------------------------------------------------
  // init
  // ---------------------------------------------------------------------------

  async function init() {
    ctx = A.config.resolveContext({ document: doc, location: root.location });

    // Live/foreign page: discover ids via /resolve; the token is not deliverable to a
    // foreign origin yet (flagged SPEC-GAP), so the full annotate UI only comes up on a
    // served page that carries a token. Heartbeat best-effort either way.
    if (ctx.mode !== 'served') {
      await A.config.discoverLiveContext(ctx, fetchImpl);
      A.config.sendHeartbeat(ctx, fetchImpl);
      return;
    }
    if (!ctx.token) {
      A.config.sendHeartbeat(ctx, fetchImpl);
      return;
    }

    feedbackSink = A.config.makeFeedbackSink(ctx, fetchImpl);
    A.config.sendHeartbeat(ctx, fetchImpl); // §6.6 load probe (POST /loaded)
    buildChrome();
    loadShotToggle(); // reflect the persisted toggle + stamp data-screenshot[-active] for THIS view
    loadWidth(); // #3: apply the persisted reading-width preset
    wireInteractions();
    startPolling();
    doc.documentElement.setAttribute('data-annotate-ready', '1');
  }

  // Public surface (also handy for programmatic driving). NOTE: only reachable from the
  // content script's OWN isolated world — the integration gate drives via shared DOM instead.
  root.Annotate = root.Annotate || {};
  root.Annotate.content = {
    getContext: function () { return ctx; },
    getDrafts: function () { return drafts.slice(); },
    addDraft: addDraft,
    openComposer: openComposer,
    // §A: reusable open-at-an-arbitrary-screen-point composer. §B reuses this to reopen the
    // composer in place at a saved comment's pin -> openComposerAt({x,y}, {anchor, ...}).
    openComposerAt: openComposerAt,
    send: send,
    accept: accept,
    anchorFor: anchorFor,
    // §B sidebar toggle API (for §F to wire the top-bar comment-bubble + the doc-level
    // add-comment affordance that mounts in .annotate-sidebar-doc-slot).
    toggleSidebar: toggleSidebar,
    openSidebar: openSidebar,
    closeSidebar: closeSidebar,
    isSidebarOpen: isSidebarOpen,
  };

  if (doc.readyState === 'loading') {
    doc.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
