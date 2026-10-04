#!/usr/bin/env python3
"""
vision_pipeline_v3.py — Video Analysis Pipeline v3

Dimension-indexed architecture: each annotation dimension uses its own
optimal temporal indexing strategy.

Dimensions:
  1. Scene/Environment — folded into the per-window call (10s windows)
  2. Camera — folded into the per-window call (10s windows)
  3. Actions/Behavior — fixed 10s windows (video clips + transcript)
  4. Objects/Entities + OCR — entity-indexed, two-tier (coarse → detail)
  5. Assessment — clip-level merge of the per-window sections (votes) +
     deterministic tail

Every native-video pass answers from the same one call per 10 s window
(20 frames at 2 fps), so no pass samples below that floor. Scene,
camera and assessment sections arrive in clip time and are merged
across windows; the assessment vote merge is `_merge_assessment_votes`
and the deterministic tail is `_finish_assessment`.

Usage:
    python3 vision_pipeline_v3.py                             # All clips in raw/
    python3 vision_pipeline_v3.py --clip raw/IMG_1813.MOV     # Single clip
    python3 vision_pipeline_v3.py --raw-dir ./raw             # Specify directory
"""

import argparse
from contextlib import contextmanager
import json
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import heapq
from pathlib import Path

import numpy as np

try:
    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
except ImportError as e:
    err_msg = str(e)
    def _missing_mlx(*args, **kwargs):
        raise RuntimeError(f"mlx_vlm is not installed. To run the vision pipeline, install mlx_vlm (macOS only): {err_msg}")
    load = generate = apply_chat_template = _missing_mlx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from model_lifecycle import managed_model

# Step 1.03 runs this file as a SCRIPT, so sys.path[0] is this directory
# and the repo root has to be put on the path by hand.  `parents[3]` is
# <repo>, above `library/`; tests/unit/picture/test_picture_quality.py asserts the
# index, because an off-by-one here still imports cleanly under pytest
# (conftest already has the root on sys.path) and fails only in a run.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from library.tools.heavy_work_lock import (
    heavy_work_lock,
    heavy_work_reservation,
)
from library.tools import perf_ledger
from library.tools.analysis import measurement_layers, picture_quality
from library.tools.camera_stability import read_camera_stability
from library.tools.segment_coverage import (
    coverage_summary,
    normalize_segments,
)


# ═══════════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════════

MODEL_ID = "mlx-community/gemma-4-12b-it-4bit"
_LOCAL_GEMMA_INFERENCE = threading.Lock()


@contextmanager
def _semantic_model_memory_reservation():
    """Keep the loaded Gemma weights reserved across non-local waits.

    Lock order is RAM reservation first, then an independently admitted
    CPU/GPU inference section. The inference grant is RAM-free and always
    released before returning to host work or unloading the model.
    """
    with heavy_work_reservation(
            "Gemma semantic model memory", "semantics.analyse:model_memory",
            nested_profiles=("semantics.analyse:inference",)):
        yield


@contextmanager
def _semantic_inference_admission():
    """Serialize local Gemma calls under a short CPU/GPU scheduler grant."""
    with _LOCAL_GEMMA_INFERENCE:
        with heavy_work_lock("Gemma semantic inference",
                             "semantics.analyse:inference"):
            yield

# The cached measurement layers of a profile and the entry points whose
# reached code is each one's method identity
# (`library/tools/analysis/measurement_layers.py`). `VisionAnalyzer` is
# named because the passes reach it through a parameter, which no name
# walk can follow. Everything `analyze_clip` derives from these is
# recomputed on every compose.
MEASUREMENT_LAYERS = {
    "windows": ("analyze_windows", "extract_video_clips",
                "VisionAnalyzer", "MODEL_ID"),
    "objects": ("extract_frames", "analyze_objects_coarse",
                "find_detail_ranges", "analyze_objects_detail",
                "VisionAnalyzer", "MODEL_ID"),
    "picture": ("picture_quality",),
}
CACHE_DIR = Path(".vision_cache")
OUTPUT_DIR = Path("pipeline_output")

# Action windows
ACTION_WINDOW_S = 10


def _write_profile_atomically(path: Path, profile: dict) -> None:
    """Publish a complete per-clip profile with one atomic rename."""
    path = Path(path)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(profile, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

# A window shorter than this is not handed to a pass. The tail sliver
# of a clip whose duration is not a multiple of `ACTION_WINDOW_S`
# (e.g. the 0.01 s second window of a 10.01 s clip at 23.976 fps, where
# every 10 s cut lands on a frame boundary plus one) re-encodes to a
# husk cv2 cannot open - or to a single frame, which `load_video`
# rejects (`nframes must be in [2, 1]`) - and the model call raises on
# it. The sliver stays undescribed; the coverage record says so.
MIN_WINDOW_S = 0.5

# Native-video frame budget, measured on mlx-vlm 0.7.2 with
# `mlx-community/gemma-4-12b-it-4bit` (2026-09-24, 25.8 GB machine).
# `load_video` decodes at fps=2.0 capped to max_frames (`utils.load_video`
# with `DEFAULT_VIDEO_SAMPLING`, `resolve_video_sampling`), and the gemma4
# processor then keeps at most `num_frames=32` (`_sample_frames`). A
# caller-supplied `max_frames=` to `generate` reaches ONLY the decoder:
# measured on a real 60 s geo-podcast excerpt, max_frames=64/96 decoded
# 64/96 frames and the processor still kept 32 (spy on `_sample_frames`;
# identical output text), costing +11 s / +21 s wall for the discarded
# decode. Raising the processor's own `num_frames` to 64/96 DOES feed
# 64/96 frames end to end (valid JSON back), at 77 s / 97 s per call
# against 25 s at 32 - about a second of wall per kept frame - and even
# 96 frames on 60 s is 1.6 fps, still under the 2 fps floor. So the cap
# is not raised: the floor is met by WINDOWING, and every native-video
# pass runs on the 10 s action windows (20 frames at 2 fps), folded into
# one call per window (see `analyze_windows`). Peak RSS never moved with
# frame count (~4.2 GB process peak in every setting) - wall time, not
# memory, is the binding cost.
NATIVE_VIDEO_FRAMES_PER_CALL = 32
NATIVE_VIDEO_DECODE_FPS = 2.0

# Window clips are cut at 720p height (aspect kept, never upscaled), not
# at source resolution. Measured 2026-09-24 on the captain's M5/25.8 GB
# box, mlx-vlm 0.7.2, `mlx-community/gemma-4-12b-it-4bit`, one folded
# call per 10 s window: 720p input is ~16% faster end to end than 4K
# (interleaved medians 43.5 s vs 52.7 s under load; prompt tokens
# identical at 2723, so the token grid is unchanged - the saving is
# decode plus processor resize). Verdicts held on the probe window:
# actions/scene/camera counts, content_type, notable_features and
# body_language all inside the 4K call's own run-to-run variation.
# Cutting `max_soft_tokens` instead (70 -> 35/18) is REJECTED: prompt
# tokens fall but wall barely moves (decode dominates) and verdicts
# drift, including one 2000-token runaway. The 2 fps floor is untouched:
# this changes pixels per frame, never frames per window.
WINDOW_CLIP_HEIGHT = 720

# Object detection — coarse sweep
COARSE_FRAME_INTERVAL_S = 5       # 1 frame every 5 seconds
COARSE_BATCH_SIZE = 15            # Max frames per model call

# Object detection — detail pass
DETAIL_FPS = 1                    # Frames per second in detail ranges
DETAIL_BATCH_SIZE = 6             # Max frames per detail model call (smaller = more reliable JSON)
DETAIL_MAX_APPEARANCE_S = 15      # Only detail-scan entities with < 15s total appearance
DETAIL_MERGE_GAP_S = 5            # Merge nearby detail ranges within 5s
DETAIL_MAX_COVERAGE = 0.6         # Skip detail pass if ranges cover > 60% of clip duration

# Token budgets per pass type
MAX_TOKENS = {
    "window_all": 2000,
    # Compact answers finish near ~140 tokens (measured 2026-09-24);
    # 600 binds only the runaway tail (caption-transcription spirals
    # that count seconds past the window) without touching good
    # answers, so a failed window costs ~1 minute, not ~3.
    "window_all_compact": 600,
    "objects_coarse": 700,
    "objects_detail": 600,
}


# ── ffprobe spawn count ──────────────────────────────────────────
#
# How many ffprobe processes this module started. The window loop
# used to pay 2-3 spawns per window (source-audio probe, per-cut
# open-checks), all serial; the thrift probes source audio once per
# clip and validates each cached window with one combined spawn, so a
# fresh cut pays ~1 spawn for the whole clip. Counted, never
# estimated: the counter moves at the one place every spawn goes
# through, alongside the call - including the thrift's own combined
# probe, so the count judges the thrift instead of missing it.
_FFPROBE_SPAWNS = 0
_FFPROBE_SPAWNS_LOCK = threading.Lock()


def _note_ffprobe_spawn() -> None:
    """One ffprobe process started. The counter is process-local and
    locked because the window extractor runs on a producer thread. The
    runner resets it per clip, so a clip's count is that clip's."""
    global _FFPROBE_SPAWNS
    with _FFPROBE_SPAWNS_LOCK:
        _FFPROBE_SPAWNS += 1


def ffprobe_spawn_count() -> int:
    """How many ffprobe processes started since the last reset."""
    with _FFPROBE_SPAWNS_LOCK:
        return _FFPROBE_SPAWNS


def reset_ffprobe_spawn_count() -> None:
    """Zero the spawn counter. The runner calls this per clip."""
    global _FFPROBE_SPAWNS
    with _FFPROBE_SPAWNS_LOCK:
        _FFPROBE_SPAWNS = 0


# ═══════════════════════════════════════════════════════════════════════
#  Prompts
# ═══════════════════════════════════════════════════════════════════════

PROMPT_WINDOW_ALL = """This is a {window_dur:.0f}-second segment (seconds {window_start:.0f} to {window_end:.0f}) of a {duration:.0f}-second video clip.
{transcript_line}
This video clip carries its AUDIO TRACK - measured on mlx-vlm 0.7.2,
gemma4-unified accepts audio alongside video (`generate` takes
`audio=` with the audio marker in the prompt, and the checkpoint
carries `embed_audio` weights), so listen to HOW speech is delivered
(pace, effort, pauses, visible effort). The only WORDS that exist are
the transcript text above (if any). NEVER quote speech: do not put
words in quotation marks and do not attribute utterances to the person
on screen. `speech_cue` describes delivery - pace, effort, pauses,
mouth movement, gestures while talking - never words.

Answer all four parts in ONE response. Part 1 - actions: for each
distinct action or behavior change in this segment, report what the
person is physically doing, observable speech delivery cues (if
speaking): mouth movement, apparent volume, gestures while talking,
and observable facial expression and body language: posture, hand
position, head orientation, facial muscle state.

Part 2 - scene: pre-detected scene boundaries (from automated visual
analysis) within this segment: {boundaries}. Describe the physical
environment for each part of this segment, and identify any additional
subtle environment changes the detector may have missed (e.g.,
significant lighting shifts within the same location).

Part 3 - camera: describe the camera behavior in this segment. Create
a new entry ONLY when the camera mode meaningfully changes (e.g.,
static to walking, selfie to rear-facing, close-up to wide shot,
stable to shaky).

Part 4 - assessment: classify what this segment shows. content_type:
one of person_talking_to_camera, scenery, action_sequence,
multiple_people, object_showcase, transition. primary_subject_visible:
time ranges where the main person is visible.

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
{{
  "actions": [
    {{"start": <seconds>, "end": <seconds>, "action": "<physical description of what they are doing>", "speech_cue": "<observable speech delivery or null if not speaking>", "body_language": "<observable posture, gestures, facial expression>"}}
  ],
  "scene": [
    {{"start": <seconds>, "end": <seconds>, "location": "<specific physical place>", "type": "<indoor|outdoor|vehicle|mixed>", "lighting": "<observable lighting conditions>", "notable_features": ["<visible sign text>", "<visible structures or landmarks>"]}}
  ],
  "camera": [
    {{"start": <seconds>, "end": <seconds>, "mode": "<selfie|handheld|mounted|panning|tracking>", "framing": "<close-up|medium|wide>", "stability": "<description of how steady or shaky>", "movement": "<stationary|walking|panning_left|panning_right|tilting_up|tilting_down|zooming_in|zooming_out>"}}
  ],
  "assessment": {{"content_type": "type_here", "primary_subject_visible": [[<start>, <end>]]}}
}}

Rules:
- All timestamps must be within [{window_start}, {window_end}].
- Describe ONLY what is physically visible - "frowning, arms crossed" not "feeling upset"; no mood, atmosphere, or interpretation.
- If one continuous action spans the whole window, return a single action entry.
- If the setting never changes, return a single scene entry spanning the window.
- If the camera stays in one mode the whole window, return a single camera entry.
- speech_cue should be null (not the string "null") if the person is not speaking.
- mode: selfie (front-facing, subject holding camera), handheld (rear-facing, hand-held),
  mounted (tripod/fixed), panning (rotating), tracking (following a subject).
- No quotation marks anywhere in your answer: quoted words cannot be
  verified against the transcript, and a deterministic check strips them.
- notable_features should include any readable text on signs or buildings."""

# Each section above is the corresponding retired single-pass prompt with
# only its JSON envelope removed: the action framing, transcript line and
# audio paragraph are PROMPT_ACTION_WINDOW's; the boundaries sentence and
# environment task are PROMPT_SCENE's; the camera task and mode glossary
# are PROMPT_CAMERA's; the classification task is PROMPT_ASSESSMENT's.
# The model answers in clip-time timestamps within the window (measured:
# it echoes the window bounds), so no offsetting is applied downstream -
# only a range check.

# Compact twin of PROMPT_WINDOW_ALL: the same four tasks, answered in a
# positional schema instead of key-per-field objects. Measured 2026-09-24
# (see `expand_compact_window`): the folded answer's 365 output tokens
# are 67% of the call's wall, so the envelope is what is cut - single
# letter keys, one array per entry, no repeated key names - plus a word
# cap per prose field. Every field a downstream step reads is still
# answered (the consumer list is in `expand_compact_window`'s docstring);
# `analyze_windows` expands the compact answer back to the canonical
# shape before anything else reads it, so no consumer changes.
PROMPT_WINDOW_ALL_COMPACT = """This is a {window_dur:.0f}-second segment (seconds {window_start:.0f} to {window_end:.0f}) of a {duration:.0f}-second video clip.
{transcript_line}
This video clip carries its AUDIO TRACK - measured on mlx-vlm 0.7.2,
gemma4-unified accepts audio alongside video (`generate` takes
`audio=` with the audio marker in the prompt, and the checkpoint
carries `embed_audio` weights), so listen to HOW speech is delivered
(pace, effort, pauses, visible effort). The only WORDS that exist are
the transcript text above (if any). NEVER quote speech: do not put
words in quotation marks and do not attribute utterances to the person
on screen. `speech_cue` describes delivery - pace, effort, pauses,
mouth movement, gestures while talking - never words.

Answer all four parts in ONE response. Part 1 - actions ("a"): for each
distinct action or behavior change in this segment, report what the
person is physically doing, observable speech delivery cues (if
speaking): mouth movement, apparent volume, gestures while talking,
and observable facial expression and body language: posture, hand
position, head orientation, facial muscle state. If the person holds
or operates something (phone, tool, cup), name it.

Part 2 - scene ("s"): pre-detected scene boundaries (from automated
visual analysis) within this segment: {boundaries}. Describe the
physical environment for each part of this segment, and identify any
additional subtle environment changes the detector may have missed
(e.g., significant lighting shifts within the same location).

Part 3 - camera ("c"): describe the camera behavior in this segment.
A new entry ONLY when the camera mode meaningfully changes (e.g.,
static to walking, selfie to rear-facing, close-up to wide shot,
stable to shaky). Judge steadiness closely: handheld phone footage
usually drifts or shakes slightly, so "steady" only for a truly
locked frame.

Part 4 - assessment ("t" content type, "p" subject ranges): classify
what this segment shows. "t": one of person_talking_to_camera,
scenery, action_sequence, multiple_people, object_showcase,
transition. "p": time ranges where the main person is visible.

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
{{"a": [[<start>, <end>, "<what they are doing, max 12 words>", "<speech delivery, max 12 words, or null if not speaking>", "<posture, gestures, expression, max 12 words>"]], "s": [[<start>, <end>, "<place, max 8 words>", "<indoor|outdoor|vehicle|mixed>", "<lighting, max 8 words>", "<visible features, max 8 words each, joined with ; - or empty string>"]], "c": [[<start>, <end>, "<selfie|handheld|mounted|panning|tracking>", "<close-up|medium|wide>", "<steadiness, max 8 words>", "<stationary|walking|panning_left|panning_right|tilting_up|tilting_down|zooming_in|zooming_out>"]], "t": "type_here", "p": [[<start>, <end>]]}}

Rules:
- All timestamps must be within [{window_start}, {window_end}].
- Describe ONLY what is physically visible - "frowning, arms crossed" not "feeling upset"; no mood, atmosphere, or interpretation.
- If one continuous action spans the whole window, return a single action entry.
- If the setting never changes, return a single scene entry spanning the window.
- If the camera stays in one mode the whole window, return a single camera entry.
- The null in an "a" entry is bare null (not the string "null") when the person is not speaking.
- mode: selfie (front-facing, subject holding camera), handheld (rear-facing, hand-held),
  mounted (tripod/fixed), panning (rotating), tracking (following a subject).
- No quotation marks anywhere in your answer: quoted words cannot be
  verified against the transcript, and a deterministic check strips them.
- "s" features carry any readable text on signs or buildings.
- The "s" features string names the 2-3 most notable STATIC things
  (sign text, landmarks, furniture, plants), joined with ";" - never
  timestamps, never an empty-join that counts seconds; "" when nothing
  is notable. Burned-in captions and subtitles are NEVER features -
  they change every second and are read by other passes, so
  transcribing them here is unbounded. Never emit ";" inside a
  feature itself.
- An "s" entry is EXACTLY 6 items: start, end, place, type, lighting,
  features-string. A 7th item is a format error - stop the entry
  instead.
- Close each section's array before the next key: `"a": [[...]],
  "s": [[...]], "c": [[...]] - a missing `]]` before `"s"`, `"c"`,
  `"t"` or `"p"` loses the whole answer.
- Keep every prose string within its word cap; brevity never drops a field."""

PROMPT_OBJECTS_COARSE = """These are {n_frames} frames extracted from a {duration:.0f}-second video clip at the timestamps shown.

Frame timestamps: {frame_timestamps}

Identify every distinct person, vehicle, object, sign, or readable text visible in these frames.

For each entity, report:
- A specific, identifying description ("young man in black baseball cap and silver chain" not "person")
- Which frame timestamps it appears in (as appearance ranges)
- Its role: primary_subject (main focus of the clip), background (visible but not focal), passing (briefly visible)
- Its category: person, vehicle, object, text, structure
- Any readable text on signs, screens, stickers, or clothing

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
[
  {{"label": "<specific description>", "appearances": [[<start_s>, <end_s>]], "role": "<primary_subject|background|passing>", "category": "<person|vehicle|object|text|structure>", "readable_text": "<any text visible on or near this entity, or null>"}}
]

Rules:
- Merge continuous appearances into single ranges.
- If an entity leaves and returns, list separate appearance ranges.
- Be as specific as possible in labels (color, brand, size, distinguishing features).
- For readable text: report the exact text you can read."""

PROMPT_OBJECTS_DETAIL = """These are {n_frames} frames spanning {range_start:.0f}s to {range_end:.0f}s of a {duration:.0f}s clip.

Known entities: {entities_summary}

For each entity, refine its timestamps and add details (color, brand, text). Also report any NEW entities missed.

JSON array only, no other text:
[
  {{"label": "description", "appearances": [[start, end]], "role": "primary_subject or background or passing", "category": "person or vehicle or object or text", "readable_text": "text or null"}}
]"""

# ═══════════════════════════════════════════════════════════════════════
#  JSON Parsing
# ═══════════════════════════════════════════════════════════════════════

def _strip_markdown(text):
    """Remove markdown code block wrappers from model output."""
    import re
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
    return text.strip()


def _fix_json(text):
    """Apply common JSON fixups for sloppy model output."""
    import re
    # Remove trailing commas before ] or }
    text = re.sub(r',\s*([\]\}])', r'\1', text)
    # Remove JS-style comments
    text = re.sub(r'//[^\n]*', '', text)
    # Replace single quotes with double quotes (only outside strings — best effort)
    # This is intentionally naive; strict parsers would need a state machine
    text = text.replace("'", '"')
    return text


def _extract_objects_greedy(text):
    """Last-resort: extract individual JSON objects from broken array output.

    When the model produces something like:
        [{...}, {...}, ...some broken text...]
    or even just a series of {...} blocks without proper array wrapping,
    this function finds each top-level {...} block and parses them individually.
    """
    import re
    objects = []
    # Find all top-level brace-balanced {...} blocks
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start:i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict) and 'label' in obj:
                        objects.append(obj)
                except (json.JSONDecodeError, ValueError):
                    # Try with fixups
                    try:
                        obj = json.loads(_fix_json(candidate))
                        if isinstance(obj, dict) and 'label' in obj:
                            objects.append(obj)
                    except (json.JSONDecodeError, ValueError):
                        pass
                start = None
    return objects


