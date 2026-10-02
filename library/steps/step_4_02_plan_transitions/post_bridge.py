#!/usr/bin/env python3
"""
Step 4.2 Bridge: Resolve Transition Creative Plan to Execution Data

Takes the LLM's creative transition selections and resolves them to
precise timeline cut points using signal data from the temporal index.

Cut point refinement strategy:
  1. Find the OUTGOING block (the one before the cut)
  2. For speech blocks: cut at the last word's end time
  3. For non-speech blocks: use block boundary
  4. Beat-snap if within tolerance
  5. Prefer positions where a word end AND a beat coincide

Classification: Deterministic / Data Transformation
Idempotent: Yes
"""
import json
import math
import sys
from library.tools.pipeline_validation import require_keys
from library.tools.plan_keys import refuse_unknown_keys
from library.tools.plan_splice import number_within_block
from library.tools.ren_refusal import RenRefusal
from library.tools.spine_contract import (
    block_word_end_times_timeline,
    is_speech_block,
)


class TransitionSpecRefused(RenRefusal):
    """A planned transition states numbers nothing can build."""
from library.tools.sub_block_anchor import (
    ANCHOR_ENTRY_KEYS,
    AnchorRefused,
    resolve_anchor,
)


# A word end counts as landing on a beat within this many seconds.
BEAT_COINCIDENCE_TOLERANCE = 0.05

# The entry keys this step reads. Anything else on an entry is REFUSED
# by `refuse_unknown_keys` in `resolve_transitions`, never dropped: an
# unread key is how a probe's SFX `at_word` landed 3.06 s early on the
# block start. `type` is the advertised kind; `transition_type` is the
# legacy spelling; the three `cut_point_*` keys are legacy timeline
# spellings of `cut_point_position`. `anchor` is the sub-block address
# (library/tools/sub_block_anchor.py): a word in the OUTGOING block, a
# beat/downbeat/bar or a timeline frame the cut lands on EXACTLY,
# winning over the word-end and beat-snap below. A cut is a point, so
# `anchor_end` is refused on it. `fallback_type` is the granted native
# transition to ship when the requested one is a measured refusal
# (Whip Pan, Dip, Push, Blur Dissolve - see library/tools/native_ops.py):
# a refused name is never downgraded or swapped unless the plan states
# the substitute, and this key is how it states one.
# `lead_seconds` / `lag_seconds` state the audio offset of a J/L cut
# (library/tools/jl_cut.py): the plan's own number for how far the ear
# crosses before (J) or lingers after (L) the picture cut, or the anchor
# beside it states it instead. `lead_frames` / `lag_frames` are the
# same offset in the requester's units, when the request states frames
# (rung 7, E3: TR3.3's "20 frames before the picture cut"). `anchor`
# on a J/L entry is the AUDIO cut - a word in the outgoing block (J)
# or the incoming one (L), a beat/downbeat/bar, or a frame - never the
# picture cut.
# `duration_frames` is the same E3 rule for the hold: the requester's
# stated count ("a 12-frame dissolve"), winning over `duration_feel`.
# `duration_seconds` carries a hold the requester stated in seconds;
# the original value remains on the resolved plan beside its frame-grid
# representation. When both units are stated they must agree within half
# a frame.
# `cut_point_position: "end"` addresses the end of the piece, past the
# last block - the only addressing an end-of-piece transition takes
# (TR3.1's dip to black out of the final shot).
TRANSITION_ENTRY_KEYS = frozenset({
    "cut_point_position",
    "cut_point_original",
    "cut_point_timeline",
    "cut_time",
    "type",
    "transition_type",
    "fallback_type",
    "duration_feel",
    "duration_frames",
    "duration_seconds",
    "lead_seconds",
    "lag_seconds",
    "lead_frames",
    "lag_frames",
    "rationale",
} | ANCHOR_ENTRY_KEYS)

#: The picture types an end-of-piece entry may carry. The end has an
#: outgoing clip and nothing after it, so only a tail-only fade
#: (`fade_to_black`, drawn on the last clip alone) and an end-placed
#: native dissolve (`cross_dissolve`, which the API trails onto
#: nothing - measured 2026-09-24) are buildable there. Anything else
#: drops with its reason: completing it from another type would invent
#: the gesture.
END_CAPABLE_TYPES = ("fade_to_black", "cross_dissolve")

# How far back from the end of a block's speech a beat-coincident word end
# may be taken. About one short word: the point is to nudge a cut onto the
# music, not to choose a different place to cut. See resolve_cut_point.
MAX_WORD_END_BACKTRACK = 0.35



CUT_KEY = "cut_into_position"
"""The spine position of the block a transition cuts INTO - or END_SLOT."""

END_SLOT = "end"
"""The cut past the last block: `cut_point_position: "end"`."""


def snap_to_beat(
    cut_time: float,
    beat_grid: list,
    tolerance: float = 0.10,
) -> tuple:
    """
    Snap a cut point to the nearest musical beat if within tolerance.

    Returns (snapped_time, was_snapped, snap_delta).
    """
    if not beat_grid:
        return cut_time, False, 0.0

    closest = min(beat_grid, key=lambda b: abs(b - cut_time))
    delta = closest - cut_time
    if abs(delta) <= tolerance:
        return closest, True, round(delta, 4)
    return cut_time, False, 0.0


