"""Cut the captain's APPROVED reels onto their own Resolve timelines.

This is the `build_reels` node of the `reels` process
(`library/processes/reels/dag.json`), not of `edit_video`.  The reason
there are two processes is measured in
`docs/REEL_BUILD_HAS_NO_OWNING_NODE.md`: the edit_video DAG describes the
production of ONE VIDEO FROM FOOTAGE, and a reel is a SECOND product
derived from a master timeline that already exists.  Registering the
build under `render` would have produced a contract that refuses for a
reason that is not true - no `assembly_manifest` - and passes on a
project with not one approved reel in it.

It owns no build logic
----------------------
Every decision here belongs to `library/tools/reel_build.py`: which takes
are redundant, which ranges are kept, where the CTA lands, what goes on
which track.  This body reads the merged input dict, REFUSES what it
cannot do, and calls that module.  That is the same shape every other
`step.py` in this tree has, and it is what keeps `build-reels` and this
node one implementation rather than two (Ruling 1,
`tests/test_operations_add_no_second_implementation.py`).

Captions come from 4.01 and 4.05
--------------------------------
Not from here, and not from a reel-shaped copy of them.
`reel_build.reel_subtitle_segments` drives `subtitles.plan` and
`subtitles.render_segment` through the operation registry, so the caption
path a reel takes is the caption path the master takes.  That is what the
captain's *"mix and match the process to what we need"* means made real:
two processes, one set of steps.

Verification is the NEXT node
-----------------------------
`verify_reels` is a node of this process rather than a tail call here,
because a build that succeeded and a build that conformed are two
different facts and the ledger should be able to carry them separately.
`rebuild_reels_in_project` still verifies by default for anything calling
it directly - this body passes `verify=False` because the process has a
node for it.
"""
from __future__ import annotations


class ReelBuildRefused(RuntimeError):
    """The build cannot start, and this says which input is missing.

    Raised rather than returned so the runner's own failure path records
    it, and so `input_contract` can see that the required inputs this
    step declares are really enforced (AGENTS.md 3, "A declaration must
    be true").
    """


def build_reels(data: dict) -> dict:
    """Build every approved reel in the plan onto its own timeline.

    `data` is the merged input dict - the same thing the runner writes to
    a step's stdin and the same thing `Operation.execute` binds.

    Returns the RECORD of what was placed, which is what the edge to
    `verify_reels` carries: the timelines built, the plan they were built
    from, and the Resolve names they were built into.  Before this the
    record existed only inside `rebuild_reels_in_project` and was written
    to provenance and then discarded, so the verify half had to re-derive
    it - which is how a verifier can grade against a different answer a
    day later (`docs/RULE_EVIDENCE.md`, the caption-hash finding).
    """
    project_folder = (data or {}).get("project_folder") or ""
    if not project_folder:
        raise ReelBuildRefused(
            "build_reels has no project_folder, so there is no project to "
            "read a reel plan from and no Resolve binding to build into.")

    transcript = (data or {}).get("timeline_transcript")
    if not transcript:
        raise ReelBuildRefused(
            "build_reels has no timeline_transcript. NO STEP MAKES ONE - "
            "it is written by `python3 -m library.tools.timeline_transcript "
            "<project> --write`, which needs Resolve open on the project's "
            "own timeline. The reel's keep-ranges, its retake scan and its "
            "captions are all cut from it.")

    from library.tools.reel_build import rebuild_reels_in_project
    from library.tools.timeline_ingest import resolve_binding

    resolve_project_name, master_timeline_name = resolve_binding(
        project_folder)
    if not resolve_project_name or not master_timeline_name:
        raise ReelBuildRefused(
            f"{project_folder}/project.yaml declares no complete `resolve` "
            f"binding (project_name={resolve_project_name!r}, "
            f"timeline_name={master_timeline_name!r}). A reel is cut FROM a "
            f"master timeline, and a near match lands on another project "
            f"(AGENTS.md 5).")

    # WHICH reels, and INTO WHAT. Both are optional and both default to
    # what every build did before: every approved moment, into the plan's
    # own timeline name. They exist because a build that can only write
    # the plan's name can only ever REPLACE what is already in Resolve,
    # and a first build against a project carrying nineteen approved
    # timelines has to be able to place one without touching them.
    #
    # FORWARDED, not interpreted. What a malformed `only_reels` means is
    # `reel_build.reel_numbers`' to say - it is the module that reads
    # the value - and a guard here would make this step refuse without
    # an input its own manifest declares OPTIONAL
    # (`tests/test_input_declarations_are_true.py`, AGENTS.md 3).

    # `verify=False`: this process has a `verify_reels` node, and running
    # the conformance verifier twice would report the same findings twice
    # under two different step ids.
    #
    # `allow_drops` is FORWARDED, not interpreted, for the same reason
    # `only_reels` is: what a malformed declaration means is the
    # replace guard's to say (`reel_replace_guard.parse_specs`), and a
    # guard here would make this step refuse without an input its own
    # manifest declares OPTIONAL. The normalised form lands on the
    # `reel_build` record, which is what the `verify_reels` node
    # promotes off. `supersede` - the reels whose captain sign-off this
    # build may replace (`reel_signoff`) - travels the same way and for
    # the same reason.
    record = rebuild_reels_in_project(
        project_folder,
        skip_captions=bool((data or {}).get("skip_captions")),
        verify=False,
        only=(data or {}).get("only_reels"),
        name_suffix=str((data or {}).get("timeline_name_suffix") or ""),
        allow_drops=(data or {}).get("allow_drops"),
        supersede=(data or {}).get("supersede"),
        accept_editor_changes=(data or {}).get("accept_editor_changes"),
        # `retain` - the reels whose superseded generation the
        # promotion may retire rather than delete
        # (`reel_retirement`) - FORWARDED, not interpreted, for the
        # reason `only_reels` is. Absent is the default: one timeline
        # per reel and an empty archive.
        retain=(data or {}).get("retain"),
        # A reel nothing changed about is not placed again
        # (`library/tools/reel_rebuild_need.py`). FORWARDED, not
        # interpreted, for the reason `only_reels` is: `rebuild_all`
        # arriving True is the operator saying "place every reel you
        # named whatever the state says", which is how the placement
        # itself is measured and how a re-place onto drift-free state
        # is asked for. Absent is the default, and the default is to
        # skip what needs no pass.
        reuse_unchanged=not bool((data or {}).get("rebuild_all")))

    return {"reel_build": record}