def parse_json_array(text):
    """Parse a JSON array from model output. Returns list or empty list."""
    import re
    text = _strip_markdown(text)
    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        # Model returned a dict instead of array — wrap it
        if isinstance(result, dict):
            return [result]
    except (json.JSONDecodeError, ValueError):
        pass
    # Try to find array in the text
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, ValueError):
            # Try with fixups
            try:
                result = json.loads(_fix_json(match.group()))
                if isinstance(result, list):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass
    # Last resort: try fixups on the whole text
    try:
        result = json.loads(_fix_json(text))
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
    except (json.JSONDecodeError, ValueError):
        pass
    # Final fallback: greedy object extraction (picks out individual {...} blocks)
    greedy = _extract_objects_greedy(text)
    if greedy:
        return greedy
    return []


def _close_section_arrays(text):
    """Rewrite section keys opened inside the previous array.

    Measured failure mode (2026-09-24, diet-vs-full check on source
    footage, 3/24 diet calls): the compact answer drops the `]]`
    closing one section's array before the next key -
    `"a": [[...], ["s": ...` or `"s": [[...], "c": ...` - which no
    generic fixup repairs, and the retry repeats. Each rewrite below
    closes the row AND the array (a complete row already carries its
    own `]`, which the pattern consumes). These rewrites only run
    after strict parsing failed, and the result must still parse to a
    dict carrying a compact key, so a prose string that happens to
    hold the pattern cannot smuggle content past the expander
    (answers may not contain quotation marks anyway).
    """
    import re
    # A trailing comma before a closer (`...""],]` - measured
    # 2026-09-24, the model trailing both a comma and an empty extra
    # item) is the same sloppiness `_fix_json` forgives elsewhere.
    text = re.sub(r",\s*([\]\}])", r"\1", text)
    # Complete row, then the next key opened as a new element:
    # `..."], ["s": ...` -> `..."]], "s": ...`.
    text = re.sub(r'\]\s*,\s*\[\s*"(a|s|c|t|p)"\s*:',
                  r']], "\1":', text)
    # Unclosed row AND array: `...", ["s": ...` -> `..."]], "s": ...`.
    text = re.sub(r'([^\]]),\s*\[\s*"(a|s|c|t|p)"\s*:',
                  r'\1]], "\2":', text)
    # Next key straight inside the array, no bogus bracket:
    # `..."], "c": ...` -> `..."]], "c": ...`. The `[^\[\]]`
    # guard keeps the well-formed `..."]], "s": ...` untouched.
    text = re.sub(r'([^\[\]])\]\s*,\s*"(a|s|c|t|p)"\s*:',
                  r'\1]], "\2":', text)
    return text


def parse_compact_window(text):
    """Parse a compact window answer, repairing the measured bracket
    error. Returns (parsed_dict, repaired_bool).

    Strict JSON first (repaired False - the common path). Then the
    section-array rewrite above, then bounded trailing-closer attempts
    for an answer that stopped before closing. Anything still
    unparsed returns ({}, False) and the caller falls back to the
    full canonical prompt rather than dropping the window.
    """
    import re
    stripped = _strip_markdown(text)
    try:
        result = json.loads(stripped)
        if isinstance(result, dict):
            return result, False
    except (json.JSONDecodeError, ValueError):
        pass
    base = _close_section_arrays(stripped)
    # The bogus `[` the rewrite removes was usually balanced by a
    # surplus `]` before the final `}` - drop up to two.
    bases = [base]
    for _ in range(2):
        narrower = re.sub(r"\]\s*}$", "}", bases[-1], count=1)
        if narrower == bases[-1]:
            break
        bases.append(narrower)
    for root in bases:
        for candidate in [root] + [root + tail for tail in
                                   ("]}", "]]}", "]}", "]")]:
            try:
                result = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(result, dict) and set(result) & {"a", "s",
                                                           "c", "t",
                                                           "p"}:
                return result, candidate != stripped
    return {}, False


def parse_json_object(text):
    """Parse a JSON object from model output. Returns dict or empty dict."""
    text = _strip_markdown(text)
    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    # Try to find object in the text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        candidate = text[start:end]
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            # Try with fixups
            try:
                result = json.loads(_fix_json(candidate))
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass
    # Last resort: fixups on full text
    try:
        result = json.loads(_fix_json(text))
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    return {}


# ═══════════════════════════════════════════════════════════════════════
#  Model Wrapper
# ═══════════════════════════════════════════════════════════════════════

class VisionAnalyzer:
    """Wraps Gemma4 12B (MLX) for multi-pass video analysis."""

    def __init__(self, model, proc, harness=None, project_folder=None,
                 step_id="semantic_analysis"):
        t0 = time.time()
        self.model = model
        self.proc = proc
        self.load_time = time.time() - t0
        # Who looks at STILLS (the object passes below): a driving
        # host with vision answers first via the file handshake, else
        # gemma (`library/tools/still_vision.py` - the captain's
        # 2026-09-24 ruling). VIDEO passes (scene, camera, action
        # windows, assessment) never consult this: they stay on gemma,
        # because most LLMs don't process video natively.
        self.harness = harness
        self.project_folder = project_folder
        self.step_id = step_id
        print(f"  Model loaded/bound in {self.load_time:.1f}s")

    @staticmethod
    def _model_version(model):
        config = getattr(model, "config", None)
        revision = (getattr(config, "_commit_hash", None)
                    or getattr(config, "revision", None))
        return str(revision or MODEL_ID)

    def analyze(self, prompt, images=None, video=None, max_tokens=512,
                  audio=None, _route=None, _still_executor=None):
        """Run a single analysis pass. Returns (text, elapsed_seconds).

        Args:
            prompt: The text prompt.
            images: List of image file paths (for frame-based passes).
            video: Path to a video file (for video-based passes).
            max_tokens: Maximum tokens to generate.
            audio: Path to an audio file (or a video file with an audio
                track - `load_audio` reads the track) heard ALONGSIDE
                the video. Measured on mlx-vlm 0.7.2: the gemma4-unified
                path accepts it - the prompt carries the audio marker
                (`num_audios=1`), `prepare_inputs` yields `input_features`
                (250 tokens per 10 s at 40 ms/token), and the model
                projects them through `embed_audio`, whose weights the
                checkpoint carries. gemma4-unified has no separate audio
                tower (projection-only path - the `audio_tower` in
                `mlx_vlm.models.gemma4` is not the class `load()` returns
                for this checkpoint), so "embed_audio weights present" is
                the whole of the static evidence - and it is backed by a
                behavioral control, 2026-09-24: the same window intact,
                muted and swapped for another window's audio, asked what
                is said, 4/4 consistent per variant - answers track the
                audio (swapped reports the other window's words, muted
                reports none). The video loader (cv2 frames) never
                hears anything on its own, so audio in the video file
                alone is NOT heard - it must be passed here.
        """
        route = _route if _route is not None else {}
        if images:
            return self._analyze_stills(
                prompt, images, max_tokens, route,
                executor=_still_executor)
        route.update({
            "backend": "mlx_vlm",
            "model": MODEL_ID,
            "model_version": self._model_version(self.model),
            "fallback_causes": [],
            "input_kind": ("video" if video else
                           "audio" if audio else "text"),
        })
        if video or audio:
            prompt = f"<|video|>{prompt}"
            formatted = apply_chat_template(
                self.proc, self.model.config, prompt, num_images=0,
                num_audios=1 if audio else 0,
            )
        else:
            formatted = apply_chat_template(
                self.proc, self.model.config, prompt, num_images=0
            )

        t0 = time.time()
        with _semantic_inference_admission():
            with perf_ledger.span("gemma_inference", backend="mlx_vlm",
                                  model=MODEL_ID, calls=1) as cost:
                r = generate(
                    self.model, self.proc,
                    prompt=formatted,
                    image=images,
                    video=video,
                    audio=audio,
                    max_tokens=max_tokens,
                    temperature=0.1,
                    verbose=False,
                )
                cost["input_tokens"] = getattr(r, "prompt_tokens", None)
                cost["output_tokens"] = getattr(r, "generation_tokens", None)
        elapsed = time.time() - t0
        text = r.text if hasattr(r, "text") else str(r)
        return text, elapsed

    def _analyze_stills(self, prompt, images, max_tokens, route,
                        executor=None):
        """One still-frame pass: the driver first, gemma fallback.

        Routes through `library/tools/still_vision.py` and returns
        (text, elapsed) in exactly the gemma shape, so
        `analyze_with_retry`'s parse-and-retry reads a host answer
        byte-for-byte the way it read gemma's. Which stills are
        extracted, and how, is untouched - only who looks at them.
        """
        from library.tools.still_vision import inspect_stills

        t0 = time.time()
        route["input_kind"] = "still"
        if executor == "local":
            route["executor"] = "local_gemma"
            route.update({
                "backend": "mlx_vlm", "model": MODEL_ID,
                "model_version": self._model_version(self.model),
                "fallback_causes": [],
            })
            formatted = apply_chat_template(
                self.proc, self.model.config, prompt,
                num_images=len(images))
            with _semantic_inference_admission():
                with perf_ledger.span(
                        "gemma_inference", backend="mlx_vlm",
                        model=MODEL_ID, calls=1) as cost:
                    result = generate(
                        self.model, self.proc, prompt=formatted,
                        image=list(images), max_tokens=max_tokens,
                        temperature=0.1, verbose=False)
                    cost["input_tokens"] = getattr(
                        result, "prompt_tokens", None)
                    cost["output_tokens"] = getattr(
                        result, "generation_tokens", None)
            text = (result.text if hasattr(result, "text")
                    else str(result))
        else:
            if executor not in (None, "cloud"):
                raise ValueError(f"unknown still executor: {executor!r}")
            if executor == "cloud":
                route["executor"] = "cloud_host"
            text = inspect_stills(
                prompt, list(images), harness=self.harness,
                project_folder=self.project_folder, step_id=self.step_id,
                label="objects", max_tokens=max_tokens,
                route_metadata=route,
                inference_admission=_semantic_inference_admission)
            if executor == "cloud" and route.get("backend") != "host":
                raise RuntimeError(
                    "cloud still executor did not receive a host answer")
        return text, time.time() - t0

    def analyze_with_retry(self, prompt, parse_fn, images=None, video=None,
                           max_tokens=512, label="pass", audio=None,
                           request_kind=None, request_id=None,
                           still_executor=None):
        """Run analysis with one retry on parse failure.

        Args:
            prompt: The text prompt.
            parse_fn: Function to parse the output (parse_json_array or parse_json_object).
            images, video, max_tokens: Passed to analyze().
            label: Label for logging.
            audio: Passed to analyze() - heard alongside `video`.

        Returns:
            (parsed_result, raw_text, total_elapsed)
        """
        if request_kind is None:
            request_kind = ("still" if images else
                            "window" if video or audio else "text")
        request_id = request_id or label

        def run_attempt(attempt_prompt, attempt_number):
            route = {}
            started_at = time.time()
            started = time.perf_counter()
            try:
                text, elapsed = self.analyze(
                    attempt_prompt, images=images, video=video,
                    max_tokens=max_tokens, audio=audio, _route=route,
                    _still_executor=still_executor)
                result = parse_fn(text.strip())
            except Exception as exc:
                elapsed = time.perf_counter() - started
                self._record_attempt(
                    request_kind, request_id, attempt_number, "error",
                    route, elapsed, started_at,
                    error=f"{type(exc).__name__}: {exc}")
                raise
            outcome = "parsed" if result else "empty"
            self._record_attempt(
                request_kind, request_id, attempt_number, outcome,
                route, elapsed, started_at)
            return result, text, elapsed

        result, text, elapsed = run_attempt(prompt, 1)

        # Retry once if parse produced empty result
        if not result:
            retry_suffix = "\n\nIMPORTANT: Respond with ONLY valid JSON. No markdown, no explanation, no text before or after the JSON."
            result2, text2, elapsed2 = run_attempt(
                prompt + retry_suffix, 2)
            elapsed += elapsed2
            if result2:
                return result2, text2.strip(), elapsed
            else:
                print(f"    ⚠ {label}: JSON parse failed after retry")
                return result, text.strip(), elapsed

        return result, text.strip(), elapsed

    @staticmethod
    def _record_attempt(request_kind, request_id, attempt_number,
                        parser_outcome, route, elapsed, started_at,
        error=None):
        backend = route.get("backend", "unknown")
        if backend == "host":
            layer = "host_model"
        elif backend in {"mlx_vlm", "gemma_server"}:
            layer = "gemma_inference"
        else:
            layer = "semantic_inference_attempt"
        # The performance report unions intervals by backend layer. Keeping
        # each attempt in its answering backend's bucket preserves the row
        # without charging the same inference wall twice.
        attempt_fields = {
            "request_kind": request_kind, "request_id": request_id,
            "input_kind": route.get("input_kind", "unknown"),
            "attempt_number": attempt_number,
            "parser_outcome": parser_outcome, "backend": backend,
            "fallback_cause": route.get("fallback_causes", []),
            "model": route.get("model", "unknown"),
            "model_version": route.get("model_version", "unknown"),
            "error": error, "elapsed_s": round(float(elapsed), 3),
        }
        if route.get("executor") is not None:
            attempt_fields["executor"] = route["executor"]
        perf_ledger.record(layer, elapsed, started_at=started_at,
                           **attempt_fields)


# ═══════════════════════════════════════════════════════════════════════
#  Clip Metadata
# ═══════════════════════════════════════════════════════════════════════

def probe_clip(clip_path):
    """Extract metadata from a video clip using ffprobe.

    Returns None if the file is corrupt, not a video, or ffprobe fails.
    """
    try:
        _note_ffprobe_spawn()
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(clip_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)
    except (json.JSONDecodeError, OSError) as e:
        print(f"    ⚠ ffprobe failed for {clip_path.name}: {e}")
        return None

    video_stream = next(
        (s for s in info.get("streams", []) if s["codec_type"] == "video"), None
    )
    if not video_stream:
        return None

    try:
        duration = float(info["format"]["duration"])
    except (KeyError, ValueError):
        return None

    fps_parts = video_stream.get("r_frame_rate", "30/1").split("/")
    fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else 30.0
    width = int(video_stream.get("width", 1920))
    height = int(video_stream.get("height", 1080))

    return {
        "clip_id": clip_path.stem,
        "file_path": str(clip_path.resolve()),
        "duration_s": round(duration, 3),
        "fps": round(fps, 1),
        "resolution": [width, height],
    }


# ═══════════════════════════════════════════════════════════════════════
#  Frame & Video Clip Extraction
# ═══════════════════════════════════════════════════════════════════════

def extract_frames(clip_path, duration, cache_dir, interval_s=COARSE_FRAME_INTERVAL_S):
    """Extract frames at a fixed interval. Returns list of {timestamp, path}.

    Only non-empty files are returned: a failed extraction (ffmpeg error
    or a zero-byte file, which ffmpeg can leave behind with a zero exit)
    is SKIPPED, never handed to the vision pass as a frame - see
    `marker_capture`'s "WHEN THE ROUTE FAILS".  An empty file is also
    re-extracted rather than reused, so one bad run does not poison the
    cache for every later one.

    The cache resolves to an absolute directory first: these paths are
    handed to the driving host as the still-vision request's `images`,
    and `llm_handshake._checked_images` refuses a relative one (`not an
    absolute path`). The default cache (`.vision_cache`) is relative to
    wherever the pipeline was launched, which is nowhere the host can
    open. See tests/unit/context/test_vision_pipeline.py (finding 2).
    """
    cache_dir = Path(cache_dir).resolve()
    frame_dir = cache_dir / clip_path.stem / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    n_frames = max(1, int(math.ceil(duration / interval_s))) + 1
    frames = []

    for i in range(n_frames):
        timestamp = min(i * interval_s, duration - 0.1)
        out_path = frame_dir / f"frame_{i:04d}.jpg"

        if not _usable_frame(out_path):
            result = subprocess.run(
                ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(clip_path),
                 "-vframes", "1", "-q:v", "2", str(out_path)],
                capture_output=True,
            )
            if not _usable_frame(out_path):
                # A nonzero exit OR an empty file: either way there is no
                # frame here, and leaving the husk would poison the cache.
                _drop_frame(out_path)

        if _usable_frame(out_path):
            frames.append({
                "timestamp": round(timestamp, 3),
                "path": str(out_path),
            })

    return frames


