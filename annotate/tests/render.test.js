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
// §C (v2.1) — nested <section class="annotate-section" data-src-line-range>
// ===========================================================================

test('markdown SECTION: each heading is the first child of a <section> with data-src-line-range', () => {
  const { html } = render(fx('sample-sections.md'));
  const root = dom(html);
  const sections = [...root.querySelectorAll('section.annotate-section')];

  // sample-sections.md has 4 headings (H1, H2, H3, H2) -> 4 wrapping sections.
  assert.equal(sections.length, 4, 'one <section> per top-level heading');

  // Every section's FIRST element child is the heading, and the section carries the
  // inclusive 1-based data-src-line-range while the heading keeps its own data-src-line.
  for (const s of sections) {
    const first = s.firstElementChild;
    assert.ok(/^H[1-6]$/.test(first.tagName), `section's first child must be a heading, got ${first.tagName}`);
    assert.match(
      s.getAttribute('data-src-line-range') || '',
      /^\d+-\d+$/,
      'section must carry data-src-line-range="N-M"'
    );
    assert.equal(s.hasAttribute('data-src-line'), false, 'section itself is NOT a [data-src-line] anchorable node');
  }
});

test('markdown SECTION: line-ranges span heading -> last line before the next same-or-higher heading', () => {
  const { html } = render(fx('sample-sections.md'));
  const root = dom(html);
  const rangeOf = (headingText) => {
    const h = [...root.querySelectorAll('h1,h2,h3')].find((e) => norm(e.textContent) === headingText);
    return h.parentElement.getAttribute('data-src-line-range');
  };
  // H1 wraps the WHOLE doc (no other H1): lines 1-15.
  assert.equal(rangeOf('Doc Title'), '1-15');
  // "## Section One" (line 5) closes just before "## Section Two" (line 13): 5-12.
  assert.equal(rangeOf('Section One'), '5-12');
  // "### Subsection A" (line 9) closes at the same boundary (next same-or-higher heading): 9-12.
  assert.equal(rangeOf('Subsection A'), '9-12');
  // "## Section Two" (line 13) is the trailing section: 13 to doc end (15).
  assert.equal(rangeOf('Section Two'), '13-15');
});

test('markdown SECTION: subsection <section> nests INSIDE its parent section', () => {
  const { html } = render(fx('sample-sections.md'));
  const root = dom(html);
  const subA = [...root.querySelectorAll('h3')].find((e) => norm(e.textContent) === 'Subsection A');
  const subSection = subA.parentElement; // the H3's wrapping section
  const sectionOne = [...root.querySelectorAll('h2')].find((e) => norm(e.textContent) === 'Section One').parentElement;
  assert.ok(sectionOne.contains(subSection) && sectionOne !== subSection, 'the H3 section must nest inside the H2 (Section One) section');
});

test('markdown SECTION: single-heading doc wraps everything in one section; data-src-line unchanged', () => {
  const { html } = render(fx('sample.md'));
  const root = dom(html);
  const sections = [...root.querySelectorAll('section.annotate-section')];
  assert.equal(sections.length, 1, 'one section for the lone H1');
  assert.equal(sections[0].getAttribute('data-src-line-range'), '1-17');
  assert.equal(sections[0].firstElementChild.tagName, 'H1');
  // The inner blocks keep their exact data-src-line (anchoring is unaffected by the wrap).
  const byLine = srcLineIndex(html);
  assert.ok((byLine.get(5) || []).some((e) => e.tagName === 'LI'), 'list item still anchored at line 5');
  assert.ok((byLine.get(12) || []).some((e) => e.tagName === 'PRE'), 'fence still anchored at line 12');
});

// ===========================================================================
// §D (v2.1) — each markdown <table> wrapped in <div class="annotate-table-wrap">
// ===========================================================================

