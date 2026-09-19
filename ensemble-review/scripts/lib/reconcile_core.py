"""Mechanical reconciliation: clustering, tiering, judgment-patch merge, verdict.

Standard library only, and deliberately free of any CLI concern so the replay test can import it
and run the whole algorithm over four report files and a patch without touching argv or the disk.

The division of labour is the spec's: this module computes what a script can compute — normalized
location equality, quote overlap, agreement tiers, the run-level verdict, the final ids — and takes
everything semantic (claim joins, splits, singleton labels, severity arbitrations, dispositions,
contradictions, canonical-edit acceptances, the method caveat) from a validated judgment patch.
Nothing here decides what two findings mean; it decides what two strings share.

Order matters and is fixed: collect → provisional clusters → drop unreal anchors from them, when an
artifact was pinned → provisional tiers → merge the patch's splits and then its joins → recompute
tiers from post-merge membership → apply the patch's labels, severities, dispositions,
contradictions and canonical edits → validate the patch against the post-merge state → mint final
ids → compute the verdict. The anchor check runs after the ids are minted and never renumbers them:
`P-n` is the judgment patch's whole reference vocabulary.
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

# The judgment patch's contract, unchanged. The product's contract is versioned separately and is at
# "2": `anchor_drops` is required, so a v1 reader would not find a field it is entitled to.
SCHEMA_VERSION = "1"
RECONCILIATION_SCHEMA_VERSION = "2"

SKIP_FILES = ("manifest.json", "reconciliation.json", "judgment.json", "judgment-request.json", "budget-refusal.json")

# Manifest seat status → the `missing_seats[].stage` enum the reconciliation schema allows. Only
# consulted for a seat with no readable report, and deliberately exhaustive: see `missing_stage`.
MISSING_STAGE = {
    "pending": "dispatch",      # resolved and never dispatched
    "dispatching": "dispatch",  # claimed by a run that did not finish, or held by another process
    "failed": "dispatch",       # the call itself failed
    "dispatch": "dispatch",
    "ok": "validation",         # the manifest says the seat reported; the file did not survive
    "validation": "validation",
    "timeout": "timeout",
    "skipped": "skipped",
}


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
    # `\W` is Unicode-aware by default on str patterns, so "Раздел 3" and "Κεφάλαιο 3" keep their
    # words instead of both collapsing to "3" and matching each other. Underscores are already gone.
    text = re.sub(r"\W+", " ", text)
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


def missing_stage(seat):
    """Map a manifest seat status onto the four stages the product's `missing_seats` enum allows.

    Explicit both ways: a status this table does not name is a manifest the script does not
    understand, and guessing `dispatch` would report a seat as never dispatched on no evidence.
    """
    status = seat.get("status")
    if status in MISSING_STAGE:
        return MISSING_STAGE[status]
    raise ReconcileError(
        "manifest seat {0!r} has status {1!r}, which is not one of {2}; `missing_seats[].stage` "
        "cannot be derived from it".format(seat.get("reviewer_id"), status, sorted(MISSING_STAGE)))


def load_reports(run_dir, manifest):
    """Load and validate every `<reviewer-id>.json` in the run directory.

    Returns (reports, missing_seats). A seat listed in the manifest with no readable, valid report
    is a missing seat and is named in the product; a seat is never silently dropped. A report file
    for a reviewer the manifest never resolved is the other error: `seats_expected` is exactly what
    the manifest resolved, and an unexpected file would silently raise the `unanimous` bar.
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
                "stage": missing_stage(seat),
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

    # A report file for a seat the manifest never resolved is a run directory that does not match
    # its manifest. It is not an extra expected seat: `seats_expected` is the `unanimous`
    # denominator, and a stray file would raise that bar with no seat behind it.
    stray = []
    for name in sorted(os.listdir(run_dir)):
        if not name.endswith(".json") or name in SKIP_FILES:
            continue
        # `<reviewer-id>.json` is a report. Everything else the run writes about a seat carries a
        # qualifier before its extension — `<id>.failed.json` from run_panel, `<id>.invalid.txt`
        # from dispatch — and is a record of the failure, not a second report from a fifth seat.
        stem, _dot, qualifier = name[:-len(".json")].partition(".")
        if qualifier:
            continue
        if stem in reports or any(m["reviewer_id"] == stem for m in missing):
            continue
        stray.append(name)
    if stray:
        raise ReconcileError(
            "report file(s) {0} in {1} for reviewer(s) the manifest never resolved; either add the "
            "seat to manifest.json or remove the file".format(", ".join(stray), run_dir))

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
    carriers = [r for r in records if normalize_quote(r["_finding"].raw.get("quote"))]
    quotes = [normalize_quote(r["_finding"].raw.get("quote")) for r in carriers]
    if not quotes:
        return None
    if len(quotes) == 1:
        # The one member that carries a quote, not `records[0]` — `records` is sorted by reviewer_id.
        return carriers[0]["_finding"].raw.get("quote")
    for index, left in enumerate(quotes):
        for right in quotes[index + 1:]:
            if not quote_match(left, right):
                return None
    shortest = min(carriers, key=lambda r: len(r["_finding"].raw.get("quote") or ""))
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

    # Exactly the reviewer ids the manifest resolved — the `unanimous` denominator, and nothing else.
    # `load_reports` has already refused a report file for a reviewer that is not one of them.
    seats_expected = [seat["reviewer_id"] for seat in (manifest.get("seats") or []) if seat.get("reviewer_id")]
    seats_reporting = sorted(reports)

    findings = collect_findings(reports)
    provisional = provisional_clusters(findings)

    anchor_drops = []
    if artifact_text is not None:
        provisional, anchor_drops = _drop_unreal_anchors(provisional, artifact_text)

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
    document["anchor_drops"] = anchor_drops
    return document, [], context


