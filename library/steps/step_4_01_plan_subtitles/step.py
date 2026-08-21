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
import os
import re
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from library.tools.subtitle_style import resolve_subtitle_style

# ── Minimum display duration (seconds) ──
# Matches Palmier Pro's AppTheme.Caption.minDisplayDuration.
# Subtitles shorter than this are extended; subsequent groups
# are shifted forward to prevent overlap.
MIN_DISPLAY_DURATION = 0.7

# Below this a subtitle flashes rather than reads; clamping a block's
# entries to its bounds can leave a sliver, and a sliver is worth dropping.
MIN_VISIBLE_DURATION = 0.08


# ── Caption case transformation ──
# Controlled by the brand template's effect.caption_case setting.
# Default is "lowercase" so that templates omitting the key (including
# older or third-party templates) preserve the house look.

def apply_caption_case(text: str, mode: str) -> str:
    """Apply the configured caption case transformation.

    Args:
        text: Raw caption text.
        mode: One of "lowercase" or "as_written". Unknown values
              fall back to "lowercase" for safety.
    """
    if mode == "as_written":
        return text
    # "lowercase" and any unknown value
    return text.lower()


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


def _require_word_timestamps(block: dict) -> list:
    """The block's word timings, or a loud failure.

    Every speech and hook block carries populated `word_timestamps` under
    the spine contract (library/tools/spine_contract.py). Estimating the
    timings proportionally when they are absent is what let unaligned
    blocks reach the timeline looking correct.
    """
    words = block["word_timestamps"]
    if not words:
        raise ValueError(
            f"Spine block {block['position']!r} ({block['block_type']}) has "
            f"empty word_timestamps - the spine contract requires word "
            f"timings on every speech block, so subtitles cannot be timed"
        )
    return words


def _words_in_source_window(
    block: dict, words: list, src_in: float, src_out: float,
) -> list:
    """Words falling inside a block's source window, or a loud failure."""
    in_range = [
        w for w in words
        if w["source_end"] > src_in - 0.05
        and w["source_start"] < src_out + 0.05
    ]
    if not in_range:
        raise ValueError(
            f"Spine block {block['position']!r} carries "
            f"{len(words)} word timings but none fall inside its own "
            f"source window {src_in:.3f}-{src_out:.3f}s - the block's "
            f"timings belong to a different passage"
        )
    return in_range


