"""Mechanical reconciliation: clustering, tiering, judgment-patch merge, verdict.

Standard library only, and deliberately free of any CLI concern so the replay test can import it
and run the whole algorithm over four report files and a patch without touching argv or the disk.

The division of labour is the spec's: this module computes what a script can compute — normalized
location equality, quote overlap, agreement tiers, the run-level verdict, the final ids — and takes
everything semantic (claim joins, splits, singleton labels, severity arbitrations, dispositions,
contradictions, canonical-edit acceptances, the method caveat) from a validated judgment patch.
Nothing here decides what two findings mean; it decides what two strings share.

Order matters and is fixed: collect → provisional clusters → provisional tiers → merge the patch's
joins and splits → recompute tiers from post-merge membership → apply the patch's labels, severities,
dispositions, contradictions and canonical edits → validate the patch against the post-merge state →
mint final ids → compute the verdict.
"""

import json
import os
import re
import unicodedata

from . import report as report_lib

SEVERITY_ORDER = {"blocker": 0, "should-fix": 1, "nice-to-have": 2}
SEVERITY_RANK = {"blocker": 2, "should-fix": 1, "nice-to-have": 0}
TIERS = ("unanimous", "consensus", "majority", "corroborated-same-family", "singleton")
MATCH_KEYS = ("location", "quote", "claim", "none")
DISPOSITIONS = ("fix-now", "flag-for-human", "defer")
SINGLETON_LABELS = ("blind-spot-catch", "family-specific-false-positive")
LABELLED_TIERS = ("singleton", "corroborated-same-family")

QUOTE_OVERLAP_MIN = 60
SCHEMA_VERSION = "1"

SKIP_FILES = ("manifest.json", "reconciliation.json", "judgment.json", "judgment-request.json", "budget-refusal.json")


# --- normalization and the two mechanical keys ---------------------------------------------------

_LOCATION_STRIP = str.maketrans("", "", "`*_")


def normalize_location(value):
    """NFKC, casefold, drop markup and a leading section sign, collapse non-alphanumerics to spaces."""
    text = unicodedata.normalize("NFKC", value or "")
    text = text.casefold()
    text = text.translate(_LOCATION_STRIP)
    text = text.lstrip()
    while text.startswith("§"):
        text = text[1:].lstrip()
    text = re.sub(r"[^0-9a-z]+", " ", text, flags=re.UNICODE)
    return text.strip()


def normalize_quote(value):
    """NFKC, casefold, collapse whitespace runs to one space, trim."""
    text = unicodedata.normalize("NFKC", value or "")
    text = text.casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def location_match(left, right):
    """Equal normalized forms, or one a prefix of the other at a space boundary."""
    if not left or not right:
        return False
    if left == right:
        return True
    longer, shorter = (left, right) if len(left) >= len(right) else (right, left)
    return longer.startswith(shorter + " ")


def longest_common_substring_len(left, right):
    """Length of the longest substring shared by two strings. O(len(left) * len(right))."""
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for i in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        left_char = left[i - 1]
        for j in range(1, len(right) + 1):
            if left_char == right[j - 1]:
                run = previous[j - 1] + 1
                current[j] = run
                if run > best:
                    best = run
        previous = current
    return best


def quote_match(left, right):
    """Containment either way, or a longest common substring of at least 60 characters."""
    if not left or not right:
        return False
    longer, shorter = (left, right) if len(left) >= len(right) else (right, left)
    if shorter and shorter in longer:
        return True
    return longest_common_substring_len(left, right) >= QUOTE_OVERLAP_MIN


# --- loading -------------------------------------------------------------------------------------

class ReconcileError(Exception):
    """A condition that stops the run before anything is written."""


