'use strict';

// Unit tests for the T6b extension pure logic that is testable without a browser:
//   - Annotate.image normalization math (normXY / pointAnchor / boxAnchor / gestureAnchor)
//     -> §5.2 `spatial` anchors with NORMALIZED 0-1 coords, NO crop (§5.3).
//   - Annotate.config.shouldCaptureScreenshot gating (§6.4): visual views capture, source/
//     code/structured views never, and the toggle gates ON TOP of the view check.
// The DOM/event wiring (attach/placeMarker) and the content.js auto-advance/screenshot
// plumbing are browser-bound and covered by the integration gate
// (tests/integration/image-gate.js).

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Ajv = require('ajv');
const { parseHTML } = require('linkedom');

const image = require('../extension/adapters/image.js');
const config = require('../extension/config.js');

// The real §5.2 anchor schema — every anchor image.js emits must validate against it, so
// these tests pin the adapter's output to the on-disk contract the server enforces.
const ajv = new Ajv({ allErrors: true, strict: false });
const feedbackSchema = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'schemas', 'feedback.schema.json'), 'utf8')
);
const validateFeedback = ajv.compile(feedbackSchema);

// Wrap a bare anchor into a full §5.2 comment feedback item so the schema (which validates
// whole items, not bare anchors) can check it.
function asItem(anchor) {
  return { id: 'a1', type: 'comment', anchor, comment: 'x' };
}

const RECT = { left: 100, top: 50, width: 200, height: 100 };

// ---------------------------------------------------------------------------
// normXY — normalize against the rendered rect, clamp to 0-1
// ---------------------------------------------------------------------------

test('normXY maps a client point to a 0-1 fraction of the rect', () => {
  // 50% across, 50% down.
  assert.deepEqual(image.normXY(RECT, 100 + 100, 50 + 50), [0.5, 0.5]);
  // top-left corner -> [0,0]; bottom-right -> [1,1].
  assert.deepEqual(image.normXY(RECT, 100, 50), [0, 0]);
  assert.deepEqual(image.normXY(RECT, 300, 150), [1, 1]);
});

test('normXY clamps out-of-bounds coordinates into 0-1 (survives an over-drag)', () => {
  assert.deepEqual(image.normXY(RECT, 0, 0), [0, 0]); // left/above the rect
  assert.deepEqual(image.normXY(RECT, 9999, 9999), [1, 1]); // far past bottom-right
});

test('normXY guards a zero-size rect (no divide-by-zero / NaN)', () => {
  const r = { left: 0, top: 0, width: 0, height: 0 };
  const xy = image.normXY(r, 10, 10);
  assert.ok(Number.isFinite(xy[0]) && Number.isFinite(xy[1]));
  assert.ok(xy[0] >= 0 && xy[0] <= 1 && xy[1] >= 0 && xy[1] <= 1);
});

// ---------------------------------------------------------------------------
// pointAnchor — a click -> normalized §5.2 spatial/point
// ---------------------------------------------------------------------------

test('pointAnchor emits a schema-valid spatial point in 0-1', () => {
  const a = image.pointAnchor(RECT, 100 + 60, 50 + 40); // 30% across, 40% down
  assert.equal(a.kind, 'spatial');
  assert.deepEqual(a.point, [0.3, 0.4]);
  assert.equal(a.box, undefined); // a click is a point, never a box
  assert.ok(validateFeedback(asItem(a)), JSON.stringify(validateFeedback.errors));
  assert.ok(a.point.every((v) => v >= 0 && v <= 1));
});

// ---------------------------------------------------------------------------
// boxAnchor — a drag -> normalized §5.2 spatial/box [x,y,w,h], top-left + size
// ---------------------------------------------------------------------------

test('boxAnchor emits a schema-valid normalized [x,y,w,h] box (top-left + size)', () => {
  // drag from (40%,25%) to (80%,75%)
  const a = image.boxAnchor(RECT, 100 + 80, 50 + 25, 100 + 160, 50 + 75);
  assert.equal(a.kind, 'spatial');
  assert.deepEqual(a.box, [0.4, 0.25, 0.4, 0.5]);
  assert.ok(validateFeedback(asItem(a)), JSON.stringify(validateFeedback.errors));
});

