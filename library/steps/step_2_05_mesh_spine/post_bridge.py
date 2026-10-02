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
from library.tools.plan_keys import refuse_unknown_keys

# Add parent directories to path so we can import shared tools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from tools.frame_utils import seconds_to_frame


from library.tools.music_bed import BED_KEY
from library.tools.music_bed import describe as describe_bed
from library.tools.music_bed import resolve_bed


# The block keys this step reads or forwards. Anything else on a block
# is REFUSED by `refuse_unknown_keys` in `enrich_spine`, never dropped:
# an unread key is how a probe's SFX `at_word` landed 3.06 s early on
# the block start. `position`, `block_type`, `duration_seconds` and
# `content` are read below; `music_track` is read for its presence;
# `music_behavior` is forwarded on the enriched blocks to the readers
# that decide the mix (5.02), the manifest (5.04) and render QA;
# `visual_note` is the plan's guidance for Phase 3, read by the 4.03
# and 4.04 pre-bridges;
# `intentional_black_beat`/`black_beat_reason` are the plan's declared
# hole, read by the spine contract, the manifest coverage assertion and
# render QA.
SPINE_BLOCK_KEYS = frozenset({
    "position",
    "block_type",
    "duration_seconds",
    "content",
    "music_track",
    "music_behavior",
    "visual_note",
    "intentional_black_beat",
    "black_beat_reason",
})

# The conducted-bed entry keys `music_bed.resolve_bed` reads. Anything
# else on an entry is refused alongside the blocks: the resolver
# rebuilds each entry from these keys, so an extra one would vanish
# silently into the bed. `ends_at_block` / `end_offset_*` end a piece
# early (rung 7, SD3.2: "music out, then the last line dry");
# `fade_out_*` ramps its last seconds (E3: seconds or frames, as the
# request states them).
BED_ENTRY_KEYS = frozenset({
    "track",
    "source_in",
    "starts_at_block",
    "crossfade_seconds",
    "ends_at_block",
    "end_offset_seconds",
    "end_offset_frames",
    "fade_out_seconds",
    "fade_out_frames",
    "why",
})

# The pacing-window keys the post-bridge reads. Anything else on a
# window is refused beside the blocks: an unread key is how a probe's
# SFX `at_word` landed 3.06 s early on the block start.
PACING_WINDOW_KEYS = frozenset({
    "start_block",
    "end_block",
    "asl_seconds",
    "feel",
})


def _resolve_pacing(pacing, enriched_blocks: list) -> list:
    """The validated ASL windows the plan states, or [].

    Rung 7 (PA3.2, PA2.2): "average shot length around 2.5s in the
    opening 20s, then let it relax to ~5s" rides as windows over spine
    block positions - `[{start_block, end_block, asl_seconds}]` - which
    `compile_manifest` measures the built picture against into
    `pacing_report` (report-only: a target is a target, and failing a
    build on a missed one would be a gate failing correct output).
    E3's other case is a `feel` word (for example "accelerating") on
    the same addressed window. Keep it verbatim; do not turn a feel
    request into a made-up ASL number. A window states exactly one of
    `asl_seconds` and `feel`.

    Refuses (ValueError, travelling the post-bridge retry path) where a
    window names no block, runs backwards, states no positive ASL, or
    overlaps another window - two targets for one stretch is an
    ambiguous spec. Gaps between windows are unmeasured stretches,
    never an error.
    """
    if pacing is None:
        return []
    if not isinstance(pacing, list):
        raise ValueError(
            "mesh_spine `pacing` must be a list of "
            "{start_block, end_block, asl_seconds} windows; got "
            f"{type(pacing).__name__}")
    refuse_unknown_keys(pacing, PACING_WINDOW_KEYS,
                         step="mesh_spine", plan="pacing")
    positions = [str(b.get("position")) for b in enriched_blocks]
    resolved = []
    for index, window in enumerate(pacing):
        start = window.get("start_block")
        end = window.get("end_block")
        asl = window.get("asl_seconds")
        feel = window.get("feel")
        if str(start) not in positions:
            raise ValueError(
                f"mesh_spine pacing window {index} starts at block "
                f"{start!r}, which is not a position on the spine "
                f"(blocks: {', '.join(positions)})")
        if str(end) not in positions:
            raise ValueError(
                f"mesh_spine pacing window {index} ends at block "
                f"{end!r}, which is not a position on the spine "
                f"(blocks: {', '.join(positions)})")
        if positions.index(str(end)) < positions.index(str(start)):
            raise ValueError(
                f"mesh_spine pacing window {index} runs backwards: "
                f"{start!r} comes after {end!r} on the spine")
        if (asl is None) == (feel is None):
            raise ValueError(
                f"mesh_spine pacing window {index} must state exactly "
                "one of asl_seconds or feel")
        row = {"start_block": start, "end_block": end}
        if asl is not None:
            if (isinstance(asl, bool)
                    or not isinstance(asl, (int, float)) or asl <= 0):
                raise ValueError(
                    f"mesh_spine pacing window {index} states "
                    f"asl_seconds {asl!r}, which is not a positive "
                    "number of seconds")
            row["asl_seconds"] = float(asl)
        else:
            if not isinstance(feel, str) or not feel.strip():
                raise ValueError(
                    f"mesh_spine pacing window {index} states feel "
                    f"{feel!r}, which is not a non-empty feel word")
            row["feel"] = feel.strip()
        resolved.append(row)
    for first, second in zip(resolved, resolved[1:]):
        if (positions.index(str(second["start_block"]))
                <= positions.index(str(first["end_block"]))):
            raise ValueError(
                f"mesh_spine pacing windows overlap: "
                f"{first['start_block']!r}-{first['end_block']!r} and "
                f"{second['start_block']!r}-{second['end_block']!r} "
                f"cover the same stretch - one stretch, one target")
    return resolved