def load_manifest(run_dir):
    path = os.path.join(run_dir, "manifest.json")
    if not os.path.isfile(path):
        raise ReconcileError("no manifest.json in {0} — the run directory is not a run".format(run_dir))
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_reports(run_dir, manifest):
    """Load and validate every `<reviewer-id>.json` in the run directory.

    Returns (reports, missing_seats). A seat listed in the manifest with no readable, valid report
    is a missing seat and is named in the product; a seat is never silently dropped.
    """
    expected = []
    for seat in manifest.get("seats") or []:
        reviewer_id = seat.get("reviewer_id")
        if reviewer_id:
            expected.append(seat)

    reports = {}
    missing = []
    for seat in expected:
        reviewer_id = seat["reviewer_id"]
        path = os.path.join(run_dir, reviewer_id + ".json")
        if not os.path.isfile(path):
            missing.append({
                "reviewer_id": reviewer_id,
                "lens": seat.get("lens"),
                "family": seat.get("family"),
                "stage": seat.get("status") if seat.get("status") in ("dispatch", "validation", "timeout", "skipped") else "dispatch",
                "reason": "no report file at {0}.json".format(reviewer_id),
            })
            continue
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        errors = report_lib.validate_report(data, lens=data.get("lens"))
        if errors:
            missing.append({
                "reviewer_id": reviewer_id,
                "lens": seat.get("lens"),
                "family": seat.get("family"),
                "stage": "validation",
                "reason": "; ".join(errors[:5]),
            })
            continue
        reports[reviewer_id] = data

    # A report file for a seat the manifest never resolved is still evidence; take it, and say so.
    for name in sorted(os.listdir(run_dir)):
        if not name.endswith(".json") or name in SKIP_FILES:
            continue
        reviewer_id = name[:-len(".json")]
        if reviewer_id in reports or any(m["reviewer_id"] == reviewer_id for m in missing):
            continue
        with open(os.path.join(run_dir, name), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if report_lib.validate_report(data, lens=data.get("lens")):
            continue
        reports[reviewer_id] = data

    return reports, missing


# --- provisional clustering ----------------------------------------------------------------------

class Finding(object):
    """One finding, with its normalized keys precomputed."""

    __slots__ = ("reviewer_id", "family", "lens", "raw", "key", "norm_location", "norm_quote")

    def __init__(self, reviewer_id, family, lens, raw):
        self.reviewer_id = reviewer_id
        self.family = family
        self.lens = lens
        self.raw = raw
        self.key = (reviewer_id, raw.get("id"))
        self.norm_location = normalize_location(raw.get("location"))
        self.norm_quote = normalize_quote(raw.get("quote"))


def collect_findings(reports):
    findings = []
    for reviewer_id in sorted(reports):
        data = reports[reviewer_id]
        for raw in data.get("findings") or []:
            findings.append(Finding(reviewer_id, data.get("family"), data.get("lens"), raw))
    return findings


class _Union(object):
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left, right):
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root
            return True
        return False


def provisional_clusters(findings):
    """Group findings on the two computable keys, location first and then quote.

    Same-reviewer duplicates are unioned by exactly the same keys, which is what "collapse each
    reviewer's own duplicates first" means mechanically: a reviewer ends up with one member per
    cluster whichever side of the panel the match came from.
    """
    union = _Union([f.key for f in findings])
    edges = {}
    for index, left in enumerate(findings):
        for right in findings[index + 1:]:
            key = None
            if location_match(left.norm_location, right.norm_location):
                key = "location"
            elif quote_match(left.norm_quote, right.norm_quote):
                key = "quote"
            if key is None:
                continue
            union.union(left.key, right.key)
            edges.setdefault(frozenset((left.key, right.key)), key)

    groups = {}
    for finding in findings:
        groups.setdefault(union.find(finding.key), []).append(finding)

    clusters = []
    for members in groups.values():
        member_keys = {m.key for m in members}
        keys_used = {key for pair, key in edges.items() if set(pair) <= member_keys}
        if not keys_used:
            match_key = "none"
        elif "location" in keys_used:
            match_key = "location"
        else:
            match_key = "quote"
        clusters.append({
            "members": sorted(members, key=lambda f: f.key),
            "match_key": match_key,
            "judgment": False,
            "split_reason": None,
            "provisional_id": None,
        })

    clusters.sort(key=lambda c: min(m.key for m in c["members"]))
    for number, cluster in enumerate(clusters, start=1):
        cluster["provisional_id"] = "P-{0}".format(number)
    return clusters


# --- tiers -----------------------------------------------------------------------------------------

def cluster_families(cluster):
    return sorted({m.family for m in cluster["members"] if m.family})


def cluster_reviewers(cluster):
    return sorted({m.reviewer_id for m in cluster["members"]})


