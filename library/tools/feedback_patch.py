"""Patch-in-place vs rebuild for a routed feedback note.

How a feedback note today becomes a rebuild (the path this module diverts
from - cite file:line):

* `library/tools/marker_feedback.py:499` (`read_notes`) reads the
  captain's typed markers off the Resolve timeline; `:1145` (`pull`)
  writes them durably to `<project>/marker_feedback/`.
* `library/tools/marker_routing.py:914` (`route_note`) routes each note
  to the step owning its decision (`STEP_DECISIONS`, `:232`).
* `library/processes/edit_video/run_pipeline.py:1265`
  (`gather_step_inputs`) hands the routed notes to planning steps as a
  prompt (`marker_routing.prompt_block`, `:1291`, delivered at
  run_pipeline.py:1561-1565, refused when undeliverable by
  `assert_deliverable`, marker_routing.py:1335).
* The plan is rebuilt from scratch and promoted over the final
  (`library/tools/reel_build.py`, `ren build` / `build-reels`), with the
  captain's hand edits carried back on (`editor_edit_carry.py:2276`,
  `carry_editor_edits`, via `versions/variants.py:1593`); the guards
  refuse rather than overwrite his edits (`reel_replace_guard.py`).
* The answer comes back as a green reply marker
  (`marker_feedback.py:834` `place_reply_marker`, `:909`
  `place_reply_clip_marker`; recorded by `marker_resolution.py:933`
  `resolve_note`), keyed to the note's durable identity
  (`feedback_ledger.py:207` `durable_identity`).

What targeted-edit machinery already exists (the path a patch takes):

* `library/tools/edit_patch.py:530` (`apply_live_patch`) - the unit of
  work a Resolve timeline accepts: preconditions, ordered operations,
  read-back judgement.
* `library/tools/edit_algebra.py` - the union vocabulary of reversible
  timeline operations every patch op is drawn from.
* `library/tools/reel_touchup.py:2196` (`apply_touchup`, reached as the
  `reel.touchup` operation, `operations.py:1550`, `step_7_01_build_reels/
  step.py:318` `touch_reel`) - change one built reel's own timeline in
  place instead of rebuilding it.
* `library/tools/resolved/ui_transaction.py:1` (`resolve.ui_transaction`)
  plus the `resolve-control` driver (`native/resolve-control/`) - UI acts
  the scripting API cannot reach.

This module is slice 1: the classifier. `decide` answers patch-in-place
vs rebuild from what the fix changes, and records the reason:

* a change that stays inside existing items/overlays/captions/markers is
  a PATCH - the fix edits properties of things already on the timeline;
* a change to the plan's selection or order is a REBUILD - the fix
  re-chooses what plays, in what order, and only a fresh plan says that.

The default is rebuild (today's behaviour): anything the classifier
cannot place - ambiguous, unrouted, unknown-step, unplaced, or a step
outside both tables - rebuilds, with the reason saying why no patch was
claimed. A patch is claimed for notes routed to one step about existing
items. A typed timeline program is sent to the execution router and is a
patch only when a production-qualified backend is selected; unsupported and
unqualified requests are refused with the router's reason. `ren revise`
(`library/tools/revise.py`) submits the resulting `EditRequest` through AXI.

`python3 -m library.tools.feedback_patch report --project <dir>` prints
the verdict for every routed note of a project (read-only).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Mapping

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools import marker_routing

VERDICT_PATCH = "patch"
VERDICT_REBUILD = "rebuild"
VERDICT_REFUSED = "refused"
"""Only for a timeline program whose ops nothing here can execute."""

#: Steps whose decision is about the properties of things already on the
#: timeline - a fix stays inside existing items/overlays/captions/markers.
PATCH_STEPS = frozenset({
    "plan_subtitles",        # how the words group into caption cards
    "plan_transitions",      # which cuts carry a drawn transition
    "plan_vfx",              # which effects sit on which clip
    "plan_sfx",              # which sound effect plays, where, at what level
    "render_subtitles",      # how a caption card is drawn
    "render_motion_graphics",  # the overlays, lower thirds, timed text cards
    "color_grade",           # the per-clip look and exposure normalisation
    "audio_mix",             # the per-block levels
    "compile_manifest",      # the placement geometry clips are conformed to
})

#: Steps whose decision is the plan's selection or order - a fix re-chooses
#: what plays, and only a fresh plan says that.
REBUILD_STEPS = frozenset({
    "semantic_analysis",   # what the vision pass measured (re-measure)
    "creative_direction",  # the direction for the piece (re-plan)
    "speech_sequence",     # which passages, in what order, cut from where
    "music_selection",     # which track plays, and which section
    "mesh_spine",          # block order and the gaps between them
    "select_broll",        # which b-roll covers which block
    "review_rough_cut",    # the review judgement before post-production
    "select_reels",        # which stretches become reels
    "render",              # what the build rendered, output-wide
})

#: Every routable step is in exactly one table - a step the tables do not
#: know is a step the classifier must not claim a patch for.
_UNKNOWN = frozenset(marker_routing.BY_NODE_ID) - PATCH_STEPS - REBUILD_STEPS
assert not _UNKNOWN, (
    f"feedback_patch tables do not cover routable steps: {sorted(_UNKNOWN)} - "
    f"decide whether each names existing items (PATCH_STEPS) or the plan's "
    f"selection or order (REBUILD_STEPS)")
assert not (PATCH_STEPS & REBUILD_STEPS), (
    f"steps in both tables: {sorted(PATCH_STEPS & REBUILD_STEPS)}")


def _as_dict(note) -> dict:
    """A routed note as a plain dict, whatever shape it arrives in."""
    if isinstance(note, dict):
        return note
    asdict = getattr(note, "__dataclass_fields__", None)
    if asdict is not None:
        from dataclasses import asdict as _asdict

        return _asdict(note)
    return dict(note)


def _edit_spec_owners(evidence: dict) -> list:
    """The owning steps of a typed edit spec's operations, in order."""
    owners = []
    for operation in (evidence or {}).get("edit_spec_ops") or ():
        owner = operation.get("owner")
        if owner and owner not in owners:
            owners.append(owner)
    return owners


