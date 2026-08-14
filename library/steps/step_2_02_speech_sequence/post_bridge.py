#!/usr/bin/env python3
"""
Step 2.02 Bridge: Enrich Speech Sequence with Word Timestamps

Takes the speech_sequence (hook + body_sequence) and enriches each passage
with word-level timestamps from the temporal index (step 1.04 output).

Each passage must have start/end timestamps from the temporal index.
We extract all words in that time range directly — no text matching.

Classification: Deterministic / Data Transformation
Idempotent: Yes

Input:  {
    "speech_sequence": <from step 2.02>,
    "temporal_index_dir": <path to temporal_index/>
}
Output: {
    "speech_sequence": <enriched with word_timestamps, start_time, end_time>
}
"""
import json
import os
import re
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../tools")))
try:
    from engagement_scorer import compute_engagement
except ImportError:
    compute_engagement = None


def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")


class PassageAlignmentError(ValueError):
    """A passage could not be resolved to real word-level timings."""


# A passage whose aligned words share less than this fraction of its text
# is not the passage the LLM meant - refuse it rather than cut to it.
MIN_TEXT_OVERLAP = 0.5

# Two ranges on the same clip overlapping by more than this fraction of
# their union are the same moment.  The hook is allowed to tease a longer
# body passage (a small IoU); it is not allowed to BE one.
MAX_HOOK_BODY_IOU = 0.8

# Full-clip text alignment may anchor to a repeated phrase far from where
# the LLM said the passage was. Beyond this drift, re-align inside the
# hint window instead of trusting the distant match. This is a secondary
# aid only: a mis-anchor of less than MAX_HINT_DRIFT still looks perfectly
# valid to it, which is how the shipped export ended up replaying 0.741s
# of block 4 at the head of block 5. The structural check below is what
# actually stands between a mis-anchor and the timeline.
MAX_HINT_DRIFT = 2.0
HINT_WINDOW_SLACK = 1.0

# Two body passages cut from one clip must not claim overlapping source
# audio - the overlap is laid down twice, back to back. Sub-millisecond
# slop is float noise, not a repeat.
SOURCE_OVERLAP_EPSILON = 1e-3


def normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip punctuation."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def collect_words_in_range(
    start: float,
    end: float,
    regions: list,
    passage_text: str = "",
    tolerance: float = 0.05,
) -> dict:
    """Extract word timestamps from temporal index, aligned to passage text.

    Strategy: collect ALL words from the clip, then use text alignment
    to find the matching sequence. The LLM's [start, end] range is used
    only as a proximity hint to disambiguate when the same text appears
    multiple times in the clip.

    Args:
        start: Approximate source start time (seconds) — used as hint
        end: Approximate source end time (seconds) — used as hint
        regions: speech_regions from the temporal index
        passage_text: The LLM's curated passage text to align against
        tolerance: Not used directly; kept for API compatibility

    Returns dict with: word_timestamps, start_time, end_time
    """
    # Collect ALL words from the clip (not just the time range)
    all_words = []
    for region in regions:
        for w in region.get("words", []):
            all_words.append({
                "word": w["word"],
                "start": w["start"],
                "end": w["end"],
            })

    if not all_words:
        return {"word_timestamps": [], "start_time": None, "end_time": None}

    if passage_text:
        # Text alignment does the real work — finds the matching
        # subsequence regardless of where it falls in the clip.
        # If the same text appears multiple times, pick the match
        # closest to the LLM's time hint.
        words = _align_words_to_text(
            all_words, passage_text,
            hint_start=start, hint_end=end,
        )
    else:
        # No passage text — fall back to time-range filter
        words = [
            w for w in all_words
            if w["end"] > start - tolerance
            and w["start"] < end + tolerance
        ]

    if not words:
        return {"word_timestamps": [], "start_time": None, "end_time": None}

    # Emit the spine contract's word shape (source_start/source_end), not
    # the temporal index's internal start/end. Every consumer downstream
    # reads source_* because that is what the enclosing block uses.
    return {
        "word_timestamps": [
            {
                "word": w["word"],
                "source_start": w["start"],
                "source_end": w["end"],
            }
            for w in words
        ],
        "start_time": words[0]["start"],
        "end_time": words[-1]["end"],
    }