def compute_tier(cluster, seats_expected, seats_reporting):
    reviewers = set(cluster_reviewers(cluster))
    families = cluster_families(cluster)
    n_reviewers = len(reviewers)
    n_families = len(families)
    reporting = len(seats_reporting)
    if n_families >= 2 and set(seats_expected) and set(seats_expected) <= reviewers:
        return "unanimous"
    if n_families >= 2:
        return "consensus"
    if n_reviewers >= 2 and n_reviewers > reporting / 2.0:
        return "majority"
    if n_reviewers >= 2:
        return "corroborated-same-family"
    return "singleton"


# --- patch merge -------------------------------------------------------------------------------

def _index_by_provisional(clusters):
    return {cluster["provisional_id"]: cluster for cluster in clusters}


def apply_patch_topology(clusters, patch):
    """Apply `splits`, then `claim_joins`. Returns (clusters, errors).

    Splits run first so a join can name a split's product: a mechanically joined pair the patch pulls
    apart usually exists precisely so one half can be joined to something else. A split's products are
    numbered `<from>.<n>` by the order of the `groups` array, with any member the patch did not place
    landing in a final group of its own, so the ids a later `claim_joins` entry names are derivable
    from the patch alone.
    """
    errors = []
    alive = list(clusters)

    for entry in patch.get("splits") or []:
        source_id = entry.get("from")
        source = next((c for c in alive if c["provisional_id"] == source_id), None)
        if source is None:
            errors.append("splits: unknown or already-split provisional id {0!r}".format(source_id))
            continue
        by_key = {m.key: m for m in source["members"]}
        produced = []
        seen = set()
        bad = False
        for group in entry.get("groups") or []:
            members = []
            for ref in group:
                key = (ref.get("reviewer_id"), ref.get("finding_id"))
                if key not in by_key:
                    errors.append("splits: {0} names {1} {2}, which is not a member of it".format(source_id, key[0], key[1]))
                    bad = True
                    continue
                if key in seen:
                    errors.append("splits: {0} lists {1} {2} in two groups".format(source_id, key[0], key[1]))
                    bad = True
                    continue
                seen.add(key)
                members.append(by_key[key])
            produced.append(members)
        if bad:
            continue
        leftover = [by_key[key] for key in sorted(by_key) if key not in seen]
        if leftover:
            produced.append(leftover)
        alive.remove(source)
        for number, members in enumerate(produced, start=1):
            if not members:
                continue
            alive.append({
                "members": sorted(members, key=lambda f: f.key),
                "match_key": source["match_key"] if len(members) > 1 else "none",
                "judgment": source.get("judgment", False),
                "split_reason": entry.get("reason"),
                "provisional_id": "{0}.{1}".format(source_id, number),
            })

    for entry in patch.get("claim_joins") or []:
        ids = entry.get("merge") or []
        by_id = _index_by_provisional(alive)
        unknown = [pid for pid in ids if pid not in by_id]
        if unknown:
            errors.append("claim_joins: unknown or already-merged provisional id(s) {0}".format(", ".join(unknown)))
            continue
        targets = [by_id[pid] for pid in ids]
        if len(targets) < 2:
            errors.append("claim_joins: {0} merges fewer than two clusters".format(", ".join(ids) or "(empty)"))
            continue
        split_reasons = [t["split_reason"] for t in targets if t.get("split_reason")]
        merged = {
            "members": sorted([m for target in targets for m in target["members"]], key=lambda f: f.key),
            "match_key": "claim",
            "judgment": True,
            "split_reason": split_reasons[0] if split_reasons else None,
            "provisional_id": "+".join(sorted(ids, key=_provisional_sort_key)),
            "join_reason": entry.get("reason"),
        }
        for target in targets:
            alive.remove(target)
        alive.append(merged)

    return alive, errors


def _provisional_sort_key(pid):
    parts = re.findall(r"\d+", pid or "")
    return tuple(int(p) for p in parts) or (0,)


# --- assembling a cluster record ------------------------------------------------------------------

def _primary(members_of_one_reviewer):
    """The member that represents a reviewer in a cluster: worst severity first, then lowest id."""
    return sorted(members_of_one_reviewer, key=lambda f: (SEVERITY_ORDER.get(f.raw.get("severity"), 9), _finding_number(f)))[0]


