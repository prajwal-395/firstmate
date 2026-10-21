"""Apply a feedback fix as a targeted edit on the existing timeline.

Slice 2 of the feedback-patch-first work: where slice 1 (`feedback_patch`,
once it merges - this module takes plain dicts so the two ship
independently) DECIDES patch-in-place vs rebuild, this module APPLIES the
patch. The fix lands as EditPatch operations on the existing final
timeline (`edit_patch.apply_live_patch`, capability `reel.touchup`),
never as a rebuild - the captain's hand edits, markers and every other
item stay where they are.

The guard, in order:

1. `require_qualification` - the project must be Ren Qualification
   (`resolved.jobs.QUALIFICATION_PROJECT`). Live proof runs there and
   only there; the captain's projects are refused by name.
2. `backup` - the timeline's live snapshot and its recorded shadow
   generation, BEFORE anything is touched.
3. `scope_covers` - every operation's target sits inside the declared
   scope, or the patch is refused before Resolve is written.
4. `inverse_operations` - every operation has a restore rule computed
   from the backup, or the patch is refused before Resolve is written.
   Only local, reversible families run here (`SUPPORTED_OPS`); anything
   ripple-shaped or pool-shaped (`clip.delete`, `clip.insert`,
   `retime.*`, tracks, fusion, grades, masks) never reaches Resolve
   through this path.
5. After the apply, `changes_outside_scope` diffs the read-back against
   the backup: anything that moved outside the declared scope is
   restored from the inverse patch, verified by a second read-back, and
   reported as `PatchMovedTooMuch`. A restore that does not read back
   clean raises `PatchRestoreFailed` naming the residue - the backup
   snapshot is kept either way, so nothing is silently lost.

`python3 -m library.tools.feedback_patch_apply check --before B.json
--after A.json --scope S.json` audits two snapshots offline (the real
offline caller); `guarded_apply` is the live entry, proven in
`tests/qualification/test_feedback_patch_apply_against_resolve.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools import timeline_shadow as shadow
from library.tools.resolved.jobs import QUALIFICATION_PROJECT

CAPABILITY = "reel.touchup"

#: The only operations a feedback patch may carry. Every one is local
#: (no ripple, no pool fetch) and every one inverts from the backup
#: snapshot alone. Anything else is `UnrestorableOp` before Resolve is
#: touched - FAIL-CLOSED, because an unrestorable op is a patch the guard
#: cannot keep.
SUPPORTED_OPS = frozenset({
    "marker.add",
    "marker.delete",
    "clip_marker.add",
    "clip_marker.delete",
    "clip.set_property",
    "clip.set_enabled",
})


class PatchRefused(RuntimeError):
    """The patch was refused before Resolve was written, with the reason."""


class UnrestorableOp(PatchRefused):
    """An operation this path cannot restore - it never runs here."""


class PatchMovedTooMuch(RuntimeError):
    """The read-back moved outside the declared scope.

    Carries `outside` (what moved), `restore` (the inverse receipt) and
    `backup` - the timeline was restored and read back clean, and the
    backup snapshot is kept regardless.
    """

    def __init__(self, message: str, *, outside: list, restore: dict,
                 backup: dict) -> None:
        super().__init__(message)
        self.outside = outside
        self.restore = restore
        self.backup = backup


class PatchRestoreFailed(RuntimeError):
    """The restore did not read back clean. Carries `residue` and `backup`."""

    def __init__(self, message: str, *, residue: list, backup: dict) -> None:
        super().__init__(message)
        self.residue = residue
        self.backup = backup


class WrongProject(PatchRefused):
    """Live proof runs in Ren Qualification, never a captain's project."""


def require_qualification(project) -> str:
    """The Resolve project's name, or refuse when it is not Qualification."""
    name = project.GetName()
    if name != QUALIFICATION_PROJECT:
        raise WrongProject(
            f"a feedback patch applies live only on {QUALIFICATION_PROJECT!r}, "
            f"never on {name!r} - prove it in Qualification first")
    return name


def require_exclusive_lease() -> None:
    """Refuse unless this process holds the EXCLUSIVE instance lease."""
    from library.tools import resolve_lock

    if not resolve_lock.exclusive_held():
        raise PatchRefused(
            "a feedback patch needs the EXCLUSIVE instance lease - "
            "hold it for the whole apply, or queue behind whoever does")


def backup(project, timeline, *, store=None, project_folder=None) -> dict:
    """The pre-patch backup: live snapshot plus recorded generation.

    The snapshot is the restore source; the generation is the ledger
    entry proving what the timeline held before the patch.
    """
    store = store or shadow.ShadowStore()
    snapshot = shadow.read_live(project, timeline, project_folder)
    generation = shadow.observe(project, timeline, store,
                                project_folder=project_folder)
    return {
        "generation": generation.generation,
        "hash": shadow.snapshot_hash(snapshot),
        "snapshot": snapshot,
    }


