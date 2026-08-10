#!/usr/bin/env python3
"""
Step 4.1: Generate Subtitles

Converts speech segments from the audio spine into styled, timed subtitle
entries. Uses word-level timestamps from WhisperX forced alignment (produced
by Step 1.04, enriched via Bridge 2.02) to assign precise on/off times —
NOT proportional estimation.

Each display group (3-6 words) appears when its first word is spoken and
disappears when the next group appears.

Classification: Deterministic / Data Transformation
Archetype: Data Transformation
Idempotent: Yes

Input:  {
    "audio_spine": { structure: [...] },
    "speech_sequence": { hook_segment, body_sequence (with word_timestamps) }
}
Output: { "subtitle_entries": [...], "total_subtitles": int }
"""
import json
import re
import sys

# ── Minimum display duration (seconds) ──
# Matches Palmier Pro's AppTheme.Caption.minDisplayDuration.
# Subtitles shorter than this are extended; subsequent groups
# are shifted forward to prevent overlap.
MIN_DISPLAY_DURATION = 0.7


# ── Font-agnostic visual-fit measurement ──
# Used to check whether subtitle text fits on screen at a given
# font. Falls back to character-count heuristic if PIL/Pillow
# is not installed or the font file can't be loaded.

_font_cache = {}


def _load_font(font_path, font_size):
    """Load a TrueType font, caching for reuse. Returns None if unavailable."""
    key = (font_path, font_size)
    if key not in _font_cache:
        try:
            from PIL import ImageFont
            _font_cache[key] = ImageFont.truetype(font_path, font_size)
        except (OSError, ImportError):
            _font_cache[key] = None
    return _font_cache[key]


def text_fits_on_screen(text, font_path, font_size, canvas_width,
                        max_width_ratio=0.9):
    """Check if text fits within max_width_ratio of canvas at the given font.

    Falls back to character-count heuristic (max 18 chars) if the font
    can't be loaded or PIL is not installed.

    Args:
        text: The subtitle text to measure.
        font_path: Path to .ttf / .otf font file (any font works).
        font_size: Font size in points.
        canvas_width: Output video width in pixels.
        max_width_ratio: Maximum fraction of canvas width to use.
    """
    font = _load_font(font_path, font_size)
    if font is None:
        return len(text) <= 18  # fallback heuristic
    return font.getlength(text) <= canvas_width * max_width_ratio


def enforce_min_duration(groups, min_dur=MIN_DISPLAY_DURATION):
    """Extend short display groups and shift later ones to prevent overlap.

    Ported from Palmier Pro's CaptionBuilder.swift:
    - If a group is shorter than min_dur, extend its end time.
    - If extending causes overlap with the next group, shift the next
      group's start forward.
    - Repeat for the cascade.

    This prevents subtitle flicker where a 2-word group appears for
    only 0.2s — unreadable at normal playback speed.
    """
    if not groups:
        return groups

    for i, g in enumerate(groups):
        dur = g["end"] - g["start"]
        if dur < min_dur:
            g["end"] = round(g["start"] + min_dur, 3)

        # Prevent overlap with next group
        if i + 1 < len(groups):
            next_g = groups[i + 1]
            if next_g["start"] < g["end"]:
                next_g["start"] = round(g["end"], 3)
                # Ensure next group still has positive duration
                if next_g["end"] <= next_g["start"]:
                    next_g["end"] = round(next_g["start"] + min_dur, 3)

    return groups


