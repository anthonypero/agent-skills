# annotate

Contextual review for command-line agents, on the [Agent Skills](https://agentskills.io) open standard. The agent opens any local artifact — a Markdown plan, a code file, a live localhost page, a generated image, a PDF — in a browser; the human marks it up with comments and edits **anchored to exact regions**; they hit send; and the anchored feedback returns to the agent in-band over a filesystem feedback loop. The whole system reduces to one primitive: *anchor a comment to a region of an arbitrary artifact, then serialize all anchors back to the agent.*

## The inversion

Annotate is an agentic skill that **dispatches no LLM agents**. There is no `agents/` directory, no backend/tier config, no orchestration loop. The **human** reviews and the **host assistant** (the model already in the conversation) revises. What it inherits from the Agent Skills framework is the portability-and-discipline subset: a standard `SKILL.md` entry, a fat shell launch script with a thin instruction file, a single-disk-authority server, atomic round files, and one-command bootstrap. Every design choice follows from that inversion — see `pm/technical-requirements.md` §1–§2.

The loop is structurally human-in-the-loop: every agent action produces a fresh presentation the human must look at before they can accept, and accept is head-checked so an unseen round can never be finalized. "Fire and walk away" is impossible by construction.

## Install

```sh
sh install.sh
```

One command brings up the whole stack: the `~/.annotate` data dir, the generated `runtime.json`, a provisioned Chrome-for-Testing (or a reused system Chromium), the dedicated browser profile, the lazy-singleton Node server, and the unpacked extension (heartbeat-verified). Node is the only hard runtime dependency beyond a POSIX shell. Setup is agent-driven and probe-and-degrade: it attempts everything automatically and prints a short checklist only for the steps a given browser/OS refuses. Re-running is a no-op; `annotate setup` re-provisions for repair.

## Architecture

Five runtime pieces, glued by a thin instruction file:

- **`SKILL.md`** — the standardized entrypoint the host assistant reads. Thin orchestration.
- **`bin/annotate`** — POSIX `sh` launch script. Owns mode + timeout; snapshots the artifact, writes the one round stub, starts the server, opens the browser, blocks/polls, and prints the `{source,snapshot,feedback}` bundle on stdout.
- **`server/`** — Node HTTP server. The single disk authority: serves the chrome + target, renders text artifacts into position-annotated DOM, and owns every round-file mutation.
- **`extension/`** — MV3 content script in a dedicated profile. Three anchor adapters (DOM/line, code/line, image point+box), the comment/edit bubble, submit, accept, head auto-advance. POSTs only; never writes disk.
- **`schemas/`** — `round.schema.json` and `feedback.schema.json` make the data-model constraints machine-checkable.

On disk: `~/.annotate/<session>/<artifact>/<guid>/` holds each round's frozen snapshot, its `{source,snapshot,status,feedback}` descriptor, and an optional screenshot. The `<timestamp>`-prefixed `<guid>` makes `ls` order chronological, so history and "what did this look like N rounds ago" read straight off disk — reliable across a cold resume or context compaction.

## Testing

```sh
npm test                                  # unit + schema + render golden tests (no browser)
node tests/integration/extension-gate.js  # real extension in CfT: anchor → submit → accept (sandbox off)
node tests/integration/image-gate.js      # image anchors, screenshot gating, auto-advance (sandbox off)
node tests/integration/setup-gate.js      # one-command setup + the real load probe (sandbox off)
node tests/integration/e2e-gate.js        # the FULL loop via the real CLI across 3 formats (sandbox off)
```

The integration gates drive the **real** Chrome-for-Testing over the Chrome DevTools Protocol and assert on the shared DOM and on disk — exactly how a human's clicks would drive it, minus the pixels. They need a `--load-extension`-honoring browser and bind loopback HTTP, so run them with any command sandbox disabled.

---

## Future maintenance / may need updating

Parts of this stack track **moving external targets** the maintainer must re-verify. This section is the first place to look when setup breaks after a browser update.

### Browser extension-loading (the volatile one)

The runtime depends on launching a **Chromium-family browser that still honors the `--load-extension` command-line switch**. This is a moving target that Google has been tightening:

- **Branded Google Chrome can no longer CLI-load extensions.** Chrome **137** (May 2025) removed `--load-extension`; Chrome **142** (Oct 2025) removed the `--disable-features=DisableLoadExtensionCommandLineSwitch` workaround too. On branded Chrome the only paths left are the manual `chrome://extensions` → Load Unpacked UI, or the heavier CDP / WebDriver-BiDi load paths (`--remote-debugging-pipe` + `--enable-unsafe-extension-debugging`, not drop-in).
- **Chrome for Testing and Chromium still honor `--load-extension`**, so they are the supported target. Setup prefers **Chrome for Testing** and falls back to a reused system Chromium. Branded Chrome is a degraded, manual-only fallback.

**If those builds ever tighten too**, the documented fallbacks, in order of preference, are: the manual `chrome://extensions` → Load Unpacked step; the Chrome DevTools Protocol / WebDriver BiDi extension-load path; or pinning a known-good older browser version. The launch invocation and the load probe live in `bin/annotate` (`open_browser`) and `server/setup.js`; the version history above is recorded in `pm/technical-requirements.md` §7 and §6.6.

### Chrome-for-Testing provisioning (the installer + the pinned version)

Setup downloads Chrome for Testing via **`@puppeteer/browsers`** (a committed dependency) into `~/.annotate/` and **pins the resolved `buildId`** into `runtime.json` (`cftBuildId`) for reproducibility. `annotate setup` re-pins to the current stable. Watch points:

- The `@puppeteer/browsers` API or its download endpoints can change — verify after a major bump.
- **No `linux-arm64` CfT build is published yet** (expected ~Q2 2026); on arm64 Linux setup uses the system-Chromium reuse path and skips the download. Revisit once Google ships that build.
- On macOS the programmatic fetch avoids the Gatekeeper quarantine prompt (CfT is Google-signed, and a non-GUI download does not stamp `com.apple.quarantine`); a hardened box may still need `xattr -cr` as the documented degrade step.

### The nano-banana image-mask spike (gated, deferred)

The image adapter ships normalized point/box anchors regardless. Whether a box becomes a *surgical mask* is gated on a build spike against the local image-gen pipeline's API: if it accepts a region-mask param, the box becomes a mask; otherwise it stays spatial steering (full regenerate with the region described in-prompt). The downstream consumer calls a fixed stub (`regionEdit(image, normalizedBox, instruction)`); only the stub's body is gated. Re-verify if the image pipeline's API changes. See `pm/technical-requirements.md` §7 and §10 (open question 3).