def ask_reels(data: dict) -> dict:
    """Write every approved reel's three visual asks, without building.

    The `reel.ask` operation. Reads the merged input dict - the same
    thing the runner writes to a step's stdin and the same thing
    `Operation.execute` binds - REFUSES what it cannot do, and calls
    `library.tools.reel_build.write_reel_asks_for_project`. That is the
    same shape `build_reels` above has: this body owns no ask logic,
    and the pass-1 build stays the one implementation rather than
    growing a second beside it (Ruling 1,
    `tests/test_operations_add_no_second_implementation.py`).

    Returns the RECORD of what was asked, per reel: the three
    `llm_requests/*.json` paths the model answers beside. The answers
    themselves stay with the model - this moves the QUESTION off the
    build, never the answer.
    """
    project_folder = (data or {}).get("project_folder") or ""
    if not project_folder:
        raise ReelBuildRefused(
            "ask_reels has no project_folder, so there is no project to "
            "read a reel plan from and no Resolve binding to read the "
            "master timeline through.")

    transcript = (data or {}).get("timeline_transcript")
    if not transcript:
        raise ReelBuildRefused(
            "ask_reels has no timeline_transcript. NO STEP MAKES ONE - "
            "it is written by `python3 -m library.tools.timeline_transcript "
            "<project> --write`, which needs Resolve open on the project's "
            "own timeline. The reel's keep-ranges, its retake scan and its "
            "motion spine are all cut from it.")

    from library.tools.reel_build import write_reel_asks_for_project
    from library.tools.timeline_ingest import resolve_binding

    resolve_project_name, master_timeline_name = resolve_binding(
        project_folder)
    if not resolve_project_name or not master_timeline_name:
        raise ReelBuildRefused(
            f"{project_folder}/project.yaml declares no complete `resolve` "
            f"binding (project_name={resolve_project_name!r}, "
            f"timeline_name={master_timeline_name!r}). A reel is cut FROM a "
            f"master timeline, and a near match lands on another project "
            f"(AGENTS.md 5).")

    # FORWARDED, not interpreted, for the reason `build_reels` forwards
    # `only_reels`: what a malformed value means is
    # `reel_build.reel_numbers`' to say.
    record = write_reel_asks_for_project(
        project_folder,
        transcript,
        only=(data or {}).get("only_reels"),
        name_suffix=str((data or {}).get("timeline_name_suffix") or ""))

    return {"reel_ask": record}


def _require_single_kind_spec(project_folder: str, spec: dict,
                               kind: str) -> dict:
    """The caller-supplied contract one in-place operation speaks.

    `reel.entry_motion` animates and `reel.set_properties` writes
    properties - one body each, in the shape `touch_reel` below has.
    A spec naming another kind through this body is a caller that
    addressed the wrong operation, so it raises rather than running
    as the other one: two names for one behaviour would be the
    second implementation Ruling 1 forbids
    (`tests/test_operations_add_no_second_implementation.py`).
    """
    if not project_folder:
        raise ValueError(
            f"{kind} needs a project_folder - the reel plan it "
            f"resolves the reel number through lives there, and there "
            f"is nothing to infer it from")
    if not isinstance(spec, dict):
        raise TypeError(
            f"{kind} needs a spec mapping (got {type(spec).__name__!r}) - "
            f"the change stated structurally: which reel, which item, "
            f"what changes")
    edits = spec.get("edits")
    if not edits:
        raise ValueError(
            f"{kind} needs a spec naming at least one edit - a "
            f"touchup with nothing to do is a caller that failed to say "
            f"what it wants")
    intruders = sorted({str(edit.get("op")) for edit in edits
                        if isinstance(edit, dict)
                        and str(edit.get("op") or "") != kind})
    if intruders:
        raise ValueError(
            f"{kind} runs {kind} edits, not {intruders} - address "
            f"those through the operation that owns them "
            f"(`reel.touchup` runs any edit kinds together)")
    return dict(spec)


