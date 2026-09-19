"""Shared report machinery: agent-file parsing, validation, Markdown rendering, digests.

Standard library only. The validator is hand-written against `schemas/review-report.schema.json`
rather than pulling in `jsonschema`, so the skill runs on any Python 3 with nothing installed. It
checks what the schema can express about this document — required fields, types, enums, the id
pattern — plus the rules the schema states in prose: a `literal-edit` finding needs a `literal_edit`
block, every finding from a citing lens (`fidelity`, `source-credibility`) needs a citation, and a
`judgment-call` finding carries the `fork` tag.

**The `fork` tag is checked at ingest only.** `ingest=True` is the moment a report first enters the
system — a model's response in `dispatch.py`, a subagent's JSON in `render_harness_report.py` —
where the reviewer is still there to be asked for a repair. Reading a report back off disk uses the
default, `ingest=False`, because a report accepted before the rule existed is still the report that
seat wrote: the frozen replay fixture's 44 untagged `judgment-call` findings must keep replaying,
and a resume must not re-dispatch a seat over a rule its report predates.

Length caps are the one rule that does not reject. A string over its cap is trimmed to the cap with
a trailing ellipsis and the trim is recorded in `_meta.truncated`, because a reviewer who wrote 696
characters of summary has done the work and losing a whole model family over 96 characters costs the
panel far more than the overrun does. Rejection is reserved for what a trim cannot repair: missing
required fields, wrong types, bad enum values.
"""

import json
import os

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VERDICTS = ("ship", "fix-then-ship", "rework")
SEVERITIES = ("blocker", "should-fix", "nice-to-have")
CHANGE_KINDS = ("literal-edit", "judgment-call")
CONFIDENCES = ("high", "medium", "low")
LEGS = ("harness", "openrouter")

# The two lenses that cite on every finding. `run_panel.py` refuses to seat either with no references
# for the same reason this rejects an uncited finding from one: the citation is the whole argument.
CITING_LENSES = ("fidelity", "source-credibility")

# `change_kind` has two values and `judgment-call` means a design fork and nothing else, so `fork` is
# the tag a judgment call carries and `gap` is the tag it may not: a determinate fix, however large,
# is a `literal-edit` with the replacement written out. `gap` stays a live tag on those.
FORK_TAG = "fork"
GAP_TAG = "gap"

SEVERITY_ORDER = {"blocker": 0, "should-fix": 1, "nice-to-have": 2}
SEVERITY_LABEL = {"blocker": "Blockers", "should-fix": "Should fix", "nice-to-have": "Nice to have"}

REPORT_REQUIRED = ("schema_version", "reviewer_id", "lens", "family", "model", "leg", "artifact", "verdict", "summary", "findings")
FINDING_REQUIRED = ("id", "location", "quote", "claim", "severity", "reasoning", "suggested_change", "change_kind", "confidence")

DIGEST_LIMIT = 2000

SUMMARY_CAP = 600
QUOTE_CAP = 300
ELLIPSIS = "…"


# --- agent files -------------------------------------------------------------------------------

def parse_agent_file(path):
    """Split a framework §6 agent file into (frontmatter dict, body string).

    Scalars and single-depth lists only — the framework's own parser contract, no PyYAML.
    """
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    frontmatter = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            body = parts[2].lstrip("\n")
            current_list_key = None
            for raw in parts[1].splitlines():
                line = raw.rstrip()
                if not line.strip():
                    continue
                stripped = line.strip()
                if stripped.startswith("- ") and current_list_key:
                    frontmatter[current_list_key].append(stripped[2:].strip())
                    continue
                if ":" in stripped:
                    key, _, value = stripped.partition(":")
                    key = key.strip()
                    value = value.strip()
                    if value in ("", "[]"):
                        frontmatter[key] = []
                        current_list_key = key if value == "" else None
                        if value == "[]":
                            frontmatter[key] = []
                    else:
                        frontmatter[key] = value.strip('"').strip("'")
                        current_list_key = None
    return frontmatter, body