def _finding_number(finding):
    digits = re.findall(r"\d+", finding.raw.get("id") or "")
    return int(digits[0]) if digits else 0


def build_member_records(cluster):
    """One record per contributing reviewer, carrying the finding ids its duplicates collapsed into it."""
    by_reviewer = {}
    for member in cluster["members"]:
        by_reviewer.setdefault(member.reviewer_id, []).append(member)

    records = []
    for reviewer_id in sorted(by_reviewer):
        group = by_reviewer[reviewer_id]
        primary = _primary(group)
        collapsed = [f.raw.get("id") for f in sorted(group, key=_finding_number) if f is not primary]
        records.append({
            "reviewer_id": reviewer_id,
            "finding_id": primary.raw.get("id"),
            "severity": primary.raw.get("severity"),
            "confidence": primary.raw.get("confidence"),
            "cited": primary.raw.get("citation") is not None,
            "collapsed_finding_ids": collapsed,
            "_finding": primary,
            "_family": primary.family,
            "_all": sorted(group, key=_finding_number),
        })
    return records


def shared_quote(records):
    quotes = [normalize_quote(r["_finding"].raw.get("quote")) for r in records]
    quotes = [q for q in quotes if q]
    if not quotes:
        return None
    if len(quotes) == 1:
        return records[0]["_finding"].raw.get("quote")
    for index, left in enumerate(quotes):
        for right in quotes[index + 1:]:
            if not quote_match(left, right):
                return None
    shortest = min(records, key=lambda r: len(r["_finding"].raw.get("quote") or ""))
    return shortest["_finding"].raw.get("quote")


def severity_distance(records):
    ranks = [SEVERITY_RANK.get(r["severity"], 0) for r in records]
    return max(ranks) - min(ranks) if ranks else 0


# --- the whole algorithm ---------------------------------------------------------------------------

def reconcile(run_dir, patch=None, artifact_text=None, now=None):
    """Run the algorithm end to end. Returns (document, errors, context).

    `document` is the `reconciliation.json` contract, or None when the patch is absent or invalid.
    `context` carries the provisional clusters so a caller with no patch can write a judgment request.
    """
    manifest = load_manifest(run_dir)
    reports, missing = load_reports(run_dir, manifest)

    seats_expected = [seat["reviewer_id"] for seat in (manifest.get("seats") or []) if seat.get("reviewer_id")]
    for reviewer_id in sorted(reports):
        if reviewer_id not in seats_expected:
            seats_expected.append(reviewer_id)
    seats_reporting = sorted(reports)

    findings = collect_findings(reports)
    anchor_drops = []
    if artifact_text is not None:
        findings, anchor_drops = _drop_unreal_anchors(findings, artifact_text)

    provisional = provisional_clusters(findings)
    for cluster in provisional:
        cluster["provisional_tier"] = compute_tier(cluster, seats_expected, seats_reporting)

    context = {
        "manifest": manifest,
        "reports": reports,
        "missing_seats": missing,
        "seats_expected": seats_expected,
        "seats_reporting": seats_reporting,
        "provisional": provisional,
        "anchor_drops": anchor_drops,
    }

    if patch is None:
        return None, ["no judgment patch supplied"], context

    errors = validate_patch_shape(patch, manifest, reports)
    if errors:
        return None, errors, context

    merged, topology_errors = apply_patch_topology(provisional, patch)
    errors.extend(topology_errors)
    if errors:
        return None, errors, context

    for cluster in merged:
        cluster["tier"] = compute_tier(cluster, seats_expected, seats_reporting)

    document, merge_errors = _merge_judgment(merged, patch, manifest, seats_expected, seats_reporting, missing, reports, now)
    if merge_errors:
        return None, merge_errors, context
    document["_anchor_drops"] = anchor_drops
    return document, [], context


def _drop_unreal_anchors(findings, artifact_text):
    """Drop any member whose `quote` is not text in the pinned artifact, and record the drop."""
    haystack = normalize_quote(artifact_text)
    kept = []
    drops = []
    for finding in findings:
        needle = finding.norm_quote
        if needle.endswith("…"):
            needle = needle[:-1].strip()
        if needle and needle not in haystack:
            drops.append({
                "reviewer_id": finding.reviewer_id,
                "finding_id": finding.raw.get("id"),
                "reason": "quote does not occur in the pinned artifact",
            })
            continue
        kept.append(finding)
    return kept, drops


