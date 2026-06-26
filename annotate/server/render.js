'use strict';

// Position-preserving renderer (tech-requirements §6.2).
//
// render(snapshotPath, renderMode) -> { html }
//
// Turns a text artifact into DOM that carries source coordinates, so a click in
// the browser serializes to a source line / key-path / cell rather than a CSS
// selector. The stamped DOM attributes ARE the anchor map — there is NO separate
// anchorMap return value (a resolved design decision, §6.2 Outputs). The Chrome
// content-script serializer reads these attributes off the clicked node and turns
// them straight into the §5.2 `anchor` object via the HTML `dataset` API:
//
//   data-src-line  -> el.dataset.srcLine  -> anchor.line     (Markdown blocks, code lines)
//   data-key-path  -> el.dataset.keyPath  -> anchor.keyPath  (JSON / YAML / TOML nodes)
//   data-cell      -> el.dataset.cell     -> anchor.cell      (CSV / TSV cells, e.g. "B7")
//
// renderMode:
//   'render-as-frontend' -> PASS-THROUGH: serve the snapshot HTML verbatim for the
//                           browser to execute; no anchor stamping (DOM/coordinate
//                           anchoring is owned by the content-script, not source position).
//   anything else (incl. 'render-as-code', undefined) -> annotate by detected format.
//
// The artifact FORMAT is detected from the snapshot extension; the code-vs-frontend
// RENDER MODE is the explicit parameter, never auto-detected from extension (§6.2).

const fs = require('node:fs');
const path = require('node:path');
const MarkdownIt = require('markdown-it');
const hljs = require('highlight.js');
const YAML = require('yaml');
const TOML = require('@iarna/toml');

// ---------------------------------------------------------------------------
// HTML escaping
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeAttr(s) {
  return escapeHtml(s);
}

// ===========================================================================
// Markdown  ->  data-src-line on every block element
// ===========================================================================

// markdown-it's `token.map = [startLine, endLine]` (0-based, end-exclusive) is the
// canonical position-preserving primitive. data-src-line value = token.map[0] + 1
// (1-based source line of the block's first line).
//
// The drift cases this MUST get right (naive line-mapping breaks here):
//   * fenced code blocks  — the <pre> carries the OPENING-fence line, and blocks
//                           AFTER the fence keep their true line (no shift).
//   * nested lists        — each <li>/<ul> at every depth carries its own line.
// Using token.map (not a hand-rolled counter) is what makes both correct.

function buildMarkdownRenderer() {
  const md = new MarkdownIt({ html: false, linkify: false });

  // Core rule: stamp data-src-line onto every mapped block-OPEN token. These go
  // through renderToken(), which honors token attrs, so the attribute lands on
  // the emitted element (heading_open, paragraph_open, bullet_list_open,
  // list_item_open, blockquote_open, table_open, tr_open, ...). Self-closing
  // block tokens with custom rules (fence/code_block/html_block) are handled by
  // the rule overrides below; setting the attr here is harmless for them.
  // fence/code_block/html_block are stamped by their rule overrides below (they
  // don't go through renderToken). Excluding them here keeps data-src-line on the
  // <pre>/<div> wrapper only — the default fence rule would otherwise propagate
  // token attrs onto the inner <code>, duplicating the attribute.
  const CUSTOM_RULE_TYPES = new Set(['fence', 'code_block', 'html_block']);
  md.core.ruler.push('annotate_src_line', (state) => {
    for (const token of state.tokens) {
      if (
        token.map &&
        token.nesting !== -1 &&
        token.type !== 'inline' &&
        !CUSTOM_RULE_TYPES.has(token.type)
      ) {
        token.attrSet('data-src-line', String(token.map[0] + 1));
      }
    }
  });

  // fence + code_block render via custom rules that DON'T honor token attrs, so
  // inject data-src-line into the leading <pre> tag explicitly.
  const injectIntoPre = (defaultRule) => function (tokens, idx, options, env, self) {
    const token = tokens[idx];
    let out = defaultRule(tokens, idx, options, env, self);
    if (token.map) {
      const line = token.map[0] + 1;
      out = out.replace(/<pre(?=[\s>])/, `<pre data-src-line="${line}"`);
    }
    return out;
  };

  const defaultFence =
    md.renderer.rules.fence ||
    ((t, i, o, e, s) => s.renderToken(t, i, o));
  const defaultCodeBlock =
    md.renderer.rules.code_block ||
    ((t, i, o, e, s) => s.renderToken(t, i, o));
  md.renderer.rules.fence = injectIntoPre(defaultFence);
  md.renderer.rules.code_block = injectIntoPre(defaultCodeBlock);

  // html_block renders raw output that may not begin with a tag we can stamp;
  // wrap it in a div that carries the line.
  const defaultHtmlBlock =
    md.renderer.rules.html_block ||
    ((t, i, o, e, s) => t[i].content);
  md.renderer.rules.html_block = function (tokens, idx, options, env, self) {
    const token = tokens[idx];
    const out = defaultHtmlBlock(tokens, idx, options, env, self);
    if (token.map) {
      return `<div data-src-line="${token.map[0] + 1}">${out}</div>`;
    }
    return out;
  };

  return md;
}