def _usable_frame(out_path) -> bool:
    """A capture that happened: on disk and non-empty.

    The one predicate every frame cache in this module reads, so a
    zero-byte file is re-extracted rather than reused and never returned
    as a frame.  See `marker_capture`'s "WHEN THE ROUTE FAILS".
    """
    try:
        return out_path.stat().st_size > 0
    except OSError:
        return False


def _drop_frame(out_path) -> None:
    """Remove a failed extraction so it cannot poison the cache."""
    try:
        out_path.unlink()
    except OSError:
        pass


def extract_detail_frames(clip_path, cache_dir, ranges, fps=DETAIL_FPS):
    """Extract frames at higher density for specific time ranges.

    Args:
        clip_path: Path to the source video.
        cache_dir: Cache directory.
        ranges: List of (start, end) tuples to extract frames from.
        fps: Frames per second within each range.

    Returns:
        Dict mapping (start, end) tuples to lists of {timestamp, path}.

    The cache resolves to an absolute directory first, for the same
    reason as `extract_frames`: these paths reach the driving host as
    the still-vision request's `images` (finding 2).
    """
    cache_dir = Path(cache_dir).resolve()
    frame_dir = cache_dir / clip_path.stem / "detail_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    result = {}
    frame_counter = 0

    for range_start, range_end in ranges:
        range_dur = range_end - range_start
        n_frames = max(2, int(math.ceil(range_dur * fps)))
        range_frames = []

        for i in range(n_frames):
            timestamp = range_start + (i / max(n_frames - 1, 1)) * range_dur
            out_path = frame_dir / f"detail_{frame_counter:04d}.jpg"

            if not _usable_frame(out_path):
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(clip_path),
                     "-vframes", "1", "-q:v", "2", str(out_path)],
                    capture_output=True,
                )
                if not _usable_frame(out_path):
                    _drop_frame(out_path)

            if _usable_frame(out_path):
                range_frames.append({
                    "timestamp": round(timestamp, 3),
                    "path": str(out_path),
                })
            frame_counter += 1

        result[(range_start, range_end)] = range_frames

    return result


# ═══════════════════════════════════════════════════════════════════════
#  Native-Video Sampling
# ═══════════════════════════════════════════════════════════════════════

def native_sample_plan(duration_s, fps):
    """Frames one native-video (`video=`) model call will actually see.

    A deterministic mirror of mlx-vlm 0.7.2's decode path for gemma4
    (`utils.load_video` with the resolved sampling
    fps=2.0/min_frames=4/max_frames=32/frame_factor=2, then
    `Gemma4UnifiedVideoProcessor._sample_frames` keeping at most 32):
    `n = duration * 2.0` clamped into `[ceil(4), floor(min(32, total))]`,
    floored to a multiple of 2. `tests/unit/context/test_vision_pipeline.py`
    asserts this mirror against the real `load_video` on synthetic clips.

    Returns {"frames", "decode_fps", "effective_fps"} - the record each
    whole-video pass stores on its output. `frames` is 0 when the clip
    is too short for the loader (fewer than 2 frames survive), in which
    case the model call itself raises, same as before this plan existed.
    """
    total = max(1, round(duration_s * fps))
    n = duration_s * NATIVE_VIDEO_DECODE_FPS
    lo = 4
    hi = min(NATIVE_VIDEO_FRAMES_PER_CALL, total)
    n = min(max(n, lo), hi)
    n = math.floor(n / 2) * 2
    if n < 2 or n > total:
        n = 0
    n = min(n, NATIVE_VIDEO_FRAMES_PER_CALL)
    return {
        "frames": n,
        "decode_fps": NATIVE_VIDEO_DECODE_FPS,
        "effective_fps": round(n / duration_s, 3) if duration_s > 0 and n else 0.0,
    }


def _file_has_video(path):
    """Whether a media file carries a video stream (ffprobe, ms).

    A tail sliver cut (e.g. the 0.01 s second window of a 10.01 s clip)
    re-encodes to a husk ffmpeg writes but cv2 cannot open, and the
    model call raises "Cannot open video" on it. Such windows are
    dropped by the extractor below, never handed to a pass.
    """
    try:
        _note_ffprobe_spawn()
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return False
        return bool(json.loads(result.stdout).get("streams"))
    except (json.JSONDecodeError, OSError):
        return False


def _file_has_audio(path):
    """Whether a media file carries an audio stream (ffprobe, ms)."""
    try:
        _note_ffprobe_spawn()
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "a:0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return False
        return bool(json.loads(result.stdout).get("streams"))
    except (json.JSONDecodeError, OSError):
        return False


# Source-audio probes, cached per (path, size, mtime) so repeated
# `extract_video_clips` calls over one source in a process - the passes
# cut different window sets from the same clip - probe it once.
_SOURCE_AUDIO_CACHE = {}


def _source_has_audio(clip_path):
    """Whether the source clip carries audio, probed once per file state.

    Thin cache over `_file_has_audio`: the extractor derives every
    window's `has_audio` from this one probe instead of re-probing each
    cut, so a clip pays one audio probe no matter how many windows it
    is cut into or how many passes cut it.
    """
    try:
        st = Path(clip_path).stat()
        key = (str(clip_path), st.st_size, st.st_mtime_ns)
    except OSError:
        return _file_has_audio(clip_path)
    if key not in _SOURCE_AUDIO_CACHE:
        _SOURCE_AUDIO_CACHE[key] = _file_has_audio(clip_path)
    return _SOURCE_AUDIO_CACHE[key]


def _probe_window_streams(path):
    """One ffprobe for a window file's (has_video, has_audio, height).

    The extractor calls this at most once per pre-existing cached
    window,     to validate the two staleness rules (audio-policy mismatch,
    pre-720p-cap height) from a single spawn instead of one probe per
    question. Fresh cuts never pay it: their audio follows the cut
    recipe and their readability is an open-check, not a probe.
    """
    try:
        _note_ffprobe_spawn()
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return False, False, 0
        streams = json.loads(result.stdout).get("streams", [])
        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        height = 0
        for s in streams:
            if s.get("codec_type") == "video":
                try:
                    height = int(s.get("height", 0))
                except (TypeError, ValueError):
                    height = 0
                break
        return has_video, has_audio, height
    except (json.JSONDecodeError, OSError):
        return False, False, 0


def _clip_opens(path):
    """Whether a cut window opens as video (no subprocess).

    The one open-check per cut: a tail-sliver husk ffmpeg writes but no
    decoder can open is dropped by the extractor on this, never handed
    to a pass. cv2 is imported lazily, the way the rest of the library
    reaches it; where it is unavailable the video-stream probe answers.
    """
    try:
        import cv2
    except ImportError:
        return _file_has_video(path)
    try:
        cap = cv2.VideoCapture(str(path))
        opened = cap.isOpened()
        cap.release()
        return opened
    except Exception:
        return False


def _video_height(path):
    """Height in px of a file's first video stream, or 0 when unknown."""
    try:
        _note_ffprobe_spawn()
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode != 0:
            return 0
        streams = json.loads(result.stdout).get("streams", [])
        return int(streams[0].get("height", 0)) if streams else 0
    except (json.JSONDecodeError, OSError, ValueError, IndexError):
        return 0


class _WindowClipPrefetch:
    """Bounded, ordered producer for window cuts consumed during inference."""

    _STOP = object()

    class _Failure:
        def __init__(self, error):
            self.error = error

    def __init__(self, producer, expected_count):
        self.expected_count = expected_count
        self.extraction_wall_s = None
        self.ffprobe_spawns = None
        self._probe_start = ffprobe_spawn_count()
        self._queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._started = time.perf_counter()
        self._count = 0
        self._has_audio_count = 0
        self._clips = []
        self._iterated = False
        self._complete = False
        self._producer = producer
        self._thread = threading.Thread(
            target=self._run,
            name="vision-window-extractor",
            daemon=False,
        )
        self._thread.start()

    def __len__(self):
        return self._count if self._complete else self.expected_count

    @property
    def has_audio_count(self):
        return self._has_audio_count

    @property
    def thread_alive(self):
        return self._thread.is_alive()

    def _publish(self, item):
        while not self._stop.is_set():
            try:
                self._queue.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _run(self):
        failure = None
        try:
            for item in self._producer(self._stop):
                if self._stop.is_set() or not self._publish(item):
                    break
        except BaseException as exc:  # noqa: BLE001 - wake the consumer for any worker failure
            failure = self._Failure(exc)
        finally:
            self.extraction_wall_s = time.perf_counter() - self._started
            self.ffprobe_spawns = ffprobe_spawn_count() - self._probe_start
        if failure is not None:
            self._publish(failure)
        self._publish(self._STOP)

    def __iter__(self):
        if self._iterated:
            raise RuntimeError("prefetched window clips can only be read once")
        self._iterated = True
        try:
            while True:
                item = self._queue.get()
                if item is self._STOP:
                    self._complete = True
                    return
                if isinstance(item, self._Failure):
                    raise item.error
                self._count += 1
                self._has_audio_count += bool(item.get("has_audio"))
                self._clips.append(item)
                yield item
        finally:
            self.close()

    def close(self):
        self._stop.set()
        while self._thread.is_alive():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._thread.join(timeout=0.05)


def extract_video_clips(clip_path, duration, cache_dir, window_s=ACTION_WINDOW_S,
                        subdir="clips", prefix="clip", with_audio=True,
                        prefetch=False, window_starts_s=None):
    """Extract video clips for windowed native-video analysis.

    Uses ffmpeg to cut the source video into ~`window_s` segments. Each
    segment is re-encoded (ultrafast) to ensure clean start/end
    boundaries.

    With `with_audio=True` (the default) the window keeps an AAC audio
    track, which the windowed pass hands to the model alongside the video
    (`analyze(..., audio=...)`) - measured on mlx-vlm 0.7.2, gemma4 hears
    it. With `with_audio=False` the track is stripped (`-an`); nothing
    in the pipeline uses that now that every native-video pass runs on
    the audio-carrying action windows, and it stays only so a stale
    silent cache is re-cut rather than reused.

    A cached window whose audio presence mismatches `with_audio` is
    re-cut: caches written when action windows were stripped (`-an`)
    would otherwise be reused silently, and `load_audio` fails on them
    with "No audio streams found in file".

    A cached window taller than `WINDOW_CLIP_HEIGHT` is re-cut too:
    caches written at source resolution predate the 720p cap and would
    otherwise be reused silently, keeping the slow decode path.

    A cut that carries no video stream (the tail sliver of a clip whose
    duration is not a multiple of `window_s` re-encodes to a husk cv2
    cannot open) is DROPPED, never handed to a pass - the model call
    would raise "Cannot open video" on it. The sliver stays undescribed;
    the coverage record says so.

    Input seeking uses ffmpeg's accurate transcode seek; a failed or
    unreadable fast cut retries the former output-seek recipe. With
    `prefetch=True`, return a single-use stream with at most one cut
    queued ahead of its consumer. `window_starts_s` selects source-time
    starts for measured slices; the default tiles the full clip.

    Returns ordered {index, start, end, path, has_audio} entries, as a
    list by default or a single-use stream when prefetching.
    """
    clip_path = Path(clip_path)
    clip_dir = cache_dir / clip_path.stem / subdir
    clip_dir.mkdir(parents=True, exist_ok=True)

    if window_starts_s is None:
        n_windows = max(1, math.ceil(duration / window_s))
        window_specs = [
            (i, i * window_s, min((i + 1) * window_s, duration))
            for i in range(n_windows)
        ]
    else:
        window_specs = []
        for i, raw_start in enumerate(window_starts_s):
            start = float(raw_start)
            if start < 0 or start >= duration:
                raise ValueError(f"window start outside clip: {start}")
            window_specs.append((i, start, min(start + window_s, duration)))

    def produce_windows(stop_event):
        # One audio probe for the whole clip: every window's `has_audio`
        # derives from it, so a 408-window clip pays 1 probe, not ~1000.
        source_has_audio = _source_has_audio(clip_path) if with_audio else False

        for i, start, end in window_specs:
            if stop_event.is_set():
                return
            if end - start < MIN_WINDOW_S:
                print(f"    ⚠ Window [{start:.0f}-{end:.0f}s]: "
                      f"only {end - start:.2f}s, too short to analyze, dropped",
                      file=sys.stderr)
                continue
            out_path = clip_dir / f"{prefix}_{i:03d}.mp4"
            want_audio = with_audio and source_has_audio
            has_audio = want_audio
            needs_cut = not out_path.exists()

            if out_path.exists():
                cached_has_video, cached_has_audio, cached_height = (
                    _probe_window_streams(out_path))
                if (cached_has_audio != want_audio
                        or cached_height > WINDOW_CLIP_HEIGHT):
                    # Audio policy and the 720p cap are the two cache
                    # properties that require a re-cut.
                    out_path.unlink(missing_ok=True)
                    needs_cut = True
                elif not cached_has_video:
                    print(f"    ⚠ Window [{start:.0f}-{end:.0f}s]: "
                          "cut carries no video stream, dropped",
                          file=sys.stderr)
                    continue
                else:
                    has_audio = cached_has_audio

            if needs_cut:
                def make_temp_output(out_path=out_path):
                    with tempfile.NamedTemporaryFile(
                            prefix=f".{out_path.stem}.", suffix=".tmp.mp4",
                            dir=clip_dir, delete=False) as temp:
                        temp_path = Path(temp.name)
                    temp_path.unlink()
                    return temp_path

                def cut_command(temp_path, fast_seek, start=start, end=end,
                                want_audio=want_audio):
                    if fast_seek:
                        cmd = ["ffmpeg", "-y", "-ss", str(start),
                               "-accurate_seek", "-i", str(clip_path)]
                    else:
                        cmd = ["ffmpeg", "-y", "-i", str(clip_path),
                               "-ss", str(start)]
                    cmd += ["-t", str(end - start), "-c:v", "libx264",
                            "-preset", "ultrafast", "-crf", "23", "-vf",
                            f"scale=-2:min({WINDOW_CLIP_HEIGHT}\\,ih)"]
                    if want_audio:
                        cmd += ["-c:a", "aac"]
                    else:
                        cmd += ["-an"]
                    cmd += ["-loglevel", "error", str(temp_path)]
                    return cmd

                temp_path = make_temp_output()
                try:
                    fast_cut = subprocess.run(
                        cut_command(temp_path, fast_seek=True),
                        capture_output=True, check=False)
                    usable = (fast_cut.returncode == 0
                              and _clip_opens(temp_path))
                    if not usable:
                        # Input seeking is accurate for transcodes when
                        # ffmpeg can seek the source. If it cannot produce
                        # a readable window, retry the former decode-from-
                        # start recipe before dropping the window.
                        temp_path.unlink(missing_ok=True)
                        temp_path = make_temp_output()
                        accurate_cut = subprocess.run(
                            cut_command(temp_path, fast_seek=False),
                            capture_output=True, check=False)
                        if accurate_cut.returncode != 0 and temp_path.exists():
                            cut_has_video, cut_has_audio, _ = (
                                _probe_window_streams(temp_path))
                            if not cut_has_video:
                                print(f"    ⚠ Window [{start:.0f}-{end:.0f}s]: "
                                      "cut carries no video stream, dropped",
                                      file=sys.stderr)
                                continue
                            has_audio = cut_has_audio
                        usable = temp_path.exists() and _clip_opens(temp_path)

                    if not usable:
                        print(f"    ⚠ Window [{start:.0f}-{end:.0f}s]: "
                              "cut carries no video stream, dropped",
                              file=sys.stderr)
                        continue
                    if stop_event.is_set():
                        return
                    temp_path.replace(out_path)
                    temp_path = None
                finally:
                    if temp_path is not None:
                        temp_path.unlink(missing_ok=True)

            elif not _clip_opens(out_path):
                print(f"    ⚠ Window [{start:.0f}-{end:.0f}s]: "
                      "cut carries no video stream, dropped",
                      file=sys.stderr)
                continue

            if stop_event.is_set():
                return
            yield {
                "index": i,
                "start": round(start, 2),
                "end": round(end, 2),
                "path": str(out_path),
                "has_audio": has_audio,
            }

    if prefetch:
        return _WindowClipPrefetch(produce_windows, len(window_specs))
    return list(produce_windows(threading.Event()))


# ═══════════════════════════════════════════════════════════════════════
#  Temporal Index & Transcript Loading
# ═══════════════════════════════════════════════════════════════════════

def _temporal_index_dirs(clip_path):
    """Where a per-clip temporal index may be found, newest layout first.

    Step 1.04 writes to `Area.TEMPORAL_INDEX`, which the project layout
    puts at `pipeline_output/steps/1_04_temporal_index/index/`.  This
    function used to look only in `raw/analysis/temporal_index/`, where
    the index lived before the layout moved it - so even on a re-run,
    after 1.04 had written a full index, the vision pass found nothing
    and rules 1-3 never fired.  The old location is still read, because a
    project that predates the layout has its index there.
    """
    raw_dir = clip_path.parent           # <project>/raw
    project = raw_dir.parent             # <project>
    return [
        project / "pipeline_output" / "steps" / "1_04_temporal_index" / "index",
        raw_dir / "analysis" / "temporal_index",
    ]


def load_temporal_index(clip_path):
    """Load this clip's temporal index, or None when there is not one.

    Returns the full index dict, or None if not found.
    """
    clip_stem = clip_path.stem
    for temporal_dir in _temporal_index_dirs(clip_path):
        if not temporal_dir.is_dir():
            continue
        for idx_file in sorted(temporal_dir.glob("clip_*.json")):
            try:
                with open(idx_file) as f:
                    idx = json.load(f)
                source = idx.get("source_file", "")
                if clip_stem in source:
                    return idx
            except Exception:
                continue
    return None


def get_scene_boundaries(temporal_index):
    """Extract scene boundary timestamps from the temporal index.

    Uses the ffmpeg scene filter results. Returns sorted list of timestamps
    (excluding 0.0 and the clip end).
    """
    if not temporal_index:
        return []

    boundaries = []
    scene_data = temporal_index.get("scene_boundaries", [])
    for sb in scene_data:
        t = sb.get("timestamp", sb.get("time", None))
        if t is not None and t > 0.5:  # Skip near-zero boundaries
            boundaries.append(round(float(t), 2))

    return sorted(set(boundaries))


