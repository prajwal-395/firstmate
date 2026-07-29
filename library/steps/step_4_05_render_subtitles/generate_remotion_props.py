#!/usr/bin/env python3
"""
Generate Remotion input props from pipeline subtitle/VFX data.

Converts subtitle_data.json + rendered_transcript.json → Remotion SubtitleOverlay
and FourthWallOverlay prop files for rendering to ProRes 4444 with alpha.

Usage:
    python generate_remotion_props.py \\
        --subtitle-data /path/to/subtitle_data.json \\
        --transcript /path/to/rendered_transcript.json \\
        --vfx-plan /path/to/assembly_manifest.json \\
        --output-dir /path/to/output \\
        --total-frames 1380 \\
        --fps 30
"""

import json
import os
import sys
import argparse
from typing import Optional


def _find_word_timing(word_text, word_segments, tolerance=0.5):
    """Find word-level timing from rendered transcript segments.

    Args:
        word_text: Word to search for (case-insensitive)
        word_segments: List of word dicts from transcript
        tolerance: Maximum time difference to consider a match

    Returns:
        (start_seconds, end_seconds) or None
    """
    clean = word_text.lower().strip().rstrip('.,!?;:')
    for w in word_segments:
        w_clean = w.get('word', '').lower().strip().rstrip('.,!?;:')
        if w_clean == clean and 'start' in w and 'end' in w:
            return (w['start'], w['end'])
    return None