def split_into_groups(
    words_with_times: list,
    fits_fn=None,
    min_words: int = 1,
    max_words: int = 6,
    max_chars: int = 18,
    max_gap: float = 1.0,
) -> list:
    """
    Split a list of {word, start, end} dicts into display groups.
    Respects sentence boundaries, character/visual limits, and forces
    breaks on large inter-word gaps.

    Args:
        fits_fn: Optional callable(text) -> bool. If provided, used
                 instead of max_chars to determine if text fits on
                 screen. Enables font-aware visual-fit grouping.
        max_chars: Fallback character limit if fits_fn is not provided.

    Returns groups with precise start/end times from word-level timestamps.
    """
    if not words_with_times:
        return []

    groups = []
    current_words = []

    def current_text():
        return " ".join(w["word"] for w in current_words)

    def flush():
        """Commit current_words as a group."""
        if not current_words:
            return
        groups.append({
            "text": current_text(),
            "start": current_words[0]["start"],
            "end": current_words[-1]["end"],
            "word_count": len(current_words),
            "_words": [
                {"word": w["word"], "start": w["start"], "end": w["end"]}
                for w in current_words
            ],
        })
        current_words.clear()

    for i, wt in enumerate(words_with_times):
        # Gap detection: if there's a long silence before this word,
        # flush the current group so the subtitle doesn't appear too early
        if current_words:
            gap = wt["start"] - current_words[-1]["end"]
            if gap > max_gap:
                flush()

        # Visual-fit / character limit: flush before adding if it wouldn't fit
        if current_words:
            would_be = current_text() + " " + wt["word"]
            if fits_fn is not None:
                if not fits_fn(would_be):
                    flush()
            elif len(would_be) > max_chars:
                flush()

        current_words.append(wt)

        word = wt["word"]
        is_sentence_end = bool(re.search(r'[.!?]$', word))
        is_clause_break = bool(re.search(r'[,;:—–]$', word))
        at_max = len(current_words) >= max_words
        at_reasonable = len(current_words) >= min_words

        if at_max or (at_reasonable and (is_sentence_end or is_clause_break)):
            flush()

    # Handle remainder
    if current_words:
        flush()

    return groups


def split_text_proportional(
    text: str,
    block_start: float,
    block_end: float,
    min_words: int = 1,
    max_words: int = 6,
    max_chars: int = 18,
) -> list:
    """
    Fallback: split text into groups and distribute time proportionally.
    Used only when word_timestamps are not available.
    """
    words = text.split()
    if not words:
        return []

    groups = []
    current = []
    for word in words:
        # Character limit: flush before adding if it would exceed max_chars
        if current:
            would_be = " ".join(current) + " " + word
            if len(would_be) > max_chars:
                groups.append({"text": " ".join(current), "word_count": len(current)})
                current = []

        current.append(word)
        is_sentence_end = bool(re.search(r'[.!?]$', word))
        is_clause_break = bool(re.search(r'[,;:—–]$', word))
        at_max = len(current) >= max_words
        at_reasonable = len(current) >= min_words

        if at_max or (at_reasonable and (is_sentence_end or is_clause_break)):
            groups.append({"text": " ".join(current), "word_count": len(current)})
            current = []
    if current:
        groups.append({"text": " ".join(current), "word_count": len(current)})

    # Distribute time proportionally by word count
    total_words = sum(g["word_count"] for g in groups)
    duration = block_end - block_start
    pos = block_start
    for g in groups:
        g_dur = duration * (g["word_count"] / total_words)
        g["start"] = round(pos, 3)
        g["end"] = round(pos + g_dur, 3)
        # Synthesize proportional per-word timing
        g_words = g["text"].split()
        n_gw = len(g_words)
        per_word_dur = g_dur / max(n_gw, 1)
        g["_words"] = []
        for wi, tw in enumerate(g_words):
            g["_words"].append({
                "word": tw,
                "start": round(pos + wi * per_word_dur, 3),
                "end": round(pos + (wi + 1) * per_word_dur, 3),
            })
        pos += g_dur

    return groups


def identify_emphasis_words(text: str) -> list:
    """
    Identify keywords that should receive visual emphasis (scale bump).
    """
    skip_words = {
        "the", "a", "an", "is", "was", "are", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "and",
        "but", "or", "nor", "not", "so", "yet", "if", "than", "that",
        "this", "it", "its", "i", "me", "my", "we", "our", "you",
        "your", "he", "she", "they", "them", "their", "just", "like",
        "um", "uh", "really", "very", "also", "then",
    }
    emphasis = []
    for word in text.split():
        clean = re.sub(r'[^a-z]', '', word.lower())
        if clean and len(clean) >= 4 and clean not in skip_words:
            emphasis.append(clean)
    return emphasis[:2]