def load_transcript_text(clip_path, output_dir):
    """Load full transcript text for a clip.

    Tries temporal index first (WhisperX), then falls back to pipeline data.
    """
    clip_stem = clip_path.stem

    # Strategy 1: Temporal index (WhisperX — most reliable)
    temporal_dir = clip_path.parent / "analysis" / "temporal_index"
    if temporal_dir.is_dir():
        for idx_file in sorted(temporal_dir.glob("clip_*.json")):
            try:
                with open(idx_file) as f:
                    idx = json.load(f)
                if clip_stem in idx.get("source_file", ""):
                    texts = []
                    for region in idx.get("speech_regions", []):
                        t = region.get("text", "").strip()
                        if t:
                            texts.append(t)
                    if texts:
                        return " ".join(texts)
                    break
            except Exception:
                continue

    # Strategy 2: step_1_02 (clip catalog) + step_1_03 (analysis) pipeline data
    catalog_path = output_dir / "step_1_02.json"
    analysis_path = output_dir / "step_1_03.json"
    if catalog_path.exists() and analysis_path.exists():
        try:
            with open(catalog_path) as f:
                catalog = json.load(f)
            with open(analysis_path) as f:
                analysis = json.load(f)
            clip_id = None
            clip_name = clip_path.name
            for entry in catalog.get("clip_catalog", []):
                if entry.get("filename", "") == clip_name:
                    clip_id = entry.get("clip_id")
                    break
            if clip_id:
                for doc in analysis.get("semantic_analysis_documents", []):
                    if doc.get("clip_id") == clip_id:
                        texts = []
                        for block in doc.get("blocks", []):
                            t = block.get("transcript", "")
                            if t:
                                texts.append(t)
                        if texts:
                            return " ".join(texts)
        except Exception:
            pass

    # Strategy 3: rendered_transcript.json (assembled timeline)
    transcript_path = output_dir / "rendered_transcript.json"
    if transcript_path.exists():
        try:
            with open(transcript_path) as f:
                data = json.load(f)
            matching = []
            for seg in data.get("segments", []):
                src = seg.get("source_file", seg.get("file", ""))
                if clip_stem in str(src):
                    text = seg.get("text", "")
                    if text:
                        matching.append(text.strip())
            if matching:
                return " ".join(matching)
        except Exception:
            pass

    return ""


def get_window_transcript(temporal_index, window_start, window_end, full_transcript=""):
    """Extract transcript text for a specific time window.

    Uses word-level timestamps from the temporal index for precision.
    Falls back to proportion-based slicing of the full transcript.

    Returns (text, is_word_timed): `is_word_timed` is True only when
    the text comes from speech regions overlapping this window. A
    proportional slice is positioned by character count, not by words
    heard in this window, so presenting it as the window's speech
    would let the model quote words that played elsewhere.
    """
    if temporal_index:
        texts = []
        for region in temporal_index.get("speech_regions", []):
            if region["end"] > window_start and region["start"] < window_end:
                t = region.get("text", "").strip()
                if t:
                    texts.append(t)
        if texts:
            return " ".join(texts), True

    # Fallback: proportion-based
    if full_transcript:
        duration = max(1, temporal_index.get("duration", 1)) if temporal_index else 1
        chars_per_sec = len(full_transcript) / duration
        excerpt = full_transcript[
            int(window_start * chars_per_sec):int(window_end * chars_per_sec)
        ].strip()
        if excerpt:
            return excerpt, False

    return "", False


# ═══════════════════════════════════════════════════════════════════════
#  Deterministic Assessment Fields
# ═══════════════════════════════════════════════════════════════════════

# Motion thresholds are in absolute units: the mean absolute difference
# between consecutive frames on a 0-1 grey scale, recovered from the
# normalized motion curve via its `peak_mean_abs_diff`. Measured over the
# reference project's 17 raw clips, a settled shot sits below 0.02 and
# ordinary handheld drift below 0.07; only the three clips a human would
# call badly handled hold above 0.08 for a full second.
MOTION_ABS_HIGH = 0.08
MOTION_ABS_HEAD_TAIL = 0.05
MIN_HIGH_MOTION_RUN_S = 1.0
MIN_HEAD_TAIL_S = 0.5
FACE_PRESENT_THRESHOLD = 0.1
MIN_FACE_ABSENT_RUN_S = 2.0

# A usable range shorter than this is not a shot anyone can cut, so it is
# not reported as one.
MIN_USABLE_RANGE_S = 0.5

AROLL_CONTENT_TYPES = ("person_talking_to_camera", "interview", "monologue")


def _absolute_motion(motion):
    """The motion curve in absolute units, or None if its scale is unknown.

    `motion_energy.values` is normalized to each clip's own peak, so every
    clip tops out at 1.0 and a threshold on it means "fraction of this
    clip's peak". `peak_mean_abs_diff` restores the pre-normalization scale
    so the same threshold means the same thing on every clip.
    """
    if not isinstance(motion, dict):
        return None
    values = motion.get("values") or []
    peak = motion.get("peak_mean_abs_diff")
    if not values or not isinstance(peak, (int, float)) or peak <= 0:
        return None
    return [float(v) * float(peak) for v in values]


def _sustained_runs(values, sample_rate, predicate, min_seconds, duration,
                    reason):
    """Runs of `min_seconds` or more where `predicate` holds, as ranges."""
    runs = []
    if not values or not sample_rate or sample_rate <= 0:
        return runs

    min_samples = max(1, int(min_seconds * sample_rate))
    run_start = None
    n = len(values)
    for i in range(n + 1):
        if i < n and predicate(values[i]):
            if run_start is None:
                run_start = i
            continue
        if run_start is not None:
            if i - run_start >= min_samples:
                start = run_start / sample_rate
                if start < duration:
                    runs.append({
                        "start": round(start, 3),
                        "end": round(min(i / sample_rate, duration), 3),
                        "reason": reason,
                    })
            run_start = None
    return runs


def _dead_head_tail(motion_vals, motion_sr, speech_regions, duration):
    """Head/tail outside all speech whose mean motion reads as handling."""
    ranges = []
    first_speech = min(r.get("start", duration) for r in speech_regions)
    last_speech = max(r.get("end", 0) for r in speech_regions)

    if first_speech > MIN_HEAD_TAIL_S:
        head = motion_vals[:int(first_speech * motion_sr)]
        if head and sum(head) / len(head) > MOTION_ABS_HEAD_TAIL:
            ranges.append({
                "start": 0.0,
                "end": round(min(first_speech, duration), 3),
                "reason": "high_motion_head",
            })

    if duration - last_speech > MIN_HEAD_TAIL_S:
        tail = motion_vals[int(last_speech * motion_sr):]
        if tail and sum(tail) / len(tail) > MOTION_ABS_HEAD_TAIL:
            ranges.append({
                "start": round(last_speech, 3),
                "end": round(duration, 3),
                "reason": "high_motion_tail",
            })

    return ranges


def _normalize_unusable(ranges):
    """Unusable ranges sorted by start, with same-reason overlaps merged."""
    out = []
    for r in sorted(ranges, key=lambda r: (r["start"], r["end"])):
        prev = next(
            (p for p in reversed(out) if p["reason"] == r["reason"]), None)
        if prev is not None and r["start"] <= prev["end"]:
            prev["end"] = max(prev["end"], r["end"])
        else:
            out.append(dict(r))
    return sorted(out, key=lambda r: (r["start"], r["end"]))


def _complement_ranges(unusable, duration, min_usable=MIN_USABLE_RANGE_S):
    """Compute the complement of unusable ranges within [0, duration].

    Merges overlapping unusable ranges first, then returns the gaps as a
    list of [start, end] pairs covering usable time. Gaps shorter than
    `min_usable` are dropped: a sliver between two unusable runs is not
    footage anyone can cut to, and rendering it as a bound invites a
    zero-length cut.
    """
    if not unusable:
        return [[0, round(duration, 3)]]

    # Sort by start time and merge overlapping ranges
    sorted_ranges = sorted(unusable, key=lambda r: r["start"])
    merged = []
    for r in sorted_ranges:
        if merged and r["start"] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], r["end"]))
        else:
            merged.append((r["start"], r["end"]))

    # Compute complement
    usable = []
    prev_end = 0.0
    for start, end in merged:
        if start > prev_end:
            usable.append([round(prev_end, 3), round(start, 3)])
        prev_end = end
    if prev_end < duration:
        usable.append([round(prev_end, 3), round(duration, 3)])

    return [r for r in usable if r[1] - r[0] >= min_usable]


def _unmeasured():
    """The answer when no signal could measure the clip.

    `usable_ranges` is EMPTY, not `[[0, duration]]`.  Asserting the whole
    clip is usable is a claim, and nothing made it: the three fields
    beside it say `unmeasured`, `[]`, `unknown` and the fourth used to
    contradict all three.  The B-roll selector read that fourth field and
    cut project 001's first interjection out of a whip pan.  An absent
    measurement reads as absent.
    """
    return [], [], "unmeasured", []


def _compute_usable_ranges(temporal_index, duration, content_type,
                           soft_picture_ranges=None):
    """Derive usable/unusable ranges from whatever really measured the clip.

    Rules:
      1. Sustained high motion (>1s of handling-grade motion) -> unusable
      2. Dead head/tail (no speech + high motion at clip boundaries),
         A-roll clips only -> unusable
      3. Subject absence on A-roll clips (face_presence < 0.1 for >2s) -> unusable
      4. Soft picture (blur, measured off the video file) -> unusable

    Rules 1-3 read the temporal index, which step 1.04 writes AFTER this
    step runs, so on a first run they have nothing to read.  Rule 4 needs
    only the video file and is what measures a first run;
    `library/tools/analysis/picture_quality.py` computes it and states
    what its sampling rate can and cannot resolve.

    Rules 1 and 2 need the absolute motion scale; a temporal index written
    before `peak_mean_abs_diff` existed carries only a per-clip normalized
    curve, which cannot answer "is this a lot of motion", so those rules
    are skipped rather than answered wrongly.

    `soft_picture_ranges` is None when the picture was never sampled and
    `[]` when it was sampled and nothing was soft.  The two are different
    answers: the second is a measurement.

    Returns (usable_ranges, unusable_ranges, method, signals_used).
    """
    signals_used = []
    unusable = []
    duration = float(duration or 0)

    if duration <= 0:
        return _unmeasured()

    # Rule 4: Soft picture, measured off the file itself.
    if soft_picture_ranges is not None:
        signals_used.append(picture_quality.SIGNAL_NAME)
        unusable += [dict(r) for r in soft_picture_ranges]

    if not temporal_index:
        if not signals_used:
            return _unmeasured()
        unusable = _normalize_unusable(unusable)
        return (_complement_ranges(unusable, duration), unusable,
                "deterministic_v1", signals_used)

    motion = temporal_index.get("motion_energy") or {}
    motion_sr = motion.get("sample_rate_hz") or 30
    motion_vals = _absolute_motion(motion)
    if motion_vals is not None and len(motion_vals) < motion_sr:
        motion_vals = None  # Less than 1s of data

    if motion_vals is not None:
        signals_used.append("motion_energy")

        # Rule 1: Sustained high motion
        unusable += _sustained_runs(
            motion_vals, motion_sr, lambda v: v > MOTION_ABS_HIGH,
            MIN_HIGH_MOTION_RUN_S, duration, "sustained_high_motion")

        # Rule 2: Dead head/tail (A-roll clips only)
        #
        # The rule reads "outside all speech" as dead, which is right for a
        # talking-head clip - the seconds while the operator raises and
        # steadies the phone before the first word.  On a B-roll clip it is
        # exactly backwards: the silent picture IS the content, and
        # `compile_manifest` places every cutaway `video_only: True`, so the
        # audio the rule reasons about is never heard.
        #
        # Measured on project 001: the rule fired on three of seventeen
        # clips - IMG_1812 (`person_talking_to_camera`, correct) and
        # IMG_1813 and IMG_1819 (both `scenery`, wrong).  IMG_1819 is 4.7s
        # carrying one 0.36s speech region, so the head and tail around it
        # were fenced off and `usable_ranges` came out `[]` - "measured, and
        # none of it usable" - on a shot that plays fine and was chosen as
        # the closing image of the edit.  IMG_1813 came out 12%.
        #
        # Gated the same way Rule 3 below already is, and for the same
        # reason: a rule that reasons about a speaker cannot be applied to a
        # clip that has no speaker in it.
        speech_regions = temporal_index.get("speech_regions") or []
        if speech_regions and content_type in AROLL_CONTENT_TYPES:
            signals_used.append("speech_regions")
            unusable += _dead_head_tail(
                motion_vals, motion_sr, speech_regions, duration)

    # Rule 3: Subject absence (A-roll clips only)
    face = temporal_index.get("face_presence") or {}
    face_vals = face.get("values") or []
    face_sr = face.get("sample_rate_hz") or 5

    if content_type in AROLL_CONTENT_TYPES and len(face_vals) > 10:
        signals_used.append("face_presence")
        unusable += _sustained_runs(
            face_vals, face_sr, lambda v: v < FACE_PRESENT_THRESHOLD,
            MIN_FACE_ABSENT_RUN_S, duration, "subject_absent")

    if not signals_used:
        return _unmeasured()

    unusable = _normalize_unusable(unusable)
    usable = _complement_ranges(unusable, duration)

    return usable, unusable, "deterministic_v1", signals_used


def _set_usable_ranges(assessment, temporal_index, duration, content_type,
                       soft_picture_ranges=None):
    """Write the four usable-range fields onto an assessment dict."""
    usable, unusable, method, signals = _compute_usable_ranges(
        temporal_index, duration, content_type, soft_picture_ranges)
    assessment["usable_ranges"] = usable
    assessment["unusable_ranges"] = unusable
    assessment["usable_ranges_method"] = method
    assessment["usable_ranges_signals"] = signals
    return assessment


def compute_deterministic_assessment(temporal_index, transcript, duration=None,
                                     soft_picture_ranges=None):
    """Compute assessment fields that don't need the vision model.

    Returns dict with speech_present, speech_coverage, speech_coverage_method,
    camera_stability, usable_ranges, unusable_ranges, usable_ranges_method,
    usable_ranges_signals.

    **No field here reports a default as though it were measured.**  The
    vision model has no audio, so it cannot determine whether speech is
    present; ``False`` / ``0.0`` would be a fabricated claim.  That was
    applied when ``temporal_index`` was absent and not when it was present
    and carried no speech regions - so an empty region list reported zero
    speech as a measured fact, with ``speech_coverage_method`` already set
    to ``"temporal_index"`` before anything had been measured.  On project
    001 that is what put ``speech_coverage: 0.0`` on 17 of 17 clips,
    including all 10 talking-to-camera ones.

    An empty ``speech_regions`` list is NOT a measurement of silence:
    ``step_1_04_temporal_index.detect_speech_regions`` returns ``[]`` both
    when WhisperX ran and heard nothing and when WhisperX raised, and the
    two are indistinguishable from here.  So the speech fields start
    unmeasured and are only filled in by evidence: speech regions, or -
    for presence alone - a transcript.  ``speech_present`` is therefore
    ``True`` or ``None``, never ``False``.
    """
    if not temporal_index:
        result = {
            "speech_present": None,
            "speech_coverage": None,
            "speech_coverage_method": "unmeasured",
            "camera_stability": "unknown",
            "camera_stability_method": "unmeasured",
        }
        _set_usable_ranges(result, None, duration or 0, "unknown",
                           soft_picture_ranges)
        return result

    result = {
        "speech_present": None,
        "speech_coverage": None,
        "speech_coverage_method": "unmeasured",
        "camera_stability": "unknown",
        "camera_stability_method": "unmeasured",
    }

    # Support both key names: older temporal indices use "duration",
    # newer ones may use "duration_s"
    ti_duration = temporal_index.get("duration_s") or temporal_index.get("duration") or 0
    # Prefer the explicit parameter (from clip metadata) when provided
    clip_duration = duration if duration is not None else ti_duration
    # Use temporal_index duration for speech coverage (may differ from clip metadata)
    if not ti_duration:
        ti_duration = clip_duration

    # Speech coverage.  Only evidence promotes these off "unmeasured":
    # regions measure both presence and coverage, a transcript measures
    # presence alone, and neither being there measures nothing at all.
    speech_regions = temporal_index.get("speech_regions") or []
    if speech_regions:
        result["speech_present"] = True
        if ti_duration > 0:
            total_speech = sum(
                r.get("end", 0) - r.get("start", 0) for r in speech_regions
            )
            result["speech_coverage"] = round(
                min(total_speech / ti_duration, 1.0), 2)
            result["speech_coverage_method"] = "temporal_index"
    elif transcript and transcript.strip():
        # Words were transcribed for this clip, so speech is present. They
        # arrive here as one string with no timings, so how MUCH of the
        # clip is speech stays unmeasured.
        result["speech_present"] = True

    # Camera stability. ONE reading, and it says which signal answered:
    # library/tools/camera_stability.py. This used to index
    # `temporal_index["camera_motion"]["residual"]`, a key nothing has
    # ever written, so the optical-flow residual was never read on any
    # clip of any run and every label came from frame differencing.
    stability, stability_method, _residual_mean = read_camera_stability(
        temporal_index)
    result["camera_stability"] = stability
    result["camera_stability_method"] = stability_method

    # Usable ranges: Rules 1, 2 & 4 (content_type not yet known; Rule 3
    # is deferred to the assessment merge where the model provides it).
    _set_usable_ranges(result, temporal_index, clip_duration, "unknown",
                       soft_picture_ranges)

    return result


# ═══════════════════════════════════════════════════════════════════════
#  Object Merging
# ═══════════════════════════════════════════════════════════════════════

def merge_objects(all_objects, max_duration=None):
    """Merge object detections across batches by label similarity.

    Uses case-insensitive exact matching. Objects with the same normalized
    label are treated as the same entity; their appearance ranges are merged.

    If max_duration is provided, filters out appearance ranges where either
    timestamp exceeds 2x the clip duration (model hallucination guard).
    """
    if not all_objects or not isinstance(all_objects, list):
        return []

    # Sanity threshold: ranges beyond this are clearly model errors
    ts_limit = (max_duration * 2) if max_duration else None

    merged = {}
    for obj in all_objects:
        if not isinstance(obj, dict):
            continue
        label = obj.get("label", "unknown").lower().strip()
        if label in merged:
            existing = merged[label]
            # Merge appearances
            new_apps = obj.get("appearances", [])
            if isinstance(new_apps, list):
                existing_apps = existing.get("appearances", [])
                existing["appearances"] = existing_apps + new_apps
            # Keep more detailed info if available
            if obj.get("details") and not existing.get("details"):
                existing["details"] = obj["details"]
            if obj.get("readable_text") and not existing.get("readable_text"):
                existing["readable_text"] = obj["readable_text"]
        else:
            merged[label] = dict(obj)  # Copy to avoid mutation

    # Consolidate overlapping appearance ranges for each entity
    for obj in merged.values():
        apps = obj.get("appearances", [])
        if apps:
            # Filter out absurd timestamps before consolidating
            if ts_limit:
                apps = [
                    a for a in apps
                    if isinstance(a, (list, tuple)) and len(a) == 2
                    and float(a[0]) <= ts_limit and float(a[1]) <= ts_limit
                ]
            obj["appearances"] = _consolidate_ranges(apps)

    return list(merged.values())


