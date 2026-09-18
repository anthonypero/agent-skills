#!/usr/bin/env python3
"""Dispatch one persona against one artifact through one model family, and land a validated report.

    dispatch.py --persona lens-fidelity --family openai \
                --artifact pm/technical-requirements.md \
                --ref pm/prd.md --ref .agents/ideas/seed.md \
                --out .agents/reviews/technical-requirements/2026-09-18-1

Writes `<run-dir>/<lens>-<family>.json` (the validated report plus a `_meta` block) and
`<run-dir>/<lens>-<family>.md` (its rendering), then prints a digest under 2000 characters.

Standard library only. The HTTP call itself lives in `backends/`, loaded by the provider's `type`,
so another access path is another file there rather than a change here.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from backends import load_driver  # noqa: E402
from lib import report as report_lib  # noqa: E402

DEFAULT_CONFIG = os.path.join(SKILL_DIR, "templates", "config.json")
FINDING_SCHEMA_REF = os.path.join(SKILL_DIR, "references", "finding-schema.md")
AGENTS_DIR = os.path.join(SKILL_DIR, "agents")
LP = os.path.join(SKILL_DIR, os.pardir, "lastpass", "scripts", "lp")

PROVIDER = "openrouter"


# --- secrets -----------------------------------------------------------------------------------

def resolve_api_key(config_entry):
    """LastPass vault first, then the environment. Fail loudly with both paths tried."""
    secret_name = config_entry.get("api_key_secret", "global/OPENROUTER_API_KEY")
    env_name = config_entry.get("api_key_env", "OPENROUTER_API_KEY")

    lp_path = os.path.normpath(LP)
    if os.path.isfile(lp_path) and os.access(lp_path, os.X_OK):
        try:
            result = subprocess.run([lp_path, "get", secret_name], capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip(), "lastpass:" + secret_name
        except (OSError, subprocess.SubprocessError):
            pass

    from_env = os.environ.get(env_name)
    if from_env and from_env.strip():
        return from_env.strip(), "env:" + env_name

    sys.exit(
        "No OpenRouter API key.\n"
        "  tried: {0} get {1}\n"
        "  tried: ${2} in the environment\n"
        "Put the key in the vault (`lp put {1}`) or export ${2}, then re-run.".format(lp_path, secret_name, env_name)
    )


# --- config ------------------------------------------------------------------------------------

def load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    if PROVIDER not in config:
        sys.exit("config {0} has no `{1}` provider entry".format(path, PROVIDER))
    return config


def resolve_model(config_entry, tier, family, override):
    if override:
        return override, tier
    tiers = config_entry.get("tiers") or {}
    if tier not in tiers:
        sys.exit("unknown tier '{0}'. Config offers: {1}".format(tier, ", ".join(sorted(tiers))))
    families = tiers[tier]
    if family not in families:
        sys.exit("family '{0}' is not in tier '{1}'. That tier offers: {2}".format(family, tier, ", ".join(sorted(families))))
    return families[family], tier


# --- prompt composition ------------------------------------------------------------------------

def repo_relative(path):
    """Path as the repo sees it, so two reviewers cite the same file by the same name."""
    absolute = os.path.abspath(path)
    directory = os.path.dirname(absolute)
    try:
        root = subprocess.run(["git", "-C", directory, "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=30)
        if root.returncode == 0 and root.stdout.strip():
            return os.path.relpath(absolute, root.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.relpath(absolute, os.getcwd())


def read_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def build_system_prompt(persona_body):
    finding_schema = read_file(FINDING_SCHEMA_REF)
    return persona_body.rstrip() + "\n\n---\n\n" + finding_schema.rstrip() + "\n"


def build_user_prompt(artifact_path, reference_paths):
    """Artifact and references inlined verbatim, delimited, each labelled by its repo-relative path."""
    blocks = []
    blocks.append(
        "You are reviewing ONE artifact. It is inlined below in full, together with the source-of-truth "
        "references you have been given. You have no tools and no file access: everything you may rely on "
        "is in this message. Cite locations and quote verbatim from these texts."
    )
    blocks.append("")

    if reference_paths:
        blocks.append("There are {0} reference document(s). Read them before the artifact.".format(len(reference_paths)))
        blocks.append("")
        for index, path in enumerate(reference_paths, start=1):
            rel = repo_relative(path)
            blocks.append("===== BEGIN REFERENCE {0} of {1}: {2} =====".format(index, len(reference_paths), rel))
            blocks.append(read_file(path).rstrip("\n"))
            blocks.append("===== END REFERENCE {0}: {1} =====".format(index, rel))
            blocks.append("")
    else:
        blocks.append("No reference documents were supplied. Say so in `method_notes` and review what you can.")
        blocks.append("")

    artifact_rel = repo_relative(artifact_path)
    blocks.append("===== BEGIN ARTIFACT UNDER REVIEW: {0} =====".format(artifact_rel))
    blocks.append(read_file(artifact_path).rstrip("\n"))
    blocks.append("===== END ARTIFACT: {0} =====".format(artifact_rel))
    blocks.append("")
    blocks.append("Review `{0}` now, under your lens, following your rubric.".format(artifact_rel))
    blocks.append(
        "Set `artifact` to `{0}` and `references` to {1}.".format(
            artifact_rel, json.dumps([repo_relative(p) for p in reference_paths])
        )
    )
    blocks.append("Return ONLY the JSON object. No prose before or after it. No code fence.")
    return "\n".join(blocks)


def build_repair_prompt(original_user_prompt, raw_output, errors):
    return "\n".join([
        original_user_prompt,
        "",
        "===== YOUR PREVIOUS RESPONSE =====",
        (raw_output or "")[:200000],
        "===== END PREVIOUS RESPONSE =====",
        "",
        "That response did not validate against the report schema. The validator reported:",
        "",
        "\n".join("- " + error for error in errors),
        "",
        "Re-emit the WHOLE report as one corrected JSON object. Keep every finding you already made and "
        "its substance; fix only what the validator named. Return ONLY the JSON object — no prose, no code fence.",
    ])


# --- http --------------------------------------------------------------------------------------

def make_http_request_fn(driver):
    def http_request_fn(url, headers, body_bytes, timeout):
        request = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.getcode(), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if exc.fp else ""
            raise driver.Rejected(exc.code, body)
    return http_request_fn


# --- attempt records ---------------------------------------------------------------------------

def _usage_cost(usage):
    try:
        cost = usage.get("cost")
        return float(cost) if cost is not None else None
    except (TypeError, ValueError):
        return None


def record_attempt(attempt_index, response, notes, errors):
    """One row of `_meta.attempts`: what was asked, what it cost, and why it did or did not validate."""
    usage = response.get("usage") or {}
    choice = (response.get("choices") or [{}])[0] or {}
    return {
        "attempt": attempt_index + 1,
        "errors": "ok" if not errors else list(errors),
        "usage": usage,
        "cost_usd": _usage_cost(usage),
        "finish_reason": choice.get("finish_reason"),
        "notes": list(notes or []),
        "response_id": response.get("id"),
    }


def build_meta(attempts, tier, model, key_source, elapsed):
    """The `_meta` block, built the same way whether the seat landed a report or failed."""
    costs = [a["cost_usd"] for a in attempts if a["cost_usd"] is not None]
    # Reasoning tokens bill as output and several frontier models cannot opt out of producing them,
    # so record them: they are often the larger half of a seat's cost and they are invisible in the
    # completion. v0 sends no reasoning/reasoning_effort parameter — the vocabularies differ by
    # vendor — so each model uses its own default and this is the only visibility we get.
    reasoning_tokens = sum(
        int(((a["usage"].get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
        for a in attempts
    )
    return {
        "tier": tier,
        "model": model,
        "provider": PROVIDER,
        "key_source": key_source,
        "elapsed_s": round(elapsed, 1),
        "repairs": max(len(attempts) - 1, 0),
        "usage": attempts[-1]["usage"] if attempts else {},
        "cost_usd": sum(costs) if costs else None,
        "reasoning_tokens": reasoning_tokens,
        "driver_notes": [note for a in attempts for note in a["notes"]],
        "attempts": attempts,
    }


# --- main --------------------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run one persona against one artifact through one model family.")
    parser.add_argument("--persona", required=True, help="Persona file name in agents/, with or without the .md suffix (e.g. lens-fidelity)")
    parser.add_argument("--family", required=True, help="Model family named in the config tier map (claude, openai, google, xai, deepseek)")
    parser.add_argument("--artifact", required=True, help="Path to the document under review")
    parser.add_argument("--ref", action="append", default=[], dest="refs", help="Path to a source-of-truth reference; repeat for several")
    parser.add_argument("--out", required=True, help="Run directory; the report and its rendering land here")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config JSON (default: the skill's templates/config.json)")
    parser.add_argument("--model", default=None, help="Concrete model id, overriding the tier map")
    parser.add_argument("--tier", default=None, help="Tier in the config tier map (default: the config's default_tier)")
    parser.add_argument("--max-tokens", type=int, default=None, help="Completion cap for the call")
    parser.add_argument("--suffix", default="", help="Appended to the reviewer id when a panel seats one pair twice (e.g. -2)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    persona_name = args.persona[:-3] if args.persona.endswith(".md") else args.persona
    persona_path = os.path.join(AGENTS_DIR, persona_name + ".md")
    if not os.path.isfile(persona_path):
        available = sorted(name[:-3] for name in os.listdir(AGENTS_DIR) if name.endswith(".md"))
        sys.exit("No persona `{0}` in {1}. Available: {2}".format(persona_name, AGENTS_DIR, ", ".join(available)))

    for path in [args.artifact] + args.refs:
        if not os.path.isfile(path):
            sys.exit("Not a file: {0}".format(path))

    _frontmatter, persona_body = report_lib.parse_agent_file(persona_path)
    lens = persona_name[len("lens-"):] if persona_name.startswith("lens-") else persona_name
    reviewer_id = "{0}-{1}{2}".format(lens, args.family, args.suffix)

    config = load_config(args.config)
    entry = dict(config[PROVIDER])
    tier = args.tier or entry.get("default_tier") or "frontier"
    model, tier = resolve_model(entry, tier, args.family, args.model)

    api_key, key_source = resolve_api_key(entry)
    entry["api_key"] = api_key
    if args.max_tokens:
        entry["max_tokens"] = args.max_tokens

    driver = load_driver(entry.get("type", "openai_compat"))
    http_request_fn = make_http_request_fn(driver)

    system_prompt = build_system_prompt(persona_body)
    user_prompt = build_user_prompt(args.artifact, args.refs)

    started = time.time()
    attempts = []
    parsed = None
    errors = []
    raw = ""

    for attempt in range(2):
        prompt = user_prompt if attempt == 0 else build_repair_prompt(user_prompt, raw, errors)
        try:
            raw, response, notes = driver.dispatch_detailed(system_prompt, prompt, model, entry, {"type": "json_object"}, http_request_fn)
        except Exception as exc:  # noqa: BLE001 — flag-and-advance: the panel continues without this seat
            sys.stderr.write("{0}: dispatch failed: {1}\n".format(reviewer_id, exc))
            return 2
        candidate = report_lib.strip_fence(raw)
        try:
            parsed = json.loads(candidate)
        except ValueError as exc:
            parsed = None
            errors = ["the response was not parseable JSON: {0}".format(exc)]
        if parsed is not None:
            parsed = stamp_authoritative(parsed, reviewer_id, lens, args, model, artifact=args.artifact)
            errors = report_lib.validate_report(parsed, lens=lens)
        # Every attempt is recorded, passing or failing. A seat that needed a repair used to leave no
        # trace of what was wrong the first time, so the manifest showed a repair count and no cause.
        attempts.append(record_attempt(attempt, response, notes, errors))
        if not errors:
            break
        if attempt == 0:
            sys.stderr.write("{0}: report failed validation ({1} error(s)); one repair re-ask\n".format(reviewer_id, len(errors)))
            for error in errors:
                sys.stderr.write("  - {0}\n".format(error))

    elapsed = time.time() - started
    meta = build_meta(attempts, tier, model, key_source, elapsed)

    if errors or parsed is None:
        sys.stderr.write("{0}: report still invalid after the repair re-ask:\n".format(reviewer_id))
        for error in errors:
            sys.stderr.write("  - {0}\n".format(error))
        failure_path = os.path.join(args.out, reviewer_id + ".invalid.txt")
        report_lib.write_text(failure_path, raw or "")
        sys.stderr.write("  raw response kept at {0}\n".format(failure_path))
        # A seat that fails still spent money. Leave the attempt record on disk so the panel can put
        # this seat's usage and cost in the manifest instead of reporting a failed seat as free.
        failed_path = os.path.join(args.out, reviewer_id + ".failed.json")
        report_lib.write_json(failed_path, {
            "reviewer_id": reviewer_id,
            "lens": lens,
            "family": args.family,
            "model": model,
            "tier": tier,
            "status": "failed",
            "errors": errors,
            "raw_response": failure_path,
            "_meta": meta,
        })
        sys.stderr.write("  attempt record kept at {0}\n".format(failed_path))
        if meta.get("cost_usd") is not None:
            sys.stderr.write("  spent anyway: ${0:.4f} across {1} attempt(s)\n".format(meta["cost_usd"], len(attempts)))
        return 3

    existing_meta = parsed.get("_meta")
    if isinstance(existing_meta, dict):
        # Keeps whatever validation already put there — `truncated`, above all.
        existing_meta.update(meta)
        meta = existing_meta
    parsed["_meta"] = meta

    json_path = os.path.join(args.out, reviewer_id + ".json")
    md_path = os.path.join(args.out, reviewer_id + ".md")
    report_lib.write_json(json_path, parsed)
    report_lib.write_text(md_path, report_lib.render_markdown(parsed))

    print(report_lib.make_digest(parsed, json_path))
    return 0


def stamp_authoritative(parsed, reviewer_id, lens, args, model, artifact):
    """Overwrite the audit fields with what the dispatcher knows, so the record cannot drift."""
    if not isinstance(parsed, dict):
        return parsed
    parsed["schema_version"] = "1"
    parsed["reviewer_id"] = reviewer_id
    parsed["lens"] = lens
    parsed["family"] = args.family
    parsed["model"] = model
    parsed["leg"] = "openrouter"
    parsed["artifact"] = repo_relative(artifact)
    parsed["references"] = [repo_relative(path) for path in args.refs]
    return parsed


if __name__ == "__main__":
    sys.exit(main())
