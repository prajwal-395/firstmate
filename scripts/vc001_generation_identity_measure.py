#!/usr/bin/env python3
"""VC-001 measured: durable generation identities, and the collisions that blocked a write.

The captain's punch list, 2026-10-20, VC-001: *"Establish durable version
identities."* Its fourth rule: *"Reject ambiguous or stale generation
targets."*

WHAT THIS MEASURES
------------------
Two numbers, each with its denominator, on Ren-owned SCRATCH projects:

1. **generations with a durable identity** - of the generations the
   project's own records hold, how many carry an identity that survives
   a rename and tells two same-named timelines apart.

2. **name collisions that block a write** - of the reel names two live
   timelines share, how many still stop a write before its first write.

BEFORE AND AFTER
----------------
The "before" is NOT a recollection: it is main's own rule, computed
here from the same recorded state.

- before, a write is addressed by NAME: the recorded base is the set of
  recorded generations whose name matches, and a set of anything but one
  refuses (``library/tools/resolve_axi_core.py`` on main).
- after, a write is addressed by IDENTITY: the request carries the
  Resolve timeline id, and the name is an assertion about it.

So the same script, run against the same recorded state, reports both.

READ-ONLY
---------
This script reads the shadow store and the project's own ledgers. It
writes nothing, contacts no Resolve, and touches no project. The
optional ``--record-sample`` mode records ONE generation through the
real ledger API so the "after" number for the first metric is a
measured one; it is refused unless it is given a scratch COPY to write
to.

Usage:
  bin/vep scripts/vc001_generation_identity_measure.py
  bin/vep scripts/vc001_generation_identity_measure.py --project <folder>
  bin/vep scripts/vc001_generation_identity_measure.py \\
      --project <scratch-copy> --record-sample
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

#: A project folder may be measured only when its name says it is a
#: Ren-owned scratch copy. The captain's projects are never read here.
SCRATCH_MARKERS = ("ren-scratch", "fm-", "axi0", "vc00", "vep-live",
                   "vep-qual", "vc001")


def _is_scratch(name: str) -> bool:
    lowered = str(name or "").lower()
    return any(marker in lowered for marker in SCRATCH_MARKERS)


def _projects_root() -> Path:
    from library.tools import paths

    return Path(paths.PROJECTS_ROOT)


def _resolve_project_name(folder: Path) -> str:
    from library.tools import project_registry

    return project_registry.resolve_project_name(str(folder)) or ""


def _shadow_project_key(resolve_project: str) -> str:
    from library.tools.resolve_axi_core import _shadow_project_key

    return _shadow_project_key(resolve_project)


def _recorded_generations(store, project_key: str):
    """The recorded heads of a project: (timeline_id, name) per timeline."""
    return [(generation.timeline_id, generation.timeline_name)
            for generation, _snapshot in store.current_snapshots(project_key)]


def _collisions(store, project_key: str) -> list:
    """The reel names two or more recorded timelines share."""
    by_name: dict[str, list[str]] = {}
    for timeline_id, name in _recorded_generations(store, project_key):
        by_name.setdefault(name, []).append(timeline_id)
    return sorted((name, sorted(ids)) for name, ids in by_name.items()
                  if len(ids) > 1)


def _legacy_base_resolution(store, project_key: str, name: str) -> str:
    """Main's rule: the recorded base is every generation with that NAME.

    This is main's own code, reproduced: the request names a timeline,
    the recorded base is the set of recorded generations whose name
    matches, and a set of anything but one refuses the write before its
    first write (``library/tools/resolve_axi_core.py`` on main).

    Returns "" when the name does not name exactly one timeline.
    """
    candidates = [timeline_id for timeline_id, recorded in
                  _recorded_generations(store, project_key)
                  if recorded == name]
    return candidates[0] if len(candidates) == 1 else ""


def _legacy_reel_resolution(folder: Path, resolve_project: str,
                            name: str) -> str:
    """Main's rule for NAMING A REEL: the journal first, the shadow second.

    This is ``library/tools/edit_spec.py::_current_reel_timeline_id`` on
    main (PR 3131): the journal is the record of what Ren made current,
    and the shadow answers - or refuses - when the name is shared.

    It is reported separately from the AXI gate because the two are
    different gates, and a number that conflates them says nothing.
    """
    from library.tools import timeline_shadow, undo_journal

    promoted = undo_journal.last_promoted_timeline(str(folder), name)
    if promoted:
        return promoted
    try:
        return timeline_shadow.ShadowStore().resolve_timeline(
            resolve_project, name)
    except Exception:  # noqa: BLE001 - a refusal is a blocked write
        return ""


def _identity_base_resolution(store, project_key: str, name: str,
                              folder: Path) -> tuple:
    """The new rule, through the REAL base resolution.

    The request carries the reel's Resolve timeline identity, and the
    engine's own base resolution is asked which recorded generation it
    names - the name is an assertion about it.

    Returns ``(timeline_id, reason)``: the empty id means the write is
    still blocked, and the reason says why it is RIGHT to be blocked -
    nothing in the project's own records names the current generation,
    so the target is ambiguous and VC-001 says to reject it.
    """
    from library.tools import generation_identity, resolve_axi_core
    from library.tools.edit_request import (
        EditOperation,
        EditRequest,
        EditTarget,
    )

    resolve_project = _resolve_project_name(folder)
    try:
        timeline_id = generation_identity.resolve_timeline_id(
            str(folder), resolve_project, name)
    except Exception as exc:  # noqa: BLE001 - unresolved is a refusal
        return "", f"no record names the current generation ({exc})"
    if not timeline_id:
        return "", "no record names the current generation"
    request = EditRequest(
        id="vc001-measure", project=resolve_project, timeline=name,
        timeline_id=timeline_id,
        operations=(EditOperation(op="clip.set_property",
                                   target=EditTarget(unique_id="vc001"),
                                   params={"key": "Tilt", "value": 0.0},
                                   expected_effect="measure"),))
    try:
        generation, _snapshot = resolve_axi_core._recorded_base(
            store, project_key, request)
    except Exception as exc:  # noqa: BLE001 - a refusal is a blocked write
        return "", f"the identity names no recorded base ({exc})"
    return generation.timeline_id, "the identity names the recorded base"


def _ledger_generations(folder: Path) -> tuple:
    """(total generations, generations carrying a durable identity)."""
    from library.tools import generation_identity

    total = 0
    identified = 0
    try:
        entries = generation_identity.entries_of(str(folder))
    except Exception:  # noqa: BLE001 - an unread ledger holds nothing
        return 0, 0
    for entry in entries:
        total += 1
        if generation_identity.identity_of(entry) is not None:
            identified += 1
    return total, identified


def _mirror_generations(folder: Path, project_key: str) -> tuple:
    """(recorded generations, generations carrying a durable identity).

    The recorded generations are the ones the timeline shadow holds for
    this project - every state Ren read or wrote, whether it promoted
    one or only observed it. The identity is DERIVED from the state, so
    every one of them has one; the "before" is the mirror on disk,
    written before the identity existed, where none carries one.
    """
    from library.tools import timeline_shadow

    store = timeline_shadow.ShadowStore()
    try:
        generations = store.generations_of(project_key)
    except Exception:  # noqa: BLE001 - an unread store holds nothing
        return 0, 0
    total = len(generations)
    identified = sum(1 for generation in generations
                     if generation.generation_id)
    return total, identified


def _mirror_on_disk(folder: Path) -> tuple:
    """The project's own mirror: (generations, generations with an id).

    The mirror is the versioned artifact, so what it carries is what
    another machine sees. Before the identity existed it carried none.
    """
    from library.tools import timeline_shadow

    path = timeline_shadow.project_shadow_path(folder)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0
    entries = document.get("generations") or []
    return len(entries), sum(1 for entry in entries
                             if isinstance(entry, dict)
                             and entry.get("generation_id"))


def _record_sample(folder: Path, resolve_project: str) -> dict:
    """Record ONE generation through the real ledger API, and read it back.

    This is the "after" number for the first metric: the code path the
    build, the touch and the adoption all use, exercised once on a
    scratch copy. It writes to the COPY and nothing else.
    """
    from library.tools import timeline_shadow
    from library.tools.versions import reel_versions

    store = timeline_shadow.ShadowStore()
    project_key = _shadow_project_key(resolve_project)
    recorded = _recorded_generations(store, project_key)
    if not recorded:
        return {"recorded": False, "why": "the project holds no recorded "
                                         "generation to identify"}
    timeline_id, name = recorded[0]
    rows = {"video:V1": {"media_type": "video", "index": 1, "name": "V1",
                         "items": [], "count": 0, "frames": 0}}
    entry = reel_versions.record(
        str(folder), f"{name} (vc001 sample)", kind=reel_versions.KIND_BUILD,
        rows=rows, resolve_project=resolve_project,
        resolve_timeline_id=timeline_id, author="ren")
    generation = entry.get("generation") or {}
    return {
        "recorded": True,
        "generation_id": generation.get("generation_id"),
        "resolve_timeline_id": generation.get("resolve_timeline_id"),
        "author": generation.get("author"),
        "has_durable_identity": bool(generation.get("generation_id")),
    }


def measure(folder: Path, *, record_sample: bool = False) -> dict:
    """The measurement for one scratch project."""
    from library.tools import timeline_shadow

    resolve_project = _resolve_project_name(folder)
    if not resolve_project:
        return {"project": folder.name, "measured": False,
                "why": "the project names no Resolve project"}
    store = timeline_shadow.ShadowStore()
    project_key = _shadow_project_key(resolve_project)
    collisions = _collisions(store, project_key)

    before_blocked = 0
    after_blocked = 0
    rows = []
    for name, timeline_ids in collisions:
        legacy = _legacy_base_resolution(store, project_key, name)
        legacy_reel = _legacy_reel_resolution(folder, resolve_project, name)
        identity, reason = _identity_base_resolution(
            store, project_key, name, folder)
        before_blocked += 0 if legacy else 1
        after_blocked += 0 if identity else 1
        rows.append({
            "name": name,
            "timeline_ids": timeline_ids,
            "before_axI_gate": "blocked" if not legacy else "addressed",
            "before_reel_gate": "blocked" if not legacy_reel else "addressed",
            "after": "blocked" if not identity else "addressed",
            "resolved_timeline_id": identity,
            "after_reason": reason,
        })

    total, identified = _ledger_generations(folder)
    shadow_total, shadow_identified = _mirror_generations(folder, project_key)
    mirror_total, mirror_identified = _mirror_on_disk(folder)
    sample = None
    if record_sample:
        sample = _record_sample(folder, resolve_project)
        if sample.get("recorded"):
            total, identified = _ledger_generations(folder)

    return {
        "project": folder.name,
        "resolve_project": resolve_project,
        "measured": True,
        "generations": {
            "denominator": total,
            "with_durable_identity": identified,
            "without": total - identified,
        },
        "recorded_generations": {
            "denominator": shadow_total,
            "with_durable_identity": shadow_identified,
            "without": shadow_total - shadow_identified,
            "mirror_on_disk": {
                "denominator": mirror_total,
                "with_durable_identity": mirror_identified,
            },
        },
        "collisions": {
            "denominator": len(collisions),
            "blocking_before": before_blocked,
            "blocking_after": after_blocked,
            "names": rows,
        },
        "sample_generation": sample,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", action="append", default=[],
                        help="a Ren-owned scratch project folder")
    parser.add_argument("--record-sample", action="store_true",
                        help="record ONE generation through the real "
                             "ledger API on the project given")
    args = parser.parse_args(argv)

    folders = [Path(item).expanduser().resolve() for item in args.project]
    if not folders:
        root = _projects_root()
        folders = sorted(
            path for path in root.iterdir()
            if path.is_dir() and _is_scratch(path.name)
            and (path / "project.yaml").exists())

    reports = []
    for folder in folders:
        if not _is_scratch(folder.name):
            print(f"refusing to measure {folder}: not a Ren-owned scratch "
                  f"project", file=sys.stderr)
            return 2
        try:
            reports.append(measure(folder,
                                    record_sample=args.record_sample))
        except Exception as exc:  # noqa: BLE001 - a project that cannot be
            # read is reported, never counted as zero
            reports.append({"project": folder.name, "measured": False,
                            "why": str(exc)})

    generations = {"denominator": 0, "with_durable_identity": 0, "without": 0}
    recorded = {"denominator": 0, "with_durable_identity": 0, "without": 0,
                "mirror_on_disk": {"denominator": 0,
                                   "with_durable_identity": 0}}
    collisions = {"denominator": 0, "blocking_before": 0, "blocking_after": 0}
    for report in reports:
        if not report.get("measured"):
            continue
        for key in generations:
            generations[key] += report["generations"][key]
        for key in ("denominator", "with_durable_identity", "without"):
            recorded[key] += report["recorded_generations"][key]
        for key in recorded["mirror_on_disk"]:
            recorded["mirror_on_disk"][key] += \
                report["recorded_generations"]["mirror_on_disk"][key]
        for key in collisions:
            collisions[key] += report["collisions"][key]

    print(json.dumps({
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "projects": reports,
        "totals": {"generations": generations,
                   "recorded_generations": recorded,
                   "collisions": collisions},
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