def generate_subtitles(audio_spine: dict, caption_case: str = "lowercase",
                       brand_effect: dict = None,
                       brand_style: dict = None) -> dict:
    """
    Generate subtitle entries from the spine's own word-level timestamps.

    Speech and hook blocks must carry populated word_timestamps; a block
    that does not fails the step rather than being timed by guesswork.

    Args:
        audio_spine: The audio spine with structure blocks.
        caption_case: "lowercase" (default) or "as_written". Controls
            whether subtitle text is lowercased or left as the source
            transcript produced it.
    """
    structure = audio_spine.get("structure", [])

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
        block_type = block["block_type"]
        if block_type not in ("hook", "speech"):
            continue

        content = block["content"]
        block_start = block["timeline_start"]
        block_end = block["timeline_end"]

        if block_type == "hook":
            text = content["text"]
            if not text:
                continue

            word_ts = _require_word_timestamps(block)

            # The spine's own source range is the V1 clip's range.
            v1_src_in = block["source_start"]
            v1_src_out = block["source_end"]
            offset = block_start - v1_src_in

            # Filter words to the V1 clip's source window
            in_range = _words_in_source_window(block, word_ts,
                                               v1_src_in, v1_src_out)

            timeline_words = [
                {
                    "word": w["word"],
                    "start": round(w["source_start"] + offset, 3),
                    "end": round(w["source_end"] + offset, 3),
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

            for g in groups:
                sub_counter += 1
                entry_text = apply_caption_case(g["text"], caption_case).strip()
                subtitle_entries.append({
                    "id": f"sub_{sub_counter:03d}",
                    "timeline_start": max(g["start"], block_start),
                    "timeline_end": min(g["end"], block_end),
                    "text": entry_text,
                    "emphasis_words": identify_emphasis_words(entry_text),
                    "spine_block_position": block["position"],
                    "word_count": g["word_count"],
                    "words": [
                        {
                            "word": apply_caption_case(w["word"], caption_case).strip(),
                            "start": w["start"],
                            "end": w["end"],
                        }
                        for w in g.get("_words", [])
                    ],
                })

        elif block_type == "speech":
            # Under the spine contract a speech block is exactly one
            # passage, carrying its own source range and word timings.
            segments = [{
                "text": content["text"],
                "start_time": block["source_start"],
                "end_time": block["source_end"],
                "word_timestamps": _require_word_timestamps(block),
            }]

            current_tl_pos = block_start

            for seg in segments:
                seg_text = seg["text"]
                if not seg_text:
                    continue

                word_ts = seg["word_timestamps"]
                seg_source_start = seg["start_time"]
                seg_source_end = seg["end_time"]
                source_dur = seg_source_end - seg_source_start

                # Each segment plays at 1x speed, so its timeline duration is its source duration.
                # Clamp to the block's overall timeline_end just in case.
                seg_tl_start = current_tl_pos
                seg_tl_end = min(current_tl_pos + source_dur, block_end)
                seg_tl_dur = seg_tl_end - seg_tl_start

                # mesh_spine syncs a block's duration to its speech, so a
                # source span far longer than the timeline span means the
                # spine is inconsistent and word timings cannot be mapped.
                # Say so instead of quietly spreading the text evenly.
                if source_dur > 0 and seg_tl_dur > 0 and source_dur > seg_tl_dur * 1.5:
                    raise ValueError(
                        f"Spine block {block['position']!r} spans "
                        f"{source_dur:.3f}s of source but only "
                        f"{seg_tl_dur:.3f}s of timeline - word timings "
                        f"cannot be mapped onto a block that was jump-cut "
                        f"after the spine was built"
                    )

                # The block's own source range is the V1 clip's range.
                v1_src_in = seg_source_start
                v1_src_out = seg_source_end

                # Filter words to the V1 clip's source window
                in_range_words = _words_in_source_window(
                    block, word_ts, v1_src_in, v1_src_out)

                # Offset: V1 source_in → segment's timeline_start
                offset = seg_tl_start - v1_src_in

                timeline_words = [
                    {
                        "word": w["word"],
                        "start": round(w["source_start"] + offset, 3),
                        "end": round(w["source_end"] + offset, 3),
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

                for g in groups:
                    sub_counter += 1
                    entry_text = apply_caption_case(g["text"], caption_case).strip()
                    subtitle_entries.append({
                        "id": f"sub_{sub_counter:03d}",
                        "timeline_start": max(g["start"], seg_tl_start),
                        "timeline_end": min(g["end"], seg_tl_end),
                        "text": entry_text,
                        "emphasis_words": identify_emphasis_words(entry_text),
                        "spine_block_position": block["position"],
                        "word_count": g["word_count"],
                        "words": [
                            {
                                "word": apply_caption_case(w["word"], caption_case).strip(),
                                "start": w["start"],
                                "end": w["end"],
                            }
                            for w in g.get("_words", [])
                        ],
                    })
                
                # Advance timeline position for the next segment
                current_tl_pos += seg_tl_dur

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

        # Build block-range lookup from spine
        block_range_lookup = {
            blk["position"]: (blk["timeline_start"], blk["timeline_end"])
            for blk in structure
        }

        for pos, group in block_groups.items():
            block_start, block_end = block_range_lookup.get(
                pos, (float("-inf"), float("inf"))
            )

            for i in range(len(group)):
                curr = group[i]
                nxt = group[i + 1] if i + 1 < len(group) else None
                max_end = nxt["timeline_start"] if nxt else block_end
                dur = curr["timeline_end"] - curr["timeline_start"]
                if dur < MIN_DISPLAY_DURATION:
                    desired_end = curr["timeline_start"] + MIN_DISPLAY_DURATION
                    curr["timeline_end"] = round(min(desired_end, max_end), 3)

            # Clamp EVERY subtitle in the block to the block's range.
            # The min-duration cascade above can push more than one
            # trailing entry past the boundary, and clamping only the last
            # left the rest bleeding into the next block - which then made
            # that block's rendered overlay overlap its neighbour.
            for entry in group:
                entry["timeline_start"] = round(
                    max(entry["timeline_start"], block_start), 3)
                entry["timeline_end"] = round(
                    min(entry["timeline_end"], block_end), 3)

            kept = [
                e for e in group
                if e["timeline_end"] - e["timeline_start"] >= MIN_VISIBLE_DURATION
            ]
            dropped = [e for e in group if e not in kept]
            for e in dropped:
                print(
                    f"WARNING: dropped subtitle {e.get('id', '?')} "
                    f"({e.get('text', '')!r}) - clamping it to block {pos} "
                    f"left no visible duration",
                    file=sys.stderr,
                )
                subtitle_entries.remove(e)
            block_groups[pos] = kept

    # --- Verification ---

    # No overlapping subtitles
    sorted_subs = sorted(subtitle_entries, key=lambda s: s["timeline_start"])
    for i in range(len(sorted_subs) - 1):
        if sorted_subs[i]["timeline_end"] > sorted_subs[i + 1]["timeline_start"] + 0.01:
            print(
                f"WARNING: Subtitle overlap: {sorted_subs[i].get('entry_id', sorted_subs[i].get('id', '?'))} "
                f"ends at {sorted_subs[i]['timeline_end']} but "
                f"{sorted_subs[i + 1].get('entry_id', sorted_subs[i + 1].get('id', '?'))} starts at "
                f"{sorted_subs[i + 1]['timeline_start']}",
                file=sys.stderr,
            )

    # All text matches the configured caption case
    if caption_case != "as_written":
        for sub in subtitle_entries:
            assert sub["text"] == sub["text"].lower(), \
                f"Subtitle {sub.get('entry_id', sub.get('id', '?'))} is not lowercase: {sub['text']}"

    # Word count warnings
    for sub in subtitle_entries:
        if sub["word_count"] > 8:
            print(
                f"WARNING: Subtitle {sub.get('entry_id', sub.get('id', '?'))} has "
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
    # The caption LOOK, resolved from the brand template. Emitted here so
    # step 4.05 has something to serialise into the Remotion props: the
    # props generator used to supply a hardcoded Montserrat/58px default
    # because this key never existed, and every template's typography,
    # palette and subtitle_style reached nothing. See
    # library/tools/subtitle_style.py.
    style = resolve_subtitle_style(brand_effect, brand_style)

    return {
        "subtitle_plan": {
            "subtitle_entries": subtitle_entries,
            "total_subtitles": len(subtitle_entries),
            "style": style,
        }
    }


def main():
    input_data = json.loads(sys.stdin.read())
    audio_spine = input_data.get("audio_spine")

    if not audio_spine:
        print(json.dumps({
            "error": "Missing required input: audio_spine",
            "step": "4.1_generate_subtitles"
        }))
        sys.exit(1)

    # Read caption case from brand template's effect slot.
    # Default to "lowercase" so omitted templates preserve the house look.
    brand_effect = input_data.get("brand_effect", {})
    brand_style = input_data.get("brand_style", {})
    caption_case = brand_effect.get("caption_case", "lowercase")

    try:
        result = generate_subtitles(
            audio_spine, caption_case=caption_case,
            brand_effect=brand_effect, brand_style=brand_style)
    except (ValueError, AssertionError) as e:
        print(json.dumps({
            "error": str(e),
            "step": "4.1_generate_subtitles"
        }))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
