"""Timeline generations: the shadow of every Resolve timeline Ren has read.

The single-Resolve plan (captain, 2026-10-02, items 3 and 4) makes Resolve
a scarce backend behind one broker. Most of what agents ask Resolve is a
QUESTION - what clips are on Reel 09, what markers exist, what changed
since the last build - and every such question used to be a live read
under the instance lease. This module answers them from a stored copy.

A generation
------------
One timeline's whole state, read once through the one reader
(`reel_read.read_reel`, quick mode: every clip on every row with spans,
source ranges, transforms, enable state, clip colour, comps and markers
at every level), serialized canonically and hashed. Generations are
numbered per timeline, 1, 2, 3, ..., and a new one is recorded only when
the hash CHANGES, so a generation number names a distinct state.

Two producers, and the row says which:

- `observed`: `observe` read the live timeline and found it differs from
  the head - the first read, or a change nobody here made (the captain
  editing by hand never takes a lock, `resolve_lock` "The fence").
- `patch`: `edit_patch.apply_patch` committed an EditPatch and read the
  result back. The row carries the patch and its receipt, which is what
  lets a later patch on a stale base ask what happened in between.

What the shadow is NOT
----------------------
It is not the live timeline. It is the timeline AS OF the generation's
`verified_at`, and every answer here says which generation and when. A
hand edit after that is invisible until the next `observe` (or the next
patch, whose precondition read finds it and records it as `observed`
before refusing the patch). Ren never treats a shadow answer as a
precondition for a WRITE - `edit_patch` re-reads live inside the lease.

Identity
--------
A timeline is keyed by its Resolve project's EXACT name plus the
timeline's `GetUniqueId` (`reel_read._timeline_identity`), never by name
alone: a rename keeps its history, and two look-alike names never share
one. The name is stored per generation for lookup.

Where it lives
--------------
One SQLite database per machine, WAL mode, at `$REN_SHADOW_DB` or
`$XDG_STATE_HOME/ren/timeline_shadow.sqlite3` (`~/.local/state/...`).
Runtime state, not project data: Resolve projects are machine-local, and
a snapshot is re-derivable by one read. Snapshots are stored once per
hash.

    python3 -m library.tools.timeline_shadow log     --project P --timeline T
    python3 -m library.tools.timeline_shadow clips   --project P --timeline T [--generation N]
    python3 -m library.tools.timeline_shadow markers --project P --timeline T [--generation N]
    python3 -m library.tools.timeline_shadow diff    --project P --timeline T --since N [--to M]

None of these touch Resolve. `tests/test_timeline_shadow.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DB_ENV = "REN_SHADOW_DB"

OBSERVED = "observed"
PATCH = "patch"
SOURCES = (OBSERVED, PATCH)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    hash TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generations (
    project       TEXT NOT NULL,
    timeline_id   TEXT NOT NULL,
    generation    INTEGER NOT NULL,
    timeline_name TEXT NOT NULL,
    hash          TEXT NOT NULL REFERENCES snapshots(hash),
    source        TEXT NOT NULL,
    patch         TEXT,
    receipt       TEXT,
    recorded_at   REAL NOT NULL,
    verified_at   REAL NOT NULL,
    PRIMARY KEY (project, timeline_id, generation)
);
CREATE INDEX IF NOT EXISTS generations_by_name
    ON generations (project, timeline_name);
"""


class ShadowError(LookupError):
    """The shadow store has no answer to what was asked of it."""


class HeadMoved(RuntimeError):
    """A generation was appended against a head that is no longer the head."""


def default_db_path() -> Path:
    explicit = os.environ.get(DB_ENV)
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_STATE_HOME") or str(
        Path.home() / ".local" / "state")
    return Path(base) / "ren" / "timeline_shadow.sqlite3"