# --- validation --------------------------------------------------------------------------------

def _is_str(value):
    return isinstance(value, str)


def _truncate_to_cap(value, cap):
    """Trim to at most `cap` characters, the last of which is the ellipsis that marks the trim."""
    return value[: cap - 1].rstrip() + ELLIPSIS


def _record_truncation(truncations, field, cap, original, finding_id=None):
    entry = {"field": field, "cap": cap, "original_chars": original}
    if finding_id:
        entry["finding_id"] = finding_id
    truncations.append(entry)


def apply_length_caps(report):
    """Trim every over-long string field in place; return one record per trim.

    Idempotent: a field already at its cap is not trimmed again, so running the validator twice — the
    first attempt and then the repair — never double-counts a truncation.
    """
    truncations = []
    if not isinstance(report, dict):
        return truncations

    summary = report.get("summary")
    if _is_str(summary) and len(summary) > SUMMARY_CAP:
        report["summary"] = _truncate_to_cap(summary, SUMMARY_CAP)
        _record_truncation(truncations, "summary", SUMMARY_CAP, len(summary))

    findings = report.get("findings")
    if isinstance(findings, list):
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                continue
            finding_id = finding.get("id") if _is_str(finding.get("id")) else None
            quote = finding.get("quote")
            if _is_str(quote) and len(quote) > QUOTE_CAP:
                finding["quote"] = _truncate_to_cap(quote, QUOTE_CAP)
                _record_truncation(truncations, "findings[{0}].quote".format(index), QUOTE_CAP, len(quote), finding_id)
            citation = finding.get("citation")
            if isinstance(citation, dict):
                citation_quote = citation.get("quote")
                if _is_str(citation_quote) and len(citation_quote) > QUOTE_CAP:
                    citation["quote"] = _truncate_to_cap(citation_quote, QUOTE_CAP)
                    _record_truncation(truncations, "findings[{0}].citation.quote".format(index), QUOTE_CAP, len(citation_quote), finding_id)

    if truncations:
        meta = report.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
            report["_meta"] = meta
        existing = meta.get("truncated")
        meta["truncated"] = (existing if isinstance(existing, list) else []) + truncations

    return truncations


def validate_report(report, lens=None, ingest=False):
    """Trim over-long strings in place, then return the errors that remain. Empty list means valid.

    `ingest=True` adds the rules that only a reviewer who is still listening can repair — today, the
    `fork` tag on a `judgment-call` finding. See this module's docstring for why re-reading a stored
    report does not apply them.
    """
    errors = []
    if not isinstance(report, dict):
        return ["top level is a {0}, expected a JSON object".format(type(report).__name__)]

    apply_length_caps(report)

    for field in REPORT_REQUIRED:
        if field not in report or report[field] is None:
            errors.append("missing required top-level field `{0}`".format(field))

    if report.get("schema_version") not in (None, "1"):
        errors.append("`schema_version` must be the string \"1\", got {0!r}".format(report.get("schema_version")))

    for field in ("reviewer_id", "lens", "family", "model", "artifact", "summary"):
        value = report.get(field)
        if value is not None and (not _is_str(value) or not value.strip()):
            errors.append("`{0}` must be a non-empty string".format(field))

    if report.get("leg") is not None and report["leg"] not in LEGS:
        errors.append("`leg` must be one of {0}, got {1!r}".format(list(LEGS), report["leg"]))

    if report.get("verdict") is not None and report["verdict"] not in VERDICTS:
        errors.append("`verdict` must be one of {0}, got {1!r}".format(list(VERDICTS), report["verdict"]))

    if "method_notes" in report and report["method_notes"] is not None and not _is_str(report["method_notes"]):
        errors.append("`method_notes` must be a string")

    refs = report.get("references")
    if refs is not None:
        if not isinstance(refs, list) or any(not _is_str(item) for item in refs):
            errors.append("`references` must be an array of strings")

    # The revision the seat read, stamped by the dispatcher from the bytes in `inputs/`. Null is the
    # honest value for a seat dispatched against a working-tree path, and for every report written
    # before revisions existed, so it is optional and nullable rather than required.
    revision = report.get("artifact_revision")
    if revision is not None and (not _is_str(revision) or not revision.strip()):
        errors.append("`artifact_revision` must be a non-empty string or null")
    reference_revisions = report.get("reference_revisions")
    if reference_revisions is not None:
        if not isinstance(reference_revisions, list) or any(not _is_str(item) for item in reference_revisions):
            errors.append("`reference_revisions` must be an array of strings")

    findings = report.get("findings")
    if findings is None:
        pass
    elif not isinstance(findings, list):
        errors.append("`findings` must be an array")
    else:
        seen_ids = set()
        for index, finding in enumerate(findings):
            errors.extend(_validate_finding(finding, index, seen_ids, lens, ingest))

    if report.get("verdict") == "ship" and isinstance(findings, list):
        if any(f.get("severity") == "blocker" for f in findings if isinstance(f, dict)):
            errors.append("`verdict` is \"ship\" but the report contains a blocker finding")

    return errors


