#!/usr/bin/env python3
"""Generate images via Google's Gemini image API (Nano Banana / Imagen).

Stdlib-only by design — the surface we need is one POST + a base64 decode, so
this hand-rolls urllib rather than pulling the google-genai SDK (protobuf /
grpc / google-auth). Auth is an AI Studio API key passed as the ``?key=`` query
param (the consumer developer path), not Vertex/ADC.

Two endpoint shapes, dispatched by model family:

- **Gemini image models** (Nano Banana family) — ``:generateContent``. Accept a
  text prompt plus reference images (a style anchor and/or subject anchors) and
  return inline image data. This is the consistency engine — it can take a
  style reference (transfer look, ignore subjects) and a subject reference
  (hold a character's identity) at once.
- **Imagen models** — ``:predict``. Text-only prompt, no reference images,
  ``sampleCount`` for batches. Cheap fire-and-forget.

API key resolution, most specific wins:

1. ``--api-key`` flag
2. ``.agents/PROJECT_SECRETS.md`` — found by walking up from cwd; entries look
   like ``- **GEMINI_API_KEY** = `value` `` (plain ``KEY=value`` lines work too)
3. ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` environment variables
4. ``~/.config/image-gen/env`` — machine-wide default, ``KEY=value`` lines
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Friendly aliases → API model ids. Unknown values pass through untouched, so a
# raw id from `--list-models` always works even if this map goes stale.
MODELS = {
    "nano-banana": "gemini-2.5-flash-image",
    "nano-banana-2": "gemini-3.1-flash-image",
    "nano-banana-lite": "gemini-3.1-flash-lite-image",
    "nano-banana-pro": "gemini-3-pro-image",
    "imagen-4-fast": "imagen-4.0-fast-generate-001",
    "imagen-4": "imagen-4.0-generate-001",
    "imagen-4-ultra": "imagen-4.0-ultra-generate-001",
}
DEFAULT_MODEL = "nano-banana-2"
KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
CONFIG_FILE = Path.home() / ".config" / "image-gen" / "env"


class ImageGenError(RuntimeError):
    pass


# ---------------------------------------------------------------- key lookup

def _parse_kv(text: str) -> dict:
    """Parse both PROJECT_SECRETS.md bullets (- **KEY** = `value`) and plain
    KEY=value lines, so the same reader covers the secrets file and env files."""
    out = {}
    for m in re.finditer(r"\*\*([A-Z][A-Z0-9_]*)\*\*\s*=\s*`([^`]+)`", text):
        out[m.group(1)] = m.group(2).strip()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip().removeprefix("export ").strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", k):
            out.setdefault(k, v.strip().strip('"').strip("'"))
    return out


def _find_project_secrets() -> Path | None:
    d = Path.cwd().resolve()
    for p in (d, *d.parents):
        f = p / ".agents" / "PROJECT_SECRETS.md"
        if f.is_file():
            return f
    return None


def resolve_key(explicit: str | None = None) -> str:
    """Most specific wins: flag > project secrets > environment > ~/.config."""
    if explicit:
        return explicit
    secrets = _find_project_secrets()
    if secrets:
        kv = _parse_kv(secrets.read_text(encoding="utf-8"))
        for name in KEY_NAMES:
            if kv.get(name):
                return kv[name]
    for name in KEY_NAMES:
        if os.environ.get(name):
            return os.environ[name]
    if CONFIG_FILE.is_file():
        kv = _parse_kv(CONFIG_FILE.read_text(encoding="utf-8"))
        for name in KEY_NAMES:
            if kv.get(name):
                return kv[name]
    raise ImageGenError(
        "no API key found — checked .agents/PROJECT_SECRETS.md (walking up from "
        f"cwd), ${'/$'.join(KEY_NAMES)} in the environment, and {CONFIG_FILE}. "
        "Add GEMINI_API_KEY to any of those tiers.")


# ------------------------------------------------------------------ API core

def resolve_model(alias: str) -> str:
    return MODELS.get(alias, alias)


def _is_imagen(model_id: str) -> bool:
    return model_id.startswith("imagen")


def _image_part(path) -> dict:
    data = Path(path).read_bytes()
    p = str(path).lower()
    mime = ("image/jpeg" if p.endswith((".jpg", ".jpeg"))
            else "image/webp" if p.endswith(".webp")
            else "image/png")
    return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}


def _gemini_payload(prompt, style_ref, refs) -> dict:
    """Text first, then the style anchor, then subject anchors. The prompt names
    which reference is which ("in the style of the first image, the character of
    the second"); ordering is just a stable convention for the prompt to lean on."""
    parts = [{"text": prompt}]
    if style_ref:
        parts.append(_image_part(style_ref))
    for r in refs:
        parts.append(_image_part(r))
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }


