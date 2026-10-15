#!/usr/bin/env python3
"""
Step 3.2 Bridge: Resolve B-Roll Creative Selections to Execution Data

Takes the LLM's creative B-roll selections (clip_id + preferred_moment +
rationale) and resolves them to execution-ready data using the temporal
event index for precision placement:
  - clip_id → source_file (from catalog)
  - preferred_moment → video_in/video_out, resolved against what the
    vision pass OBSERVED in the picture - see
    `library/tools/cutaway_window.py`.  A cutaway plays `video_only`, so
    its audio is never heard and may never choose its window.
  - Resolution comparison → needs_conform flag

Classification: Deterministic / Data Transformation
Idempotent: Yes

Input:  {
    "broll_creative": <LLM output from 3.2>,
    "clip_catalog": [{ clip_id, path, ... }],
    "semantic_analysis_documents": [{ clip_id, blocks, ... }],
    "temporal_event_indices": [{ clip_id, scene_boundaries, energy_curve, ... }],
    "timed_spine": <output from 2.6 with timeline_start/end>
}
Output: {
    "b_roll_assignments": [{ spine_block_position, source_file, video_in, video_out, ... }]
}
"""
import json
import os
import sys

from library.tools.semantic_index import build_semantic_lookup
from library.tools import asset_transformations
from library.tools.asset_transformations import check_needs_conform
from library.tools.delivery_format import resolve_delivery_format
from library.tools.cutaway_window import choose_window
from library.tools.spine_contract import declares_black_beat
from library.tools.plan_keys import refuse_unknown_keys
from library.tools.transition_carriers import block_reaches_v1
from library.tools import decided_value

# The B-roll-density slot this step decides, and the decision-entry keys
# the model's answer may carry.
BROLL_SLOT = "broll.density_per_minute"
DECISION_ENTRY_KEYS = frozenset({"slot", "scope", "value", "why"})

# The entry keys this step reads, per plan list. Anything else on an
# entry is REFUSED by `refuse_unknown_keys` in `resolve_broll`, never
# dropped: an unread key is how a probe's SFX `at_word` landed 3.06 s
# early on the block start.
BROLL_ENTRY_KEYS = frozenset({
    "clip_id",
    "spine_block_position",
    "preferred_moment",
    "selection_rationale",
    # Rung 7 (CT3.3): a stated source slip - "slip the shot 1s later
    # in its source" - shifting the chosen window at the same timeline
    # position and duration. Seconds when the request states seconds,
    # frames when it states frames (E3); both stated must agree. A slip
    # past the file's ends refuses rather than clamping onto silence.
    "slip_seconds",
    "slip_frames",
    # FI-18 is optional per selection. Keeping it nested means an absent
    # proposal is a valid empty default, not a missing model answer.
    "audio_preserving_replacement",
})
INTERJECTION_ENTRY_KEYS = frozenset({
    "clip_id",
    "over_spine_block_position",
    "preferred_moment",
    "selection_rationale",
    "purpose",
    "timeline_start",
    "timeline_end",
})

def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")


# `check_needs_conform` lives in `library.tools.asset_transformations`
# (imported above): the routed B-roll substitute carries the same
# measurement, so the comparison exists once. It is None-safe where
# this local copy was not - a clip with no recorded rotation conforms
# by resolution alone instead of raising in `abs`.


def find_best_segment(
    preferred_moment: str,
    clip_analysis: dict,
    temporal_index: dict,
    clip_duration: float,
    target_duration: float,
) -> tuple:
    """The (video_in, video_out) of a cutaway, chosen from the PICTURE.

    Thin wrapper over `library.tools.cutaway_window.choose_window`, kept
    because callers only want the pair.  `resolve_broll` calls the chooser
    directly so it can record WHAT chose the window.

    This function used to hold three strategies of its own, and the one
    that ran centred the window on the clip's **audio energy peak** - on
    a clip placed `video_only: True`, whose audio is never heard.
    """
    return choose_window(
        preferred_moment, clip_analysis, temporal_index,
        clip_duration, target_duration,
    ).as_tuple()


# A cutaway shorter than this is a flicker, not a shot. An interjection
# that cannot be placed in a free window at least this long is dropped
# rather than allowed to swallow a block's B-roll assignment.
MIN_INTERJECTION_SECONDS = 0.5


def _window_key(source_file: str, video_in: float, video_out: float) -> tuple:
    """The identity of one placed source window, at the plan's rounding.

    Two placements naming the same file and the same rounded seconds play
    the same shot twice - a redundant insert (F05), not two cutaways.
    """
    return (source_file or "", round(float(video_in), 3),
            round(float(video_out), 3))