test('markdown TABLE: each table is wrapped in div.annotate-table-wrap (full-width/scroll, §D)', () => {
  const { html } = render(fx('sample-table.md'));
  const root = dom(html);

  const tables = [...root.querySelectorAll('table')];
  assert.equal(tables.length, 1, 'sample-table.md has one table');
  const table = tables[0];

  // The table's parent is the §D scroll wrapper.
  const wrap = table.parentElement;
  assert.equal(wrap.tagName, 'DIV', "the table's parent is a <div>");
  assert.ok(
    wrap.classList.contains('annotate-table-wrap'),
    'the <table> is wrapped in .annotate-table-wrap'
  );

  // The wrapper is purely presentational — NOT an anchorable node.
  assert.equal(wrap.hasAttribute('data-src-line'), false, 'the wrap carries no data-src-line');
  assert.equal(wrap.hasAttribute('data-key-path'), false, 'the wrap carries no data-key-path');

  // Anchoring is unaffected: the <table> keeps its exact source line (the header row, line 5).
  assert.equal(table.getAttribute('data-src-line'), '5', 'the table keeps data-src-line=5 (no drift)');

  // And exactly one .annotate-table-wrap per table (no double-wrap).
  assert.equal(root.querySelectorAll('.annotate-table-wrap').length, 1, 'exactly one wrap');
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

  // FIX B (soft-wrap) invariant: a source line that visually wraps to several rows must remain
  // ONE anchor. The code-view wrap is CSS-only (white-space: pre-wrap on .annotate-code), so the
  // DOM must keep EXACTLY one [data-src-line] per source line — no extra/nested anchors, no split.
  const anchors = root.querySelectorAll('[data-src-line]');
  assert.equal(anchors.length, 5, 'exactly one data-src-line element per source line (wrap must not split anchors)');
  anchors.forEach((el) => {
    assert.ok(el.classList.contains('annotate-line'), 'every code-view anchor is an .annotate-line wrapper (hljs spans carry none)');
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
// Code BLOCK detection (v2.7) — nested .annotate-code-block wrappers
// ===========================================================================

// Ranges of every .annotate-code-block, in document order.
function codeBlockRanges(html) {
  return [...dom(html).querySelectorAll('.annotate-code-block')].map((b) =>
    b.getAttribute('data-src-line-range')
  );
}

// The enclosing .annotate-code-block ranges of the wrapper with `range`, innermost-first.
function codeBlockAncestors(html, range) {
  const b = dom(html).querySelector('.annotate-code-block[data-src-line-range="' + range + '"]');
  const out = [];
  let p = b && b.parentElement;
  while (p) {
    if (p.classList && p.classList.contains('annotate-code-block')) {
      out.push(p.getAttribute('data-src-line-range'));
    }
    p = p.parentElement;
  }
  return out;
}

test('code blocks (PHP): {} bodies nest class > function > if; () excluded; comment braces ignored', () => {
  const { html } = render(fx('sample.php'));
  const ranges = codeBlockRanges(html);

  // class body 2-11 ⊃ function body 4-10 ⊃ if body 6-8 — enclosing scopes (parents), nested.
  assert.deepEqual(ranges.sort(), ['2-11', '4-10', '6-8'].sort(), 'exactly the {}-body blocks');
  assert.deepEqual(codeBlockAncestors(html, '6-8'), ['4-10', '2-11'], 'if nests in function nests in class');
  assert.deepEqual(codeBlockAncestors(html, '4-10'), ['2-11'], 'function nests in class');

  // The `{ ... }` inside the line-7 comment must NOT create a block (token-stream, not raw text).
  // The single-line `private $items = [];` on line 3 must NOT create a block (no 3-3 level).
  assert.ok(!ranges.includes('3-3'), 'single-line [] does not create a block');
  assert.equal(
    ranges.filter((r) => r.startsWith('7')).length,
    0,
    'comment braces create no block'
  );
});

test('code blocks: per-line [data-src-line] count is unchanged; wrappers carry no line anchor', () => {
  const { html } = render(fx('sample.php')); // 11 source lines
  const root = dom(html);
  // One anchor per source line — the wrappers must only GROUP existing spans, never split/duplicate.
  assert.equal(root.querySelectorAll('[data-src-line]').length, 11, 'one [data-src-line] per source line');
  assert.equal(root.querySelectorAll('.annotate-line').length, 11, 'one .annotate-line per source line');
  root.querySelectorAll('.annotate-code-block').forEach((b) => {
    assert.equal(b.hasAttribute('data-src-line'), false, 'a code-block wrapper is NOT a [data-src-line] anchor');
    assert.ok(b.hasAttribute('data-src-line-range'), 'a code-block wrapper carries data-src-line-range (same attr as md <section>)');
  });
  // The <pre> text is byte-for-byte the source (display:contents wrappers add no whitespace).
  const src = fs.readFileSync(fx('sample.php'), 'utf8').replace(/\n$/, '');
  assert.equal(root.querySelector('pre.annotate-code').textContent, src, 'wrappers add no <pre> whitespace');
});

test('code blocks (JS): [] arrays nest inside {} objects; string/comment delimiters ignored', () => {
  const { html } = render(fx('blocks.js'));
  const ranges = codeBlockRanges(html);
  // { object } 1-8 ⊃ [ array ] 2-5.
  assert.deepEqual(ranges.sort(), ['1-8', '2-5'].sort(), 'object + array blocks');
  assert.deepEqual(codeBlockAncestors(html, '2-5'), ['1-8'], 'array nests in object');
  // The `}`/`]` inside the line-6 string and the `{`/`[` inside the line-7 comment create no blocks.
  assert.ok(!ranges.some((r) => r.startsWith('6')), 'string delimiters create no block');
  assert.ok(!ranges.some((r) => r.startsWith('7')), 'comment delimiters create no block');
});

test('code blocks (HTML/XML): element nesting climbs; void + self-closing tags do NOT nest', () => {
  const { html } = render(fx('sample.html'));
  const ranges = codeBlockRanges(html);
  // <div> 1-8 ⊃ <ul> 2-5. <li>one</li> is single-line (no level); <img> is void; <br/> self-closing.
  assert.deepEqual(ranges.sort(), ['1-8', '2-5'].sort(), 'div + ul element blocks only');
  assert.deepEqual(codeBlockAncestors(html, '2-5'), ['1-8'], 'ul nests in div');
  assert.ok(!ranges.includes('3-3') && !ranges.includes('4-4'), 'single-line <li> creates no block');
  assert.ok(!ranges.some((r) => r.startsWith('6') || r.startsWith('7')), 'void/self-closing tags do not open a block');
});

test('code blocks: touching siblings (`} else {`) stay laminar (no crossing wrappers)', () => {
  // hljs-token detection + the physical-start bump must keep the DOM strictly nested even when a
  // block closes and a sibling opens on the SAME line. We assert valid nesting via parse, not text.
  const tmp = path.join(require('node:os').tmpdir(), 'annotate-elsecase.php');
  fs.writeFileSync(
    tmp,
    ['<?php', 'function f() {', '  if (a) {', '    x();', '  } else {', '    y();', '  }', '}', ''].join('\n')
  );
  const { html } = render(tmp);
  const root = dom(html);
  // The if-body and else-body are SIBLINGS, each nested only in the function body — never each other.
  assert.deepEqual(codeBlockAncestors(html, '3-5'), ['2-8'], 'if body nests only in function');
  assert.deepEqual(codeBlockAncestors(html, '5-7'), ['2-8'], 'else body nests only in function (sibling of if)');
  // Per-line anchors intact + no extra <pre> whitespace despite the wrappers.
  const src = fs.readFileSync(tmp, 'utf8').replace(/\n$/, '');
  assert.equal(root.querySelectorAll('[data-src-line]').length, src.split('\n').length, 'one anchor per line');
  assert.equal(root.querySelector('pre.annotate-code').textContent, src, 'no extra whitespace');
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