def _op_targets(op: dict) -> list:
    """`(kind, locator)` pairs this operation writes, for scope checks."""
    op_id = op.get("op")
    if op_id == "marker.add" or op_id == "marker.delete":
        return [("marker", int(op["frame"]))]
    if op_id in ("clip_marker.add", "clip_marker.delete"):
        return [("clip_marker", (str(op["unique_id"]), int(op["frame"])))]
    if op_id in ("clip.set_property", "clip.set_enabled"):
        return [("clip", str(op["unique_id"]))]
    return []


def scope_covers(operations, scope: dict) -> list:
    """Every operation target outside `scope`, or [] when all are inside.

    `scope` is `{"clips": [unique_id], "marker_frames": [frame],
    "clip_markers": [[unique_id, frame]]}` - frames in snapshot space,
    the same space both read-backs carry, so no conversion can drift.
    """
    clips = set((scope or {}).get("clips") or ())
    frames = set((scope or {}).get("marker_frames") or ())
    clip_markers = {(unique_id, int(frame))
                    for unique_id, frame in
                    ((scope or {}).get("clip_markers") or ())}
    gaps = []
    for op in operations:
        for kind, locator in _op_targets(op):
            inside = (
                (kind == "clip" and locator in clips)
                or (kind == "marker" and locator in frames)
                or (kind == "clip_marker" and locator in clip_markers))
            if not inside:
                gaps.append({"op": op.get("op"), "kind": kind,
                             "locator": locator})
    return gaps


def _clips_by_id(snapshot: dict) -> dict:
    return {clip["unique_id"]: clip for clip in shadow.clips(snapshot)}


def _timeline_markers(snapshot: dict) -> set:
    return {
        (int(marker["frame"]), marker.get("color") or "",
         marker.get("name") or "", marker.get("note") or "")
        for marker in (snapshot.get("markers") or {}).get("timeline", [])
    }


def _clip_markers(clip: dict) -> set:
    return {
        (int(marker["frame"]), marker.get("color") or "",
         marker.get("name") or "", marker.get("note") or "")
        for marker in clip.get("markers") or []
    }


_COMPARED_CLIP_FIELDS = ("name", "record_in", "record_out", "duration",
                         "enabled")
"""Clip scalar fields compared whole; `transform` is compared per key, so
a hand edit to one property is never masked by a patch to another."""


def inventory_changes(before: dict, after: dict) -> list:
    """Every inventory change between two snapshots, with no scope.

    Clips keyed by `unique_id`; timeline markers and per-clip markers by
    (frame, color, name, note). Field-level, so the guard sees a moved
    hand edit, not just an added row.
    """
    out = []
    old, new = _clips_by_id(before), _clips_by_id(after)
    for unique_id in sorted(set(old) | set(new)):
        if unique_id not in old:
            out.append({"where": "clip", "unique_id": unique_id,
                        "change": "added"})
            continue
        if unique_id not in new:
            out.append({"where": "clip", "unique_id": unique_id,
                        "change": "removed"})
            continue
        first, second = old[unique_id], new[unique_id]
        for field in ("name", "record_in", "record_out", "duration",
                      "enabled"):
            if first.get(field) != second.get(field):
                out.append({"where": "clip", "unique_id": unique_id,
                            "change": field,
                            "before": first.get(field),
                            "after": second.get(field)})
        for key in sorted(set(first.get("transform") or {})
                          | set(second.get("transform") or {})):
            old_value = (first.get("transform") or {}).get(key)
            new_value = (second.get("transform") or {}).get(key)
            if old_value != new_value:
                out.append({"where": "clip", "unique_id": unique_id,
                            "change": f"transform.{key}",
                            "before": old_value, "after": new_value})
        for marker in sorted(_clip_markers(second) - _clip_markers(first)):
            out.append({"where": "clip_marker", "unique_id": unique_id,
                        "change": "added", "marker": marker})
        for marker in sorted(_clip_markers(first) - _clip_markers(second)):
            out.append({"where": "clip_marker", "unique_id": unique_id,
                        "change": "removed", "marker": marker})
    for marker in sorted(_timeline_markers(after) - _timeline_markers(before)):
        out.append({"where": "marker", "change": "added", "marker": marker})
    for marker in sorted(_timeline_markers(before) - _timeline_markers(after)):
        out.append({"where": "marker", "change": "removed", "marker": marker})
    return out


