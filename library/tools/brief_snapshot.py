"""The creative brief's bytes, given the versioned home its path cannot have.

The design question, answered: the `project.yaml`-declared brief path is
a FREE per-project path that may sit outside the project folder entirely
(a read-only planning tree), so no static allow-list can name it - and
the versioned `project.yaml` records only WHERE it was, never WHAT it
said.  A video built from an outside brief could therefore never be
explained later: the build record shows what was built, not what it was
built from.

Either the path stops being free, or the versioning stops being static.
This module is the second answer, for three reasons:

1. The free path exists for a real reason, stated where the runner reads
   it (`run_pipeline.load_pipeline_state`): the brief may live in a
   read-only planning tree outside the project.  Constraining it to a
   fixed in-project location would force the captain to relocate - or
   duplicate - their planning tree, trading one unexplained build for
   two sources of truth.  The source is never moved here.
2. A static allow-list CAN name a fixed in-project snapshot even though
   it cannot name a free source.  The snapshot lands under
   `pipeline_output/provenance/`, which the allow-list already versions
   (`/pipeline_output/provenance/**`), whose stated purpose is "what
   [an] artifact names as its source", and which the runner already
   writes (`hooks.ledger_path`).  No allow-list change was needed - and
   `tests/test_brief_snapshot.py` pins that the snapshot is tracked, so
   a future narrowing of that wildcard breaks loudly.
3. The precedent is already in the tree: the replay bench COPIES the
   brief rather than referencing it, "because a later edit does not
   silently change what a replay reconstructs"
   (`replay_bench/snapshot.py`).  The project record does the same for
   the same reason: a later edit to the outside file does not silently
   change what a committed build claims it read.

Shape: two files, written once per run by `run_pipeline.run_pipeline`
AFTER the dry-run early return (a dry run reports and writes nothing)
and BEFORE the step loop.  NOT in `gather_step_inputs`: the replay
bench calls that function and it must not touch the project
(`run_pipeline.py`, at the `record_delivery` call).

* `creative_brief_snapshot.md` - the brief's bytes VERBATIM, as read.
* `creative_brief_snapshot.json` - `{source, sha256, bytes, lines,
  recorded_at}`: the absolute path that was read, so a reader can see
  the file lived outside the project, and the digest binding these
  bytes to the build commit that carries them.

When the run attaches no brief (declined, or none declared), any stale
snapshot from an earlier attached run is REMOVED: a tree that keeps
yesterday's brief beside a build that never read it claims an input it
did not have.  Git keeps the history; the removal is what says "this
build read no brief".

Like `versions.store.record_finished_timeline`, this never
raises: a record that breaks the build is worse than no record.  Every
failure is returned as `{"snapshotted": False, "reason": ...}` for the
caller to print.

`tests/test_brief_snapshot.py`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os

#: The brief's bytes, verbatim, as the run read them.
SNAPSHOT_MD = "creative_brief_snapshot.md"
#: What was read, when, and its digest - the binding of bytes to build.
SNAPSHOT_JSON = "creative_brief_snapshot.json"


def snapshot_relpaths() -> tuple:
    """The two project-relative snapshot paths, derived, not copied.

    Tests derive the allow-list assertion from here (the PR 1051
    pattern: inputs come from the module that writes the store, never
    from the allow-list, so a test that merely restated the list could
    not fail when the snapshot stops landing).
    """
    from library.tools.project_layout import AREAS, Area

    base = AREAS[Area.PROVENANCE].relpath
    return (f"{base}/{SNAPSHOT_MD}", f"{base}/{SNAPSHOT_JSON}")


def _resolve(declared_path: str, project_folder: str) -> str:
    """The filesystem path the runner would read, resolved the same way.

    Mirrors `gather_step_inputs`: a relative declaration is read
    against the project folder, an absolute one as given - which is
    what lets the brief live outside the project.
    """
    if os.path.isabs(declared_path):
        return declared_path
    if project_folder:
        return os.path.join(project_folder, declared_path)
    return declared_path


def _paths(project_folder: str) -> tuple:
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project_folder)
    return (
        layout.read_path(Area.PROVENANCE, SNAPSHOT_MD),
        layout.read_path(Area.PROVENANCE, SNAPSHOT_JSON),
    )


def _write_paths(project_folder: str) -> tuple:
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project_folder)
    return (
        layout.write_path(Area.PROVENANCE, SNAPSHOT_MD),
        layout.write_path(Area.PROVENANCE, SNAPSHOT_JSON),
    )


def record_brief_for_run(project_folder: str, declared_path: str) -> dict:
    """Snapshot the attached brief, or clear a stale one.  Never raises.

    `declared_path` is the value `load_pipeline_state` put in
    `state["creative_brief"]` - present only when the attachment reading
    is ATTACHED, so a falsy value means this run read no brief and any
    snapshot on file is stale.
    """
    if not declared_path:
        return _clear_stale(project_folder)
    try:
        source = _resolve(declared_path, project_folder)
        with open(source, "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        return {"snapshotted": False,
                "reason": f"brief at {declared_path!r} could not be "
                          f"read for the snapshot: {exc}"}
    if not content.strip():
        return {"snapshotted": False,
                "reason": f"brief at {source!r} is empty: nothing to "
                          f"snapshot (the step runner refuses an empty "
                          f"brief by name)"}
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        md_path, json_path = _paths(project_folder)
        if json_path.is_file():
            try:
                prior = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prior = {}
            if (prior.get("sha256") == digest
                    and md_path.is_file()
                    and md_path.read_text(encoding="utf-8") == content):
                return {"snapshotted": True, "unchanged": True,
                        "sha256": digest, "source": source,
                        "snapshot": str(md_path)}
        md_path, json_path = _write_paths(project_folder)
        md_path.write_text(content, encoding="utf-8")
        record = {
            "source": os.path.abspath(source),
            "sha256": digest,
            "bytes": len(content.encode("utf-8")),
            "lines": content.count("\n") + 1,
            "recorded_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
        }
        json_path.write_text(json.dumps(record, indent=2,
                                        sort_keys=True) + "\n",
                             encoding="utf-8")
    except OSError as exc:
        return {"snapshotted": False,
                "reason": f"brief snapshot could not be written: {exc}"}
    return {"snapshotted": True, "unchanged": False, "sha256": digest,
            "source": os.path.abspath(source),
            "snapshot": str(md_path)}


def _clear_stale(project_folder: str) -> dict:
    """Remove a snapshot no run is reading any more.  Never raises."""
    try:
        md_path, json_path = _paths(project_folder)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return {"snapshotted": False,
                "reason": f"snapshot paths could not be resolved: {exc}"}
    cleared = []
    for path in (md_path, json_path):
        try:
            if path.is_file():
                path.unlink()
                cleared.append(path.name)
        except OSError as exc:
            return {"snapshotted": False, "cleared": cleared,
                    "reason": f"stale snapshot {path.name} could not be "
                              f"removed: {exc}"}
    if cleared:
        return {"snapshotted": False, "cleared": sorted(cleared),
                "reason": "this run attached no brief, so the stale "
                          "snapshot was removed rather than left "
                          "claiming an input the build did not read"}
    return {"snapshotted": False, "cleared": [],
            "reason": "no brief attached and no snapshot on file"}


def read_snapshot(project_folder: str) -> dict | None:
    """The versioned answer to "what brief did this build read".

    Returns `{"content", "record"}` when a snapshot is on file, else
    `None` - which, on a project whose `project.yaml` declares an
    outside brief, IS the hole this module closes: nothing versioned
    says what the build was built from.
    """
    md_path, json_path = _paths(project_folder)
    try:
        if not (md_path.is_file() and json_path.is_file()):
            return None
        return {
            "content": md_path.read_text(encoding="utf-8"),
            "record": json.loads(json_path.read_text(encoding="utf-8")),
        }
    except (OSError, ValueError):
        return None