def _refuse_reused_window(window_key: tuple, where: str,
                          first_where: str) -> None:
    """Refuse a source window a cutaway already plays (F05).

    Source diversity and repeated-shot constraints are mechanical: the
    same seconds of the same file on two timeline spans is the same shot
    replayed, whichever block each covers. The fix is a different moment
    or a different clip, so the refusal names both placements and the
    window, and travels the post-bridge retry path back to the model.
    """
    raise ValueError(
        f"B-roll {where} replays the same source window as "
        f"{first_where} ({window_key[0]} "
        f"{window_key[1]:.3f}-{window_key[2]:.3f}s) - a repeated shot. "
        f"Pick a different moment or a different clip."
    )


def _selection_evidence(spine_pos, block_type: str, block_start: float,
                       block_end: float, placed_start: float,
                       placed_end: float, shortfall: float,
                       rationale: str, preferred_moment: str,
                       search_status: str, basis: str,
                       basis_detail: str) -> dict:
    """The receipt joining a cutaway to the slot it covers (F05).

    A selection is evidence-explained when one record names the relation
    the model stated (rationale / preferred moment), the evidence that
    vouched for the clip (search status) and chose its seconds (window
    basis), and the time coverage the placement actually carries (the
    block's span beside the placed span, with the shortfall). An entry
    that states neither a rationale nor a moment is recorded as
    `unreasoned` - said aloud, not silently carried.
    """
    reasoned = bool((rationale or "").strip()
                    or (preferred_moment or "").strip())
    return {
        "spine_block_position": spine_pos,
        "block_type": block_type,
        "relation": "reasoned" if reasoned else "unreasoned",
        "selection_rationale": rationale,
        "preferred_moment": preferred_moment,
        "search_status": search_status,
        "window_basis": basis,
        "window_basis_detail": basis_detail,
        "block_timeline_start": block_start,
        "block_timeline_end": block_end,
        "placed_timeline_start": placed_start,
        "placed_timeline_end": placed_end,
        "coverage_shortfall_seconds": shortfall,
    }


def _resolve_slip(broll: dict, spine_pos, clip_id: str,
                  timed_spine: dict) -> float:
    """The stated source slip in seconds, or 0.0.

    E3: `slip_seconds` when the request states seconds, `slip_frames`
    when it states frames; both stated must agree past half a frame.
    Frames read the timed spine's own timebase - stated frames with no
    timebase refuse, never guess one. Positive slips later into the
    source ("1s later in its source"); negative runs earlier.
    """
    seconds = broll.get("slip_seconds")
    frames = broll.get("slip_frames")
    if seconds is not None and (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))):
        raise ValueError(
            f"B-roll for block {spine_pos} ({clip_id}) states "
            f"slip_seconds {seconds!r}, which is not a number of "
            f"seconds")
    if frames is not None and (
            isinstance(frames, bool) or not isinstance(frames, int)):
        raise ValueError(
            f"B-roll for block {spine_pos} ({clip_id}) states "
            f"slip_frames {frames!r}, which is not a whole number of "
            f"frames")
    if frames is not None:
        fps = (timed_spine or {}).get("frame_rate")
        if not fps:
            raise ValueError(
                f"B-roll for block {spine_pos} ({clip_id}) states a "
                f"slip in frames but the timed spine carries no "
                f"frame_rate - frames have no grid without the timebase")
        from_frames = frames / float(fps)
        if seconds is not None and abs(from_frames - float(seconds)) > (
                0.5 / float(fps) + 1e-9):
            raise ValueError(
                f"B-roll for block {spine_pos} ({clip_id}) states "
                f"slip_seconds {float(seconds):.3f}s and slip_frames "
                f"{frames} ({from_frames:.3f}s) - two numbers for one "
                f"slip is an ambiguous spec")
        return from_frames
    if seconds is not None:
        return float(seconds)
    return 0.0


def _free_windows(start: float, end: float, occupied: list) -> list:
    """Sub-intervals of [start, end) not covered by any occupied range."""
    windows = [(start, end)]
    for occ_start, occ_end in sorted(occupied):
        remaining = []
        for w_start, w_end in windows:
            if occ_end <= w_start or occ_start >= w_end:
                remaining.append((w_start, w_end))
                continue
            if occ_start > w_start:
                remaining.append((w_start, occ_start))
            if occ_end < w_end:
                remaining.append((occ_end, w_end))
        windows = remaining
    return windows


def _place_without_overlap(start: float, end: float, occupied: list):
    """Largest free sub-window of the requested range, or None.

    V2 can only show one clip at a time. An interjection whose LLM-chosen
    range swallows a block's B-roll assignment used to survive to
    compilation, where overlap resolution silently deleted the assignment.
    Trimming the interjection here keeps both clips.
    """
    windows = [
        w for w in _free_windows(start, end, occupied)
        if w[1] - w[0] >= MIN_INTERJECTION_SECONDS
    ]
    if not windows:
        return None
    return max(windows, key=lambda w: w[1] - w[0])