def _hint_drift(enrichment: dict, start: float, end: float):
    """How far the aligned span sits from the LLM's hint, in seconds."""
    if enrichment["start_time"] is None:
        return None
    return max(
        abs(enrichment["start_time"] - start),
        abs(enrichment["end_time"] - end),
    )


def _clip_regions_to_window(regions: list, start: float, end: float) -> list:
    """Regions restricted to a window around the hint, with slack."""
    lo = start - HINT_WINDOW_SLACK
    hi = end + HINT_WINDOW_SLACK
    windowed = []
    for region in regions:
        words = [
            w for w in region.get("words", [])
            if w["end"] > lo and w["start"] < hi
        ]
        if words:
            windowed.append({**region, "words": words})
    return windowed


def _clip_regions_after(regions: list, floor: float) -> list:
    """Regions restricted to words starting at or after `floor`."""
    trimmed = []
    for region in regions:
        words = [
            w for w in region.get("words", [])
            if w["start"] >= floor - SOURCE_OVERLAP_EPSILON
        ]
        if words:
            trimmed.append({**region, "words": words})
    return trimmed


def _overlaps(enrichment: dict, claimed: tuple) -> bool:
    """Does an aligned span claim source audio a previous passage took?"""
    if enrichment["start_time"] is None or claimed is None:
        return False
    prior_start, prior_end = claimed
    return (
        enrichment["start_time"] < prior_end - SOURCE_OVERLAP_EPSILON
        and enrichment["end_time"] > prior_start + SOURCE_OVERLAP_EPSILON
    )


