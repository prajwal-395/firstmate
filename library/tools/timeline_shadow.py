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

A row also carries its model linkage (Lane A3): `model_revision`, the
Project Model revision the placed clips' ids were read from, and
`nle_binding_ids`, the bindings verified against this generation.
Both are recorded after the row exists - the revision does not exist
until the build commits it - through `attach_model_link` (or `record`
where the revision is already known). The shadow stays the NLE-truth
cache; the model stays the identity authority.

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

The project-scoped mirror
-------------------------
The local store is the query engine; the project git repo is the
substrate every machine sees. So each project also carries a mirror of
its own generations at `pipeline_output/review/timeline_shadow.json`
(on the version store's allow-list). `export_project` writes it on
commit (`versions/store.py`); `sync_project` merges it into a fresh
local store when a project is opened (`manage_project.py`). A second
machine - or a restarted Ren - then answers from the same generations
without a live read. The mirror is a read answer only: `edit_patch`
still re-reads live inside the lease, so a synced generation is never a
write precondition.

    python3 -m library.tools.timeline_shadow log     --project P --timeline T
    python3 -m library.tools.timeline_shadow clips   --project P --timeline T [--generation N]
    python3 -m library.tools.timeline_shadow markers --project P --timeline T [--generation N]
    python3 -m library.tools.timeline_shadow diff    --project P --timeline T --since N [--to M]

    A reel by number (LE-14): the Ren project folder plus `--reel N`
    resolves the reel's recorded exact final timeline - `--staging`
    for its `(rebuild staging)` sibling - through the project's own
    records (`reel_address`), never a prefix match:

    python3 -m library.tools.timeline_shadow clips <project> --reel 7
    python3 -m library.tools.timeline_shadow clips <project> --reel 7 --staging

None of these touch Resolve. `tests/unit/resolve/test_timeline_shadow.py`.
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
    model_revision INTEGER,
    nle_binding_ids TEXT,
    PRIMARY KEY (project, timeline_id, generation)
);
CREATE INDEX IF NOT EXISTS generations_by_name
    ON generations (project, timeline_name);