def _search_provenance(clip_id: str, preferred_moment: str,
                       rationale: str, spine_pos,
                       clip_catalog: list,
                       project_folder: str) -> dict:
    """Which search result and evidence a chosen clip came from.

    Queries the index with the slot's own intent (`preferred_moment`,
    else the rationale) as a visual slot. `search_verified` only when
    the index vouches for THIS clip with a verified span; a vouched
    DIFFERENT clip is recorded as the alternative the model did not
    take; anything else is the catalog path said aloud. Never raises:
    provenance is a receipt, not a gate, and the model's choice stands
    on every path.
    """
    from library.tools import search_in_planning as sip

    blank = {"search_status": "catalog_fallback:no_intent",
             "search_evidence": None}
    intent = (preferred_moment or rationale or "").strip()
    if not intent or not project_folder:
        return blank
    try:
        result = sip.slot_search(project_folder, intent,
                                 slot_kind="visual",
                                 catalog=clip_catalog)
    except Exception as exc:  # noqa: BLE001 - receipt, never gate
        return {"search_status": f"catalog_fallback:search_error:{exc}",
                "search_evidence": None}
    status = result.get("status")
    if status == "filled" and result.get("clip_id") == clip_id:
        return {"search_status": "search_verified",
                "search_evidence": result.get("evidence")}
    if status == "filled":
        return {"search_status":
                    f"search_other:{result.get('clip_id')}",
                "search_evidence": result.get("evidence")}
    return {"search_status": f"catalog_fallback:{status}",
            "search_evidence": None}