def changes_outside_scope(before: dict, after: dict, scope: dict) -> list:
    """The inventory changes no declared scope entry covers, or []."""
    clips = set((scope or {}).get("clips") or ())
    frames = set((scope or {}).get("marker_frames") or ())
    clip_markers = {(unique_id, int(frame))
                    for unique_id, frame in
                    ((scope or {}).get("clip_markers") or ())}
    outside = []
    for change in inventory_changes(before, after):
        where = change["where"]
        if where == "clip" and change["unique_id"] in clips:
            continue
        if where == "marker" and int(change["marker"][0]) in frames:
            continue
        if (where == "clip_marker"
                and (change["unique_id"], int(change["marker"][0]))
                in clip_markers):
            continue
        outside.append(change)
    return outside


def _before_clip(before: dict, unique_id: str) -> dict:
    clip = _clips_by_id(before).get(unique_id)
    if clip is None:
        raise UnrestorableOp(
            f"clip {unique_id!r} is not in the backup snapshot - its "
            f"previous value cannot be restored from")
    return clip


def _marker_at(snapshot: dict, frame: int) -> dict:
    for marker in (snapshot.get("markers") or {}).get("timeline", []):
        if int(marker["frame"]) == int(frame):
            return marker
    raise UnrestorableOp(
        f"no timeline marker at frame {frame} in the backup snapshot - "
        f"its fields cannot be re-added on restore")


def inverse_operations(operations, before: dict) -> list:
    """The patch undoing `operations`, read off the backup snapshot.

    Refuses (`UnrestorableOp`) before anything runs when an operation is
    outside `SUPPORTED_OPS` or its previous state is not in the backup -
    a patch whose restore cannot be stated never reaches Resolve.
    """
    inverses = []
    for op in operations:
        op_id = op.get("op")
        if op_id not in SUPPORTED_OPS:
            raise UnrestorableOp(
                f"{op_id!r} has no restore rule on this path - a feedback "
                f"patch carries only {sorted(SUPPORTED_OPS)}")
        if op_id == "marker.add":
            inverses.append({"op": "marker.delete",
                             "frame": int(op["frame"])})
        elif op_id == "marker.delete":
            marker = _marker_at(before, int(op["frame"]))
            inverses.append({"op": "marker.add",
                             "frame": int(marker["frame"]),
                             "color": marker.get("color") or "",
                             "name": marker.get("name") or "",
                             "note": marker.get("note") or "",
                             "duration": int(marker.get("duration") or 1),
                             "custom_data": marker.get("custom_data") or ""})
        elif op_id == "clip_marker.add":
            inverses.append({"op": "clip_marker.delete",
                             "unique_id": str(op["unique_id"]),
                             "frame": int(op["frame"])})
        elif op_id == "clip_marker.delete":
            clip = _before_clip(before, str(op["unique_id"]))
            before_marker = next(
                (marker for marker in clip.get("markers") or []
                 if int(marker["frame"]) == int(op["frame"])), None)
            if before_marker is None:
                raise UnrestorableOp(
                    f"clip {op['unique_id']!r} carries no marker at frame "
                    f"{op['frame']} in the backup - its fields cannot be "
                    f"re-added on restore")
            inverses.append({"op": "clip_marker.add",
                             "unique_id": str(op["unique_id"]),
                             "frame": int(before_marker["frame"]),
                             "color": before_marker.get("color") or "",
                             "name": before_marker.get("name") or "",
                             "note": before_marker.get("note") or "",
                             "duration": int(before_marker.get("duration")
                                             or 1),
                             "custom_data": (before_marker.get("custom_data")
                                             or "")})
        elif op_id == "clip.set_property":
            clip = _before_clip(before, str(op["unique_id"]))
            inverses.append({"op": "clip.set_property",
                             "unique_id": str(op["unique_id"]),
                             "key": str(op["key"]),
                             "value": (clip.get("transform") or {}).get(
                                 op["key"])})
        elif op_id == "clip.set_enabled":
            clip = _before_clip(before, str(op["unique_id"]))
            inverses.append({"op": "clip.set_enabled",
                             "unique_id": str(op["unique_id"]),
                             "enabled": bool(clip.get("enabled"))})
    return list(reversed(inverses))


def _conflict_domains(operations, declared=None) -> tuple[str, ...]:
    """Include every EditPatch facet written by the feedback operations."""
    from library.tools import edit_algebra

    domains = set(declared or ())
    for operation in operations:
        spec = edit_algebra.ALGEBRA.get(operation.get("op"))
        if spec is not None:
            domains.add(spec.domain)
    return tuple(sorted(domains))


def _measured_costs(project_folder):
    """What THIS project has measured for a targeted edit and a rebuild.

    Read from the project's own performance ledger. A targeted
    revision is cheap beside a rebuild, and the record of the choice
    says so from a measurement rather than from an assumption.
    """
    if not project_folder:
        return None
    from library.tools import perf_ledger
    return perf_ledger.measured_strategy_costs(
        perf_ledger.read_rows(project_folder))


