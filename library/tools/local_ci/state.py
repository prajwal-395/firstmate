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
MAIN_BASELINE_FILENAME = "main_baseline.json"

# How many main SHAs keep a cached baseline verdict: the daemon judges
# one SHA at a time, so this is headroom, not a working set. Bounded so
# the ledger cannot grow with every main advance.
MAIN_BASELINE_KEEP = 20

# What a CERTIFIED baseline carries beyond the judged verdict ({ok,
# reason, log, recorded_at}): the gate's verdict line, the test
# inventory the gate ran over, the per-file verification keys (CI-01)
# later evidence reuses against, the environment fingerprints those
# keys were computed under, and which executor certified it. A green
# record WITHOUT these is judged, never certified - the pre-CI-07
# shape judge_main still writes for verdicts with no checkout in hand.
CERTIFIED_FIELDS = ("verdict", "verdict_line", "inventory", "keys",
                    "env", "interpreter", "native", "config",
                    "source")

# Queue entry statuses. queued -> testing -> green -> merged is the
# happy path; testing can also return to queued when a runner is
# interrupted, or become kicked when the stack proves a PR is red.
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


def update_check(head: str, fields: dict, state: str = "") -> dict | None:
    """Update delivery metadata without changing the check's verdict time."""
    checks = read_checks(state)
    record = checks.get(head)
    if not isinstance(record, dict):
        return None
    record.update(fields)
    checks[head] = record
    _write(CHECKS_FILENAME, checks, state)
    return dict(record)


def read_queue(state: str = "") -> list:
    """The merge queue, oldest first."""
    data = _read(QUEUE_FILENAME, state)
    entries = data.get("entries")
    return list(entries) if isinstance(entries, list) else []


def write_queue(entries: list, state: str = "") -> list:
    """Replace the queue wholesale. Returns what was written."""
    _write(QUEUE_FILENAME, {"entries": list(entries)}, state)
    return list(entries)


def recover_interrupted_queue(state: str = "") -> int:
    """Requeue members left testing by a daemon or process interruption.

    The caller must first establish that no Tier-2 gate is still active.
    """
    entries = read_queue(state)
    recovered = 0
    for entry in entries:
        if entry.get("status") == ENTRY_TESTING:
            entry["status"] = ENTRY_QUEUED
            entry["reason"] = "previous Tier-2 run interrupted - re-testing"
            recovered += 1
    if recovered:
        write_queue(entries, state)
    return recovered


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


def remove_active_queue_entries(pr: int, state: str = "") -> int:
    """Drop queued membership when Tier-1 no longer admits this PR."""
    entries = read_queue(state)
    retained = [entry for entry in entries
                if not (entry.get("pr") == pr and entry.get("status") in
                        (ENTRY_QUEUED, ENTRY_TESTING, ENTRY_GREEN))]
    removed = len(entries) - len(retained)
    if removed:
        write_queue(retained, state)
    return removed


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


def read_main_baseline(state: str = "") -> dict:
    """Main SHA -> baseline record ({ok, reason, log, recorded_at}).

    The Tier-2 main gate caches its verdict per main SHA so a red
    batch re-tests the same main without re-running the full gate.
    """
    data = _read(MAIN_BASELINE_FILENAME, state)
    return data if isinstance(data, dict) else {}


def main_baseline_green(sha: str, state: str = "") -> dict | None:
    """The cached GREEN baseline for this main SHA, if one exists.

    Only green verdicts are reusable evidence: a red main is re-judged
    every batch (the same SHA re-run is cheap next to a wrong kick),
    and an unjudgeable main (narrowed) never caches at all.
    """
    if not sha:
        return None
    record = read_main_baseline(state).get(sha)
    if isinstance(record, dict) and record.get("ok") is True:
        return dict(record)
    return None


