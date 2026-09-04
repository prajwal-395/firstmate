#!/usr/bin/env python3
"""
Generate Remotion input props from pipeline subtitle data.

Converts subtitle plan entries into per-spine-block Remotion SubtitleOverlay
prop files. Each block gets its own props file with subtitle timings re-based
to start at frame 0 (relative to the block start).

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
from typing import Optional

# Render handles either side of a block so entrance/exit animations have
# room. Trimmed off at placement time - see _source_in_frame below.
SUBTITLE_RENDER_BUFFER_S = 0.5


def generate_subtitle_props_per_block(
    subtitle_data: dict,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    audio_spine: dict = None,
) -> list[dict]:
    """Generate SubtitleOverlay props grouped by spine block.

    Returns a list of prop dicts, one per spine block containing subtitles.
    Each block's subtitles have their frame timings re-based to 0 (relative
    to the block's start time) so the rendered clip can be placed at the
    block's timeline position in Resolve.
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

    # Group entries by spine_block_position
    block_groups: dict[int, list] = {}
    for entry in entries:
        pos = entry.get('spine_block_position', 0)
        block_groups.setdefault(pos, []).append(entry)
        
    structure = []
    if audio_spine:
        structure = audio_spine.get("structure", audio_spine.get("blocks", []))
    block_lookup = {b.get("position", i): b for i, b in enumerate(structure)}

    props_list = []
    for block_pos in sorted(block_groups.keys(), key=str):
        block_entries = block_groups[block_pos]
        
        block_info = block_lookup.get(block_pos)
        # The spine block names the speaker where the edit provides one;
        # an entry carries it too, for a plan whose spine did not.
        block_speaker = ((block_info or {}).get("speaker")
                         or next((e.get("speaker") for e in block_entries
                                  if e.get("speaker")), None))
        block_style = styles_by_speaker.get(block_speaker) or style
        # 4.01 subtitle_entries are already in the timeline domain.
        # No offset should be applied here.

        # Determine block timeline range from the entries
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
            entry_words = entry.get('words', [])

            if entry_words:
                # Use actual per-word timing from plan_subtitles (step 4.01),
                # converting timeline seconds to render-relative frames.
                for wi, w in enumerate(entry_words):
                    w_start_s = w['start'] - render_start
                    w_end_s = w['end'] - render_start
                    word_text = w['word']
                    word_timings.append({
                        "word": word_text,
                        "startFrame": max(start_frame, round(w_start_s * fps)),
                        "endFrame": min(end_frame, round(w_end_s * fps)),
                    })
            else:
                # Fallback: distribute words evenly across the subtitle
                # duration when per-word timing is not available.
                text_words = entry['text'].split()
                duration_frames = end_frame - start_frame
                per_word = max(1, duration_frames // max(len(text_words), 1))
                for wi, tw in enumerate(text_words):
                    word_text = tw
                    word_timings.append({
                        "word": word_text,
                        "startFrame": start_frame + wi * per_word,
                        "endFrame": min(
                            start_frame + (wi + 1) * per_word,
                            end_frame,
                        ),
                    })

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
            # timeline bounds are the block's TRUE content bounds; the
            # source_in/out frames trim the render padding.
            "_block_position": block_pos,
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
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Load subtitle data (accept path or JSON string)
    if os.path.exists(args.subtitle_data):
        with open(args.subtitle_data) as f:
            subtitle_data = json.load(f)
    else:
        subtitle_data = json.loads(args.subtitle_data)

    # Generate per-block props
    props_list = generate_subtitle_props_per_block(
        subtitle_data,
        fps=args.fps,
        width=args.width,
        height=args.height,
    )

    for props in props_list:
        block_pos = props["_block_position"]
        props_path = os.path.join(
            args.output_dir, f"sub_block_{block_pos}_props.json"
        )

        # Remove metadata keys before writing
        clean_props = {k: v for k, v in props.items() if not k.startswith("_")}
        with open(props_path, 'w') as f:
            json.dump(clean_props, f, indent=2)

        print(f"Block {block_pos}: {len(props['subtitles'])} subtitles, "
              f"{props['durationInFrames']}f -> {props_path}")

    print(f"\nGenerated {len(props_list)} subtitle prop files")


if __name__ == "__main__":
    main()
