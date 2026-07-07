---
name: image-gen
description: "Generate images and visual assets with Google's Gemini image API (Nano Banana / Imagen models). Use whenever the user asks to generate, create, edit, or iterate on an image, illustration, icon, texture, character art, cover art, diagram mockup, or any other visual asset — even if they don't mention Gemini, Nano Banana, or AI image generation explicitly. Also use for style-consistent series (same character or same look across many images) via reference images."
---

# Image Gen

Generate images via Google's Gemini image API using the bundled stdlib-only script — no SDK, no dependencies beyond Python 3.10+.

## Before generating

Check the `resources/` folder of this skill for special instructions (house styles, project-specific presets, prompting guides). Read any file whose name matches the task at hand. `resources/prompting.md` has general prompt-writing guidance for Nano Banana.

## Usage

All commands run the bundled script (path relative to this SKILL.md):

```bash
python3 scripts/imagegen.py "PROMPT" [options]
```

Common invocations:

```bash
# Single image into the current directory
python3 scripts/imagegen.py "a watercolor fox reading a book" -o ./assets --name fox-reading

# Exact output file
python3 scripts/imagegen.py "flat vector icon of a paper airplane, white background" -o icon.png

# Batch of variants
python3 scripts/imagegen.py "seamless parchment texture" -n 4 -o ./textures

# Hold a character's identity across images (subject reference)
python3 scripts/imagegen.py "the character from the first image riding a horse" --ref character.png -o scene2.png

# Transfer a look (style reference) — combinable with --ref
python3 scripts/imagegen.py "in the style of the first image: a castle at dusk" --style-ref style-anchor.png -o castle.png

# Smoke-test the API key + list available image models (free, no image credits)
python3 scripts/imagegen.py --list-models
```

The script prints the saved file path(s), one per line. Failures print `error: ...` to stderr and exit 1.

## Models

| Alias             | API id                        | Notes                                              |
| ----------------- | ----------------------------- | -------------------------------------------------- |
| `nano-banana-2`   | `gemini-3.1-flash-image`      | **Default.** Fast, supports reference images       |
| `nano-banana-pro` | `gemini-3-pro-image`          | Highest quality Nano Banana                        |
| `nano-banana-lite`| `gemini-3.1-flash-lite-image` | Cheapest/fastest Nano Banana                       |
| `nano-banana`     | `gemini-2.5-flash-image`      | Previous generation                                |
| `imagen-4-fast`   | `imagen-4.0-fast-generate-001`| Cheap text-only batches (`-n`), no reference images|
| `imagen-4`        | `imagen-4.0-generate-001`     | Text-only                                          |
| `imagen-4-ultra`  | `imagen-4.0-ultra-generate-001`| Text-only, highest Imagen quality                 |

Pass `-m <alias>` or any raw API id (unknown values pass through untouched). Reference images (`--ref`, `--style-ref`) are Nano Banana only — Imagen rejects them.

## API key setup

The script resolves the key itself — most specific tier wins:

1. `--api-key` flag
2. **Project secrets**: `.agents/PROJECT_SECRETS.md`, found by walking up from cwd. Entry format: `` - **GEMINI_API_KEY** = `the-key` ``
3. **Environment**: `GEMINI_API_KEY` or `GOOGLE_API_KEY`
4. **Machine default**: `~/.config/image-gen/env` with a `GEMINI_API_KEY=the-key` line

This lets a project pin its own billing key while a machine-wide default covers everything else. If no key is found the script says exactly which tiers it checked — relay that to the user rather than guessing. Keys come from [Google AI Studio](https://aistudio.google.com/apikey).

## Where to save

Resolve the output location in this order:

1. **The user named a destination** — use it.
2. **Project instructions define an asset/image location** (CLAUDE.md, AGENTS.md, or a file in this skill's `resources/`) — use that.
3. **Otherwise default to `<project root>/.agents/assets/images/`** (pass it as `-o`; the script creates it). This is a staging area: when the user approves an image that belongs in the project proper, move it to its real home rather than leaving it in staging.

Without an explicit `-o` the script writes to the cwd — don't rely on that; always pass `-o`.

## Workflow notes

- **Look at what you made.** After generating, Read the output image to verify it matches the request before telling the user it's done. Iterate on the prompt if it missed.
- **Name outputs meaningfully** (`--name` or an explicit `-o file.png`); the timestamped default is for throwaway drafts.
- **Reference-image ordering matters**: the payload is text, then the style anchor, then subject refs. Write prompts that name them positionally ("in the style of the first image, the character of the second").
- `--list-models` is a free connectivity/key check — use it to debug auth before burning image credits on retries.