-- One row per question answered here instead of by a live read: the
-- "live reads avoided" KPI (`library/tools/resolved/kpi.py`).
CREATE TABLE IF NOT EXISTS answers (
    answered_at REAL NOT NULL,
    command     TEXT NOT NULL,
    project     TEXT NOT NULL,
    timeline_id TEXT NOT NULL
);
-- Every structural read routed through timeline_read. This is the
-- denominator for the shadow-hit KPI; misses are counted too.
CREATE TABLE IF NOT EXISTS read_requests (
    requested_at REAL NOT NULL,
    command     TEXT NOT NULL,
    project     TEXT NOT NULL,
    timeline_id TEXT,
    outcome     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS read_requests_by_time
    ON read_requests (requested_at, outcome);
"""


class ShadowError(LookupError):
    """The shadow store has no answer to what was asked of it."""


class HeadMoved(RuntimeError):
    """A generation was appended against a head that is no longer the head."""


#: Lane A3 model-linkage columns on `generations`, added after the
#: table already existed on machines. `CREATE TABLE IF NOT EXISTS`
#: never adds a column to an existing table, so a store made before
#: this lane gains them here, idempotently.
_GENERATION_MODEL_COLUMNS = (
    ("model_revision", "INTEGER"),
    ("nle_binding_ids", "TEXT"),
)


class SharedMigrationRefused(RuntimeError):
    """A qualification session refused to ALTER the shared shadow DB.

    On 2026-10-10 an unmerged lane's live proof ALTERed the shared
    store, and main's positional INSERT then broke every lane until
    the lane merged. A session whose code carries a migration that is
    not on main must run against a copied DB, never the shared one -
    so the ALTER is refused here, with the remedy, instead of landing
    in shared state.
    """


def _missing_generation_columns(db) -> list:
    columns = {row["name"] for row in
               db.execute("PRAGMA table_info(generations)").fetchall()}
    return [(name, kind) for name, kind in _GENERATION_MODEL_COLUMNS
            if name not in columns]


def _migrate_generations(db) -> None:
    for name, kind in _missing_generation_columns(db):
        db.execute(f"ALTER TABLE generations ADD COLUMN {name} {kind}")


def _refuse_shared_migration(path: Path, missing: list) -> None:
    """Refuse an ALTER the shared DB would take from a qualification run.

    The shared store is the DEFAULT path - no explicit path given and
    no `REN_SHADOW_DB` override. Anything else (an explicit file, the
    qualification harness's own scratch DB) is the caller's own to
    migrate. Only called when columns are actually missing and only
    refuses while a borrowed session is open, so ordinary runs -
    including sessions on an already-migrated store - never see it.
    """
    from library.tools import qualification_project

    session = qualification_project.qualification_session_active()
    if session is None:
        return
    names = ", ".join(name for name, _ in missing)
    raise SharedMigrationRefused(
        f"refusing to ALTER the shared shadow DB at {path} (missing "
        f"columns: {names}) from inside a qualification session "
        f"(pid {session['pid']}, borrowed from {session['project']!r}): "
        "this code carries a migration that is not on main. Copy the "
        "shared DB to scratch, set REN_SHADOW_DB to the copy, and "
        "re-run the proof against that.")


def default_db_path() -> Path:
    explicit = os.environ.get(DB_ENV)
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_STATE_HOME") or str(
        Path.home() / ".local" / "state")
    return Path(base) / "ren" / "timeline_shadow.sqlite3"


# ── The project-scoped mirror: sharing generations across machines ──
#
# The SQLite store above is machine-local runtime state. The project
# git repo is the one substrate every machine sees, so the shadow is
# mirrored into it: `export_project` writes every generation of one
# project to `pipeline_output/review/timeline_shadow.json` (on the
# version store's allow-list, `/pipeline_output/review/**`), and
# `sync_project` merges that file into a fresh local store. A second
# machine - or a restarted Ren - then sees the same generations
# without a live read. The mirror never changes the write path:
# `edit_patch` still re-reads live inside the lease, so a synced
# generation is a read answer, never a write precondition.

SHADOW_FORMAT = "timeline_shadow/1"
"""The version of the project-scoped shadow file format."""

SHADOW_FILENAME = "timeline_shadow.json"
"""The mirror's filename, under `pipeline_output/review/`."""


def project_shadow_path(project_folder) -> Path:
    """The project-scoped shadow file's path.

    Lives under `pipeline_output/review/`, which the version store's
    allow-list already commits, so the mirror is project state a second
    machine reads. Runtime state stays in the local SQLite store; this
    file is the shared projection of it.
    """
    return Path(project_folder) / "pipeline_output" / "review" / SHADOW_FILENAME


def export_project(project_name: str, project_folder,
                   store: ShadowStore | None = None) -> Path:
    """Write the project-scoped mirror for `project_name` to the project.

    The shared half of the shadow: called by `versions/store.py` on
    commit, so the mirror lands in the project git repo and travels to
    every machine that has the repo. Returns the path written.
    """
    store = store or ShadowStore()
    document = store.export_document(project_name)
    path = project_shadow_path(project_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".json.staged")
    staged.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False,
                   default=str) + "\n", encoding="utf-8")
    os.replace(staged, path)
    return path


def sync_project(project_folder, store: ShadowStore | None = None) -> dict:
    """Merge the project-scoped mirror into a local store. Idempotent.

    The read half of the sharing: a second machine (or a restarted Ren)
    reads the committed mirror and pulls in every generation it does
    not already have, so it answers timeline questions without a live
    read. A missing or unreadable mirror is not an error - a project
    with no mirror yet simply has nothing to sync. Returns what was
    merged.
    """
    store = store or ShadowStore()
    path = project_shadow_path(project_folder)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"generations": 0, "snapshots": 0, "path": str(path)}
    if not isinstance(document, dict):
        return {"generations": 0, "snapshots": 0, "path": str(path)}
    report = store.merge_document(document)
    report["path"] = str(path)
    return report