_ELLIPSES = ("…", "...")


def anchor_is_real(text, haystack):
    """Whether a `quote` or an `old_text` occurs in the normalized artifact.

    The validator truncates a `quote` at 300 characters and marks the cut with U+2026. NFKC — which
    `normalize_quote` applies to both sides — maps U+2026 to "...", so the marker cannot be
    recognised after normalization and the artifact contains neither form. The raw string is
    therefore inspected for the marker first; a marked quote is matched as the prefix it is.
    """
    raw = (text or "").strip()
    if not raw:
        return True
    truncated = any(raw.endswith(marker) for marker in _ELLIPSES)
    if truncated:
        raw = raw.rstrip("….").rstrip()
    needle = normalize_quote(raw)
    return not needle or needle in haystack


def _drop_unreal_anchors(clusters, artifact_text):
    """Drop any member whose `quote` or `old_text` is not text in the pinned artifact.

    This runs **after** the provisional ids are minted, and drops the member from its cluster rather
    than the finding from the run: `P-n` is the judgment patch's entire reference vocabulary, and a
    check that renumbered the clusters would invalidate every patch written against the previous
    run. A cluster left with no members disappears; a cluster left with one is a cluster of one and
    no longer rests on the key that joined it.
    """
    haystack = normalize_quote(artifact_text)
    kept = []
    drops = []
    for cluster in clusters:
        survivors = []
        for finding in cluster["members"]:
            unreal = []
            if not anchor_is_real(finding.raw.get("quote"), haystack):
                unreal.append("quote")
            edit = finding.raw.get("literal_edit")
            if isinstance(edit, dict) and not anchor_is_real(edit.get("old_text"), haystack):
                unreal.append("literal_edit.old_text")
            if unreal:
                drops.append({
                    "reviewer_id": finding.reviewer_id,
                    "finding_id": finding.raw.get("id"),
                    "cluster": cluster["provisional_id"],
                    "reason": "{0} does not occur in the pinned artifact".format(" and ".join(unreal)),
                })
                continue
            survivors.append(finding)
        if not survivors:
            continue
        if len(survivors) < len(cluster["members"]):
            cluster["members"] = survivors
            if len(survivors) == 1:
                cluster["match_key"] = "none"
        kept.append(cluster)
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

    # One entry per cluster per list. Two entries for one cluster silently last-wins otherwise, and
    # the losing entry is a judgment the supplier wrote down and the product never carried.
    for name in ("singleton_labels", "severities", "dispositions", "contradictions", "canonical_edits", "rulings"):
        seen = set()
        for entry in patch.get(name) or []:
            reference = entry.get("cluster")
            if reference in seen:
                errors.append("{0}: more than one entry for {1!r}".format(name, reference))
            seen.add(reference)
    seen_splits = set()
    for entry in patch.get("splits") or []:
        reference = entry.get("from")
        if reference in seen_splits:
            errors.append("splits: more than one entry for {0!r}".format(reference))
        seen_splits.add(reference)

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