def _validate_finding(finding, index, seen_ids, lens, ingest=False):
    where = "findings[{0}]".format(index)
    errors = []
    if not isinstance(finding, dict):
        return ["{0} is a {1}, expected an object".format(where, type(finding).__name__)]

    fid = finding.get("id")
    if _is_str(fid):
        where = "finding {0}".format(fid)
        if not (fid.startswith("F") and fid[1:].isdigit()):
            errors.append("{0}: `id` must look like F1, F2, ...".format(where))
        if fid in seen_ids:
            errors.append("{0}: duplicate `id`".format(where))
        seen_ids.add(fid)

    for field in FINDING_REQUIRED:
        if field not in finding or finding[field] is None:
            errors.append("{0}: missing required field `{1}`".format(where, field))

    for field in ("location", "quote", "claim", "reasoning", "suggested_change"):
        value = finding.get(field)
        if value is not None and (not _is_str(value) or not value.strip()):
            errors.append("{0}: `{1}` must be a non-empty string".format(where, field))

    for field, allowed in (("severity", SEVERITIES), ("change_kind", CHANGE_KINDS), ("confidence", CONFIDENCES)):
        value = finding.get(field)
        if value is not None and value not in allowed:
            errors.append("{0}: `{1}` must be one of {2}, got {3!r}".format(where, field, list(allowed), value))

    citation = finding.get("citation")
    if citation is not None:
        if not isinstance(citation, dict):
            errors.append("{0}: `citation` must be an object or null".format(where))
        else:
            for field in ("reference", "location", "quote"):
                value = citation.get(field)
                if not _is_str(value) or not value.strip():
                    errors.append("{0}: `citation.{1}` must be a non-empty string".format(where, field))

    if lens in CITING_LENSES and citation is None:
        errors.append("{0}: the {1} lens requires a `citation` on every finding".format(where, lens))

    literal_edit = finding.get("literal_edit")
    if finding.get("change_kind") == "literal-edit":
        if not isinstance(literal_edit, dict):
            errors.append("{0}: `change_kind` is \"literal-edit\" so `literal_edit` must be an object with `old_text` and `new_text`".format(where))
        else:
            if not _is_str(literal_edit.get("old_text")) or not literal_edit.get("old_text"):
                errors.append("{0}: `literal_edit.old_text` must be a non-empty string".format(where))
            if not _is_str(literal_edit.get("new_text")):
                errors.append("{0}: `literal_edit.new_text` must be a string".format(where))
    elif literal_edit is not None and not isinstance(literal_edit, dict):
        errors.append("{0}: `literal_edit` must be an object or null".format(where))

    if "externally_verified" in finding and finding["externally_verified"] is not None:
        if not isinstance(finding["externally_verified"], bool):
            errors.append("{0}: `externally_verified` must be true or false".format(where))

    tags = finding.get("tags")
    if tags is not None and (not isinstance(tags, list) or any(not _is_str(t) for t in tags)):
        errors.append("{0}: `tags` must be an array of strings".format(where))
    else:
        errors.extend(_judgment_call_tag_errors(finding, tags, where, ingest))

    return errors