def canonical(snapshot: dict) -> str:
    """The one spelling a snapshot is hashed and stored in."""
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(canonical(snapshot).encode("utf-8")).hexdigest()


_LIGHT_CLIP_KEYS = (
    "unique_id", "name", "record_in", "record_out", "duration",
    "source_in_frame", "source_out_frame", "left_offset", "right_offset",
    "source_file", "media_pool_item_id", "enabled", "clip_color", "flags",
    "speed", "transform", "markers",
)


def light_state_of_snapshot(snapshot: dict) -> dict:
    """The structural slice the watcher fingerprints before a full read."""
    return {
        "timeline": snapshot["timeline"],
        "start_frame": snapshot["start_frame"],
        "end_frame": snapshot["end_frame"],
        "tracks": [
            {
                "type": track["type"],
                "index": track["index"],
                "name": track["name"],
                "clips": [
                    {
                        **{key: clip[key] for key in _LIGHT_CLIP_KEYS
                           if key != "markers"},
                        "markers": sorted(
                            clip["markers"],
                            key=lambda marker: int(marker["frame"])),
                    }
                    for clip in track["clips"]
                ],
            }
            for track in snapshot["tracks"]
        ],
        "markers": sorted(
            snapshot["markers"]["timeline"],
            key=lambda marker: int(marker["frame"])),
    }


def light_fingerprint(state: dict) -> str:
    """Hash the light timeline read or its projection from a stored snapshot."""
    return snapshot_hash(state)


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
    model_revision: int | None = None
    """The Project Model revision this generation is linked to (Lane A3).

    None where no model linkage has been recorded yet: the shadow
    predates the link, or the timeline state was observed without one.
    """
    nle_binding_ids: tuple = ()
    """The model `NleBinding` ids verified against this generation."""

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
            "model_revision": self.model_revision,
            "nle_binding_ids": list(self.nle_binding_ids),
        }


def _row_to_generation(row) -> Generation:
    try:
        binding_ids = json.loads(row["nle_binding_ids"])
    except (TypeError, ValueError):
        binding_ids = []
    if not isinstance(binding_ids, list):
        binding_ids = []
    return Generation(
        project=row["project"], timeline_id=row["timeline_id"],
        generation=row["generation"], timeline_name=row["timeline_name"],
        hash=row["hash"], source=row["source"],
        patch=json.loads(row["patch"]) if row["patch"] else None,
        receipt=json.loads(row["receipt"]) if row["receipt"] else None,
        recorded_at=row["recorded_at"], verified_at=row["verified_at"],
        model_revision=row["model_revision"],
        nle_binding_ids=tuple(binding_ids))


def _checked_model_revision(value) -> int | None:
    """A model revision fit to record, or None where none is recorded."""
    if value is None:
        return None
    if (not isinstance(value, int) or isinstance(value, bool)
            or value < 1):
        raise ValueError(
            f"a linked model revision must be >= 1, not {value!r}: "
            "revision 0 is the empty store, not a build")
    return value


def _checked_binding_ids(values) -> tuple:
    """Canonical binding ids: a tuple of non-empty strings."""
    if values is None:
        return ()
    ids = tuple(values)
    for binding_id in ids:
        if not isinstance(binding_id, str) or not binding_id:
            raise ValueError(
                f"an NLE binding id must be a non-empty string, "
                f"not {binding_id!r}")
    return ids