test('boxAnchor normalizes drag DIRECTION: dragging up-left yields the same box as down-right', () => {
  const downRight = image.boxAnchor(RECT, 100 + 80, 50 + 25, 100 + 160, 50 + 75);
  const upLeft = image.boxAnchor(RECT, 100 + 160, 50 + 75, 100 + 80, 50 + 25); // reversed
  assert.deepEqual(upLeft.box, downRight.box); // x,y = the top-left corner; w,h positive
  assert.ok(upLeft.box[2] > 0 && upLeft.box[3] > 0);
});

test('boxAnchor is NO-crop: it is pure coordinates over the full image (no image bytes)', () => {
  const a = image.boxAnchor(RECT, 100, 50, 300, 150);
  // full-image drag -> the whole 0..1 box; only coordinates, no `crop`/`image` field (§5.3).
  assert.deepEqual(a.box, [0, 0, 1, 1]);
  assert.deepEqual(Object.keys(a).sort(), ['box', 'kind']);
});

// ---------------------------------------------------------------------------
// gestureAnchor — point below the travel threshold, box above (pure-math counterpart
// of attach's pixel threshold)
// ---------------------------------------------------------------------------

test('gestureAnchor: a near-zero drag is a point, a real drag is a box', () => {
  const tiny = image.gestureAnchor(RECT, 100 + 100, 50 + 50, 100 + 100.5, 50 + 50.5);
  assert.ok(tiny.point && !tiny.box, 'sub-threshold travel -> point');

  const real = image.gestureAnchor(RECT, 100 + 40, 50 + 20, 100 + 160, 50 + 90);
  assert.ok(real.box && !real.point, 'above-threshold travel -> box');
  assert.ok(validateFeedback(asItem(real)));
});

// ---------------------------------------------------------------------------
// isImageView / findImage — adapter applicability against a served image page
// ---------------------------------------------------------------------------

test('isImageView / findImage detect the server-rendered .annotate-image viewer', () => {
  const { document } = parseHTML(
    '<!doctype html><html><body>' +
      '<div class="annotate-render annotate-image"><img src="/s/a/snapshot" data-head="g1"></div>' +
      '</body></html>'
  );
  const img = image.findImage(document);
  assert.ok(img, 'finds the <img> inside .annotate-image');
  assert.equal(img.getAttribute('data-head'), 'g1');
  assert.ok(image.isImageView(img));

  const { document: text } = parseHTML(
    '<!doctype html><html><body><div class="annotate-render annotate-markdown"><p>hi</p></div></body></html>'
  );
  assert.equal(image.findImage(text), null, 'a text view has no anchorable image');
});

// ---------------------------------------------------------------------------
// shouldCaptureScreenshot — the §6.4 gating (config.js): capture only on VISUAL views,
// and only when the toggle is on
// ---------------------------------------------------------------------------

test('shouldCaptureScreenshot captures on visual views (image/markdown/frontend) only', () => {
  assert.equal(config.shouldCaptureScreenshot('image', true), true); // T6b task: image captures
  assert.equal(config.shouldCaptureScreenshot('markdown', true), true);
  assert.equal(config.shouldCaptureScreenshot('frontend', true), true);
  // source-coordinate views are their own faithful record -> never capture (gated/inert).
  assert.equal(config.shouldCaptureScreenshot('code', true), false);
  assert.equal(config.shouldCaptureScreenshot('struct', true), false);
  assert.equal(config.shouldCaptureScreenshot('csv', true), false);
});

test('shouldCaptureScreenshot: the toggle gates ON TOP of the view check', () => {
  assert.equal(config.shouldCaptureScreenshot('image', false), false); // toggle OFF suppresses
  assert.equal(config.shouldCaptureScreenshot('code', false), false); // off + non-visual = off
  // default-on semantics: only an explicit false suppresses on a visual view.
  assert.equal(config.shouldCaptureScreenshot('image', undefined), true);
});