def resolve_cut_point(
    incoming: dict,
    outgoing: dict,
    beat_grid: list,
) -> dict:
    """Find the precise cut point for a transition.

    Examines the OUTGOING block (the one before the cut) to find the
    natural end point from its own word timings.  The spine carries those
    timings directly (see library/tools/spine_contract.py), so this reads
    `outgoing["word_timestamps"]` rather than re-deriving them from a
    temporal index keyed by a clip_id the block used not to expose.

    Args:
        incoming: The incoming spine block
        outgoing: The outgoing spine block
        beat_grid: List of beat positions in timeline domain

    Returns:
        {
            "cut_time": float,
            "method": str,
            "word_beat_coincidence": bool
        }
    """
    incoming_start = incoming["timeline_start"]

    # ── Speech blocks: cut at the last word's end ──
    if is_speech_block(outgoing):
        tl_end = outgoing["timeline_end"]
        block_word_ends_tl = block_word_end_times_timeline(outgoing)

        if block_word_ends_tl:
            last_word_end = max(block_word_ends_tl)

            # Don't exceed the block's timeline_end
            cut_time = min(last_word_end, tl_end)

            # Check for word-end + beat coincidence (the ideal cut).
            #
            # Only word ends NEAR the end of the block are candidates,
            # and the latest one wins. This loop used to scan the whole
            # block from its FIRST word and take whichever word end
            # happened to land on a beat, which is a relocation, not a
            # snap: on project 001 the cut planned for the end of the
            # 2.4s hook was placed at 0.196s - the end of its first word
            # - and the cut at 18.37s moved to 11.33s, dropping seven
            # seconds of speech. compile_manifest then failed with
            # "Transition trans_001 at 0.196s does not sit at the end of
            # any V1 clip", which is the only reason it was caught: the
            # record said `snap_delta_seconds: 0.0` throughout, because
            # the delta is only measured on the snap_to_beat path.
            #
            # A beat coincidence is a sub-word adjustment to a cut that
            # is already at the end of the speech. It must never be able
            # to move the cut somewhere else in the block.
            word_beat_coincidence = False
            if beat_grid:
                candidates = sorted(
                    (we for we in block_word_ends_tl
                     if 0 <= cut_time - we <= MAX_WORD_END_BACKTRACK),
                    reverse=True,
                )
                for we in candidates:
                    if any(abs(we - beat) < BEAT_COINCIDENCE_TOLERANCE
                           for beat in beat_grid):
                        cut_time = min(we, tl_end)
                        word_beat_coincidence = True
                        break

            # If no coincidence, still beat-snap the word-end cut
            if not word_beat_coincidence and beat_grid:
                snapped, was_snapped, _ = snap_to_beat(cut_time, beat_grid, tolerance=0.08)
                if was_snapped:
                    cut_time = min(snapped, tl_end)

            return {
                "cut_time": cut_time,
                "method": "word-end" + (" + beat" if word_beat_coincidence else ""),
                "word_beat_coincidence": word_beat_coincidence,
            }

    # ── Non-speech blocks: use incoming block's timeline_start ──
    return {
        "cut_time": incoming_start,
        "method": "block-boundary",
        "word_beat_coincidence": False,
    }


def _unplaceable_reason(ttype: str, cut_time: float,
                         carriers_row: dict | None,
                         v2_spans: list | None) -> str | None:
    """Why this resolved transition carries on no track, or None.

    The plan-time half of findings 16 and 32: a transition the model
    placed where no cut exists must go back to the model (first pass)
    or ship as the hard cut the boundary already is (later passes) -
    never forward to a compile that downgrades it unasked. Judges what
    4.02 can see: the spine (V1 membership both sides) and the b-roll
    plan (V2 pairs). An overlay element rides its own track and needs
    no cut; a withdrawn or refused name is refused on its own path;
    either returns None here.
    """
    from library.tools.transition_carriers import (
        CARRIES, v2_pair_at,
    )
    from library.tools.transition_vocabulary import (
        is_cut, is_overlay, native_canonical_type,
    )
    if is_cut(ttype) or is_overlay(ttype):
        return None
    if native_canonical_type(ttype) is None:
        # Drawn route: a V1 clip must end at the cut with another
        # following - the `cut_carriers` verdict the bridge already
        # puts in `cuts_toon` beside every cut.
        if carriers_row is not None and carriers_row.get("verdict") == CARRIES:
            return None
        basis = (carriers_row or {}).get("basis") or \
            "the boundary names no spine cut this step can seat"
        return (f"drawn {ttype!r} at {cut_time:.3f}s carries on no V1 "
                f"cut ({basis})")
    # Native route: a V1 cut that draws through, or a V2 pair
    # abutting the cut on the b-roll's own track (finding 16).
    if (carriers_row is not None
            and carriers_row.get("verdict") == CARRIES
            and "draws through" in str(carriers_row.get("basis") or "")):
        return None
    if v2_spans is not None and v2_pair_at(v2_spans, cut_time) is not None:
        return None
    if v2_spans is None:
        # No b-roll plan to judge V2 by (a direct call, not the
        # runner): what cannot be judged is not refused.
        return None
    basis = (carriers_row or {}).get("basis") or \
        "the boundary names no spine cut this step can seat"
    return (f"native {ttype!r} at {cut_time:.3f}s carries on no cut "
            f"({basis}; no V2 pair abuts it either)")


def _resolve_cut_block_index(trans: dict, spine_blocks: list):
    """Resolve a creative transition entry to the INCOMING block's index.

    A cut sits at the boundary before a block, so index 0 (the first
    block) is never a valid cut point.  Returns None when the entry names
    no boundary at all - previously a missing position silently defaulted
    to timeline 0.0, which collapsed every transition onto one boundary.

    `cut_point_position: "end"` addresses the end of the piece, past
    the last block, and resolves to `len(spine_blocks)` - the one index
    no block owns. The group loop reads it as the end slot (no incoming
    block, no J/L offset, tail-only or end-placed types only).
    """
    pos = trans.get("cut_point_position")
    if isinstance(pos, str) and pos.strip().lower() == "end":
        return len(spine_blocks)
    if pos is not None:
        for i, b in enumerate(spine_blocks):
            if str(b["position"]) == str(pos):
                return i if i > 0 else None
        # Position didn't match any spine label - fall through to
        # timeline-based matching using it as a timeline timestamp.
        if isinstance(pos, (int, float)):
            candidates = [
                (abs(b["timeline_start"] - float(pos)), i)
                for i, b in enumerate(spine_blocks)
                if i > 0
            ]
            if candidates:
                return min(candidates)[1]

    for key in ("cut_point_original", "cut_point_timeline", "cut_time"):
        if trans.get(key) is not None:
            target = trans[key]
            candidates = [
                (abs(b["timeline_start"] - target), i)
                for i, b in enumerate(spine_blocks)
                if i > 0
            ]
            if not candidates:
                return None
            return min(candidates)[1]

    return None


