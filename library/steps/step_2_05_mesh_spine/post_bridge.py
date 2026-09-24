#!/usr/bin/env python3
"""
Step 2.5 Bridge: Enrich Audio Spine with Execution Data

Takes the LLM's creative spine output (which uses passage_ref, text_snippet,
and semantic-level decisions) and enriches it with technical execution data:
- Resolves passage_ref → clip_id, start_time, end_time, word_timestamps
- Injects resolved data from Step 2.3 into each speech block's content

This bridge sits between the LLM's creative output (Step 2.5) and the
deterministic timing calculator (Step 2.6).

Classification: Deterministic / Data Transformation
Idempotent: Yes

It also places the intro / outro / end card the brand template declares
(``content.bookends``), which is the only place they come from - see
``library/tools/bookends.py``.

Input:  {
    "spine": <LLM output from 2.5>,
    "speech_sequence": <resolved output from 2.3>,
    "music_selection": <output from 2.4>,
    "brand_content": <the template's content slots, injected by the runner>,
    "project_folder": <used to resolve declared bookend paths>
}
Output: {
    "audio_spine": <enriched spine ready for 2.6 calc_timing>
}
"""
import json
import sys
import uuid
import os
from library.tools.pipeline_validation import require_keys
from library.tools.spine_contract import (
    BOOKEND_BLOCK_TYPES,
    MUSIC_BLOCK_TYPES,
    PICTURE_BLOCK_TYPES,
    SPEECH_BLOCK_TYPES,
    validate_passage_coverage,
    validate_spine_blocks,
)
from library.tools.bookends import (
    assert_no_invented_bookends,
    declared_bookends,
    insert_bookend_blocks,
    resolve_bookend,
)

# Add parent directories to path so we can import shared tools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from tools.frame_utils import seconds_to_frame


from library.tools.music_bed import BED_KEY
from library.tools.music_bed import describe as describe_bed
from library.tools.music_bed import resolve_bed