class ShadowStore:
    """Every recorded generation of every timeline, on this machine."""

    def __init__(self, path: Path | None = None) -> None:
        #: The shared store is the default path - no explicit file and
        #: no `REN_SHADOW_DB` override. Only that store gets the
        #: qualification-session migration guard below; a caller that
        #: names its DB (a test, a scratch copy) owns its migrations.
        explicit = path is not None or DB_ENV in os.environ
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
            missing = _missing_generation_columns(db)
            if missing and not explicit:
                _refuse_shared_migration(self.path, missing)
            _migrate_generations(db)

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

    def generations_of(self, project: str) -> list[Generation]:
        """Every recorded generation of every timeline in a project.

        All of them, not just heads: the mirror shares a timeline's
        whole history, so a second machine can answer "what changed
        since generation N" without a live read.
        """
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM generations WHERE project=?"
                " ORDER BY timeline_id, generation",
                (project,)).fetchall()
        return [_row_to_generation(r) for r in rows]

    def since_with_snapshots(self, project: str, timeline_id: str,
                             since: int = 0) -> list[tuple]:
        """Every generation `since` or newer, oldest first, with bodies.

        One joined read, the `current_snapshots` pattern: a generation
        row and its body share the same query view, so a concurrent
        append cannot pair a generation with another's snapshot. A
        generation whose body is missing raises, as `snapshot` does.
        """
        with self._connect() as db:
            rows = db.execute(
                "SELECT g.*, s.body FROM generations g LEFT JOIN snapshots"
                " s ON s.hash=g.hash WHERE g.project=? AND g.timeline_id=?"
                " AND g.generation>=? ORDER BY g.generation",
                (project, timeline_id, since)).fetchall()
        out = []
        for row in rows:
            if row["body"] is None:
                raise ShadowError(
                    f"snapshot {row['hash']} is missing")
            out.append((_row_to_generation(row), json.loads(row["body"])))
        return out

    def current_snapshots(self, project: str | None = None) -> list[tuple]:
        """Heads and bodies from one SQLite read snapshot.

        A name lookup and the returned body share the same query view, so a
        concurrent generation append cannot pair an old head with a newer
        snapshot (or return a generation that was already superseded before
        its body was fetched).
        """
        where = ""
        params: tuple = ()
        if project is not None:
            where = "g.project=? AND "
            params = (project,)
        with self._connect() as db:
            rows = db.execute(
                "SELECT g.*, s.body FROM generations g JOIN snapshots s"
                " ON s.hash=g.hash WHERE " + where +
                " g.generation=(SELECT MAX(h.generation) FROM generations h"
                " WHERE h.project=g.project AND h.timeline_id=g.timeline_id)"
                " ORDER BY g.project, g.timeline_name, g.timeline_id", params
            ).fetchall()
        return [(_row_to_generation(row), json.loads(row["body"]))
                for row in rows]

    def heads(self, project: str) -> list[Generation]:
        """The newest recorded generation of each timeline in a project."""
        return [generation for generation, _snapshot in
                self.current_snapshots(project)]

    def all_heads(self) -> list[Generation]:
        """The newest recorded generation of every known timeline."""
        return [generation for generation, _snapshot in
                self.current_snapshots()]

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

    def answered(self, command: str, project: str, timeline_id: str) -> None:
        """Count one question this store answered in place of Resolve."""
        with self._connect() as db:
            # Named columns, like every INSERT here: a schema that gains
            # a column must not break this writer (2026-10-10: a shared
            # store's new columns broke a positional INSERT on main).
            db.execute("INSERT INTO answers (answered_at, command, project,"
                       " timeline_id) VALUES (?, ?, ?, ?)",
                       (time.time(), command, project, timeline_id))

    def record_read_request(self, command: str, project: str,
                            timeline_id: str | None, outcome: str) -> None:
        """Record one structural read and whether the shadow served it."""
        if outcome not in {"shadow_hit", "live_refresh", "miss"}:
            raise ValueError(f"unknown read outcome {outcome!r}")
        with self._connect() as db:
            db.execute("INSERT INTO read_requests (requested_at, command,"
                       " project, timeline_id, outcome)"
                       " VALUES (?, ?, ?, ?, ?)",
                       (time.time(), command, project, timeline_id, outcome))

    def answers_since(self, since: float,
                      until: float | None = None) -> int:
        with self._connect() as db:
            if until is not None:
                return db.execute("SELECT COUNT(*) FROM answers WHERE"
                                  " answered_at >= ? AND answered_at <= ?",
                                  (since, until)).fetchone()[0]
            return db.execute("SELECT COUNT(*) FROM answers WHERE"
                              " answered_at >= ?", (since,)).fetchone()[0]

    def read_requests_since(self, since: float,
                            until: float | None = None) -> dict:
        """Read requests by outcome for the shadow-hit-rate denominator."""
        with self._connect() as db:
            if until is None:
                rows = db.execute(
                    "SELECT outcome, COUNT(*) AS count FROM read_requests"
                    " WHERE requested_at >= ? GROUP BY outcome", (since,)
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT outcome, COUNT(*) AS count FROM read_requests"
                    " WHERE requested_at >= ? AND requested_at <= ?"
                    " GROUP BY outcome", (since, until)
                ).fetchall()
        counts = {row["outcome"]: row["count"] for row in rows}
        return {
            "shadow_hits": counts.get("shadow_hit", 0),
            "live_refreshes": counts.get("live_refresh", 0),
            "misses": counts.get("miss", 0),
        }

    # ── writes ──

    def record(self, *, project: str, timeline_id: str, timeline_name: str,
               snapshot: dict, source: str, expected_head: int,
               patch: dict | None = None,
               receipt: dict | None = None,
               model_revision: int | None = None,
               nle_binding_ids=None) -> Generation:
        """Append a generation, or refresh the head when nothing changed.

        Compare-and-set on `expected_head` (0 for a timeline never read):
        a writer that read head N and finds N+1 already recorded raises
        `HeadMoved` rather than numbering two states the same. When the
        snapshot hashes equal to the head's, no generation is added and
        the head's `verified_at` advances - unless the caller is a patch,
        which always records its own row so its receipt has a home.

        `model_revision` + `nle_binding_ids` (Lane A3) link the row to
        the Project Model revision the placed clips' ids were read from
        and the bindings verified against it. A row recorded without
        them carries None/() and gains them later through
        `attach_model_link`, once the build's revision is committed -
        placement is recorded first, the revision after it, because the
        revision does not exist until the build commits it.
        """
        if source not in SOURCES:
            raise ValueError(f"source {source!r} is not one of {SOURCES}")
        revision = _checked_model_revision(model_revision)
        binding_ids = _checked_binding_ids(nle_binding_ids)
        if binding_ids and revision is None:
            raise ValueError(
                "nle_binding_ids name model entities and need the model "
                "revision they were verified against: refusing ids "
                "without a revision")
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
                    if revision is not None:
                        return self.attach_model_link(
                            project=project, timeline_id=timeline_id,
                            generation=head, model_revision=revision,
                            nle_binding_ids=binding_ids)
                    return self.get(project, timeline_id, head)
                db.execute(
                    "INSERT OR IGNORE INTO snapshots (hash, body)"
                    " VALUES (?, ?)", (digest, canonical(snapshot)))
                db.execute(
                    "INSERT INTO generations (project, timeline_id,"
                    " generation, timeline_name, hash, source, patch,"
                    " receipt, recorded_at, verified_at, model_revision,"
                    " nle_binding_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project, timeline_id, head + 1, timeline_name, digest,
                     source,
                     json.dumps(patch, sort_keys=True) if patch else None,
                     json.dumps(receipt, sort_keys=True) if receipt else None,
                     now, now, revision,
                     json.dumps(list(binding_ids)) if binding_ids else None))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return self.get(project, timeline_id, head + 1)

    def attach_model_link(self, *, project: str, timeline_id: str,
                          generation: int, model_revision: int,
                          nle_binding_ids=None) -> Generation:
        """Join one recorded generation to a committed model revision.

        The revision is known only after the build commits it, which is
        after the generation row exists - so the link is an UPDATE, not
        a second row. Fail-closed: an unknown generation raises
        `ShadowError`, and a revision older than the one recorded
        raises `ValueError` (revisions only move forward along the
        store's hash chain). The same revision with different binding
        ids is also refused: one revision carries one set of bindings.
        """
        revision = _checked_model_revision(model_revision)
        if revision is None:  # unreachable: the check above refuses None
            raise ValueError("attach_model_link needs a model revision")
        binding_ids = _checked_binding_ids(nle_binding_ids)
        with self._connect() as db:
            row = db.execute(
                "SELECT model_revision, nle_binding_ids FROM generations"
                " WHERE project=? AND timeline_id=? AND generation=?",
                (project, timeline_id, generation)).fetchone()
            if row is None:
                raise ShadowError(
                    f"no generation {generation} of timeline {timeline_id!r} "
                    f"in project {project!r}")
            previous = row["model_revision"]
            if previous is not None and revision < previous:
                raise ValueError(
                    f"generation {generation} already links revision "
                    f"{previous}; refusing older revision {revision}")
            try:
                previous_ids = tuple(json.loads(
                    row["nle_binding_ids"] or "[]"))
            except ValueError:
                previous_ids = ()
            if (previous == revision and previous_ids
                    and tuple(previous_ids) != binding_ids):
                raise ValueError(
                    f"generation {generation} already links revision "
                    f"{revision} with different bindings; refusing")
            db.execute(
                "UPDATE generations SET model_revision=?,"
                " nle_binding_ids=? WHERE project=? AND timeline_id=?"
                " AND generation=?",
                (revision,
                 json.dumps(list(binding_ids)) if binding_ids else None,
                 project, timeline_id, generation))
        return self.get(project, timeline_id, generation)

    def export_document(self, project: str) -> dict:
        """The project-scoped mirror document for `project`.

        Every generation of the project and the snapshots they
        reference, as one JSON-able dict. A generation whose snapshot
        is missing is skipped rather than exported broken: the mirror
        stays loadable, and the next live read records the snapshot
        again.
        """
        generations = []
        snapshots: dict = {}
        for generation in self.generations_of(project):
            try:
                body = self.snapshot(generation)
            except ShadowError:
                continue
            generations.append({
                "project": generation.project,
                "timeline_id": generation.timeline_id,
                "generation": generation.generation,
                "timeline_name": generation.timeline_name,
                "hash": generation.hash,
                "source": generation.source,
                "patch": generation.patch,
                "receipt": generation.receipt,
                "recorded_at": generation.recorded_at,
                "verified_at": generation.verified_at,
                "model_revision": generation.model_revision,
                "nle_binding_ids": list(generation.nle_binding_ids),
            })
            snapshots[generation.hash] = body
        return {
            "format": SHADOW_FORMAT,
            "project": project,
            "snapshots": snapshots,
            "generations": generations,
        }

    def merge_document(self, document: dict) -> dict:
        """Merge a mirror document into this store. Idempotent.

        A snapshot is inserted only when its hash is unknown; a
        generation only when `(project, timeline_id, generation)` is
        not already recorded. A machine's own newer data is never
        clobbered - the mirror adds what is missing, never rewrites
        what is there.

        A generation number both stores hold with DIFFERENT hashes is a
        real divergence: reported in `conflicts`, never silently
        resolved. This machine keeps its own observation - the shadow
        is a cache, not a source of truth, so a conflict is surfaced
        for a reader, not picked by the merge.
        """
        report = {"generations": 0, "snapshots": 0, "conflicts": []}
        if document.get("format") != SHADOW_FORMAT:
            return report
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for digest, body in (document.get("snapshots") or {}).items():
                    known = db.execute(
                        "SELECT 1 FROM snapshots WHERE hash=?",
                        (digest,)).fetchone()
                    if known is None:
                        db.execute(
                            "INSERT INTO snapshots (hash, body)"
                            " VALUES (?, ?)",
                            (digest, canonical(body)))
                        report["snapshots"] += 1
                for entry in (document.get("generations") or []):
                    key = (entry["project"], entry["timeline_id"],
                           entry["generation"])
                    row = db.execute(
                        "SELECT hash FROM generations WHERE project=? AND"
                        " timeline_id=? AND generation=?",
                        key).fetchone()
                    if row is not None:
                        if row["hash"] == entry["hash"]:
                            continue
                        report["conflicts"].append({
                            "project": entry["project"],
                            "timeline_id": entry["timeline_id"],
                            "generation": entry["generation"],
                            "local_hash": row["hash"],
                            "mirror_hash": entry["hash"],
                        })
                        continue
                    revision = _checked_model_revision(
                        entry.get("model_revision"))
                    binding_ids = _checked_binding_ids(
                        entry.get("nle_binding_ids"))
                    db.execute(
                        "INSERT INTO generations (project, timeline_id,"
                        " generation, timeline_name, hash, source, patch,"
                        " receipt, recorded_at, verified_at,"
                        " model_revision, nle_binding_ids)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (entry["project"], entry["timeline_id"],
                         entry["generation"], entry["timeline_name"],
                         entry["hash"], entry["source"],
                         json.dumps(entry["patch"], sort_keys=True)
                         if entry.get("patch") else None,
                         json.dumps(entry["receipt"], sort_keys=True)
                         if entry.get("receipt") else None,
                         entry["recorded_at"], entry["verified_at"],
                         revision,
                         json.dumps(list(binding_ids))
                         if binding_ids else None))
                    report["generations"] += 1
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return report