def resolve_transitions(
    creative_plan: list,
    timed_spine: dict,
    music_selection: dict,
    temporal_indices: list = None,
    frame_rate: float = 30.0,
    creative_direction: dict = None,
    brand_effect: dict = None,
    music_analysis: dict = None,
    v2_spans: list | None = None,
    attempt: int | None = None,
) -> list:
    """Resolve creative transition plan to execution specs.

    For each transition:
    1. Find the outgoing spine block
    2. If speech: cut at last word's end time
    3. Beat-snap (prefer word-end + beat coincidence)
    4. Resolve duration_feel to frame count
    """
    # An entry key nothing here reads is refused before anything
    # resolves - the refusal travels the post-bridge retry path so the
    # model re-plans instead of the cut landing somewhere unasked.
    refuse_unknown_keys(creative_plan, TRANSITION_ENTRY_KEYS,
                         step="plan_transitions", plan="transition_creative")
    if creative_direction is None:
        creative_direction = {}
    if brand_effect is None:
        brand_effect = {}

    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))

    # What each cut can carry, keyed by incoming block position - the
    # same `cut_carriers` rows the bridge puts in `cuts_toon`, so the
    # retry below judges by the table the model was shown. Read only
    # where the runner is judging a pass (`attempt` is not None): a
    # direct call carries no b-roll plan to judge V2 by, and what
    # cannot be judged is not refused.
    carriers_by_position = {}
    if attempt is not None:
        from library.tools.transition_carriers import cut_carriers
        for row in cut_carriers(spine_blocks):
            carriers_by_position[row["position"]] = row
    unplaceable: list = []

    from library.tools.beat_grid import beat_positions as real_beat_positions

    # The REAL beat grid, from step 2.06's analysis of the actual track.
    # This used to synthesise [i * 60/bpm for i in ...] starting at t=0,
    # and no track's first beat lands at 0.000s - so every "beat-snapped"
    # cut was snapped to a grid offset from the music by the track's
    # lead-in. See library/tools/beat_grid.py.
    beat_positions = real_beat_positions(music_analysis, music_selection)

    from library.tools.transition_selector import select_transition
    from library.tools.transition_vocabulary import is_cut
    from library.tools.jl_cut import (
        JLCutRefused,
        normalise_kind as _jl_kind,
        resolve_audio_cut as _resolve_audio_cut,
    )
    from library.tools.transition_carriers import block_reaches_v1

    # Entries are grouped by boundary FIRST: a boundary may carry ONE
    # picture decoration and ONE J/L audio offset (a drawn dissolve
    # whose audio crosses early is one cut, not two). Two audio offsets
    # at one boundary refuse - two numbers for one audio cut is an
    # ambiguous spec - while second picture decorations drop exactly as
    # before. Grouping preserves plan order inside each boundary and
    # the final sort below makes boundary order irrelevant downstream.
    indexed = []
    for trans in creative_plan:
        block_idx = _resolve_cut_block_index(trans, spine_blocks)
        if block_idx is None:
            print(
                f"  Dropped transition {trans!r}: it names no spine "
                f"boundary (needs cut_point_position or cut_point_timeline)",
                file=sys.stderr,
            )
            continue
        indexed.append((block_idx, trans))
    groups: dict = {}
    for block_idx, trans in indexed:
        groups.setdefault(block_idx, []).append(trans)

    resolved = []

    for block_idx in sorted(groups):
        entries = groups[block_idx]
        # The end slot: `cut_point_position: "end"` resolved past the
        # last block. There is no incoming block, so no J/L offset and
        # no word-end/beat-snap - the piece ends where it ends, on the
        # last block's frame-exact end.
        is_end = block_idx == len(spine_blocks)
        if is_end:
            if not spine_blocks:
                for trans in entries:
                    print(
                        f"  Dropped transition {trans!r}: the spine is "
                        f"empty, so there is no end to place it on",
                        file=sys.stderr,
                    )
                continue
            block = None
            outgoing = spine_blocks[-1]
            where = "the end of the piece"
        else:
            block = spine_blocks[block_idx]
            outgoing = spine_blocks[block_idx - 1]
            where = f"spine boundary {block.get('position')!r}"

        jl_here = [t for t in entries
                   if _jl_kind(t.get("type",
                                     t.get("transition_type", "")))
                   is not None]
        if jl_here and is_end:
            (jl_entry,) = jl_here
            raise JLCutRefused(
                what=(f"step plan_transitions J/L entry at {where}"),
                why=("a J/L cut trims the speech row between two V1 "
                     "blocks, and the end of the piece has no incoming "
                     "side - there is no join to offset."),
                fix=("re-plan the cut at a join between two V1 blocks, "
                     "or drop it and keep the end transition's picture "
                     "alone."),
            )
        if len(jl_here) > 1:
            raise JLCutRefused(
                what=(f"step plan_transitions plans two audio offsets "
                      f"at {where}"),
                why=("one join carries one audio cut - two offsets is "
                     "an ambiguous spec, and placing one silently "
                     "would ship timing the plan did not agree on."),
                fix=(f"re-plan a single `j_cut` or `l_cut` entry at "
                     f"{where}, or drop one of them."),
            )
        pic_here = [t for t in entries
                    if all(t is not j for j in jl_here)]

        trans_dict = None
        for pos, trans in enumerate(pic_here):
            if is_end:
                candidate = _resolve_end_entry(
                    trans, outgoing, frame_rate,
                    creative_direction, brand_effect,
                    music_analysis, music_selection,
                    index=len(resolved),
                    temporal_indices=temporal_indices)
            else:
                candidate = _resolve_picture_entry(
                    trans, block, outgoing, beat_positions, frame_rate,
                    creative_direction, brand_effect,
                    music_analysis, music_selection, index=len(resolved),
                    temporal_indices=temporal_indices)
            if candidate is None:
                # Dropped for no stated hold (message already printed):
                # the boundary stays free for the next entry, exactly
                # as the discard below used to.
                continue
            trans_dict = candidate
            for dup in pic_here[pos + 1:]:
                print(
                    f"  Dropped duplicate transition at {where}",
                    file=sys.stderr,
                )
            break

        if jl_here:
            (jl_entry,) = jl_here
            trans_dict = _attach_jl_offset(
                jl_entry, trans_dict, block, outgoing, beat_positions,
                frame_rate, music_analysis, music_selection,
                index=len(resolved), temporal_indices=temporal_indices)

        if trans_dict is not None:
            # End-slot entries have no incoming block. `_resolve_end_entry`
            # already restricts them to types that can draw on the final
            # clip; the per-cut carrier table only describes joins between
            # blocks and must not inspect `block` (which is None here).
            if attempt is not None and not is_end:
                reason = _unplaceable_reason(
                    trans_dict.get("transition_type", ""),
                    trans_dict.get("cut_point_timeline"),
                    carriers_by_position.get(block.get("position")),
                    v2_spans)
                if reason is not None:
                    unplaceable.append((block.get("position"), trans_dict,
                                        reason))
            # The cut this transition sits on, named by the block it
            # cuts INTO ("end" past the last one): the key a region
            # splice exchanges transitions by, and what the id counts
            # within.
            trans_dict[CUT_KEY] = END_SLOT if is_end else block.get(
                "position")
            resolved.append(trans_dict)

    if unplaceable and attempt == 1:
        # First pass: back to the model that placed them, with the
        # reason - the post_bridge_retry path re-asks, so the model
        # can re-place each transition onto a cut that carries it
        # (findings 16, 32). Later passes ship the hard cut instead:
        # the retry is the correction chance, not a refusal loop.
        lines = "\n".join(
            f"  - into block {pos!r}: {reason}"
            for pos, _t, reason in unplaceable
        )
        raise ValueError(
            f"step plan_transitions placed {len(unplaceable)} of "
            f"{len(resolved)} transition(s) where no cut carries "
            f"them:\n{lines}\n"
            f"Answer again with each transition re-placed onto a cut "
            f"its track carries - `cuts_toon`'s "
            f"`can_carry_drawn_transition` / `carry_basis` columns say "
            f"which cuts those are - or dropped. At most 3 passes; "
            f"what still carries nothing after that ships as the hard "
            f"cut the boundary already is, with the reason recorded."
        )
    for _pos, t, reason in unplaceable:
        # A later pass: the retry did not place it, so the boundary
        # ships as the hard cut it already is - stamped on the row,
        # the same surfacing the compile gives a fallback, so the
        # per-item record says what was asked and why it changed.
        t["requested_type"] = t.get("transition_type")
        if (t.get("duration_source") in (
                "frames", "stated_frames", "stated_frames_and_seconds")
                and t.get("duration_frames") is not None):
            t["requested_duration_frames"] = t["duration_frames"]
        t["transition_type"] = "hard_cut"
        t["duration_frames"] = 0
        t["downgrade_reason"] = reason

    resolved.sort(key=lambda t: t["cut_point_timeline"])
    number_within_block(resolved, CUT_KEY, "transition_id", "trans")

    _assert_transitions_distinct(resolved)
    return resolved