def _resolve_cluster_refs(clusters, reference):
    """Every post-merge cluster a patch entry's provisional id could now name.

    More than one means the patch split `reference` and then named the parent: which half the entry
    meant is not recoverable, so the caller reports it rather than binding to the first product.
    """
    matches = []
    for cluster in clusters:
        parts = cluster["provisional_id"].split("+")
        if reference in parts or any(part.startswith(reference + ".") for part in parts):
            matches.append(cluster)
    return matches


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
        record = by_ref.get(reference)
        if record is not None:
            return record
        matches = [_wrap(cluster, records) for cluster in _resolve_cluster_refs(clusters, reference)]
        matches = [match for match in matches if match is not None]
        if len(matches) > 1:
            errors.append("{0}: {1!r} was split into {2}; name the product, not the parent".format(
                where, reference, ", ".join(sorted(m["cluster"]["provisional_id"] for m in matches))))
            return None
        if not matches:
            errors.append("{0}: unknown provisional id {1!r}".format(where, reference))
            return None
        return matches[0]

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
            if len(set(severities)) == 1:
                record["severity"] = severities[0]
                record["arbitration_reason"] = "members agree"
            else:
                # Step 4 is the supplier's. Taking the worst severity here would be the script
                # arbitrating a disagreement on no reasoning, in a field that reads as a ruling.
                errors.append("cluster {0} ({1}) has members at {2} and the patch supplies no `severities` entry".format(
                    cluster["provisional_id"], _describe(members),
                    ", ".join(sorted(set(severities), key=lambda s: SEVERITY_ORDER.get(s, 9)))))
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

    # A contradiction forces flag-for-human, whatever the patch said.
    for record in records:
        if record.get("contradicted_by"):
            record["disposition"] = "flag-for-human"

    # The `synthesis` persona always flags a design fork, and this is where that is enforced. An
    # unattended persona disposing a fork is a human decision recorded as settled by nobody. A host
    # is not held to it: it has an owner to answer to and its `disposition_reason` is the trace,
    # which is why `rulings` stays optional and reserved for a decision of record.
    #
    # A cluster whose judgment calls are all tagged `gap` is not a fork and is not forced anywhere:
    # it is a determinate fix that was filed under the wrong `change_kind`, and it goes on the fix
    # list like any other. `_needs_a_human_ruling` is where that reading lives.
    if patch.get("author") == "synthesis":
        for record in records:
            if _needs_a_human_ruling(record) and record["disposition"] != "flag-for-human":
                errors.append(
                    "cluster {0} ({1}) is all `judgment-call` and this patch from `synthesis` disposes it "
                    "{2}; the synthesis persona always flags a judgment call".format(
                        record["cluster"]["provisional_id"], _describe(record["members"]), record["disposition"]))
    if errors:
        return None, errors

    ordered = sorted(records, key=_presentation_key)
    counters = {"blocker": 0, "should-fix": 0, "nice-to-have": 0}
    prefix = {"blocker": "BK", "should-fix": "SF", "nice-to-have": "NH"}
    clusters_out = []
    for record in ordered:
        severity = record["severity"]
        counters[severity] += 1
        clusters_out.append(_render_cluster(record, "{0}-{1}".format(prefix[severity], counters[severity]), patch.get("author")))

    document = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
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
        "anchor_drops": [],
        "families_reporting": sorted({reports[r].get("family") for r in seats_reporting if reports[r].get("family")}),
        "clusters": clusters_out,
        "disagreements": _disagreements(clusters_out, patch),
        "verdict": None,
        "method_caveat": method_caveat(patch.get("method_caveat"), manifest),
        "counts": _counts(clusters_out),
    }
    document["verdict"] = compute_verdict(clusters_out, reports)
    return document, []


