'use strict';

// Image anchor adapter (tech-requirements §6.4 "Image adapter" — built third, leverage
// order DOM -> code/line -> image; §5.2 spatial anchors; §5.3 the no-crop rule).
//
// On an image view the artifact IS the visual content, so there is no source position to
// read off a node (the dom/code adapters return null here). Instead a gesture over the
// rendered <img> produces a §5.2 `spatial` anchor with NORMALIZED 0-1 coordinates:
//
//   click  -> { kind:'spatial', point:[x,y] }        a single normalized point
//   drag   -> { kind:'spatial', box:[x,y,w,h] }      a normalized rectangle (x,y = top-left)
//
// NO CROP (§5.3): a `box` is coordinates over the FULL image, not a cropped image — the
// single viewport screenshot is the visual leg, per-element crops are Won't-Have. Normalized
// 0-1 coords survive resizing (seed §8): they are computed against the rendered <img>'s
// bounding rect, so the same anchor lands on the same pixels at any display size.
//
// SPLIT (matches dom/code, see submit.js header): the pure normalization math (normXY /
// pointAnchor / boxAnchor / gestureAnchor) is dependency-free and unit-tested in Node; the
// DOM event wiring (attach) is browser-bound and exercised by the integration gate. Each
// half attaches to globalThis.Annotate.image as an MV3 content script, or exports for Node.