def generate_subtitles(
    audio_spine: dict,
    speech_sequence: dict,
) -> dict:
    """
    Generate subtitle entries using word-level timestamps from wav2vec2 FA.
    Falls back to proportional estimation if word_timestamps are absent.
    """
    structure = audio_spine.get("structure", [])

    # Build a lookup: clip_id + text -> passage with word_timestamps
    passage_lookup = {}
    if speech_sequence.get("hook_segment"):
        hook = speech_sequence["hook_segment"]
        passage_lookup[("hook", hook.get("clip_id"))] = hook
    for passage in speech_sequence.get("body_sequence", []):
        key = (passage.get("position"), passage.get("clip_id"))
        passage_lookup[key] = passage

    subtitle_entries = []
    sub_counter = 0

    # ── Build font-aware fits_fn ──
    # If the spine carries a subtitle style spec with a font path,
    # construct a visual-fit function. Otherwise fall back to max_chars.
    style = audio_spine.get("subtitle_style", {})
    font_path = style.get("font_path")
    font_size = style.get("font_size", 48)
    canvas_width = style.get("canvas_width", 1080)

    fits_fn = None
    if font_path:
        fits_fn = lambda text: text_fits_on_screen(
            text, font_path, font_size, canvas_width)

    for block in structure:
        block_type = block.get("block_type")
        if block_type not in ("hook", "speech"):
            continue

        content = block.get("content", {})
        block_start = block.get("timeline_start", 0.0)
        block_end = block.get("timeline_end", 0.0)

        if block_type == "hook":
            text = content.get("text", "")
            if not text:
                continue

            clip_id = content.get("clip_id")
            passage = passage_lookup.get(("hook", clip_id), {})
            word_ts = passage.get("word_timestamps", [])

            if word_ts:
                # Use the V1 clip's actual source range for the offset
                # calculation, same logic as the speech block path.
                v1_src_in = content.get(
                    "v1_source_in",
                    passage.get("start_time",
                                block.get("source_start", 0.0))
                )
                v1_src_out = content.get(
                    "v1_source_out",
                    passage.get("end_time",
                                block.get("source_end", 0.0))
                )
                offset = block_start - v1_src_in

                # Filter words to the V1 clip's source window
                in_range = [
                    w for w in word_ts
                    if w["end"] > v1_src_in - 0.05
                    and w["start"] < v1_src_out + 0.05
                ]

                timeline_words = [
                    {
                        "word": w["word"],
                        "start": round(w["start"] + offset, 3),
                        "end": round(w["end"] + offset, 3),
                    }
                    for w in in_range
                ]
                # Clip to the block's timeline window
                timeline_words = [
                    w for w in timeline_words
                    if w["end"] > block_start - 0.05
                    and w["start"] < block_end + 0.05
                ]
                groups = split_into_groups(timeline_words, fits_fn=fits_fn)
            else:
                groups = split_text_proportional(
                    text, block_start, block_end
                )

            for g in groups:
                sub_counter += 1
                entry_text = g["text"].lower().strip()
                subtitle_entries.append({
                    "entry_id": f"sub_{sub_counter:03d}",
                    "timeline_start": max(g["start"], block_start),
                    "timeline_end": min(g["end"], block_end),
                    "text": entry_text,
                    "emphasis_words": identify_emphasis_words(entry_text),
                    "spine_block_position": block["position"],
                    "word_count": g["word_count"],
                    "words": [
                        {
                            "word": w["word"].lower().strip(),
                            "start": w["start"],
                            "end": w["end"],
                        }
                        for w in g.get("_words", [])
                    ],
                })

        elif block_type == "speech":
            # The enriched spine may have word_timestamps directly in
            # content (from the 2.5 bridge), or in content.segments[]
            # (legacy). Handle both.
            segments = content.get("segments", [])

            if not segments and content.get("text"):
                # Direct content format (from enriched spine)
                # Try both key naming conventions
                segments = [{
                    "text": content.get("text", ""),
                    "clip_id": content.get("clip_id"),
                    "position": content.get("passage_ref"),
                    "start_time": content.get("source_start",
                                   content.get("start_time", 0.0)),
                    "end_time": content.get("source_end",
                                 content.get("end_time", 0.0)),
                }]

            current_tl_pos = block_start

            for seg in segments:
                seg_text = seg.get("text", "")
                if not seg_text:
                    continue

                clip_id = seg.get("clip_id")
                position = seg.get("position")

                # Try to find word timestamps:
                # 1. Directly in content (enriched spine)
                # 2. In passage_lookup (legacy)
                word_ts = content.get("word_timestamps", [])
                if not word_ts:
                    passage = passage_lookup.get((position, clip_id), {})
                    word_ts = passage.get("word_timestamps", [])

                seg_source_start = seg.get("start_time", 0.0)
                seg_source_end = seg.get("end_time", 0.0)
                source_dur = seg_source_end - seg_source_start
                
                # Each segment plays at 1x speed, so its timeline duration is its source duration.
                # Clamp to the block's overall timeline_end just in case.
                seg_tl_start = current_tl_pos
                seg_tl_end = min(current_tl_pos + source_dur, block_end)
                seg_tl_dur = seg_tl_end - seg_tl_start

                # If source duration significantly exceeds edit duration,
                # we can't use raw word timestamps (the block will be
                # jump-cut and actual word timing is unknown). Use
                # proportional splitting instead.
                use_proportional = (
                    source_dur > 0 and seg_tl_dur > 0
                    and source_dur > seg_tl_dur * 1.5
                )

                if word_ts and not use_proportional:
                    # Use the V1 clip's actual source range for the offset
                    # calculation.
                    v1_src_in = content.get("v1_source_in", seg_source_start)
                    v1_src_out = content.get("v1_source_out", seg_source_end)

                    # Filter words to the V1 clip's source window
                    in_range_words = [
                        w for w in word_ts
                        if w["end"] > v1_src_in - 0.05
                        and w["start"] < v1_src_out + 0.05
                    ]

                    if in_range_words:
                        # Offset: V1 source_in → segment's timeline_start
                        offset = seg_tl_start - v1_src_in

                        timeline_words = [
                            {
                                "word": w["word"],
                                "start": round(w["start"] + offset, 3),
                                "end": round(w["end"] + offset, 3),
                            }
                            for w in in_range_words
                        ]
                        # Clip to segment's timeline window
                        timeline_words = [
                            w for w in timeline_words
                            if w["end"] > seg_tl_start - 0.05
                            and w["start"] < seg_tl_end + 0.05
                        ]
                        groups = split_into_groups(timeline_words, fits_fn=fits_fn)
                    else:
                        groups = split_text_proportional(
                            seg_text, seg_tl_start, seg_tl_end
                        )
                else:
                    # Proportional: spread text evenly across segment
                    groups = split_text_proportional(
                        seg_text, seg_tl_start, seg_tl_end
                    )

                for g in groups:
                    sub_counter += 1
                    entry_text = g["text"].lower().strip()
                    subtitle_entries.append({
                        "entry_id": f"sub_{sub_counter:03d}",
                        "timeline_start": max(g["start"], seg_tl_start),
                        "timeline_end": min(g["end"], seg_tl_end),
                        "text": entry_text,
                        "emphasis_words": identify_emphasis_words(entry_text),
                        "spine_block_position": block["position"],
                        "word_count": g["word_count"],
                        "words": [
                            {
                                "word": w["word"].lower().strip(),
                                "start": w["start"],
                                "end": w["end"],
                            }
                            for w in g.get("_words", [])
                        ],
                    })
                
                # Advance timeline position for the next segment
                current_tl_pos += source_dur

    # ── Enforce minimum display duration PER BLOCK ──
    # Each block's subtitles are enforced independently so that
    # extending a short subtitle in one block never cascades into the
    # next block.  The last subtitle in each block is clamped to the
    # block's end time — it may be shorter than MIN_DISPLAY_DURATION,
    # but that's preferable to bleeding across the block boundary.
    if subtitle_entries:
        # Group entries by spine block position
        block_groups = {}
        for entry in subtitle_entries:
            pos = entry.get("spine_block_position")
            block_groups.setdefault(pos, []).append(entry)

        # Build block-end lookup from spine
        block_end_lookup = {}
        for blk in structure:
            block_end_lookup[blk["position"]] = blk.get(
                "timeline_end", blk.get("timeline_end_frame", 0) / 30.0
            )

        for pos, group in block_groups.items():
            block_end = block_end_lookup.get(pos, float("inf"))

            max_passes = 10
            for _pass in range(max_passes):
                changed = False
                for entry in group:
                    dur = entry["timeline_end"] - entry["timeline_start"]
                    if dur < MIN_DISPLAY_DURATION:
                        entry["timeline_end"] = round(
                            entry["timeline_start"] + MIN_DISPLAY_DURATION, 3
                        )
                        changed = True

                # Prevent overlap between consecutive entries IN THIS BLOCK
                for i in range(len(group) - 1):
                    curr = group[i]
                    nxt = group[i + 1]
                    if nxt["timeline_start"] < curr["timeline_end"]:
                        nxt["timeline_start"] = round(
                            curr["timeline_end"], 3
                        )
                        changed = True
                        if nxt["timeline_end"] <= nxt["timeline_start"]:
                            nxt["timeline_end"] = round(
                                nxt["timeline_start"]
                                + MIN_DISPLAY_DURATION,
                                3,
                            )

                if not changed:
                    break

            # Clamp the last subtitle to the block boundary
            if group:
                last = group[-1]
                if last["timeline_end"] > block_end + 0.001:
                    last["timeline_end"] = round(block_end, 3)
                    # If clamping makes it zero-length, push start back
                    if last["timeline_end"] <= last["timeline_start"]:
                        last["timeline_start"] = round(
                            last["timeline_end"] - 0.1, 3
                        )
                        if len(group) > 1:
                            prev = group[-2]
                            if last["timeline_start"] < prev["timeline_end"]:
                                last["timeline_start"] = round(prev["timeline_end"], 3)

    # --- Verification ---

    # No overlapping subtitles
    sorted_subs = sorted(subtitle_entries, key=lambda s: s["timeline_start"])
    for i in range(len(sorted_subs) - 1):
        if sorted_subs[i]["timeline_end"] > sorted_subs[i + 1]["timeline_start"] + 0.01:
            print(
                f"WARNING: Subtitle overlap: {sorted_subs[i]['entry_id']} "
                f"ends at {sorted_subs[i]['timeline_end']} but "
                f"{sorted_subs[i + 1]['entry_id']} starts at "
                f"{sorted_subs[i + 1]['timeline_start']}",
                file=sys.stderr,
            )

    # All text is lowercase
    for sub in subtitle_entries:
        assert sub["text"] == sub["text"].lower(), \
            f"Subtitle {sub['entry_id']} is not lowercase: {sub['text']}"

    # Word count warnings
    for sub in subtitle_entries:
        if sub["word_count"] > 8:
            print(
                f"WARNING: Subtitle {sub['entry_id']} has "
                f"{sub['word_count']} words",
                file=sys.stderr,
            )

    # Filter emphasis words not present in subtitle text
    for sub in subtitle_entries:
        sub["emphasis_words"] = [
            ew for ew in sub["emphasis_words"]
            if ew in sub["text"]
        ]

    # C4 fix: Wrap output under subtitle_plan key to match manifest contract.
    # Manifest declares output as 'subtitle_plan', and DAG edge
    # plan_subtitles -> render_subtitles maps subtitle_plan -> subtitle_plan.
    return {
        "subtitle_plan": {
            "subtitle_entries": subtitle_entries,
            "total_subtitles": len(subtitle_entries),
        }
    }


def main():
    input_data = json.loads(sys.stdin.read())
    audio_spine = input_data.get("audio_spine")
    speech_sequence = input_data.get("speech_sequence", {})

    if not audio_spine:
        print(json.dumps({
            "error": "Missing required input: audio_spine",
            "step": "4.1_generate_subtitles"
        }))
        sys.exit(1)

    try:
        result = generate_subtitles(audio_spine, speech_sequence)
    except (ValueError, AssertionError) as e:
        print(json.dumps({
            "error": str(e),
            "step": "4.1_generate_subtitles"
        }))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