def _align_words_to_text(
    candidates: list,
    passage_text: str,
    hint_start: float = None,
    hint_end: float = None,
) -> list:
    """Align candidate word timestamps to the passage text.

    Uses a two-pointer approach with lookahead to handle:
      - Words the LLM included but WhisperX didn't transcribe (e.g. "2026")
      - Contractions ("it's" vs "it is")
      - Extra filler words in the candidate list

    hint_start/hint_end are used to pick the right starting position
    when searching through the full clip (in case the same text appears
    multiple times).

    Returns the aligned subset of candidates.
    """
    passage_words = normalize(passage_text).split()
    if not passage_words:
        return candidates

    # Find the best starting candidate index using the time hint.
    # Start from the candidate nearest to (but before) hint_start.
    first_passage_word = passage_words[0]
    best_start_idx = 0

    if hint_start is not None:
        # Find all candidates that match the first passage word
        first_word_indices = [
            i for i, c in enumerate(candidates)
            if normalize(c["word"]) == first_passage_word
        ]

        if first_word_indices:
            # Pick the one closest to hint_start
            best_start_idx = min(
                first_word_indices,
                key=lambda i: abs(candidates[i]["start"] - hint_start),
            )
            # Back up by time (2s window), not fixed index count,
            # to avoid crossing a large gap to an earlier occurrence
            backup_time = candidates[best_start_idx]["start"] - 2.0
            while (best_start_idx > 0 and
                   candidates[best_start_idx - 1]["start"] >= backup_time):
                best_start_idx -= 1

    # Contraction expansion table
    expanded = {
        "its": ("it", "is"),
        "im": ("i", "am"), "ive": ("i", "have"),
        "dont": ("do", "not"), "didnt": ("did", "not"),
        "cant": ("can", "not"), "wont": ("will", "not"),
        "isnt": ("is", "not"), "wasnt": ("was", "not"),
        "thats": ("that", "is"), "whats": ("what", "is"),
        "hes": ("he", "is"), "shes": ("she", "is"),
        "theyre": ("they", "are"), "were": ("we", "are"),
        "youre": ("you", "are"), "youve": ("you", "have"),
        "theres": ("there", "is"), "heres": ("here", "is"),
        "lets": ("let", "us"), "wouldnt": ("would", "not"),
        "couldnt": ("could", "not"), "shouldnt": ("should", "not"),
        "havent": ("have", "not"), "hasnt": ("has", "not"),
        "ill": ("i", "will"), "itll": ("it", "will"),
        "well": ("we", "will"), "theyll": ("they", "will"),
    }

    aligned = []
    p_idx = 0   # pointer into passage_words
    c_idx = best_start_idx  # start near the time hint

    while p_idx < len(passage_words) and c_idx < len(candidates):
        p_word = passage_words[p_idx]
        c_word = normalize(candidates[c_idx]["word"])

        if not c_word:
            c_idx += 1
            continue

        # Direct match
        if c_word == p_word:
            aligned.append(candidates[c_idx])
            p_idx += 1
            c_idx += 1
            continue

        # Contraction match: "it's" in candidate vs "it is" in passage
        if c_word in expanded and p_idx + 1 < len(passage_words):
            exp = expanded[c_word]
            if (p_word == exp[0] and
                    passage_words[p_idx + 1] == exp[1]):
                aligned.append(candidates[c_idx])
                p_idx += 2  # consumed 2 passage words
                c_idx += 1
                continue

        # Lookahead: maybe the current passage word wasn't transcribed
        # (e.g. "2026" missing from WhisperX). Try matching the NEXT
        # passage word against the current candidate.
        lookahead = min(3, len(passage_words) - p_idx)
        matched = False
        for skip in range(1, lookahead):
            if normalize(passage_words[p_idx + skip]) == c_word:
                p_idx += skip  # skip untranscribed passage words
                aligned.append(candidates[c_idx])
                p_idx += 1
                c_idx += 1
                matched = True
                break

        if matched:
            continue

        # No match at all — skip this candidate
        c_idx += 1

    return aligned


