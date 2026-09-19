#!/usr/bin/env python3
"""Apply the reconciliation's accepted literal edits to the artifact, through a five-condition gate.

    apply_fixes.py --run-dir <run-dir> --i-authored-this
    apply_fixes.py --run-dir <run-dir> --dry-run          # resolve and report, write nothing

**Off by default, and refused outright on a document the operator did not write.** The artifact is
untrusted input: it is inlined verbatim into every seat's user message, and a document can contain
instructions as easily as content. A hostile artifact can ask its reviewers to emit a
consensus-shaped `literal-edit` whose `new_text` is the attacker's paragraph, and if two families
comply the gate below sees a well-formed cluster. So the gate is not the whole defence — the
authorship assertion is, and `--i-authored-this` is the operator saying, in the one place it can be
checked, that this is their own document.

**The five conditions, all of which must hold for a cluster to be applied:**

1. `disposition` is `fix-now`.
2. `contradicted_by` is empty and `edit_conflict` is false.
3. `canonical_edit` is present and was explicitly accepted in the judgment patch — exactly one
   literal edit, whose `old_text` occurs **exactly once** in the artifact.
4. `tier` is `consensus`, or `unanimous` with `n_families >= 2`. Stated as evidence rather than as
   an enum, the same rule reads: `n_families >= 2` **and** the tier is not a same-family tier. Both
   wordings are checked, so the gate cannot silently change meaning if the tier table does.
5. The artifact's current content hash equals the `artifact_revision` the manifest recorded.

Condition 4's corroboration is of the **defect**, not of the replacement text: requiring two
families to write byte-identical wording would make the gate fire almost never. What stands in for
that is condition 3's acceptance — a mind looked at the wording and said so in the patch — and the
canonical edit's source reviewer is named in the log.

**Application is all-or-nothing.** Every candidate anchor is resolved against the current file
first, no two edits may overlap, and only then is the file written once, atomically. A failure at
any anchor aborts the whole set and applies nothing: a half-applied set of corroborated fixes is a
document in a state no reviewer read and no reconciliation describes. A cluster that simply does
not pass the gate is not a failure — it is reported as unapplied with its reason, and the rest
proceed.

A panel that ran single-family cannot satisfy condition 4, so auto-apply on such a run is a **no-op
that says so**: exit 0, nothing written, and no quietly relaxed gate.

Exit codes: `0` applied, or nothing was eligible, or a single-family no-op; `1` usage — no
authorship assertion, no run directory, no reconciliation, an artifact that does not resolve;
`3` the set was aborted — an anchor did not resolve, two edits overlapped, or the artifact has
moved since the panel read it.
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from lib import report as report_lib  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ABORTED = 3

# Condition 4, both wordings. The enum half and the evidence half are kept side by side on purpose:
# if the tier table ever gains a cross-family tier, the two disagree and the gate says so rather
# than quietly applying under a tier nobody weighed.
CROSS_FAMILY_TIERS = ("consensus", "unanimous")
SAME_FAMILY_TIERS = ("majority", "corroborated-same-family", "singleton")

AUTHORSHIP_FLAG = "--i-authored-this"


class ApplyError(Exception):
    """A condition that stops the whole set before the artifact is touched."""


# --- loading -------------------------------------------------------------------------------------

def load_json(path, what):
    if not os.path.isfile(path):
        raise ApplyError("no {0} at {1}".format(what, path))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        raise ApplyError("{0} at {1} is not readable JSON: {2}".format(what, path, exc))


def resolve_artifact(run_dir, document, override):
    """The **working-tree** artifact this run reviewed — never the run's own `inputs/` copy.

    The pinned copy in `inputs/` is the evidence, is read-only, and is what every anchor was checked
    against; writing into it would edit the record of what the seats read and leave the real
    document untouched. So a path that resolves inside the run directory is refused by name rather
    than followed.
    """
    if override:
        candidate = os.path.abspath(override)
        if not os.path.isfile(candidate):
            raise ApplyError("no such artifact: {0}".format(override))
        return _refuse_inside_run(run_dir, candidate)

    recorded = (document.get("artifact") or {}).get("path")
    if not recorded:
        raise ApplyError("this reconciliation records no artifact path; pass --artifact <path>")
    if os.path.isabs(recorded):
        if not os.path.isfile(recorded):
            raise ApplyError("the reconciliation's artifact {0} is not a readable file".format(recorded))
        return _refuse_inside_run(run_dir, os.path.abspath(recorded))

    candidates = [os.path.abspath(recorded)]
    directory = run_dir
    while True:
        candidates.append(os.path.join(directory, recorded))
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    for candidate in candidates:
        if os.path.isfile(candidate) and not _is_inside(run_dir, candidate):
            return os.path.abspath(candidate)
    raise ApplyError(
        "the reconciliation's artifact {0!r} was not found from the working directory or above {1}; "
        "pass --artifact <path>".format(recorded, run_dir))


def _is_inside(run_dir, path):
    return os.path.abspath(path).startswith(os.path.abspath(run_dir) + os.sep)


def _refuse_inside_run(run_dir, path):
    if _is_inside(run_dir, path):
        raise ApplyError(
            "{0} is inside the run directory. That copy is the pinned evidence of what the seats "
            "read, and it is read-only on purpose; apply to the working-tree document instead.".format(path))
    return path


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


# --- the gate ------------------------------------------------------------------------------------

def gate(cluster):
    """Why this cluster may not be applied, or None when conditions 1 to 4 hold.

    Condition 5 is a property of the run rather than of a cluster and is checked once, above.
    Condition 3's *anchor* half — `old_text` occurring exactly once — is checked later still,
    against the file, because it is the one condition whose failure aborts the whole set rather
    than dropping one cluster.
    """
    if cluster.get("disposition") != "fix-now":
        return "condition 1: disposition is {0!r}, not `fix-now`".format(cluster.get("disposition"))
    if cluster.get("contradicted_by"):
        return "condition 2: {0} reviewer(s) contradicted it".format(len(cluster["contradicted_by"]))
    if cluster.get("edit_conflict"):
        return "condition 2: the members proposed competing replacements and none was chosen"

    edit = cluster.get("canonical_edit")
    if not isinstance(edit, dict):
        return "condition 3: no canonical edit was accepted in the judgment patch"
    if not (edit.get("old_text") or "").strip():
        return "condition 3: the canonical edit carries no `old_text`"
    if not (edit.get("accepted_by") or "").strip():
        return "condition 3: the canonical edit records nobody who accepted it"

    tier = cluster.get("tier")
    families = cluster.get("n_families") or 0
    if families < 2:
        return "condition 4: {0} famil{1} — cross-family corroboration of the defect is the whole " \
               "evidence the gate turns on".format(families, "y" if families == 1 else "ies")
    if tier in SAME_FAMILY_TIERS:
        return "condition 4: tier {0!r} is a same-family tier".format(tier)
    if tier not in CROSS_FAMILY_TIERS:
        # The two wordings of condition 4 disagree. That means the tier table has moved and nobody
        # weighed this tier against the gate; refusing is the only safe reading.
        return "condition 4: tier {0!r} is neither {1}; the gate and the tier table disagree and " \
               "the gate will not guess".format(tier, " nor ".join(CROSS_FAMILY_TIERS))
    return None


def resolve_anchor(text, cluster):
    """`(start, end)` for this cluster's `old_text`, or `ApplyError` naming why it did not resolve."""
    old_text = cluster["canonical_edit"]["old_text"]
    occurrences = text.count(old_text)
    if occurrences == 0:
        raise ApplyError(
            "{0}: condition 3 — `old_text` does not occur in the artifact as it stands now. The "
            "document has changed since the panel read it, or the reviewer did not quote it "
            "verbatim.".format(cluster["id"]))
    if occurrences > 1:
        raise ApplyError(
            "{0}: condition 3 — `old_text` occurs {1} times in the artifact and must occur exactly "
            "once. Widen it until it is unique.".format(cluster["id"], occurrences))
    start = text.index(old_text)
    return start, start + len(old_text)