def _refuse_duration(what: str, why: str, fix: str) -> TransitionSpecRefused:
    return TransitionSpecRefused(what=what, why=why, fix=fix)


def _resolve_hold(trans: dict, ttype: str, selected_trans: dict,
                  frame_rate: float, where: str):
    """How many frames a drawn transition holds, and whose number it is.

    Returns `(dur_frames, duration_source, stated_seconds)` or None
    when the entry drops for no stated hold (the message is printed
    here). `stated_seconds` preserves the requester's number when the
    request used seconds; the renderer receives the frame-grid count.
    `duration_source` records whether the number came from frames,
    seconds, a feel word, a brand template, or an instantaneous cut.
    Raises `TransitionSpecRefused` where the plan states disagreeing
    numbers, an unplaceable value, or a stated count the brand's own
    bounds forbid - two declarations in conflict, never a choice to
    make silently.
    """
    from library.tools.transition_vocabulary import is_cut

    if is_cut(ttype):
        return 0, "cut", None
    # Duration frame calculation.
    #
    # How long a transition holds is PACE, and the plan is what says
    # it. The handoff asks for the requester's stated frames or seconds,
    # and a feel word only when the request gives no length. This used
    # to read the brand template's `transition_duration_ms` FIRST and
    # consult the plan only if that came out under a frame, so on project 001 a
    # "quick" defocus and a "medium" defocus were both held for
    # 500 ms - the top of a range in default_brand.yaml, a template
    # the project never selected.  The model's own rationale for the
    # second one reads "'medium' (333ms)".
    #
    # `duration_map` is not a choice of pace: it is the rendering of
    # the word the model wrote into frames, the same way
    # `audio_mix` renders a declared `music_behavior` into dB.
    duration_map = {
        "instant": 0,
        "quick": int(6 * (frame_rate / 30)),
        "medium": int(10 * (frame_rate / 30)),
        "slow": int(15 * (frame_rate / 30)),
    }
    feel = trans.get("duration_feel")
    stated_f = trans.get("duration_frames")
    stated_s = trans.get("duration_seconds")
    lo, hi = selected_trans.get("duration_bounds_ms", (None, None))
    if stated_s is not None and (
            isinstance(stated_s, bool)
            or not isinstance(stated_s, (int, float))
            or not math.isfinite(stated_s)
            or stated_s < 0):
        raise _refuse_duration(
            f"step plan_transitions entry at {where} states "
            f"duration_seconds {stated_s!r}, which is not a "
            f"non-negative finite number of seconds",
            "a transition hold must have a placeable length.",
            f"re-plan the cut at {where} with a non-negative "
            f"duration_seconds, or drop it.",
        )
    if stated_f is not None and (
            isinstance(stated_f, bool)
            or not isinstance(stated_f, int)
            or stated_f < 0):
        raise _refuse_duration(
            f"step plan_transitions entry at {where} states "
            f"duration_frames {stated_f!r}, which is not a "
            f"non-negative whole number of frames",
            "frames place on the timeline's own grid - a fractional, "
            "negative, or non-numeric frame count is not placeable.",
            f"re-plan the cut at {where} with duration_frames as "
            f"non-negative whole frames, or drop it.",
        )

    seconds_frames = (round(float(stated_s) * frame_rate)
                      if stated_s is not None else None)
    if (stated_f is not None and stated_s is not None
            and abs(float(stated_s) * frame_rate - stated_f) > 0.5 + 1e-9):
        raise _refuse_duration(
            f"step plan_transitions entry at {where} states "
            f"duration_seconds {stated_s:g}s "
            f"({float(stated_s) * frame_rate:g} frames at "
            f"{frame_rate:g} fps) and duration_frames {stated_f}",
            "two numbers for one hold disagree by more than half "
            "a frame.",
            f"re-plan the cut at {where} with seconds and frames "
            f"agreeing, or state only the requester's unit.",
        )

    explicit_frames = stated_f if stated_f is not None else seconds_frames
    if (explicit_frames is not None and feel in duration_map
            and duration_map[feel] != explicit_frames):
        raise _refuse_duration(
            f"step plan_transitions entry at {where} states "
            f"duration_feel {feel!r} "
            f"({duration_map[feel]} frames at {frame_rate:g} fps) "
            f"beside an explicit duration of {explicit_frames} frames",
            "two numbers for one hold is an ambiguous spec - "
            "picking one silently would ship a hold the plan did "
            "not agree on.",
            f"re-plan the cut at {where} with the feel and explicit "
            f"duration agreeing, or state only one of them.",
        )

    if explicit_frames is not None:
        # A brand's {min, max} is a permission, so a stated count it
        # forbids is two declarations in conflict - refused, never
        # clamped silently: clamping would ship a hold neither the
        # requester nor the template agreed on.
        lo_f = (int((lo / 1000.0) * frame_rate)
                if lo is not None else None)
        hi_f = (int((hi / 1000.0) * frame_rate)
                if hi is not None else None)
        if ((lo_f is not None and explicit_frames > 0
             and explicit_frames < lo_f)
                or (hi_f is not None and explicit_frames > 0
                    and explicit_frames > hi_f)):
            raise _refuse_duration(
                f"step plan_transitions entry at {where} states a "
                f"{explicit_frames}-frame hold, outside the selected "
                f"brand template's transition bounds "
                f"({lo}ms-{hi}ms, {lo_f}-{hi_f} frames at "
                f"{frame_rate:g} fps)",
                "the template's bounds are a permission, and the "
                "stated hold exceeds what it permits.",
                f"re-plan the cut at {where} inside the template's "
                f"bounds, or widen the template's transition "
                f"duration range.",
            )
        if stated_f is not None and stated_s is not None:
            source = "stated_frames_and_seconds"
        elif stated_f is not None:
            source = "stated_frames"
        elif feel in duration_map:
            source = "stated_seconds_and_feel"
        else:
            source = "stated_seconds"
        return explicit_frames, source, (
            float(stated_s) if stated_s is not None else None)

    if feel in duration_map:
        dur_frames = duration_map[feel]
        # A brand's {min, max} is a permission, so it BOUNDS the
        # plan's choice rather than replacing it.
        # `instant` is zero frames: the plan asking for no hold.
        # Clamping it up to the brand's minimum would give it one.
        if lo is not None and dur_frames > 0:
            dur_frames = max(dur_frames, int((lo / 1000.0) * frame_rate))
        if hi is not None and dur_frames > 0:
            dur_frames = min(dur_frames, int((hi / 1000.0) * frame_rate))
        return dur_frames, "feel", None
    declared_ms = selected_trans.get("duration_ms")
    if declared_ms:
        # The plan declared no pace and the brand declared ONE
        # length (a scalar, not a range). That is a chosen value.
        return int((declared_ms / 1000.0) * frame_rate), "brand_only", None
    # DROPPED, with the reason.  A drawn transition needs a
    # length, and neither the plan nor a selected brand
    # template gave one - so there is nothing to hold it for
    # that anybody chose.  Emitting it at zero frames leaves a
    # `defocus` in the spec that draws nothing and says
    # nothing, which is the unread-parameter failure of
    # AGENTS.md 10.2; completing it from a constant is the
    # invented-taste failure of 10.5.  This is the third
    # option that rule names: drop it and say so.
    print(
        f"  Dropped {ttype!r} at {where}: the plan declares no "
        f"duration_feel ({feel!r}), duration_frames, or "
        f"duration_seconds and no selected brand "
        f"template declares a single transition duration, so "
        f"nothing has said how long to hold it",
        file=sys.stderr,
    )
    return None