const MD = buildMarkdownRenderer();

function renderMarkdown(content) {
  const body = MD.render(content);
  return `<div class="annotate-render annotate-markdown">\n${body}</div>`;
}

// ===========================================================================
// Code  ->  one data-src-line per rendered line (highlight.js)
// ===========================================================================

// Extension -> highlight.js language. Most extensions ARE valid hljs aliases
// (js, ts, py, rb, go, rs, ...); the map covers the ones that aren't.
function languageForExt(ext) {
  const e = ext.toLowerCase().replace(/^\./, '');
  if (hljs.getLanguage(e)) return e;
  const map = {
    mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
    tsx: 'typescript', py: 'python', rb: 'ruby', rs: 'rust',
    kt: 'kotlin', sh: 'bash', zsh: 'bash', yml: 'yaml',
    h: 'cpp', hpp: 'cpp', cc: 'cpp', cxx: 'cpp',
    htm: 'xml', html: 'xml', vue: 'xml', svg: 'xml',
    md: 'markdown', rs2: 'rust',
  };
  return map[e] || null;
}

// Split highlighted HTML into per-line spans WITHOUT breaking hljs <span> nesting
// that crosses newlines (multi-line strings / comments). At each line boundary we
// close every currently-open span and re-open it on the next line, so each line is
// independently well-formed and clickable, and carries its true source line number.
function wrapHighlightedLines(highlightedHtml) {
  const lines = highlightedHtml.split('\n');
  const open = []; // stack of full opening <span ...> tags currently unclosed
  const tagRe = /<\/?span[^>]*>/g;
  const out = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const prefix = open.join(''); // re-open spans carried from previous lines
    let m;
    tagRe.lastIndex = 0;
    while ((m = tagRe.exec(line)) !== null) {
      if (m[0].charAt(1) === '/') open.pop();
      else open.push(m[0]);
    }
    const suffix = '</span>'.repeat(open.length); // close spans still open at EOL
    out.push(
      `<span class="annotate-line" data-src-line="${i + 1}">${prefix}${line}${suffix}</span>`
    );
  }
  return out.join('\n');
}

function renderCode(content, ext) {
  // Drop a single trailing newline so we don't emit a phantom empty final line
  // (line N still maps to source line N).
  let src = content;
  if (src.endsWith('\n')) src = src.slice(0, -1);

  const lang = languageForExt(ext);
  let highlighted;
  if (lang && hljs.getLanguage(lang)) {
    highlighted = hljs.highlight(src, { language: lang, ignoreIllegals: true }).value;
  } else {
    highlighted = hljs.highlightAuto(src).value;
  }

  const lines = wrapHighlightedLines(highlighted);
  return `<pre class="annotate-render annotate-code"><code class="hljs">${lines}</code></pre>`;
}

// ===========================================================================
// Structured (JSON / YAML / TOML)  ->  data-key-path on every value node
// ===========================================================================

// keyPath format (stable across reformatting — that is why it beats line numbers
// for structured data):
//   object member:  parent.child        (top-level member: just `child`)
//   array element:  parent[0]           (top-level array element: just `[0]`)
//   nested:         items[0].name, user.roles[1], a.b.c
// Container nodes (objects/arrays) ALSO carry their own keyPath, so a whole
// subtree is anchorable. (Keys containing `.` or `[` are not bracket-escaped in
// v1 — a documented limitation, not a v1 production path.)

function childPath(parentPath, key, isIndex) {
  if (isIndex) return `${parentPath}[${key}]`;
  return parentPath ? `${parentPath}.${key}` : `${key}`;
}

function renderScalar(value) {
  let cls = 'null';
  let text;
  if (value === null) {
    text = 'null';
  } else if (typeof value === 'string') {
    cls = 'string';
    text = JSON.stringify(value);
  } else if (typeof value === 'number') {
    cls = 'number';
    text = String(value);
  } else if (typeof value === 'boolean') {
    cls = 'boolean';
    text = String(value);
  } else {
    // Dates (YAML/TOML can yield Date objects) and other scalars.
    cls = 'scalar';
    text = String(value);
  }
  return `<span class="val ${cls}">${escapeHtml(text)}</span>`;
}