def _consolidate_ranges(ranges):
    """Merge overlapping or adjacent time ranges."""
    if not ranges:
        return ranges
    # Flatten nested structure and ensure pairs
    flat = []
    for r in ranges:
        if isinstance(r, (list, tuple)) and len(r) == 2:
            flat.append((float(r[0]), float(r[1])))
    if not flat:
        return ranges
    flat.sort(key=lambda x: x[0])

    merged = [flat[0]]
    for start, end in flat[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 0.5:  # Merge if within 0.5s
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return [[round(s, 2), round(e, 2)] for s, e in merged]


def find_detail_ranges(coarse_objects, duration):
    """Identify time ranges that need detail-pass analysis.

    Targets entities that are NOT primary_subject and have total appearance
    less than DETAIL_MAX_APPEARANCE_S. Returns list of (start, end) tuples.

    Skips entirely if merged ranges would cover > DETAIL_MAX_COVERAGE of
    the clip duration (coarse sweep already has good data for full-coverage).
    """
    ranges = []
    for obj in coarse_objects:
        if not isinstance(obj, dict):
            continue
        if obj.get("role") == "primary_subject":
            continue
        apps = obj.get("appearances", [])
        total_dur = sum(
            (a[1] - a[0]) for a in apps
            if isinstance(a, (list, tuple)) and len(a) == 2
        )
        if total_dur < DETAIL_MAX_APPEARANCE_S:
            for a in apps:
                if isinstance(a, (list, tuple)) and len(a) == 2:
                    # Add 2s padding on each side for context
                    r_start = max(0, a[0] - 2)
                    r_end = min(duration, a[1] + 2)
                    ranges.append((r_start, r_end))

    if not ranges:
        return []

    # Merge overlapping/nearby ranges
    ranges.sort()
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + DETAIL_MERGE_GAP_S:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    # Coverage check: skip if detail would rescan most of the clip
    total_range_dur = sum(e - s for s, e in merged)
    coverage = total_range_dur / duration if duration > 0 else 1.0
    if coverage > DETAIL_MAX_COVERAGE:
        print(f"    (detail ranges cover {coverage:.0%} of clip — skipping, coarse is sufficient)")
        return []

    return [(round(s, 2), round(e, 2)) for s, e in merged]


# ═══════════════════════════════════════════════════════════════════════
#  Dimension Analyzers
# ═══════════════════════════════════════════════════════════════════════

def _scene_boundaries_text(temporal_index, start=0.0, end=None):
    """Boundary prompt text, optionally clipped to a window.

    Bounds are reported relative to `start`, since the prompt describes
    the window clip, not the whole source clip.
    """
    boundaries = get_scene_boundaries(temporal_index)
    if end is not None:
        boundaries = [b for b in boundaries if start <= b < end]
    else:
        boundaries = [b for b in boundaries if b >= start]
    if boundaries:
        boundaries_str = ", ".join(f"{t - start:.1f}s" for t in boundaries)
        return f"Detected visual changes at: {boundaries_str}"
    return "No hard scene boundaries detected (likely continuous)"


def _window_segments_in_range(segments, w_start, w_end):
    """Folded scene/camera segments that fall inside their own window.

    The folded prompt asks for clip-time timestamps within the window
    (measured: the model echoes the window bounds), so unlike the retired
    60 s passes nothing is offset here - a segment outside its window is
    a model error and is dropped, never shifted into place.
    """
    out = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (TypeError, ValueError, KeyError):
            continue
        if start < w_start - 0.01 or end > w_end + 0.01 or end <= start:
            print(f"    ⚠ Window [{w_start:.0f}-{w_end:.0f}s]: "
                  f"dropped out-of-window segment [{start}, {end}]",
                  file=sys.stderr)
            continue
        seg = dict(seg)
        seg["start"] = round(start, 3)
        seg["end"] = round(end, 3)
        out.append(seg)
    return out


def merge_camera_modes(modes):
    """Merge consecutive camera modes with identical mode/framing/stability.

    The model sometimes returns per-second entries that are all the same.
    This collapses them into a single entry spanning the full range.
    """
    if not modes or len(modes) <= 1:
        return modes

    merged = [dict(modes[0])]
    for entry in modes[1:]:
        prev = merged[-1]
        # Same mode/framing/stability → extend the previous entry
        if (entry.get("mode") == prev.get("mode") and
            entry.get("framing") == prev.get("framing") and
            entry.get("stability") == prev.get("stability")):
            prev["end"] = entry.get("end", prev.get("end"))
        else:
            merged.append(dict(entry))
    return merged


def _strip_unheard_quotations(text):
    """Remove quoted spans the model could not have verified.

    Action windows carry audio, so the model hears delivery - but the
    transcript is still the only source of words, so quoted words in the
    model's answer from a window the transcript gives nothing are
    unverifiable - measured: a quotation attributed to the person on
    screen from a window with no speech. Returns (cleaned_text_or_None,
    stripped_any). A leftover unbalanced quote mark nulls the field:
    the span patterns could not bound the invention, so the field goes
    rather than shipping half of one. A straight apostrophe (') is
    never a quote mark and is kept.
    """
    import re
    if not isinstance(text, str) or not text:
        return text, False
    span_res = (
        re.compile(r'"[^"]*"'),
        re.compile("\u201c[^\u201d]*\u201d"),
        re.compile("\u2018[^\u2019]*\\s[^\u2019]*\u2019"),
    )
    cleaned = text
    for rx in span_res:
        cleaned = rx.sub("", cleaned)
    if any(q in cleaned for q in ('"', "\u201c", "\u201d")):
        return None, True
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None, bool(text.strip())
    return cleaned, cleaned != " ".join(text.split())


def _compact_float(value):
    """A timestamp from a compact row, or None when it is not a number."""
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def expand_compact_window(obj):
    """Expand a `PROMPT_WINDOW_ALL_COMPACT` answer to the canonical shape.

    The compact schema answers the same four sections positionally:
    ``a`` (actions), ``s`` (scene), ``c`` (camera), ``t`` (content
    type) and ``p`` (subject ranges). This returns
    ``{"actions", "scene", "camera", "assessment"}`` in exactly the
    shape `analyze_windows` builds from a canonical answer, so every
    consumer below reads the expansion without knowing which prompt
    produced it. Consumers of each field, all verified 2026-09-24:

    - actions[].start/end: `_window_segments_in_range` (range check),
      `footage_segments._action_segments` (cut bounds)
    - actions[].action/body_language/speech_cue:
      `vision_schema_adapter._blocks_from_actions` (blocks visual,
      body_language, speech_cue), `footage_segments._action_segments`
      (embedded text), `analyze_windows` quote-strip
    - scene[].start/end/location/type/lighting/notable_features:
      `vision_schema_adapter.scene_prose` + `derived_keywords` (type),
      `footage_reference._scene_lines`,
      `footage_segments._scene_segments` + `_facets_from_vision`
      (type, lighting)
    - camera[].start/end/mode/framing/stability/movement:
      `merge_camera_modes` (mode/framing/stability),
      `vision_schema_adapter.camera_prose` + `derived_keywords`
      (framing/mode/movement) + `framing_summary` + `stability_summary`
      + `movement_summary`, `footage_reference._camera_lines`,
      `footage_segments._facets_from_vision`
      (framing/mode/stability/movement)
    - assessment.content_type/primary_subject_visible:
      `_merge_assessment_votes`, `vision_schema_adapter._derived_clip_type`
      + `derived_keywords`, `footage_reference._identity_line`,
      `semantic_index.index_fields`, `footage_segments._facets_from_vision`

    A section whose key is absent stays absent (None assessment, missing
    list) - the same "no key is not an empty answer" rule the canonical
    path keeps. A row with bad timestamps or prose of the wrong type is
    DROPPED, never repaired, with one measured exception: an "s" row
    longer than 6 items merges its extra string items into the features
    string (see below) - a model error must not become a placed
    segment, but neither should a hedging model lose its whole scene.
    Enum vocabularies are NOT validated here - the canonical path never
    validated them either, and refusing a new mode word would be a
    stricter gate than the baseline.
    """
    if not isinstance(obj, dict):
        return {"actions": [], "scene": [], "camera": [], "assessment": None}

    actions = []
    for row in obj.get("a") or []:
        if not isinstance(row, (list, tuple)) or len(row) != 5:
            continue
        start, end = _compact_float(row[0]), _compact_float(row[1])
        action, cue, body = row[2], row[3], row[4]
        if start is None or end is None or end <= start:
            continue
        if not isinstance(action, str) or not isinstance(body, str):
            continue
        if cue is not None and not isinstance(cue, str):
            continue
        actions.append({
            "start": start, "end": end, "action": action,
            "speech_cue": cue, "body_language": body,
        })

    scene = []
    for row in obj.get("s") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        start, end = _compact_float(row[0]), _compact_float(row[1])
        location, stype, lighting = row[2], row[3], row[4]
        if start is None or end is None or end <= start:
            continue
        if not all(isinstance(v, str) for v in (location, stype, lighting)):
            continue
        # Features travel as one ";"-joined string (measured 2026-09-24:
        # an array slot made the model count seconds instead of naming
        # things). Never emit ";" inside a feature - the split below
        # would cut it in two. The model sometimes hedges: the joined
        # string in slot 6 AND extra feature items after it (measured
        # 7-item row). Those extras merge into the string rather than
        # dropping the section - they are the same list, unrolled.
        pieces = [p for p in row[5:] if isinstance(p, str)]
        if not pieces:
            continue
        feats = ";".join(pieces)
        features = [f.strip() for f in feats.split(";")]
        features = [f for f in features if f]
        scene.append({
            "start": start, "end": end, "location": location,
            "type": stype, "lighting": lighting,
            "notable_features": features,
        })

    camera = []
    for row in obj.get("c") or []:
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            continue
        start, end = _compact_float(row[0]), _compact_float(row[1])
        mode, framing, stability, movement = row[2], row[3], row[4], row[5]
        if start is None or end is None or end <= start:
            continue
        if not all(isinstance(v, str)
                   for v in (mode, framing, stability, movement)):
            continue
        camera.append({
            "start": start, "end": end, "mode": mode,
            "framing": framing, "stability": stability,
            "movement": movement,
        })

    assessment = None
    if "t" in obj or "p" in obj:
        assessment = {}
        if isinstance(obj.get("t"), str):
            assessment["content_type"] = obj["t"]
        if "p" in obj:
            psv = obj["p"]
            if psv is None:
                assessment["primary_subject_visible"] = None
            elif isinstance(psv, list):
                ranges = []
                for rng in psv:
                    if not isinstance(rng, (list, tuple)) or len(rng) < 2:
                        continue
                    start = _compact_float(rng[0])
                    end = _compact_float(rng[1])
                    if start is None or end is None or end <= start:
                        continue
                    ranges.append([start, end])
                assessment["primary_subject_visible"] = ranges

    out = {}
    if "a" in obj:
        out["actions"] = actions
    if "s" in obj:
        out["scene"] = scene
    if "c" in obj:
        out["camera"] = camera
    out["assessment"] = assessment
    return out


def _expand_or_canonical(parsed):
    """A window answer in the shape downstream reads.

    The window prompt asks for the compact schema (`a`/`s`/`c`/`t`/`p`
    in `PROMPT_WINDOW_ALL_COMPACT`), expanded by
    `expand_compact_window`. A model that answers in the retired
    canonical keys anyway (`actions`/`scene`/`camera`/`assessment`)
    passes through untouched - its sections are already what
    `analyze_windows` reads. Compact keys win a mixed answer: the
    expansion is the asked-for shape, and a half-canonical tail is
    not a second answer.
    """
    if not isinstance(parsed, dict):
        return parsed
    if set(parsed) & {"a", "s", "c", "t", "p"}:
        return expand_compact_window(parsed)
    return parsed


def run_window_call(analyzer, compact_prompt, fallback_prompt, video,
                    audio, label, request_id=None):
    """One window's model call: compact first, full canonical fallback.

    Returns (expanded_result, compact_raw, elapsed_total, prompt_path)
    where prompt_path is "compact", "compact_repaired" or
    "full_fallback". A window the compact answer cannot describe is
    re-asked with the full canonical prompt rather than recorded as
    UNPARSED - measured 2026-09-24, the diet drops the `]]` closing a
    section array on ~1 in 8 windows and the retry repeats it, and a
    dropped window is never acceptable: the fallback costs one full
    call and its wall joins the window's total.
    """
    state = {}

    def _parse(text):
        parsed, repaired = parse_compact_window(text)
        state["repaired"] = repaired
        return parsed

    result, raw, elapsed = analyzer.analyze_with_retry(
        compact_prompt, _parse, video=video, audio=audio,
        max_tokens=MAX_TOKENS["window_all_compact"], label=label,
        request_kind="window", request_id=f"{request_id or label}:compact")
    if result:
        path = "compact_repaired" if state.get("repaired") else "compact"
        return _expand_or_canonical(result), raw, elapsed, path
    fb_result, _fb_raw, fb_elapsed = analyzer.analyze_with_retry(
        fallback_prompt, parse_json_object, video=video, audio=audio,
        max_tokens=MAX_TOKENS["window_all"],
        label=label + " fallback", request_kind="window",
        request_id=f"{request_id or label}:full_fallback")
    elapsed += fb_elapsed
    if fb_result:
        fb_result = _expand_or_canonical(fb_result)
    return fb_result, raw, elapsed, "full_fallback"


def analyze_windows(analyzer, video_clips, duration, temporal_index, transcript,
                    fps=None, request_prefix="clip"):
    """Analyze actions, scene, camera and assessment - one model call per 10s window.

    The folded call (`PROMPT_WINDOW_ALL_COMPACT`) answers all four
    native-video passes from the same 20 frames: measured 2026-09-24 on
    a real reel excerpt, one compact call per 10 s window (~20 s wall,
    ~140 output tokens) against the retired canonical prompt on the
    same window (~35 s, ~370 tokens), with counts, content_type,
    notable_features and body_language inside the canonical call's own
    run-to-run variation. Every pass therefore samples at the decode
    rate (2 fps), and a 68-minute clip takes 408 video calls instead
    of 612.

    The compact answer is expanded back to the canonical section shape
    (`_expand_or_canonical`) before anything reads it, so every
    consumer below is unchanged. A compact answer that still does not
    parse - directly or via the section-array repair in
    `parse_compact_window` - is re-asked once with the retired
    canonical prompt (`run_window_call`); only a window both answers
    fail to describe is recorded UNPARSED.

    Each window clip keeps its audio track, which the model hears
    alongside the video (`audio=`); `fps` records the sampling the call
    actually used (`native_sample_plan`) on the window entry.

    Returns list of window results. Each entry carries the `actions`
    list (the shape `analyze_actions` used to return, unchanged), plus
    `scene`, `camera` and `assessment` sections answered from the same
    call, and `prompt_path` recording which prompt described it
    ("compact", "compact_repaired" or "full_fallback"). Each entry
    carries ``parse_error`` only when the compact answer AND the full
    fallback both could not be parsed, so consumers can distinguish
    *unparsed* (the model returned gibberish twice) from *genuinely
    empty* (the model saw nothing happening).

    An answer that came back without a key is not an answer of ``[]``.
    """
    results = []

    for clip_info in video_clips:
        w_start = clip_info["start"]
        w_end = clip_info["end"]
        w_dur = w_end - w_start

        # Get transcript for this window
        w_transcript, w_word_timed = get_window_transcript(
            temporal_index, w_start, w_end, full_transcript=transcript
        )
        if w_transcript and w_word_timed:
            transcript_line = f'\nSPEECH IN THIS SEGMENT: "{w_transcript}"'
        elif w_transcript:
            transcript_line = (
                f'\nAPPROXIMATE SPEECH NEAR THIS SEGMENT (positioned by '
                f'character count, not word-timed - do not quote it as '
                f'heard here): "{w_transcript}"'
            )
        else:
            transcript_line = "\n(No speech in this segment.)"

        boundaries_text = _scene_boundaries_text(
            temporal_index, start=w_start, end=w_end)
        prompt_args = {
            "window_start": w_start,
            "window_end": w_end,
            "window_dur": w_dur,
            "duration": duration,
            "transcript_line": transcript_line,
            "boundaries": boundaries_text,
        }
        prompt = PROMPT_WINDOW_ALL_COMPACT.format(**prompt_args)
        fallback_prompt = PROMPT_WINDOW_ALL.format(**prompt_args)

        video = clip_info["path"]
        audio = video if clip_info.get("has_audio") else None
        result, _raw, elapsed, prompt_path = run_window_call(
            analyzer, prompt, fallback_prompt, video, audio,
            f"Window [{w_start:.0f}-{w_end:.0f}s]",
            request_id=(f"{request_prefix}:window:"
                        f"{w_start:.3f}-{w_end:.3f}"))

        # `parse_compact_window` returns {} on total parse failure.
        # An empty dict is falsy; a dict with empty sections is truthy.
        # The distinction matters: {} means neither the compact nor
        # the fallback response could be parsed at all, while
        # {"actions": []} means the model saw nothing happening in
        # this window.
        parse_failed = not result

        # A section that came back without its key is not an answer of
        # `[]`: the key's absence is carried as the section missing, so
        # a dropped scene section does not read as "no scene here".
        actions = result.get("actions") or [] if result else []
        scene = _window_segments_in_range(
            result.get("scene"), w_start, w_end) if result else []
        camera = _window_segments_in_range(
            result.get("camera"), w_start, w_end) if result else []
        assessment = result.get("assessment") if result else None

        entry = {
            "window": [w_start, w_end],
            "actions": actions,
            "scene": scene,
            "camera": camera,
            "assessment": assessment,
            "analysis_time_s": round(elapsed, 2),
            # Which prompt described this window - "compact",
            # "compact_repaired" or "full_fallback" (see
            # `run_window_call`). A dropped window is never silent
            # about how it was recovered.
            "prompt_path": prompt_path,
            # The window's own audio track reaches the model (`audio=`
            # above) when the source clip has one; a sourceless clip
            # yields silent windows, which hear nothing.
            "has_audio": bool(clip_info.get("has_audio")),
        }
        if fps:
            entry["sampling"] = {
                "window_s": round(w_dur, 2),
                **native_sample_plan(w_dur, fps),
            }
        if parse_failed:
            entry["parse_error"] = True
            print(f"    ⚠ Window [{w_start:.0f}-{w_end:.0f}s]: "
                  f"window recorded as UNPARSED (neither the compact "
                  f"nor the full fallback response parsed)",
                  file=sys.stderr)

        # The model hears HOW speech is delivered in the window's audio,
        # but the transcript stays the only source of WORDS. Quoted words
        # in its answer cannot be verified against the transcript the
        # window was given, so where the window has no words of its own
        # they are still stripped - measured on a wordless window as a
        # quotation attributed to the person on screen. The prompt
        # forbids them; this enforces it where the transcript gives the
        # window no words of its own (word-timed speech may echo the
        # transcript text it was given, which is attributed correctly by
        # construction).
        if not (w_transcript and w_word_timed):
            quote_stripped = False
            for action in entry["actions"]:
                if not isinstance(action, dict):
                    continue
                for field in ("action", "speech_cue", "body_language"):
                    cleaned, stripped = _strip_unheard_quotations(
                        action.get(field))
                    if stripped:
                        quote_stripped = True
                        action[field] = cleaned
            if quote_stripped:
                entry["speech_quote_stripped"] = True
                print(f"    ⚠ Window [{w_start:.0f}-{w_end:.0f}s]: "
                      f"stripped quoted speech the transcript-less window "
                      f"could not have verified", file=sys.stderr)

        results.append(entry)

    return results


def _window_cache_params(clip_info, duration, temporal_index, transcript,
                         fps):
    """Inputs that can change the answer for one folded window.

    Transcript and boundary values are the local values rendered into
    that window's prompt. The source content digest and the reached
    window method digest are supplied by ``LayerCache``.
    """
    start = clip_info["start"]
    end = clip_info["end"]
    window_duration = end - start
    transcript_slice, word_timed = get_window_transcript(
        temporal_index, start, end, full_transcript=transcript)
    return {
        "duration_s": duration,
        "transcript_slice": {
            "text": transcript_slice,
            "word_timed": word_timed,
        },
        "temporal_prompt_measurements": {
            "scene_boundaries": _scene_boundaries_text(
                temporal_index, start=start, end=end),
        },
        "has_audio": bool(clip_info.get("has_audio")),
        "model_id": MODEL_ID,
        "sampling": {
            "action_window_s": ACTION_WINDOW_S,
            "minimum_window_s": MIN_WINDOW_S,
            "window_clip_height": WINDOW_CLIP_HEIGHT,
            "native_video_decode_fps": NATIVE_VIDEO_DECODE_FPS,
            "native_video_frames_per_call": NATIVE_VIDEO_FRAMES_PER_CALL,
            "source_fps": fps,
            "sample_plan": (native_sample_plan(window_duration, fps)
                            if fps else None),
        },
    }


def analyze_windows_cached(analyzer, video_clips, duration, temporal_index,
                           transcript, fps, layer_cache,
                           request_prefix="clip"):
    """Read or measure each window independently, preserving input order.

    Clips are consumed as they arrive so the bounded extractor can stay
    one cut ahead during inference. On a cold cache, each valid clip is
    still analyzed in the original order by the unchanged
    ``analyze_windows`` path. Cache hits skip only their own model call.
    """
    results = []
    for clip_info in video_clips:
        params = _window_cache_params(
            clip_info, duration, temporal_index, transcript, fps)
        cached = layer_cache.get_window(
            clip_info["start"], clip_info["end"], params)
        if cached is not None:
            results.append(cached)
            continue

        measured = analyze_windows(
            analyzer, [clip_info], duration, temporal_index, transcript,
            fps=fps, request_prefix=request_prefix)
        if len(measured) != 1:
            raise RuntimeError(
                "analyze_windows did not return one result for a valid "
                "window clip")
        result = measured[0]
        results.append(result)
        layer_cache.put_window(
            clip_info["start"], clip_info["end"], params, result)

    return results


def analyze_objects_coarse_batch(analyzer, frames, duration, batch_idx,
                                 n_batches, request_prefix="clip",
                                 still_executor=None):
    """Answer one established coarse batch without changing its prompt."""
    batch_paths = [frame["path"] for frame in frames]
    batch_ts = [frame["timestamp"] for frame in frames]
    ts_str = ", ".join(f"{ts:.1f}s" for ts in batch_ts)
    prompt = PROMPT_OBJECTS_COARSE.format(
        n_frames=len(frames), duration=duration, frame_timestamps=ts_str)
    route_options = ({"still_executor": still_executor}
                     if still_executor is not None else {})
    result, _raw, elapsed = analyzer.analyze_with_retry(
        prompt, parse_json_array, images=batch_paths,
        max_tokens=MAX_TOKENS["objects_coarse"],
        label=f"Objects coarse batch {batch_idx+1}/{n_batches}",
        request_kind="object-coarse",
        request_id=(f"{request_prefix}:object-coarse:"
                    f"batch:{batch_idx+1}/{n_batches}"),
        **route_options)
    return result, elapsed


def analyze_objects_coarse(analyzer, frames, duration,
                           request_prefix="clip"):
    """Object detection — coarse sweep with batched frames.

    Returns (merged_objects_list, total_elapsed).
    """
    n_frames = len(frames)
    n_batches = max(1, int(math.ceil(n_frames / COARSE_BATCH_SIZE)))
    all_objects = []
    total_elapsed = 0

    for batch_idx in range(n_batches):
        start_i = batch_idx * COARSE_BATCH_SIZE
        end_i = min(start_i + COARSE_BATCH_SIZE, n_frames)
        result, elapsed = analyze_objects_coarse_batch(
            analyzer, frames[start_i:end_i], duration, batch_idx, n_batches,
            request_prefix=request_prefix)
        total_elapsed += elapsed
        all_objects.extend(result)

    merged = merge_objects(all_objects, max_duration=duration)
    return merged, total_elapsed


def analyze_objects_detail_batch(analyzer, frames, coarse_objects, duration,
                                 range_start, range_end, batch_start,
                                 request_prefix="clip", still_executor=None):
    """Answer one established detail batch after its coarse result exists."""
    relevant_entities = []
    for obj in coarse_objects:
        for appearance in obj.get("appearances", []):
            if isinstance(appearance, (list, tuple)) and len(appearance) == 2:
                if appearance[1] > range_start and appearance[0] < range_end:
                    relevant_entities.append(obj.get("label", "unknown"))
                    break
    entities_str = (
        ", ".join(relevant_entities)
        if relevant_entities else "(none previously detected)")
    frame_paths = [frame["path"] for frame in frames]
    prompt = PROMPT_OBJECTS_DETAIL.format(
        n_frames=len(frames), duration=duration,
        range_start=range_start, range_end=range_end,
        entities_summary=entities_str)
    route_options = ({"still_executor": still_executor}
                     if still_executor is not None else {})
    return analyzer.analyze_with_retry(
        prompt, parse_json_array, images=frame_paths,
        max_tokens=MAX_TOKENS["objects_detail"],
        label=f"Objects detail [{range_start:.0f}-{range_end:.0f}s]",
        request_kind="object-detail",
        request_id=(f"{request_prefix}:object-detail:"
                    f"{range_start:.3f}-{range_end:.3f}:"
                    f"batch:{batch_start}-{batch_start + len(frames)}"),
        **route_options)


def analyze_objects_detail(analyzer, clip_path, cache_dir, coarse_objects,
                           duration, request_prefix="clip"):
    """Object detection — targeted detail pass for transient entities.

    Returns (refined_objects_list, total_elapsed).
    """
    detail_ranges = find_detail_ranges(coarse_objects, duration)
    if not detail_ranges:
        return coarse_objects, 0

    # Extract detail frames
    range_frames = extract_detail_frames(clip_path, cache_dir, detail_ranges)

    all_detail_objects = []
    total_elapsed = 0

    for (r_start, r_end), frames in range_frames.items():
        if not frames:
            continue

        for batch_start in range(0, len(frames), DETAIL_BATCH_SIZE):
            batch = frames[batch_start:batch_start + DETAIL_BATCH_SIZE]
            result, _raw, elapsed = analyze_objects_detail_batch(
                analyzer, batch, coarse_objects, duration, r_start, r_end,
                batch_start, request_prefix=request_prefix)
            total_elapsed += elapsed
            all_detail_objects.extend(result)

    # Merge detail results into coarse results
    combined = list(coarse_objects) + all_detail_objects
    merged = merge_objects(combined, max_duration=duration)
    return merged, total_elapsed


def _merge_assessment_votes(window_entries):
    """Folded assessment sections merged to clip level.

    `content_type` is the most frequent window vote (ties go to the
    earliest window); `primary_subject_visible` is the union of the
    windows' ranges, which arrive in clip time already (see
    `_window_segments_in_range`) and are only validated, never offset.
    When no window parsed, `content_type` is "unknown" and
    `primary_subject_visible` is None - the same absent-measurement the
    retired single call reported.
    """
    from collections import Counter

    votes = []
    psv_all = []
    explicit_empty = 0
    voted_windows = 0
    for entry in window_entries:
        result = (entry or {}).get("assessment")
        if result:
            voted_windows += 1
            votes.append(result.get("content_type", "unknown"))
            psv = result.get("primary_subject_visible")
            if psv == []:
                explicit_empty += 1
            for rng in psv or []:
                try:
                    start = round(float(rng[0]), 3)
                    end = round(float(rng[1]), 3)
                except (TypeError, ValueError, IndexError):
                    continue
                w = entry.get("window") or [0, 0]
                if start < w[0] - 0.01 or end > w[1] + 0.01 or end <= start:
                    print(f"    ⚠ Window [{w[0]:.0f}-{w[1]:.0f}s]: "
                          f"dropped out-of-window subject range "
                          f"[{start}, {end}]", file=sys.stderr)
                    continue
                psv_all.append([start, end])

    if votes:
        counts = Counter(votes)
        first_seen = {v: i for i, v in enumerate(votes)}
        content_type = max(counts, key=lambda v: (counts[v], -first_seen[v]))
        if psv_all:
            primary_subject_visible = sorted(psv_all)
        elif explicit_empty == voted_windows:
            # Every window that answered returned `[]`: the model really
            # did say the subject is nowhere, and that is kept.
            primary_subject_visible = []
        else:
            # An answer WITHOUT the key is not an answer of `[]`:
            # carried as None.
            primary_subject_visible = None
    else:
        content_type = "unknown"
        primary_subject_visible = None

    return content_type, primary_subject_visible


def _uses_image_capable_still_host(analyzer):
    """Whether object stills are answered by a declared image host.

    Unknown harnesses remain on the existing serial path so the still
    router keeps owning its established refusal and message. The helper
    only opts into overlap for a host the router explicitly declares
    image-capable.
    """
    from library.tools.still_vision import HOST_SEES_IMAGES, resolve_harness

    harness = resolve_harness(getattr(analyzer, "harness", None))
    return HOST_SEES_IMAGES.get(harness) is True


def _iter_checking_worker(video_clips, future):
    """Yield windows in order, surfacing a failed still worker between them."""
    iterator = iter(video_clips)
    try:
        while True:
            if future.done():
                future.result()
            try:
                clip_info = next(iterator)
            except StopIteration:
                return
            yield clip_info
    finally:
        close_iterator = getattr(iterator, "close", None)
        if close_iterator is not None:
            close_iterator()
        close_source = getattr(video_clips, "close", None)
        if close_source is not None:
            close_source()


def _finish_assessment(deterministic, content_type, primary_subject_visible,
                       temporal_index, duration, soft_picture_ranges):
    """Merge model fields into the deterministic dict (shared tail).

    Usable ranges are set AFTER the model merge so the model result
    cannot override them.
    """
    assessment = dict(deterministic)
    assessment["content_type"] = content_type
    assessment["primary_subject_visible"] = primary_subject_visible

    # Usable ranges: deterministic measurement from temporal index signals.
    # Re-compute with the model's content_type so Rule 3 (subject absence)
    # can fire for A-roll clips.  Set AFTER the model merge above so the
    # model result cannot override.
    _set_usable_ranges(
        assessment, temporal_index, duration, assessment["content_type"],
        soft_picture_ranges)

    return assessment


# ═══════════════════════════════════════════════════════════════════════
#  Main Analysis Orchestrator
# ═══════════════════════════════════════════════════════════════════════

def analyze_clip(analyzer, clip_meta, frames, video_clips, transcript,
                 temporal_index, cache_dir, layer_cache=None, clock=None,
                 precomputed_layers=None):
    """Orchestrate all dimension passes for a single clip.

    Execution order:
      Group A (independent): Folded windows (actions + scene + camera +
        assessment sections, one call per 10 s window), Objects coarse
      Group B (dependent):   Objects detail (depends on coarse results)
      Group C (final):       Assessment merge (votes + deterministic tail)

    Every native-video (`video=`) pass runs on the 10 s windows, so each
    call sees 20 frames at 2 fps - no pass samples below that floor.

    `layer_cache` (a `measurement_layers.LayerCache`) supplies each
    measured layer it already holds and stores the ones measured here;
    `frames` / `video_clips` are not read for a layer it supplies. None
    measures everything, as before.

    With a declared image-capable host, the serial coarse/detail object
    branch runs on one worker while this thread processes ordered windows.
    `clock` is injectable so measurement tests do not patch the shared
    process clock.
    """
    clock = clock if clock is not None else time.time
    cached = {layer: layer_cache.get(layer)
              for layer in MEASUREMENT_LAYERS} if layer_cache else {}
    cached.update(precomputed_layers or {})
    clip_id = clip_meta["clip_id"]
    duration = clip_meta["duration_s"]
    video_path = clip_meta["file_path"]
    clip_path = Path(clip_meta["file_path"])
    fps = clip_meta.get("fps") or 30.0

    n_window_calls = (len(cached["windows"]["windows"])
                      if cached.get("windows") else len(video_clips))
    n_obj_coarse_calls = max(1, int(math.ceil(
        len(frames or []) / COARSE_BATCH_SIZE)))
    n_estimated = (n_window_calls + n_obj_coarse_calls
                   + 1)  # +detail is variable

    print(f"\n{'═'*60}")
    print(f"  Analyzing: {clip_id} ({duration:.1f}s)")
    print(f"  Estimated model calls: {n_estimated}+ "
          f"({n_window_calls} folded windows + "
          f"{n_obj_coarse_calls} obj coarse + detail)")
    print(f"{'═'*60}")

    total_time = 0
    total_calls = 0

    def measure_objects():
        """Run the existing coarse-then-detail object route in order."""
        objects_time = 0
        objects_calls = 0
        n_frames = len(frames)

        # 2. Objects - coarse sweep
        print(f"  [Objects] Coarse sweep ({len(frames)} frames, "
              f"{n_obj_coarse_calls} batch(es))...", end=" ", flush=True)
        coarse_objects, elapsed = analyze_objects_coarse(
            analyzer, frames, duration, request_prefix=clip_id)
        objects_time += elapsed
        objects_calls += n_obj_coarse_calls
        print(f"({elapsed:.1f}s) → {len(coarse_objects)} entities")

        # 3. Objects - detail pass, dependent on the coarse result
        detail_ranges = find_detail_ranges(coarse_objects, duration)
        if detail_ranges:
            n_detail_ranges = len(detail_ranges)
            print(f"  [Objects] Detail pass ({n_detail_ranges} range(s))...",
                  end=" ", flush=True)
            objects, elapsed = analyze_objects_detail(
                analyzer, clip_path, cache_dir, coarse_objects, duration,
                request_prefix=clip_id)
            objects_time += elapsed
            objects_calls += max(1, n_detail_ranges)  # Approximate
            print(f"({elapsed:.1f}s) → {len(objects)} entities (refined)")
        else:
            objects = coarse_objects
            print("  [Objects] Detail pass skipped (no transient entities)")

        return objects, objects_time, objects_calls, n_frames

    objects_executor = None
    objects_future = None
    objects_result = None
    overlap_objects = (
        not cached.get("objects")
        and not cached.get("windows")
        and _uses_image_capable_still_host(analyzer)
    )
    if overlap_objects:
        # The worker owns only the sequential host-backed object calls.
        # Cache writes and profile composition stay on this thread.
        objects_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="semantic-object-stills")
        objects_future = objects_executor.submit(measure_objects)

    # ── Group A: Independent passes ──────────────────────────────────

    # 1. Folded windows: actions + scene + camera + assessment sections
    if cached.get("windows"):
        print(f"  [Windows] {n_window_calls} windows reused "
              f"(measurement layer cache)")
        windows = cached["windows"]["windows"]
        window_inference_wall_s = cached["windows"]["inference_wall_s"]
        n_video_clips = cached["windows"]["video_clips_extracted"]
    else:
        print(f"  [Windows] {n_window_calls} windows × {ACTION_WINDOW_S}s (video+audio clips)...")
        _inference_t0 = clock()
        window_source = (
            _iter_checking_worker(video_clips, objects_future)
            if objects_future is not None else video_clips)
        try:
            if layer_cache:
                windows = analyze_windows_cached(
                    analyzer, window_source, duration, temporal_index,
                    transcript, fps, layer_cache, request_prefix=clip_id)
            else:
                windows = analyze_windows(
                    analyzer, window_source, duration, temporal_index,
                    transcript, fps=fps, request_prefix=clip_id)
            if objects_future is not None:
                objects_result = objects_future.result()
        except BaseException:
            if objects_future is not None:
                close_window_source = getattr(window_source, "close", None)
                if close_window_source is not None:
                    close_window_source()
                close_windows = getattr(video_clips, "close", None)
                if close_windows is not None:
                    close_windows()
            if objects_executor is not None:
                objects_executor.shutdown(wait=True, cancel_futures=True)
                objects_executor = None
            raise
        else:
            if objects_executor is not None:
                objects_executor.shutdown(wait=True)
                objects_executor = None
        window_inference_wall_s = round(clock() - _inference_t0, 2)
        n_video_clips = len(video_clips)
        if layer_cache:
            layer_cache.put("windows", {
                "windows": windows,
                "inference_wall_s": window_inference_wall_s,
                "video_clips_extracted": n_video_clips,
            })
    n_window_calls = len(windows)
    window_time = sum(a.get("analysis_time_s", 0) for a in windows)
    total_action_count = sum(len(a.get("actions", [])) for a in windows)
    total_time += window_time
    total_calls += n_window_calls
    for a in windows:
        w = a["window"]
        n_acts = len(a.get("actions", []))
        t = a.get("analysis_time_s", 0)
        tag = " ⚠ UNPARSED" if a.get("parse_error") else ""
        print(f"    [{w[0]:.0f}-{w[1]:.0f}s] {n_acts} action(s), "
              f"{len(a.get('scene', []))} scene(s), "
              f"{len(a.get('camera', []))} mode(s) ({t:.1f}s){tag}")
    unparsed = [a for a in windows if a.get("parse_error")]
    if unparsed:
        print(f"  ⚠ {len(unparsed)} of {len(windows)} window(s) "
              f"could not be parsed and are recorded as UNPARSED",
              file=sys.stderr)

    # The folded sections arrive in clip time (see
    # `_window_segments_in_range`); the merge runs once over the
    # concatenated windows, so an identical camera mode spanning a
    # window boundary still collapses.
    scene = []
    camera = []
    for a in windows:
        scene.extend(a.get("scene", []))
        camera.extend(a.get("camera", []))
    # The model describes whatever span it feels like - on project 001
    # four long clips stop at 13.9-18.9 s while one 85.8 s clip was
    # described whole (issue #302). Normalize once here so one sloppy
    # end does not become fifteen quiet misreads, and record what share
    # of the clip was actually described: nothing downstream may invent
    # a location for the rest.
    scene = normalize_segments(scene, duration)
    scene_coverage = coverage_summary(scene, duration)
    print(f"  [Scene] → {len(scene)} scene(s), "
          f"{scene_coverage['ratio']:.0%} of {duration:.1f}s described")
    # The same folded shape as the scene sections, so the same sloppy
    # bounds - normalized and merged for the same reason.
    camera = normalize_segments(merge_camera_modes(camera), duration)
    print(f"  [Camera] → {len(camera)} mode(s)")
    # `actions` keeps the per-window shape the retired `analyze_actions`
    # returned: downstream joins each window to the catalog by it.
    actions = [
        {
            "window": a["window"],
            "actions": a["actions"],
            "analysis_time_s": a["analysis_time_s"],
            "prompt_path": a["prompt_path"],
            **{k: a[k] for k in ("has_audio", "sampling", "parse_error",
                                 "speech_quote_stripped") if k in a},
        }
        for a in windows
    ]

    # Per-pass sampling records, in the shape the 60 s passes kept: one
    # {start, end, frames, decode_fps, effective_fps} entry per window
    # the pass answered from. All four passes now answer from the same
    # windows, so the four lists are identical - recorded per pass
    # anyway, so a reader never has to know they share calls.
    sampling_windows = [
        {"start": a["window"][0], "end": a["window"][1],
         **native_sample_plan(a["window"][1] - a["window"][0], fps)}
        for a in windows
    ]

    if cached.get("objects"):
        objects = cached["objects"]["objects"]
        n_frames = cached["objects"]["frames_extracted"]
        total_time += cached["objects"]["analysis_time_s"]
        total_calls += cached["objects"]["model_calls"]
        print(f"  [Objects] {len(objects)} entities reused "
              f"(measurement layer cache)")
    else:
        if objects_result is None:
            objects, objects_time, objects_calls, n_frames = measure_objects()
        else:
            objects, objects_time, objects_calls, n_frames = objects_result
        total_time += objects_time
        total_calls += objects_calls
        if layer_cache:
            layer_cache.put("objects", {
                "objects": objects,
                "frames_extracted": n_frames,
                "analysis_time_s": objects_time,
                "model_calls": objects_calls,
            })

    # ── Group C: Final synthesis ─────────────────────────────────────

    # 4. Assessment — hybrid (deterministic + folded votes)
    #
    # The picture is sampled ONCE here and handed to the merge below.
    # `None` back means it could not be sampled, and that is carried
    # through as an absent measurement rather than smoothed into "fine".
    print(f"  [Picture] Sharpness at "
          f"{picture_quality.SAMPLE_RATE_HZ:g}Hz...", end=" ", flush=True)
    t_pic = clock()
    if cached.get("picture"):
        soft_ranges = cached["picture"]["soft_ranges"]
    else:
        soft_ranges = picture_quality.measure_soft_picture(
            video_path, duration)
        # An unmeasured picture is not stored: the next compose tries
        # again rather than carrying the absence forward.
        if layer_cache and soft_ranges is not None:
            layer_cache.put("picture", {"soft_ranges": soft_ranges})
    t_pic = clock() - t_pic
    if soft_ranges is None:
        print(f"({t_pic:.1f}s) → unmeasured")
    else:
        soft_s = sum(r["end"] - r["start"] for r in soft_ranges)
        print(f"({t_pic:.1f}s) → {len(soft_ranges)} soft range(s), {soft_s:.1f}s")

    deterministic = compute_deterministic_assessment(
        temporal_index, transcript, duration=duration,
        soft_picture_ranges=soft_ranges)
    print(f"  [Assessment] Hybrid (votes from {n_window_calls} window(s) + deterministic)...",
          end=" ", flush=True)
    t_assess = clock()
    content_type, primary_subject_visible = _merge_assessment_votes(windows)
    assessment = _finish_assessment(
        deterministic, content_type, primary_subject_visible,
        temporal_index, duration, soft_ranges)
    t_assess = clock() - t_assess
    print(f"({t_assess:.1f}s) → {assessment.get('content_type', '?')}")

    # ── Build clip profile ───────────────────────────────────────────

    profile = {
        "clip_id": clip_id,
        "file_path": clip_meta["file_path"],
        "duration_s": duration,
        "fps": clip_meta["fps"],
        "resolution": clip_meta["resolution"],
        "transcript": transcript or "",

        "scene": scene,
        "camera": camera,
        "actions": actions,
        "objects": objects,
        "assessment": assessment,

        # What share of the clip `scene` actually describes (#302). A
        # machine-readable pin of the measurement the run log prints
        # above; the prose every consumer reads carries the same gaps
        # in words (`vision_schema_adapter.scene_prose`).
        "scene_coverage": scene_coverage,

        "analysis_metadata": {
            "pipeline_version": "v3",
            "model": MODEL_ID,
            "total_model_calls": total_calls,
            "analysis_time_s": round(total_time, 2),
            # The folded-window inference wall for THIS clip - the
            # other half of the extraction-vs-inference split the
            # runner prints per clip. A sum of per-window model times
            # is not a wall (retries and gaps hide in it); this is.
            "window_inference_wall_s": window_inference_wall_s,
            "frames_extracted": n_frames,
            "video_clips_extracted": n_video_clips,
            # What each native-video (`video=`) call actually saw.
            # Every such call is capped at 32 frames (mlx-vlm 0.7.2
            # gemma4 path: decode at 2 fps, processor keeps at most
            # 32), so one call covers one 10 s window at 20 frames -
            # the 2 fps floor holds on every pass. Each window entry
            # records {start, end, frames, decode_fps, effective_fps}
            # (`native_sample_plan`); each action entry carries its own
            # `sampling` plus `has_audio`.
            "video_sampling": {
                "frames_per_call": NATIVE_VIDEO_FRAMES_PER_CALL,
                "decode_fps": NATIVE_VIDEO_DECODE_FPS,
                "window_s": ACTION_WINDOW_S,
                "windows": {
                    "scene": [dict(w) for w in sampling_windows],
                    "camera": [dict(w) for w in sampling_windows],
                    "assessment": [dict(w) for w in sampling_windows],
                },
            },
        },
    }

    if layer_cache:
        # Which layers this compose reused and which it measured, each
        # under the key that names its source, method and parameters.
        # The timings above are the MEASUREMENT's, whichever run paid.
        profile["analysis_metadata"]["measurement_layers"] = (
            layer_cache.record())

    # ── Summary ──────────────────────────────────────────────────────

    print(f"\n  ✓ Complete in {total_time:.1f}s ({total_calls} model calls)")
    print(f"    Scene: {len(scene)} | Camera: {len(camera)} | "
          f"Actions: {total_action_count} across {len(actions)} windows | "
          f"Objects: {len(objects)}")
    _cov = assessment.get('speech_coverage')
    _cov_str = f"{_cov:.0%}" if _cov is not None else "unmeasured"
    _signals = assessment.get("usable_ranges_signals") or []
    print(f"    Type: {assessment.get('content_type', '?')} | "
          f"Speech: {_cov_str} coverage | "
          f"Stability: {assessment.get('camera_stability', '?')}")
    print(f"    Usable: {assessment.get('usable_ranges_method', '?')}"
          f"{' (' + ', '.join(_signals) + ')' if _signals else ''} → "
          f"{len(assessment.get('usable_ranges') or [])} range(s), "
          f"{len(assessment.get('unusable_ranges') or [])} excluded")

    return profile