def _resolve_picture_entry(trans: dict, block: dict, outgoing: dict,
                           beat_positions: list, frame_rate: float,
                           creative_direction: dict, brand_effect: dict,
                           music_analysis, music_selection,
                           index: int = 0,
                           temporal_indices: list = None):
    """Resolve one picture-transition plan entry to its spec dict.

    Returns None when the entry drops for no stated hold (the message
    is printed here) - the boundary stays free for the next entry.
    """
    from library.tools.transition_selector import select_transition
    from library.tools.transition_vocabulary import is_cut

    original_tl = block["timeline_start"]

    # Use the content-aware transition selector
    selected_trans = select_transition(
        from_clip=outgoing,
        to_clip=block,
        brand_effect=brand_effect,
        creative_direction=creative_direction,
        requested_type=trans.get("type", trans.get("transition_type", "")),
        fallback_type=trans.get("fallback_type", ""),
    )

    ttype = selected_trans["type"]

    held = _resolve_hold(
        trans, ttype, selected_trans, frame_rate,
        where=(f"spine boundary {block.get('position')!r}"))
    if held is None:
        return None
    dur_frames, duration_source, duration_seconds = held

    # Resolve the precise cut point. A sub-block anchor wins
    # exactly: the plan named the word, beat or frame, so the
    # word-end and beat-snap below do not run on it. A cut is a
    # point, so an end anchor refuses here rather than landing
    # nowhere.
    cut_time, cut_info, beat_aligned, snap_delta = _resolve_picture_cut(
        trans, block, outgoing, beat_positions, frame_rate,
        music_analysis, music_selection, index=index,
        temporal_indices=temporal_indices)

    trans_dict = {
        "transition_id": f"trans_tmp_{index+1:03d}",
        "cut_point_timeline": round(cut_time, 3),
        **({"cut_point_frame": cut_info["cut_frame"]}
           if "cut_frame" in cut_info else {}),
        "cut_point_original": round(original_tl, 3),
        "transition_type": ttype,
        "duration_frames": dur_frames,
        **({"duration_feel": trans["duration_feel"]}
           if trans.get("duration_feel") is not None else {}),
        # E3 receipt: whose number the hold is - the requester's
        # stated frames / seconds, a feel word, the template's single
        # length, or zero because the type is a cut.
        "duration_source": duration_source,
        **({"duration_seconds": duration_seconds}
           if duration_seconds is not None else {}),
        "beat_aligned": beat_aligned,
        "snap_delta_seconds": snap_delta,
        # How far the resolved cut ended up from the block boundary
        # the plan named. `snap_delta_seconds` only measures the
        # snap_to_beat path, so a cut relocated by word-end matching
        # recorded 0.0 while having moved 2.2 seconds. Always
        # recorded, so a relocation is visible in the manifest
        # without re-deriving it.
        "displacement_seconds": round(cut_time - original_tl, 3),
        "placement_method": cut_info["method"],
        "word_beat_coincidence": cut_info["word_beat_coincidence"],
        "rationale": trans.get("rationale", ""),
        # What the plan asked for, and why it is not what shipped.
        # The rationale used to be carried through unchanged onto a
        # transition it no longer described - "Standard dialogue cut"
        # sitting on a 15-frame dissolve.
        "requested_type": selected_trans["requested_type"],
        "downgrade_reason": selected_trans["downgrade_reason"],
    }
    return trans_dict


