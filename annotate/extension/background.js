'use strict';

// Background service worker — the screenshot-capture SEAM (tech-requirements §6.4, §5.5).
//
// chrome.tabs.captureVisibleTab must run in the extension (service-worker) context, NOT a
// content script, so the content script messages here when the screenshot toggle is on for a
// VISUAL view (live frontend / Markdown-rendered-to-HTML). Capture is GATED (§6.4): NONE for
// source/text/code views (the T6a exit-gate views) and images; only rendered visual views,
// when the persistent toggle (chrome.storage.local, default on) is on. So in T6a the content
// script sends screenshot:null and this worker is the seam T6b wires the visual-view capture to.
//
// Returns the base64 PNG payload (data: prefix stripped) the server decodes into
// <guid>-screenshot.png (§5.5 — base64 on the wire only, never inlined in round.json).

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (!msg || msg.type !== 'annotate-capture') return false;
  try {
    const windowId = sender && sender.tab ? sender.tab.windowId : undefined;
    chrome.tabs.captureVisibleTab(windowId, { format: 'png' }, function (dataUrl) {
      if (chrome.runtime.lastError || !dataUrl) {
        sendResponse({
          ok: false,
          error: String((chrome.runtime.lastError && chrome.runtime.lastError.message) || 'capture-failed'),
        });
        return;
      }
      const b64 = dataUrl.replace(/^data:image\/png;base64,/, '');
      sendResponse({ ok: true, screenshot: b64 });
    });
    return true; // keep the message channel open for the async sendResponse
  } catch (e) {
    sendResponse({ ok: false, error: String(e && e.message) });
    return false;
  }
});