def plan_edits(text, clusters):
    """Resolve every candidate's anchor and check no two overlap. Returns them in file order."""
    planned = []
    for cluster in clusters:
        start, end = resolve_anchor(text, cluster)
        planned.append({"cluster": cluster, "start": start, "end": end})
    planned.sort(key=lambda entry: entry["start"])
    for earlier, later in zip(planned, planned[1:]):
        if later["start"] < earlier["end"]:
            raise ApplyError(
                "{0} and {1} edit overlapping spans of the artifact ({2}-{3} and {4}-{5}). Applying "
                "either changes what the other's `old_text` means, so neither is applied.".format(
                    earlier["cluster"]["id"], later["cluster"]["id"],
                    earlier["start"], earlier["end"], later["start"], later["end"]))
    return planned


def apply_edits(text, planned):
    """The new document. Applied back to front, so an earlier edit cannot move a later offset."""
    out = text
    for entry in reversed(planned):
        edit = entry["cluster"]["canonical_edit"]
        out = out[:entry["start"]] + (edit.get("new_text") or "") + out[entry["end"]:]
    return out


# --- the audit log -------------------------------------------------------------------------------

def render_applied(document, artifact_path, planned, unapplied, applied, note=None):
    """`applied.md`: every edit that landed, with its provenance, and every one that did not.

    The point of the file is that an applied fix can be audited backwards — from the bytes in the
    document, to the cluster, to the reviewers behind it, to the seat whose wording was used and the
    mind that accepted it.
    """
    lines = []
    lines.append("# Applied fixes — {0}, run {1}".format(
        (document.get("artifact") or {}).get("path") or "artifact", document.get("run_id")))
    lines.append("")
    lines.append("> **{0} edit(s) applied**, {1} candidate(s) not applied. Panel `{2}`; judgment "
                 "supplied by `{3}`; verdict `{4}`.".format(
                     len(planned) if applied else 0, len(unapplied),
                     document.get("panel"), document.get("reconciler"), document.get("verdict")))
    lines.append(">")
    lines.append("> Artifact: `{0}`, at revision `{1}`. Generated {2}.".format(
        artifact_path, (document.get("artifact") or {}).get("revision"),
        datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()))
    lines.append("")
    if note:
        lines.append("**{0}**".format(note))
        lines.append("")

    lines.append("## Applied")
    lines.append("")
    if not planned or not applied:
        lines.append("None.")
        lines.append("")
    else:
        for entry in planned:
            cluster = entry["cluster"]
            edit = cluster["canonical_edit"]
            source = edit.get("source") or {}
            lines.append("### {0} — {1}".format(cluster["id"], cluster.get("claim") or ""))
            lines.append("")
            lines.append("**Tier.** {0}, {1} famil{2} ({3}) · **Disposition.** {4}".format(
                cluster.get("tier"), cluster.get("n_families"),
                "y" if cluster.get("n_families") == 1 else "ies",
                ", ".join(cluster.get("families") or []), cluster.get("disposition")))
            lines.append("")
            lines.append("**Reviewers.** {0}".format(", ".join(
                "`{0}` {1}".format(m.get("reviewer_id"), m.get("finding_id"))
                for m in cluster.get("members") or [])))
            lines.append("")
            lines.append("**Canonical edit.** From `{0}` {1}, accepted by `{2}`. {3}".format(
                source.get("reviewer_id"), source.get("finding_id"),
                edit.get("accepted_by"), edit.get("reason") or ""))
            lines.append("")
            lines.append("```diff")
            for line in (edit.get("old_text") or "").splitlines() or [""]:
                lines.append("- " + line)
            for line in (edit.get("new_text") or "").splitlines() or [""]:
                lines.append("+ " + line)
            lines.append("```")
            lines.append("")

    lines.append("## Not applied")
    lines.append("")
    if not unapplied:
        lines.append("None — every cluster in this reconciliation passed the gate.")
        lines.append("")
    else:
        lines.append("| Cluster | Severity | Tier | Why not |")
        lines.append("| --- | --- | --- | --- |")
        for cluster, reason in unapplied:
            lines.append("| {0} | {1} | {2} | {3} |".format(
                cluster["id"], cluster.get("severity"), cluster.get("tier"),
                reason.replace("|", "\\|")))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- cli -----------------------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Apply the reconciliation's accepted literal edits to the artifact, through the five-condition gate.")
    parser.add_argument("--run-dir", required=True, help="The run directory holding reconciliation.json and manifest.json")
    parser.add_argument("--artifact", default=None,
                        help="The document to edit (default: the reconciliation's own `artifact.path`). Never the run's `inputs/` copy")
    parser.add_argument("--i-authored-this", action="store_true", dest="authored",
                        help="Assert that you wrote the document under review. Required: an artifact is untrusted input, and auto-apply is refused on one the operator did not author")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve every anchor and report what would be applied; write nothing, not even applied.md")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        sys.stderr.write("no such run directory: {0}\n".format(run_dir))
        return EXIT_USAGE

    if not args.authored and not args.dry_run:
        sys.stderr.write(
            "refusing to apply: nobody has asserted authorship of this document.\n"
            "  The artifact is inlined verbatim into every seat's user message, so a document can\n"
            "  instruct its own reviewers to return a consensus-shaped replacement. The gate cannot\n"
            "  tell that apart from a real corroborated fix; an author can.\n"
            "  Pass {0} if this is your own document, or apply the fixes by hand.\n".format(AUTHORSHIP_FLAG))
        return EXIT_USAGE

    try:
        document = load_json(os.path.join(run_dir, "reconciliation.json"), "reconciliation.json")
        manifest = load_json(os.path.join(run_dir, "manifest.json"), "manifest.json")
        artifact_path = resolve_artifact(run_dir, document, args.artifact)
    except ApplyError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_USAGE

    clusters = document.get("clusters") or []
    families = document.get("families_reporting") or []

    # A single-family run cannot satisfy condition 4 for any cluster, so there is nothing to weigh.
    # Saying so is the point: silence here would read as "the gate found nothing", and the truth is
    # that this panel never produced the kind of evidence the gate turns on.
    if len(families) < 2:
        print("auto-apply is a no-op on this run: {0} reporting famil{1} ({2}).".format(
            len(families), "y" if len(families) == 1 else "ies", ", ".join(families) or "none"))
        print("  Condition 4 needs cross-family corroboration of the defect, which a single-family")
        print("  panel cannot produce. Nothing was applied and nothing was written.")
        return EXIT_OK

    candidates = []
    unapplied = []
    for cluster in clusters:
        reason = gate(cluster)
        if reason is None:
            candidates.append(cluster)
        else:
            unapplied.append((cluster, reason))

    # Condition 5, once, over the whole set: the artifact on disk must be the bytes the seats read.
    recorded = manifest.get("artifact_revision")
    in_document = (document.get("artifact") or {}).get("revision")
    aborted = None
    planned = []
    if recorded and in_document and recorded != in_document:
        aborted = ("the manifest and the reconciliation disagree about which revision this run "
                   "reviewed ({0} against {1}); nothing was applied".format(recorded, in_document))
    elif not recorded:
        aborted = ("condition 5: this run's manifest records no `artifact_revision`, so there is no "
                   "revision to check the document against. An unpinned run is not auto-appliable.")
    else:
        actual = sha256_file(artifact_path)
        if actual != recorded:
            aborted = (
                "condition 5: {0} is not the artifact this run reviewed.\n"
                "  manifest artifact_revision: {1}\n"
                "  the file on disk hashes to: {2}\n"
                "  Re-run the panel against the current document rather than applying a review of "
                "another one.".format(artifact_path, recorded, actual))

    with open(artifact_path, "r", encoding="utf-8") as handle:
        text = handle.read()

    if aborted is None and candidates:
        try:
            planned = plan_edits(text, candidates)
        except ApplyError as failure:
            aborted = "{0}\n  Application is all-or-nothing, so none of the {1} candidate(s) was applied.".format(
                failure, len(candidates))

    if aborted is not None:
        sys.stderr.write("{0}\n".format(aborted))
        for cluster in candidates:
            unapplied.append((cluster, "the whole set was aborted before any edit was written"))
        if not args.dry_run:
            _write_log(run_dir, document, artifact_path, [], unapplied, False,
                       note="The set was aborted and the artifact was not touched.")
        _report(unapplied, [])
        return EXIT_ABORTED

    if args.dry_run:
        print("dry run: {0} edit(s) would be applied to {1}; {2} candidate(s) would not.".format(
            len(planned), artifact_path, len(unapplied)))
        _report(unapplied, planned)
        return EXIT_OK

    if planned:
        _write_artifact(artifact_path, apply_edits(text, planned))
        print("applied {0} edit(s) to {1}".format(len(planned), artifact_path))
        print("  the artifact's revision has changed; this reconciliation no longer pins it")
    else:
        print("nothing was applied: no cluster in this reconciliation passed all five conditions")

    log_path = _write_log(run_dir, document, artifact_path, planned, unapplied, bool(planned))
    _report(unapplied, planned)
    print("audit log: {0}".format(log_path))
    return EXIT_OK


def _write_artifact(path, text):
    """One atomic write, with the original file's mode carried onto the replacement.

    `os.replace` swaps the directory entry, so without the `copymode` the document would silently
    take the temp file's private mode — a permission change nobody asked for, made by a tool whose
    whole contract is that it changes only the spans it names.
    """
    tmp = path + ".apply.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def _write_log(run_dir, document, artifact_path, planned, unapplied, applied, note=None):
    path = os.path.join(run_dir, "applied.md")
    report_lib.write_text(path, render_applied(document, artifact_path, planned, unapplied, applied, note))
    return path


def _report(unapplied, planned):
    for entry in planned:
        cluster = entry["cluster"]
        source = (cluster["canonical_edit"].get("source") or {})
        print("  {0} <- `{1}` {2} ({3}, {4} families)".format(
            cluster["id"], source.get("reviewer_id"), source.get("finding_id"),
            cluster.get("tier"), cluster.get("n_families")))
    for cluster, reason in unapplied:
        print("  {0} not applied — {1}".format(cluster["id"], reason.splitlines()[0]))


if __name__ == "__main__":
    sys.exit(main())