# ═══════════════════════════════════════════════════════════════════════
#  Pipeline Runner
# ═══════════════════════════════════════════════════════════════════════

class _LazyAnalyzer:
    """A `VisionAnalyzer` whose model loads on first use, and unloads on exit.

    The managed-model lifetime `run_pipeline` always had, entered only
    when a pass actually runs: a compose from cached layers never loads
    gemma. Its RAM reservation spans that lifetime. The CPU/GPU lease is
    taken only for the load, unload and individual local inference calls.
    """

    def __init__(self, harness, project_folder):
        self._args = (harness, project_folder)
        self._analyzer = None
        self._context = None
        self._memory_context = None
        self._load_lock = threading.RLock()

    def _ensure_loaded(self):
        with self._load_lock:
            if self._analyzer is not None:
                return

            memory_context = _semantic_model_memory_reservation()
            memory_context.__enter__()
            context = managed_model("gemma-4", lambda: load(MODEL_ID))
            entered = False
            try:
                with _semantic_inference_admission():
                    model, proc = context.__enter__()
                    entered = True
                harness, project_folder = self._args
                analyzer = VisionAnalyzer(
                    model, proc, harness=harness,
                    project_folder=project_folder)
            except BaseException:
                if entered:
                    with _semantic_inference_admission():
                        context.__exit__(None, None, None)
                memory_context.__exit__(None, None, None)
                raise

            self._memory_context = memory_context
            self._context = context
            self._analyzer = analyzer

    def __getattr__(self, name):
        self._ensure_loaded()
        return getattr(self._analyzer, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        with self._load_lock:
            if self._context is not None:
                with _semantic_inference_admission():
                    result = self._context.__exit__(*exc)
                # managed_model has now unloaded its weights. Only then may
                # the persistent RAM reservation be released.
                self._memory_context.__exit__(*exc)
                self._context = None
                self._memory_context = None
                self._analyzer = None
                return result
        return False


def _run_semantic_work(window_tasks, coarse_tasks, on_complete, *,
                       executor_factory=None, wait_for=wait,
                       clock=time.perf_counter):
    """Run ready semantic work on one local and one cloud lane.

    Window and coarse tasks are available at startup. A completion may
    enqueue detail tasks; the coordinator alone receives results and
    releases those dependencies. Queue order is stable within each kind,
    and cloud details always precede unstarted coarse batches.
    """
    if executor_factory is None:
        executor_factory = lambda lane: ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"semantic-{lane}")

    queues = {"window": [], "detail": [], "coarse": []}
    serial = 0

    def enqueue(task):
        nonlocal serial
        serial += 1
        heapq.heappush(
            queues[task["kind"]],
            (task["order"], serial, task))

    for task in window_tasks:
        enqueue(task)
    for task in coarse_tasks:
        enqueue(task)

    local = executor_factory("local")
    cloud = executor_factory("cloud")
    executors = {"local": local, "cloud": cloud}
    running = {}

    def next_task(lane):
        kinds = ("detail", "coarse") if lane == "cloud" else (
            "window", "coarse")
        for kind in kinds:
            skipped = []
            while queues[kind]:
                entry = heapq.heappop(queues[kind])
                task = entry[2]
                if lane == "cloud" and task.get("local_only"):
                    skipped.append(entry)
                    continue
                for skipped_entry in skipped:
                    heapq.heappush(queues[kind], skipped_entry)
                return task
            for skipped_entry in skipped:
                heapq.heappush(queues[kind], skipped_entry)
        return None

    try:
        while any(queues.values()) or running:
            active_lanes = {entry["lane"] for entry in running.values()}
            for lane in ("cloud", "local"):
                if lane in active_lanes:
                    continue
                task = next_task(lane)
                if task is None:
                    continue
                started = clock()
                future = executors[lane].submit(task["run"], lane)
                running[future] = {
                    "task": task, "lane": lane, "started": started}
                active_lanes.add(lane)

            if not running:
                raise RuntimeError(
                    "semantic scheduler has pending work but no ready task")

            completed, _pending = wait_for(
                tuple(running), return_when=FIRST_COMPLETED)
            for future in sorted(
                    completed,
                    key=lambda item: running[item]["task"]["order"]):
                entry = running.pop(future)
                result = future.result()
                on_complete(
                    entry["task"], result, entry["lane"],
                    entry["started"], clock(), enqueue)
    except BaseException:
        for future in running:
            future.cancel()
        for executor in executors.values():
            executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        for executor in executors.values():
            executor.shutdown(wait=True)


