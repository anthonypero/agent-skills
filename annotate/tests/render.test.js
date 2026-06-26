'use strict';

// Golden tests for the position-preserving renderer (tech-requirements §6.2, §8).
//
// THE foundational gate: every downstream component reads anchor coordinates off
// the DOM attributes this module stamps, so these tests assert that each stamped
// value TRULY corresponds to its element's source position — with explicit
// coverage of the classic drift cases (fenced code, nested lists) where naive
// line-mapping is off-by-one or wrong.
//
// Run with: npm test

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { parseHTML } = require('linkedom');

const { render, columnLetter, parseDelimited, detectFormat } = require('../server/render.js');

const FIX = path.join(__dirname, 'fixtures');
const fx = (f) => path.join(FIX, f);

// linkedom is a spec-following HTML parser, so it descends into <pre>/<code> (where
// the code-line and fence anchors live) — unlike the lighter parsers that keep
// <pre> as raw text. We read attributes/text off a real DOM.
function dom(html) {
  return parseHTML(`<!doctype html><body>${html}</body>`).document;
}

// Map data-src-line -> [elements], for accuracy assertions.
function srcLineIndex(html) {
  const root = dom(html);
  const byLine = new Map();
  for (const el of root.querySelectorAll('[data-src-line]')) {
    const n = Number(el.getAttribute('data-src-line'));
    if (!byLine.has(n)) byLine.set(n, []);
    byLine.get(n).push(el);
  }
  return byLine;
}

function keyPathIndex(html) {
  const root = dom(html);
  const byPath = new Map();
  for (const el of root.querySelectorAll('[data-key-path]')) {
    byPath.set(el.getAttribute('data-key-path'), el);
  }
  return byPath;
}

function cellIndex(html) {
  const root = dom(html);
  const byCell = new Map();
  for (const el of root.querySelectorAll('[data-cell]')) {
    byCell.set(el.getAttribute('data-cell'), el);
  }
  return byCell;
}

const norm = (s) => s.replace(/\s+/g, ' ').trim();

// ===========================================================================
// Markdown — data-src-line on block elements (incl. the drift cases)
// ===========================================================================

test('markdown: every block element carries its exact source line', () => {
  const { html } = render(fx('sample.md'));
  const byLine = srcLineIndex(html);

  // Expected (data-src-line -> tag, text-substring), hand-verified against
  // sample.md and markdown-it token.map. Source lines (1-based):
  //   1  # Title
  //   3  Intro paragraph.
  //   5  - Item one        (outer <ul> AND its <li> both start here)
  //   6    - Nested A      (nested <ul> AND its <li>)
  //   7    - Nested B
  //   8  - Item two
  //  10  Some text.
  //  12  ```js   (fenced code block — opening fence line)
  //  17  Final paragraph.
  const expectTag = (line, tag, sub) => {
    assert.ok(byLine.has(line), `expected an element at data-src-line=${line}`);
    const els = byLine.get(line);
    const match = els.find(
      (e) => e.tagName === tag && norm(e.textContent).includes(sub)
    );
    assert.ok(
      match,
      `expected <${tag}> containing ${JSON.stringify(sub)} at line ${line}; ` +
        `got ${els.map((e) => e.tagName + ':' + JSON.stringify(norm(e.textContent).slice(0, 30)))}`
    );
  };

  expectTag(1, 'H1', 'Title');
  expectTag(3, 'P', 'Intro paragraph.');
  expectTag(5, 'UL', 'Item one');
  expectTag(5, 'LI', 'Item one');
  expectTag(8, 'LI', 'Item two');
  expectTag(10, 'P', 'Some text.');
  expectTag(17, 'P', 'Final paragraph.');
});