def _resolve_end_entry(trans: dict, outgoing: dict,
                       frame_rate: float,
                       creative_direction: dict, brand_effect: dict,
                       music_analysis, music_selection,
                       index: int = 0,
                       temporal_indices: list = None):
    """Resolve an end-of-piece transition entry to its spec dict.

    TR3.1's dip to black out of the final shot: the plan addresses the
    end with `cut_point_position: "end"`, and only a tail-only fade
    (`fade_to_black`, drawn on the last clip alone) or an end-placed
    native dissolve (`cross_dissolve`) can sit there - anything else
    drops with its reason, because completing it from another type
    would invent the gesture. A cut type drops too: the piece ending
    is not a cut. Returns None on a drop (message printed here).

    The returned dict carries `at_end: true`, which `compile_manifest`
    reads to place the tail-only / end-placed build instead of looking
    for an incoming clip that does not exist.
    """
    from library.tools.transition_selector import select_transition

    # The selector reads two clips for its content heuristics; the end
    # has one. The requested type wins when drawable and allowed, which
    # is the end-slot case the handoff asks for - so both sides read
    # the outgoing clip and the heuristics never fire on a stated type.
    # An end entry with no stated type drops below (nothing to place),
    # never a heuristic's choice.
    selected_trans = select_transition(
        from_clip=outgoing,
        to_clip=outgoing,
        brand_effect=brand_effect,
        creative_direction=creative_direction,
        requested_type=trans.get("type", trans.get("transition_type", "")),
        fallback_type=trans.get("fallback_type", ""),
    )

    ttype = selected_trans["type"]

    if ttype not in END_CAPABLE_TYPES:
        print(
            f"  Dropped {ttype!r} at the end of the piece: only "
            f"{' and '.join(END_CAPABLE_TYPES)} can sit there - the "
            f"end has an outgoing clip and nothing after it, so a "
            f"transition needing two pictures has no head half to "
            f"draw",
            file=sys.stderr,
        )
        return None

    held = _resolve_hold(
        trans, ttype, selected_trans, frame_rate,
        where="the end of the piece")
    if held is None:
        return None
    dur_frames, duration_source, duration_seconds = held

    end_time = float(outgoing["timeline_end"])
    if trans.get("anchor_end") is not None:
        raise AnchorRefused(
            what=("step plan_transitions end entry anchor_end names an "
                  "end anchor on the end of the piece"),
            why=("the end is one point in time: the entry carries "
                 "`anchor_end` and nothing in this step reads it "
                 "as an end."),
            fix=("re-plan the end transition with `anchor` alone for "
                 "the point it lands on, or drop `anchor_end` - and "
                 "note the end defaults to the last block's end."),
        )
    cut_frame = None
    if trans.get("anchor") is not None:
        from library.tools.sub_block_anchor import resolve_anchor
        hit = resolve_anchor(
            trans["anchor"], block=outgoing,
            music_analysis=music_analysis,
            music_selection=music_selection,
            temporal_indices=temporal_indices,
            frame_rate=frame_rate, step="plan_transitions",
            plan="transition_creative",
            index=index)
        cut_time = hit["timeline_seconds"]
        cut_frame = hit["frame"]
        method = f"anchor: {hit['method']}"
    else:
        cut_time = end_time
        method = "timeline-end"

    return {
        "transition_id": f"trans_tmp_{index+1:03d}",
        "cut_point_timeline": round(cut_time, 3),
        **({"cut_point_frame": cut_frame} if cut_frame is not None else {}),
        "cut_point_original": round(end_time, 3),
        "transition_type": ttype,
        "duration_frames": dur_frames,
        **({"duration_feel": trans["duration_feel"]}
           if trans.get("duration_feel") is not None else {}),
        "duration_source": duration_source,
        **({"duration_seconds": duration_seconds}
           if duration_seconds is not None else {}),
        "at_end": True,
        "beat_aligned": False,
        "snap_delta_seconds": 0.0,
        "displacement_seconds": round(cut_time - end_time, 3),
        "placement_method": method,
        "word_beat_coincidence": False,
        "rationale": trans.get("rationale", ""),
        "requested_type": selected_trans["requested_type"],
        "downgrade_reason": selected_trans["downgrade_reason"],
    }


def _resolve_picture_cut(trans: dict | None, block: dict, outgoing: dict,
                         beat_positions: list, frame_rate: float,
                         music_analysis, music_selection,
                         index: int = 0, temporal_indices: list = None) -> tuple:
    """The picture cut in timeline seconds, plus its resolution record.

    `trans` is the picture entry (its `anchor`, when present, wins
    exactly) or None for a J/L-only boundary (the default word-end /
    beat-snap resolution). Returns (cut_time, cut_info).
    """
    if trans is not None and trans.get("anchor_end") is not None:
        raise AnchorRefused(
            what=(f"step plan_transitions plan entry at boundary "
                  f"{block.get('position')!r} anchor_end names an "
                  f"end anchor on a cut"),
            why=("a cut is one point in time: the entry carries "
                 "`anchor_end` and nothing in this step reads it "
                 "as an end."),
            fix=(f"re-plan the cut into block "
                 f"{block.get('position')!r} with `anchor` alone "
                 f"for the point it lands on, and drop "
                 f"`anchor_end`."),
        )
    if trans is not None and trans.get("anchor") is not None:
        hit = resolve_anchor(
            trans["anchor"], block=outgoing,
            music_analysis=music_analysis,
            music_selection=music_selection,
            temporal_indices=temporal_indices,
            frame_rate=frame_rate, step="plan_transitions",
            plan="transition_creative",
            index=index)
        cut_time = hit["timeline_seconds"]
        cut_info = {"cut_time": cut_time,
                    "cut_frame": hit["frame"],
                    "method": f"anchor: {hit['method']}",
                    "word_beat_coincidence": False}
    else:
        cut_info = resolve_cut_point(
            incoming=block,
            outgoing=outgoing,
            beat_grid=beat_positions,
        )
        cut_time = cut_info["cut_time"]

    beat_aligned = False
    snap_delta = 0.0
    if cut_info["method"] == "block-boundary":
        snapped, beat_aligned, snap_delta = snap_to_beat(
            cut_time, beat_positions,
        )
        cut_time = snapped
    elif "beat" in cut_info["method"]:
        beat_aligned = True
    return cut_time, cut_info, beat_aligned, snap_delta


