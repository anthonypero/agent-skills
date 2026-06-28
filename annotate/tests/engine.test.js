'use strict';

// Comment/edit engine tests (tech-requirements §5.2, §5.5, §6.4; T3).
//
// Exercises the whole click->anchor->bubble->disjoint->§5.5-bundle->sink pipeline against
// the SAME fixtures the renderer (T2) emits: render via server/render.js, parse the real
// HTML with linkedom, find a KNOWN element, "click" it (call the serializer on that node —
// exactly what document.elementFromPoint hands the adapter), and assert the exact anchor.
// Bundles are validated against schemas/feedback.schema.json with ajv (T1).
//
// Run with: npm test

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { parseHTML } = require('linkedom');
const Ajv = require('ajv');

const { render } = require('../server/render.js');
const dom = require('../extension/adapters/dom.js');
const code = require('../extension/adapters/code.js');
const bubble = require('../extension/bubble.js');
const submit = require('../extension/submit.js');

const FIX = path.join(__dirname, 'fixtures');
const fx = (f) => path.join(FIX, f);

function docFor(file, mode) {
  const { html } = render(fx(file), mode);
  return parseHTML(`<!doctype html><body>${html}</body>`).document;
}

const norm = (s) => s.replace(/\s+/g, ' ').trim();

// ajv over feedback.schema.json — the §5.5 anchors[] item validator (T1).
const feedbackSchema = JSON.parse(fs.readFileSync(path.join(FIX, '..', '..', 'schemas', 'feedback.schema.json'), 'utf8'));
const ajv = new Ajv({ allErrors: true, strict: false });
const validateFeedback = ajv.compile(feedbackSchema);

// ===========================================================================
// 1. Serializer round-trip: clicked element -> EXACT §5.2 anchor, per format
// ===========================================================================

test('serialize MARKDOWN line: click a block -> { kind:source, line }', () => {
  const d = docFor('sample.md');
  // The "Intro paragraph." <p> is source line 3.
  const p = [...d.querySelectorAll('p')].find((e) => norm(e.textContent) === 'Intro paragraph.');
  assert.ok(p, 'fixture sanity: found the intro paragraph');
  assert.deepEqual(dom.anchorFromElement(p), { kind: 'source', line: 3 });

  // Nested list item "Nested B" is source line 7 (drift case the renderer gets right).
  const nestedB = [...d.querySelectorAll('li')].find((e) => norm(e.textContent).startsWith('Nested B'));
  assert.deepEqual(dom.anchorFromElement(nestedB), { kind: 'source', line: 7 });
});

test('serialize CODE line: click an INNER hljs span -> resolves to the whole line', () => {
  const d = docFor('sample.js');
  // Line 3 is `function add(a, b) {`. Grab an inner hljs <span> on that line (no
  // data-src-line of its own) and confirm the walk resolves to line 3.
  const line3 = [...d.querySelectorAll('.annotate-line')].find(
    (e) => e.getAttribute('data-src-line') === '3'
  );
  assert.ok(line3, 'fixture sanity: found code line 3');
  const innerSpan = line3.querySelector('span'); // an hljs token span inside the line
  assert.ok(innerSpan, 'line 3 has inner hljs spans');

  // Both the generic DOM adapter and the focused code adapter agree.
  assert.deepEqual(dom.anchorFromElement(innerSpan), { kind: 'source', line: 3 });
  assert.deepEqual(code.anchorFromCodeNode(innerSpan), { kind: 'source', line: 3 });
  assert.equal(code.isCodeView(innerSpan), true);
});

test('serialize KEYPATH: click a leaf value -> { kind:source, keyPath } (and root = "")', () => {
  const d = docFor('sample.json');
  const byPath = new Map();
  for (const el of d.querySelectorAll('[data-key-path]')) byPath.set(el.getAttribute('data-key-path'), el);

  // Click the value span inside the user.roles[1] leaf -> walks up to that leaf node.
  const leaf = byPath.get('user.roles[1]');
  const valSpan = leaf.querySelector('.val') || leaf;
  assert.deepEqual(dom.anchorFromElement(valSpan), { kind: 'source', keyPath: 'user.roles[1]' });

  // The structured ROOT carries data-key-path="" — a real (falsy-valued) keyPath that
  // must round-trip as the empty string, not be dropped.
  const root = byPath.get('');
  assert.ok(root, 'root container carries data-key-path=""');
  assert.deepEqual(dom.anchorFromElement(root), { kind: 'source', keyPath: '' });
});