def method_caveat(supplied, manifest):
    """The patch's caveat, plus everything about the run the judgment supplier could not know.

    Five appendices, each drawn from the manifest and none of them from the patch:

    - **Seat substitutions.** A re-seated seat changes what the agreement counts mean — the panel
      that ran is not quite the panel that was composed. An unsatisfiable constraint and a runtime
      re-seat are both decided by `run_panel.py` after the patch's author has read the reports.
    - **Missing seats.** A seat the panel composed and the run did not hear back from, named with
      the reason the manifest recorded. The judgment supplier sees the reports that landed; it
      cannot see which of the composed seats are absent, or why.
    - **The family target.** `min_families` is a target, not a precondition: a run that misses it
      proceeds and says so here. **One reporting family is `lens-diverse only`** — several lenses,
      one mind, and no cross-family corroboration available at any tier, which is the single fact
      that most changes how the tier table should be read. A run that met its target but lost a seat
      still gets both counts, because a met target over a short panel is worth stating.
    - **Projected against billed cost.** Run 3 projected $2.30 and was billed $2.75 with nothing
      comparing the two. Nothing meters spend as seats return — that gap is the spec's — so the
      after-the-fact comparison is the only place it is visible at all. The figure is the **billed**
      cost, and a run carrying unbilled upstream inference says so in one clause.
    - **The inferred panel.** A run with no references never infers a template whose
      `requires_references` is true, so the panel that ran may not be the panel the artifact's own
      name suggested. That substitution is recorded the same way a seat's is.

    Nothing else about the supplied text is changed.
    """
    text = (supplied or "").strip()
    appendices = []

    lines = []
    for seat in manifest.get("seats") or []:
        substitution = seat.get("substitution")
        if not isinstance(substitution, dict):
            continue
        lines.append("`{0}` asked for {1} and ran on {2} ({3}): {4}".format(
            seat.get("reviewer_id"), substitution.get("requested"), substitution.get("resolved"),
            substitution.get("kind"), (substitution.get("reason") or "").rstrip(".")))
    if lines:
        appendices.append("Seat substitutions recorded in the manifest — " + "; ".join(lines) + ".")

    absent = _missing_seats_caveat(manifest)
    if absent:
        appendices.append(absent)

    families = _min_families_caveat(manifest, always=bool(absent))
    if families:
        appendices.append(families)

    spend = _cost_caveat(manifest, always=bool(absent))
    if spend:
        appendices.append(spend)

    inference = manifest.get("panel_inference")
    if isinstance(inference, dict) and inference.get("reason"):
        appendices.append("Panel substitution recorded in the manifest — the artifact's name suggested "
                          "`{0}` and the run used `{1}`: {2}.".format(
                              inference.get("heuristic"), inference.get("resolved"),
                              (inference.get("reason") or "").rstrip(".")))

    if not appendices:
        return text
    appended = "\n\n".join(appendices)
    return (text + "\n\n" + appended) if text else appended


def _missing_seats_caveat(manifest):
    """The composed seats that did not report, named with the reason and the stage.

    Only an **explicit** failure counts. A seat record carrying no status at all is a manifest this
    function does not understand, and asserting a missing seat over it would put a fact in the
    reconciliation that the run never established. A harness seat still `pending` is not missing
    either: the host has simply not rendered it yet.
    """
    lines = []
    for seat in manifest.get("seats") or []:
        if seat.get("status") not in ("failed", "timeout"):
            continue
        reason = (seat.get("failure_reason")
                  or (seat.get("error") or "").strip().splitlines()[-1:] or ["no reason recorded"])
        reason = reason if isinstance(reason, str) else reason[0]
        lines.append("`{0}` ({1}, {2}) — {3}".format(
            seat.get("reviewer_id"), seat.get("lens") or "lens unrecorded",
            seat.get("model") or seat.get("family") or "model unrecorded", reason.rstrip(".")))
    if not lines:
        return None
    return ("Seats that did not report — {0} of {1} composed seat(s) are absent from every cluster "
            "below: {2}. Their lenses were not applied to this artifact at all.".format(
                len(lines), len(manifest.get("seats") or []), "; ".join(lines)))