def enrich_speech_sequence(
    speech_sequence: dict,
    temporal_index_dir: str,
    semantic_data: dict = None,
    prosody_data: dict = None,
) -> dict:
    """Enrich all passages with word timestamps from temporal index.

    Uses timestamp-based lookup. Each passage must have start/end
    timestamps resolved from the temporal index.
    """

    # Cache loaded temporal indices
    ti_cache = {}

    def load_ti(clip_id):
        if clip_id not in ti_cache:
            path = os.path.join(temporal_index_dir, f"{clip_id}.json")
            if os.path.exists(path):
                with open(path) as f:
                    ti_cache[clip_id] = json.load(f)
            else:
                print(f"  WARNING: No temporal index for {clip_id}",
                      file=sys.stderr)
                ti_cache[clip_id] = {}
        return ti_cache[clip_id]

    # The source range each clip's most recent body passage claimed. Two
    # body passages cut from one clip may not claim the same audio: the
    # second would replay the tail of the first. The hook is deliberately
    # absent - it is allowed to tease a body passage, which
    # _drop_hook_duplicates bounds by IoU instead.
    claimed_by_clip = {}

    def enrich_passage(passage: dict, label: str,
                       prior_claim: tuple = None) -> dict:
        """Resolve a passage's real source timings from the temporal index.

        The LLM's `source_start`/`source_end` are a HINT only - they
        disambiguate which occurrence of the text to align against.  The
        returned times always come from WhisperX word timings, so an LLM
        that guessed a plausible-looking round-number range cannot leak
        fabricated timings into the timeline.

        Raises PassageAlignmentError when the passage cannot be aligned.
        Failing loudly here is the point: a passage with no word timings
        used to sail through as `word_timestamps: []`, which disabled
        beat-aligned cutting and left the back half of the video showing
        footage that did not match its transcript.
        """
        clip_id = passage.get("clip_id")
        if not clip_id:
            raise PassageAlignmentError(f"{label}: passage has no clip_id")

        if "source_start" not in passage or "source_end" not in passage:
            raise PassageAlignmentError(
                f"{label}: passage is missing source_start/source_end. "
                f"The speech_sequence contract requires both. "
                f"Got keys: {sorted(passage.keys())}"
            )
        try:
            start = float(passage["source_start"])
            end = float(passage["source_end"])
        except (TypeError, ValueError) as e:
            raise PassageAlignmentError(
                f"{label}: source_start/source_end are not numeric "
                f"({passage['source_start']!r}, {passage['source_end']!r})"
            ) from e

        passage_text = passage.get("text", "")
        if not passage_text.strip():
            raise PassageAlignmentError(f"{label}: passage has no text")

        ti = load_ti(clip_id)
        regions = ti.get("speech_regions", [])
        if not regions:
            raise PassageAlignmentError(
                f"{label}: no speech regions in the temporal index for "
                f"{clip_id} - cannot resolve real word timings"
            )

        enrichment = collect_words_in_range(
            start, end, regions, passage_text=passage_text,
        )

        # Text alignment searches the whole clip, so a passage whose
        # opening words recur can anchor to the wrong occurrence and come
        # back looking perfectly valid. If the aligned span wanders far
        # from the LLM's hint, re-align against the hint window only.
        drift = _hint_drift(enrichment, start, end)
        if drift is not None and drift > MAX_HINT_DRIFT:
            windowed = collect_words_in_range(
                start, end, _clip_regions_to_window(regions, start, end),
                passage_text=passage_text,
            )
            if windowed["word_timestamps"]:
                print(
                    f"  {label}: full-clip alignment drifted {drift:.2f}s "
                    f"from the hint; re-anchored inside the hint window",
                    file=sys.stderr,
                )
                enrichment = windowed

        # Structural check, not a threshold: a passage that begins inside
        # the range the previous passage on this clip already took is
        # mis-anchored by construction - the timeline would lay that audio
        # down twice, back to back. This is how block 5 came to start on
        # block 4's trailing "to post" while its own opening words went
        # unmatched, at 0.76s of drift - comfortably under MAX_HINT_DRIFT.
        # Re-anchor past the claimed range, or fail the passage loudly.
        if _overlaps(enrichment, prior_claim):
            overlap_start = enrichment["start_time"]
            reanchored = collect_words_in_range(
                start, end, _clip_regions_after(regions, prior_claim[1]),
                passage_text=passage_text,
            )
            if (reanchored["word_timestamps"]
                    and not _overlaps(reanchored, prior_claim)):
                print(
                    f"  {label}: alignment started at {overlap_start:.3f}s, "
                    f"inside the previous passage on {clip_id} "
                    f"({prior_claim[0]:.3f}-{prior_claim[1]:.3f}); "
                    f"re-anchored to {reanchored['start_time']:.3f}s",
                    file=sys.stderr,
                )
                enrichment = reanchored
            else:
                raise PassageAlignmentError(
                    f"{label}: aligned to {overlap_start:.3f}-"
                    f"{enrichment['end_time']:.3f}s in {clip_id}, which "
                    f"overlaps the previous passage on that clip "
                    f"({prior_claim[0]:.3f}-{prior_claim[1]:.3f}s), and no "
                    f"alignment after {prior_claim[1]:.3f}s matches the "
                    f"passage text {passage_text[:60]!r}. Two blocks cut "
                    f"from one clip cannot claim the same source audio."
                )

        words = enrichment["word_timestamps"]

        if not words:
            raise PassageAlignmentError(
                f"{label}: no words in {clip_id} matched the passage text "
                f"{passage_text[:60]!r} (LLM hint was "
                f"{start:.2f}-{end:.2f}s). The passage text must be "
                f"verbatim from the transcript."
            )

        # Faithfulness check: the aligned words must actually be this
        # passage, not a nearby run of words that happened to match.
        passage_words = set(normalize(passage_text).split())
        found_words = set(normalize(w["word"]) for w in words)
        ratio = len(passage_words & found_words) / max(len(passage_words), 1)
        if ratio < MIN_TEXT_OVERLAP:
            raise PassageAlignmentError(
                f"{label}: aligned words overlap the passage text by only "
                f"{ratio:.0%} (minimum {MIN_TEXT_OVERLAP:.0%}) in {clip_id} "
                f"at {enrichment['start_time']:.2f}-"
                f"{enrichment['end_time']:.2f}s"
            )

        hint_drift = max(
            abs(enrichment["start_time"] - start),
            abs(enrichment["end_time"] - end),
        )
        enrichment["alignment_method"] = "whisperx_word_alignment"
        # stderr, not stdout: stdout carries this bridge's JSON output.
        print(
            f"  {label}: {len(words)} words "
            f"({enrichment['start_time']:.3f}->{enrichment['end_time']:.3f}), "
            f"LLM hint was {start:.3f}->{end:.3f} "
            f"(drift {hint_drift:.2f}s)",
            file=sys.stderr,
        )
        return enrichment

    def apply_enrichment(passage: dict, enrichment: dict) -> None:
        """Write resolved timings back onto a passage, in place."""
        passage["word_timestamps"] = enrichment["word_timestamps"]
        passage["alignment_method"] = enrichment["alignment_method"]
        # The aligned times replace the LLM's hint outright - they are the
        # only timings the pipeline is allowed to cut to.
        passage["source_start"] = enrichment["start_time"]
        passage["source_end"] = enrichment["end_time"]
        passage["start_time"] = enrichment["start_time"]
        passage["end_time"] = enrichment["end_time"]
        passage["duration_seconds"] = round(
            enrichment["end_time"] - enrichment["start_time"], 3
        )
        if compute_engagement:
            passage["engagement"] = compute_engagement(
                passage, prosody_data, semantic_data, result
            )

    result = dict(speech_sequence)
    failures = []

    # Enrich hook segment
    hook = result.get("hook_segment")
    if hook:
        try:
            apply_enrichment(hook, enrich_passage(hook, "Hook"))
        except PassageAlignmentError as e:
            failures.append(str(e))

    # Enrich body passages
    body = result.get("body_sequence", [])
    aligned_body = []
    for passage in body:
        pos = passage.get("position", "?")
        clip_id = passage.get("clip_id")
        try:
            enrichment = enrich_passage(
                passage, f"Body[{pos}]", claimed_by_clip.get(clip_id),
            )
        except PassageAlignmentError as e:
            failures.append(str(e))
            continue
        apply_enrichment(passage, enrichment)
        claimed_by_clip[clip_id] = (
            enrichment["start_time"], enrichment["end_time"],
        )
        aligned_body.append(passage)

    if failures:
        raise PassageAlignmentError(
            f"{len(failures)} passage(s) could not be aligned to real word "
            f"timings:\n  - " + "\n  - ".join(failures)
        )

    result["body_sequence"] = _drop_hook_duplicates(hook, aligned_body, result)
    return result