def canonical(snapshot: dict) -> str:
    """The one spelling a snapshot is hashed and stored in."""
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(canonical(snapshot).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Generation:
    project: str
    timeline_id: str
    generation: int
    timeline_name: str
    hash: str
    source: str
    patch: dict | None
    receipt: dict | None
    recorded_at: float
    verified_at: float

    def summary(self) -> dict:
        return {
            "project": self.project,
            "timeline": self.timeline_name,
            "timeline_id": self.timeline_id,
            "generation": self.generation,
            "hash": self.hash,
            "source": self.source,
            "patch_id": (self.patch or {}).get("id"),
            "recorded_at": self.recorded_at,
            "verified_at": self.verified_at,
        }


def _row_to_generation(row) -> Generation:
    return Generation(
        project=row["project"], timeline_id=row["timeline_id"],
        generation=row["generation"], timeline_name=row["timeline_name"],
        hash=row["hash"], source=row["source"],
        patch=json.loads(row["patch"]) if row["patch"] else None,
        receipt=json.loads(row["receipt"]) if row["receipt"] else None,
        recorded_at=row["recorded_at"], verified_at=row["verified_at"])


class ShadowStore:
    """Every recorded generation of every timeline, on this machine."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    # ── reads ──

    def head(self, project: str, timeline_id: str) -> Generation | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM generations WHERE project=? AND timeline_id=?"
                " ORDER BY generation DESC LIMIT 1",
                (project, timeline_id)).fetchone()
        return _row_to_generation(row) if row else None

    def get(self, project: str, timeline_id: str,
            generation: int) -> Generation:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM generations WHERE project=? AND timeline_id=?"
                " AND generation=?",
                (project, timeline_id, generation)).fetchone()
        if row is None:
            raise ShadowError(
                f"no generation {generation} of timeline {timeline_id!r} "
                f"in project {project!r}")
        return _row_to_generation(row)

    def history(self, project: str, timeline_id: str,
                after: int = 0) -> list:
        """Every generation newer than `after`, oldest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM generations WHERE project=? AND timeline_id=?"
                " AND generation>? ORDER BY generation",
                (project, timeline_id, after)).fetchall()
        return [_row_to_generation(r) for r in rows]

    def snapshot(self, generation: Generation) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT body FROM snapshots WHERE hash=?",
                             (generation.hash,)).fetchone()
        if row is None:
            raise ShadowError(f"snapshot {generation.hash} is missing")
        return json.loads(row["body"])

    def resolve_timeline(self, project: str, timeline: str) -> str:
        """The timeline id for an EXACT name (or an id given as-is).

        The name is matched against each timeline's NEWEST generation,
        so a rename answers under the new name, and a name two distinct
        timelines carry refuses rather than picking one (AGENTS.md 5:
        exact names, never a substitute).
        """
        with self._connect() as db:
            ids = [r["timeline_id"] for r in db.execute(
                "SELECT g.timeline_id FROM generations g WHERE g.project=?"
                " AND g.timeline_name=? AND g.generation=(SELECT"
                " MAX(generation) FROM generations h WHERE h.project="
                "g.project AND h.timeline_id=g.timeline_id)",
                (project, timeline)).fetchall()]
            if not ids and db.execute(
                    "SELECT 1 FROM generations WHERE project=? AND"
                    " timeline_id=?", (project, timeline)).fetchone():
                return timeline
        if not ids:
            raise ShadowError(
                f"no timeline named {timeline!r} has been read in project "
                f"{project!r}; `observe` it first (one live read)")
        if len(ids) > 1:
            raise ShadowError(
                f"{len(ids)} timelines in project {project!r} are named "
                f"{timeline!r} ({', '.join(ids)}); name one by its id")
        return ids[0]

    # ── writes ──

    def record(self, *, project: str, timeline_id: str, timeline_name: str,
               snapshot: dict, source: str, expected_head: int,
               patch: dict | None = None,
               receipt: dict | None = None) -> Generation:
        """Append a generation, or refresh the head when nothing changed.

        Compare-and-set on `expected_head` (0 for a timeline never read):
        a writer that read head N and finds N+1 already recorded raises
        `HeadMoved` rather than numbering two states the same. When the
        snapshot hashes equal to the head's, no generation is added and
        the head's `verified_at` advances - unless the caller is a patch,
        which always records its own row so its receipt has a home.
        """
        if source not in SOURCES:
            raise ValueError(f"source {source!r} is not one of {SOURCES}")
        digest = snapshot_hash(snapshot)
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM generations WHERE project=? AND"
                    " timeline_id=? ORDER BY generation DESC LIMIT 1",
                    (project, timeline_id)).fetchone()
                head = row["generation"] if row else 0
                if head != expected_head:
                    raise HeadMoved(
                        f"{timeline_name!r} is at generation {head}, not "
                        f"{expected_head}")
                if (row is not None and row["hash"] == digest
                        and source == OBSERVED):
                    db.execute(
                        "UPDATE generations SET verified_at=?,"
                        " timeline_name=? WHERE project=? AND timeline_id=?"
                        " AND generation=?",
                        (now, timeline_name, project, timeline_id, head))
                    db.execute("COMMIT")
                    return self.get(project, timeline_id, head)
                db.execute(
                    "INSERT OR IGNORE INTO snapshots (hash, body)"
                    " VALUES (?, ?)", (digest, canonical(snapshot)))
                db.execute(
                    "INSERT INTO generations VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (project, timeline_id, head + 1, timeline_name, digest,
                     source,
                     json.dumps(patch, sort_keys=True) if patch else None,
                     json.dumps(receipt, sort_keys=True) if receipt else None,
                     now, now))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return self.get(project, timeline_id, head + 1)


# ── The live half: one read, under the reader's own lease ─────────────


def timeline_key(resolve_project, timeline) -> tuple:
    """`(project name, timeline id, timeline name)` for the store."""
    from library.tools.reel_read import _timeline_identity

    _kind, value = _timeline_identity(timeline)
    return resolve_project.GetName(), str(value), timeline.GetName()