def _min_families_caveat(manifest, always=False):
    """One sentence when the run missed its family target, or when `always` asks for it anyway.

    Both counts are reported, because they answer different questions: `seated` says what the panel
    was composed to reach and `reporting` says what it actually reached, and a panel that seated four
    families and heard back from one is a different document from one that only ever had one.

    `always` is set when a seat is missing. Run 3 seated four families, lost one and still met its
    target of two, so the counts said nothing — and a reader of the tier table had no way to know
    that "three families agree" was three out of four rather than three out of three.
    """
    block = manifest.get("min_families")
    if not isinstance(block, dict):
        return None
    target = block.get("target")
    seated = block.get("seated")
    reporting = block.get("reporting")
    if not isinstance(target, int):
        return None
    counts = [value for value in (seated, reporting) if isinstance(value, int)]
    if not counts:
        return None
    met = min(counts) >= target
    if met and not always:
        return None
    reading = ("this run is **lens-diverse only** — several lenses, one family, so no cluster on it "
               "can carry cross-family corroboration"
               if reporting == 1 else
               "the target is met, but over a short panel" if met else
               "cross-family corroboration is thinner than the panel asked for")
    return ("Family diversity {0} the panel's target — `min_families` target {1}, {2} seated, {3} "
            "reporting ({4}); {5}.".format(
                "against" if met else "below",
                target,
                seated if seated is not None else "unrecorded",
                reporting if reporting is not None else "unrecorded",
                ", ".join(block.get("families_reporting") or block.get("families_seated") or []) or "no family recorded",
                reading))


def _cost_caveat(manifest, always=False):
    """Projected against actual, when the manifest carries both. Silent when it carries either alone.

    The projection is a dispatch gate and nothing meters spend as seats return, so this comparison
    is made after the fact or not at all. It is a **caveat** rather than an accounting line because
    an overrun says something about the reading: a seat that cost three times its projection usually
    did so by retrying, and a retried seat is one whose report came from a different attempt than
    the one the panel was composed around.

    **The figure is what the account was billed**, `cost_usd_total`, which is the number that
    reconciles against a credit ledger — the first unattended run's $2.752117 against a `/credits`
    delta of $2.752118. Inference the provider ran and did not charge for is real and is reported in
    its own clause, and only when there is some: it explains why a seat took an hour without
    pretending the run cost more than it did.

    A run that came in **under** its projection says nothing, unless `always` — set when a seat is
    missing, where the money that did not get spent is part of what the missing seat cost. A caveat
    section that appends a line to every clean run is a caveat section readers stop reading.
    """
    projection = manifest.get("projection")
    if not isinstance(projection, dict):
        return None
    projected = projection.get("projection_usd")
    actual = manifest.get("cost_usd_total")
    if projected is None or actual is None:
        return None
    try:
        projected, actual = float(projected), float(actual)
    except (TypeError, ValueError):
        return None
    if actual <= projected and not always:
        return None
    ratio = (actual / projected) if projected else None
    reading = ("under the pre-flight" if actual <= projected else
               "over the pre-flight by {0:.0f}%".format((ratio - 1) * 100) if ratio else
               "over the pre-flight")
    budget = projection.get("budget_usd")
    unbilled = _upstream_unbilled_total(manifest)
    return ("Cost — the pre-flight projected ${0:.2f} and the run was billed ${1:.2f}, {2}{3}{4}. "
            "The projection is a dispatch gate: nothing meters spend as seats return, so an overrun "
            "runs to completion.".format(
                projected, actual, reading,
                " against a budget of ${0:.2f}".format(float(budget)) if budget is not None else "",
                "; a further ${0:.2f} of upstream inference was run and not billed".format(unbilled)
                if unbilled else ""))


def _upstream_unbilled_total(manifest):
    """Inference the provider ran and did not charge for, over every seat and the judge.

    Summed here rather than kept as a manifest field, because it belongs to the same read as the
    billed total and nothing else asks for it. Zero and absent are the same answer.
    """
    total = 0.0
    records = list(manifest.get("seats") or [])
    judge = manifest.get("judge")
    if isinstance(judge, dict):
        records.append(judge)
    for record in records:
        try:
            total += float(record.get("upstream_unbilled_usd") or 0.0)
        except (TypeError, ValueError):
            continue
    return total or None