def _attach_handles(blocks: list, catalog, fps: float) -> None:
    """Write `head_handle_frames` / `tail_handle_frames` onto each block
    cut from source media, in place. See the call site for the rule."""
    if not catalog:
        return
    entries = catalog.values() if isinstance(catalog, dict) else catalog
    durations = {}
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("clip_id") is None:
            continue
        try:
            durations[str(entry["clip_id"])] = float(
                entry.get("duration_seconds"))
        except (TypeError, ValueError):
            continue
    for b in blocks:
        clip_id = b.get("clip_id")
        if clip_id is None:
            continue
        try:
            src_start = float(b["source_start"])
            src_end = float(b["source_end"])
        except (TypeError, ValueError):
            continue
        total = durations.get(str(clip_id))
        if total is None:
            continue
        b["head_handle_frames"] = seconds_to_frame(
            max(0.0, src_start), fps)
        b["tail_handle_frames"] = seconds_to_frame(
            max(0.0, total - src_end), fps)


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
    # Block and bed keys nothing downstream reads are refused before
    # anything resolves - the refusal travels the post-bridge retry path
    # so the model re-plans instead of the spine carrying a key nobody
    # reads (or the bed silently dropping one it rebuilds without).
    refuse_unknown_keys(spine.get("structure", []), SPINE_BLOCK_KEYS,
                         step="mesh_spine", plan="structure")
    refuse_unknown_keys(spine.get(BED_KEY) or (data or {}).get(BED_KEY) or [],
                         BED_ENTRY_KEYS, step="mesh_spine", plan=BED_KEY)
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
    #
    # Rung 7 (finding 13): the cursor runs in FRAMES, and the seconds
    # are derived from it - so every boundary sits exactly on a frame.
    # The cursor used to accumulate rounded seconds while the frame
    # fields rounded each edge independently, and the two roundings of
    # one edge disagreed: a cut at frame 767 read as outside a block
    # starting at 25.576s, and a frame-stated anchor was refused for
    # landing between them. Downstream readers of the seconds now get
    # frame-true values; readers of the frames are unchanged.
    fps = spine.get("frame_rate", 30.0)
    cursor_f = 0
    for b in enriched_blocks:
        dur_f = seconds_to_frame(b.get("duration_seconds", 0), fps)
        b["timeline_start_frame"] = cursor_f
        b["timeline_start"] = round(cursor_f / fps, 3)
        cursor_f += dur_f
        b["timeline_end_frame"] = cursor_f
        b["timeline_end"] = round(cursor_f / fps, 3)
        b["duration_frames"] = dur_f

    frame_cursor = cursor_f

    # ── Head/tail handles (rung 7, finding 14) ──
    # How much source media each played span leaves unplayed on either
    # side, in frames: the headroom a trim can extend into without
    # restating the spine (CT1.3's "hold the wide longer" is an
    # extension into the tail handle, not a new picture block).
    # Measured off the catalog's media durations, clamped at zero - a
    # span past the file's end is a defect upstream, not negative
    # headroom. Absent where the catalog is not routed: unknown
    # headroom reads as absent, never as zero.
    _attach_handles(enriched_blocks, (data or {}).get("clip_catalog"),
                    fps)

    # ── Pacing targets (rung 7, PA3.2/PA2.2) ──
    # ASL windows over block positions, validated against the enriched
    # structure (post-bookend, post-captain-edit: the positions the
    # plan actually produced). `compile_manifest` measures the built
    # picture against them into `pacing_report`, report-only.
    pacing_windows = _resolve_pacing(spine.get("pacing"),
                                     enriched_blocks)

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
        # Rung 7: the ASL windows the plan stated ([] for feel-word
        # pacing), measured by `compile_manifest` into `pacing_report`.
        "pacing": pacing_windows,
    }
    if spine.get("max_words") is not None:
        max_words = spine["max_words"]
        if (isinstance(max_words, bool) or not isinstance(max_words, int)
                or max_words < 1):
            raise ValueError(
                "mesh_spine max_words must be a whole number of words "
                "per caption card, at least 1")
        spine_out["max_words"] = max_words
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


def resolve_spine(data: dict) -> dict:
    """Mesh the model's spine against the speech and the music: the
    step's whole post-bridge, `audio_spine` and `timed_spine` out."""
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
    return result


def main():
    data = json.loads(sys.stdin.read())
    json.dump(resolve_spine(data), sys.stdout, indent=2)


if __name__ == "__main__":
    main()