test('serialize CELL: click a td -> { kind:source, cell }', () => {
  const d = docFor('sample.csv');
  const c3 = [...d.querySelectorAll('[data-cell]')].find((e) => e.getAttribute('data-cell') === 'C3');
  assert.ok(c3, 'fixture sanity: found cell C3');
  assert.deepEqual(dom.anchorFromElement(c3), { kind: 'source', cell: 'C3' });
});

test('serialize: non-anchorable target -> null; anchorFromPoint wraps elementFromPoint', () => {
  const d = docFor('sample.md');
  const wrapper = d.querySelector('.annotate-render'); // no data-* and no anchorable ancestor
  assert.equal(dom.anchorFromElement(wrapper), null);
  assert.equal(dom.anchorFromElement(null), null);

  // anchorFromPoint is a thin wrapper over the page's elementFromPoint; stub it.
  const h1 = d.querySelector('h1');
  const fakeDoc = { elementFromPoint: () => h1 };
  assert.deepEqual(dom.anchorFromPoint(fakeDoc, 10, 10), { kind: 'source', line: 1 });
});

// v2.4 §B.5 — the DOM-free sub-line text-anchor producer. A strict sub-span of a block becomes a
// quote-based `text` anchor (with sliced before/after context); a whole-block selection (or a
// quote that isn't a literal sub-span) returns null, signaling content.js to keep the source anchor.
test('text anchor: a sub-line span -> { kind:text, quote, context:{before,after} }', () => {
  const blockText = 'the lorem ipsum dolor sit amet';
  assert.deepEqual(dom.textAnchorFromSelectionText(blockText, 'lorem ipsum'), {
    kind: 'text',
    quote: 'lorem ipsum',
    context: { before: 'the ', after: ' dolor sit amet' },
  });

  // The quote is trimmed exactly as content.js captures it (String(sel).trim()).
  assert.deepEqual(dom.textAnchorFromSelectionText(blockText, '  lorem ipsum  '), {
    kind: 'text',
    quote: 'lorem ipsum',
    context: { before: 'the ', after: ' dolor sit amet' },
  });
});

test('text anchor: a WHOLE-block selection stays source (null); so does an absent quote', () => {
  // quote === blockText.trim() -> the whole block -> keep the source line anchor (null).
  assert.equal(dom.textAnchorFromSelectionText('  Just one sentence.  ', 'Just one sentence.'), null);
  // A quote that isn't a literal sub-span (e.g. a soft markdown mismatch) -> keep source (null).
  assert.equal(dom.textAnchorFromSelectionText('the lorem ipsum dolor', 'nonexistent phrase'), null);
  // Empty selection -> null.
  assert.equal(dom.textAnchorFromSelectionText('the lorem ipsum dolor', '   '), null);
});

test('text anchor: context makes before+quote+after UNIQUE for a repeated quote', () => {
  // "cat" appears twice; the helper anchors the FIRST occurrence and slices enough context that
  // before+quote+after locates it uniquely — the invariant §C relies on to re-find the right span.
  const blockText = 'the black cat sat by the lazy red cat in the warm afternoon sun';
  const a = dom.textAnchorFromSelectionText(blockText, 'cat');
  assert.equal(a.kind, 'text');
  assert.equal(a.quote, 'cat');
  const window = a.context.before + a.quote + a.context.after;
  assert.equal(blockText.indexOf(window), blockText.lastIndexOf(window), 'before+quote+after is unique');
  assert.equal(blockText.indexOf(window) + a.context.before.length, blockText.indexOf('cat'),
    'the window brackets the first "cat"');
});

// ===========================================================================
// 2. Comment/edit bubble state machine
// ===========================================================================

test('bubble defaults to COMMENT and emits a comment feedback item', () => {
  const anchor = { kind: 'source', line: 42 };
  const b = bubble.createBubble(anchor);
  assert.equal(b.type, 'comment');
  b.setComment('this guard clause is unreachable');
  assert.equal(b.isComplete(), true);
  const item = b.toFeedback();
  assert.deepEqual(item, { type: 'comment', anchor, comment: 'this guard clause is unreachable' });
});