def _judgment_call_tag_errors(finding, tags, where, ingest):
    """`judgment-call` means a fork, and the `fork` tag is where the reviewer says so out loud.

    Two rejections, because the two mistakes need opposite repairs. A `judgment-call` carrying `gap`
    is a determinate fix filed as a decision: the repair is to write the replacement out. A
    `judgment-call` carrying neither tag is a finding nobody calibrated: the repair is to decide
    which of the two it was. Both are ingest-time rules — nothing here rejects a stored report.
    """
    if not ingest or finding.get("change_kind") != "judgment-call":
        return []
    carried = [tag for tag in (tags or []) if tag in (FORK_TAG, GAP_TAG)]
    if GAP_TAG in carried:
        return ["{0}: a `judgment-call` finding must not carry the `{1}` tag. A determinate fix is a "
                "`literal-edit` however large it is; `judgment-call` is reserved for a design fork, "
                "which carries `{2}`".format(where, GAP_TAG, FORK_TAG)]
    if FORK_TAG not in carried:
        return ["{0}: every `judgment-call` finding carries the `{1}` tag in `tags`. If this is not a "
                "design fork with two defensible answers, it is a `literal-edit` with the replacement "
                "written out".format(where, FORK_TAG)]
    return []


# --- rendering ---------------------------------------------------------------------------------

def _counts(findings):
    counts = {"blocker": 0, "should-fix": 0, "nice-to-have": 0}
    for finding in findings:
        severity = finding.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