# ── The live half: one read, under the reader's own lease ─────────────


def answers_since(since: float, path: Path | None = None,
                  until: float | None = None) -> int | None:
    """Questions answered since `since`; None where no store exists yet."""
    target = Path(path) if path is not None else default_db_path()
    if not target.exists():
        return None
    return ShadowStore(target).answers_since(since, until=until)


def read_requests_since(since: float, path: Path | None = None,
                        until: float | None = None) -> dict | None:
    """Read-request counts; None means this machine has no shadow history."""
    target = Path(path) if path is not None else default_db_path()
    if not target.exists():
        return None
    return ShadowStore(target).read_requests_since(since, until=until)


def timeline_key(resolve_project, timeline) -> tuple:
    """`(project name, timeline id, timeline name)` for the store."""
    from library.tools.reel_read import _timeline_identity

    _kind, value = _timeline_identity(timeline)
    return resolve_project.GetName(), str(value), timeline.GetName()


def read_live(resolve_project, timeline, project_folder=None) -> dict:
    """The snapshot of a live timeline: `read_reel` quick mode.

    The timeline must be the project's CURRENT one (`read_reel` refuses
    otherwise), because a transform read through a non-current handle is
    scaled and would hash as a change that is not one.
    """
    from library.tools.reel_read import QUICK, read_reel

    snapshot = read_reel(timeline, resolve_project.GetName(), mode=QUICK,
                         project_folder=project_folder,
                         resolve_project=resolve_project)
    # `read_at` is when the note was READ, not part of the timeline: left
    # in, every read would hash as a new state.
    for note in snapshot["markers"]["notes"]:
        note.pop("read_at", None)
    return snapshot


