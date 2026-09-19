"""Run directories: claiming one, materializing its inputs, and the manifest's compare-and-set.

Three mechanisms, all of which exist because two processes can touch one run:

- **Claim.** The run directory is created with an atomic exclusive `mkdir`. On collision the run id's
  trailing sequence number increments and the claim retries, so two concurrent runs on one artifact
  can never write into the same directory.
- **Materialize.** The artifact and every reference are copied into `<run-dir>/inputs/`, made
  read-only, and hashed as written. **The revision is the SHA-256 of the bytes in `inputs/`** — not
  of the working-tree file, which may be edited mid-run, and not the commit id, because this skill's
  primary case is an uncommitted draft. The commit id is recorded *beside* the hash when the file's
  working tree is clean, as the human-legible pointer.
- **Compare-and-set.** A seat's manifest record moves `pending -> dispatching` under a lock file
  before its first paid call. Exclusive `mkdir` guards the create path only; resume is the path this
  skill actually uses, and without the CAS two resumes both see a seat as absent and both pay for it.

Every structured write here is atomic: temp file plus `os.replace()`.
"""

import hashlib
import json
import os
import shutil
import subprocess
import time

READ_ONLY = 0o444
LOCK_TIMEOUT_S = 30
LOCK_POLL_S = 0.05
LOCK_STALE_S = 300


class RunError(Exception):
    """A condition that stops the run before anything is written."""


# --- claiming ------------------------------------------------------------------------------------

def claim_run_dir(path):
    """Create `path` with an atomic exclusive mkdir, incrementing its sequence number on collision.

    `<run-id>` is `YYYY-MM-DD-<n>`, so a collision bumps `<n>`. A directory whose name carries no
    trailing `-<n>` gets one appended: `.../run` collides into `.../run-2`.
    """
    parent = os.path.dirname(os.path.abspath(path))
    base = os.path.basename(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)

    stem, sequence = _split_sequence(base)
    attempt = os.path.join(parent, base)
    guard = 0
    while True:
        try:
            os.mkdir(attempt)
            return attempt
        except FileExistsError:
            guard += 1
            if guard > 1000:
                raise RunError("could not claim a run directory beside {0} after 1000 tries".format(path))
            sequence += 1
            attempt = os.path.join(parent, "{0}-{1}".format(stem, sequence))


def _split_sequence(name):
    stem, _dash, tail = name.rpartition("-")
    if stem and tail.isdigit():
        return stem, int(tail)
    return name, 1


# --- materializing -------------------------------------------------------------------------------

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def git_commit_if_clean(path):
    """The commit id, but only when this file's working tree is clean for it.

    A dirty file gets no commit id: a commit id beside bytes that have changed since is worse than
    no pointer at all, because it reads as a pin.
    """
    directory = os.path.dirname(os.path.abspath(path))
    try:
        status = subprocess.run(["git", "-C", directory, "status", "--porcelain", "--", os.path.abspath(path)],
                                capture_output=True, text=True, timeout=30)
        if status.returncode != 0 or status.stdout.strip():
            return None
        head = subprocess.run(["git", "-C", directory, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
        if head.returncode == 0 and head.stdout.strip():
            return head.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def materialize(run_dir, paths, label_fn=None):
    """Copy every path into `<run-dir>/inputs/`, read-only, and hash the bytes written.

    Returns one record per path: `{path, materialized, revision, commit, bytes}`. `path` is the label
    the run records — repo-relative when `label_fn` supplies it — and `materialized` is the copy both
    legs read. Basename collisions are disambiguated with a numeric suffix, so two references called
    `restart.md` do not overwrite each other.
    """
    inputs_dir = os.path.join(run_dir, "inputs")
    if not os.path.isdir(inputs_dir):
        os.makedirs(inputs_dir)

    used = set()
    records = []
    for source in paths:
        if not os.path.isfile(source):
            raise RunError("not a file: {0}".format(source))
        name = _unique_name(os.path.basename(source), used)
        used.add(name)
        destination = os.path.join(inputs_dir, name)
        if os.path.exists(destination):
            os.chmod(destination, 0o644)
        shutil.copyfile(source, destination)
        os.chmod(destination, READ_ONLY)
        records.append({
            "path": label_fn(source) if label_fn else source,
            "materialized": os.path.join("inputs", name),
            "revision": sha256_file(destination),
            "commit": git_commit_if_clean(source),
            "bytes": os.path.getsize(destination),
        })
    return records


def preview(paths, label_fn=None):
    """What `materialize` *would* write, without writing it — the revisions of the source bytes.

    Resume needs the fingerprint of the current working tree before it is allowed to touch anything:
    re-materializing first would overwrite the pinned copies of the run it is about to refuse.
    """
    records = []
    for source in paths:
        if not os.path.isfile(source):
            raise RunError("not a file: {0}".format(source))
        records.append({
            "path": label_fn(source) if label_fn else source,
            "revision": sha256_file(source),
            "bytes": os.path.getsize(source),
        })
    return records


def _unique_name(name, used):
    if name not in used:
        return name
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    index = 2
    while True:
        candidate = "{0}-{1}{2}{3}".format(stem, index, "." if extension else "", extension)
        if candidate not in used:
            return candidate
        index += 1


def input_fingerprint(artifact_record, reference_records, panel, tier):
    """The set of hashes plus the panel and the tier — what resume refuses to mix.

    Reference order does not change the fingerprint: the same documents supplied in another order are
    the same inputs. The panel and the tier are in it because a resume that changed either would be
    two different reviews sharing one directory.
    """
    payload = {
        "artifact": (artifact_record or {}).get("revision"),
        "references": sorted(record.get("revision") for record in (reference_records or [])),
        "panel": panel,
        "tier": tier,
    }
    return sha256_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))


