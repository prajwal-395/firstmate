"""Part-one dry run of the composed edit path (Ren): plan, read live, print, stop.

The captain wants to exercise Ren for real: a minor edit going through the
COMPOSER to the shortest capability set, with preconditions checked against
the LIVE timeline through the oracle, executing as a TOUCHUP - never a
rebuild. Three real pieces exist and have never been joined:

* PLANNING IS REAL. `library/tools/composer.py` composes goals to the
  shortest capability set and refuses unreachable ones by name.
* LIVE READING IS REAL. `library/tools/timeline_oracle.py` genuinely
  reads Resolve - `find_timeline_exact`, `under_lease` read-only getters.
* EXECUTING A TOUCHUP IS REAL AND SHIPS TODAY. `manage_project.py` has a
  `touch-reel` subcommand, `reel.touchup` is a registered operation, and
  `library/tools/reel_touchup.py` is a real module.

This module is the JOIN, and part one ends at the dry run. It composes the
reel goal, reads the live timeline, qualifies the change through the gate
that already ships, prints EXACTLY what it WOULD execute, prints what the
OLD path would have executed instead, and stops. It never executes: not
once, not to check, not "just to see".

What it calls, and what it never does
-------------------------------------
* `composer.compose` / `composer.compose_with_change` - the plan and the
  route selection. Runs nothing, writes nothing.
* `timeline_oracle.find_timeline_exact` - the timeline by EXACT name,
  never a prefix, never via `SetCurrentTimeline`.
* `reel_read.read_tracks` - the one enumeration the oracle itself
  projects (`snapshot_live_rows` is `read_tracks` projected through
  `rows_of`; one read here serves both the gate and the projection, so
  the two cannot disagree mid-edit). Read-only getters under the read's
  own shared lease.
* `timeline_oracle.evaluate_precondition_against_live` - each
  precondition judged against the live rows, plus the reel goal itself.
* `reel_touchup.qualify` - the gate, pure over the track read.
* `reel_touchup.resolve_final_name` - reel number to timeline name, off
  the plan file, no Resolve.
* `drift_check.newest_snapshots` - which recorded snapshot is the
  "records claim" side of the oracle comparison.

What it NEVER calls: `reel_touchup.apply_touchup` (the execute), any pool
import, any cursor move, any exclusive lease. A grep test pins that
(`tests/test_ren_dry_run.py`): a second implementation of any of the
three pieces is the failure mode this task exists to avoid, and an
execute hiding in the dry run is worse.

    python3 manage_project.py ren-dry-run <project> --reel 26 \\
        --new-media /path/to/logo_bulb_lines_23976.mov
    python3 -m library.tools.ren_dry_run <project> --reel 26 \\
        --new-media /path/to/logo_bulb_lines_23976.mov
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

REEL_GOAL = "state.verify_reels.reel_build"
"""The plan-declared name this dry run composes: the reel goal."""

DEFAULT_REEL = 26
"""The built reel number the dry run addresses when the caller states none.

