"""The coarse answer, kept on disk so the escalation does not re-rank.

The problem this removes
------------------------
The frame lane answers at two resolutions (see
:mod:`library.tools.fidelity`): a COARSE answer that ranks the
sampled frames and says where to look, and a DETAILED answer that puts
the candidates to the local vision model and says which of them
establishes the claim.  The detailed answer is computed FROM the coarse
answer's candidates, so the escalation is not a second payment for the
ranking - but only while the coarse answer is still in the caller's
hands.

In one call that is true.  Across two calls it was not: the coarse
answer was printed and gone, so a caller that asked for the cheap
answer first and the verified answer second paid for the ranking TWICE,
and the second call's cost had nothing to do with the answer it
wanted.  The cost of the question was decided by how the caller split
it into commands.

This module is the store that fixes it.  A coarse answer is written
beside the index it was made from - the project's own scratch area,
next to the index, where derived data belongs - and a later call finds
it again by its IDENTITY: the query, the policy the search ran under,
and the identity of the index it ranked.

Three rules it keeps
--------------------

**A stored answer is only reused when it is the SAME answer.**  The
identity of a coarse answer is the query plus the policy that produced
it, and the policy is read back from the store, so an escalation
cannot quietly widen or narrow the pool the coarse answer was made
with.

**A stored answer that the index has moved on from is not reused.**
Every store entry carries the index's fingerprint, and a fingerprint
that does not match the index on disk is a REFUSAL, not a warning: the
ranking was made over frames that are no longer the ones on disk, so
reusing it would answer a different question.

**The store is derived data.**  It lives in the project's scratch area
beside the index, it is safe to delete at any moment, and deleting it
costs one ranking - never an answer that cannot be had again.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

STORE_DIRNAME = "coarse_reports"
"""The directory, inside the index directory, the coarse answers live in."""

STORE_VERSION = 1
"""The shape of a stored answer.  A file of another version is refused."""

IDENTITY_KEYS = (
    "candidate_pool_size",
    "per_source_pool_size",
    "merge_gap_seconds",
    "top_interval_limit",
    "query_floor",
    "window_floor",
    "max_range_seconds",
    "max_range_frames",
    "windows_per_source",
    "include_actions",
)
"""The policy keys that decide what a search WOULD produce.

The two COUNT keys of the lane's own policy are not here: they are
what the search MEASURED, and the index's fingerprint already covers
them.  A policy that decides the answer is part of the answer's
identity; a count that describes it is not.
"""


def store_dir(index_dir: str | Path) -> Path:
    """Where the coarse answers of one index live."""
    return Path(index_dir) / STORE_DIRNAME


def identity(query: str, policy: dict[str, Any]) -> str:
    """The name one coarse answer is stored under.

    The same query under a different policy is a DIFFERENT answer, so
    the policy is part of the name: a caller that asked for three
    windows and then asks for five must not be served the three.
    """
    payload = json.dumps(
        {"query": str(query), "policy": stable_policy(policy)},
        sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def stable_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    """The part of a policy that decides what the search produces."""
    policy = policy or {}
    return {key: policy.get(key) for key in IDENTITY_KEYS}


def index_fingerprint(index_dir: str | Path) -> str:
    """The identity of the index a ranking was made over.

    The fingerprint covers the index's own files, so an index that was
    rebuilt - or a different index altogether - is a different index,
    and a coarse answer made over the old one is refused rather than
    reused.
    """
    digest = hashlib.sha256()
    directory = Path(index_dir)
    for name in ("frame_index.json", "frame_embeddings.npy"):
        path = directory / name
        digest.update(name.encode("utf-8"))
        if not path.exists():
            digest.update(b"absent")
            continue
        digest.update(str(path.stat().st_size).encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode(
            "utf-8"))
    return digest.hexdigest()[:16]


def save(index_dir: str | Path, report: dict[str, Any], *,
         policy: dict[str, Any] | None = None,
         fingerprint: str | None = None) -> dict[str, Any]:
    """Keep one coarse answer, and say where it went.

    A store that cannot be written is NOT a failed answer: the coarse
    answer is still the answer, and the caller is told the escalation
    will have to pay for the ranking again.
    """
    directory = store_dir(index_dir)
    query = str(report.get("query") or "")
    policy = policy if policy is not None else (report.get("search_policy") or {})
    key = identity(query, policy)
    path = directory / f"{key}.json"
    payload = {
        "version": STORE_VERSION,
        "query": query,
        "policy": stable_policy(policy),
        "fingerprint": (fingerprint if fingerprint is not None
                        else index_fingerprint(index_dir)),
        "report_id": report.get("coarse_report_id"),
        "stored_at": time.time(),
        "report": report,
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, payload)
    except OSError as exc:
        return {"status": "refused", "path": str(path),
                "reason": f"the coarse answer could not be stored: {exc}",
                "report_id": payload["report_id"]}
    return {"status": "stored", "path": str(path), "reason": None,
            "report_id": payload["report_id"]}


def load(index_dir: str | Path, *, query: str,
         policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """The stored coarse answer for this query and policy, or why not.

    The result says which of three things happened: the answer was
    FOUND, it was ABSENT (no coarse answer was stored for this query
    under this policy), or it is STALE (the index has moved on, or the
    file is of another shape).  A stale answer is never reused: the
    escalation would answer a question about frames that are no longer
    there.
    """
    directory = store_dir(index_dir)
    key = identity(query, policy or {})
    path = directory / f"{key}.json"
    if not path.exists():
        return {"status": "absent", "path": str(path), "report": None,
                "reason": (f"no coarse answer is stored for this query at "
                           f"this policy ({path})"),
                "report_id": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "stale", "path": str(path), "report": None,
                "reason": f"the stored coarse answer could not be read: {exc}",
                "report_id": None}
    if not isinstance(payload, dict) or payload.get("version") != STORE_VERSION:
        return {"status": "stale", "path": str(path), "report": None,
                "reason": ("the stored coarse answer is of another shape "
                           f"(version {payload.get('version') if isinstance(payload, dict) else 'unreadable'})"),
                "report_id": None}
    current = index_fingerprint(index_dir)
    if payload.get("fingerprint") != current:
        return {"status": "stale", "path": str(path), "report": None,
                "reason": ("the index has changed since the coarse answer "
                           "was made, so the ranking is not this index's "
                           "ranking"),
                "report_id": payload.get("report_id")}
    report = payload.get("report")
    if not isinstance(report, dict):
        return {"status": "stale", "path": str(path), "report": None,
                "reason": "the stored coarse answer carries no report",
                "report_id": payload.get("report_id")}
    return {"status": "found", "path": str(path), "report": report,
            "reason": None, "report_id": payload.get("report_id")}


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write the file whole, or not at all.

    A half-written coarse answer is worse than none: a reader would
    find a report that is not an answer.
    """
    handle, temporary = tempfile.mkstemp(dir=str(path.parent),
                                          prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, default=str)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
