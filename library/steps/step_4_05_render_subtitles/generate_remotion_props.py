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
        # 4.01 subtitle_entries are already in the timeline domain.
        # No offset should be applied here.

        # Determine block timeline range from the entries
        block_tl_start = min(e['timeline_start'] for e in block_entries)
        block_tl_end = max(e['timeline_end'] for e in block_entries)
        block_dur = block_tl_end - block_tl_start

        if block_dur <= 0:
            continue

        # Add a small buffer (0.5s) before first and after last subtitle
        # so animations have room to entrance/exit
        buffer = 0.5
        render_start = max(0, block_tl_start - buffer)
        render_end = block_tl_end + buffer
        render_dur = render_end - render_start
        total_frames = max(1, round(render_dur * fps))

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
            })

        props = {
            "subtitles": subtitles,
            "fps": fps,
            "width": width,
            "height": height,
            "durationInFrames": total_frames,
            "style": subtitle_data.get("style", {
                "fontFamily": "Montserrat",
                "fontColor": "#FFFFFF",
                "accentColor": "#FBF0B8",
                "position": "bottom",
                "outlineColor": "#000000",
                "outlineWidth": 4,
                "fontSize": 58
            }),
            # Metadata for placement (not consumed by Remotion)
            "_block_position": block_pos,
            "_timeline_start": render_start,
            "_timeline_end": render_end,
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
