#!/usr/bin/env python3
"""Dispatch one persona against one artifact through one model family, and land a validated report.

    dispatch.py --persona lens-fidelity --family openai \
                --artifact .agents/reviews/.../inputs/technical-requirements.md \
                --artifact-name pm/technical-requirements.md \
                --ref .agents/reviews/.../inputs/prd.md --ref-name pm/prd.md \
                --out .agents/reviews/technical-requirements/2026-09-18-1

Writes `<run-dir>/<lens>-<family>.json` (the validated report plus a `_meta` block) and
`<run-dir>/<lens>-<family>.md` (its rendering), then prints a digest under 2000 characters.

Standard library only. The HTTP call itself lives in `backends/`, loaded by the provider's `type`,
so another access path is another file there rather than a change here.

The persona, the finding schema, the config, the registry and the driver all resolve through
`lib/paths.py`'s two-root cascade — `<workspace>/.agents/ensemble-review/` first, then the read-only
package — so `--workspace` is the only thing `run_panel.py` has to pass for the child to read exactly
the files the parent resolved. `--config` and `--models` stay available as operator paths.

**Three retry paths, in this order, and they are not the same thing.**

1. *Transient provider errors* — 429, 5xx, a socket timeout — are retried three times at 1 s, 4 s and
   16 s, same prompt, same cap. A 401 or 403 is not transient: it halts with exit 2.
2. *A truncated completion* — `finish_reason: length` — is retried **once at double the cap, with a
   fresh prompt**. The truncated bytes are discarded and never quoted back: run 2 paid $0.66 for one
   seat that spent an entire 32000-token cap on reasoning, returned nothing, and then had its
   truncation quoted into a 90k-token repair prompt. A truncation is not a reviewer error.
3. *A report that does not validate* gets one repair re-ask, which does quote the previous response
   back, because there the previous response is the thing to fix.

Every call is recorded in `_meta.attempts` with the cap it was sent, its finish reason, its
validation errors, its usage and its cost — including calls that produced nothing usable, because a
failed seat still spent money and a manifest that says $0.00 for it is wrong.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from backends import AuthFailure, load_driver  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import report as report_lib  # noqa: E402

FINDING_SCHEMA = "finding-schema.md"
LP = os.path.join(SKILL_DIR, os.pardir, "lastpass", "scripts", "lp")

PROVIDER = "openrouter"

# Framework §11: three tries, exponential backoff. The sleeps are module-level so a test can shorten
# them without reaching into the retry loop.
TRANSIENT_BACKOFF_S = (1, 4, 16)
TRANSIENT_STATUSES = (429,)
AUTH_STATUSES = (401, 403)

EXIT_COMPOSITION = 1
EXIT_AUTH = 2
EXIT_INVALID = 3
# A provider that will not serve this family's model at all. Distinct from EXIT_INVALID because the
# caller can do something about it that it cannot do about a bad report: re-seat the lens once.
EXIT_MODEL_UNAVAILABLE = 5

# What a 400 or 404 body says when the model, rather than the request, is the problem.
UNAVAILABLE_MARKERS = (
    "no endpoints", "not a valid model", "no allowed providers", "is not available",
    "does not exist", "not found", "unknown model", "model_not_found", "no providers",
)


# --- secrets -----------------------------------------------------------------------------------

def resolve_api_key(config_entry):
    """LastPass vault first, then the environment. Fail loudly with both paths tried.

    A config entry whose `api_key_secret` is null opts out of the vault leg and resolves from the
    environment alone — for a connector whose credential is not in the fleet vault, and for any run
    that must not shell out to it.
    """
    secret_name = config_entry.get("api_key_secret", "global/OPENROUTER_API_KEY")
    env_name = config_entry.get("api_key_env", "OPENROUTER_API_KEY")

    lp_path = os.path.normpath(LP)
    if secret_name and os.path.isfile(lp_path) and os.access(lp_path, os.X_OK):
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
        "  tried: {0} get {1}{3}\n"
        "  tried: ${2} in the environment\n"
        "Put the key in the vault (`lp put {1}`) or export ${2}, then re-run.".format(
            lp_path, secret_name, env_name, "" if secret_name else "  (skipped: api_key_secret is null)")
    )


# --- config ------------------------------------------------------------------------------------

def load_config(paths, override=None):
    """The merged config, through the workspace-first cascade. `--config` is an operator path."""
    config, path = paths.config(override)
    if PROVIDER not in config:
        sys.exit("config {0} has no `{1}` provider entry".format(path, PROVIDER))
    return config, path


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


class EffortRefused(Exception):
    """The config asks for an effort this model does not accept. A composition error, exit 1."""


def resolve_effort(config_entry, model, entry):
    """The effort string for this model, checked against the registry's vocabulary.

    The effort map is keyed by concrete model id because the vocabularies differ by vendor. A model
    absent from the map is dispatched with **no effort parameter at all** — v0's behaviour, which
    works everywhere at the cost of control.

    An effort the registry says this model does **not** accept is a composition error rather than a
    warning. Dropping it silently would dispatch the seat at whatever depth the provider defaults to,
    and a panel whose seats ran at unintended depths is not the comparison this skill exists to make.
    A vocabulary the registry simply has not learned yet is not the same as an unsupported value, and
    is allowed through: unknown is not refused.
    """
    effort = (config_entry.get("effort") or {}).get(model)
    if not effort:
        return None
    supported = registry_lib.effort_is_supported(entry, effort)
    if supported is False:
        raise EffortRefused(
            "the config asks for effort {0!r} on {1}, whose registry vocabulary is {2}.\n"
            "  Fix the `effort` entry in the config, or refresh the vocabulary with\n"
            "  python3 scripts/refresh_models.py".format(
                effort, model, "/".join((entry or {}).get("effort_vocabulary") or [])))
    return effort


def model_is_unavailable(exc, model):
    """Whether this dispatch failure means the provider will not serve this model at all.

    Narrow on purpose. A 400 or 404 whose body names the model, or whose body carries one of the
    phrases a provider uses when the model rather than the request is the problem. Anything else is
    an ordinary failed seat: re-seating a lens costs a second seat's money, so it is not a guess.
    """
    status = getattr(exc, "status", None)
    body = getattr(exc, "body", None) or str(exc)
    low = body.lower()
    if status in (400, 404) and (model.lower() in low or any(m in low for m in UNAVAILABLE_MARKERS)):
        return True
    return status is None and model.lower() in low and any(m in low for m in UNAVAILABLE_MARKERS)


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


def build_system_prompt(persona_body, finding_schema_path):
    finding_schema = read_file(finding_schema_path)
    return persona_body.rstrip() + "\n\n---\n\n" + finding_schema.rstrip() + "\n"


def build_user_prompt(artifact_path, reference_paths, artifact_label=None, reference_labels=None):
    """Artifact and references inlined verbatim, delimited, each labelled by its repo-relative path.

    The bytes come from wherever `artifact_path` points — on a panel run that is the read-only copy
    in `<run-dir>/inputs/`, so a mid-run edit to the working tree cannot give two seats two different
    documents. The *label* is the repo-relative path the reviewer cites, which is the working-tree
    path, because a finding that cited `inputs/v3-spec.md` would be unusable.
    """
    labels = list(reference_labels or []) or [repo_relative(p) for p in reference_paths]
    artifact_rel = artifact_label or repo_relative(artifact_path)

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
            rel = labels[index - 1]
            blocks.append("===== BEGIN REFERENCE {0} of {1}: {2} =====".format(index, len(reference_paths), rel))
            blocks.append(read_file(path).rstrip("\n"))
            blocks.append("===== END REFERENCE {0}: {1} =====".format(index, rel))
            blocks.append("")
    else:
        blocks.append("No reference documents were supplied. Say so in `method_notes` and review what you can.")
        blocks.append("")

    blocks.append("===== BEGIN ARTIFACT UNDER REVIEW: {0} =====".format(artifact_rel))
    blocks.append(read_file(artifact_path).rstrip("\n"))
    blocks.append("===== END ARTIFACT: {0} =====".format(artifact_rel))
    blocks.append("")
    blocks.append("Review `{0}` now, under your lens, following your rubric.".format(artifact_rel))
    blocks.append(
        "Set `artifact` to `{0}` and `references` to {1}.".format(artifact_rel, json.dumps(labels))
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

def make_http_request_fn(driver, sleep=time.sleep, backoff=TRANSIENT_BACKOFF_S):
    """The transport, with framework §11's retry policy wrapped around it.

    429, 5xx and socket timeouts are transient: three tries at 1 s, 4 s, 16 s, same prompt, same cap.
    401 and 403 are not, and raise `AuthFailure` so the caller can halt the run rather than spend
    three more calls proving the key is still wrong. Every other 4xx is handed straight to the driver,
    which needs to see a `response_format` rejection to fall back out of JSON mode.
    """
    def http_request_fn(url, headers, body_bytes, timeout):
        last = None
        for index in range(len(backoff) + 1):
            request = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.getcode(), response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace") if exc.fp else ""
                if exc.code in AUTH_STATUSES:
                    raise AuthFailure("the provider answered HTTP {0}: {1}".format(exc.code, body[:300]))
                if exc.code in TRANSIENT_STATUSES or exc.code >= 500:
                    last = driver.Rejected(exc.code, body)
                else:
                    raise driver.Rejected(exc.code, body)
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
                last = exc
            if index < len(backoff):
                sys.stderr.write("transient provider error ({0}); retrying in {1}s\n".format(last, backoff[index]))
                sleep(backoff[index])
        raise last
    return http_request_fn


# --- attempt records ---------------------------------------------------------------------------

def finish_attempt(attempt, number, errors, note=None):
    """Stamp a driver attempt record with what only the caller knows: its number and its verdict."""
    attempt = dict(attempt)
    attempt["n"] = number
    attempt["validation_errors"] = "ok" if not errors else list(errors)
    if note:
        attempt["notes"] = list(attempt.get("notes") or []) + [note]
    return attempt


def build_meta(attempts, tier, model, key_source, elapsed, effort, connector, provider, max_tokens):
    """The `_meta` block, built the same way whether the seat landed a report or failed.

    `cost_usd` is the sum across **every** attempt, the failed ones included: run 1's manifest said
    $0.00 for a seat that had spent about $0.34, and the run total was under-reported by that amount.
    """
    costs = [a.get("cost_usd") for a in attempts if a.get("cost_usd") is not None]
    reasoning_tokens = sum(int(a.get("reasoning_tokens") or 0) for a in attempts)
    length_truncations = sum(1 for a in attempts if a.get("finish_reason") == "length")
    return {
        "tier": tier,
        "model": model,
        "connector": connector,
        "provider": provider,
        "effort": effort,
        "max_tokens": max_tokens,
        "key_source": key_source,
        "elapsed_s": round(elapsed, 1),
        "attempts_count": len(attempts),
        "repairs": max(len(attempts) - 1 - length_truncations, 0),
        "length_truncations": length_truncations,
        "usage": attempts[-1].get("usage") if attempts else {},
        "cost_usd": sum(costs) if costs else None,
        "reasoning_tokens": reasoning_tokens,
        "driver_notes": [note for a in attempts for note in (a.get("notes") or [])],
        "attempts": attempts,
    }


# --- main --------------------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run one persona against one artifact through one model family.")
    parser.add_argument("--persona", required=True, help="Persona file name, with or without the .md suffix (e.g. lens-fidelity); resolved through the workspace-first cascade")
    parser.add_argument("--family", required=True, help="The **resolved** model family, named in the config tier map (claude, openai, google, xai, deepseek). A constrained seat's constraint is resolved by run_panel.py before this point")
    parser.add_argument("--artifact", required=True, help="Path to the document under review, or to its read-only copy in the run's inputs/")
    parser.add_argument("--artifact-name", default=None, help="Repo-relative path recorded in the report; defaults to the artifact's own")
    parser.add_argument("--artifact-revision", default=None, help="SHA-256 of the materialized artifact bytes, recorded in the report envelope")
    parser.add_argument("--ref", action="append", default=[], dest="refs", help="Path to a source-of-truth reference; repeat for several")
    parser.add_argument("--ref-name", action="append", default=[], dest="ref_names", help="Repo-relative path for the reference in the same position")
    parser.add_argument("--ref-revision", action="append", default=[], dest="ref_revisions", help="SHA-256 for the reference in the same position")
    parser.add_argument("--out", required=True, help="Run directory; the report and its rendering land here")
    parser.add_argument("--workspace", default=None,
                        help="The project holding the artifact. Files resolve from <workspace>/.agents/ensemble-review/ first, then the skill package (default: the working directory)")
    parser.add_argument("--config", default=None, help="Config JSON, taken as given (default: the workspace-first cascade, deep-merged)")
    parser.add_argument("--models", default=None, help="Model registry, taken as given (default: the workspace-first cascade)")
    parser.add_argument("--reviewer-id", default=None,
                        help="Reviewer id for this seat; defaults to <lens>-<family>. run_panel.py passes the id it minted from the seat's *requested* family, which is stable across runs and across a re-seat")
    parser.add_argument("--model", default=None, help="Concrete model id, overriding the tier map")
    parser.add_argument("--tier", default=None, help="Tier in the config tier map (default: the config's default_tier)")
    parser.add_argument("--max-tokens", type=int, default=registry_lib.DEFAULT_MAX_TOKENS,
                        help="Completion cap for the call; a model's registry floor raises it (default: {0})".format(registry_lib.DEFAULT_MAX_TOKENS))
    parser.add_argument("--suffix", default="", help="Appended to the reviewer id when a panel seats one pair twice (e.g. -2)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    paths = paths_lib.Paths(args.workspace)

    persona_name = args.persona[:-3] if args.persona.endswith(".md") else args.persona
    try:
        persona_path = paths.persona(persona_name)
        finding_schema_path = paths.reference(FINDING_SCHEMA)
    except paths_lib.PathError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_COMPOSITION

    for path in [args.artifact] + args.refs:
        if not os.path.isfile(path):
            sys.exit("Not a file: {0}".format(path))

    _frontmatter, persona_body = report_lib.parse_agent_file(persona_path)
    lens = persona_name[len("lens-"):] if persona_name.startswith("lens-") else persona_name
    reviewer_id = args.reviewer_id or "{0}-{1}{2}".format(lens, args.family, args.suffix)

    try:
        config, _config_path = load_config(paths, args.config)
    except paths_lib.PathError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_COMPOSITION
    entry = dict(config[PROVIDER])
    tier = args.tier or entry.get("default_tier") or "frontier"
    model, tier = resolve_model(entry, tier, args.family, args.model)

    # The registry covers every resolved seat or the run does not start. A model with no price is a
    # composition error, not a silent zero in the projection.
    try:
        registry = registry_lib.load(paths.registry(args.models))
        registry_entry = registry.require(model)
    except (paths_lib.PathError, registry_lib.RegistryError, registry_lib.MissingModel) as failure:
        sys.stderr.write("{0}: {1}\n".format(reviewer_id, failure))
        return EXIT_COMPOSITION

    base_cap = registry_lib.cap_for(registry_entry, args.max_tokens)
    if base_cap != args.max_tokens:
        sys.stderr.write("{0}: the registry raises the cap for {1} from {2} to {3} (min_max_tokens)\n".format(
            reviewer_id, model, args.max_tokens, base_cap))

    input_price, output_price = registry_lib.prices(registry_entry)
    try:
        effort = resolve_effort(entry, model, registry_entry)
    except EffortRefused as failure:
        sys.stderr.write("{0}: composition error: {1}\n".format(reviewer_id, failure))
        return EXIT_COMPOSITION

    api_key, key_source = resolve_api_key(entry)
    entry["api_key"] = api_key
    entry["reasoning_effort"] = effort
    entry["prices"] = {"input": input_price, "output": output_price}
    routing = (entry.get("provider_routing") or {}).get(model)
    if routing:
        entry["provider_routing_for_model"] = routing

    try:
        driver_ref = paths.driver_ref(entry.get("type", "openai_compat"))
    except paths_lib.PathError as failure:
        sys.stderr.write("{0}: composition error: {1}\n".format(reviewer_id, failure))
        return EXIT_COMPOSITION
    driver = load_driver(driver_ref)
    http_request_fn = make_http_request_fn(driver)

    system_prompt = build_system_prompt(persona_body, finding_schema_path)
    user_prompt = build_user_prompt(args.artifact, args.refs, args.artifact_name, args.ref_names)

    started = time.time()
    attempts = []
    parsed = None
    errors = []
    raw = ""
    cap = base_cap
    prompt = user_prompt
    length_retried = False
    repaired = False
    connector = entry.get("type", "openai_compat")
    provider = PROVIDER

    while True:
        entry["max_tokens"] = cap
        try:
            result = driver.dispatch_detailed(system_prompt, prompt, model, entry, {"type": "json_object"}, http_request_fn)
        except AuthFailure as failure:
            sys.stderr.write("{0}: provider auth failure: {1}\n".format(reviewer_id, failure))
            sys.stderr.write("  auth failures are not transient; no further paid calls from this seat.\n")
            return EXIT_AUTH
        except Exception as exc:  # noqa: BLE001 — flag-and-advance: the panel continues without this seat
            # Exhausted transient retries, a provider error, a malformed body: this seat is missing,
            # stage `dispatch`. It is not exit 2 — only an auth failure halts the whole panel.
            sys.stderr.write("{0}: dispatch failed: {1}\n".format(reviewer_id, exc))
            if model_is_unavailable(exc, model):
                sys.stderr.write("{0}: the provider will not serve {1}; this family is unreachable "
                                 "for this run\n".format(reviewer_id, model))
                return EXIT_MODEL_UNAVAILABLE
            return EXIT_INVALID

        raw = result.get("text") or ""
        connector = result.get("connector") or connector
        provider = result.get("provider") or provider
        driver_attempt = (result.get("attempts") or [{}])[0]

        # A truncation is not a reviewer error, so it is handled before validation: one retry at
        # double the cap, with the original prompt. The truncated bytes are discarded, never quoted.
        if result.get("finish_reason") == "length" and not length_retried:
            attempts.append(finish_attempt(driver_attempt, len(attempts) + 1, ["finish_reason: length — truncated completion"],
                                           "truncated at {0}; retrying once at {1} with a fresh prompt".format(cap, cap * registry_lib.LENGTH_RETRY_MULTIPLIER)))
            sys.stderr.write("{0}: truncated at max_tokens={1}; one retry at {2} with a fresh prompt\n".format(
                reviewer_id, cap, cap * registry_lib.LENGTH_RETRY_MULTIPLIER))
            length_retried = True
            cap = cap * registry_lib.LENGTH_RETRY_MULTIPLIER
            prompt = user_prompt
            continue

        candidate = report_lib.strip_fence(raw)
        try:
            parsed = json.loads(candidate)
        except ValueError as exc:
            parsed = None
            errors = ["the response was not parseable JSON: {0}".format(exc)]
        if parsed is not None:
            parsed = stamp_authoritative(parsed, reviewer_id, lens, args, model, artifact=args.artifact)
            errors = report_lib.validate_report(parsed, lens=lens)
        attempts.append(finish_attempt(driver_attempt, len(attempts) + 1, errors))

        if not errors:
            break
        if repaired:
            break
        repaired = True
        sys.stderr.write("{0}: report failed validation ({1} error(s)); one repair re-ask\n".format(reviewer_id, len(errors)))
        for error in errors:
            sys.stderr.write("  - {0}\n".format(error))
        prompt = build_repair_prompt(user_prompt, raw, errors)

    elapsed = time.time() - started
    meta = build_meta(attempts, tier, model, key_source, elapsed, effort, connector, provider, cap)

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
        return EXIT_INVALID

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
    labels = list(args.ref_names or []) or [repo_relative(path) for path in args.refs]
    parsed["schema_version"] = "1"
    parsed["reviewer_id"] = reviewer_id
    parsed["lens"] = lens
    parsed["family"] = args.family
    parsed["model"] = model
    parsed["leg"] = "openrouter"
    parsed["artifact"] = args.artifact_name or repo_relative(artifact)
    parsed["references"] = labels
    # The revision is the SHA-256 of the bytes in `inputs/`, so a report can be checked against the
    # document its seat actually read. Null when the seat was dispatched against a working-tree path.
    parsed["artifact_revision"] = args.artifact_revision
    if args.ref_revisions:
        parsed["reference_revisions"] = list(args.ref_revisions)
    return parsed


if __name__ == "__main__":
    sys.exit(main())