test('bubble toggled to EDIT pre-fills original and seeds replacement from it', () => {
  const anchor = { kind: 'source', line: 13 };
  const b = bubble.createBubble(anchor, { selectedText: 'const x = 1;' });
  assert.equal(b.type, 'comment');
  assert.equal(b.replacement, ''); // not seeded while still a comment

  b.toggle(); // -> edit: original is the highlight, replacement pre-filled FROM it
  assert.equal(b.type, 'edit');
  assert.equal(b.original, 'const x = 1;');
  assert.equal(b.replacement, 'const x = 1;');

  b.setReplacement('const x = 2;'); // human edits from the pre-fill
  assert.equal(b.isComplete(), true);
  const item = b.toFeedback('a1');
  assert.deepEqual(item, {
    id: 'a1', type: 'edit', anchor, original: 'const x = 1;', replacement: 'const x = 2;',
  });
});

test('bubble items validate against feedback.schema.json (comment AND edit)', () => {
  const c = bubble.createBubble({ kind: 'source', line: 1 }).setComment('x').toFeedback('a1');
  const e = bubble.createBubble({ kind: 'source', cell: 'B7' }, { selectedText: 'old' })
    .setType('edit').setReplacement('new').toFeedback('a2');
  assert.ok(validateFeedback(c), JSON.stringify(validateFeedback.errors));
  assert.ok(validateFeedback(e), JSON.stringify(validateFeedback.errors));
});

// v2.6 §2 — user-image attachments ACCUMULATE in the §5.2 item's optional `attachments[]` array;
// a 2nd add APPENDS (no overwrite). Field omitted by default; per-item removable.
test('bubble accumulates user-image attachments (§2); omitted by default; appends + removes', () => {
  const plain = bubble.createBubble({ kind: 'source', line: 1 }).setComment('x').toFeedback();
  assert.equal('attachments' in plain, false, 'no attachments field when none set (existing items unchanged)');
  assert.equal('attachment' in plain, false, 'never emits the deprecated singular form');

  const b = bubble.createBubble({ kind: 'source', line: 5 }).setComment('see images');
  b.addAttachment('G-attach-1.png');
  b.addAttachment('G-attach-2.jpg'); // 2nd upload APPENDS — does NOT overwrite the 1st
  assert.deepEqual(b.attachments, ['G-attach-1.png', 'G-attach-2.jpg'], 'both present, in order');
  b.addAttachment('G-attach-1.png'); // dedupe — already present
  assert.deepEqual(b.attachments, ['G-attach-1.png', 'G-attach-2.jpg']);

  const withAttach = b.toFeedback('a1');
  assert.deepEqual(withAttach, {
    id: 'a1', type: 'comment', anchor: { kind: 'source', line: 5 }, comment: 'see images',
    attachments: ['G-attach-1.png', 'G-attach-2.jpg'],
  });

  b.removeAttachment('G-attach-1.png'); // per-item remove drops just that reference
  assert.deepEqual(b.attachments, ['G-attach-2.jpg']);
  assert.deepEqual(b.toFeedback('a1').attachments, ['G-attach-2.jpg']);

  b.removeAttachment('G-attach-2.jpg'); // empty again -> field drops back out
  assert.deepEqual(b.attachments, []);
  assert.equal('attachments' in b.toFeedback(), false);
});

test('bubble seeds attachments from opts (plural array OR legacy singular)', () => {
  const fromArray = bubble.createBubble({ kind: 'source', line: 1 }, { attachments: ['a.png', 'b.png'] });
  assert.deepEqual(fromArray.attachments, ['a.png', 'b.png']);
  const fromLegacy = bubble.createBubble({ kind: 'source', line: 1 }, { attachment: 'legacy.png' });
  assert.deepEqual(fromLegacy.attachments, ['legacy.png'], 'wraps a legacy singular opts.attachment');
});

test('feedback validates with the attachments[] array AND with a legacy singular attachment', () => {
  // New canonical shape: plural array on a comment + on an edit.
  const c = bubble.createBubble({ kind: 'source', line: 1 }).setComment('x')
    .addAttachment('g-attach-1.png').addAttachment('g-attach-2.jpg').toFeedback('a1');
  const e = bubble.createBubble({ kind: 'spatial', point: [0.5, 0.5] }, { selectedText: 'o' })
    .setType('edit').setReplacement('n').addAttachment('g-attach-3.png').toFeedback('a2');
  assert.ok(validateFeedback(c), JSON.stringify(validateFeedback.errors));
  assert.ok(validateFeedback(e), JSON.stringify(validateFeedback.errors));
  assert.deepEqual(c.attachments, ['g-attach-1.png', 'g-attach-2.jpg']);

  // Back-compat alias: an old on-disk item carrying the singular `attachment` STILL validates.
  const legacy = {
    id: 'a3', type: 'comment', anchor: { kind: 'source', line: 7 }, comment: 'old', attachment: 'g-attach-1.png',
  };
  assert.ok(validateFeedback(legacy), JSON.stringify(validateFeedback.errors));
});