def _make_host_semantic_tasks(contexts, analyzer):
    """Build stable window/coarse tasks for the step-wide host scheduler."""
    window_tasks = []
    coarse_tasks = []
    for context in contexts:
        clip_order = context["clip_order"]
        meta = context["meta"]
        clip_id = meta["clip_id"]
        duration = meta["duration_s"]
        window_cache = context["windows_by_index"]
        for window_index, clip_info in enumerate(context["video_clips"]):
            if window_cache[window_index] is not None:
                continue

            def analyze_window(_lane, *, ctx=context, index=window_index,
                               info=clip_info):
                results = analyze_windows(
                    analyzer, [info], ctx["meta"]["duration_s"],
                    ctx["temporal_index"], ctx["transcript"],
                    ctx["meta"].get("fps") or 30.0,
                    request_prefix=ctx["meta"]["clip_id"])
                if len(results) != 1:
                    raise RuntimeError(
                        "analyze_windows did not return one result for a "
                        "valid window clip")
                return results[0]

            window_tasks.append({
                "key": (clip_order, "window", window_index),
                "kind": "window", "order": (clip_order, window_index),
                "run": analyze_window,
            })

        if context["objects_cached"] is not None:
            continue
        frames = context["frames"]
        n_batches = max(1, int(math.ceil(len(frames) / COARSE_BATCH_SIZE)))
        context["coarse_batches"] = n_batches
        context["coarse_answers"] = {}
        for batch_index in range(n_batches):
            start = batch_index * COARSE_BATCH_SIZE
            end = min(start + COARSE_BATCH_SIZE, len(frames))
            batch_frames = frames[start:end]

            def analyze_coarse(lane, *, ctx=context, index=batch_index,
                               count=n_batches, batch=batch_frames):
                return analyze_objects_coarse_batch(
                    analyzer, batch, ctx["meta"]["duration_s"], index,
                    count, request_prefix=ctx["meta"]["clip_id"],
                    still_executor=lane)

            coarse_tasks.append({
                "key": (clip_order, "coarse", batch_index),
                "kind": "coarse", "order": (clip_order, batch_index),
                "local_only": not batch_frames,
                "run": analyze_coarse,
            })
    return window_tasks, coarse_tasks


def _run_host_semantic_schedule(contexts, analyzer, *,
                                executor_factory=None, wait_for=wait,
                                clock=time.perf_counter):
    """Measure uncached layers across clips, committing caches in order."""
    window_tasks, coarse_tasks = _make_host_semantic_tasks(contexts, analyzer)

    def complete(task, result, lane, started, finished, enqueue):
        clip_order, kind, item_index = task["key"]
        context = contexts[clip_order]
        if kind == "window":
            context["windows_by_index"][item_index] = result
            context["window_task_spans"][item_index] = (started, finished)
            return
        if kind == "coarse":
            context["coarse_answers"][item_index] = result
            if len(context["coarse_answers"]) != context["coarse_batches"]:
                return

            all_objects = []
            coarse_time = 0.0
            for batch_index in range(context["coarse_batches"]):
                batch_objects, elapsed = context["coarse_answers"][batch_index]
                all_objects.extend(batch_objects)
                coarse_time += elapsed
            coarse_objects = merge_objects(
                all_objects, max_duration=context["meta"]["duration_s"])
            context["coarse_objects"] = coarse_objects
            context["objects_time"] = coarse_time
            context["objects_calls"] = context["coarse_batches"]

            ranges = find_detail_ranges(
                coarse_objects, context["meta"]["duration_s"])
            context["detail_ranges"] = ranges
            if not ranges:
                context["objects_result"] = coarse_objects
                return
            context["objects_calls"] += max(1, len(ranges))

            range_frames = extract_detail_frames(
                context["clip_path"], context["cache_dir"], ranges)
            detail_tasks = []
            detail_item = 0
            for range_index, (time_range, frames) in enumerate(
                    range_frames.items()):
                if not frames:
                    continue
                range_start, range_end = time_range
                for batch_start in range(0, len(frames), DETAIL_BATCH_SIZE):
                    batch = frames[batch_start:batch_start + DETAIL_BATCH_SIZE]

                    def analyze_detail(lane, *, ctx=context,
                                       start=range_start, end=range_end,
                                       index=batch_start, details=batch):
                        return analyze_objects_detail_batch(
                            analyzer, details, ctx["coarse_objects"],
                            ctx["meta"]["duration_s"], start, end, index,
                            request_prefix=ctx["meta"]["clip_id"],
                            still_executor=lane)

                    detail_tasks.append({
                        "key": (clip_order, "detail", detail_item),
                        "kind": "detail",
                        "order": (clip_order, range_index, batch_start),
                        "run": analyze_detail,
                    })
                    detail_item += 1
            context["detail_tasks"] = detail_tasks
            if not detail_tasks:
                context["objects_result"] = coarse_objects
                return
            context["detail_answers"] = {}
            context["detail_batches"] = len(detail_tasks)
            for detail_task in detail_tasks:
                enqueue(detail_task)
            return

        context["detail_answers"][item_index] = result
        if len(context["detail_answers"]) != context["detail_batches"]:
            return
        detailed_objects = []
        detail_time = 0.0
        for detail_index in range(context["detail_batches"]):
            objects, _raw, elapsed = context["detail_answers"][detail_index]
            detailed_objects.extend(objects)
            detail_time += elapsed
        context["objects_time"] += detail_time
        context["objects_result"] = merge_objects(
            list(context["coarse_objects"]) + detailed_objects,
            max_duration=context["meta"]["duration_s"])

    # All contexts share input clip order, which is the index used by the
    # stable task keys and the result/cache commit pass below.
    _run_semantic_work(
        window_tasks, coarse_tasks, complete,
        executor_factory=executor_factory, wait_for=wait_for, clock=clock)

    for context in contexts:
        layer_cache = context["layer_cache"]
        windows = context["windows_by_index"]
        if any(window is None for window in windows):
            raise RuntimeError(
                f"scheduler did not produce every window for "
                f"{context['meta']['clip_id']}")
        window_spans = list(context["window_task_spans"].values())
        inference_wall_s = (
            max(finished for _started, finished in window_spans)
            - min(started for started, _finished in window_spans)
            if window_spans else 0.0)
        cached_windows = context["windows_cached"]
        context["windows_result"] = {
            "windows": windows,
            "inference_wall_s": (
                cached_windows["inference_wall_s"]
                if cached_windows is not None
                else round(inference_wall_s, 2)),
            "video_clips_extracted": (
                cached_windows["video_clips_extracted"]
                if cached_windows is not None
                else len(context["video_clips"])),
        }
        if context["windows_cached"] is None and layer_cache:
            for index, clip_info in enumerate(context["video_clips"]):
                params = context["window_params"][index]
                if context["window_cache_hits"][index]:
                    continue
                layer_cache.put_window(
                    clip_info["start"], clip_info["end"], params,
                    windows[index])
            layer_cache.put("windows", context["windows_result"])

        objects = context["objects_cached"]
        if objects is None:
            objects = {
                "objects": context["objects_result"],
                "frames_extracted": len(context["frames"]),
                "analysis_time_s": context["objects_time"],
                "model_calls": context["objects_calls"],
            }
            if layer_cache:
                layer_cache.put("objects", objects)
        context["objects_result_layer"] = objects