def validate_patch_shape(patch, manifest, reports):
    """Everything checkable before the merge: ids exist, references resolve, enums hold."""
    errors = []
    if patch.get("schema_version") != SCHEMA_VERSION:
        errors.append("judgment: `schema_version` must be \"1\"")
    if patch.get("run_id") != manifest.get("run_id"):
        errors.append("judgment: `run_id` {0!r} does not match the manifest's {1!r}".format(patch.get("run_id"), manifest.get("run_id")))
    author = patch.get("author")
    if author not in ("host", "synthesis"):
        errors.append("judgment: `author` must be \"host\" or \"synthesis\"")
    if author == "synthesis" and patch.get("rulings"):
        errors.append("judgment: a patch from `synthesis` may not carry `rulings`")

    known = {}
    for reviewer_id, data in reports.items():
        known[reviewer_id] = {f.get("id") for f in (data.get("findings") or [])}

    def check_ref(where, ref):
        reviewer_id = ref.get("reviewer_id")
        finding_id = ref.get("finding_id")
        if reviewer_id not in known:
            errors.append("{0}: no validated report from {1!r}".format(where, reviewer_id))
        elif finding_id not in known[reviewer_id]:
            errors.append("{0}: {1} has no finding {2!r}".format(where, reviewer_id, finding_id))

    for entry in patch.get("splits") or []:
        for group in entry.get("groups") or []:
            for ref in group:
                check_ref("splits from {0}".format(entry.get("from")), ref)
    for entry in patch.get("contradictions") or []:
        for ref in entry.get("contradicted_by") or []:
            check_ref("contradictions on {0}".format(entry.get("cluster")), ref)
    for entry in patch.get("canonical_edits") or []:
        if entry.get("source"):
            check_ref("canonical_edits on {0}".format(entry.get("cluster")), entry["source"])

    for entry in patch.get("singleton_labels") or []:
        if entry.get("label") not in SINGLETON_LABELS:
            errors.append("singleton_labels on {0}: `label` must be one of {1}".format(entry.get("cluster"), list(SINGLETON_LABELS)))
        if not (entry.get("reason") or "").strip():
            errors.append("singleton_labels on {0}: `reason` is required".format(entry.get("cluster")))
    for entry in patch.get("dispositions") or []:
        if entry.get("disposition") not in DISPOSITIONS:
            errors.append("dispositions on {0}: must be one of {1}".format(entry.get("cluster"), list(DISPOSITIONS)))
    for entry in patch.get("severities") or []:
        if entry.get("severity") not in SEVERITY_ORDER:
            errors.append("severities on {0}: must be one of {1}".format(entry.get("cluster"), list(SEVERITY_ORDER)))
    if not (patch.get("method_caveat") or "").strip():
        errors.append("judgment: `method_caveat` is required")
    return errors


def _resolve_cluster_ref(clusters, reference):
    """Find the post-merge cluster a patch entry's provisional id now lives in."""
    for cluster in clusters:
        pid = cluster["provisional_id"]
        if pid == reference:
            return cluster
        if "+" in pid and reference in pid.split("+"):
            return cluster
        if pid.startswith(reference + "."):
            return cluster
    return None