(function (root) {
  'use strict';

  // Below this pointer-pixel travel a gesture is a click (point), not a drag (box).
  const DRAG_THRESHOLD_PX = 4;

  function clamp01(v) {
    if (v < 0) return 0;
    if (v > 1) return 1;
    return v;
  }

  // Round to 4 decimals — plenty for steering, and keeps round.json tidy. Stays within 0-1.
  function round4(v) {
    return Math.round(v * 10000) / 10000;
  }

  // (rect, clientX, clientY) -> [x, y] normalized 0-1 against the rendered element rect.
  // rect is any { left, top, width, height } (a DOMRect or a plain object in tests).
  function normXY(rect, clientX, clientY) {
    const w = rect.width || 1;
    const h = rect.height || 1;
    const x = clamp01((clientX - rect.left) / w);
    const y = clamp01((clientY - rect.top) / h);
    return [round4(x), round4(y)];
  }

  // A single normalized point (§5.2 spatial/point).
  function pointAnchor(rect, clientX, clientY) {
    return { kind: 'spatial', point: normXY(rect, clientX, clientY) };
  }

  // A normalized box [x, y, w, h] (top-left + size), w/h always positive regardless of
  // drag direction. NO crop — coordinates over the full image (§5.3).
  function boxAnchor(rect, x0, y0, x1, y1) {
    const a = normXY(rect, x0, y0);
    const b = normXY(rect, x1, y1);
    const x = Math.min(a[0], b[0]);
    const y = Math.min(a[1], b[1]);
    const w = round4(Math.abs(b[0] - a[0]));
    const h = round4(Math.abs(b[1] - a[1]));
    return { kind: 'spatial', box: [x, y, w, h] };
  }

  // Decide point vs box from the normalized travel (the pure-math counterpart of attach's
  // pixel-threshold). `threshold` is in normalized units (default ~1% of the image).
  function gestureAnchor(rect, x0, y0, x1, y1, threshold) {
    const t = threshold == null ? 0.01 : threshold;
    const a = normXY(rect, x0, y0);
    const b = normXY(rect, x1, y1);
    if (Math.abs(b[0] - a[0]) < t && Math.abs(b[1] - a[1]) < t) {
      return pointAnchor(rect, x1, y1);
    }
    return boxAnchor(rect, x0, y0, x1, y1);
  }

  // ---------------------------------------------------------------------------
  // DOM wiring (browser-only; covered by the integration gate)
  // ---------------------------------------------------------------------------

  function isImageView(node) {
    return !!(node && typeof node.closest === 'function' && node.closest('.annotate-image'));
  }

  // The rendered image element the gesture anchors against (the server renders
  // <div class="annotate-render annotate-image"><img ... data-head=...>).
  function findImage(doc) {
    if (!doc || typeof doc.querySelector !== 'function') return null;
    return doc.querySelector('.annotate-image img') || doc.querySelector('img[data-head]') || null;
  }

  // Wire click/drag on the image. onAnchor(anchor, imgEl) fires once per gesture (a click
  // -> point, a drag -> box). Returns a detach() that removes every listener + overlay.
  function attach(doc, opts) {
    opts = opts || {};
    const win = opts.root || (typeof window !== 'undefined' ? window : null);
    const img = findImage(doc);
    if (!img || !win) return function noop() {};
    const onAnchor = typeof opts.onAnchor === 'function' ? opts.onAnchor : function () {};

    let dragging = false;
    let startX = 0;
    let startY = 0;
    let rectEl = null; // the live drag rectangle

    function pageRect() {
      const r = img.getBoundingClientRect();
      const sx = win.scrollX || 0;
      const sy = win.scrollY || 0;
      return { left: r.left, top: r.top, width: r.width, height: r.height, pageLeft: r.left + sx, pageTop: r.top + sy, sx, sy };
    }

    function clearRect() {
      if (rectEl && rectEl.parentNode) rectEl.parentNode.removeChild(rectEl);
      rectEl = null;
    }

    function drawRect(curX, curY) {
      const pr = pageRect();
      const x0 = Math.min(startX, curX);
      const y0 = Math.min(startY, curY);
      const w = Math.abs(curX - startX);
      const h = Math.abs(curY - startY);
      if (!rectEl) {
        rectEl = doc.createElement('div');
        rectEl.className = 'annotate-ui annotate-drag-rect';
        doc.body.appendChild(rectEl);
      }
      rectEl.style.left = x0 + pr.sx + 'px';
      rectEl.style.top = y0 + pr.sy + 'px';
      rectEl.style.width = w + 'px';
      rectEl.style.height = h + 'px';
    }

    function down(ev) {
      if (ev.button != null && ev.button !== 0) return;
      dragging = true;
      startX = ev.clientX;
      startY = ev.clientY;
      ev.preventDefault(); // suppress the browser's native image drag-ghost
    }

    function move(ev) {
      if (!dragging) return;
      if (Math.abs(ev.clientX - startX) >= DRAG_THRESHOLD_PX || Math.abs(ev.clientY - startY) >= DRAG_THRESHOLD_PX) {
        drawRect(ev.clientX, ev.clientY);
      }
    }

    function up(ev) {
      if (!dragging) return;
      dragging = false;
      clearRect();
      const rect = img.getBoundingClientRect();
      const dx = Math.abs(ev.clientX - startX);
      const dy = Math.abs(ev.clientY - startY);
      const anchor =
        dx < DRAG_THRESHOLD_PX && dy < DRAG_THRESHOLD_PX
          ? pointAnchor(rect, ev.clientX, ev.clientY)
          : boxAnchor(rect, startX, startY, ev.clientX, ev.clientY);
      onAnchor(anchor, img);
    }

    function noDrag(ev) {
      ev.preventDefault();
    }

    img.addEventListener('mousedown', down);
    win.addEventListener('mousemove', move);
    win.addEventListener('mouseup', up);
    img.addEventListener('dragstart', noDrag);

    return function detach() {
      img.removeEventListener('mousedown', down);
      win.removeEventListener('mousemove', move);
      win.removeEventListener('mouseup', up);
      img.removeEventListener('dragstart', noDrag);
      clearRect();
    };
  }

  // Draw a persistent marker for a COMMITTED spatial anchor (a dot for a point, an outline
  // for a box), positioned over the image in document coords. Purely visual feedback so the
  // human sees what they anchored; safe no-op if the image or anchor is absent.
  function placeMarker(doc, anchor, win) {
    win = win || (typeof window !== 'undefined' ? window : null);
    const img = findImage(doc);
    if (!img || !anchor || !win) return null;
    const r = img.getBoundingClientRect();
    const sx = win.scrollX || 0;
    const sy = win.scrollY || 0;
    const mk = doc.createElement('div');
    mk.className = 'annotate-ui annotate-marker';
    if (Array.isArray(anchor.point)) {
      mk.classList.add('annotate-marker-point');
      mk.style.left = r.left + sx + anchor.point[0] * r.width + 'px';
      mk.style.top = r.top + sy + anchor.point[1] * r.height + 'px';
    } else if (Array.isArray(anchor.box)) {
      mk.classList.add('annotate-marker-box');
      mk.style.left = r.left + sx + anchor.box[0] * r.width + 'px';
      mk.style.top = r.top + sy + anchor.box[1] * r.height + 'px';
      mk.style.width = anchor.box[2] * r.width + 'px';
      mk.style.height = anchor.box[3] * r.height + 'px';
    } else {
      return null;
    }
    doc.body.appendChild(mk);
    return mk;
  }

  const api = {
    DRAG_THRESHOLD_PX,
    clamp01,
    normXY,
    pointAnchor,
    boxAnchor,
    gestureAnchor,
    isImageView,
    findImage,
    attach,
    placeMarker,
  };

  if (typeof module === 'object' && module.exports) {
    module.exports = api; // Node (unit tests)
  } else {
    root.Annotate = root.Annotate || {};
    root.Annotate.image = api; // MV3 content script
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