def _attach_jl_offset(jl_entry: dict, trans_dict: dict | None,
                      block: dict, outgoing: dict,
                      beat_positions: list, frame_rate: float,
                      music_analysis, music_selection,
                      index: int = 0, temporal_indices: list = None):
    """Resolve a J/L plan entry and carry its audio offset on the cut.

    The picture decoration (when the boundary carries one) is resolved
    first so the audio cut is measured against the picture cut the eye
    actually gets. A J/L-only boundary ships the hard cut it already
    is - the absence of decoration, never a choice of effect - with
    the audio offset beside it. Returns the transition spec dict.
    """
    from library.tools.jl_cut import JLCutRefused
    from library.tools.jl_cut import normalise_kind as _jl_kind
    from library.tools.jl_cut import resolve_audio_cut as _audio_cut
    from library.tools.transition_carriers import block_reaches_v1

    kind = _jl_kind(jl_entry.get("type",
                                 jl_entry.get("transition_type", "")))
    join_label = f"boundary {block.get('position')!r}"
    for key in ("duration_feel", "fallback_type"):
        if jl_entry.get(key) is not None:
            raise JLCutRefused(
                what=(f"step plan_transitions J/L entry at {join_label} "
                      f"states `{key}`"),
                why=("a J/L cut draws no picture transition, so a hold "
                     "or a substitute transition on it is read by "
                     "nothing - an unread key is how P5's `at_word` "
                     "landed 3.06 s early."),
                fix=(f"re-plan the cut into block "
                     f"{block.get('position')!r} without `{key}` - the "
                     f"picture stays the hard cut the boundary already "
                     f"is - or move the decoration onto a picture "
                     f"transition entry at the same boundary."),
            )
    if jl_entry.get("anchor_end") is not None:
        raise AnchorRefused(
            what=(f"step plan_transitions J/L entry at {join_label} "
                  f"anchor_end names an end anchor on an audio cut"),
            why=("an audio cut is one point in time: the entry "
                 "carries `anchor_end` and nothing in this step reads "
                 "it as an end."),
            fix=(f"re-plan the cut into block "
                 f"{block.get('position')!r} with `anchor` alone for "
                 f"the point the audio crosses on, and drop "
                 f"`anchor_end`."),
        )
    if not block_reaches_v1(outgoing) or not block_reaches_v1(block):
        raise JLCutRefused(
            what=(f"step plan_transitions J/L entry at {join_label} "
                  f"joins blocks that do not both reach V1"),
            why=("both sides of a J/L join must put picture on V1: "
                 "the trim is addressed to the speech row the V1 "
                 "clips play with."),
            fix=(f"re-plan the cut at a join between two V1 blocks, "
                 f"or drop it."),
        )
    from library.tools.frame_utils import seconds_to_frame
    if "timeline_start_frame" in block:
        boundary_frame = block["timeline_start_frame"]
        if (isinstance(boundary_frame, bool)
                or not isinstance(boundary_frame, int)):
            raise JLCutRefused(
                what=(f"the {join_label} has an unreadable shared boundary "
                      f"frame {boundary_frame!r}"),
                why=("the mesh spine carries one authoritative frame at the "
                     "join; re-rounding a malformed value from seconds would "
                     "invent a different boundary."),
                fix=("re-run step 2.05 (mesh_spine) so its shared frame "
                     "boundary reaches the transition planner."),
            )
    else:
        boundary_frame = seconds_to_frame(float(block["timeline_start"]),
                                          frame_rate)
    boundary_seconds = boundary_frame / float(frame_rate)
    if trans_dict is None:
        # A J/L-only boundary ships the hard cut it already is, AT
        # the boundary frame - the eye cuts where the V1 clips abut,
        # so the default word-end/beat-snap resolution (which can sit
        # seconds inside the outgoing block) would record a picture
        # cut the timeline never plays.
        original_tl = block["timeline_start"]
        trans_dict = {
            "transition_id": f"trans_tmp_{index+1:03d}",
            "cut_point_timeline": round(boundary_seconds, 3),
            "cut_point_original": round(original_tl, 3),
            "transition_type": "hard_cut",
            "duration_frames": 0,
            "beat_aligned": False,
            "snap_delta_seconds": 0.0,
            "displacement_seconds": round(boundary_seconds - original_tl,
                                          3),
            "placement_method": "v1-boundary",
            "word_beat_coincidence": False,
            "rationale": jl_entry.get("rationale", ""),
            "requested_type": kind,
            "downgrade_reason": "",
        }
    hit = _audio_cut(
        kind=kind, entry=jl_entry, outgoing=outgoing, incoming=block,
        boundary_frame=boundary_frame, frame_rate=frame_rate,
        music_analysis=music_analysis, music_selection=music_selection,
        temporal_indices=temporal_indices,
        index=index)
    trans_dict["audio_offset"] = {
        "kind": kind,
        "picture_cut_timeline": hit["picture_cut_timeline"],
        "picture_cut_frame": hit["picture_cut_frame"],
        "audio_cut_timeline": hit["audio_cut_timeline"],
        "audio_cut_frame": hit["audio_cut_frame"],
        "audio_cut_exact": hit["audio_cut_exact"],
        "lead_seconds": hit["lead_seconds"],
        "method": hit["method"],
        "outgoing_position": outgoing.get("position"),
        "incoming_position": block.get("position"),
    }
    return trans_dict


def _assert_transitions_distinct(resolved: list) -> None:
    """Fail when every cut lands on the same timeline position.

    Ten transitions at 2.682s is not a plan; it is a collapse.  It used to
    pass because nothing downstream compared cut points to each other.
    """
    if len(resolved) < 2:
        return
    cut_points = {round(t["cut_point_timeline"], 3) for t in resolved}
    if len(cut_points) < len(resolved):
        raise ValueError(
            f"{len(resolved)} transitions resolved to only "
            f"{len(cut_points)} distinct cut point(s): "
            f"{sorted(cut_points)}. Each transition must sit at its own "
            f"spine boundary."
        )


def _v2_spans(assignments, interjections) -> list:
    """Every b-roll assignment and interjection, as a V2 span."""
    spans = []
    for rows in (assignments, interjections):
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            clip = row.get("assigned_clip") if "assigned_clip" in row \
                else row
            if not isinstance(clip, dict):
                continue
            try:
                spans.append((float(clip["timeline_start"]),
                              float(clip["timeline_end"])))
            except (KeyError, TypeError, ValueError):
                continue
    return spans


# ── Region-scoped re-plan, and putting it back ──────────────────────

