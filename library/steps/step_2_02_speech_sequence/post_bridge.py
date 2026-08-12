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

    return {
        "word_timestamps": words,
        "start_time": words[0]["start"],
        "end_time": words[-1]["end"],
    }

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

    def enrich_passage(passage: dict, label: str) -> dict:
        """Enrich a single passage with word timestamps."""
        clip_id = passage.get("clip_id")
        if not clip_id:
            print(f"  ERROR: {label} — missing clip_id", file=sys.stderr)
            return {"word_timestamps": [], "start_time": None, "end_time": None}

        start_val = (passage.get("source_in")
                     if passage.get("source_in") is not None
                     else passage.get("start")
                     if passage.get("start") is not None
                     else passage.get("start_time"))
        end_val = (passage.get("source_out")
                   if passage.get("source_out") is not None
                   else passage.get("end")
                   if passage.get("end") is not None
                   else passage.get("end_time"))
        try:
            start = float(start_val)
            end = float(end_val)
        except (TypeError, ValueError):
            start = None
            end = None
        if start is None or end is None:
            print(
                f"  ERROR: {label} — missing start/end timestamps. "
                f"The LLM must provide these from the temporal index.",
                file=sys.stderr,
            )
            return {"word_timestamps": [], "start_time": None, "end_time": None}

        ti = load_ti(clip_id)
        regions = ti.get("speech_regions", [])

        enrichment = collect_words_in_range(
            start, end, regions,
            passage_text=passage.get("text", ""),
        )
        wt_count = len(enrichment["word_timestamps"])

        if wt_count == 0:
            print(
                f"  WARNING: {label} — no words found in "
                f"{clip_id} at {start:.2f}-{end:.2f}s",
                file=sys.stderr,
            )
            return enrichment

        # Sanity check: verify the word text roughly matches
        passage_text = passage.get("text", "")
        if passage_text:
            passage_words = set(normalize(passage_text).split())
            found_words = set(
                normalize(w["word"])
                for w in enrichment["word_timestamps"]
            )
            overlap = len(passage_words & found_words)
            ratio = overlap / max(len(passage_words), 1)
            if ratio < 0.3:
                print(
                    f"  WARNING: {label} — low text overlap "
                    f"({ratio:.0%}) between passage and temporal "
                    f"index at {start:.2f}-{end:.2f}s",
                    file=sys.stderr,
                )

        print(
            f"  {label}: {wt_count} words "
            f"({enrichment['start_time']:.3f}"
            f"→{enrichment['end_time']:.3f})"
        )
        return enrichment

    result = dict(speech_sequence)

    # Enrich hook segment
    hook = result.get("hook_segment")
    if hook and not hook.get("word_timestamps"):
        # Extract start/end for fallback
        start_val = hook.get("start") if hook.get("start") is not None else hook.get("start_time")
        end_val = hook.get("end") if hook.get("end") is not None else hook.get("end_time")
        try:
            start = float(start_val)
            end = float(end_val)
        except (TypeError, ValueError):
            start = None
            end = None
            
        enrichment = enrich_passage(hook, "Hook")
        hook["word_timestamps"] = enrichment["word_timestamps"]
        
        if enrichment["start_time"] is not None:
            hook["start_time"] = enrichment["start_time"]
            hook["end_time"] = enrichment["end_time"]
        else:
            hook["start_time"] = start
            hook["end_time"] = end
            
        if hook.get("start_time") is not None and hook.get("end_time") is not None:
            hook["duration_seconds"] = round(hook["end_time"] - hook["start_time"], 3)
        if compute_engagement:
            hook["engagement"] = compute_engagement(hook, prosody_data, semantic_data, result)

    # Enrich body passages
    body = result.get("body_sequence", [])
    for passage in body:
        pos = passage.get("position", "?")
        
        # Extract start/end for fallback
        start_val = passage.get("start") if passage.get("start") is not None else passage.get("start_time")
        end_val = passage.get("end") if passage.get("end") is not None else passage.get("end_time")
        try:
            start = float(start_val)
            end = float(end_val)
        except (TypeError, ValueError):
            start = None
            end = None

        enrichment = enrich_passage(passage, f"Body[{pos}]")
        passage["word_timestamps"] = enrichment["word_timestamps"]
        
        if enrichment["start_time"] is not None:
            passage["start_time"] = enrichment["start_time"]
            passage["end_time"] = enrichment["end_time"]
        else:
            passage["start_time"] = start
            passage["end_time"] = end
            
        if passage.get("start_time") is not None and passage.get("end_time") is not None:
            passage["duration_seconds"] = round(passage["end_time"] - passage["start_time"], 3)
        if compute_engagement:
            passage["engagement"] = compute_engagement(passage, prosody_data, semantic_data, result)

    return result


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
            
    project_config = data.get("project_config", {})
    target_duration = project_config.get("target_duration_seconds") if isinstance(project_config, dict) else None
    if target_duration:
        min_dur = target_duration * 0.9
        max_dur = target_duration * 1.1
    else:
        min_dur = 30.0
        max_dur = 60.0

    if total_duration > max_dur or total_duration < min_dur:
        print(f"WARNING: Total speech duration ({total_duration:.1f}s) is out of bounds ({min_dur:.1f}-{max_dur:.1f}s).", file=sys.stderr)

    json.dump({
        "step": "2.02_bridge",
        "speech_sequence": enriched,
    }, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