def render_markdown(report):
    """Render a validated report as the Markdown a human reads."""
    findings = report.get("findings") or []
    counts = _counts(findings)
    meta = report.get("_meta") or {}

    lines = []
    lines.append("# {0} — {1}".format(report.get("reviewer_id", "review"), report.get("artifact", "")))
    lines.append("")
    lines.append("**Verdict:** {0}  ".format(report.get("verdict", "?")))
    lines.append("**Lens:** {0} · **Family:** {1} · **Model:** `{2}` · **Leg:** {3}  ".format(
        report.get("lens", "?"), report.get("family", "?"), report.get("model", "?"), report.get("leg", "?")))
    lines.append("**Findings:** {0} blocker, {1} should-fix, {2} nice-to-have".format(
        counts["blocker"], counts["should-fix"], counts["nice-to-have"]))
    references = report.get("references") or []
    if references:
        lines.append("")
        lines.append("**References supplied:**")
        for ref in references:
            lines.append("- `{0}`".format(ref))
    if meta:
        bits = []
        if meta.get("tier"):
            bits.append("tier `{0}`".format(meta["tier"]))
        if meta.get("cost_usd") is not None:
            bits.append("cost ${0:.4f}".format(meta["cost_usd"]))
        if meta.get("elapsed_s") is not None:
            bits.append("{0:.0f}s".format(meta["elapsed_s"]))
        if meta.get("repairs"):
            bits.append("{0} repair re-ask(s)".format(meta["repairs"]))
        if meta.get("truncated"):
            bits.append("{0} field(s) truncated to their cap".format(len(meta["truncated"])))
        if bits:
            lines.append("")
            lines.append("*Run: {0}.*".format(", ".join(bits)))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(report.get("summary", "").strip())
    lines.append("")

    if not findings:
        lines.append("---")
        lines.append("")
        lines.append("## Findings")
        lines.append("")
        lines.append("None raised.")
        lines.append("")
    else:
        ordered = sorted(enumerate(findings), key=lambda pair: (SEVERITY_ORDER.get(pair[1].get("severity"), 9), pair[0]))
        current = None
        for _index, finding in ordered:
            severity = finding.get("severity")
            if severity != current:
                current = severity
                lines.append("---")
                lines.append("")
                lines.append("## {0}".format(SEVERITY_LABEL.get(severity, str(severity))))
                lines.append("")
            lines.extend(_render_finding(finding))

    method_notes = report.get("method_notes")
    if method_notes:
        lines.append("---")
        lines.append("")
        lines.append("## Method notes")
        lines.append("")
        lines.append(method_notes.strip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_finding(finding):
    lines = []
    lines.append("### {0} — {1}".format(finding.get("id", "F?"), finding.get("claim", "").strip()))
    lines.append("")
    lines.append("**Location:** {0} · **Confidence:** {1} · **Change kind:** {2}{3}".format(
        finding.get("location", "?"),
        finding.get("confidence", "?"),
        finding.get("change_kind", "?"),
        " · externally verified" if finding.get("externally_verified") else ""))
    tags = finding.get("tags") or []
    if tags:
        lines.append("")
        lines.append("**Tags:** {0}".format(", ".join("`{0}`".format(t) for t in tags)))
    lines.append("")
    lines.append("> {0}".format((finding.get("quote") or "").strip().replace("\n", "\n> ")))
    lines.append("")

    citation = finding.get("citation")
    if isinstance(citation, dict):
        lines.append("**Cited source:** `{0}` — {1}".format(citation.get("reference", "?"), citation.get("location", "?")))
        lines.append("")
        lines.append("> {0}".format((citation.get("quote") or "").strip().replace("\n", "\n> ")))
        lines.append("")

    lines.append("**Reasoning.** {0}".format((finding.get("reasoning") or "").strip()))
    lines.append("")
    lines.append("**Suggested change.** {0}".format((finding.get("suggested_change") or "").strip()))
    lines.append("")

    literal_edit = finding.get("literal_edit")
    if isinstance(literal_edit, dict):
        lines.append("**Literal edit.**")
        lines.append("")
        lines.append("```diff")
        for line in (literal_edit.get("old_text") or "").splitlines() or [""]:
            lines.append("- " + line)
        for line in (literal_edit.get("new_text") or "").splitlines() or [""]:
            lines.append("+ " + line)
        lines.append("```")
        lines.append("")

    return lines


# --- digest ------------------------------------------------------------------------------------

def make_digest(report, report_path, limit=DIGEST_LIMIT):
    """The <=2000-character pointer the orchestrator gets back. Never the report itself."""
    findings = report.get("findings") or []
    counts = _counts(findings)
    head = []
    head.append("{0} [{1}] verdict: {2}".format(report.get("reviewer_id", "?"), report.get("model", "?"), report.get("verdict", "?")))
    head.append("findings: {0} blocker / {1} should-fix / {2} nice-to-have".format(
        counts["blocker"], counts["should-fix"], counts["nice-to-have"]))
    meta = report.get("_meta") or {}
    if meta.get("cost_usd") is not None:
        head.append("cost: ${0:.4f}".format(meta["cost_usd"]))
    head.append("report: {0}".format(report_path))
    head.append("")

    ordered = sorted(enumerate(findings), key=lambda pair: (SEVERITY_ORDER.get(pair[1].get("severity"), 9), pair[0]))
    for _index, finding in ordered[:3]:
        head.append("- [{0}] {1}: {2}".format(finding.get("severity", "?"), finding.get("id", "F?"), (finding.get("claim") or "").strip()))

    text = "\n".join(head)
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


# --- io ----------------------------------------------------------------------------------------

def write_text(path, text):
    """Write a file atomically enough for a run directory: temp file, then replace."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def write_json(path, data):
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def strip_fence(text):
    """Remove a ```json fence a model wrapped its JSON in, and any prose either side of the object."""
    if text is None:
        return ""
    stripped = text.strip()
    if stripped.startswith("```"):
        newline = stripped.find("\n")
        if newline != -1:
            stripped = stripped[newline + 1:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        stripped = stripped.strip()
    if stripped.startswith("{"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start:end + 1]
    return stripped