# --- the manifest --------------------------------------------------------------------------------

def manifest_path(run_dir):
    return os.path.join(run_dir, "manifest.json")


def write_json_atomic(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    tmp = path + ".tmp-{0}".format(os.getpid())
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_manifest(run_dir):
    path = manifest_path(run_dir)
    if not os.path.isfile(path):
        raise RunError("no manifest.json in {0}".format(run_dir))
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_manifest(run_dir, manifest):
    write_json_atomic(manifest_path(run_dir), manifest)


class _Lock(object):
    """A lock file around one read-modify-write of the manifest.

    `os.replace()` makes each write atomic; it does not make read-modify-write atomic, which is what
    a compare-and-set needs. The lock is an exclusive `mkdir`, held for the few milliseconds of one
    swap, and a lock older than `LOCK_STALE_S` is broken — a crashed run must not wedge a resume.
    """

    def __init__(self, run_dir):
        self.path = os.path.join(run_dir, "manifest.lock")

    def __enter__(self):
        deadline = time.time() + LOCK_TIMEOUT_S
        while True:
            try:
                os.mkdir(self.path)
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    age = 0
                if age > LOCK_STALE_S:
                    try:
                        os.rmdir(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() > deadline:
                    raise RunError("could not take the manifest lock at {0} within {1}s".format(self.path, LOCK_TIMEOUT_S))
                time.sleep(LOCK_POLL_S)

    def __exit__(self, *_exc):
        try:
            os.rmdir(self.path)
        except OSError:
            pass
        return False


def update_seat(run_dir, reviewer_id, mutate, expect_status=None):
    """Read the manifest, apply `mutate` to one seat record, write it back — under the lock.

    `expect_status` makes it a compare-and-set: the swap happens only when the seat is in one of the
    statuses given. Returns (applied, status_seen). A seat another process already moved to
    `dispatching` returns (False, "dispatching") and is left strictly alone.
    """
    with _Lock(run_dir):
        manifest = read_manifest(run_dir)
        for seat in manifest.get("seats") or []:
            if seat.get("reviewer_id") != reviewer_id:
                continue
            seen = seat.get("status")
            if expect_status is not None and seen not in expect_status:
                return False, seen
            mutate(seat)
            write_manifest(run_dir, manifest)
            return True, seen
    raise RunError("no seat {0!r} in the manifest at {1}".format(reviewer_id, run_dir))


def claim_seat(run_dir, reviewer_id, claimable=("pending", "failed")):
    """Move one seat `pending`/`failed` -> `dispatching` before its first paid call."""
    def mutate(seat):
        seat["status"] = "dispatching"
        seat["claimed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    return update_seat(run_dir, reviewer_id, mutate, expect_status=claimable)


def report_is_valid(run_dir, reviewer_id, validate_fn):
    """Whether this seat already has a report on disk that validates. Resume's whole question."""
    path = os.path.join(run_dir, reviewer_id + ".json")
    if not os.path.isfile(path):
        return False, "no report file"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (ValueError, OSError) as exc:
        return False, "report unreadable: {0}".format(exc)
    errors = validate_fn(data)
    if errors:
        return False, "report invalid: {0}".format("; ".join(errors[:3]))
    return True, "validated"