def _prepare_host_semantic_contexts(clips, cache_dir, output_dir,
                                   layer_methods, force, still_viewer):
    """Prepare inputs for the host scheduler without invoking a model."""
    entries = []
    contexts = []
    skipped = 0
    for clip_order, raw_clip_path in enumerate(clips):
        clip_path = Path(raw_clip_path)
        if not clip_path.exists():
            print(f"\n  ⚠ Skipping {clip_path} (not found)")
            entries.append(None)
            continue

        out_path = output_dir / f"clip_profile_{clip_path.stem}_v3.json"
        if out_path.exists() and not force:
            print(f"\n  ⏭ Skipping {clip_path.name} "
                  "(profile exists, use --force to re-analyze)")
            try:
                with open(out_path, encoding="utf-8") as handle:
                    entries.append(json.load(handle))
            except Exception:
                entries.append(None)
            skipped += 1
            continue

        print(f"\n{'━'*60}")
        print(f"  Clip {clip_order+1}/{len(clips)}: {clip_path.name}")
        print(f"{'━'*60}")
        meta = probe_clip(clip_path)
        if not meta:
            print("  ⚠ No video stream found, skipping")
            entries.append(None)
            continue
        duration = meta["duration_s"]
        print(f"  Duration: {duration:.1f}s | "
              f"{meta['resolution'][0]}x{meta['resolution'][1]} "
              f"@ {meta['fps']}fps")

        temporal_index = load_temporal_index(clip_path)
        if temporal_index:
            n_speech = len(temporal_index.get("speech_regions", []))
            n_boundaries = len(get_scene_boundaries(temporal_index))
            print(f"  Temporal index: loaded ({n_speech} speech regions, "
                  f"{n_boundaries} scene boundaries)")
        else:
            print("  Temporal index: not found")
        transcript = load_transcript_text(clip_path, output_dir)
        if transcript:
            print(f"  Transcript: \"{transcript[:80]}...\""
                  if len(transcript) > 80 else f"  Transcript: \"{transcript}\"")
        else:
            print("  Transcript: (none)")

        layer_cache = measurement_layers.LayerCache.for_source(
            clip_path, layer_methods, {
                "windows": {
                    "duration_s": duration, "fps": meta.get("fps"),
                    "temporal_index": measurement_layers.canonical_digest(
                        temporal_index),
                    "transcript": transcript or "",
                },
                "objects": {"duration_s": duration,
                            "still_viewer": still_viewer},
                "picture": {"duration_s": duration},
            }, force=force)
        cached = {
            layer: layer_cache.get(layer)
            for layer in MEASUREMENT_LAYERS
        } if layer_cache else {}
        cached_layers = {layer for layer, value in cached.items()
                         if value is not None}
        if cached_layers:
            print(f"  Measurement layers cached: "
                  f"{', '.join(sorted(cached_layers))}")

        frames = None
        if cached.get("objects") is None:
            frames = extract_frames(clip_path, duration, cache_dir)
            print(f"  Frames extracted: {len(frames)} "
                  f"(every {COARSE_FRAME_INTERVAL_S}s)")
        reset_ffprobe_spawn_count()
        extraction_started = time.perf_counter()
        video_clips = []
        if cached.get("windows") is None:
            video_clips = extract_video_clips(
                clip_path, duration, cache_dir, prefetch=False)
        extraction_wall = round(time.perf_counter() - extraction_started, 2)
        extraction_spawns = ffprobe_spawn_count()
        audio_count = sum(1 for item in video_clips
                          if item.get("has_audio"))
        if video_clips:
            print(f"  Video window cuts ready: {len(video_clips)} × "
                  f"{ACTION_WINDOW_S}s")

        window_params = []
        window_cache_hits = []
        windows_by_index = []
        if cached.get("windows") is not None:
            windows_by_index = list(cached["windows"]["windows"])
            window_params = [None] * len(windows_by_index)
            window_cache_hits = [True] * len(windows_by_index)
        else:
            for clip_info in video_clips:
                params = _window_cache_params(
                    clip_info, duration, temporal_index, transcript,
                    meta.get("fps") or 30.0)
                cached_window = (
                    layer_cache.get_window(
                        clip_info["start"], clip_info["end"], params)
                    if layer_cache else None)
                windows_by_index.append(cached_window)
                window_params.append(params)
                window_cache_hits.append(cached_window is not None)

        perf_ledger.record(
            "demux", extraction_wall, backend="ffmpeg",
            subprocesses=extraction_spawns,
            decoded_source_s=round(float(duration or 0.0), 3))
        context = {
            "clip_order": len(contexts), "meta": meta,
            "clip_order_in_input": clip_order,
            "clip_path": clip_path, "cache_dir": cache_dir,
            "transcript": transcript or "",
            "temporal_index": temporal_index, "frames": frames or [],
            "video_clips": video_clips, "layer_cache": layer_cache,
            "windows_cached": cached.get("windows"),
            "objects_cached": cached.get("objects"),
            "window_params": window_params,
            "window_cache_hits": window_cache_hits,
            "windows_by_index": windows_by_index,
            "window_task_spans": {},
            "objects_time": 0.0, "objects_calls": 0,
            "window_extraction_wall_s": extraction_wall,
            "window_extraction_ffprobe_spawns": extraction_spawns,
            "window_audio_count": audio_count,
            "out_path": out_path,
        }
        contexts.append(context)
        entries.append(context)

    return entries, contexts, skipped


def _run_host_semantic_pipeline(analyzer, clips, cache_dir, output_dir,
                                layer_methods, force, still_viewer):
    """Analyze every host-driven clip through the global two-lane schedule."""
    entries, contexts, skipped = _prepare_host_semantic_contexts(
        clips, cache_dir, output_dir, layer_methods, force, still_viewer)
    if contexts:
        has_model_work = any(
            (context["windows_cached"] is None
             and any(not hit for hit in context["window_cache_hits"]))
            or context["objects_cached"] is None
            for context in contexts)
        if has_model_work:
            # Materialize the lazy Gemma wrapper on the coordinator before
            # either executor can enter VisionAnalyzer.
            getattr(analyzer, "harness")
            resolved_analyzer = getattr(analyzer, "_analyzer", analyzer)
        else:
            resolved_analyzer = getattr(analyzer, "_analyzer", analyzer)
        try:
            _run_host_semantic_schedule(contexts, resolved_analyzer)
        finally:
            for context in contexts:
                close_windows = getattr(context["video_clips"], "close", None)
                if close_windows is not None:
                    close_windows()

        for context in contexts:
            cached_layers = {
                "windows": context["windows_result"],
                "objects": context["objects_result_layer"],
            }
            profile = analyze_clip(
                resolved_analyzer, context["meta"], None, [],
                context["transcript"], context["temporal_index"],
                context["cache_dir"], layer_cache=context["layer_cache"],
                precomputed_layers=cached_layers)
            profile.setdefault("analysis_metadata", {}).update({
                "window_extraction_wall_s": (
                    context["window_extraction_wall_s"]),
                "window_extraction_ffprobe_spawns": (
                    context["window_extraction_ffprobe_spawns"]),
            })
            entries[context["clip_order_in_input"]] = profile
            _write_profile_atomically(context["out_path"], profile)
            print(f"  Saved: {context['out_path']}")

    return [entry for entry in entries if entry is not None], skipped


def _write_vision_index(all_profiles, skipped, total_start, output_dir):
    """Write the combined profile index and print its run summary."""
    total_time = time.time() - total_start
    index = {
        "pipeline": "vision_analysis_v3",
        "model": MODEL_ID,
        "total_clips": len(all_profiles),
        "total_analyzed": len(all_profiles) - skipped,
        "total_skipped": skipped,
        "total_time_s": round(total_time, 2),
        "avg_time_per_clip_s": round(
            total_time / max(len(all_profiles) - skipped, 1), 2
        ),
        "clips": all_profiles,
    }
    index_path = output_dir / "vision_index_v3.json"
    with open(index_path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2)

    print(f"\n{'═'*60}")
    print("  Pipeline Complete")
    print(f"{'═'*60}")
    print(f"  Clips analyzed: {len(all_profiles) - skipped} "
          f"(skipped {skipped})")
    print(f"  Total time: {total_time:.1f}s ({total_time/60:.1f} min)")
    if all_profiles:
        total_calls = sum(
            profile.get("analysis_metadata", {}).get("total_model_calls", 0)
            for profile in all_profiles)
        print(f"  Total model calls: {total_calls}")
    print(f"  Index saved: {index_path}")
    print()
    return index


def run_pipeline(clips, cache_dir=CACHE_DIR, output_dir=OUTPUT_DIR, force=False,
                 harness=None, project_folder=None):
    """Run the v3 vision pipeline on a list of clip paths.

    Args:
        clips: List of Path objects to video files.
        cache_dir: Directory for frame/clip cache.
        output_dir: Directory for output profiles.
        force: If True, re-analyze clips even if profile exists.
        harness: Driving harness for the STILL passes (object
            coarse/detail) - a host with vision answers first, else
            gemma. None reads `PIPELINE_HOST_HARNESS`, so standalone
            runs keep the gemma behaviour. VIDEO passes never read
            this; they stay on gemma.
        project_folder: Project the still-vision handshake files under
            when a host answers. Required when `harness` declares
            vision; unused otherwise.
    """
    # Absolute before anything joins them: the still-vision handshake
    # carries frame paths to a host that opens them with its own file
    # tools, and it refuses a relative one (measured 2026-09-24: a
    # host-driven run failed every clip on `.vision_cache/...`
    # relative paths). Same directory as before - `resolve` only
    # spells it absolutely - so no cache is orphaned by this.
    cache_dir = Path(cache_dir).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    all_profiles = []
    total_start = time.time()
    skipped = 0

    # One method identity per layer for the whole run; each clip adds
    # its own source digest and parameters.
    layer_methods = {
        layer: measurement_layers.method_digest(__file__, entry_points)
        for layer, entry_points in MEASUREMENT_LAYERS.items()
    }
    from library.tools.still_vision import HOST_SEES_IMAGES, resolve_harness
    still_viewer = resolve_harness(harness)

    if HOST_SEES_IMAGES.get(still_viewer) is True:
        with _LazyAnalyzer(harness, project_folder) as analyzer:
            all_profiles, skipped = _run_host_semantic_pipeline(
                analyzer, clips, cache_dir, output_dir, layer_methods,
                force, still_viewer)
        return _write_vision_index(
            all_profiles, skipped, total_start, output_dir)

    # The model loads on the first pass that needs it: a clip whose
    # windows are all cached never pays for it.
    with _LazyAnalyzer(harness, project_folder) as analyzer:
        for i, clip_path in enumerate(clips, 1):
            clip_path = Path(clip_path)
            if not clip_path.exists():
                print(f"\n  ⚠ Skipping {clip_path} (not found)")
                continue
    
            # Check for existing profile
            out_path = output_dir / f"clip_profile_{clip_path.stem}_v3.json"
            if out_path.exists() and not force:
                print(f"\n  ⏭ Skipping {clip_path.name} (profile exists, use --force to re-analyze)")
                try:
                    with open(out_path) as f:
                        all_profiles.append(json.load(f))
                except Exception:
                    pass
                skipped += 1
                continue
    
            print(f"\n{'━'*60}")
            print(f"  Clip {i}/{len(clips)}: {clip_path.name}")
            print(f"{'━'*60}")
    
            # Step 0: Probe metadata
            meta = probe_clip(clip_path)
            if not meta:
                print(f"  ⚠ No video stream found, skipping")
                continue
    
            duration = meta["duration_s"]
            print(f"  Duration: {duration:.1f}s | "
                  f"{meta['resolution'][0]}x{meta['resolution'][1]} @ {meta['fps']}fps")
    
            # Load temporal index
            temporal_idx = load_temporal_index(clip_path)
            if temporal_idx:
                n_speech = len(temporal_idx.get("speech_regions", []))
                n_boundaries = len(get_scene_boundaries(temporal_idx))
                print(f"  Temporal index: loaded ({n_speech} speech regions, "
                      f"{n_boundaries} scene boundaries)")
            else:
                print(f"  Temporal index: not found")
    
            # Load transcript
            transcript = load_transcript_text(clip_path, output_dir)
            if transcript:
                print(f"  Transcript: \"{transcript[:80]}...\"" if len(transcript) > 80
                      else f"  Transcript: \"{transcript}\"")
            else:
                print(f"  Transcript: (none)")

            layer_cache = measurement_layers.LayerCache.for_source(
                clip_path, layer_methods, {
                    "windows": {
                        "duration_s": duration, "fps": meta.get("fps"),
                        "temporal_index": measurement_layers.canonical_digest(
                            temporal_idx),
                        "transcript": transcript or "",
                    },
                    "objects": {"duration_s": duration,
                                "still_viewer": still_viewer},
                    "picture": {"duration_s": duration},
                }, force=force)
            cached_layers = {
                layer for layer in MEASUREMENT_LAYERS
                if layer_cache and layer_cache.get(layer) is not None}
            if cached_layers:
                print(f"  Measurement layers cached: "
                      f"{', '.join(sorted(cached_layers))}")

            # Extract frames (for objects — 1 per 5s)
            frames = None
            if "objects" not in cached_layers:
                frames = extract_frames(clip_path, duration, cache_dir)
                print(f"  Frames extracted: {len(frames)} (every {COARSE_FRAME_INTERVAL_S}s)")

            # Start the bounded window producer before inference. It
            # keeps one cut ahead while `analyze_windows` consumes clips
            # in order; extraction timing and probe count are read back
            # once the producer has joined.
            reset_ffprobe_spawn_count()
            video_clips = []
            if "windows" not in cached_layers:
                video_clips = extract_video_clips(
                    clip_path, duration, cache_dir, prefetch=True)
                print(f"  Video window cuts queued: {len(video_clips)} × "
                      f"{ACTION_WINDOW_S}s; extraction overlaps inference")

            # Run analysis
            try:
                profile = analyze_clip(
                    analyzer, meta, frames, video_clips, transcript,
                    temporal_idx, cache_dir, layer_cache=layer_cache,
                )
            finally:
                if isinstance(video_clips, _WindowClipPrefetch):
                    video_clips.close()

            if isinstance(video_clips, _WindowClipPrefetch):
                window_extraction_wall_s = round(
                    video_clips.extraction_wall_s, 2)
                window_extraction_ffprobe_spawns = (
                    video_clips.ffprobe_spawns)
                n_aud = video_clips.has_audio_count
            else:
                window_extraction_wall_s = 0.0
                window_extraction_ffprobe_spawns = ffprobe_spawn_count()
                n_aud = sum(1 for c in video_clips if c.get("has_audio"))
            print(f"  Video clips extracted: {len(video_clips)} × "
                  f"{ACTION_WINDOW_S}s ({n_aud} with audio) "
                  f"[{window_extraction_wall_s:.1f}s extraction, "
                  f"{window_extraction_ffprobe_spawns} ffprobe spawns]")
            # Extraction ran in a producer thread BESIDE inference, so
            # its wall overlaps gemma_inference's; the profile says so.
            perf_ledger.record(
                "demux", window_extraction_wall_s, backend="ffmpeg",
                subprocesses=window_extraction_ffprobe_spawns,
                decoded_source_s=round(float(duration or 0.0), 3))
            profile.setdefault("analysis_metadata", {}).update({
                "window_extraction_wall_s": window_extraction_wall_s,
                "window_extraction_ffprobe_spawns": (
                    window_extraction_ffprobe_spawns),
            })
            _inference_wall = profile["analysis_metadata"].get(
                "window_inference_wall_s")
            try:
                _inference_wall = float(_inference_wall)
            except (TypeError, ValueError):
                _inference_wall = 0.0
            print(f"  Timing: extraction {window_extraction_wall_s:.1f}s "
                  f"vs inference {_inference_wall:.1f}s "
                  f"({len(video_clips)} windows, "
                  f"{window_extraction_ffprobe_spawns} ffprobe spawns)")
            all_profiles.append(profile)

            # Save individual profile
            _write_profile_atomically(out_path, profile)
            print(f"  Saved: {out_path}")

    return _write_vision_index(all_profiles, skipped, total_start, output_dir)


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Vision Pipeline v3 — Dimension-indexed video analysis"
    )
    parser.add_argument("--clip", type=str, nargs="+",
                        help="Analyze specific clip(s)")
    parser.add_argument("--raw-dir", type=str, default="raw",
                        help="Directory containing raw clips")
    parser.add_argument("--cache-dir", type=str, default=str(CACHE_DIR),
                        help="Frame/clip cache directory")
    parser.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR),
                        help="Output directory for profiles")
    parser.add_argument("--force", action="store_true",
                        help="Re-analyze clips even if profile exists")
    parser.add_argument("--extensions", type=str, default="MOV,mov,mp4,MP4",
                        help="Comma-separated video extensions")
    parser.add_argument("--harness", type=str, default=None,
                        help="Driving harness for the STILL passes "
                             "(agent, mock); unset reads "
                             "PIPELINE_HOST_HARNESS, and with no host "
                             "the still passes stay on gemma")
    parser.add_argument("--project-folder", type=str, default=None,
                        help="Project the still-vision handshake files "
                             "under when a host answers")
    args = parser.parse_args()

    if args.clip:
        clips = [Path(c) for c in args.clip]
    else:
        raw_dir = Path(args.raw_dir)
        exts = args.extensions.split(",")
        clips = sorted(
            f for ext in exts for f in raw_dir.glob(f"*.{ext}")
        )
        if not clips:
            print(f"No video files found in {raw_dir}")
            sys.exit(1)
        print(f"Found {len(clips)} clips in {raw_dir}")

    run_pipeline(
        clips,
        cache_dir=Path(args.cache_dir),
        output_dir=Path(args.output_dir),
        force=args.force,
        harness=args.harness,
        project_folder=args.project_folder,
    )


if __name__ == "__main__":
    main()