def animate_entry(project_folder: str, spec: dict) -> dict:
    """Animate a placed overlay element in (and out), in place.

    The `reel.entry_motion` operation. Its arguments come from a
    CALLER, not from gathering - which reel, which item, how many
    frames of entrance and exit fade - so the runner never drives it
    (see `Operation.caller_supplied`). Reads nothing but its
    arguments, REFUSES what is malformed, and calls
    `library.tools.reel_touchup.apply_touchup`. That is the same shape
    `touch_reel` below has: this body owns no entry-motion logic,
    and the composed-edit path stays one implementation rather than
    growing a second beside it (Ruling 1,
    `tests/test_operations_add_no_second_implementation.py`).

    Malformed input RAISES: no project folder or a spec naming no
    edits (`ValueError`), a spec that is not a mapping at all
    (`TypeError`), or an edit of another kind (`ValueError`).
    Anything Resolve-side raises the tool's own `TouchupRefused` /
    `TouchupError` unwrapped, the way `manage_project.py touch-reel`
    reports them.

    Returns the RECORD of what was touched under `reel_touchup`: the
    receipt `apply_touchup` wrote, carrying the gate class, the cost
    statement and the verification read.
    """
    from library.tools import reel_touchup

    receipt = reel_touchup.apply_touchup(
        project_folder,
        _require_single_kind_spec(project_folder, spec, "entry_motion"))
    return {"reel_touchup": receipt}


def set_clip_properties(project_folder: str, spec: dict) -> dict:
    """Write properties onto an already-placed clip, in place.

    The `reel.set_properties` operation. Its arguments come from a
    CALLER, not from gathering - which reel, which item, which
    properties - so the runner never drives it (see
    `Operation.caller_supplied`). Reads nothing but its arguments,
    REFUSES what is malformed, and calls
    `library.tools.reel_touchup.apply_touchup`. That is the same shape
    `touch_reel` below has: this body owns no property logic
    (`composed_edit.set_properties` writes, with read-back), and the
    composed-edit path stays one implementation rather than growing
    a second beside it (Ruling 1,
    `tests/test_operations_add_no_second_implementation.py`).

    Malformed input RAISES like `animate_entry` above, and anything
    Resolve-side raises the tool's own `TouchupRefused` /
    `TouchupError` unwrapped.

    Returns the RECORD of what was touched under `reel_touchup`: the
    receipt `apply_touchup` wrote, carrying the gate class, the cost
    statement and the verification read.
    """
    from library.tools import reel_touchup

    receipt = reel_touchup.apply_touchup(
        project_folder,
        _require_single_kind_spec(project_folder, spec,
                                  "set_properties"))
    return {"reel_touchup": receipt}


def touch_reel(project_folder: str, spec: dict) -> dict:
    """Apply a structured change to one built reel's existing timeline.

    The `reel.touchup` operation. Its arguments come from a CALLER,
    not from gathering - which reel, which item, what changes, stated
    structurally - so the runner never drives it (see
    `Operation.caller_supplied`). Reads nothing but its arguments,
    REFUSES what is malformed, and calls
    `library.tools.reel_touchup.apply_touchup`. That is the same shape
    `build_reels` and `ask_reels` above have: this body owns no
    touchup logic, and the composed-edit path stays one
    implementation rather than growing a second beside it (Ruling 1,
    `tests/test_operations_add_no_second_implementation.py`).

    Malformed input RAISES: no project folder or a spec naming no
    edits (`ValueError`), or a spec that is not a mapping at all
    (`TypeError`). Anything Resolve-side - no such timeline, an
    unclassifiable change, a mid-flight failure - raises the tool's
    own `TouchupRefused` / `TouchupError` unwrapped, the way
    `manage_project.py touch-reel` reports them.

    Returns the RECORD of what was touched under `reel_touchup`: the
    receipt `apply_touchup` wrote, carrying the gate class, the cost
    statement and the verification read.
    """
    from library.tools import reel_touchup

    if not project_folder:
        raise ValueError(
            "touch_reel needs a project_folder - the reel plan it "
            "resolves the reel number through lives there, and there "
            "is nothing to infer it from")
    if not isinstance(spec, dict):
        raise TypeError(
            f"touch_reel needs a spec mapping (got {type(spec).__name__!r}) - "
            f"the change stated structurally: which reel, which item, "
            f"what changes")
    if not spec.get("edits"):
        raise ValueError(
            "touch_reel needs a spec naming at least one edit - a "
            "touchup with nothing to do is a caller that failed to say "
            "what it wants")

    receipt = reel_touchup.apply_touchup(project_folder, dict(spec))
    return {"reel_touchup": receipt}


def main():
    import json
    import sys

    data = json.loads(sys.stdin.read())
    try:
        json.dump(build_reels(data), sys.stdout, indent=2)
    except ReelBuildRefused as refused:
        print(f"build_reels REFUSED: {refused}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
