#!/usr/bin/env python3
"""
Generate Remotion input props from pipeline subtitle data.

Converts subtitle plan entries into per-card Remotion SubtitleOverlay
prop files. Each karaoke card gets its own props file with subtitle
timings re-based to start at frame 0 (relative to the card's start).

A timeline caption clip holds exactly one karaoke card: the segment
boundaries ARE the card boundaries step 4.01's `split_into_groups`
chose (max words, word-gap, measured pixel fit), so no second rule set
decides where a clip cuts and none can drift from the first. A
transcript row boundary survives as a segment edge only where the
cards tile it - every block edge coincides with a card edge, never
with a cut through a card.

Usage (standalone):
    python generate_remotion_props.py \\
        --subtitle-data /path/to/subtitle_data.json \\
        --output-dir /path/to/output \\
        --total-frames 1380 \\
        --fps 30
"""

import json
import os
import argparse
import sys
from typing import Optional

# Render handles either side of a block so entrance/exit animations have
# room. Trimmed off at placement time - see _source_in_frame below.
SUBTITLE_RENDER_BUFFER_S = 0.5


def _clamp_word_window(raw_start: int, raw_end: int,
                       card_start: int, card_end: int):
    """Bound one word's highlight window to its card, or refuse the sweep loudly.

    Returns `(startFrame, endFrame)`, or `(None, reason)` where the bound
    window has no width. A window with no width can never sweep - the
    renderer's accent phase (`frame >= startFrame && frame < endFrame`)
    is unsatisfiable - so the word is DRAWN WITHOUT A SWEEP (Reel 05
    frame 241: seven across three cards, from aligner pile-up stamps
    step 4.01 grouped without validating). The word is KEPT, never
    re-timed and never omitted: the renderer draws every entry in
    `words`, so omitting one deletes it from the captions entirely -
    which is how "10-man"/"50-person"/"hundred" vanished from Reel 05
    after the 2026-09-19 lane skipped these windows. The reason names
    which way the window collapsed, so the run says what it drew
    unswept and why.
    """
    start = max(card_start, raw_start)
    end = min(card_end, raw_end)
    if end > start:
        return (start, end), ""
    if raw_end <= raw_start:
        reason = (
            f"aligner stamp has no width ({raw_start} -> {raw_end}) - "
            f"pile-up timings no sweep can cross")
    else:
        reason = (
            f"word sits outside its card "
            f"(raw {raw_start} -> {raw_end}, card {card_start} -> "
            f"{card_end}) - the card bound leaves no sweepable width")
    return (None, reason)


def _say_unswept_words(unswept: list, block_pos, card_index) -> None:
    """The loud record for words the card bound left with no sweep.

    One NOTE per card, naming every word, because a word this step
    declined to highlight and did not report is a sweep it falsified
    quietly - the same discipline as step 4.01's stretched-word clamp.
    The words ARE drawn (omitting them deleted Reel 05's numbers);
    only the highlight skips them.
    """
    if not unswept:
        return
    listed = ", ".join(
        f"{text!r} [{reason}]" for text, reason in unswept[:7])
    if len(unswept) > 7:
        listed += f", +{len(unswept) - 7} more"
    print(
        f"NOTE: card (block {block_pos}, card {card_index}) draws "
        f"{len(unswept)} word(s) with no highlight window - the sweep "
        f"cannot cross them, so they render unswept, never highlighted "
        f"and never re-timed: {listed}",
        file=sys.stderr,
    )