test('markdown DRIFT: nested list items carry their own (not the parent) line', () => {
  const { html } = render(fx('sample.md'));
  const byLine = srcLineIndex(html);

  // The nested <ul> opens at line 6; Nested A is line 6, Nested B is line 7.
  const nestedUl = (byLine.get(6) || []).find((e) => e.tagName === 'UL');
  assert.ok(nestedUl, 'nested <ul> must carry data-src-line=6');

  const nestedA = (byLine.get(6) || []).find(
    (e) => e.tagName === 'LI' && norm(e.textContent).startsWith('Nested A')
  );
  const nestedB = (byLine.get(7) || []).find(
    (e) => e.tagName === 'LI' && norm(e.textContent).startsWith('Nested B')
  );
  assert.ok(nestedA, '"Nested A" <li> must carry data-src-line=6');
  assert.ok(nestedB, '"Nested B" <li> must carry data-src-line=7');

  // And there must be NO <li> claiming a wrong line for the nested items.
  assert.equal(
    (byLine.get(5) || []).filter(
      (e) => e.tagName === 'LI' && norm(e.textContent).startsWith('Nested')
    ).length,
    0,
    'nested items must not inherit the outer list-item line (5)'
  );
});

test('markdown DRIFT: fenced code block + the block after it keep exact lines', () => {
  const { html } = render(fx('sample.md'));
  const byLine = srcLineIndex(html);

  // The fence's <pre> carries the OPENING-fence line (12), not line 13 (first
  // code line) and not line 15 (closing fence).
  const pre = (byLine.get(12) || []).find((e) => e.tagName === 'PRE');
  assert.ok(pre, 'fenced-code <pre> must carry data-src-line=12 (the ```js line)');
  assert.match(pre.textContent, /const x = 1;/, 'fence <pre> must contain the code body');

  // data-src-line must appear EXACTLY once for the fence (no duplicate on <code>).
  const all = [...dom(html).querySelectorAll('[data-src-line]')];
  const twelves = all.filter((e) => e.getAttribute('data-src-line') === '12');
  assert.equal(twelves.length, 1, 'fence line 12 must be stamped on exactly one element');

  // The paragraph AFTER the fence must be line 17 — the classic drift bug would
  // shift it (miscounting the fence's span). token.map keeps it exact.
  const after = (byLine.get(17) || []).find(
    (e) => e.tagName === 'P' && norm(e.textContent) === 'Final paragraph.'
  );
  assert.ok(after, 'the paragraph after the fence must keep data-src-line=17 (no drift)');
});

// ===========================================================================
// Code — one data-src-line per rendered line (highlight.js)
// ===========================================================================

test('code: each rendered line carries its exact 1-based source line', () => {
  const { html } = render(fx('sample.js'));
  const root = dom(html);
  const lines = root.querySelectorAll('.annotate-line');

  // sample.js has 5 source lines.
  assert.equal(lines.length, 5, 'expected one anchored element per source line');
  lines.forEach((el, i) => {
    assert.equal(
      el.getAttribute('data-src-line'),
      String(i + 1),
      `line element #${i} must carry data-src-line=${i + 1}`
    );
  });

  // Content lands on the right line number.
  const byLine = srcLineIndex(html);
  assert.match(byLine.get(1)[0].textContent, /const greeting/, 'line 1 = const greeting');
  assert.match(byLine.get(2)[0].textContent, /line template/, 'line 2 = template continuation');
  assert.match(byLine.get(3)[0].textContent, /function add/, 'line 3 = function add');
  assert.match(byLine.get(5)[0].textContent, /\}/, 'line 5 = closing brace');
});

test('code: multi-line string keeps hljs spans balanced across lines', () => {
  const { html } = render(fx('sample.js'));
  // The template literal opens on line 1 and closes on line 2; each line must be
  // independently well-formed (equal open/close <span> counts) so it is clickable.
  const root = dom(html);
  for (const el of root.querySelectorAll('.annotate-line')) {
    const opens = (el.innerHTML.match(/<span[\s>]/g) || []).length;
    const closes = (el.innerHTML.match(/<\/span>/g) || []).length;
    assert.equal(
      opens,
      closes,
      `line ${el.getAttribute('data-src-line')} must have balanced spans`
    );
  }
});

// ===========================================================================
// Structured (JSON / YAML / TOML) — data-key-path on every value node
// ===========================================================================