def enrich_spine(spine: dict, speech_sequence: dict, music: dict, data: dict = None) -> dict:
    """
    Enrich the LLM's creative spine with execution-layer data.

    `speech_sequence` is always present - step 2.02 runs on every run
    and answers zero transcribed speech with an empty body - but the
    body may be empty. Both are the same promise: no passage_ref will
    resolve, and a spine with no speech blocks is a legal spine. Only a
    speech/hook block on a speechless run fails, by name, like any
    other dangling ref.
    """
    structure = spine.get("structure", [])
    speech_sequence = speech_sequence or {}

    # Build lookup: passage position → resolved passage data.
    #
    # `position` is the ONLY name a spine block may reference. Step 2.2
    # emits one ordered `body_sequence` and names no opener: the closed
    # role vocabulary, and the separately mandated `hook_segment` it used
    # to address as the literal `"hook"`, were withdrawn on the captain's
    # ruling of 2026-09-02. Which passage opens the video is decided HERE,
    # by putting it in a `hook` block - and that block references its
    # passage by position like every other one.
    passage_lookup = {}
    for i, p in enumerate(speech_sequence.get("body_sequence", []), start=1):
        passage_lookup[i] = p

    enriched_blocks = []

    unresolved = []

    for block in structure:
        enriched = dict(block)  # shallow copy
        block_type = block.get("block_type")
        content = block.get("content") or {}

        # Every block carries the full contract key set (see
        # library/tools/spine_contract.py). Non-speech blocks carry None -
        # the keys are never absent, so consumers can read them directly
        # instead of guessing where the clip reference lives.
        enriched["clip_id"] = None
        enriched["source_clip_id"] = None
        enriched["source_start"] = None
        enriched["source_end"] = None
        enriched["word_timestamps"] = []
        enriched["alignment_method"] = None

        if block_type in SPEECH_BLOCK_TYPES:
            # Generate link_group_id for A/V synchronization.
            # This allows the XMEML generator to pair video and audio
            # clipitems using reciprocal <link> blocks.
            enriched["link_group_id"] = str(uuid.uuid4())

            passage_ref = content.get("passage_ref")
            passage = passage_lookup.get(passage_ref) if passage_ref is not None else None
            if passage is None:
                unresolved.append(
                    f"block position {block.get('position')!r} references "
                    f"passage_ref {passage_ref!r}, which is not a "
                    f"position in the speech_sequence body_sequence "
                    f"(available: {sorted(passage_lookup, key=str)})"
                )
                continue

            clip_id = passage["clip_id"]
            src_start = passage["source_start"]
            src_end = passage["source_end"]
            words = passage["word_timestamps"]

            enriched["content"] = {
                "passage_ref": passage_ref,
                "text": passage.get("text", ""),
                "clip_id": clip_id,
                "source_start": src_start,
                "source_end": src_end,
                "v1_source_in": src_start,
                "v1_source_out": src_end,
                "source_duration": passage.get("duration_seconds"),
                "alignment_method": passage["alignment_method"],
                "word_timestamps": words,
            }
            # Block-level contract fields. These are what every downstream
            # step reads; `content` is kept for prompt/context rendering.
            enriched["clip_id"] = clip_id
            enriched["source_clip_id"] = clip_id
            enriched["source_start"] = src_start
            enriched["source_end"] = src_end
            enriched["word_timestamps"] = words
            enriched["alignment_method"] = passage["alignment_method"]

            # ALWAYS sync block duration to actual speech.
            # The word timestamps define the exact duration —
            # the LLM's creative target should never override.
            # If block is longer, unselected audio bleeds through.
            # If block is shorter, speech gets clipped.
            src_dur = src_end - src_start
            block_dur = enriched.get("duration_seconds", 0)
            if abs(src_dur - block_dur) > 0.05:
                enriched["duration_seconds"] = round(src_dur, 3)
                print(
                    f"  Block [{enriched.get('position')}]: "
                    f"synced duration {block_dur:.2f}s → "
                    f"{src_dur:.2f}s (word boundaries)",
                    file=sys.stderr,
                )
        elif block_type in PICTURE_BLOCK_TYPES:
            # A picture-led block names its own clip span - the model's
            # `content.clip_id/source_start/source_end`, lifted onto the
            # block like a passage's timings, because assign_aroll and
            # compile_manifest read the block, never the content. No
            # words and no alignment: the moment says nothing.
            # Duration syncs to the span for the same reason speech
            # does - the timeline plays the span for the block's
            # duration, so a disagreement truncates or stretches it.
            pic_clip = content.get("clip_id")
            pic_start = content.get("source_start")
            pic_end = content.get("source_end")
            if pic_clip is None or pic_start is None or pic_end is None:
                unresolved.append(
                    f"block position {block.get('position')!r} is a "
                    f"picture block but names no clip span "
                    f"(content.clip_id/source_start/source_end)"
                )
                continue
            enriched["content"] = {
                "clip_id": pic_clip,
                "source_start": pic_start,
                "source_end": pic_end,
            }
            enriched["clip_id"] = pic_clip
            enriched["source_clip_id"] = pic_clip
            enriched["source_start"] = pic_start
            enriched["source_end"] = pic_end
            try:
                span_dur = float(pic_end) - float(pic_start)
            except (TypeError, ValueError):
                span_dur = None
            block_dur = enriched.get("duration_seconds", 0)
            if (span_dur is not None and span_dur > 0
                    and abs(span_dur - (block_dur or 0)) > 0.05):
                enriched["duration_seconds"] = round(span_dur, 3)
                print(
                    f"  Block [{enriched.get('position')}]: "
                    f"synced duration {block_dur:.2f}s → "
                    f"{span_dur:.2f}s (picture span)",
                    file=sys.stderr,
                )
        elif block_type in MUSIC_BLOCK_TYPES:
            # A music-led moment carries its track reference (if any) in
            # content, as written - the conducted bed resolves what
            # actually plays. Nothing to lift: the block keeps the
            # non-speech shape, and B-roll covers its picture.
            enriched["content"] = dict(content) if content else None
        else:
            enriched["content"] = dict(content) if content else None

        # Inject music reference
        if music and not enriched.get("music_track"):
            enriched["music_track"] = "music_01"

        enriched_blocks.append(enriched)

    if unresolved:
        raise ValueError(
            "mesh_spine could not resolve "
            f"{len(unresolved)} spine block(s) to speech passages or "
            f"picture spans:\n  - "
            + "\n  - ".join(unresolved)
        )

    # No content loss, checked exactly: every body passage must appear
    # in the spine, and no passage twice across speech blocks. Which
    # passage opens, continues or closes is the editorial decision; the
    # integer bookkeeping around it is not. A hook reusing a body
    # passage is permitted (see validate_passage_coverage). An empty
    # body obligates nothing - a speechless spine covers zero of zero.
    validate_passage_coverage(
        enriched_blocks, len(speech_sequence.get("body_sequence", [])))

    # ── Bookends: intro / outro / end card ──
    # Only what the brand template declares (Q7). A template that declares
    # nothing adds nothing here, which is every template unless someone
    # opted in. The LLM never writes these blocks: the card a video shows
    # is a brand decision, and inventing one per run is how a client's end
    # card ends up on a series video.
    #
    # A planned card block REFUSES the step, by name. It used to be
    # dropped with a line on stderr, and a log line is read by nobody
    # forty minutes into an unattended run - the plan around the card was
    # written knowing the card was there, so the drop shipped an edit
    # designed for a moment it no longer had.
    assert_no_invented_bookends(enriched_blocks)
    resolved_bookends = [
        resolve_bookend(d, (data or {}).get("project_folder", ""))
        for d in declared_bookends((data or {}).get("brand_content"))
    ]
    if resolved_bookends:
        enriched_blocks = insert_bookend_blocks(
            enriched_blocks, resolved_bookends)
        for r in resolved_bookends:
            print(
                f"  Bookend [{r['slot']}]: {r['duration_seconds']}s from "
                f"{r['mode']} {r['composition'] or r['asset']}",
                file=sys.stderr,
            )

    # ── The captain's edits: drops, re-derived ──
    # A drop_fragment names spoken words ("drop the 'so what do they'
    # fragment"), and it applies on EVERY rebuild here - after the LLM's
    # structure is enriched and before timeline positions are
    # recalculated, so everything after the cut moves up by exactly the
    # removed seconds. Whole-value supply (the entire speech_sequence
    # handed over) is unreadable and goes stale upstream; a word-anchored
    # delta survives re-transcription, re-cutting and renumbering, and an
    # anchor that matches nothing reports STALE loudly rather than
    # vanishing (library/tools/captain_edits.py).
    captain_applied, captain_stale = [], []
    try:
        from library.tools import captain_edits as _edits
        _project = (data or {}).get("project_folder", "")
        _all = _edits.load_edits(_project) if _project else []
        if any(e.get("kind") == "drop_fragment" for e in _all):
            enriched_blocks, captain_applied, captain_stale = \
                _edits.apply_drop_fragments(enriched_blocks, _all)
            for record in captain_applied:
                print(
                    f"  Captain edit: dropped {record['blocks_removed']} "
                    f"({record['seconds_removed']}s) saying "
                    f"{record['anchor_phrase']!r} - "
                    f"{record['reason']}",
                    file=sys.stderr,
                )
            if captain_stale:
                _edits.report_stale(captain_stale)
    except Exception as exc:  # noqa: BLE001 - an edit pass must never
        # refuse a spine: unapplied edits are reported, not fatal.
        print(f"WARNING: captain edits could not apply ({exc}); "
              f"continuing without them.", file=sys.stderr)

    # Recalculate timeline positions from (potentially extended) durations.
    # Block extensions shift all subsequent blocks forward.
    cursor = 0.0
    for b in enriched_blocks:
        b["timeline_start"] = round(cursor, 3)
        dur = b.get("duration_seconds", 0)
        b["timeline_end"] = round(cursor + dur, 3)
        cursor += dur

    # ── Frame conversion ──
    # Add integer frame positions using global timeline seconds.
    # This prevents frame rounding drift from accumulating over many blocks.
    # All steps from Phase 3 onward should read the _frame fields.
    fps = spine.get("frame_rate", 30.0)
    for b in enriched_blocks:
        start_f = seconds_to_frame(b["timeline_start"], fps)
        end_f = seconds_to_frame(b["timeline_end"], fps)
        b["timeline_start_frame"] = start_f
        b["timeline_end_frame"] = end_f
        b["duration_frames"] = end_f - start_f

    frame_cursor = enriched_blocks[-1]["timeline_end_frame"] if enriched_blocks else 0

    # Recalculate total duration from enriched blocks
    total_dur = sum(b.get("duration_seconds", 0) for b in enriched_blocks)

    from library.tools.duration_targets import get_target_duration_zone
    target_duration_zone = get_target_duration_zone(data or {})

    # The duration zone is a target for the CONTENT the spine planned.
    # A declared bookend is a fixed brand decision the planner never chose,
    # so charging its seconds against the zone would fail a spine that hit
    # its target exactly, for the crime of having an end card.
    bookend_dur = sum(
        b.get("duration_seconds", 0) for b in enriched_blocks
        if b.get("block_type") in BOOKEND_BLOCK_TYPES
    )

    # The single gate on the spine contract. Every creative step downstream
    # reads these blocks directly, so a malformed spine stops here rather
    # than degrading silently in five different consumers.
    validate_spine_blocks(
        enriched_blocks, total_dur - bookend_dur, target_duration_zone)

    # ── The bed the spine conducts ──
    # This is the first step that has the spine, the chosen tracks and the
    # music analysis together, so it is where "this piece here, that piece
    # there" can be said at all. Step 2.04 chose the pieces in SOURCE
    # time; this places them, anchored to a spine block position. A plan
    # that declares nothing here gets the one-section bed every run before
    # this got. See library/tools/music_bed.py.
    spine_out = {
        "total_estimated_duration_seconds": round(total_dur, 2),
        "total_duration_frames": frame_cursor,
        "frame_rate": fps,
        "structure": enriched_blocks,
        # No `music_selection` here. The selector's answer - how each
        # track was found and why it was chosen - used to be copied
        # into both spines, ~24 kB per run into pipeline_data.json
        # and into every prompt routed a spine, while no code ever
        # read the nested copy. Captain's ruling, 2026-09-16: the
        # record is KEPT, in its own file
        # (library/tools/music_audit_trail.py, written by step 2.04
        # into its own directory), and the spines carry only the
        # conducted bed below. Every operational consumer reads the
        # top-level `music_selection`, which still reaches this step
        # and every step that needs it.
        # What the captain's deltas did on this build, or [] - a run
        # that cannot say which edits are in force cannot be reviewed.
        "captain_edits_applied": captain_applied,
        "captain_edits_stale": captain_stale,
    }
    declared_bed = spine.get(BED_KEY) or (data or {}).get(BED_KEY)
    if declared_bed:
        spine_out[BED_KEY] = declared_bed
    # Resolve it HERE, so a bed that cannot be played is refused by the
    # post-bridge that can carry the violation back to the model
    # (library/tools/post_bridge_retry.py) rather than four steps later in
    # compile_manifest, where the only recovery is a re-run.
    bed = resolve_bed(music, spine_out, round(total_dur, 3))
    print("  " + describe_bed(bed), file=sys.stderr)

    return {"audio_spine": spine_out}


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    # C3 fix: Accept LLM output format. The LLM outputs {structure: [...],
    # total_estimated_duration_seconds: ...} directly, not nested under a
    # "spine" key. Support both formats for robustness.
    #
    # speech_sequence is REQUIRED and always present: step 2.02 runs on
    # every run, emitting an empty body for a speechless edit - the
    # manifest declares the same hard edge, so --only runs pull its
    # producer in.
    require_keys(data, ["speech_sequence", "music_selection"], "step_2_05_mesh_spine/post_bridge.py")
    if "spine" in data:
        spine_data = data["spine"]
    elif "structure" in data:
        # LLM output: structure is at root level
        spine_data = data
    else:
        # Generate dummy structure for auto mode
        seq = data.get("speech_sequence", {})
        dummy_struct = []
        for i, p in enumerate(seq.get("body_sequence", []), start=1):
            dummy_struct.append({
                "position": i,
                "block_type": "speech",
                "content": {"passage_ref": i}
            })
        spine_data = {"structure": dummy_struct}
    if not isinstance(spine_data, dict):
        raise ValueError("spine data must be a dictionary")
    require_keys(spine_data, ["structure"], "spine data")
    if not isinstance(spine_data["structure"], list):
        raise ValueError("spine.structure must be a list")

    # C3 fix: Use spine_data resolved above (handles both LLM direct output
    # and wrapped formats).
    spine = spine_data

    speech = data.get("speech_sequence") or {}
    music = data.get("music_selection", {})

    result = enrich_spine(spine, speech, music, data)
    result["timed_spine"] = result["audio_spine"]
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