def _merge_judgment(clusters, patch, manifest, seats_expected, seats_reporting, missing, reports, now):
    errors = []
    records = []
    for cluster in clusters:
        members = build_member_records(cluster)
        families = sorted({r["_family"] for r in members if r["_family"]})
        records.append({
            "cluster": cluster,
            "members": members,
            "families": families,
        })

    by_ref = {}
    for record in records:
        pid = record["cluster"]["provisional_id"]
        by_ref[pid] = record
        for part in pid.split("+"):
            by_ref.setdefault(part, record)

    def resolve(reference, where):
        record = by_ref.get(reference) or _wrap(_resolve_cluster_ref(clusters, reference), records)
        if record is None:
            errors.append("{0}: unknown provisional id {1!r}".format(where, reference))
        return record

    for entry in patch.get("severities") or []:
        record = resolve(entry.get("cluster"), "severities")
        if record is not None:
            record["severity"] = entry.get("severity")
            record["arbitration_reason"] = entry.get("arbitration_reason") or "arbitrated"

    for entry in patch.get("singleton_labels") or []:
        record = resolve(entry.get("cluster"), "singleton_labels")
        if record is not None:
            record["singleton_label"] = entry.get("label")
            record["singleton_reason"] = entry.get("reason")

    for entry in patch.get("dispositions") or []:
        record = resolve(entry.get("cluster"), "dispositions")
        if record is not None:
            record["disposition"] = entry.get("disposition")
            record["disposition_reason"] = entry.get("disposition_reason") or ""

    for entry in patch.get("contradictions") or []:
        record = resolve(entry.get("cluster"), "contradictions")
        if record is not None:
            record["contradicted_by"] = [
                {"reviewer_id": ref.get("reviewer_id"), "finding_id": ref.get("finding_id")}
                for ref in entry.get("contradicted_by") or []
            ]

    for entry in patch.get("canonical_edits") or []:
        record = resolve(entry.get("cluster"), "canonical_edits")
        if record is not None:
            record["canonical_edit_entry"] = entry

    for entry in patch.get("rulings") or []:
        record = resolve(entry.get("cluster"), "rulings")
        if record is not None:
            record["ruling"] = {"text": entry.get("ruling"), "owner_to_confirm": bool(entry.get("owner_to_confirm"))}

    if errors:
        return None, errors

    # Severity, label and disposition, with the two patch-validation checks the recompute produces.
    for record in records:
        cluster = record["cluster"]
        members = record["members"]
        tier = cluster["tier"]
        if "severity" not in record:
            severities = [m["severity"] for m in members]
            worst = min(severities, key=lambda s: SEVERITY_ORDER.get(s, 9))
            record["severity"] = worst
            record["arbitration_reason"] = "members agree" if len(set(severities)) == 1 else "highest member severity; the patch supplied no arbitration"
        if tier in LABELLED_TIERS and not record.get("singleton_label"):
            errors.append("post-merge {0} cluster {1} ({2}) carries no label in the patch".format(tier, cluster["provisional_id"], _describe(members)))
        if tier not in LABELLED_TIERS and record.get("singleton_label"):
            errors.append("the patch labels {0}, which merged into a {1} cluster and is no longer labellable".format(cluster["provisional_id"], tier))
        if "disposition" not in record:
            errors.append("cluster {0} ({1}) has no disposition in the patch".format(cluster["provisional_id"], _describe(members)))

    seen_findings = {}
    for record in records:
        for member in record["members"]:
            for finding in member["_all"]:
                if finding.key in seen_findings:
                    errors.append("{0} {1} lands in two final clusters".format(finding.key[0], finding.key[1]))
                seen_findings[finding.key] = record

    if errors:
        return None, errors

    # A contradiction forces flag-for-human; so does a judgment-call with no host ruling.
    for record in records:
        if record.get("contradicted_by"):
            record["disposition"] = "flag-for-human"

    ordered = sorted(records, key=_presentation_key)
    counters = {"blocker": 0, "should-fix": 0, "nice-to-have": 0}
    prefix = {"blocker": "BK", "should-fix": "SF", "nice-to-have": "NH"}
    clusters_out = []
    for record in ordered:
        severity = record["severity"]
        counters[severity] += 1
        clusters_out.append(_render_cluster(record, "{0}-{1}".format(prefix[severity], counters[severity])))

    document = {
        "schema_version": SCHEMA_VERSION,
        "run_id": manifest.get("run_id"),
        "artifact": {
            "path": manifest.get("artifact"),
            "revision": manifest.get("artifact_revision"),
        },
        "panel": manifest.get("panel") or "ad-hoc",
        "reconciler": patch.get("author"),
        "judgment": {
            "path": "judgment.json",
            "author": patch.get("author"),
            "generated_at": patch.get("generated_at"),
        },
        "generated_at": now or patch.get("generated_at"),
        "seats_expected": seats_expected,
        "seats_reporting": seats_reporting,
        "missing_seats": missing,
        "families_reporting": sorted({reports[r].get("family") for r in seats_reporting if reports[r].get("family")}),
        "clusters": clusters_out,
        "disagreements": _disagreements(clusters_out, patch),
        "verdict": None,
        "method_caveat": patch.get("method_caveat"),
        "counts": _counts(clusters_out),
    }
    document["verdict"] = compute_verdict(clusters_out, reports)
    return document, []