function renderNode(value, keyPath) {
  const kpAttr = ` data-key-path="${escapeAttr(keyPath)}"`;

  if (Array.isArray(value)) {
    const rows = value.map((v, i) => {
      const cp = childPath(keyPath, i, true);
      return `<div class="entry"><span class="idx">[${i}]</span> ${renderNode(v, cp)}</div>`;
    });
    return `<div class="node array"${kpAttr}>${rows.join('')}</div>`;
  }

  if (value && typeof value === 'object' && !(value instanceof Date)) {
    const rows = Object.keys(value).map((k) => {
      const cp = childPath(keyPath, k, false);
      return `<div class="entry"><span class="key">${escapeHtml(k)}</span>: ${renderNode(value[k], cp)}</div>`;
    });
    return `<div class="node object"${kpAttr}>${rows.join('')}</div>`;
  }

  // Scalar value node still carries its key path.
  return `<div class="node leaf"${kpAttr}>${renderScalar(value)}</div>`;
}

function renderStructured(parsed, format) {
  // Root container carries data-key-path="" (the document root); children get
  // real paths. renderNode handles the empty root path correctly.
  const body = renderNode(parsed, '');
  return `<div class="annotate-render annotate-struct annotate-${format}">${body}</div>`;
}

// ===========================================================================
// CSV / TSV  ->  data-cell="<col-letter><row-number>" (spreadsheet address)
// ===========================================================================

// RFC-4180 CSV tokenizer: handles quoted fields, escaped quotes (""), and commas
// / newlines embedded inside quotes. Row 1 = the first row (header included),
// columns are spreadsheet letters (A, B, ... Z, AA, ...) — so data-cell="B7" is
// column B, row 7, matching the §5.2 example.

function parseDelimited(text, delimiter) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else { inQuotes = false; }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') { inQuotes = true; continue; }
    if (ch === delimiter) { row.push(field); field = ''; continue; }
    if (ch === '\r') { continue; } // fold CRLF into LF
    if (ch === '\n') { row.push(field); rows.push(row); row = []; field = ''; continue; }
    field += ch;
  }
  // Flush the final field/row unless the input ended exactly on a row boundary.
  if (field !== '' || row.length > 0) { row.push(field); rows.push(row); }
  return rows;
}

function columnLetter(index) {
  let n = index;
  let s = '';
  do {
    s = String.fromCharCode(65 + (n % 26)) + s;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return s;
}

function renderCSV(content, delimiter) {
  const rows = parseDelimited(content, delimiter);
  const parts = ['<table class="annotate-render annotate-csv">'];
  rows.forEach((cols, r) => {
    const tag = r === 0 ? 'th' : 'td';
    const cells = cols.map((cell, c) => {
      const addr = `${columnLetter(c)}${r + 1}`;
      return `<${tag} data-cell="${addr}">${escapeHtml(cell)}</${tag}>`;
    });
    parts.push(`<tr>${cells.join('')}</tr>`);
  });
  parts.push('</table>');
  return parts.join('');
}

// ===========================================================================
// Format detection + dispatch
// ===========================================================================

const MARKDOWN_EXTS = new Set(['.md', '.markdown', '.mdown', '.mkd', '.mkdn', '.markdn']);

function detectFormat(ext) {
  const e = ext.toLowerCase();
  if (MARKDOWN_EXTS.has(e)) return 'markdown';
  if (e === '.json') return 'json';
  if (e === '.yaml' || e === '.yml') return 'yaml';
  if (e === '.toml') return 'toml';
  if (e === '.csv') return 'csv';
  if (e === '.tsv') return 'tsv';
  return 'code';
}

function render(snapshotPath, renderMode) {
  const content = fs.readFileSync(snapshotPath, 'utf8');

  // render-as-frontend is the sole pass-through: serve the snapshot HTML verbatim
  // for the browser to execute. No anchor stamping (§6.2).
  if (renderMode === 'render-as-frontend') {
    return { html: content };
  }

  const ext = path.extname(snapshotPath);
  const format = detectFormat(ext);

  let html;
  switch (format) {
    case 'markdown':
      html = renderMarkdown(content);
      break;
    case 'json':
      html = renderStructured(JSON.parse(content), 'json');
      break;
    case 'yaml':
      html = renderStructured(YAML.parse(content), 'yaml');
      break;
    case 'toml':
      html = renderStructured(TOML.parse(content), 'toml');
      break;
    case 'csv':
      html = renderCSV(content, ',');
      break;
    case 'tsv':
      html = renderCSV(content, '\t');
      break;
    case 'code':
    default:
      html = renderCode(content, ext);
      break;
  }

  return { html };
}

module.exports = {
  render,
  // Exported for unit tests / reuse by other server modules.
  detectFormat,
  columnLetter,
  parseDelimited,
};