A bare number names no client, speaker, colour or brand - but the CLIP the
swap locates always comes from the caller: --old-clip is required (like
--new-media already is) because a baked-in clip filename would be one
project's asset shipping as every project's default (AGENTS.md 14). The
documented Reel 26 ending swap is an example invocation, not a default.
"""


class DryRunRefused(RuntimeError):
    """The dry run could not honestly report. Fail closed, by name."""


class DryRunFinding(RuntimeError):
    """The shape does not fit the join. A finding, not a licence."""


# ── The change, stated structurally ────────────────────────────────
#
# The caller states WHICH item by clip name off the live read; the spec
# itself addresses it by (row, position), the way `touch-reel` takes
# `--edits`. Name-to-position happens here, once, from the same track
# read the gate qualifies - so the position cannot disagree with the
# qualification. The row is LOCATED across the timeline's video rows,
# never taken as given: `row` is an optional narrowing, and the search
# itself lives in `reel_touchup.locate_named_clip` so the dry run and
# the execute resolve a name to the same position.


def build_change_spec(
    tracks, *, reel: int, row: str = "", old_clip: str, new_media: str
) -> dict:
    """The `swap_pixels` spec for the ending swap. Pure over `tracks`.

    Finds the ONE item named `old_clip` - on `row` when a narrowing is
    given, across every video row otherwise. Zero matches, or more than
    one, refuses naming which rows were searched and what was found, so
    the model checks the ROW instead of re-checking a name that was
    right all along.
    """
    from library.tools import reel_touchup as touchup_mod

    try:
        return touchup_mod.swap_spec_for_tracks(
            tracks, reel=int(reel), old_clip=old_clip,
            new_media=new_media, row=row or "")
    except touchup_mod.TouchupRefused as refused:
        raise DryRunRefused(str(refused)) from refused


def _track_row(track) -> str:
    from library.tools import composed_edit as _ce

    return _ce.row_label(track["type"], int(track["index"]))


def describe_target_item(tracks, spec: dict) -> dict:
    """What the spec addresses, off the same read - for the report."""
    edit = (spec.get("edits") or [{}])[0]
    row = str(edit.get("row") or "").upper()
    index = int(edit.get("item"))
    for track in tracks or ():
        if _track_row(track) != row:
            continue
        clips = list(track.get("clips") or ())
        if 0 <= index < len(clips):
            clip = clips[index] or {}
            fusion = clip.get("fusion") or {}
            return {
                "row": row,
                "item": index,
                "name": clip.get("name", ""),
                "record_in": clip.get("record_in"),
                "record_out": clip.get("record_out"),
                "duration": clip.get("duration"),
                "fusion_comp_count": fusion.get("comp_count"),
                "fusion_comp_names": list(fusion.get("comp_names") or ()),
            }
    return {"row": row, "item": index}


# ── The live read: one read, shared lease only ─────────────────────
#
# `read_tracks` carries its own `@under_lease(exclusive=False)`; the
# projection is the oracle's (`live_rows_of_tracks` is `rows_of` over
# the same tracks `snapshot_live_rows` projects). No lease is taken
# here, no cursor moves, no `resolve_project` is passed - rows carry
# names and spans, which are current-independent, while a currency
# proof would need the cursor excursion this module refuses to make.


def current_resolve_project():
    """Resolve's current project, or a refusal naming why not."""
    try:
        from library.tools import marker_feedback
    except Exception as exc:
        raise DryRunRefused(f"Resolve support could not be loaded ({exc}).") from exc
    try:
        _timeline, project = marker_feedback.current_timeline()
    except Exception as exc:
        raise DryRunRefused(f"cannot read Resolve: {exc}") from exc
    return project


def read_live_tracks(project, timeline_name: str):
    """Tracks of the EXACT-named timeline, plus the timeline object."""
    from library.tools import reel_read
    from library.tools import timeline_oracle as oracle

    try:
        timeline = oracle.find_timeline_exact(project, timeline_name)
    except (oracle.TimelineNotFound, oracle.TimelineOracleError) as exc:
        raise DryRunRefused(str(exc)) from exc
    try:
        tracks = reel_read.read_tracks(timeline)
    except Exception as exc:
        raise DryRunRefused(
            f"the live timeline {timeline_name!r} could not be read "
            f"({exc}); refusing rather than judging what cannot be "
            f"seen."
        ) from exc
    return timeline, tracks


def check_project_binding(project, project_folder: str) -> dict:
    """The open Resolve project is the pipeline project's, or refuse.

    Reading timeline "Reel 26 - ending" in the WRONG Resolve project
    could match a same-named timeline and report preconditions for the
    wrong project - a confidently-wrong answer. One `GetName` getter
    against the project's own `project.yaml` binding, read-only.
    """
    try:
        live_name = project.GetName()
    except Exception as exc:
        raise DryRunRefused(
            f"the open Resolve project would not say its name ({exc})."
        ) from exc
    configured = _configured_resolve_name(project_folder)
    if configured is None:
        return {
            "live_project": live_name,
            "configured_project": None,
            "binding_checked": False,
            "note": (
                "the pipeline project names no Resolve project "
                "its binding could be checked against, so the "
                "live project is reported, not verified."
            ),
        }
    if str(live_name) != str(configured):
        raise DryRunRefused(
            f"Resolve has project {live_name!r} open, but this pipeline "
            f"project binds to {configured!r}. Open the bound project "
            f"(or pass the project whose binding matches) and re-run - "
            f"reading another project's same-named timeline would "
            f"report preconditions for the wrong project."
        )
    return {
        "live_project": live_name,
        "configured_project": configured,
        "binding_checked": True,
    }


def _configured_resolve_name(project_folder: str):
    try:
        from library.tools.project_registry import get_project

        return get_project(project_folder).resolve.project_name
    except Exception:
        return None