def _decide_program(operations: list, steps: list,
                    evidence: Mapping | None = None) -> dict:
    """Route a semantic timeline program through the shared capability router.

    This classifier does not maintain an execution allowlist. In-place
    execution is eligible only when the shared router names a safe backend.

    The policy the router is given is the SAME one the AXI planner uses
    (`edit_qualification_policy`). Asking for the production policy here
    refused every operation the planner would have run under the
    protected experimental policy, so the front door could only ever
    reach the production-enabled operations.
    """
    from library.tools import edit_qualification_policy
    from library.tools import edit_request, execution_router

    evidence = evidence or {}
    try:
        normalized = [edit_request._program_op_to_operation(operation, index)
                      .to_algebra_op()
                      for index, operation in enumerate(operations)]
    except (TypeError, ValueError) as exc:
        return {"verdict": VERDICT_REFUSED,
                "reason": f"the timeline program is invalid: {exc}",
                "steps": steps, "execution": None}
    context = edit_qualification_policy.protected_experimental_context(
        [str(operation.get("op", "")) for operation in normalized],
        originator_name=str(evidence.get("originator") or ""))
    if context is None:
        context = {"require_qualified": True}
    try:
        routing = execution_router.route_compiled_edit(
            {"operations": normalized},
            context=context).as_dict()
    except execution_router.RoutingRefused as exc:
        return {"verdict": VERDICT_REFUSED,
                "reason": f"the shared execution planner refused this edit: {exc}",
                "steps": steps, "execution": None}
    return {
        "verdict": VERDICT_PATCH,
        "reason": (
            "the shared execution router selected "
            f"{routing['backend']} for the typed timeline program"),
        "steps": steps,
        "execution": routing,
    }


