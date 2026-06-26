'use strict';

// Content-script orchestrator (tech-requirements §6.4; T6a). Wires the T3 logic modules
// (Annotate.dom / .code / .bubble / .submit) and the T6a config module (Annotate.config)
// into a live review surface over the server-rendered artifact:
//
//   on load   -> resolve { session, artifact, head, token, origin } from the server-injected
//                config (Annotate.config), fire the one-time /loaded heartbeat (§6.6 / S0),
//                inject the REQUIRED top "Annotate chrome" bar (accept + send + format badge +
//                Copy + expand + reserved version slot), and start the 1s head auto-advance poll.
//   click     -> read the renderer's stamped position attribute off the clicked node
//                (Annotate.code for code views, Annotate.dom otherwise) -> a §5.2 anchor;
//                open the comment/edit composer anchored to it.
//   select    -> the REFS selection-pill affordance ("highlight to add a comment").
//   add       -> Annotate.bubble draft -> a right-margin comment card.
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
// (-> a .annotate-card[data-anchor-line]); after send/accept the chrome carries
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

  let ctx = null;
  let feedbackSink = null;
  const drafts = []; // §5.2 items (no id; ids assigned at submit by submit.js)
  let pending = null; // { bubble, anchor, element } while a composer is open
  let revertTarget = null; // the §5.5 revertTarget; null until the version UI (T6b) sets it
  let submitted = false;
  let lastHeadInfo = null;

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

  function setStatus(msg) {
    const s = doc.querySelector('.annotate-status');
    if (s) s.textContent = msg;
  }

  function chrome() {
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
    if (anchor.line != null) return 'Line ' + anchor.line;
    if (anchor.keyPath != null) return anchor.keyPath === '' ? '(root)' : anchor.keyPath;
    if (anchor.cell != null) return 'Cell ' + anchor.cell;
    if (anchor.point) return 'Point';
    if (anchor.box) return 'Region';
    return anchor.kind || '';
  }

  // ---------------------------------------------------------------------------
  // the REQUIRED top "Annotate chrome" bar (§6.4 + REFS.md)
  // ---------------------------------------------------------------------------

  function buildChrome() {
    const view = detectView();
    const bar = el('div', { id: 'annotate-chrome', class: 'annotate-ui annotate-chrome' }, [
      el('div', { class: 'annotate-brand' }, [
        el('span', { class: 'annotate-logo', text: 'annotate' }),
        el('span', { class: 'annotate-badge', text: view.badge }),
        el('span', { class: 'annotate-title', text: ctx.artifact }),
      ]),
      el('div', { class: 'annotate-actions' }, [
        // Reserved slot for the deferred revert/version dropdown (PRD §6 — seam only in v1).
        el('div', { class: 'annotate-version-slot', title: 'version history (coming soon)', text: 'v ▾' }),
        el('button', { class: 'annotate-btn annotate-copy', type: 'button', onclick: onCopy, text: 'Copy' }),
        el('button', { class: 'annotate-btn annotate-expand', type: 'button', onclick: onExpand, text: 'Expand' }),
        el('button', {
          class: 'annotate-btn annotate-send',
          type: 'button',
          onclick: function () { send(); },
          text: 'Send feedback (0)',
        }),
        el('button', {
          class: 'annotate-btn annotate-primary annotate-accept',
          type: 'button',
          onclick: function () { accept(); },
          text: 'Accept',
        }),
      ]),
      el('div', { class: 'annotate-status', text: 'Ready — click a line or select text to annotate' }),
    ]);
    doc.body.appendChild(bar);
    doc.body.classList.add('annotate-has-chrome');

    // Right rail that holds the margin comment cards (REFS: cards aligned to the right).
    doc.body.appendChild(el('div', { class: 'annotate-ui annotate-rail', id: 'annotate-rail' }, []));
  }

  function updateSendCount() {
    const b = doc.querySelector('.annotate-send');
    if (b) b.textContent = 'Send feedback (' + drafts.length + ')';
  }

  function onCopy() {
    const target = doc.querySelector('.annotate-render') || doc.body;
    const text = target.innerText || '';
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => setStatus('Copied'), () => setStatus('Copy blocked'));
      } else {
        setStatus('Copy unavailable');
      }
    } catch (e) {
      setStatus('Copy unavailable');
    }
  }

  function onExpand() {
    doc.body.classList.toggle('annotate-expanded');
  }

  // ---------------------------------------------------------------------------
  // composer (click/selection -> comment/edit on one anchor)
  // ---------------------------------------------------------------------------

  function closeComposer() {
    const c = doc.querySelector('.annotate-composer');
    if (c) c.remove();
    pending = null;
  }

  // Open the comment/edit composer for an anchor. `selectedText` seeds the edit box.
  function openComposer(opts) {
    closeComposer();
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
    });
    const card = el('div', {
      class: 'annotate-ui annotate-composer',
      'data-anchor-kind': anchor.kind,
    }, []);
    if (anchor.line != null) card.setAttribute('data-anchor-line', String(anchor.line));
    if (anchor.keyPath != null) card.setAttribute('data-anchor-keypath', anchor.keyPath);
    if (anchor.cell != null) card.setAttribute('data-anchor-cell', anchor.cell);

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
    });

    const addBtn = el('button', {
      class: 'annotate-btn annotate-primary annotate-add',
      type: 'button',
      text: 'Add',
    });
    addBtn.addEventListener('click', function () {
      if (bubble.type === 'comment') bubble.setComment(input.value);
      else bubble.setReplacement(replInput.value);
      if (!bubble.isComplete()) {
        setStatus(bubble.type === 'comment' ? 'Enter a comment first' : 'Enter a replacement first');
        return;
      }
      addDraft(bubble.toFeedback());
      closeComposer();
    });

    const cancelBtn = el('button', {
      class: 'annotate-btn annotate-cancel',
      type: 'button',
      text: 'Cancel',
      onclick: closeComposer,
    });

    card.appendChild(el('div', { class: 'annotate-composer-head' }, [
      el('span', { class: 'annotate-anchor-label', text: anchorLabel(anchor) }),
      toggleBtn,
    ]));
    card.appendChild(input);
    card.appendChild(replInput);
    card.appendChild(el('div', { class: 'annotate-composer-foot' }, [cancelBtn, addBtn]));

    positionNear(card, opts.element);
    doc.body.appendChild(card);
    renderMode();
    input.focus();
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
  // drafts + margin cards
  // ---------------------------------------------------------------------------

  function addDraft(item) {
    drafts.push(item);
    renderCard(item);
    updateSendCount();
    setStatus(drafts.length + ' annotation' + (drafts.length === 1 ? '' : 's') + ' staged — Send when ready');
  }

  function renderCard(item) {
    const rail = doc.getElementById('annotate-rail');
    if (!rail) return;
    const a = item.anchor || {};
    const card = el('div', {
      class: 'annotate-card annotate-card-' + item.type,
      'data-anchor-kind': a.kind,
    }, []);
    if (a.line != null) card.setAttribute('data-anchor-line', String(a.line));
    if (a.keyPath != null) card.setAttribute('data-anchor-keypath', a.keyPath);
    if (a.cell != null) card.setAttribute('data-anchor-cell', a.cell);
    card.appendChild(el('div', { class: 'annotate-card-head' }, [
      el('span', { class: 'annotate-card-type', text: item.type }),
      el('span', { class: 'annotate-card-anchor', text: anchorLabel(a) }),
    ]));
    const bodyText = item.type === 'comment' ? item.comment : '→ ' + item.replacement;
    card.appendChild(el('div', { class: 'annotate-card-body', text: bodyText }));
    rail.appendChild(card);
  }

  // ---------------------------------------------------------------------------
  // interactions (click + selection)
  // ---------------------------------------------------------------------------

  function onClick(ev) {
    const t = ev.target;
    if (inUi(t)) return; // chrome / composer / cards manage their own clicks
    // A non-collapsed selection is handled by the selection-pill path; a bare click anchors
    // to the clicked element/line.
    const sel = root.getSelection ? root.getSelection() : null;
    if (sel && !sel.isCollapsed && String(sel).trim()) return;
    const anchor = anchorFor(t);
    if (!anchor) return;
    ev.preventDefault();
    const lineEl = (A.code && A.code.lineElementFor(t)) || (A.dom && A.dom.nearestAnchorable(t)) || t;
    openComposer({ anchor, element: lineEl, selectedText: (lineEl.innerText || lineEl.textContent || '').trim() });
  }

  // Selection-pill affordance (REFS "highlight to add a comment").
  function onMouseUp(ev) {
    if (inUi(ev.target)) return;
    const sel = root.getSelection ? root.getSelection() : null;
    removePill();
    if (!sel || sel.isCollapsed) return;
    const text = String(sel).trim();
    if (!text) return;
    const node = sel.anchorNode;
    const host = node && (node.nodeType === 1 ? node : node.parentElement);
    if (!host || inUi(host)) return;
    const anchor = anchorFor(host);
    if (!anchor) return;
    showPill(sel, anchor, text, host);
  }

  function removePill() {
    const p = doc.querySelector('.annotate-pill');
    if (p) p.remove();
  }

  function showPill(sel, anchor, text, host) {
    let rect;
    try {
      rect = sel.getRangeAt(0).getBoundingClientRect();
    } catch (e) {
      rect = host.getBoundingClientRect();
    }
    const pill = el('div', { class: 'annotate-ui annotate-pill' }, [
      el('span', { class: 'annotate-pill-label', text: 'Highlight to add a comment' }),
      el('button', {
        class: 'annotate-btn annotate-pill-add',
        type: 'button',
        text: '+',
        onclick: function () {
          removePill();
          openComposer({ anchor, element: host, selectedText: text });
        },
      }),
    ]);
    pill.style.position = 'absolute';
    pill.style.top = Math.max(64, rect.top + (root.scrollY || 0) - 40) + 'px';
    pill.style.left = rect.left + (root.scrollX || 0) + 'px';
    doc.body.appendChild(pill);
  }

  function wireInteractions() {
    doc.addEventListener('click', onClick, true);
    doc.addEventListener('mouseup', onMouseUp, false);
  }

  // ---------------------------------------------------------------------------
  // submit + accept (the round-trip)
  // ---------------------------------------------------------------------------

  async function send() {
    const bar = chrome();
    if (!drafts.length) {
      setStatus('Nothing to send — add an annotation first');
      if (bar) bar.setAttribute('data-last-submit', 'empty');
      return { error: 'empty' };
    }
    setStatus('Sending…');
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
          screenshot: null, // gated OFF for source/text/code views (§6.4); visual-view capture is T6b
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
    } else if (resp.httpStatus === 409 || resp.error === 'stale-head') {
      setStatus('This round was superseded — advancing to the new round');
      if (bar) bar.setAttribute('data-last-submit', 'stale');
      await maybeAdvance(resp.head);
    } else {
      setStatus('Submit error (' + (resp.httpStatus || '?') + '): ' + (resp.error || 'unknown'));
      if (bar) bar.setAttribute('data-last-submit', 'error');
    }
    return resp;
  }

  async function accept() {
    const bar = chrome();
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
      doc.body.classList.add('annotate-accepted');
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
  // head auto-advance poll (§6.4) — reloads on a head GUID change, guarding in-progress
  // work; reflects an in-place pending->submitted/accepted status flip without a reload.
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
      await maybeAdvance(info.head);
    } else if (info.status && info.status !== 'pending') {
      reflectStatus(info.status);
    }
  }

  function reflectStatus(status) {
    const bar = chrome();
    if (bar) bar.setAttribute('data-round-status', status);
  }

  async function maybeAdvance(newHead) {
    // Don't silently discard in-progress work (§6.4): warn and hold.
    if (pending || drafts.length) {
      setStatus('A new round is available — send or discard your annotations to advance');
      const bar = chrome();
      if (bar) bar.setAttribute('data-pending-advance', newHead || '1');
      return;
    }
    // Terminal accepted head: stop here, show the accepted state (§6.3).
    if (lastHeadInfo && lastHeadInfo.status === 'accepted' && newHead === ctx.head) {
      reflectStatus('accepted');
      return;
    }
    root.location.reload();
  }

  function startPolling() {
    root.setInterval(pollHead, 1000);
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
    send: send,
    accept: accept,
    anchorFor: anchorFor,
  };

  if (doc.readyState === 'loading') {
    doc.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