def load_tracks_file(path: str) -> list:
    """Tracks from a file: a `read_reel` result or `{"tracks": [...]}`."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise DryRunRefused(f"cannot read --tracks-file {path}: {exc}.") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise DryRunRefused(f"--tracks-file {path} is not JSON ({exc}).") from exc
    if isinstance(document, dict) and isinstance(document.get("tracks"), list):
        return list(document["tracks"])
    raise DryRunRefused(
        f"--tracks-file {path} carries no `tracks` list; refusing "
        f"rather than qualifying against nothing."
    )


def _load_tracks_dir(path: str):
    """`{reel: tracks}` from `<REEL>.json` files under `path`.

    Returns None after printing the refusal (the CLI's convention):
    a directory that cannot be read refuses the batch rather than
    live-reading reels the caller meant to replay.
    """
    try:
        entries = sorted(Path(path).iterdir())
    except OSError as exc:
        print(f"REFUSED: cannot read --tracks-dir {path}: {exc}.",
              file=sys.stderr)
        return None
    by_reel = {}
    for entry in entries:
        if entry.suffix != ".json" or not entry.stem.isdigit():
            continue
        try:
            by_reel[int(entry.stem)] = load_tracks_file(str(entry))
        except DryRunRefused as refused:
            print(f"REFUSED: {refused}", file=sys.stderr)
            return None
    return by_reel


def expected_rows_for(project_folder: str, timeline_name: str) -> tuple[dict, str]:
    """The records-claim side: newest build snapshot, or an honest `{}`.

    Reuses `drift_check.newest_snapshots` (the file finding) and the
    oracle's `load_rows` (the row reading) - never a new reader for the
    same question. When no snapshot names this timeline the records
    claim nothing, and the report says exactly that: the screen winning
    over empty paperwork is still the screen winning.
    """
    from library.tools import drift_check
    from library.tools import timeline_oracle as oracle

    review_dir = Path(project_folder) / drift_check.REVIEW_DIRNAME
    found = drift_check.newest_snapshots(review_dir)
    path = (found.get("snapshots") or {}).get(timeline_name)
    if path is None:
        return {}, (
            "no recorded snapshot names this timeline under "
            f"{review_dir} - the records claim nothing, so the "
            f"live picture decides alone."
        )
    try:
        return (
            oracle.load_rows(str(path)),
            f"newest build snapshot for this timeline: {path}",
        )
    except oracle.TimelineOracleError as exc:
        raise DryRunRefused(
            f"the recorded snapshot for {timeline_name!r} could not be read ({exc})."
        ) from exc


# ── The composition: plan, route, and the compile_manifest check ────


def compose_for_report(change_spec: dict, tracks) -> dict:
    """The context-free plan plus the route the change selects.

    Returns `{"plan", "selected"}` - `composer.Composition` objects, the
    module's own types, never re-described. Raises `DryRunFinding` when
    `compile_manifest` appears anywhere in the context-free plan: the
    reel goal lives in the reels process (two nodes, `build_reels` and
    `verify_reels`), so an edit_video node in its plan is a finding and
    the join stops.
    """
    from library.tools import composer as composer_mod
    from library.tools import operations as ops_mod

    plan = composer_mod.compose(REEL_GOAL)
    if plan.refused:
        raise DryRunRefused(f"the reel goal refused: {plan.refusal_reason()}")
    for op_name in plan.operations:
        owner = ops_mod.get(op_name).legacy_node
        if owner == "compile_manifest":
            raise DryRunFinding(
                f"the reel-goal plan names {op_name}, owned by "
                f"`compile_manifest` - an edit_video node in a reels goal's "
                f"plan. The structural read said this cannot happen; it "
                f"just did, so the join stops here."
            )
    selected = composer_mod.compose_with_change(REEL_GOAL, change_spec, tracks)
    if selected.refused:  # pragma: no cover - same goal composed above
        raise DryRunRefused(
            f"the reel goal with the change refused: {selected.refusal_reason()}"
        )
    return {"plan": plan, "selected": selected}


# ── Preconditions against the live timeline ──────────────────────────


def evaluate_selected_preconditions(
    selected, expected_rows: dict, live_rows: dict, project_folder: str, state: dict
) -> list:
    """The reel goal plus every require of the selected operation.

    Each through `evaluate_precondition_against_live` - the oracle's own
    three-case evaluation, never a parallel one. The context carries the
    project's real folder and state, so disk and machine checks answer
    against this project rather than an empty room.
    """
    from library.tools import operations as ops_mod
    from library.tools import requirements as req_mod
    from library.tools import timeline_oracle as oracle

    context = req_mod.Context(project_folder=project_folder or "", state=state or {})
    names = [REEL_GOAL]
    (selected_op,) = selected.operations
    names.extend(
        r.name for r in ops_mod.get(selected_op).requires if r.name not in names
    )
    evaluations = []
    for name in names:
        try:
            evaluation = oracle.evaluate_precondition_against_live(
                name, expected_rows, live_rows, context
            )
        except oracle.TimelineOracleError as exc:
            raise DryRunRefused(
                f"precondition {name!r} could not be evaluated ({exc})."
            ) from exc
        evaluations.append(evaluation)
    return evaluations


def preconditions_hold(evaluations) -> tuple[bool, list]:
    """Whether every evaluation holds, and which do not."""
    failed = []
    for evaluation in evaluations or ():
        if "satisfied_by_live_timeline" in evaluation:
            holds = bool(evaluation["satisfied_by_live_timeline"])
        else:
            holds = bool(evaluation.get("satisfied"))
        if not holds:
            failed.append(evaluation.get("precondition"))
    return (not failed, failed)


# ── The old path, as coded ───────────────────────────────────────────
#
# Read off the registry and the runner, never hardcoded: the reels
# process's capabilities in run order (`capabilities.run_order`) and -
# for caller-supplied operations the runner cannot drive - the skip,
# which is what `manage_project.cmd_build_reels` does with them: the
# runner is not the caller that supplies their arguments, so it leaves
# them out and runs the rest (`reel.build`, `reel.ask`, `reel.verify`).
# The unbound deduction below names what each skipped op would have
# refused on had the runner driven it (`spec` unbound for the three
# touchup siblings, `reel_label`/`timeline_name`/`frames` for the
# stills grab).


def old_path_walk() -> list:
    """What `build-reels` WOULD execute instead, op by op, as coded."""
    import inspect

    from library.tools import capabilities
    from library.tools import operations as ops_mod
    from library.tools import processes as processes_mod

    walk = []
    for spec in capabilities.run_order(processes_mod.REELS):
        op = ops_mod.get(spec.id)
        if op.caller_supplied:
            required = tuple(
                name
                for name, parameter in inspect.signature(op.run).parameters.items()
                if parameter.default is inspect.Parameter.empty
                and name != "project_folder"
            )
            walk.append(
                {
                    "operation": op.name,
                    "would": "skip",
                    "why": (
                        f"caller-supplied: the runner is not the caller "
                        f"that supplies {', '.join(required)} - it "
                        f"leaves this op out and runs the rest."
                    ),
                }
            )
        else:
            walk.append(
                {
                    "operation": op.name,
                    "would": "run",
                    "why": (
                        "runner-driven: gathered inputs bind it, so "
                        "the runner runs it whole."
                    ),
                }
            )
    return walk


def old_path_command(project: str, reel: int) -> str:
    return f"python3 manage_project.py build-reels {project} --only-reel {int(reel)}"


# ── The orchestration: dry means dry ─────────────────────────────────


def dry_run(
    *,
    project_folder: str,
    project_label: str,
    reel: int = 26,
    row: str = "",
    old_clip: str = "",
    new_media: str = "",
    timeline_name: str = "",
    tracks=None,
    tracks_basis: str = "",
    expected_rows=None,
    expected_basis: str = "",
) -> dict:
    """Plan, read live (or take tracks in hand), qualify, report. Stop.

    Takes `tracks` ONLY from the live read or `--tracks-file` - the
    gate reads tracks, not prose. Returns the full record; raises
    `DryRunRefused` (fail closed, by name) or `DryRunFinding` (the
    shape does not fit the join). NEVER calls `apply_touchup`.
    """
    from library.tools import composer as composer_mod
    from library.tools import reel_touchup as touchup_mod
    from library.tools import timeline_oracle as oracle

    if not project_folder or not os.path.isdir(project_folder):
        raise DryRunRefused(f"no pipeline project at {project_folder!r}.")
    if not (old_clip or "").strip():
        raise DryRunRefused(
            "the clip to swap out is not stated: pass --old-clip NAME. "
            "The engine states no clip of its own - the reference comes "
            "from the caller, never from a baked-in default."
        )
    if not new_media:
        raise DryRunRefused(
            "the replacement file is not stated: pass --new-media PATH. "
            "A touchup never renders media - render it first, then state "
            "its path."
        )
    wanted_media = os.path.abspath(os.path.expanduser(new_media))
    if not os.path.isfile(wanted_media):
        # The first refusal `pool_item_for_path` would raise at execute
        # time, answered here off disk - read-only, before anything else.
        raise DryRunRefused(
            f"the overlay file is not on disk: {wanted_media}. A "
            f"touchup never renders media - render it first, then "
            f"state its path."
        )

    final = touchup_mod.resolve_final_name(project_folder, int(reel))
    timeline_label = timeline_name or final

    live_rows = None
    if tracks is None:
        project = current_resolve_project()
        binding = check_project_binding(project, project_folder)
        _, tracks = read_live_tracks(project, timeline_label)
        tracks_basis = (
            f"live Resolve timeline {timeline_label!r} in "
            f"project {binding['live_project']!r} "
            f"(binding "
            f"{'verified' if binding['binding_checked'] else 'reported, not verified'})"
        )
    else:
        if not tracks_basis:
            tracks_basis = "supplied off-disk tracks (no Resolve read)"
        binding = {
            "live_project": None,
            "configured_project": None,
            "binding_checked": False,
            "note": (
                "off-disk tracks: no Resolve project was "
                "opened, so no binding is checked."
            ),
        }

    change_spec = build_change_spec(
        tracks, reel=int(reel), row=row, old_clip=old_clip, new_media=wanted_media
    )
    target = describe_target_item(tracks, change_spec)

    composed = compose_for_report(change_spec, tracks)
    plan, selected = composed["plan"], composed["selected"]

    live_rows = oracle.live_rows_of_tracks(tracks)
    if expected_rows is None:
        expected_rows, expected_basis = expected_rows_for(
            project_folder, timeline_label
        )
    state = _pipeline_state(project_folder)
    evaluations = evaluate_selected_preconditions(
        selected, expected_rows, live_rows, project_folder, state
    )
    holds, failed = preconditions_hold(evaluations)

    try:
        qualification = touchup_mod.qualify(tracks, change_spec)
        gate = {
            "class": qualification.gate_class,
            "cost": qualification.cost_statement,
            "notes": list(qualification.notes or ()),
            "refused": "",
        }
    except touchup_mod.TouchupRefused as refused:
        gate = {"class": "refused", "cost": "", "notes": [], "refused": str(refused)}

    (selected_op,) = selected.operations
    edits_json = json.dumps(change_spec["edits"])
    found_row = str((change_spec.get("edits") or [{}])[0].get("row")
                    or "").upper()
    record = {
        "goal": REEL_GOAL,
        "reel": int(reel),
        "final": final,
        "change": {
            "row": found_row,
            "old_clip": old_clip,
            "new_media": wanted_media,
            "target": target,
        },
        "plan": {
            "operations": list(plan.operations),
            "assumes_machine": list(plan.assumes_machine),
            "assumes_outside": list(plan.assumes_outside),
            "describe": composer_mod.describe_plan(plan),
        },
        "selection": [s.as_record() for s in selected.selection],
        "selected_operation": selected_op,
        "compile_manifest_in_plan": any(
            "compile_manifest" in op for op in plan.operations
        ),
        "tracks_basis": tracks_basis or "live read",
        "live_clip_count": oracle.live_clip_count(live_rows),
        "expected_basis": expected_basis,
        "preconditions": evaluations,
        "preconditions_hold": holds,
        "preconditions_failed": failed,
        "gate": gate,
        "binding": binding,
        "would_execute": {
            "command": (
                f"python3 manage_project.py touch-reel "
                f"{project_label} {int(reel)} --edits "
                f"'{edits_json}'"
            ),
            "call": (
                f"reel_touchup.apply_touchup({project_folder!r}, "
                f"{json.dumps(change_spec, sort_keys=True)})"
            ),
            "operation": (
                f"{selected_op} via "
                f"step_7_01_build_reels/step.py:"
                f"{_operation_attr(selected_op)}"
            ),
        },
        "old_path": {
            "command": old_path_command(project_label, int(reel)),
            "walk": old_path_walk(),
        },
    }
    record["go"] = holds and gate["class"] in (
        touchup_mod.COMPOSED,
        touchup_mod.COMPOSED_WITH_REDERIVATION,
    )
    return record


# ── The batch: the same change on every reel ───────────────────────
#
# One reel's refusal never stops the rest: a refused reel is reported
# with its reason alongside the records of the reels that hold
# together. Still a dry run throughout - this loop plans, reads and
# qualifies per reel and never executes.


def dry_run_all_reels(
    *,
    project_folder: str,
    project_label: str,
    old_clip: str = "",
    new_media: str = "",
    row: str = "",
    reels=None,
    tracks_by_reel=None,
    offline: bool = False,
    expected_rows_by_reel=None,
) -> dict:
    """Dry-run the named-clip swap on every reel. Never executes."""
    from library.tools import reel_touchup as touchup_mod

    try:
        numbers = ([int(entry) for entry in reels]
                   if reels is not None
                   else touchup_mod.reel_numbers(project_folder))
    except Exception as exc:
        raise DryRunRefused(
            f"the plan names no reels to run ({exc}).") from exc
    if not numbers:
        raise DryRunRefused("the plan names no reels to run.")
    per_reel = []
    for number in numbers:
        try:
            final = touchup_mod.resolve_final_name(
                project_folder, int(number))
        except touchup_mod.TouchupRefused as refused:
            per_reel.append({"reel": int(number), "final": "",
                             "ok": False, "go": False,
                             "refused": str(refused), "record": None})
            continue
        tracks = (tracks_by_reel or {}).get(int(number))
        basis = ""
        if tracks is not None:
            basis = (f"batch: supplied off-disk tracks for reel "
                     f"{int(number)} (no Resolve read)")
        elif offline:
            per_reel.append({
                "reel": int(number), "final": final, "ok": False,
                "go": False,
                "refused": (f"no off-disk tracks for reel "
                            f"{int(number)}: the batch was given "
                            f"tracks for other reels but none for "
                            f"this one, and offline means no live "
                            f"read is attempted."),
                "record": None})
            continue
        try:
            record = dry_run(
                project_folder=project_folder,
                project_label=project_label,
                reel=int(number),
                row=row or "",
                old_clip=old_clip,
                new_media=new_media,
                tracks=tracks,
                tracks_basis=basis,
                expected_rows=((expected_rows_by_reel or {}).get(
                    int(number))
                    if expected_rows_by_reel is not None else None),
            )
        except (DryRunRefused, DryRunFinding) as refused:
            per_reel.append({"reel": int(number), "final": final,
                             "ok": False, "go": False,
                             "refused": str(refused), "record": None})
            continue
        except touchup_mod.TouchupRefused as refused:
            per_reel.append({"reel": int(number), "final": final,
                             "ok": False, "go": False,
                             "refused": str(refused), "record": None})
            continue
        except Exception as exc:  # noqa: BLE001 - one reel never stops rest
            per_reel.append({"reel": int(number), "final": final,
                             "ok": False, "go": False,
                             "refused": f"the dry run failed ({exc!r})",
                             "record": None})
            continue
        per_reel.append({"reel": int(number), "final": final,
                         "ok": True, "go": bool(record.get("go")),
                         "refused": "", "record": record})
    go = all(entry["go"] for entry in per_reel) if per_reel else False
    return {
        "reels": per_reel,
        "go": go,
        "reels_go": sum(1 for entry in per_reel if entry["go"]),
        "reels_refused": sum(1 for entry in per_reel
                             if not entry["ok"]),
        "reels_no_go": sum(1 for entry in per_reel
                           if entry["ok"] and not entry["go"]),
    }


def render_batch_report(summary: dict) -> str:
    """Every reel's verdict on one page, plus whether anything ran."""
    lines = ["REN DRY RUN - all reels ending swap (no execution)", ""]
    for entry in summary.get("reels") or ():
        if not entry.get("ok"):
            lines.append(f"Reel {entry['reel']} "
                         f"({entry.get('final') or 'unnamed'}): "
                         f"REFUSED - {entry.get('refused')}")
            continue
        record = entry.get("record") or {}
        verdict = "GO" if entry.get("go") else "NO-GO"
        detail = ""
        if not entry.get("go"):
            gate = (record.get("gate") or {}).get("refused") or ""
            failed = ", ".join(record.get("preconditions_failed") or ())
            detail = f" - {failed or gate or 'the gate refused'}"
        target = ((record.get("change") or {}).get("target") or {})
        where = (f" on {target.get('row')}[{target.get('item')}]"
                 if target.get("name") else "")
        lines.append(f"Reel {entry['reel']} "
                     f"({entry.get('final') or 'unnamed'}): "
                     f"{verdict}{where}{detail}")
    lines.append("")
    lines.append(
        f"BATCH VERDICT: "
        f"{summary.get('reels_go', 0)}/{len(summary.get('reels') or ())} "
        f"reel(s) GO"
        + (" - the batch holds together" if summary.get("go")
           else " - refused or NO-GO reels are named above")
        + ". Nothing executed.")
    return "\n".join(lines)


def _operation_attr(op_name: str) -> str:
    from library.tools import operations as ops_mod

    return ops_mod.get(op_name).attr


def _pipeline_state(project_folder: str) -> dict:
    try:
        from library.tools.project_layout import ProjectLayout

        path = ProjectLayout(project_folder).pipeline_data_path
        if Path(path).is_file():
            return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


# ── The report: the deliverable ──────────────────────────────────────


def render_report(record: dict) -> str:
    """The dry run, in the sentences the captain has to read."""
    lines = []
    lines.append(
        f"REN DRY RUN - Reel {record['reel']} ending swap (PART ONE: no execution)"
    )
    lines.append("")
    lines.append(
        f"Change: {record['change']['old_clip']} -> "
        f"{Path(record['change']['new_media']).name} on "
        f"{record['change']['row']}, same span, no played-length "
        f"change, on {record['final']!r}."
    )
    target = record["change"]["target"] or {}
    if target.get("name"):
        lines.append(
            f"Live target: {target['row']}[{target['item']}] "
            f"{target['name']!r} @{target.get('record_in')}.."
            f"{target.get('record_out')} ({target.get('duration')}f, "
            f"fusion comps: {target.get('fusion_comp_count')})."
        )
    lines.append(f"Tracks basis: {record['tracks_basis']}.")
    lines.append("")

    lines.append("1. COMPOSED PLAN (context-free, runs no step)")
    lines.append(f"   goal: {record['goal']}")
    lines.append("   run, in order: " + ", ".join(record["plan"]["operations"]))
    lines.append(
        "   compile_manifest in plan: "
        + ("YES - FINDING, STOPPING" if record["compile_manifest_in_plan"] else "no")
    )
    lines.append("")

    lines.append("2. ROUTE SELECTION (change + live track read)")
    for selection in record["selection"]:
        lines.append(
            f"   route for {selection['node']}: "
            f"{selection['operation']} "
            f"({selection['decided_by']})"
        )
        lines.append(f"     {selection['reason']}")
    lines.append("")

    lines.append("3. LIVE PRECONDITIONS (the timeline wins over paperwork)")
    lines.append(f"   {record['expected_basis']}")
    for evaluation in record["preconditions"]:
        lines.append("   " + _one_line_verdict(evaluation).replace("\n", "\n   "))
    intents = []
    seen_sentences = set()
    for evaluation in record["preconditions"]:
        for intent in evaluation.get("hand_edits") or ():
            sentence = intent.get("sentence", "")
            if sentence and sentence not in seen_sentences:
                seen_sentences.add(sentence)
                intents.append(intent)
    if intents:
        for intent in intents:
            lines.append(f"   hand edit: {intent.get('sentence', '')}")
    else:
        lines.append("   Nothing moved: the live cut matches the records.")
    lines.append("")

    lines.append("4. GATE QUALIFICATION (pure over the track read)")
    gate = record["gate"]
    lines.append(f"   class: {gate['class']}")
    if gate["refused"]:
        lines.append(f"   refused: {gate['refused']}")
    if gate["cost"]:
        lines.append(f"   cost: {gate['cost']}")
    for note in gate["notes"]:
        lines.append(f"   - {note}")
    lines.append("")

    lines.append("5. WOULD EXECUTE (printed, never called)")
    lines.append(f"   {record['would_execute']['command']}")
    lines.append(f"   {record['would_execute']['call']}")
    lines.append(f"   {record['would_execute']['operation']}")
    lines.append("")

    lines.append("6. OLD PATH WOULD HAVE EXECUTED INSTEAD")
    lines.append(f"   {record['old_path']['command']}")
    for entry in record["old_path"]["walk"]:
        lines.append(
            f"   [{entry['would']}] {entry['operation']}: {entry['why']}"
        )
    lines.append(
        "   NOTE: the loop leaves the caller-supplied ops out - "
        "`reel.touchup`, `reel.entry_motion`, `reel.set_properties` "
        "and `reel.gate_stills` take arguments only a caller supplies "
        "- and runs the rest, so `reel.ask` and `verify_reels` run. "
        "Part two measures that path against the composed one; the "
        "contrast is reported here, not run here."
    )
    lines.append("")

    lines.append("7. MEASUREMENT CONTRACT (part two measures, not this run)")
    lines.append("   - WALL CLOCK both ways, old path vs composed path.")
    lines.append(
        "   - WHAT EACH PATH EXECUTES, counted in steps: "
        "composed = 1 operation "
        f"({record['selected_operation']}: stage + compose + "
        "verify); old = the walk above."
    )
    lines.append(
        "   - WHETHER THE RESULTING TIMELINE IS CORRECT, judged "
        "against the live cut, not a fixture."
    )
    lines.append(
        "   The hypothesis stays a hypothesis: a touchup SHOULD "
        "be dramatically cheaper, but the measurement decides, "
        "and no gain is a finding, not a failure."
    )
    lines.append("")

    verdict = (
        "GO - the dry run holds together"
        if record["go"]
        else (
            "NO-GO - "
            + (
                ", ".join(record["preconditions_failed"])
                or record["gate"]["refused"]
                or "the gate refused"
            )
        )
    )
    lines.append(f"DRY-RUN VERDICT: {verdict}. Nothing executed.")
    return "\n".join(lines)


def _one_line_verdict(evaluation: dict) -> str:
    if "live_picture" in evaluation:
        live = "shows" if evaluation.get("live_picture") else "shows no"
        held = (
            "satisfied"
            if evaluation.get("satisfied_by_live_timeline")
            else "not satisfied"
        )
        return (
            f"{evaluation.get('precondition')}: the live timeline "
            f"{live} picture ({held} against the live cut)."
        )
    name = evaluation.get("precondition")
    if evaluation.get("satisfied"):
        return f"{name}: holds ({evaluation.get('source')})."
    reason = evaluation.get("reason") or "not satisfied"
    return f"{name}: does not hold - {reason}"


# ── CLI ──────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 manage_project.py ren-dry-run",
        description=(
            "Dry-run the composed edit path for the Reel 26 "
            "ending swap: compose the reel goal, read the live "
            "timeline, qualify the touchup, print what WOULD "
            "execute, and stop. Never executes."
        ),
    )
    parser.add_argument(
        "project", help="Project slug, or an absolute path to the project directory"
    )
    parser.add_argument(
        "--reel",
        type=int,
        default=DEFAULT_REEL,
        help=f"The built reel number to change (default {DEFAULT_REEL})",
    )
    parser.add_argument(
        "--row", default="",
        help="Narrow the clip search to this overlay row "
        "(default: search every video row)",
    )
    parser.add_argument(
        "--old-clip",
        default="",
        help="The clip name on the live timeline to swap out. "
        "Required: the engine states no clip of its own.",
    )
    parser.add_argument(
        "--new-media",
        default="",
        help="The replacement file's path on disk. "
        "Required: a touchup never renders media.",
    )
    parser.add_argument(
        "--timeline",
        default="",
        help="The timeline's EXACT name (default: the plan's approved name for --reel)",
    )
    parser.add_argument(
        "--tracks-file",
        default="",
        help="JSON track read INSTEAD of reading Resolve "
        "(demonstration without a running Resolve)",
    )
    parser.add_argument(
        "--expected-rows",
        default="",
        help="JSON file carrying the expected rows "
        "instead of the newest build snapshot",
    )
    parser.add_argument(
        "--all-reels",
        action="store_true",
        help="Run the same swap on every reel the plan names, "
        "reporting per reel - one reel's refusal never stops the "
        "rest. Still never executes.",
    )
    parser.add_argument(
        "--tracks-dir",
        default="",
        help="Directory of per-reel JSON track reads named "
        "<REEL>.json (as --tracks-file, one per reel) for an "
        "--all-reels run without Resolve. A reel with no file is "
        "reported refused, never live-read.",
    )
    parser.add_argument("--out", default="", help="Write the JSON record here as well")
    args = parser.parse_args(argv)

    project_folder = args.project
    if not (os.path.isabs(project_folder) and os.path.isdir(project_folder)):
        try:
            from library.tools.project_registry import get_project

            project_folder = str(get_project(args.project).project_root)
        except Exception as exc:
            print(
                f"Error: no pipeline project at {args.project!r} ({exc}).",
                file=sys.stderr,
            )
            return 2

    tracks = None
    tracks_basis = ""
    if args.tracks_file:
        try:
            tracks = load_tracks_file(args.tracks_file)
        except DryRunRefused as refused:
            print(f"REFUSED: {refused}", file=sys.stderr)
            return 1
        tracks_basis = f"file:{args.tracks_file}"

    if args.all_reels:
        tracks_by_reel = None
        offline = False
        if args.tracks_dir:
            tracks_by_reel = _load_tracks_dir(args.tracks_dir)
            if tracks_by_reel is None:
                return 2
            offline = True
        try:
            summary = dry_run_all_reels(
                project_folder=project_folder,
                project_label=args.project,
                old_clip=args.old_clip,
                new_media=args.new_media,
                row=args.row or "",
                tracks_by_reel=tracks_by_reel,
                offline=offline,
            )
        except DryRunFinding as finding:
            print(f"FINDING: {finding}", file=sys.stderr)
            return 3
        except DryRunRefused as refused:
            print(f"REFUSED: {refused}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - fail closed, by name
            print(f"FAILED: {exc!r}", file=sys.stderr)
            return 1
        if args.out:
            try:
                Path(args.out).write_text(
                    json.dumps(summary, indent=2, sort_keys=True,
                               default=str) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                print(f"Error: cannot write {args.out}: {exc}.",
                      file=sys.stderr)
                return 2
        print(render_batch_report(summary))
        return 0 if summary["go"] else 1

    expected_rows = None
    expected_basis = ""
    if args.expected_rows:
        from library.tools import timeline_oracle as oracle

        try:
            expected_rows = oracle.load_rows(args.expected_rows)
        except oracle.TimelineOracleError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1
        expected_basis = f"expected rows file: {args.expected_rows}"

    try:
        record = dry_run(
            project_folder=project_folder,
            project_label=args.project,
            reel=args.reel,
            row=args.row,
            old_clip=args.old_clip,
            new_media=args.new_media,
            timeline_name=args.timeline,
            tracks=tracks,
            tracks_basis=tracks_basis,
            expected_rows=expected_rows,
            expected_basis=expected_basis,
        )
    except DryRunFinding as finding:
        print(f"FINDING: {finding}", file=sys.stderr)
        return 3
    except DryRunRefused as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - fail closed, by name
        print(f"FAILED: {exc!r}", file=sys.stderr)
        return 1

    if args.out:
        try:
            Path(args.out).write_text(
                json.dumps(record, indent=2, sort_keys=True, default=str) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"Error: cannot write {args.out}: {exc}.", file=sys.stderr)
            return 2
    print(render_report(record))
    if not record["go"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