def resolve_broll(
    broll_creative: list,
    broll_interjections: list,
    clip_catalog: list,
    semantic_docs: list,
    temporal_indices: list,
    timed_spine: dict,
    target_resolution: tuple,
    occupied: list = None,
    project_folder: str = "",
) -> dict:
    """Resolve B-roll creative selections to execution data.

    The target frame is REQUIRED - the delivery format the caller
    resolved (`resolve_delivery_format`), never a shape literal here.

    `occupied` is V2 already claimed by cutaways this call is NOT
    resolving - `(timeline_start, timeline_end)` pairs - so a region
    re-plan places its interjections around the ones it keeps.
    """
    # Entry keys nothing here reads are refused before anything
    # resolves - the refusal travels the post-bridge retry path so the
    # model re-plans instead of the cutaway landing somewhere unasked.
    refuse_unknown_keys(broll_creative, BROLL_ENTRY_KEYS,
                         step="select_broll", plan="broll_creative")
    refuse_unknown_keys(broll_interjections, INTERJECTION_ENTRY_KEYS,
                         step="select_broll", plan="b_roll_interjections")

    catalog_lookup = {c["clip_id"]: c for c in clip_catalog}
    analysis_lookup = build_semantic_lookup(semantic_docs, clip_catalog)
    index_lookup = {i["clip_id"]: i for i in temporal_indices}

    # Build spine block lookup for timeline positions
    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))
    block_lookup = {str(b["position"]): b for b in spine_blocks if "position" in b}

    assignments = []
    covered_positions = set()

    # Every source window this call places, to its first placement. A
    # second placement of the same window is a repeated shot (F05) and
    # refuses below, in assignments and interjections alike.
    placed_windows = {}

    for broll in broll_creative:
        clip_id = broll["clip_id"]
        spine_pos = broll["spine_block_position"]
        preferred_moment = broll.get("preferred_moment", "")
        rationale = broll.get("selection_rationale", "")

        clip = catalog_lookup.get(clip_id)
        if not clip:
            print(
                f"WARNING: clip_id '{clip_id}' not in catalog, skipping",
                file=sys.stderr,
            )
            continue

        spine_block = block_lookup.get(str(spine_pos))
        if spine_block is None:
            print(
                f"WARNING: B-roll targets spine block {spine_pos!r}, which "
                f"does not exist - skipping",
                file=sys.stderr,
            )
            continue

        # One cutaway per block. A second selection takes its timeline
        # range from the same spine block, so both would claim the same
        # stretch of V2 - the collapse compile_manifest refuses. First
        # selection that resolves wins.
        if str(spine_pos) in covered_positions:
            print(
                f"  Dropped duplicate B-roll on block {spine_pos!r} "
                f"({clip_id}) - the block already has a cutaway",
                file=sys.stderr,
            )
            continue

        # B-roll must show something OTHER than the A-roll it covers.
        # Cutting to the same source clip is not a cutaway; on screen it
        # reads as a glitch in the same shot. The selection is dropped
        # and the A-roll picture plays - which picture replaces it is a
        # creative outcome, and settling it by catalogue order
        # (`sorted()` over the clip ids) decided what the viewer sees by
        # alphabet. Nothing is substituted for a clip nothing chose
        # (AGENTS.md 10.5): the model re-plans the slot, or the block
        # simply has no cutaway.
        covered_clip = spine_block["clip_id"]
        if covered_clip and clip_id == covered_clip:
            print(
                f"WARNING: B-roll for block {spine_pos} selected "
                f"{clip_id}, the same clip as its A-roll - skipping. "
                f"The A-roll picture plays; no alternative clip is "
                f"substituted.",
                file=sys.stderr,
            )
            continue

        timeline_start = spine_block["timeline_start"]
        timeline_end = spine_block["timeline_end"]
        block_duration = timeline_end - timeline_start

        clip_analysis = analysis_lookup.get(clip_id, {})
        clip_index = index_lookup.get(clip_id, {})
        clip_duration = clip.get("duration_seconds", 10.0)

        # Resolve the preferred moment to video in/out from the picture.
        choice = choose_window(
            preferred_moment,
            clip_analysis,
            clip_index,
            clip_duration,
            block_duration,
        )
        video_in, video_out = choice.as_tuple()

        # A stated source slip shifts the chosen window at the same
        # timeline position and duration (rung 7, CT3.3). The slip is
        # the requester's number - seconds or frames, never invented -
        # and a window it pushes past the file's ends refuses with the
        # bounds, rather than clamping onto unplayed media.
        slip = _resolve_slip(broll, spine_pos, clip_id, timed_spine)
        if slip:
            shifted_in = video_in + slip
            shifted_out = video_out + slip
            if shifted_in < -1e-9 or shifted_out > clip_duration + 1e-9:
                raise ValueError(
                    f"B-roll for block {spine_pos} ({clip_id}) states a "
                    f"slip of {slip:.3f}s, moving the chosen window "
                    f"({video_in:.3f}-{video_out:.3f}s) to "
                    f"({shifted_in:.3f}-{shifted_out:.3f}s) - outside "
                    f"the file's 0-{clip_duration:.3f}s. Slip inside "
                    f"the media, or pick another moment."
                )
            video_in, video_out = shifted_in, shifted_out

        # A repeated shot refuses here, naming both placements (F05).
        # The same seconds of the same file on two timeline spans is one
        # shot replayed, not two cutaways - whichever blocks they cover.
        source_file = clip.get("source_file") or clip.get("path")
        window_key = _window_key(source_file, video_in, video_out)
        if window_key in placed_windows:
            _refuse_reused_window(
                window_key, f"for block {spine_pos} ({clip_id})",
                placed_windows[window_key])
        placed_windows[window_key] = f"block {spine_pos} ({clip_id})"

        # A cutaway can be shorter than the block it covers - but only
        # where something plays underneath. A speech, hook or picture
        # block (or a bookend card) puts a clip on V1 - `block_reaches_v1`, the one
        # statement of V1 membership compile_manifest builds its V1 track
        # from, so the two cannot drift - and returning to A-roll early is
        # safe there. The shortfall is DECLARED on the assignment as
        # `coverage_shortfall_seconds`: the stderr line below is gone by
        # the time anything downstream reads the plan, and
        # compile_manifest's undeclared-black check must not be the first
        # thing that notices a shortened cutaway.
        #
        # A transition_slot, intro or outro block puts NOTHING on V1 - the
        # cutaway is the only picture. Shortening it there leaves a hole no
        # A-roll fills, which used to fail a whole stage later at
        # compile_manifest with the evidence about WHY gone. That case
        # REFUSES here, naming the clip and the shortfall, so the
        # post-bridge rejection reaches the model that chose it
        # (`library/tools/post_bridge_retry.py`) instead of arriving as
        # black frames a stage later.
        #
        # The line is V1 membership, not a duration threshold: a 0.2s
        # shortfall on a transition_slot is a hole, and a 2.8s shortfall on
        # a speech block is an early return to A-roll. No tolerance number
        # is invented here - the 0.001s epsilon below is the pre-existing
        # float-noise guard between independently rounded durations, not a
        # judgement about how much black is acceptable.
        available = round(video_out - video_in, 3)
        shortfall = max(0.0, round(block_duration - available, 3))
        if available < block_duration - 0.001:
            if not block_reaches_v1(spine_block):
                raise ValueError(
                    f"B-roll for block {spine_pos} "
                    f"({spine_block.get('block_type', '?')}) cannot be placed: "
                    f"{clip_id} can only supply {available:.3f}s of the "
                    f"{block_duration:.3f}s slot (shortfall {shortfall:.3f}s, "
                    f"{choice.basis}: {choice.basis_detail}) - and a "
                    f"{spine_block.get('block_type', '?')} block puts no clip "
                    f"on V1, so shortening the cutaway would leave "
                    f"{shortfall:.3f}s of black with nothing underneath. "
                    f"Pick a clip that covers the slot."
                )
            print(
                f"  Block {spine_pos}: {clip_id} can only supply "
                f"{available:.3f}s of the {block_duration:.3f}s slot - "
                f"shortening the cutaway",
                file=sys.stderr,
            )
            timeline_end = round(timeline_start + available, 3)

        # Check if clip needs resolution conform
        needs_conform = check_needs_conform(clip, target_resolution[0], target_resolution[1])

        search_provenance = _search_provenance(
            clip_id, preferred_moment, rationale, spine_pos,
            clip_catalog, project_folder)

        assignments.append({
            "spine_block_position": spine_pos,
            "block_type": spine_block.get("block_type", "transition_slot"),
            "clip_id": clip_id,
            "source_file": clip.get("source_file") or clip.get("path"),
            "video_in": video_in,
            "video_out": video_out,
            "duration_seconds": round(video_out - video_in, 3),
            "timeline_start": timeline_start,
            "timeline_end": timeline_end,
            # Which search result and evidence this clip came from, or
            # the catalog path said aloud. Same shape as `window_basis`:
            # a receipt beside the decision, not a second decision.
            "search_status": search_provenance["search_status"],
            "search_evidence": search_provenance["search_evidence"],
            # The evidence joining this cutaway to its slot (F05): the
            # stated relation, what vouched for the clip and chose its
            # seconds, and the block's span beside the placed span.
            "selection_evidence": _selection_evidence(
                spine_pos, spine_block.get("block_type",
                                           "transition_slot"),
                spine_block["timeline_start"], spine_block["timeline_end"],
                timeline_start, timeline_end, shortfall, rationale,
                preferred_moment, search_provenance["search_status"],
                choice.basis, choice.basis_detail),
            # Seconds of the slot the cutaway does not cover, with the
            # A-roll picture playing underneath. 0.0 is full cover. The
            # shortening used to be announced on stderr only, which nothing
            # downstream can read - compile_manifest's undeclared-black
            # check must not be the first thing that notices.
            "coverage_shortfall_seconds": shortfall,
            "needs_conform": needs_conform,
            "selection_rationale": rationale,
            # What chose these seconds, recorded beside them. A window
            # picked on `undiscriminated` is the absence of a decision,
            # not a decision, and a reviewer must be able to tell.
            "window_basis": choice.basis,
            "window_basis_detail": choice.basis_detail,
            # The stated slip this window carries (0.0 unstated) - the
            # E3 receipt beside the shifted seconds above.
            "slip_seconds": round(slip, 3),
            "video_only": True,  # B-roll audio should NOT be linked
        })
        covered_positions.add(str(spine_pos))

    resolved_interjections = []
    # Everything already claiming a stretch of V2. Interjections are placed
    # around it, never over it.
    occupied = list(occupied or []) + [
        (a["timeline_start"], a["timeline_end"]) for a in assignments]
    for interj in broll_interjections:
        clip_id = interj.get("clip_id")
        if not clip_id:
            continue
        spine_pos = interj.get("over_spine_block_position")
        preferred_moment = interj.get("preferred_moment", "")
        rationale = interj.get("selection_rationale", "")
        purpose = interj.get("purpose", "")

        clip = catalog_lookup.get(clip_id)
        if not clip:
            print(f"WARNING: interjection clip_id '{clip_id}' not in catalog, skipping", file=sys.stderr)
            continue

        spine_block = block_lookup.get(str(spine_pos), {})
        covered_clip = spine_block.get("clip_id")
        if covered_clip and clip_id == covered_clip:
            print(
                f"WARNING: interjection over block {spine_pos} selected "
                f"{clip_id}, the same clip as its A-roll - skipping. "
                f"No alternative clip is substituted.",
                file=sys.stderr,
            )
            continue

        default_tl_start = spine_block.get("timeline_start", 0.0)

        requested_start = interj.get("timeline_start", default_tl_start)
        requested_end = interj.get("timeline_end", requested_start + 2.0)

        placement = _place_without_overlap(
            requested_start, requested_end, occupied,
        )
        if placement is None:
            print(
                f"WARNING: interjection over block {spine_pos} "
                f"({requested_start:.3f}-{requested_end:.3f}s) is fully "
                f"covered by B-roll already placed on V2 and leaves no "
                f"free window of {MIN_INTERJECTION_SECONDS}s - dropping it "
                f"rather than displacing an assignment",
                file=sys.stderr,
            )
            continue

        timeline_start, timeline_end = (
            round(placement[0], 3), round(placement[1], 3),
        )
        if (timeline_start, timeline_end) != (requested_start, requested_end):
            print(
                f"  Interjection over block {spine_pos}: trimmed "
                f"{requested_start:.3f}-{requested_end:.3f}s to "
                f"{timeline_start:.3f}-{timeline_end:.3f}s so the B-roll "
                f"it overlapped survives",
                file=sys.stderr,
            )
        block_duration = timeline_end - timeline_start

        clip_analysis = analysis_lookup.get(clip_id, {})
        clip_index = index_lookup.get(clip_id, {})
        clip_duration = clip.get("duration_seconds", 10.0)

        choice = choose_window(
            preferred_moment, clip_analysis, clip_index, clip_duration,
            block_duration,
        )
        video_in, video_out = choice.as_tuple()

        # A repeated shot refuses here too (F05) - an interjection
        # replaying an assignment's (or another interjection's) window
        # is the same redundancy on a free stretch of V2.
        interjection_source = clip.get("source_file") or clip.get("path")
        interjection_key = _window_key(
            interjection_source, video_in, video_out)
        if interjection_key in placed_windows:
            _refuse_reused_window(
                interjection_key,
                f"interjection over block {spine_pos} ({clip_id})",
                placed_windows[interjection_key])
        placed_windows[interjection_key] = (
            f"interjection over block {spine_pos} ({clip_id})")

        # Same invariant the assignment path enforces: a cutaway may end
        # early, but it must never claim more timeline than its source can
        # fill, or V2 freezes mid-cutaway. An interjection makes no coverage
        # promise - it is a visual break placed in a free V2 window, and
        # which picture owns the stretch underneath is the assignment/V1
        # layer's answer, still guarded by compile_manifest - so the
        # shortfall is DECLARED on the clip rather than refused here.
        available = round(video_out - video_in, 3)
        interjection_shortfall = max(0.0, round(block_duration - available, 3))
        if available < block_duration - 0.001:
            print(
                f"  Interjection over block {spine_pos}: {clip_id} can only "
                f"supply {available:.3f}s of the {block_duration:.3f}s slot "
                f"- shortening the cutaway",
                file=sys.stderr,
            )
            timeline_end = round(timeline_start + available, 3)
            if timeline_end - timeline_start < MIN_INTERJECTION_SECONDS:
                print(
                    f"WARNING: interjection over block {spine_pos} would be "
                    f"only {timeline_end - timeline_start:.3f}s of usable "
                    f"source - dropping it",
                    file=sys.stderr,
                )
                continue

        occupied.append((timeline_start, timeline_end))

        needs_conform = check_needs_conform(clip, target_resolution[0], target_resolution[1])

        interjection_provenance = _search_provenance(
            clip_id, preferred_moment, rationale or purpose, spine_pos,
            clip_catalog, project_folder)

        resolved_interjections.append({
            "over_spine_block_position": spine_pos,
            "timeline_start": timeline_start,
            "timeline_end": timeline_end,
            "purpose": purpose,
            "assigned_clip": {
                "clip_id": clip_id,
                "source_file": clip.get("source_file") or clip.get("path"),
                "video_in": video_in,
                "video_out": video_out,
                "duration_seconds": round(video_out - video_in, 3),
                "search_status":
                    interjection_provenance["search_status"],
                "search_evidence":
                    interjection_provenance["search_evidence"],
                # The evidence joining this cutaway to its slot (F05):
                # the requested span beside the placed span, with the
                # same stated-relation and window-basis receipt an
                # assignment carries.
                "selection_evidence": _selection_evidence(
                    spine_pos, "interjection", requested_start,
                    requested_end, timeline_start, timeline_end,
                    interjection_shortfall, rationale or purpose,
                    preferred_moment,
                    interjection_provenance["search_status"],
                    choice.basis, choice.basis_detail),
                # Seconds of the placed window the cutaway does not cover.
                # 0.0 is full cover. Same declaration the assignment path
                # carries, for the same reason: a shortening nothing
                # downstream can read is a hole found a stage later.
                "coverage_shortfall_seconds": interjection_shortfall,
                "needs_conform": needs_conform,
                "selection_rationale": rationale,
                "window_basis": choice.basis,
                "window_basis_detail": choice.basis_detail,
                "video_only": True,
            }
        })

    # Timeline order, so a region splice (`splice_region_broll`) - which
    # merges in timeline order - leaves the entries it keeps in the order
    # they were stored. The sort is stable.
    assignments.sort(key=lambda a: a["timeline_start"])
    resolved_interjections.sort(key=lambda i: i["timeline_start"])
    return {
        "b_roll_assignments": assignments,
        "b_roll_interjections": resolved_interjections,
    }


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    _require_keys(data, ["clip_catalog", "semantic_analysis_documents"], "Input data")
    if "timed_spine" in data and not isinstance(data["timed_spine"], dict):
        raise ValueError("timed_spine must be a dictionary")

    broll_creative = data.get("broll_creative", [])
    if not isinstance(broll_creative, list):
        broll_creative = []

    for broll in broll_creative:
        if not isinstance(broll, dict):
            raise ValueError("Items in broll_creative must be dictionaries")
        _require_keys(broll, ["clip_id", "spine_block_position"], "broll_creative item")

    clip_catalog = data.get("clip_catalog", [])
    semantic_docs = data.get("semantic_analysis_documents", [])
    temporal_raw = data.get("temporal_event_indices", [])
    temporal_indices = temporal_raw.get("temporal_event_indices", temporal_raw) if isinstance(temporal_raw, dict) else temporal_raw
    timed_spine = data.get("timed_spine", {})
    
    # The frame the product ships in, not the frame the footage arrived
    # in. See library/tools/delivery_format.py.
    target_width, target_height = resolve_delivery_format(
        data.get("project_folder"))

    interjections = data.get("b_roll_interjections", [])

    result = resolve_broll(
        broll_creative, interjections, clip_catalog, semantic_docs,
        temporal_indices, timed_spine,
        target_resolution=(target_width, target_height),
        project_folder=data.get("project_folder", ""),
    )

    # FI-18 proposals pass through as explicit editorial decisions. The
    # compile manifest owns the final span match and refuses missing
    # synchronization, bad-picture or continuity evidence.
    result["audio_preserving_replacements"] = [
        item["audio_preserving_replacement"]
        for item in broll_creative
        if item.get("audio_preserving_replacement") is not None]

    # There is NO minimum B-roll count. The creative direction decides how
    # many cutaways a piece gets; nothing is padded to satisfy a number.
    # Captain's ruling 2026-08-20 - the floor that rejected fewer than 5
    # clips (while its message said 5-15 and its check said 5-20) is
    # removed outright, not reconciled and not downgraded to a warning.
    # The accepted consequence: a thin edit is no longer caught
    # mechanically. Do not reintroduce an equivalent check here or
    # elsewhere. Sparse and uncovered-block behavior is covered by
    # tests/unit/context/test_bridges.py.
    #
    # What an empty plan must still not do is leave a block with no V1
    # picture uncovered: with no A-roll underneath, the cutaway IS the
    # picture, and the hole would otherwise fail a whole stage later at
    # compile_manifest as undeclared black. That case refuses HERE,
    # naming the block, through the post-bridge retry path back to the
    # model that chose nothing for it. A deliberately declared black
    # beat is covered by its declaration, not by a cutaway.
    # Route uncovered blocks through the asset router before refusing:
    # a block with no V1 picture and no cutaway is a missing B-roll
    # asset, and the router's broll_substitute handler can select a
    # substitute from the project's own catalog. The decision record is
    # written for each substitute; a block no clip can cover is left
    # for _refuse_uncovered to refuse.
    broll_assignments = result.get("b_roll_assignments", [])
    substitutes = _route_uncovered_substitutes(
        timed_spine.get("structure", []), broll_assignments,
        clip_catalog, data.get("project_folder", ""))
    if substitutes:
        broll_assignments.extend(substitutes)
        result["b_roll_assignments"] = broll_assignments
    _refuse_uncovered(timed_spine.get("structure", []),
                      result.get("b_roll_assignments", []))

    # The B-roll density this piece carries, decided through the one
    # ladder.  A decision key nothing here reads is refused before
    # anything resolves - the refusal travels the post-bridge retry path
    # so the model re-plans instead of the plan carrying a key nobody
    # reads.
    refuse_unknown_keys(data.get(decided_value.MERGE_KEY) or [],
                         DECISION_ENTRY_KEYS, step="select_broll",
                         plan="value_decisions")
    broll_decision = decided_value.decide(
        BROLL_SLOT,
        project_folder=data.get("project_folder", ""),
        creative_direction=data.get("creative_direction"),
        model_answer=decided_value.answers_from(data, BROLL_SLOT),
        step="select_broll")
    for line in decided_value.summary_lines([broll_decision]):
        print(f"  {line}", file=sys.stderr)
    result["value_decisions"] = [broll_decision.as_record()]

    # Say which path each cutaway took: search-verified or catalog.
    provenances = [
        a.get("search_status", "") for a in
        result.get("b_roll_assignments", [])]
    verified = sum(1 for s in provenances if s == "search_verified")
    print(f"  Search provenance: {verified}/{len(provenances)} "
          f"assignment(s) search-verified, "
          f"{len(provenances) - verified} on the catalog path",
          file=sys.stderr)

    json.dump(result, sys.stdout, indent=2)