def generate_subtitle_props_per_block(
    subtitle_data: dict,
    fps: float = 30,
    *,
    width: int,
    height: int,
    audio_spine: dict = None,
) -> list[dict]:
    """Generate SubtitleOverlay props grouped by caption card.

    `width`/`height` are the DECLARED delivery frame and have no default:
    a default is what let these render vertical onto a landscape timeline
    (project 001's lighter central band). The caller states the frame -
    see library/tools/delivery_format.py.

    `fps` is the exact TIMELINE rate - the reel path passes the master's
    rate, which may be 24000/1001, 24, or another supported value. Frame
    counts below are media frames of a file rendered at this rate, so
    they land 1:1 on the timeline. Rounding it to an integer
    renders media at a different rate than the timeline and costs every
    segment a frame in Resolve's time-mapping (vep-caption-segment-
    off-by-one-frame). Remotion renders fractional fps faithfully.

    Returns a list of prop dicts, one per caption card carrying
    subtitles. Each card's timings are re-based to 0 (relative to the
    card's start time) so the rendered clip can be placed at the card's
    timeline position in Resolve.

    The grouping key is `(spine_block_position, card_index)` - the card
    step 4.01 planned, in the block it planned it in. A plan entry with
    no `card_index` (written before step 4.01 emitted one) falls back
    to per-block grouping, so a stored plan still renders exactly as
    it did: one segment holding the block's cards. Entries are ordered
    by timeline start within a segment, so a segment's bounds always
    read off the cards it holds.
    """
    entries = subtitle_data.get('subtitle_entries', [])
    if not entries:
        return []

    # The caption look comes from step 4.01, which resolves it from the
    # brand template. There is deliberately NO default here any more: this
    # line used to be `subtitle_data.get("style", {...Montserrat 58px...})`,
    # and because 4.01 never wrote the key, every project in the pipeline's
    # life rendered that hardcoded look while four templates declared
    # typography, palettes and subtitle_style that reached nothing. A
    # missing style is now a loud failure rather than a silent house look.
    style = subtitle_data.get('style')
    if not style:
        raise ValueError(
            "subtitle_plan carries no 'style'. Step 4.01 resolves it from "
            "the brand template (library/tools/subtitle_style.py) and must "
            "run before 4.05. Refusing to substitute a default, because a "
            "silent default is what made every template's caption styling "
            "inert."
        )

    # A project may caption each speaker differently. 4.01 resolved one
    # style per speaker and grouped the captions with THAT style's
    # metrics, so rendering them with the shared style would draw cards
    # the grouper never measured. Absent for a project that declared no
    # speaker styles, which then renders exactly as before.
    styles_by_speaker = subtitle_data.get('styles_by_speaker') or {}

    # Group entries by (spine block, card): one rendered segment per
    # karaoke card. The card boundaries are step 4.01's craft partition,
    # so the segmenter reads them rather than re-deciding them - there
    # is no second threshold to drift.
    segment_groups: dict[tuple, list] = {}
    for entry in entries:
        pos = entry.get('spine_block_position', 0)
        segment_groups.setdefault(
            (pos, entry.get('card_index')), []).append(entry)

    structure = []
    if audio_spine:
        structure = audio_spine.get("structure", audio_spine.get("blocks", []))
    block_lookup = {b.get("position", i): b for i, b in enumerate(structure)}

    props_list = []
    for (block_pos, _card_index) in sorted(
            segment_groups.keys(), key=lambda key: str(key)):
        block_entries = sorted(segment_groups[(block_pos, _card_index)],
                               key=lambda e: (e.get('timeline_start', 0),
                                              e.get('timeline_end', 0)))
        
        block_info = block_lookup.get(block_pos)
        # The spine block names the speaker where the edit provides one;
        # an entry carries it too, for a plan whose spine did not.
        block_speaker = ((block_info or {}).get("speaker")
                         or next((e.get("speaker") for e in block_entries
                                  if e.get("speaker")), None))
        block_style = styles_by_speaker.get(block_speaker) or style
        # 4.01 subtitle_entries are already in the timeline domain.
        # No offset should be applied here.

        # Determine this segment's timeline range from its entries -
        # for a card-indexed plan that is exactly one card's display
        # interval, so no card can display past its segment: the
        # segment IS the card's [timeline_start, timeline_end].
        # (Step 4.01's `display_until=block_end` still times the
        # block's last card in the plan; the segment edge is read off
        # the card here rather than re-decided.)
        block_tl_start = min(e['timeline_start'] for e in block_entries)
        block_tl_end = max(e['timeline_end'] for e in block_entries)
        block_dur = block_tl_end - block_tl_start

        if block_dur <= 0:
            continue

        # Render with a small buffer (0.5s) either side so entrance and
        # exit animations have handles. The buffer is RENDER padding only:
        # `_timeline_start`/`_timeline_end` below stay on the block's true
        # content bounds, and `_source_in_frame`/`_source_out_frame` tell
        # the timeline builder to trim the handles off when placing the
        # clip. Placing the padded clip at its padded start is what made
        # every one of the 13 block overlays overlap its neighbour by ~1s.
        buffer = min(SUBTITLE_RENDER_BUFFER_S, block_tl_start)
        render_start = block_tl_start - buffer
        render_end = block_tl_end + SUBTITLE_RENDER_BUFFER_S
        render_dur = render_end - render_start
        total_frames = max(1, round(render_dur * fps))
        head_frames = round(buffer * fps)
        content_frames = max(1, round(block_dur * fps))

        # Re-base subtitle timings relative to render_start
        subtitles = []
        for entry in block_entries:
            start_s = entry['timeline_start'] - render_start
            end_s = entry['timeline_end'] - render_start
            start_frame = round(start_s * fps)
            end_frame = round(end_s * fps)

            # Build per-word timing
            word_timings = []
            unswept_words = []
            entry_words = entry.get('words', [])

            if entry_words:
                # Use actual per-word timing from plan_subtitles (step 4.01),
                # converting timeline seconds to render-relative frames.
                # Every planned word is KEPT: the renderer draws `words`,
                # never the card `text`, so omitting one deletes it from
                # the captions (Reel 05 frame 241: the numbers vanished).
                # A word the card bound leaves with no width is drawn
                # UNSWEPT with a loud record, never re-timed
                # (see _clamp_word_window).
                for wi, w in enumerate(entry_words):
                    w_start_s = w['start'] - render_start
                    w_end_s = w['end'] - render_start
                    word_text = w['word']
                    raw_start = round(w_start_s * fps)
                    raw_end = round(w_end_s * fps)
                    (bounded, reason) = _clamp_word_window(
                        raw_start, raw_end, start_frame, end_frame)
                    if bounded is None:
                        unswept_words.append((word_text, reason))
                        word_timings.append({
                            "word": word_text,
                            "startFrame": max(start_frame, raw_start),
                            "endFrame": min(end_frame, raw_end),
                        })
                        continue
                    word_timings.append({
                        "word": word_text,
                        "startFrame": bounded[0],
                        "endFrame": bounded[1],
                    })
                _say_unswept_words(unswept_words, block_pos, _card_index)
            else:
                # Fallback: distribute words evenly across the subtitle
                # duration when per-word timing is not available. The
                # same rule applies: on a sub-frame card the even split
                # collapses, and a collapsed window is drawn unswept and
                # reported loudly rather than omitted.
                text_words = entry['text'].split()
                duration_frames = end_frame - start_frame
                per_word = max(1, duration_frames // max(len(text_words), 1))
                for wi, tw in enumerate(text_words):
                    word_text = tw
                    raw_start = start_frame + wi * per_word
                    raw_end = min(start_frame + (wi + 1) * per_word,
                                  end_frame)
                    (bounded, reason) = _clamp_word_window(
                        raw_start, raw_end, start_frame, end_frame)
                    if bounded is None:
                        unswept_words.append((word_text, reason))
                        word_timings.append({
                            "word": word_text,
                            "startFrame": max(start_frame, raw_start),
                            "endFrame": min(end_frame, raw_end),
                        })
                        continue
                    word_timings.append({
                        "word": word_text,
                        "startFrame": bounded[0],
                        "endFrame": bounded[1],
                    })
                _say_unswept_words(unswept_words, block_pos, _card_index)

            # Detect emphasis words
            emphasis_words = entry.get('emphasis_words', [])

            subtitles.append({
                "text": entry['text'],
                "startFrame": start_frame,
                "endFrame": end_frame,
                "emphasisWords": emphasis_words,
                "words": word_timings,
                # How far THIS card shrinks so its widest word fits the
                # safe area. 1.0 for almost every card; below 1.0 only
                # when a single unbreakable word is wider than the usable
                # width, which the frame would otherwise clip at both
                # edges. Planned in step 4.01 - see its CaptionFitter.
                "fitScale": entry.get('fit_scale', 1.0),
            })

        props = {
            "subtitles": subtitles,
            "fps": fps,
            "width": width,
            "height": height,
            "durationInFrames": total_frames,
            "style": block_style,
            # Metadata for placement (not consumed by Remotion).
            # timeline bounds are the segment's TRUE content bounds; the
            # source_in/out frames trim the render padding.
            "_block_position": block_pos,
            # Which card of the block this segment holds - the grouping
            # key's second half, carried so a listing says which card a
            # props dict (or a render of it) belongs to. Placement, not
            # pixels: excluded from the reuse digest next to the other
            # `_`-prefixed keys (see NON_DRAWING_PROPS_KEYS in step.py).
            "_card_index": _card_index,
            "_timeline_start": block_tl_start,
            "_timeline_end": block_tl_end,
            "_source_in_frame": head_frames,
            "_source_out_frame": head_frames + content_frames,
            # The spine block this render captions, carried through so
            # the rendered file can be NAMED for what it belongs to
            # rather than for its ordinal within one spine. See
            # library/tools/subtitle_segment_id.py - an ordinal collides
            # the moment a second timeline exists.  `block_info` was
            # already looked up above and previously went unread.
            "_speaker": block_speaker,
            "_source_clip_id": (block_info or {}).get("clip_id"),
            "_source_start": (block_info or {}).get("source_start"),
            "_source_end": (block_info or {}).get("source_end"),
        }
        props_list.append(props)

    return props_list


def main():
    parser = argparse.ArgumentParser(description="Generate Remotion subtitle props")
    parser.add_argument("--subtitle-data", required=True,
                        help="Path to subtitle data JSON (or JSON string)")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for props JSON files")
    parser.add_argument("--fps", type=int, default=30)
    # No defaults: the frame is the declared delivery format, stated by
    # whoever invokes this. A default here is what rendered vertical
    # overlays onto project 001's landscape timeline.
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Load subtitle data (accept path or JSON string)
    if os.path.exists(args.subtitle_data):
        with open(args.subtitle_data) as f:
            subtitle_data = json.load(f)
    else:
        subtitle_data = json.loads(args.subtitle_data)

    # Generate per-card props
    props_list = generate_subtitle_props_per_block(
        subtitle_data,
        fps=args.fps,
        width=args.width,
        height=args.height,
    )

    for props in props_list:
        block_pos = props["_block_position"]
        # One file per segment: several cards of one block would
        # otherwise overwrite each other under the old per-block name.
        # A plan that predates `card_index` keeps the old name, which
        # is also still unique then (one segment per block).
        card_index = props.get("_card_index")
        stem = (f"sub_block_{block_pos}_props.json" if card_index is None
                else f"sub_block_{block_pos}_card{card_index}_props.json")
        props_path = os.path.join(args.output_dir, stem)

        # Remove metadata keys before writing
        clean_props = {k: v for k, v in props.items() if not k.startswith("_")}
        with open(props_path, 'w') as f:
            json.dump(clean_props, f, indent=2)

        print(f"Block {block_pos}: {len(props['subtitles'])} subtitles, "
              f"{props['durationInFrames']}f -> {props_path}")

    print(f"\nGenerated {len(props_list)} subtitle prop files")


if __name__ == "__main__":
    main()