def observe(resolve_project, timeline,
            store: ShadowStore | None = None, *,
            project_folder=None) -> Generation:
    """Read the live timeline once and record it if it changed.

    This is the `timeline.snapshot` job: the one live read that keeps
    the shadow honest after a hand edit.
    """
    store = store or ShadowStore()
    project, timeline_id, name = timeline_key(resolve_project, timeline)
    try:
        head = store.head(project, timeline_id)
        snapshot = read_live(resolve_project, timeline, project_folder)
        generation = store.record(
            project=project, timeline_id=timeline_id,
            timeline_name=name, snapshot=snapshot, source=OBSERVED,
            expected_head=head.generation if head else 0)
    except Exception:
        store.record_read_request("timeline.snapshot", project, timeline_id,
                                  "miss")
        raise
    store.record_read_request("timeline.snapshot", project, timeline_id,
                              "live_refresh")
    return generation


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


def _resolve_scope(args, parser) -> tuple:
    """`(Resolve project, exact timeline)` for this invocation.

    `--reel N` plus the Ren project folder resolves through the
    project's own records (`reel_address`: the binding plus LE-07's
    recorded exact final/staging name) - never a prefix match. Without
    it, `--project`/`--timeline` behave exactly as before. Mixing the
    two addressings, or a project path without `--reel`, is a usage
    error (exit 2); an unresolvable reel is a refusal (exit 4).
    """
    if getattr(args, "reel", None) is not None:
        if args.project or args.timeline:
            parser.error("--reel N addresses the reel through the Ren "
                         "project's own records - pass the project folder "
                         "instead of --project/--timeline, not as well")
        if not args.project_ref:
            parser.error("--reel N needs the Ren project folder, e.g. "
                         "timeline_shadow clips <project> --reel 7")
        from library.tools import reel_address

        resolved = reel_address.resolve_reel_timeline(
            args.project_ref, args.reel,
            staging=bool(args.staging))
        return resolved["resolve_project"], resolved["timeline"]
    if args.project_ref:
        parser.error("a project folder addresses a reel by number - "
                     "pass --reel N with it, or use "
                     "--project/--timeline without it")
    if getattr(args, "staging", False):
        parser.error("--staging refines --reel N - pass both, or neither")
    if not args.project or not args.timeline:
        parser.error("pass --project/--timeline, or the Ren project folder "
                     "with --reel N")
    return args.project, args.timeline