def guarded_apply(*, project, timeline, operations, scope: dict,
                  idempotency_key: str, capability: str = CAPABILITY,
                  conflict_domains=None, preconditions=(),
                  postconditions=(), store=None,
                  project_folder=None) -> dict:
    """Apply `operations` on the existing timeline, guarded end to end.

    Backup, scope check and restore-rule check all pass BEFORE Resolve is
    written; after the apply the read-back is diffed against the backup
    and anything outside `scope` is restored and reported. Returns
    `{"backup", "patch", "outside_scope"}` - with `outside_scope`
    always [] on success, since a non-empty one raises.
    """
    from library.tools import edit_patch
    from library.tools.execution_router import RoutingRefused, route_compiled_edit

    require_qualification(project)
    require_exclusive_lease()
    operations = [dict(op) for op in operations]
    # Declare every facet the operations actually write. A mixed feedback
    # patch (for example, a marker plus a clip transform) must contend on
    # both facets in the scheduler and pass EditPatch's domain validation.
    conflict_domains = _conflict_domains(operations, conflict_domains)
    if not operations:
        raise PatchRefused("a feedback patch needs at least one operation")
    if not idempotency_key:
        raise PatchRefused("a feedback patch needs a non-empty "
                           "idempotency key")
    try:
        routing = route_compiled_edit(
            {"operations": operations},
            context={"measured_costs": _measured_costs(project_folder)}
        ).as_dict()
    except RoutingRefused as exc:
        raise PatchRefused(
            f"the shared execution planner refused the edit: {exc}") from exc
    # AXI-002: the guarded path's transport is the router's.  This module
    # used to carry its own set of route names, beside the dispatcher's
    # set and the broker's gate.
    from library.tools import execution_router

    if not execution_router.is_edit_patch_route(routing["backend"]):
        raise PatchRefused(
            "the shared execution planner selected unsupported backend "
            f"{routing['backend']!r} for this guarded path")
    made = backup(project, timeline, store=store,
                  project_folder=project_folder)
    gaps = scope_covers(operations, scope)
    if gaps:
        raise PatchRefused(
            f"{len(gaps)} operation target(s) sit outside the declared "
            f"scope - refusing before Resolve is written: {gaps}")
    inverses = inverse_operations(operations, made["snapshot"])
    receipt = edit_patch.apply_live_patch(
        project=project, timeline=timeline, capability=capability,
        operations=operations, conflict_domains=conflict_domains,
        idempotency_key=idempotency_key, preconditions=list(preconditions),
        postconditions=list(postconditions), store=store,
        project_folder=project_folder)
    if receipt.get("status") != "committed":
        raise PatchRefused(
            f"EditPatch did not commit the feedback patch: {receipt}")
    after = shadow.read_live(project, timeline, project_folder)
    outside = changes_outside_scope(made["snapshot"], after, scope)
    if not outside:
        return {"backup": {k: made[k] for k in ("generation", "hash")},
                "patch": receipt, "outside_scope": [],
                "routing": routing,
                "verification": list(routing["verification"])}
    restore_receipt = edit_patch.apply_live_patch(
        project=project, timeline=timeline, capability=capability,
        operations=inverses, conflict_domains=conflict_domains,
        idempotency_key=f"{idempotency_key}:restore",
        preconditions=[], postconditions=[], store=store,
        project_folder=project_folder)
    restored = shadow.read_live(project, timeline, project_folder)
    residue = inventory_changes(made["snapshot"], restored)
    if residue:
        raise PatchRestoreFailed(
            f"the restore did not read back clean - {len(residue)} "
            f"change(s) remain: {residue}; the backup snapshot is kept",
            residue=residue, backup=made)
    raise PatchMovedTooMuch(
        f"the patch moved {len(outside)} thing(s) outside its scope and "
        f"was restored: {outside}",
        outside=outside, restore=restore_receipt, backup=made)


def check_files(before_path: str, after_path: str, scope_path: str) -> list:
    """Offline audit: the outside-scope changes between two snapshots."""
    before = json.loads(Path(before_path).read_text(encoding="utf-8"))
    after = json.loads(Path(after_path).read_text(encoding="utf-8"))
    scope = json.loads(Path(scope_path).read_text(encoding="utf-8"))
    return changes_outside_scope(before, after, scope)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.feedback_patch_apply",
        description="Audit or apply a feedback patch under its guard.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="audit two snapshots offline")
    check.add_argument("--before", required=True)
    check.add_argument("--after", required=True)
    check.add_argument("--scope", required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        outside = check_files(args.before, args.after, args.scope)
        if outside:
            print(json.dumps(outside, indent=2))
            print(f"{len(outside)} change(s) outside the declared scope")
            return 1
        print("clean: nothing moved outside the declared scope")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