def read_live(resolve_project, timeline) -> dict:
    """The snapshot of a live timeline: `read_reel` quick mode.

    The timeline must be the project's CURRENT one (`read_reel` refuses
    otherwise), because a transform read through a non-current handle is
    scaled and would hash as a change that is not one.
    """
    from library.tools.reel_read import QUICK, read_reel

    snapshot = read_reel(timeline, resolve_project.GetName(), mode=QUICK,
                         resolve_project=resolve_project)
    # `read_at` is when the note was READ, not part of the timeline: left
    # in, every read would hash as a new state.
    for note in snapshot["markers"]["notes"]:
        note.pop("read_at", None)
    return snapshot


def observe(resolve_project, timeline,
            store: ShadowStore | None = None) -> Generation:
    """Read the live timeline once and record it if it changed.

    This is the `timeline.snapshot` job: the one live read that keeps
    the shadow honest after a hand edit.
    """
    store = store or ShadowStore()
    project, timeline_id, name = timeline_key(resolve_project, timeline)
    head = store.head(project, timeline_id)
    snapshot = read_live(resolve_project, timeline)
    return store.record(project=project, timeline_id=timeline_id,
                        timeline_name=name, snapshot=snapshot,
                        source=OBSERVED,
                        expected_head=head.generation if head else 0)


# ── Questions answered from the shadow, never from Resolve ────────────


def clips(snapshot: dict) -> list:
    from library.tools.reel_read import clips_of

    return clips_of(snapshot)


def markers(snapshot: dict) -> dict:
    from library.tools.reel_read import markers_of

    return markers_of(snapshot)


def _marker_keys(snapshot: dict) -> set:
    keys = set()
    for marker in snapshot.get("markers", {}).get("timeline", []):
        keys.add(("timeline", int(marker["frame"]), marker["color"],
                  marker["name"], marker["note"]))
    for clip in clips(snapshot):
        for marker in clip.get("markers", []):
            keys.add((f"clip:{clip['unique_id']}", int(marker["frame"]),
                      marker["color"], marker["name"], marker["note"]))
    return keys


def diff(earlier: dict, later: dict) -> dict:
    """What changed between two snapshots: rows, then markers.

    Rows go through `versions.rounds.diff_reel` over `reel_read.rows_of`,
    the differ promotion and round-diff already use, so the shadow never
    disagrees with them about what a row holds.
    """
    from library.tools.reel_read import rows_of
    from library.tools.versions.rounds import diff_reel

    before, after = _marker_keys(earlier), _marker_keys(later)
    fields = ("level", "frame", "color", "name", "note")
    return {
        "rows": diff_reel(rows_of(earlier), rows_of(later)),
        "markers_added": [dict(zip(fields, k)) for k in sorted(after - before)],
        "markers_removed": [dict(zip(fields, k))
                            for k in sorted(before - after)],
    }


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="timeline_shadow",
        description="Answer timeline questions from recorded generations. "
                    "Never touches Resolve.")
    parser.add_argument("--db", type=Path, default=None)
    subs = parser.add_subparsers(dest="command", required=True)
    for name, text in (("log", "every generation, newest last"),
                       ("show", "one generation's whole snapshot"),
                       ("clips", "every clip of one generation"),
                       ("markers", "every marker of one generation"),
                       ("diff", "what changed since a generation")):
        sub = subs.add_parser(name, help=text)
        sub.add_argument("--project", required=True,
                         help="the Resolve project's EXACT name")
        sub.add_argument("--timeline", required=True,
                         help="the timeline's exact name, or its id")
        if name == "diff":
            sub.add_argument("--since", type=int, required=True)
            sub.add_argument("--to", type=int, default=None,
                             help="default: the head")
        elif name != "log":
            sub.add_argument("--generation", type=int, default=None,
                             help="default: the head")
    args = parser.parse_args(argv)
    store = ShadowStore(args.db)
    try:
        timeline_id = store.resolve_timeline(args.project, args.timeline)
        head = store.head(args.project, timeline_id)
        if args.command == "log":
            out = [g.summary() for g in store.history(args.project,
                                                      timeline_id)]
        elif args.command == "diff":
            earlier = store.get(args.project, timeline_id, args.since)
            later = (store.get(args.project, timeline_id, args.to)
                     if args.to is not None else head)
            out = {"from": earlier.summary(), "to": later.summary(),
                   "diff": diff(store.snapshot(earlier),
                                store.snapshot(later))}
        else:
            generation = (store.get(args.project, timeline_id,
                                    args.generation)
                          if args.generation is not None else head)
            snap = store.snapshot(generation)
            body = {"show": snap, "clips": clips(snap),
                    "markers": markers(snap)}[args.command]
            out = {"generation": generation.summary(), args.command: body}
    except ShadowError as missing:
        print(f"timeline_shadow: {missing}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
