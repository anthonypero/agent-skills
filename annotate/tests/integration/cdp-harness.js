'use strict';

// Shared Chrome-for-Testing + CDP harness for the integration gates (T6a extension-gate.js,
// T6b image-gate.js). Factored out of the T6a gate so the T6b gate EXTENDS the same proven
// machinery (drive the REAL extension in the provisioned CfT over the Chrome DevTools
// Protocol, assert on the SHARED DOM + ON DISK) rather than re-inventing it.
//
// CDP Runtime.evaluate runs in the PAGE MAIN WORLD, which cannot see the content script's JS
// globals (MV3 isolated world), so a gate drives the UI exclusively through the shared DOM
// (real .click()/dispatchEvent, reading the [data-*] result attributes content.js stamps).
//
// The Bash sandbox blocks loopback HTTP (false ECONNREFUSED) — run any gate that uses this
// with the sandbox disabled.

const fs = require('node:fs');
const path = require('node:path');
const { spawn, execSync } = require('node:child_process');

const PKG_ROOT = path.join(__dirname, '..', '..');
const EXT_DIR = path.join(PKG_ROOT, 'extension');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(label, fn, { timeout = 30000, interval = 200 } = {}) {
  const deadline = Date.now() + timeout;
  let last;
  for (;;) {
    try {
      last = await fn();
      if (last) return last;
    } catch (e) {
      last = e;
    }
    if (Date.now() >= deadline) throw new Error(`timeout waiting for ${label} (last: ${JSON.stringify(last)})`);
    await sleep(interval);
  }
}

// Locate the Chrome-for-Testing binary downloaded by the S0 spike (or ANNOTATE_CFT).
function findCft() {
  if (process.env.ANNOTATE_CFT && fs.existsSync(process.env.ANNOTATE_CFT)) return process.env.ANNOTATE_CFT;
  const cache = path.join(PKG_ROOT, '.spike', 'cache');
  const out = execSync(`find "${cache}" -type f -name 'Google Chrome for Testing' 2>/dev/null | head -1`)
    .toString()
    .trim();
  if (!out) throw new Error(`no Chrome for Testing under ${cache} — run @puppeteer/browsers install chrome@stable`);
  return out;
}

// Launch CfT with the dedicated profile + unpacked extension + remote debugging, opening
// `url`. Headful by default (the S0-proven path); ANNOTATE_HEADLESS=1 forces headless=new.
function launchCft({ cft, profileDir, debugPort, url, extDir = EXT_DIR }) {
  const args = [
    `--user-data-dir=${profileDir}`,
    `--load-extension=${extDir}`,
    `--disable-extensions-except=${extDir}`,
    `--remote-debugging-port=${debugPort}`,
    '--remote-allow-origins=*',
    '--no-first-run',
    '--no-default-browser-check',
    '--no-service-autorun',
    '--disable-background-networking',
  ];
  if (process.env.ANNOTATE_HEADLESS) args.push('--headless=new');
  args.push(url);
  return spawn(cft, args, { stdio: 'ignore' });
}

// ---- minimal CDP client over the built-in WebSocket (Node 22+) ----------------
function cdpConnect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
    let nextId = 1;
    const send = (method, params) =>
      new Promise((res, rej) => {
        const id = nextId++;
        pending.set(id, { res, rej });
        ws.send(JSON.stringify({ id, method, params: params || {} }));
      });
    const evaluate = async (expression) => {
      const r = await send('Runtime.evaluate', {
        expression,
        awaitPromise: true,
        returnByValue: true,
      });
      if (r.exceptionDetails) {
        throw new Error('eval exception: ' + JSON.stringify(r.exceptionDetails.exception || r.exceptionDetails));
      }
      return r.result.value;
    };
    ws.addEventListener('open', () => resolve({ ws, send, evaluate, close: () => ws.close() }));
    ws.addEventListener('error', () => reject(new Error('CDP websocket error: ' + wsUrl)));
    ws.addEventListener('message', (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) rej(new Error(JSON.stringify(msg.error)));
        else res(msg.result);
      }
    });
  });
}

// Find the page target for a served path (e.g. "/<session>/<artifact>") and connect CDP.
async function connectPage(debugPort, matchPath) {
  const target = await waitFor('CfT page target', async () => {
    const res = await fetch(`http://127.0.0.1:${debugPort}/json/list`);
    const list = await res.json();
    return list.find((t) => t.type === 'page' && t.url && t.url.indexOf(matchPath) >= 0);
  });
  const cdp = await cdpConnect(target.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  return cdp;
}

module.exports = { PKG_ROOT, EXT_DIR, sleep, waitFor, findCft, launchCft, cdpConnect, connectPage };