def _wrap(cluster, records):
    if cluster is None:
        return None
    for record in records:
        if record["cluster"] is cluster:
            return record
    return None


def _describe(members):
    return ", ".join("{0} {1}".format(m["reviewer_id"], m["finding_id"]) for m in members)


def _presentation_key(record):
    """Blockers first, then within a severity the cross-family clusters before the single-seat ones."""
    return (
        SEVERITY_ORDER.get(record["severity"], 9),
        -len(record["families"]),
        -len(record["members"]),
        _provisional_sort_key(record["cluster"]["provisional_id"]),
    )


def _render_cluster(record, final_id):
    cluster = record["cluster"]
    members = record["members"]
    primary = sorted(members, key=lambda m: (SEVERITY_ORDER.get(m["severity"], 9), m["reviewer_id"]))[0]
    tags = []
    for member in members:
        for finding in member["_all"]:
            for tag in finding.raw.get("tags") or []:
                if tag not in tags:
                    tags.append(tag)

    canonical_edit, edit_conflict = _canonical_edit(record)

    return {
        "id": final_id,
        "provisional_id": cluster["provisional_id"],
        "claim": primary["_finding"].raw.get("claim"),
        "location": primary["_finding"].raw.get("location"),
        "quote": shared_quote(members),
        "members": [
            {
                "reviewer_id": m["reviewer_id"],
                "finding_id": m["finding_id"],
                "severity": m["severity"],
                "confidence": m["confidence"],
                "cited": m["cited"],
                "collapsed_finding_ids": m["collapsed_finding_ids"],
            }
            for m in members
        ],
        "n_reviewers": len(members),
        "n_families": len(record["families"]),
        "families": record["families"],
        "tier": cluster["tier"],
        "match_key": cluster["match_key"],
        "judgment": bool(cluster.get("judgment")),
        "split_reason": cluster.get("split_reason"),
        "singleton_label": record.get("singleton_label"),
        "singleton_reason": record.get("singleton_reason"),
        "severity": record["severity"],
        "severity_spread": [{"reviewer_id": m["reviewer_id"], "severity": m["severity"]} for m in members],
        "arbitration_reason": record.get("arbitration_reason") or "members agree",
        "disposition": record["disposition"],
        "disposition_reason": record.get("disposition_reason") or "",
        "ruling": record.get("ruling"),
        "contradicted_by": record.get("contradicted_by") or [],
        "canonical_edit": canonical_edit,
        "edit_conflict": edit_conflict,
        "tags": sorted(tags),
    }


def _canonical_edit(record):
    edits = []
    for member in record["members"]:
        for finding in member["_all"]:
            edit = finding.raw.get("literal_edit")
            if isinstance(edit, dict) and edit.get("old_text"):
                edits.append((member["reviewer_id"], finding.raw.get("id"), edit))
    if not edits:
        return None, False
    replacements = {edit.get("new_text") for _r, _f, edit in edits}
    entry = record.get("canonical_edit_entry")
    if entry is None or not entry.get("accepted"):
        return None, len(replacements) > 1
    source = entry.get("source") or {}
    for reviewer_id, finding_id, edit in edits:
        if reviewer_id == source.get("reviewer_id") and finding_id == source.get("finding_id"):
            return {
                "old_text": edit.get("old_text"),
                "new_text": edit.get("new_text"),
                "source": {"reviewer_id": reviewer_id, "finding_id": finding_id},
                "accepted_by": entry.get("accepted_by") or "judgment patch",
                "reason": entry.get("reason") or "",
            }, False
    return None, len(replacements) > 1