def _wrap(cluster, records):
    if cluster is None:
        return None
    for record in records:
        if record["cluster"] is cluster:
            return record
    return None


def _needs_a_human_ruling(record):
    """True when the cluster is all `judgment-call` **and** the tags do not say it is only a gap.

    Two conditions, and the second is what `change_kind` cannot express. A `judgment-call` is a
    design fork — two defensible answers the references do not settle — and it carries the `fork`
    tag to say so. A cluster whose judgment calls are every one of them tagged `gap` is a determinate
    fix somebody filed as a decision; it belongs on the fix list, and forcing it to a human is how a
    reviewer's miscalibration becomes a question the owner has to answer.

    Untagged is treated as needing the ruling. A report written before the tag rule existed — the
    frozen replay fixture's 44 untagged judgment calls among them — says nothing either way, and the
    safe reading of silence is the one that asks rather than the one that applies.
    """
    findings = [finding for member in record["members"] for finding in member["_all"]]
    if not findings or not all(f.raw.get("change_kind") == "judgment-call" for f in findings):
        return False
    return not all(report_lib.GAP_TAG in (f.raw.get("tags") or []) for f in findings)


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


def _render_cluster(record, final_id, author):
    cluster = record["cluster"]
    members = record["members"]
    primary = sorted(members, key=lambda m: (SEVERITY_ORDER.get(m["severity"], 9), m["reviewer_id"]))[0]
    tags = []
    for member in members:
        for finding in member["_all"]:
            for tag in finding.raw.get("tags") or []:
                if tag not in tags:
                    tags.append(tag)

    canonical_edit, edit_conflict = _canonical_edit(record, author)

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


def _canonical_edit(record, author):
    edits = []
    for member in record["members"]:
        for finding in member["_all"]:
            edit = finding.raw.get("literal_edit")
            if isinstance(edit, dict) and edit.get("old_text"):
                edits.append((member["reviewer_id"], finding.raw.get("id"), edit))
    if not edits:
        return None, False
    conflict = _members_disagree_on_replacement(edits)
    entry = record.get("canonical_edit_entry")
    if entry is None or not entry.get("accepted"):
        return None, conflict
    source = entry.get("source") or {}
    for reviewer_id, finding_id, edit in edits:
        if reviewer_id == source.get("reviewer_id") and finding_id == source.get("finding_id"):
            return {
                "old_text": edit.get("old_text"),
                "new_text": edit.get("new_text"),
                "source": {"reviewer_id": reviewer_id, "finding_id": finding_id},
                "accepted_by": entry.get("accepted_by") or author,
                "reason": entry.get("reason") or "",
            }, False
    return None, conflict


def _members_disagree_on_replacement(edits):
    """`edit_conflict` is about **members**: two reviewers proposing different replacements.

    One reviewer's own collapsed duplicates offering two wordings is that reviewer restating itself,
    not a conflict between seats, and reporting it as one made the field mean two different things.
    """
    by_reviewer = {}
    for reviewer_id, _finding_id, edit in edits:
        by_reviewer.setdefault(reviewer_id, set()).add(edit.get("new_text"))
    order = sorted(by_reviewer)
    for index, left in enumerate(order):
        for right in order[index + 1:]:
            if any(a != b for a in by_reviewer[left] for b in by_reviewer[right]):
                return True
    return False


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
        "anchor_drops": context["anchor_drops"],
        "note": (
            "Write judgment.json in this directory against schemas/judgment-patch.schema.json, then re-run reconcile.py "
            "with --judgment. Provisional tiers are computed before your claim joins and splits and are recomputed after "
            "them; label every cluster that is a singleton or corroborated-same-family AFTER the merge. Any member listed "
            "in `anchor_drops` quoted text that is not in the pinned artifact and is already out of the clusters below."
        ),
        "clusters": entries,
    }


def _required_fields(tier):
    required = ["dispositions"]
    if tier in LABELLED_TIERS:
        required.append("singleton_labels")
    required.append("severities (only when the members disagree)")
    return required