def record_main_baseline(sha: str, ok: bool, reason: str = "",
                         state: str = "", log: str = "") -> dict:
    """Store one main-baseline verdict, keyed by main SHA.

    A later verdict never strips a certification: when the stored
    record for this SHA already carries a certified baseline (CI-07),
    its inventory, keys, fingerprints and verdict line survive - the
    same SHA is the same tree, so the baseline still describes it.
    """
    data = read_main_baseline(state)
    previous = data.get(sha)
    record = {"ok": bool(ok), "reason": reason or "", "log": log or "",
              "recorded_at": time.time()}
    if is_certified(previous):
        for field in CERTIFIED_FIELDS:
            record[field] = previous[field]
    data[sha] = record
    while len(data) > MAIN_BASELINE_KEEP:
        oldest = min(data, key=lambda key: float(
            data[key].get("recorded_at", 0)
            if isinstance(data[key], dict) else 0))
        del data[oldest]
    _write(MAIN_BASELINE_FILENAME, data, state)
    return dict(record)


def is_certified(record: dict | None) -> bool:
    """Whether a baseline record is a full certified baseline.

    Certified means green AND carrying the inventory + per-file keys +
    fingerprints a later incremental verdict (CI-08) can reuse against.
    A bare green verdict (judged, e.g. from a scripted world with no
    checkout) is evidence the SHA passed, never a baseline.
    """
    return (isinstance(record, dict) and record.get("ok") is True
            and isinstance(record.get("inventory"), list)
            and bool(record.get("inventory"))
            and isinstance(record.get("keys"), dict)
            and bool(record.get("keys")))


def certified_baseline(sha: str, state: str = "") -> dict | None:
    """The certified baseline for this main SHA, if one was recorded.

    Returns None for unknown SHAs AND for judged-but-uncertified
    verdicts: callers deciding what evidence to reuse must not read a
    bare green verdict as a baseline.
    """
    if not sha:
        return None
    record = read_main_baseline(state).get(sha)
    if is_certified(record):
        return dict(record)
    return None


def certify_baseline(sha: str, *, verdict: str = "",
                     verdict_line: str = "",
                     inventory: list | None = None,
                     keys: dict | None = None, env: str = "",
                     interpreter: str = "", native: str = "",
                     config_text: str = "", reason: str = "",
                     log: str = "", source: str = "",
                     state: str = "") -> dict:
    """Certify this main SHA's full-suite baseline, or refuse with the reason.

    Certifies only on a full `FULL-SUITE GATE: PASS` - the same
    predicate merges use (`runner.certifiable_verdict`): a NARROWED
    PASS names an incomplete machine, never a passing tree, so it is
    refused and stores nothing, as is a FAIL or a missing verdict.
    An empty inventory or key map is also refused: a baseline with no
    test evidence certifies nothing. Never raises on a refusal - the
    caller reports the reason and the SHA stays uncertified.

    Returns {"ok": True, "record": ...} on success, {"ok": False,
    "reason": ...} on refusal. Shares the main-baseline ledger and its
    bound, so `main_baseline_green` keeps answering for certified
    SHAs and `record_main_baseline` never strips a certification.
    """
    from library.tools.local_ci import runner
    if not sha:
        return {"ok": False,
                "reason": "certify refused: no main sha to certify"}
    if not runner.certifiable_verdict(verdict):
        return {"ok": False,
                "reason": (f"certify refused: gate verdict "
                           f"'{verdict or 'missing'}' is not a full PASS - "
                           f"a baseline certifies only on "
                           f"FULL-SUITE GATE: PASS")}
    inventory = list(inventory or [])
    keys = dict(keys or {})
    if not inventory or not keys:
        return {"ok": False,
                "reason": ("certify refused: no test evidence - a "
                           "baseline without inventory and keys "
                           "certifies nothing")}
    data = read_main_baseline(state)
    record = {"ok": True, "reason": reason or "", "log": log or "",
              "recorded_at": time.time(), "verdict": verdict,
              "verdict_line": verdict_line or f"FULL-SUITE GATE: {verdict}",
              "inventory": inventory, "keys": keys, "env": env or "",
              "interpreter": interpreter or "", "native": native or "",
              "config": config_text or "", "source": source or ""}
    data[sha] = record
    while len(data) > MAIN_BASELINE_KEEP:
        oldest = min(data, key=lambda key: float(
            data[key].get("recorded_at", 0)
            if isinstance(data[key], dict) else 0))
        del data[oldest]
    _write(MAIN_BASELINE_FILENAME, data, state)
    return {"ok": True, "record": dict(record)}