function assertKeyPaths(html) {
  const byPath = keyPathIndex(html);
  // Leaf values carry their key path with the right value text.
  const leaf = (kp, sub) => {
    assert.ok(byPath.has(kp), `expected a node at keyPath ${JSON.stringify(kp)}`);
    assert.match(
      byPath.get(kp).textContent,
      sub,
      `node ${JSON.stringify(kp)} must contain ${sub}`
    );
  };
  leaf('name', /annotate/);
  leaf('version', /0\.1\.0/);
  leaf('user.name', /Ada/);
  leaf('user.roles[0]', /admin/);
  leaf('user.roles[1]', /dev/);
  leaf('items[0].id', /1/);
  leaf('items[0].label', /first/);
  leaf('items[1].label', /second/);
  leaf('enabled', /true/);

  // Containers are anchorable too.
  assert.ok(byPath.has('user'), 'object container user must carry its keyPath');
  assert.ok(byPath.has('user.roles'), 'array container user.roles must carry its keyPath');
  assert.ok(byPath.has('items[0]'), 'array-element object items[0] must carry its keyPath');
}

test('json: every value node carries its exact key path', () => {
  const { html } = render(fx('sample.json'));
  assertKeyPaths(html);
});

test('yaml: every value node carries its exact key path', () => {
  const { html } = render(fx('sample.yaml'));
  assertKeyPaths(html);
});

test('toml: every value node carries its exact key path', () => {
  const { html } = render(fx('sample.toml'));
  assertKeyPaths(html);
});

// ===========================================================================
// CSV — data-cell="<col-letter><row-number>" (spreadsheet address)
// ===========================================================================

test('csv: each cell carries its exact spreadsheet address', () => {
  const { html } = render(fx('sample.csv'));
  const byCell = cellIndex(html);

  // Header row = row 1.
  assert.equal(byCell.get('A1').textContent, 'name');
  assert.equal(byCell.get('B1').textContent, 'role');
  assert.equal(byCell.get('C1').textContent, 'note');

  // Data rows, including a quoted field with an embedded comma...
  assert.equal(byCell.get('A2').textContent, 'Ada');
  assert.equal(byCell.get('C2').textContent, 'first, founder');

  // ...and a quoted field with an embedded newline — addressing must NOT drift.
  assert.match(byCell.get('C3').textContent, /line one\s+line two/);
  assert.equal(byCell.get('A3').textContent, 'Grace');

  // Final row stays correctly addressed despite the earlier multi-line cell.
  assert.equal(byCell.get('A4').textContent, 'Alan');
  assert.equal(byCell.get('C4').textContent, 'plain');
});

test('csv: columnLetter maps indices to spreadsheet columns', () => {
  assert.equal(columnLetter(0), 'A');
  assert.equal(columnLetter(1), 'B');
  assert.equal(columnLetter(25), 'Z');
  assert.equal(columnLetter(26), 'AA');
  assert.equal(columnLetter(27), 'AB');
});

test('csv: RFC-4180 quoting (escaped quotes, embedded delimiters/newlines)', () => {
  const rows = parseDelimited('a,"b,c","he said ""hi"""\n"x\ny",z\n', ',');
  assert.deepEqual(rows, [
    ['a', 'b,c', 'he said "hi"'],
    ['x\ny', 'z'],
  ]);
});

// ===========================================================================
// render-as-frontend — pass-through, NO anchor stamping
// ===========================================================================

test('render-as-frontend: serves the snapshot HTML verbatim, unstamped', () => {
  const { html } = render(fx('sample-frontend.html'), 'render-as-frontend');
  const raw = fs.readFileSync(fx('sample-frontend.html'), 'utf8');
  assert.equal(html, raw, 'frontend mode must be a byte-for-byte pass-through');
  assert.doesNotMatch(html, /data-src-line|data-key-path|data-cell/, 'no anchor stamping');
});

// ===========================================================================
// Format detection + return shape
// ===========================================================================

test('detectFormat: extension-driven format routing', () => {
  assert.equal(detectFormat('.md'), 'markdown');
  assert.equal(detectFormat('.json'), 'json');
  assert.equal(detectFormat('.yaml'), 'yaml');
  assert.equal(detectFormat('.yml'), 'yaml');
  assert.equal(detectFormat('.toml'), 'toml');
  assert.equal(detectFormat('.csv'), 'csv');
  assert.equal(detectFormat('.py'), 'code');
  assert.equal(detectFormat('.unknown'), 'code');
});

test('render: returns { html } only — the DOM attributes ARE the anchor map', () => {
  const result = render(fx('sample.md'));
  assert.deepEqual(Object.keys(result), ['html'], 'render() returns { html } and nothing else');
  assert.equal(typeof result.html, 'string');
});