def generate_subtitle_props(
    subtitle_data: dict,
    transcript: Optional[dict] = None,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    total_frames: Optional[int] = None,
) -> dict:
    """Generate SubtitleOverlay props for Remotion.

    Takes subtitle_data.json (with timeline_start/end) and optionally
    rendered_transcript.json (with word-level timing) to produce
    the Remotion SubtitleOverlay schema.
    """
    entries = subtitle_data.get('subtitle_entries', [])
    if not entries:
        return {}

    # Build flat word timing list from transcript
    all_words = []
    if transcript:
        for seg in transcript.get('segments', []):
            for w in seg.get('words', []):
                if 'start' in w and 'end' in w:
                    all_words.append(w)

    # If no total_frames, compute from last subtitle end
    if total_frames is None:
        last_end = max(e.get('timeline_end_frame', e.get('timeline_end', 0) * fps)
                       for e in entries)
        total_frames = int(last_end) + fps  # add 1s padding

    subtitles = []
    word_cursor = 0  # track position in all_words for efficient matching

    for entry in entries:
        start_frame = entry.get('timeline_start_frame',
                                round(entry['timeline_start'] * fps))
        end_frame = entry.get('timeline_end_frame',
                              round(entry['timeline_end'] * fps))
        text = entry['text']

        # Split text into words and find timing for each
        text_words = text.split()
        word_timings = []
        emphasis_words = []

        if all_words:
            # Find matching words in transcript
            tl_start_s = start_frame / fps
            tl_end_s = end_frame / fps

            # Search from cursor for words in the right time range
            matched_words = []
            search_start = max(0, word_cursor - 5)
            for wi in range(search_start, len(all_words)):
                w = all_words[wi]
                w_start = w.get('start', 0)
                w_end = w.get('end', 0)

                # Check if this word falls within our subtitle time range
                if w_start >= tl_start_s - 0.5 and w_end <= tl_end_s + 0.5:
                    w_text = w.get('word', '')
                    # Check if this word is in our subtitle text
                    w_clean = w_text.lower().strip().rstrip('.,!?;:')
                    for tw in text_words:
                        tw_clean = tw.lower().strip().rstrip('.,!?;:')
                        if tw_clean == w_clean:
                            matched_words.append({
                                "word": tw,
                                "startFrame": round(w_start * fps),
                                "endFrame": round(w_end * fps),
                            })
                            word_cursor = wi + 1
                            break

                if w_start > tl_end_s + 1.0:
                    break

            if matched_words:
                word_timings = matched_words
            else:
                # Fallback: evenly distribute words across the time range
                duration_frames = end_frame - start_frame
                per_word = max(1, duration_frames // len(text_words))
                for wi, tw in enumerate(text_words):
                    word_timings.append({
                        "word": tw,
                        "startFrame": start_frame + wi * per_word,
                        "endFrame": start_frame + (wi + 1) * per_word,
                    })
        else:
            # No transcript — evenly distribute
            duration_frames = end_frame - start_frame
            per_word = max(1, duration_frames // max(len(text_words), 1))
            for wi, tw in enumerate(text_words):
                word_timings.append({
                    "word": tw,
                    "startFrame": start_frame + wi * per_word,
                    "endFrame": start_frame + (wi + 1) * per_word,
                })

        # Detect emphasis words (longer words, proper nouns, emotionally significant)
        for tw in text_words:
            clean = tw.strip().rstrip('.,!?;:')
            if (len(clean) >= 6 or clean[0].isupper()  # long or capitalized
                    or clean.lower() in {'every', 'single', 'camera', 'announcement',
                                         'inspire', 'videography', 'photography',
                                         'birthday', 'challenge', 'casey', 'vlog'}):
                emphasis_words.append(tw)

        subtitles.append({
            "text": text,
            "startFrame": start_frame,
            "endFrame": end_frame,
            "emphasisWords": emphasis_words,
            "words": word_timings,
        })

    return {
        "subtitles": subtitles,
        "fps": fps,
        "width": width,
        "height": height,
        "durationInFrames": total_frames,
    }


def generate_fourth_wall_props(
    manifest: dict,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    total_frames: Optional[int] = None,
) -> dict:
    """Generate FourthWallOverlay props from assembly manifest VFX entries."""
    vfx_entries = manifest.get('vfx', [])
    project = manifest.get('project', {})

    if total_frames is None:
        total_frames = round(project.get('duration_seconds', 46.0) * fps)

    # Defaults
    props = {
        "fps": fps,
        "width": width,
        "height": height,
        "durationInFrames": total_frames,
        "fontFamily": "'Nanum Pen Script', cursive",
        # Night card
        "nightCardText": "",
        "nightCardColor": "#D4A34A",
        "nightCardStartFrame": 0,
        "nightCardDurationFrames": 0,
        # Counter
        "counterText": "",
        "counterColor": "#00BFFF",
        "counterStartFrame": 0,
        "counterDurationFrames": 0,
        # Closing lines
        "closingLine1Text": "",
        "closingLine1Color": "#00BFFF",
        "closingLine1StartFrame": 0,
        "closingLine2Text": "",
        "closingLine2Color": "#D4A34A",
        "closingLine2StartFrame": 0,
    }

    for vfx in vfx_entries:
        vfx_type = vfx.get('type', '')
        text = vfx.get('text', '')
        start_s = vfx.get('timeline_start', 0)
        end_s = vfx.get('timeline_end', start_s + 2)
        start_f = round(start_s * fps)
        dur_f = round((end_s - start_s) * fps)

        if vfx_type == 'text_overlay':
            if 'WALL' in text.upper() or 'THROUGH' in text.upper():
                # Night card / title card
                props["nightCardText"] = text
                props["nightCardStartFrame"] = start_f
                props["nightCardDurationFrames"] = dur_f
            elif '/' in text:
                # Counter (e.g., "1 / 100")
                props["counterText"] = text
                props["counterStartFrame"] = start_f
                props["counterDurationFrames"] = dur_f

    return props


def main():
    parser = argparse.ArgumentParser(description="Generate Remotion props")
    parser.add_argument("--subtitle-data", required=True,
                        help="Path to subtitle_data.json")
    parser.add_argument("--transcript",
                        help="Path to rendered_transcript.json (for word timing)")
    parser.add_argument("--manifest",
                        help="Path to assembly_manifest.json (for VFX/motion graphics)")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for props JSON files")
    parser.add_argument("--total-frames", type=int, default=None,
                        help="Total timeline frames")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Load subtitle data
    with open(args.subtitle_data) as f:
        subtitle_data = json.load(f)

    # Load transcript if available
    transcript = None
    if args.transcript and os.path.exists(args.transcript):
        with open(args.transcript) as f:
            transcript = json.load(f)

    # Generate subtitle props
    sub_props = generate_subtitle_props(
        subtitle_data, transcript,
        fps=args.fps, width=args.width, height=args.height,
        total_frames=args.total_frames,
    )

    sub_path = os.path.join(args.output_dir, "subtitleProps.json")
    with open(sub_path, 'w') as f:
        json.dump(sub_props, f, indent=2)
    print(f"✓ SubtitleOverlay props: {sub_path}")
    print(f"  {len(sub_props.get('subtitles', []))} subtitle entries")
    print(f"  {sub_props.get('durationInFrames', 0)} frames @ {args.fps}fps")

    # Generate FourthWallOverlay props if manifest provided
    if args.manifest and os.path.exists(args.manifest):
        with open(args.manifest) as f:
            manifest = json.load(f)

        fw_props = generate_fourth_wall_props(
            manifest,
            fps=args.fps, width=args.width, height=args.height,
            total_frames=args.total_frames,
        )

        fw_path = os.path.join(args.output_dir, "fourthWallProps.json")
        with open(fw_path, 'w') as f:
            json.dump(fw_props, f, indent=2)
        print(f"\n✓ FourthWallOverlay props: {fw_path}")
        vfx_count = sum(1 for v in manifest.get('vfx', []))
        print(f"  {vfx_count} VFX entries processed")


if __name__ == "__main__":
    main()