// ===========================================================================
// 3. Disjoint-range check — reject overlapping EDITs, allow overlapping COMMENTs
// ===========================================================================

function edit(id, anchor) { return { id, type: 'edit', anchor, original: 'o', replacement: 'r' }; }
function comment(id, anchor) { return { id, type: 'comment', anchor, comment: 'c' }; }

test('disjoint REJECTS two edits on the same line; ALLOWS different lines', () => {
  const same = submit.checkDisjointEdits([
    edit('a1', { kind: 'source', line: 10 }),
    edit('a2', { kind: 'source', line: 10 }),
  ]);
  assert.equal(same.ok, false);
  assert.deepEqual(same.conflicts, [{ a: 'a1', b: 'a2' }]);

  const diff = submit.checkDisjointEdits([
    edit('a1', { kind: 'source', line: 10 }),
    edit('a2', { kind: 'source', line: 11 }),
  ]);
  assert.equal(diff.ok, true);
});

test('disjoint REJECTS ancestor/descendant keyPath edits; ALLOWS siblings', () => {
  const nested = submit.checkDisjointEdits([
    edit('a1', { kind: 'source', keyPath: 'user' }),
    edit('a2', { kind: 'source', keyPath: 'user.name' }),
  ]);
  assert.equal(nested.ok, false, 'editing a container overlaps editing its child');

  const siblings = submit.checkDisjointEdits([
    edit('a1', { kind: 'source', keyPath: 'user.name' }),
    edit('a2', { kind: 'source', keyPath: 'user.roles' }),
  ]);
  assert.equal(siblings.ok, true);

  // 'user' must NOT be treated as a prefix of 'username' (boundary check).
  const lookalike = submit.checkDisjointEdits([
    edit('a1', { kind: 'source', keyPath: 'user' }),
    edit('a2', { kind: 'source', keyPath: 'username' }),
  ]);
  assert.equal(lookalike.ok, true);
});

test('disjoint REJECTS same cell; ALLOWS different cells', () => {
  assert.equal(submit.checkDisjointEdits([
    edit('a1', { kind: 'source', cell: 'B7' }),
    edit('a2', { kind: 'source', cell: 'B7' }),
  ]).ok, false);
  assert.equal(submit.checkDisjointEdits([
    edit('a1', { kind: 'source', cell: 'B7' }),
    edit('a2', { kind: 'source', cell: 'C7' }),
  ]).ok, true);
});

test('disjoint ALLOWS overlapping COMMENTs and comment-over-edit on the same range', () => {
  // Two comments on the same line — fine.
  assert.equal(submit.checkDisjointEdits([
    comment('a1', { kind: 'source', line: 10 }),
    comment('a2', { kind: 'source', line: 10 }),
  ]).ok, true);

  // A comment overlapping an edit on the same line — fine (only edit-edit pairs checked).
  assert.equal(submit.checkDisjointEdits([
    edit('a1', { kind: 'source', line: 10 }),
    comment('a2', { kind: 'source', line: 10 }),
  ]).ok, true);
});

// v2.4 — inline edits now carry a quote-based `text` anchor (§B). textOverlap (submit.js:130) is
// conservative: two text edits conflict only when their `quote` is byte-identical.
test('disjoint REJECTS two text edits with the SAME quote; ALLOWS different quotes', () => {
  const same = submit.checkDisjointEdits([
    edit('a1', { kind: 'text', quote: 'lorem ipsum', context: { before: 'the ', after: ' dolor' } }),
    edit('a2', { kind: 'text', quote: 'lorem ipsum', context: { before: 'a ', after: ' sit' } }),
  ]);
  assert.equal(same.ok, false, 'identical quotes conflict (textOverlap)');
  assert.deepEqual(same.conflicts, [{ a: 'a1', b: 'a2' }]);

  const diff = submit.checkDisjointEdits([
    edit('a1', { kind: 'text', quote: 'lorem ipsum', context: { before: 'the ', after: ' dolor' } }),
    edit('a2', { kind: 'text', quote: 'sit amet', context: { before: 'dolor ', after: '' } }),
  ]);
  assert.equal(diff.ok, true, 'different quotes are independent edits');
});