def _disagreements(clusters, patch):
    contradictions = []
    spreads = []
    for cluster in clusters:
        if cluster["contradicted_by"]:
            contradictions.append({
                "cluster": cluster["id"],
                "flagged_by": [m["reviewer_id"] for m in cluster["members"]],
                "contradicted_by": cluster["contradicted_by"],
                "ruling": cluster["disposition_reason"],
            })
        ranks = [SEVERITY_RANK.get(m["severity"], 0) for m in cluster["members"]]
        if ranks and max(ranks) - min(ranks) >= 2:
            high = [m["reviewer_id"] for m in cluster["members"] if SEVERITY_RANK.get(m["severity"], 0) == max(ranks)]
            low = [m["reviewer_id"] for m in cluster["members"] if SEVERITY_RANK.get(m["severity"], 0) == min(ranks)]
            spreads.append({
                "cluster": cluster["id"],
                "steps": max(ranks) - min(ranks),
                "high_side": high,
                "low_side": low,
                "spread": cluster["severity_spread"],
                "ruling": cluster["arbitration_reason"],
            })

    altitude = []
    by_provisional = {}
    for cluster in clusters:
        by_provisional[cluster["provisional_id"]] = cluster["id"]
        for part in cluster["provisional_id"].split("+"):
            by_provisional.setdefault(part, cluster["id"])
    for entry in patch.get("altitude_splits") or []:
        altitude.append({
            "reviewer_id": entry.get("reviewer_id"),
            "clusters": [by_provisional.get(pid, pid) for pid in entry.get("clusters") or []],
            "note": entry.get("note") or "",
        })
    return {"contradictions": contradictions, "severity_spreads": spreads, "altitude_splits": altitude}


def _counts(clusters):
    by_severity = {}
    by_tier = {}
    by_disposition = {}
    for cluster in clusters:
        by_severity[cluster["severity"]] = by_severity.get(cluster["severity"], 0) + 1
        by_tier[cluster["tier"]] = by_tier.get(cluster["tier"], 0) + 1
        by_disposition[cluster["disposition"]] = by_disposition.get(cluster["disposition"], 0) + 1
    return {
        "clusters": len(clusters),
        "by_severity": by_severity,
        "by_tier": by_tier,
        "by_disposition": by_disposition,
    }


def compute_verdict(clusters, reports):
    """Severity and disposition together, per the spec's four bullets."""
    blockers = [c for c in clusters if c["severity"] == "blocker"]
    rework_seats = sum(1 for data in reports.values() if data.get("verdict") == "rework")
    families_at_rework = len({data.get("family") for data in reports.values() if data.get("verdict") == "rework"})
    if families_at_rework >= 2:
        return "rework"
    if blockers:
        for cluster in blockers:
            if cluster["disposition"] in ("flag-for-human", "defer") or cluster["contradicted_by"]:
                return "rework"
        return "fix-then-ship"
    for cluster in clusters:
        if cluster["severity"] == "should-fix" and cluster["disposition"] in ("fix-now", "flag-for-human"):
            return "fix-then-ship"
    del rework_seats
    return "ship"


# --- judgment request ------------------------------------------------------------------------------

def judgment_request(context):
    """What the judgment supplier has to answer, one entry per provisional cluster."""
    entries = []
    for cluster in context["provisional"]:
        members = build_member_records(cluster)
        entries.append({
            "provisional_id": cluster["provisional_id"],
            "match_key": cluster["match_key"],
            "provisional_tier": cluster["provisional_tier"],
            "families": sorted({m["_family"] for m in members if m["_family"]}),
            "members": [
                {
                    "reviewer_id": m["reviewer_id"],
                    "finding_id": m["finding_id"],
                    "severity": m["severity"],
                    "confidence": m["confidence"],
                    "cited": m["cited"],
                    "collapsed_finding_ids": m["collapsed_finding_ids"],
                    "location": m["_finding"].raw.get("location"),
                    "claim": m["_finding"].raw.get("claim"),
                }
                for m in members
            ],
            "required": _required_fields(cluster["provisional_tier"]),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": context["manifest"].get("run_id"),
        "seats_expected": context["seats_expected"],
        "seats_reporting": context["seats_reporting"],
        "missing_seats": context["missing_seats"],
        "note": (
            "Write judgment.json in this directory against schemas/judgment-patch.schema.json, then re-run reconcile.py "
            "with --judgment. Provisional tiers are computed before your claim joins and splits and are recomputed after "
            "them; label every cluster that is a singleton or corroborated-same-family AFTER the merge."
        ),
        "clusters": entries,
    }


def _required_fields(tier):
    required = ["dispositions"]
    if tier in LABELLED_TIERS:
        required.append("singleton_labels")
    required.append("severities (only when the members disagree)")
    return required