def splice_region_transitions(transition_creative: list, timed_spine: dict,
                              music_selection: dict,
                              stored_transitions: list, scope,
                              temporal_event_indices=None,
                              project_fps: float = 30.0,
                              creative_direction: dict = None,
                              brand_effect: dict = None,
                              music_analysis: dict = None,
                              b_roll_assignments: list = None,
                              b_roll_interjections: list = None) -> dict:
    """Resolve a REGION's fresh transitions and splice them into
    `stored_transitions` (this step's recorded `transition_spec`).

    A transition sits on a CUT, and a region owns the cuts INTO the
    blocks it touches - plus the end slot when it touches the last block.
    The cut out of the region's last block is the next block's, outside.
    `transition_creative` is the model's answer FOR THE REGION, and every
    transition on a cut outside it comes back byte-identical; the report
    MEASURES that.

    Each cut resolves from its own two blocks, the beat grid and the
    temporal index, and ids count within their cut, so a cut re-plans to
    the same entry whether or not the others are in the plan.

    A transition no cut carries is downgraded to the hard cut with the
    reason recorded - the later-pass behaviour, because a splice has no
    model retry to send it back through.

    Refuses: a region touching no block; a fresh transition on a cut
    outside the region (`plan_splice`); a merged plan with two
    transitions on one cut point (`_assert_transitions_distinct`).

    Returns `{"transition_spec": [...], "splice": <report>}`.
    """
    from library.tools.plan_splice import (
        SpliceRefused,
        splice_entries,
        splice_report,
    )
    from library.tools.post_bridge_retry import MAX_ATTEMPTS
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
    cuts = [b["position"] for b in touched]
    if touched[-1] is structure[-1]:
        cuts.append(END_SLOT)

    creative = [v for v in (transition_creative or [])
                if isinstance(v, dict)]
    temporal = temporal_event_indices or []
    if isinstance(temporal, dict):
        temporal = temporal.get("temporal_event_indices", [])
    fresh = resolve_transitions(
        creative, timed_spine, music_selection or {}, temporal, project_fps,
        creative_direction or {}, brand_effect or {},
        music_analysis=music_analysis or {},
        v2_spans=_v2_spans(b_roll_assignments, b_roll_interjections),
        attempt=MAX_ATTEMPTS)

    stored = list(stored_transitions or [])
    merged = splice_entries(stored, fresh, cuts, CUT_KEY, "transition_id",
                            start_key="cut_point_timeline")
    _assert_transitions_distinct(merged)

    report = splice_report(stored, merged, cuts, CUT_KEY)
    report["region"] = span.as_address()
    report["region_proposed"] = len(creative)
    return {"transition_spec": merged, "splice": report}


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    require_keys(data, ["music_selection"], "step_4_02_plan_transitions/post_bridge.py")
    if "timed_spine" in data and not isinstance(data["timed_spine"], dict):
        raise ValueError("timed_spine must be a dictionary")
        
    creative = data.get("transition_creative")
    if not creative and "llm_raw_response" in data:
        try:
            parsed = json.loads(data["llm_raw_response"])
            creative = parsed if isinstance(parsed, list) else parsed.get("transition_creative", [])
        except Exception:
            creative = data["llm_raw_response"]
        
    if not isinstance(creative, list):
        print(f"  Warning: LLM returned invalid response for plan_transitions. Defaulting to empty list. Response was: {str(creative)[:100]}", file=sys.stderr)
        creative = []
        
    creative = [v for v in creative if isinstance(v, dict)]

    # There is NO minimum transition count, and no transition is ever
    # added to a plan that did not ask for one.
    #
    # This is where the third creative floor lived, and it outlived the
    # captain's ruling of 2026-08-20 for the same reason
    # `inject_default_ken_burns` did (#192): it was written in CODE, and
    # `tests/test_no_creative_floors.py` only read prompts. It did all
    # three of the things the ruling forbids at once:
    #
    #   * `min_trans = max(1, total_cuts // 3)` - a floor of one drawn
    #     transition per three cuts, chosen by a constant;
    #   * an injection loop that appended `{"type": "defocus",
    #     "duration_feel": "medium", "rationale": "Default defocus added
    #     at scene boundary due to mood/topic shift"}` to the model's plan
    #     wherever the semantic mood or the keyword tags differed;
    #   * and, if the padded plan still fell short, `sys.exit(1)` with
    #     "You MUST plan at least N transitions at DISTINCT cut points" -
    #     word for word the guard removed from plan_vfx.
    #
    # How many transitions a piece gets is a creative decision. An empty
    # plan is a legitimate answer: `transition_vocabulary.CUT_TYPES` draw
    # nothing, so an edit of nothing but hard cuts is the absence of
    # decoration (AGENTS.md 10.4), not a defect. The capability's
    # creative policy and shared bridge scan guard against reintroduced
    # defaults; empty-plan behavior is covered by transition scenarios.

    spine = data.get("timed_spine", {})
    music = data.get("music_selection", {})
    temporal_raw = data.get("temporal_event_indices", [])
    temporal = temporal_raw.get("temporal_event_indices", temporal_raw) if isinstance(temporal_raw, dict) else temporal_raw
    # `project_fps` FIRST, and `frame_rate` is a key nothing in this
    # pipeline has ever produced. The catalog measures the timebase and
    # calls it `project_fps`; step 4.04 was given the edge and the read
    # in #124 ("every consumer used to read its own 30.0 default because
    # no edge carried it") and this consumer was left behind, so every
    # `duration_frames` below was computed at 30.0. On the captain's
    # geo-podcast, which is 23.976 fps, a 400 ms transition became 12
    # frames and played for 500 ms.
    fps = data.get("project_fps", data.get("frame_rate", 30.0))

    # Extract new inputs
    creative_direction = data.get("creative_direction", {})
    brand_effect = data.get("brand_effect", {})

    music_analysis = data.get("music_analysis", {})
    # The V2 spans the buildability retry judges native transitions
    # against: every b-roll assignment and interjection becomes a V2
    # clip, so a native transition abutting a pair of them carries on
    # the b-roll's own track (finding 16). Read defensively - an
    # entry without a usable span is not a span.
    from library.tools.post_bridge_retry import ATTEMPT_KEY
    v2_spans = _v2_spans(data.get("b_roll_assignments"),
                         data.get("b_roll_interjections"))
    result = resolve_transitions(creative, spine, music, temporal, fps,
                                 creative_direction, brand_effect,
                                 music_analysis=music_analysis,
                                 v2_spans=v2_spans,
                                 attempt=data.get(ATTEMPT_KEY))
    json.dump({"transition_spec": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