test('disjoint (forward-compat): spatial-box edits overlap by intersection', () => {
  assert.equal(submit.checkDisjointEdits([
    edit('a1', { kind: 'spatial', box: [0.1, 0.1, 0.4, 0.4] }),
    edit('a2', { kind: 'spatial', box: [0.3, 0.3, 0.4, 0.4] }),
  ]).ok, false, 'intersecting boxes conflict');
  assert.equal(submit.checkDisjointEdits([
    edit('a1', { kind: 'spatial', box: [0.0, 0.0, 0.2, 0.2] }),
    edit('a2', { kind: 'spatial', box: [0.5, 0.5, 0.2, 0.2] }),
  ]).ok, true, 'disjoint boxes are fine');
});

// ===========================================================================
// 4. §5.5 bundle producer + stand-in sink (end-to-end, no server)
// ===========================================================================

test('end-to-end: bubbles -> §5.5 bundle -> stand-in sink; shape + ids + schema', async () => {
  const d = docFor('sample.md');
  const p = [...d.querySelectorAll('p')].find((e) => norm(e.textContent) === 'Intro paragraph.');

  // Two annotations: a comment on the rendered Markdown line, and an edit on a code cell.
  const b1 = bubble.createBubble(dom.anchorFromElement(p)).setComment('tighten this intro');
  const b2 = bubble.createBubble({ kind: 'source', line: 13 }, { selectedText: 'const x = 1;' })
    .setType('edit').setReplacement('const x = 2;');
  const drafts = [b1.toFeedback(), b2.toFeedback()];

  const sink = submit.makeStandInSink();
  const result = await submit.submitFeedback({
    drafts,
    context: { session: 's1', artifact: 'plan', head: 'g-head' },
    sink,
  });

  assert.equal(result.ok, true);

  // Ids assigned a1, a2 monotonically (§5.2).
  assert.deepEqual(result.items.map((i) => i.id), ['a1', 'a2']);

  // The §5.5 body shape — exactly these keys, in order.
  const { body, headers } = result.bundle;
  assert.deepEqual(Object.keys(body), [
    'session', 'artifact', 'head', 'anchors', 'revertTarget', 'screenshot', 'nonce',
  ]);
  assert.equal(body.session, 's1');
  assert.equal(body.artifact, 'plan');
  assert.equal(body.head, 'g-head');
  assert.equal(body.revertTarget, null); // default null = head's own snapshot (§5.5/§5.4)
  assert.equal(body.screenshot, null); // gated off here (§6.4)
  assert.equal(typeof body.nonce, 'string'); // fresh idempotency key (§5.5)
  assert.ok(body.nonce.length > 0);

  // The token slot rides the auth header, NOT the body (§5.5/§6.3).
  assert.ok('X-Annotate-Token' in headers);

  // The stand-in sink received the bundle and mirrored the §5.5 200 (no server).
  assert.equal(sink.received.length, 1);
  assert.deepEqual(result.response, { status: 'submitted', head: 'g-head' });

  // Every anchors[] item validates against feedback.schema.json (the §5.5 validator).
  for (const item of body.anchors) {
    assert.ok(validateFeedback(item), `anchors[] invalid: ${JSON.stringify(validateFeedback.errors)}`);
  }
});

test('bundle: unfilled context -> named placeholders; overlapping edits never reach the sink', async () => {
  // Placeholders make missing runtime values obvious (T6a injects the real ones).
  const empty = submit.buildBundle({ feedback: [] });
  assert.equal(empty.body.session, '<SESSION_ID>');
  assert.equal(empty.body.head, '<HEAD_GUID>');
  assert.equal(empty.token, '<SESSION_TOKEN>');
  assert.equal(empty.headers['X-Annotate-Token'], '<SESSION_TOKEN>');

  // A disjoint failure short-circuits BEFORE the sink is called.
  const sink = submit.makeStandInSink();
  const res = await submit.submitFeedback({
    drafts: [
      { type: 'edit', anchor: { kind: 'source', line: 5 }, original: 'a', replacement: 'b' },
      { type: 'edit', anchor: { kind: 'source', line: 5 }, original: 'c', replacement: 'd' },
    ],
    sink,
  });
  assert.equal(res.ok, false);
  assert.equal(res.error, 'overlapping-edits');
  assert.deepEqual(res.conflicts, [{ a: 'a1', b: 'a2' }]);
  assert.equal(sink.received.length, 0, 'sink must not receive a conflicting submit');
});

test('nonce is fresh per buildBundle call (idempotency keys do not collide)', () => {
  const n1 = submit.buildBundle({ feedback: [] }).body.nonce;
  const n2 = submit.buildBundle({ feedback: [] }).body.nonce;
  assert.notEqual(n1, n2);
});