def decide(note) -> dict:
    """Patch-in-place or rebuild for one routed note, with the reason.

    `note` is a `marker_routing.RoutedNote` (or its `asdict`), i.e. it
    carries `outcome`, `basis`, `steps`, `frame` and `evidence`. Returns
    `{"verdict", "reason", "steps"}`; the reason is the record of why.

    A typed edit spec places itself: each operation carries its own
    anchor (the words, the reel or the items), so it needs no marker
    frame - a note typed in chat has none and is still a patch when its
    operations stay inside existing items. A timeline program is a patch
    or is `VERDICT_REFUSED` (`_decide_program`), never a rebuild.
    """
    raw = _as_dict(note)
    outcome = raw.get("outcome") or marker_routing.OUTCOME_UNROUTED
    basis = raw.get("basis") or ""
    steps = list(raw.get("steps") or ())
    frame = raw.get("frame")

    if basis == marker_routing.BASIS_EDIT_SPEC:
        operations = list((raw.get("evidence") or {}).get(
            "edit_spec_ops") or ())
        if any(operation.get("ledger_op") == "edit_program"
               for operation in operations):
            return _decide_program(operations, steps, raw.get("evidence") or {})
        if outcome == marker_routing.OUTCOME_ROUTED:
            return _decide_edit_spec(raw, steps)

    if outcome != marker_routing.OUTCOME_ROUTED:
        return {
            "verdict": VERDICT_REBUILD,
            "reason": (
                f"note is {outcome}, not routed to one step - nothing here "
                f"names what would change, so the rebuild path stands "
                f"({raw.get('reason') or 'no routing reason recorded'})"),
            "steps": steps,
        }
    if frame is None:
        return {
            "verdict": VERDICT_REBUILD,
            "reason": (
                "note has no placed frame - a patch edits the timeline at "
                "the note's position, and there is none to edit at, so the "
                "rebuild path stands"),
            "steps": steps,
        }
    if len(steps) == 1 and steps[0] in REBUILD_STEPS:
        owns = marker_routing.BY_NODE_ID[steps[0]].owns
        return {
            "verdict": VERDICT_REBUILD,
            "reason": (
                f"routed to {steps[0]}, which owns {owns} - a fix there "
                f"changes the plan's selection or order, so the rebuild "
                f"path stands"),
            "steps": steps,
        }
    if len(steps) == 1 and steps[0] in PATCH_STEPS:
        owns = marker_routing.BY_NODE_ID[steps[0]].owns
        return {
            "verdict": VERDICT_PATCH,
            "reason": (
                f"routed to {steps[0]}, which owns {owns} - a fix there "
                f"stays inside existing items/overlays/captions/markers"),
            "steps": steps,
        }
    return {
        "verdict": VERDICT_REBUILD,
        "reason": (
            f"note names {steps or 'no step'} outside the two tables - a "
            f"patch is claimed only for exactly one known step, so the "
            f"rebuild path stands"),
        "steps": steps,
    }


def _decide_edit_spec(raw: dict, steps: list) -> dict:
    owners = _edit_spec_owners(raw.get("evidence") or {})
    rebuild_owners = [owner for owner in owners if owner not in PATCH_STEPS]
    if owners and not rebuild_owners:
        return {
            "verdict": VERDICT_PATCH,
            "reason": (
                "the typed edit spec's operations all stay inside "
                f"existing items ({', '.join(owners)}) - no operation "
                f"re-chooses the plan's selection or order"),
            "steps": steps,
        }
    return {
        "verdict": VERDICT_REBUILD,
        "reason": (
            "the typed edit spec reaches "
            f"{', '.join(rebuild_owners) if rebuild_owners else 'no known owner'} - "
            f"an operation there changes the plan's selection or order, "
            f"so the rebuild path stands"),
        "steps": steps,
    }


def decide_many(routed_notes) -> list:
    """`(note_id, verdict)` for every routed note, in order."""
    out = []
    for note in routed_notes:
        raw = _as_dict(note)
        verdict = decide(raw)
        out.append({"note_id": raw.get("note_id", ""), **verdict})
    return out


def report(project_folder) -> str:
    """The patch/rebuild verdict for every routed note, printable."""
    routed = marker_routing.route_project(project_folder)
    verdicts = decide_many(routed)
    patches = sum(1 for verdict in verdicts
                  if verdict["verdict"] == VERDICT_PATCH)
    lines = [(f"-- feedback patch plan "
              f"({len(verdicts)} note(s), {patches} patchable) --")]
    if not verdicts:
        lines.append("  no routed notes in this project.")
        return "\n".join(lines)
    for verdict in verdicts:
        lines.append(f"  [{verdict['verdict']}] {verdict['note_id']}")
        lines.append(f"    {verdict['reason']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.feedback_patch",
        description="Patch-in-place or rebuild for each routed feedback note.")
    parser.add_argument("command", choices=("report",),
                        help="report: print the verdict per routed note")
    parser.add_argument("--project", required=True,
                        help="path to the project folder")
    args = parser.parse_args(argv)
    if args.command == "report":
        print(report(args.project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