def _drop_hook_duplicates(hook: dict, body: list, result: dict) -> list:
    """Remove body passages that are the same moment as the hook.

    The hook is allowed to tease a longer body passage - that is a
    deliberate shortform technique.  It is not allowed to be byte-identical
    to one: that opens the video with a line and replays it verbatim a few
    seconds later.
    """
    if not hook or hook.get("source_start") is None:
        return body

    hook_clip = hook.get("clip_id")
    hook_start = hook["source_start"]
    hook_end = hook["source_end"]

    kept = []
    for passage in body:
        if passage.get("clip_id") != hook_clip:
            kept.append(passage)
            continue

        overlap = min(hook_end, passage["source_end"]) - max(
            hook_start, passage["source_start"]
        )
        union = max(hook_end, passage["source_end"]) - min(
            hook_start, passage["source_start"]
        )
        iou = overlap / union if union > 0 else 0.0

        if overlap > 0 and iou > MAX_HOOK_BODY_IOU:
            print(
                f"  Dropped Body[{passage.get('position')}]: same moment as "
                f"the hook ({hook_clip} {passage['source_start']:.3f}-"
                f"{passage['source_end']:.3f}, IoU {iou:.2f}) - the video "
                f"would open with this line and repeat it verbatim",
                file=sys.stderr,
            )
            result.setdefault("excluded_passages", []).append({
                "clip_id": hook_clip,
                "text": passage.get("text", ""),
                "source_start": passage["source_start"],
                "source_end": passage["source_end"],
                "reason_excluded": (
                    f"duplicate of hook_segment (IoU {iou:.2f})"
                ),
            })
            continue

        kept.append(passage)

    # Renumber so positions stay contiguous after a drop.
    for i, passage in enumerate(kept, start=1):
        passage["position"] = i
    return kept


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    # C2 fix: State provides temporal_index as an object with index_dir,
    # not temporal_index_dir at root level.
    _require_keys(data, ["speech_sequence", "temporal_index"], "Input data")
    if not isinstance(data["speech_sequence"], dict):
        raise ValueError("speech_sequence must be a dictionary")

    speech_sequence = data.get("speech_sequence")
    if not speech_sequence:
        print(json.dumps({"error": "Missing: speech_sequence",
                          "step": "2.02_bridge"}))
        sys.exit(1)

    temporal_index = data.get("temporal_index", {})
    ti_dir = ""
    if isinstance(temporal_index, dict):
        ti_dir = temporal_index.get("index_dir", "")
    elif isinstance(temporal_index, list) and len(temporal_index) > 0:
        first = temporal_index[0]
        if isinstance(first, dict) and "index_path" in first:
            ti_dir = os.path.dirname(first["index_path"])
            
    if not ti_dir or not os.path.isdir(ti_dir):
        # Fallback to pipeline_data.json like bridge.py
        project_dir = data.get("project_folder", "")
        if project_dir:
            state_file = os.path.join(project_dir, "pipeline_data.json")
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    state_data = json.load(f)
                    ti_dir = state_data.get("step_outputs", {}).get("temporal_index", {}).get("index_dir", "")
                    
    if not ti_dir or not os.path.isdir(ti_dir):
        print(json.dumps({"error": f"Invalid temporal_index.index_dir: {ti_dir}",
                          "step": "2.02_bridge"}))
        sys.exit(1)

    semantic_data = data.get("semantic_analysis_documents", {})
    prosody_data = data.get("prosody_analysis", {})

    enriched = enrich_speech_sequence(speech_sequence, ti_dir, semantic_data, prosody_data)

    body = enriched.get("body_sequence", [])
    if len(body) < 8 or len(body) > 20:
        print(f"WARNING: Selected {len(body)} speech passages. Recommended is 10-15.", file=sys.stderr)
        
    total_duration = 0.0
    hook = enriched.get("hook_segment")
    if hook:
        start = hook.get("start_time")
        end = hook.get("end_time")
        if start is not None and end is not None:
            total_duration += (end - start)
            
    for passage in body:
        start = passage.get("start_time")
        end = passage.get("end_time")
        if start is not None and end is not None:
            total_duration += (end - start)
            
    from library.tools.duration_targets import get_target_duration_zone
    min_dur, target_dur, max_dur = get_target_duration_zone(data)

    if total_duration > max_dur or total_duration < min_dur:
        print(f"WARNING: Total speech duration ({total_duration:.1f}s) is out of bounds ({min_dur:.1f}-{max_dur:.1f}s).", file=sys.stderr)

    json.dump({
        "step": "2.02_bridge",
        "speech_sequence": enriched,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