def _post(url, body, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    return _send(req, timeout)


def _send(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise ImageGenError(f"{e.code} {e.reason}: {detail}") from None
    except urllib.error.URLError as e:
        raise ImageGenError(f"network error: {e.reason}") from None


def _extract_gemini_images(data) -> list:
    images = []
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                images.append({
                    "bytes": base64.b64decode(inline["data"]),
                    "mime": inline.get("mimeType") or inline.get("mime_type", "image/png"),
                })
    if not images:
        raise ImageGenError(f"no image in response: {json.dumps(data)[:400]}")
    return images


def generate(prompt, *, model, api_key, style_ref=None, refs=(), n=1, timeout=120):
    """Generate image(s); return ``[{"bytes": ..., "mime": ...}]``.

    ``model`` may be an alias (see ``MODELS``) or a raw API id. ``style_ref`` and
    ``refs`` are image file paths and are Gemini-only — Imagen rejects them.
    Raises ``ImageGenError`` on any API or transport failure."""
    model_id = resolve_model(model)
    refs = list(refs or [])

    if _is_imagen(model_id):
        if style_ref or refs:
            raise ImageGenError(
                f"{model} (Imagen) takes no reference images — use a Nano Banana model")
        url = f"{API_BASE}/models/{model_id}:predict?key={api_key}"
        body = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": n}}
        data = _post(url, body, timeout)
        return [{"bytes": base64.b64decode(p["bytesBase64Encoded"]),
                 "mime": p.get("mimeType", "image/png")}
                for p in data.get("predictions", [])]

    url = f"{API_BASE}/models/{model_id}:generateContent?key={api_key}"
    body = _gemini_payload(prompt, style_ref, refs)
    out = []
    for _ in range(max(1, n)):  # generateContent yields one image per call
        out.extend(_extract_gemini_images(_post(url, body, timeout)))
    return out


def list_models(api_key, timeout=30) -> list:
    """The model catalog — doubles as a key / connectivity smoke test (no image
    credits spent)."""
    url = f"{API_BASE}/models?key={api_key}&pageSize=1000"
    data = _send(urllib.request.Request(url, method="GET"), timeout)
    return [{"id": m.get("name", "").split("/")[-1],
             "methods": m.get("supportedGenerationMethods", [])}
            for m in data.get("models", [])]


# ----------------------------------------------------------------------- CLI

def ext_for_mime(mime: str) -> str:
    return {"image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="imagegen",
        description="Generate images with Gemini (Nano Banana) / Imagen models.")
    ap.add_argument("prompt", nargs="?", help="text prompt describing the image")
    ap.add_argument("-m", "--model", default=DEFAULT_MODEL,
                    help=f"model alias or raw id (default: {DEFAULT_MODEL}; "
                         f"aliases: {', '.join(MODELS)})")
    ap.add_argument("-o", "--out", default=".",
                    help="output file (single image) or directory (default: cwd)")
    ap.add_argument("--name", help="basename for output files (default: img-<timestamp>)")
    ap.add_argument("-n", type=int, default=1, help="number of images (default 1)")
    ap.add_argument("--style-ref", help="style reference image (Nano Banana only)")
    ap.add_argument("--ref", action="append", default=[],
                    help="subject reference image, repeatable (Nano Banana only)")
    ap.add_argument("--api-key", help="explicit API key (overrides all lookup tiers)")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--list-models", action="store_true",
                    help="list image-capable models and exit (also a free key smoke test)")
    args = ap.parse_args(argv)

    try:
        key = resolve_key(args.api_key)

        if args.list_models:
            for m in list_models(key, timeout=args.timeout):
                if any("image" in meth.lower() or "predict" in meth.lower()
                       for meth in m["methods"]) or "image" in m["id"]:
                    print(f"{m['id']}  ({', '.join(m['methods'])})")
            return 0

        if not args.prompt:
            ap.error("a prompt is required unless --list-models is given")

        images = generate(args.prompt, model=args.model, api_key=key,
                          style_ref=args.style_ref, refs=args.ref,
                          n=args.n, timeout=args.timeout)

        out = Path(args.out)
        single_file = out.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        if single_file:
            out.parent.mkdir(parents=True, exist_ok=True)
        else:
            out.mkdir(parents=True, exist_ok=True)
        base = args.name or (out.stem if single_file
                             else f"img-{datetime.now():%Y%m%d-%H%M%S}")

        for i, img in enumerate(images):
            if single_file and len(images) == 1:
                path = out
            else:
                stem = base if len(images) == 1 else f"{base}-{i + 1}"
                folder = out.parent if single_file else out
                path = folder / f"{stem}{ext_for_mime(img['mime'])}"
            path.write_bytes(img["bytes"])
            print(path)
        return 0
    except ImageGenError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