def _route_uncovered_substitutes(blocks: list, assignments: list,
                                 catalog: list,
                                 project_folder: str) -> list:
    """Route each uncovered block's B-roll gap through the router.

    A block with no V1 picture and no cutaway is a missing B-roll
    asset. Before the step refuses, the gap is routed as a ``b_roll``
    need: the ``broll_substitute`` handler selects a substitute from
    the project's own catalog, and the decision record is written.
    Returns the substitute assignments; a block no catalog clip can
    cover is left for ``_refuse_uncovered`` to refuse.
    """
    covered = {str(a.get("spine_block_position"))
               for a in assignments if isinstance(a, dict)}
    substitutes = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if (block_reaches_v1(block) or declares_black_beat(block)
                or str(block.get("position")) in covered):
            continue
        substitute = asset_transformations.route_broll_gap(
            block, catalog, assignments + substitutes, project_folder)
        if substitute is not None:
            substitutes.append(substitute)
    return substitutes


def _refuse_uncovered(blocks: list, assignments: list) -> None:
    """Refuse a block with nothing on V1 that no cutaway covers."""
    covered = {str(a.get("spine_block_position"))
               for a in assignments if isinstance(a, dict)}
    uncovered = [
        str(block.get("position"))
        for block in blocks
        if isinstance(block, dict)
        and not block_reaches_v1(block)
        and not declares_black_beat(block)
        and str(block.get("position")) not in covered
    ]
    if uncovered:
        from library.tools.ren_refusal import RenRefusal

        raise RenRefusal(
            what=(f"B-roll selection covers no picture for block(s) "
                  f"{uncovered}"),
            why=("each puts nothing on V1, so without a cutaway those "
                 "ranges render black"),
            fix=("select a clip covering each block, or declare the "
                 "hole an intentional black beat on the spine block"))


