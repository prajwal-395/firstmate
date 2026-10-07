"""The daemon's ledger: check results and the merge queue, as JSON files.

Two files under the state dir (config.state_dir), never in a repo:

- checks.json: head SHA -> check record (verdict, counts, log path).
  Keyed by SHA, not PR number: a new push supersedes by SHA, and a
  re-run of the same SHA is a cache hit, not a second verdict.
- queue.json: the merge queue, oldest first: entries with status
  queued | testing | green | kicked | merged, the head SHA each
  verdict belongs to, and the reason a kick names.

Writes are atomic (tmp file + rename) so `ren ci status` never reads a
torn ledger while the daemon writes it.
"""

from __future__ import annotations

import json
import os
import time

from library.tools.local_ci import config

CHECKS_FILENAME = "checks.json"
QUEUE_FILENAME = "queue.json"
SEEN_FILENAME = "seen.json"

# Queue entry statuses. queued -> testing -> green -> merged is the
# happy path; testing -> kicked (with reason) is the only other exit.
# A kick never blocks the entries behind it.
ENTRY_QUEUED = "queued"
ENTRY_TESTING = "testing"
ENTRY_GREEN = "green"
ENTRY_KICKED = "kicked"
ENTRY_MERGED = "merged"


def _path(filename: str, state: str = "") -> str:
    root = state if state else config.state_dir()
    return os.path.join(root, filename)


def _read(filename: str, state: str = "") -> dict:
    path = _path(filename, state)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(filename: str, data: dict, state: str = "") -> None:
    root = state if state else config.state_dir()
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, filename)
    tmp = path + f".tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def read_checks(state: str = "") -> dict:
    """Head SHA -> check record."""
    data = _read(CHECKS_FILENAME, state)
    return data if isinstance(data, dict) else {}


def record_check(entry: dict, state: str = "") -> dict:
    """Store one check record, keyed by its head SHA. Returns the record."""
    checks = read_checks(state)
    record = dict(entry)
    record["recorded_at"] = time.time()
    checks[record["head"]] = record
    _write(CHECKS_FILENAME, checks, state)
    return record


def read_queue(state: str = "") -> list:
    """The merge queue, oldest first."""
    data = _read(QUEUE_FILENAME, state)
    entries = data.get("entries")
    return list(entries) if isinstance(entries, list) else []


def write_queue(entries: list, state: str = "") -> list:
    """Replace the queue wholesale. Returns what was written."""
    _write(QUEUE_FILENAME, {"entries": list(entries)}, state)
    return list(entries)


def enqueue(pr: int, head: str, state: str = "") -> dict:
    """Queue a green PR head, or return its existing entry.

    A re-queue of the same PR+head is a no-op (the label webhook may
    fire twice); a NEW head for a queued PR supersedes the old entry,
    because the old verdict belongs to a tree that no longer exists.
    """
    entries = read_queue(state)
    for entry in entries:
        if entry.get("pr") == pr and entry.get("head") == head:
            return entry
    entries = [e for e in entries
               if not (e.get("pr") == pr and e.get("status") in
                       (ENTRY_QUEUED, ENTRY_TESTING, ENTRY_GREEN))]
    entry = {"pr": pr, "head": head, "status": ENTRY_QUEUED,
             "reason": "", "enqueued_at": time.time()}
    entries.append(entry)
    write_queue(entries, state)
    return entry


def note_seen(pr: int, head: str, state: str = "") -> float:
    """First-seen timestamp of a PR head. First write wins: latency is
    measured from when the daemon first SAW the head, the closest local
    proxy for push time (GitHub offers no push timestamp to a poller).
    Returns the (possibly pre-existing) timestamp."""
    data = _read(SEEN_FILENAME, state)
    key = f"{pr}:{head}"
    if key not in data:
        data[key] = time.time()
        _write(SEEN_FILENAME, data, state)
    return float(data[key])


def read_seen(state: str = "") -> dict:
    """Every first-seen key -> timestamp."""
    data = _read(SEEN_FILENAME, state)
    return data if isinstance(data, dict) else {}