def _main(argv=None) -> int:
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE,
        RenRefusal,
        emit_refusal_result,
    )

    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()

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
        sub.add_argument("project_ref", nargs="?",
                         help="Ren project folder (or slug): with --reel N, "
                              "resolve the reel's exact timeline name from "
                              "the project's own records instead of "
                              "--project/--timeline")
        sub.add_argument("--project", required=False, default="",
                         help="the Resolve project's EXACT name "
                              "(omit with --reel N)")
        sub.add_argument("--timeline", required=False, default="",
                         help="the timeline's exact name, or its id "
                              "(omit with --reel N)")
        sub.add_argument("--reel", default=None,
                         help="the reel number (e.g. 7): read the reel's "
                              "recorded exact final timeline - with "
                              "--staging, its staging sibling. Needs the "
                              "Ren project folder, never a prefix match.")
        sub.add_argument("--staging", action="store_true",
                         help="with --reel N, read the (rebuild staging) "
                              "sibling instead of the final")
        if name == "diff":
            sub.add_argument("--since", type=int, required=True)
            sub.add_argument("--to", type=int, default=None,
                             help="default: the head")
        elif name != "log":
            sub.add_argument("--generation", type=int, default=None,
                             help="default: the head")
    args = parser.parse_args(argv)
    try:
        project, timeline = _resolve_scope(args, parser)
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        return REFUSAL_EXIT_CODE
    store = ShadowStore(args.db)
    try:
        timeline_id = store.resolve_timeline(project, timeline)
        head = store.head(project, timeline_id)
        if args.command == "log":
            out = [g.summary() for g in store.history(project,
                                                      timeline_id)]
        elif args.command == "diff":
            earlier = store.get(project, timeline_id, args.since)
            later = (store.get(project, timeline_id, args.to)
                     if args.to is not None else head)
            out = {"from": earlier.summary(), "to": later.summary(),
                   "diff": diff(store.snapshot(earlier),
                                store.snapshot(later))}
        else:
            generation = (store.get(project, timeline_id,
                                    args.generation)
                          if args.generation is not None else head)
            snap = store.snapshot(generation)
            body = {"show": snap, "clips": clips(snap),
                    "markers": markers(snap)}[args.command]
            out = {"generation": generation.summary(), args.command: body}
    except ShadowError as missing:
        print(f"timeline_shadow: {missing}", file=sys.stderr)
        return 2
    store.answered(args.command, project, timeline_id)
    store.record_read_request(f"timeline_shadow.{args.command}",
                              project, timeline_id, "shadow_hit")
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        raise SystemExit(_main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
