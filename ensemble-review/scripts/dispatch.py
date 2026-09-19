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

**Two pieces of this file are shared with the judge stage.** `prepare_call()` turns a family and a
tier into a resolved model, a priced registry entry, a checked effort, a key and a loaded driver;
`attempt_loop()` runs the three retry paths above around whatever validator the caller supplies.
`reconcile.py` dispatches the `synthesis` persona through both, so the judgment call is made by the
same driver, under the same length retry, through the same registry gate and with the same per-call
cost record as a review seat. What differs is the validator — a judgment patch, not a report — and
that is exactly the callback `attempt_loop` takes.
"""

import argparse
import http.client
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
from backends import base as backends_base  # noqa: E402
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

# **The transient exception set, and why it is spelled out rather than left at `OSError`.**
# Run 3's `buildability-glm` seat died on `http.client.IncompleteRead(528 bytes read)` — the provider
# closed the connection mid-body 147 seconds in. `IncompleteRead` descends from `HTTPException`, not
# from `OSError`, so it fell straight past the retry loop, cost the seat its whole dispatch and
# reached the manifest as a 147-second seat with `attempts: null` and a null cost. A connection torn
# down mid-stream is the most ordinary transient failure there is, so the set names the whole
# `HTTPException` branch alongside the socket branch. `ConnectionError`, `ConnectionResetError`,
# `socket.timeout` and `urllib.error.URLError` are all already `OSError` subclasses; they are named
# anyway because a reader of framework §11's policy should be able to find each one here rather than
# have to know the standard library's hierarchy to see that it is covered.
TRANSIENT_EXCEPTIONS = (
    urllib.error.URLError,
    socket.timeout,
    TimeoutError,
    ConnectionResetError,
    ConnectionError,
    http.client.IncompleteRead,
    http.client.HTTPException,
    OSError,
)

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

# The repair re-ask's quote ceiling lives in `lib/report.py`, shared with the judgment patch's own
# repair prompt in `lib/judge.py`: one cap, or the leg nobody remembered keeps the old 200,000.
# Re-exported here because this is where the repair path reads.
REPAIR_QUOTE_CHARS = report_lib.REPAIR_QUOTE_CHARS
quote_for_repair = report_lib.quote_for_repair


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


def build_system_prompt(persona_body, reference_paths):
    """The persona body, then every reference its frontmatter `context` names, in that order.

    One separator rule between blocks. A lens persona names one reference and gets exactly what it
    always got; `synthesis` names two, so its system message carries `reconciliation.md` as well.
    """
    blocks = [persona_body.rstrip()]
    for path in reference_paths:
        blocks.append(read_file(path).rstrip())
    return "\n\n---\n\n".join(blocks) + "\n"


def persona_context_paths(paths, frontmatter, warn=None):
    """The references a persona's frontmatter `context` names, resolved in declaration order.

    **One implementation for both legs of the system message.** The seat leg used to hardcode the
    finding schema while the judge leg read the declaration, so a workspace persona naming a second
    reference got it as a judge and silently lost it as a reviewer — and a persona's system message
    is the one thing this skill promises is identical across families.

    The finding schema is appended when the declaration omits it rather than refused. Every seat's
    system message carries the output contract by invariant, not by declaration: a reviewer asked
    for JSON against a schema it was never shown produces a repair re-ask and then a missing seat.
    A workspace persona that forgot the line gets it, and is told so.
    """
    warn = warn or sys.stderr.write
    declared = (frontmatter or {}).get("context") or []
    if isinstance(declared, str):
        declared = [declared]

    # Deduped, first occurrence winning, so a reference named twice is inlined once. A repeated
    # block does not make the reviewer follow it harder; it costs prompt tokens on every seat of
    # every run, and it makes the seat's message differ from the judge's for no reason anyone chose.
    names = []
    repeated = []
    for name in declared:
        if not name:
            continue
        if name in names:
            if name not in repeated:
                repeated.append(name)
            continue
        names.append(name)
    if repeated:
        warn("note: this persona's `context` names {0} more than once; each reference is loaded "
             "once, in the order it first appears.\n".format(", ".join(repeated)))

    if FINDING_SCHEMA not in names:
        warn("note: this persona's `context` does not name {0}; it is loaded anyway, because every "
             "seat's system message carries the output contract.\n".format(FINDING_SCHEMA))
        names.append(FINDING_SCHEMA)
    return [paths.reference(name) for name in names]


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
    quoted, _note = quote_for_repair(raw_output)
    return "\n".join([
        original_user_prompt,
        "",
        "===== YOUR PREVIOUS RESPONSE =====",
        quoted,
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

    429, 5xx, socket timeouts and a connection torn down mid-body are transient: three tries at 1 s,
    4 s, 16 s, same prompt, same cap — `TRANSIENT_EXCEPTIONS` is the set and says why it is spelled
    out. 401 and 403 are not, and raise `AuthFailure` so the caller can halt the run rather than spend
    three more calls proving the key is still wrong. Every other 4xx is handed straight to the driver,
    which needs to see a `response_format` rejection to fall back out of JSON mode.

    Every retry leaves a line on `http_request_fn.transient_notes`, naming the **exception class** and
    the wait. `attempt_loop` drains that list onto the attempt the call belongs to, so the manifest
    records that a seat which eventually landed spent two minutes on an `IncompleteRead` first — the
    one fact a reader needs to tell a slow model from a flaky provider.
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
            except TRANSIENT_EXCEPTIONS as exc:
                last = exc
            if index < len(backoff):
                http_request_fn.transient_notes.append(
                    "transient {0} on try {1}; retried after {2}s".format(
                        type(last).__name__, index + 1, backoff[index]))
                sys.stderr.write("transient provider error ({0}: {1}); retrying in {2}s\n".format(
                    type(last).__name__, last, backoff[index]))
                sleep(backoff[index])
        http_request_fn.transient_notes.append(
            "transient {0} exhausted {1} retries at {2}".format(
                type(last).__name__, len(backoff), ", ".join("{0}s".format(s) for s in backoff)))
        raise last

    # The caller's drain point. A list on the function rather than a return value because the driver
    # sits between the two and framework §9 fixes its signature: the transport cannot hand the caller
    # anything the driver does not already carry.
    http_request_fn.transient_notes = []
    return http_request_fn


# --- attempt records ---------------------------------------------------------------------------

def finish_attempt(attempt, number, errors, note=None, notes=None):
    """Stamp a driver attempt record with what only the caller knows: its number and its verdict.

    `notes` carries the caller's own observations about this call — the transport's transient
    retries, the elision of an over-long quote — which the driver cannot see and the manifest has to
    keep.
    """
    # The optional accounting fields are filled here, the one place a driver's attempt becomes a
    # manifest attempt, so a connector that predates them still leaves rows a reader can scan.
    attempt = backends_base.with_attempt_defaults(attempt)
    attempt["n"] = number
    attempt["validation_errors"] = "ok" if not errors else list(errors)
    extra = list(notes or []) + ([note] if note else [])
    if extra:
        attempt["notes"] = list(attempt.get("notes") or []) + extra
    return attempt


def dispatch_error_attempt(number, cap, exc, elapsed, notes=None):
    """The attempt record for a call that raised instead of answering.

    Without it a seat that died in the transport reaches the manifest with `attempts: null`, a null
    cost and an `elapsed_s` of two and a half minutes — which reads as a seat nobody ever called.
    The record carries the **exception class**, the cap that was sent, the wall time spent and every
    transient retry the transport made before giving up.
    """
    return {
        "n": number,
        "max_tokens_sent": cap,
        "finish_reason": "dispatch-error",
        "validation_errors": ["dispatch failed: {0}: {1}".format(type(exc).__name__, exc)],
        "usage": None,
        "reasoning_tokens": None,
        "cost_usd": None,
        "cost_source": None,
        "upstream_unbilled_usd": None,
        "elapsed_s": round(elapsed, 2),
        "notes": list(notes or []) + ["exception: {0}".format(type(exc).__name__)],
        "response_id": None,
    }


def build_meta(attempts, tier, model, key_source, elapsed, effort, connector, provider, max_tokens):
    """The `_meta` block, built the same way whether the seat landed a report or failed.

    `cost_usd` is the sum across **every** attempt, the failed ones included: run 1's manifest said
    $0.00 for a seat that had spent about $0.34, and the run total was under-reported by that amount.
    """
    costs = [a.get("cost_usd") for a in attempts if a.get("cost_usd") is not None]
    reasoning_tokens = sum(int(a.get("reasoning_tokens") or 0) for a in attempts)
    length_truncations = sum(1 for a in attempts if a.get("finish_reason") == "length")
    # Every source that went into the total, so a reader can tell a billed figure from a reconstructed
    # one without re-deriving it from the attempts. `estimated` anywhere makes the seat's total an
    # estimate; see `openai_compat._cost` for what each source means.
    cost_sources = sorted({a.get("cost_source") for a in attempts if a.get("cost_source")})
    # Inference the provider says it ran and did not charge for — an errored generation, above all.
    # Summed separately and never folded into `cost_usd`: the billed total has to reconcile against
    # a credit balance, and this does not.
    unbilled = [a.get("upstream_unbilled_usd") for a in attempts if a.get("upstream_unbilled_usd")]
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
        "cost_sources": cost_sources,
        "cost_estimated": "estimated" in cost_sources,
        "upstream_unbilled_usd": sum(unbilled) if unbilled else None,
        "reasoning_tokens": reasoning_tokens,
        "driver_notes": [note for a in attempts for note in (a.get("notes") or [])],
        "attempts": attempts,
    }


# --- the shared call machinery ------------------------------------------------------------------

class CompositionError(Exception):
    """A seat that cannot be resolved into a paid call. Every caller turns this into exit 1."""


class DispatchFailed(Exception):
    """The driver raised something that is not an auth failure. Carries the original exception."""

    def __init__(self, cause):
        super(DispatchFailed, self).__init__(str(cause))
        self.cause = cause


class Call(object):
    """Everything resolved between a family name and the first paid request.

    Built by `prepare_call` and consumed by `attempt_loop`. It holds the connector entry the driver
    is handed — including the key, the effort and the prices — so the two halves can be reused by
    any caller that needs one model call made this skill's way.
    """

    def __init__(self, label, model, tier, entry, driver, http_request_fn, cap, effort,
                 key_source, connector, provider, registry_entry):
        self.label = label
        self.model = model
        self.tier = tier
        self.entry = entry
        self.driver = driver
        self.http_request_fn = http_request_fn
        self.cap = cap
        self.effort = effort
        self.key_source = key_source
        self.connector = connector
        self.provider = provider
        self.registry_entry = registry_entry


def prepare_call(paths, family, tier=None, model=None, max_tokens=None, config_override=None,
                 models_override=None, label="seat", warn=None):
    """Resolve one model call: model, registry entry, cap, effort, key, driver, transport.

    The gates are the run's, in the run's order: the registry must price the model (a composition
    error otherwise, never a silent zero in the projection), the config's effort must be inside that
    model's vocabulary, and the key must resolve through the vault-then-environment chain. Raises
    `CompositionError` for the first two; `resolve_api_key` exits on the third, naming both paths.
    """
    warn = warn or sys.stderr.write
    config, _config_path = load_config(paths, config_override)
    entry = dict(config[PROVIDER])
    tier = tier or entry.get("default_tier") or "frontier"
    model, tier = resolve_model(entry, tier, family, model)

    try:
        registry = registry_lib.load(paths.registry(models_override))
        registry_entry = registry.require(model)
    except (paths_lib.PathError, registry_lib.RegistryError, registry_lib.MissingModel) as failure:
        raise CompositionError("{0}: {1}".format(label, failure))

    requested_cap = registry_lib.DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens
    cap = registry_lib.cap_for(registry_entry, requested_cap)
    if cap != requested_cap:
        warn("{0}: the registry raises the cap for {1} from {2} to {3} (min_max_tokens)\n".format(
            label, model, requested_cap, cap))

    input_price, output_price = registry_lib.prices(registry_entry)
    try:
        effort = resolve_effort(entry, model, registry_entry)
    except EffortRefused as failure:
        raise CompositionError("{0}: composition error: {1}".format(label, failure))

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
        raise CompositionError("{0}: composition error: {1}".format(label, failure))
    driver = load_driver(driver_ref)

    return Call(
        label=label, model=model, tier=tier, entry=entry, driver=driver,
        http_request_fn=make_http_request_fn(driver), cap=cap, effort=effort,
        key_source=key_source, connector=entry.get("type", "openai_compat"), provider=PROVIDER,
        registry_entry=registry_entry)


class CallResult(object):
    """What one seat's or one judge's whole call history came to.

    `raw` is the last response verbatim — what `<reviewer-id>.invalid.txt` keeps when nothing
    validated, and the only place the model's own bytes survive a failed call.
    """

    def __init__(self, parsed, errors, attempts, cap, connector, provider, raw):
        self.parsed = parsed
        self.errors = errors
        self.attempts = attempts
        self.cap = cap
        self.connector = connector
        self.provider = provider
        self.raw = raw


def attempt_loop(call, system_prompt, user_prompt, check, repair, warn=None, attempts=None):
    """The three retry paths, around whatever validator the caller supplies.

    `check(raw)` returns `(parsed_or_None, errors)`; `repair(original_prompt, raw, errors)` returns
    the one repair prompt. Returns a `CallResult`.

    `attempts` lets the caller own the history list. A `check` that refuses outright — a judgment
    patch from `synthesis` carrying `rulings`, which is a hard error and earns no repair — raises
    rather than returning errors, and the call it refused is recorded before the exception leaves,
    so a caller holding the list can still put that call's cost in the manifest. A failed call that
    spent money and is accounted at $0.00 is the defect this skill has closed twice already.

    The order is the one the spec fixes and it is not the same order three times over. A transient
    provider error never reaches here — `http_request_fn` has already retried it three times. A
    **truncation** is handled before validation, because `finish_reason: length` is not a reviewer
    error: one retry at double the cap with a fresh prompt, the truncated bytes discarded rather than
    quoted back. Only a response that parsed and failed *validation* earns the repair re-ask, and
    only one, and it is the single prompt that does quote the previous response.
    """
    warn = warn or sys.stderr.write
    attempts = attempts if attempts is not None else []
    parsed = None
    errors = []
    raw = ""
    cap = call.cap
    prompt = user_prompt
    length_retried = False
    repaired = False
    connector = call.connector
    provider = call.provider
    pending_notes = []

    while True:
        call.entry["max_tokens"] = cap
        # Drained per call, so each attempt's notes describe the transient retries *that* call made
        # rather than the seat's running total.
        notes_sink = getattr(call.http_request_fn, "transient_notes", None)
        if notes_sink is not None:
            del notes_sink[:]
        started_call = time.time()
        try:
            result = call.driver.dispatch_detailed(
                system_prompt, prompt, call.model, call.entry, {"type": "json_object"}, call.http_request_fn)
        except AuthFailure:
            raise
        except Exception as exc:  # noqa: BLE001 — the caller decides whether to flag-and-advance
            # Recorded before it leaves. A caller holding the list gets an attempt carrying the
            # exception class, the cap, the wall time and the transport's retries, so a seat that
            # died in the transport is never accounted at zero attempts and a null cost.
            attempts.append(dispatch_error_attempt(
                len(attempts) + 1, cap, exc, time.time() - started_call,
                pending_notes + list(notes_sink or [])))
            raise DispatchFailed(exc)
        transient_notes = list(notes_sink or [])

        raw = result.get("text") or ""
        connector = result.get("connector") or connector
        provider = result.get("provider") or provider
        driver_attempt = (result.get("attempts") or [{}])[0]
        call_notes = pending_notes + transient_notes
        pending_notes = []

        if result.get("finish_reason") == "length" and not length_retried:
            doubled = cap * registry_lib.LENGTH_RETRY_MULTIPLIER
            attempts.append(finish_attempt(
                driver_attempt, len(attempts) + 1, ["finish_reason: length — truncated completion"],
                "truncated at {0}; retrying once at {1} with a fresh prompt".format(cap, doubled),
                notes=call_notes))
            warn("{0}: truncated at max_tokens={1}; one retry at {2} with a fresh prompt\n".format(
                call.label, cap, doubled))
            length_retried = True
            cap = doubled
            prompt = user_prompt
            continue

        try:
            parsed, errors = check(raw)
        except Exception as refusal:  # noqa: BLE001 — a validator that refuses outright, not an error here
            attempts.append(finish_attempt(driver_attempt, len(attempts) + 1,
                                           ["refused: {0}".format(refusal)], notes=call_notes))
            raise
        attempts.append(finish_attempt(driver_attempt, len(attempts) + 1, errors, notes=call_notes))

        if not errors:
            break
        if repaired:
            break
        repaired = True
        warn("{0}: the response failed validation ({1} error(s)); one repair re-ask\n".format(
            call.label, len(errors)))
        for error in errors:
            warn("  - {0}\n".format(error))
        _quoted, elision = quote_for_repair(raw)
        if elision:
            # On the *next* attempt's record: it is that call's prompt the elision describes.
            pending_notes.append(elision)
            warn("  {0}\n".format(elision))
        prompt = repair(user_prompt, raw, errors)

    return CallResult(parsed, errors, attempts, cap, connector, provider, raw)


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
        frontmatter, persona_body = report_lib.parse_agent_file(persona_path)
        context_paths = persona_context_paths(paths, frontmatter)
    except paths_lib.PathError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_COMPOSITION

    for path in [args.artifact] + args.refs:
        if not os.path.isfile(path):
            sys.exit("Not a file: {0}".format(path))

    lens = persona_name[len("lens-"):] if persona_name.startswith("lens-") else persona_name
    reviewer_id = args.reviewer_id or "{0}-{1}{2}".format(lens, args.family, args.suffix)

    try:
        call = prepare_call(paths, args.family, tier=args.tier, model=args.model,
                            max_tokens=args.max_tokens, config_override=args.config,
                            models_override=args.models, label=reviewer_id)
    except (paths_lib.PathError, CompositionError) as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_COMPOSITION

    system_prompt = build_system_prompt(persona_body, context_paths)
    user_prompt = build_user_prompt(args.artifact, args.refs, args.artifact_name, args.ref_names)

    def check(raw_text):
        """Parse, stamp the audit fields over the model's guesses, then validate at ingest."""
        try:
            parsed_report = json.loads(report_lib.strip_fence(raw_text))
        except ValueError as exc:
            return None, ["the response was not parseable JSON: {0}".format(exc)]
        parsed_report = stamp_authoritative(parsed_report, reviewer_id, lens, args, call.model, artifact=args.artifact)
        return parsed_report, report_lib.validate_report(parsed_report, lens=lens, ingest=True)

    started = time.time()
    # Owned here, not inside `attempt_loop`, so a dispatch that raises still hands back the calls it
    # made. The list is the only record of a seat that died in the transport.
    attempts = []
    try:
        outcome = attempt_loop(call, system_prompt, user_prompt, check, build_repair_prompt,
                               attempts=attempts)
    except AuthFailure as failure:
        sys.stderr.write("{0}: provider auth failure: {1}\n".format(reviewer_id, failure))
        sys.stderr.write("  auth failures are not transient; no further paid calls from this seat.\n")
        return EXIT_AUTH
    except DispatchFailed as failure:
        # Exhausted transient retries, a provider error, a malformed body: this seat is missing,
        # stage `dispatch`. It is not exit 2 — only an auth failure halts the whole panel.
        #
        # It still writes `<reviewer-id>.failed.json`. Run 3's `buildability-glm` took this path and
        # reached the manifest as `elapsed_s: 147.5` with `attempts: null`, `cost_usd: null` and
        # nothing on disk to say what had been tried — a seat that looks as though it was never
        # called. The attempt record below is what `run_panel.py` folds in so the manifest carries
        # the exception class, the cap sent, the wall time and the transport's retries.
        elapsed = time.time() - started
        meta = build_meta(attempts, call.tier, call.model, call.key_source, elapsed, call.effort,
                          call.connector, call.provider, call.cap)
        failed_path = os.path.join(args.out, reviewer_id + ".failed.json")
        report_lib.write_json(failed_path, {
            "reviewer_id": reviewer_id,
            "lens": lens,
            "family": args.family,
            "model": call.model,
            "tier": call.tier,
            "status": "failed",
            "failure_stage": "dispatch",
            "errors": ["dispatch failed: {0}: {1}".format(type(failure.cause).__name__, failure.cause)],
            "raw_response": None,
            "_meta": meta,
        })
        sys.stderr.write("{0}: dispatch failed: {1}\n".format(reviewer_id, failure.cause))
        sys.stderr.write("  attempt record kept at {0}\n".format(failed_path))
        if model_is_unavailable(failure.cause, call.model):
            sys.stderr.write("{0}: the provider will not serve {1}; this family is unreachable "
                             "for this run\n".format(reviewer_id, call.model))
            return EXIT_MODEL_UNAVAILABLE
        return EXIT_INVALID

    elapsed = time.time() - started
    model, tier, effort, key_source = call.model, call.tier, call.effort, call.key_source
    parsed, errors, attempts, raw = outcome.parsed, outcome.errors, outcome.attempts, outcome.raw
    meta = build_meta(attempts, tier, model, key_source, elapsed, effort,
                      outcome.connector, outcome.provider, outcome.cap)

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