# ── Region-scoped re-plan, and putting it back ──────────────────────

def splice_region_broll(broll_creative: list, clip_catalog: list,
                        semantic_analysis_documents: list,
                        timed_spine: dict, stored_selections: dict, scope,
                        b_roll_interjections: list = None,
                        temporal_event_indices=None,
                        project_folder: str = "") -> dict:
    """Resolve a REGION's fresh cutaways and splice them into
    `stored_selections` (this step's recorded output).

    `broll_creative` / `b_roll_interjections` are the model's answer FOR
    THE REGION: assignments on, and interjections over, only blocks the
    region touches.  Every cutaway keyed to a block outside the region
    comes back byte-identical, and the report MEASURES that.

    V2 is one track, so the region's interjections are placed AROUND
    every cutaway it keeps (`resolve_broll(occupied=...)`), and a fresh
    assignment that would collide with a kept interjection - one placed
    over a neighbouring block that reached into the region - is refused
    by name rather than overlapped.

    Refuses: a region touching no block; a fresh cutaway keyed outside
    the region (`plan_splice`); a V2 collision with a kept cutaway; a
    region block with nothing on V1 left uncovered (`_refuse_uncovered`).

    Returns `{"b_roll_assignments", "b_roll_interjections",
    "splice": <report>}`.
    """
    from library.tools.plan_splice import (
        SpliceRefused,
        outside_region,
        splice_entries,
        splice_report,
    )
    from library.tools.spine_contract import blocks_overlapping

    span = scope.region_span
    structure = timed_spine.get(
        "structure", timed_spine.get("audio_spine", {}).get("structure", []))
    touched = blocks_overlapping(structure, span.start, span.end)
    if not touched:
        raise SpliceRefused(
            f"region {span} touches no spine block",
            "there is nothing in it to re-plan",
            "address a region inside the timeline")
    positions = [b["position"] for b in touched]

    a_key, i_key = "spine_block_position", "over_spine_block_position"
    stored_a = stored_selections.get("b_roll_assignments") or []
    stored_i = stored_selections.get("b_roll_interjections") or []
    kept = ([(a["timeline_start"], a["timeline_end"], f"assignment on "
              f"block {a[a_key]}")
             for a in outside_region(stored_a, positions, a_key)]
            + [(i["timeline_start"], i["timeline_end"], f"interjection "
                f"over block {i[i_key]}")
               for i in outside_region(stored_i, positions, i_key)])

    temporal = temporal_event_indices or []
    if isinstance(temporal, dict):
        temporal = temporal.get("temporal_event_indices", [])
    width, height = resolve_delivery_format(project_folder)
    fresh = resolve_broll(
        broll_creative or [], b_roll_interjections or [], clip_catalog,
        semantic_analysis_documents, temporal, timed_spine,
        target_resolution=(width, height),
        occupied=[(lo, hi) for lo, hi, _ in kept],
        project_folder=project_folder)

    assignments = splice_entries(stored_a, fresh["b_roll_assignments"],
                                 positions, a_key, a_key)
    interjections = splice_entries(stored_i, fresh["b_roll_interjections"],
                                   positions, i_key, i_key)

    collisions = [
        f"block {a[a_key]} ({a['timeline_start']}-{a['timeline_end']}s) "
        f"against the kept {what} ({lo}-{hi}s)"
        for a in fresh["b_roll_assignments"]
        for lo, hi, what in kept
        if a["timeline_start"] < hi - 1e-9 and lo < a["timeline_end"] - 1e-9]
    if collisions:
        raise SpliceRefused(
            "the region's cutaways would overlap ones it keeps on V2",
            "V2 is one track, and a kept cutaway already plays there:\n"
            "  - " + "\n  - ".join(collisions),
            "widen the region to the block the kept cutaway belongs to, "
            "or re-plan B-roll at project scope")

    _refuse_uncovered(touched, assignments)

    a_report = splice_report(stored_a, assignments, positions, a_key)
    i_report = splice_report(stored_i, interjections, positions, i_key)
    return {
        "b_roll_assignments": assignments,
        "b_roll_interjections": interjections,
        "splice": {
            "region": span.as_address(),
            "positions": a_report["positions"],
            "outside_unchanged": (a_report["outside_unchanged"]
                                  and i_report["outside_unchanged"]),
            "b_roll_assignments": a_report,
            "b_roll_interjections": i_report,
        },
    }


if __name__ == "__main__":
    main()
