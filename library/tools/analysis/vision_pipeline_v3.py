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
import json
import math
import subprocess
import sys
import time
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
# <repo>, above `library/`; tests/test_picture_quality.py asserts the
# index, because an off-by-one here still imports cleanly under pytest
# (conftest already has the root on sys.path) and fails only in a run.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from library.tools.analysis import picture_quality
from library.tools.camera_stability import read_camera_stability
from library.tools.segment_coverage import (
    coverage_summary,
    normalize_segments,
)


# ═══════════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════════

MODEL_ID = "mlx-community/gemma-4-12b-it-4bit"
CACHE_DIR = Path(".vision_cache")
OUTPUT_DIR = Path("pipeline_output")

# Action windows
ACTION_WINDOW_S = 10

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

    def analyze(self, prompt, images=None, video=None, max_tokens=512,
                  audio=None):
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
        if images:
            return self._analyze_stills(prompt, images, max_tokens)
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
        elapsed = time.time() - t0
        text = r.text if hasattr(r, "text") else str(r)
        return text, elapsed

    def _analyze_stills(self, prompt, images, max_tokens):
        """One still-frame pass: the driver first, gemma fallback.

        Routes through `library/tools/still_vision.py` and returns
        (text, elapsed) in exactly the gemma shape, so
        `analyze_with_retry`'s parse-and-retry reads a host answer
        byte-for-byte the way it read gemma's. Which stills are
        extracted, and how, is untouched - only who looks at them.
        """
        from library.tools.still_vision import inspect_stills

        t0 = time.time()
        text = inspect_stills(
            prompt, list(images), harness=self.harness,
            project_folder=self.project_folder, step_id=self.step_id,
            label="objects", max_tokens=max_tokens)
        return text, time.time() - t0

    def analyze_with_retry(self, prompt, parse_fn, images=None, video=None,
                           max_tokens=512, label="pass", audio=None):
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
        text, elapsed = self.analyze(prompt, images=images, video=video,
                                     max_tokens=max_tokens, audio=audio)
        result = parse_fn(text.strip())

        # Retry once if parse produced empty result
        if not result:
            retry_suffix = "\n\nIMPORTANT: Respond with ONLY valid JSON. No markdown, no explanation, no text before or after the JSON."
            text2, elapsed2 = self.analyze(prompt + retry_suffix, images=images,
                                            video=video, max_tokens=max_tokens,
                                            audio=audio)
            elapsed += elapsed2
            result2 = parse_fn(text2.strip())
            if result2:
                return result2, text2.strip(), elapsed
            else:
                print(f"    ⚠ {label}: JSON parse failed after retry")
                return result, text.strip(), elapsed

        return result, text.strip(), elapsed


# ═══════════════════════════════════════════════════════════════════════
#  Clip Metadata
# ═══════════════════════════════════════════════════════════════════════

def probe_clip(clip_path):
    """Extract metadata from a video clip using ffprobe.

    Returns None if the file is corrupt, not a video, or ffprobe fails.
    """
    try:
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
    open. See tests/test_vision_pipeline.py (finding 2).
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
    floored to a multiple of 2. `tests/test_native_video_sampling.py`
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
    window, to validate the two staleness rules (audio-policy mismatch,
    pre-720p-cap height) from a single spawn instead of one probe per
    question. Fresh cuts never pay it: their audio follows the cut
    recipe and their readability is an open-check, not a probe.
    """
    try:
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


def extract_video_clips(clip_path, duration, cache_dir, window_s=ACTION_WINDOW_S,
                        subdir="clips", prefix="clip", with_audio=True):
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

    Returns list of {index, start, end, path, has_audio}.
    """
    clip_dir = cache_dir / clip_path.stem / subdir
    clip_dir.mkdir(parents=True, exist_ok=True)

    # One audio probe for the whole clip: every window's `has_audio`
    # derives from it, so a 408-window clip pays 1 probe, not ~1000.
    source_has_audio = _source_has_audio(clip_path) if with_audio else False

    n_windows = max(1, int(math.ceil(duration / window_s)))
    clips = []

    for i in range(n_windows):
        start = i * window_s
        end = min((i + 1) * window_s, duration)
        if end - start < MIN_WINDOW_S:
            print(f"    ⚠ Window [{start:.0f}-{end:.0f}s]: "
                  f"only {end - start:.2f}s, too short to analyze, dropped",
                  file=sys.stderr)
            continue
        out_path = clip_dir / f"{prefix}_{i:03d}.mp4"

        want_audio = with_audio and source_has_audio
        # Fresh cuts carry exactly what the recipe below encodes, so
        # `has_audio` derives from `want_audio` with no per-window
        # probe; a cached file keeps its probed value (equal to
        # `want_audio`, else it is re-cut just below).
        has_audio = want_audio
        needs_cut = not out_path.exists()
        if out_path.exists():
            # One spawn validates both staleness rules (audio policy,
            # 720p cap) and the video stream at once.
            cached_has_video, cached_has_audio, cached_height = (
                _probe_window_streams(out_path))
            if cached_has_audio != want_audio:
                # Stale cache from the other audio policy - re-cut below.
                try:
                    out_path.unlink()
                except OSError:
                    pass
                needs_cut = True
            elif cached_height > WINDOW_CLIP_HEIGHT:
                # Stale cache from before the 720p cap - re-cut below.
                try:
                    out_path.unlink()
                except OSError:
                    pass
                needs_cut = True
            elif not cached_has_video:
                print(f"    ⚠ Window [{start:.0f}-{end:.0f}s]: "
                      f"cut carries no video stream, dropped",
                      file=sys.stderr)
                continue
            else:
                has_audio = cached_has_audio

        if needs_cut:
            cmd = ["ffmpeg", "-y", "-i", str(clip_path),
                   "-ss", str(start), "-t", str(end - start),
                   "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                   "-vf", f"scale=-2:min({WINDOW_CLIP_HEIGHT}\\,ih)"]
            if want_audio:
                cmd += ["-c:a", "aac"]
            else:
                # No audio in the window clip. The model therefore
                # cannot hear anything in it.
                cmd += ["-an"]
            cmd += ["-loglevel", "error", str(out_path)]
            cut = subprocess.run(cmd, capture_output=True, check=False)
            if cut.returncode != 0 and out_path.exists():
                # The encode failed: do not trust the recipe, ground-
                # truth the survivor before handing it to a pass.
                cut_has_video, cut_has_audio, _ = (
                    _probe_window_streams(out_path))
                if not cut_has_video:
                    print(f"    ⚠ Window [{start:.0f}-{end:.0f}s]: "
                          f"cut carries no video stream, dropped",
                          file=sys.stderr)
                    continue
                has_audio = cut_has_audio

        if out_path.exists():
            # One open-check per cut, no probe: a husk no decoder can
            # open is dropped, never handed to a pass.
            if not _clip_opens(out_path):
                print(f"    ⚠ Window [{start:.0f}-{end:.0f}s]: "
                      f"cut carries no video stream, dropped",
                      file=sys.stderr)
                continue
            clips.append({
                "index": i,
                "start": round(start, 2),
                "end": round(end, 2),
                "path": str(out_path),
                "has_audio": has_audio,
            })

    return clips


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
                    audio, label):
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
        max_tokens=MAX_TOKENS["window_all_compact"], label=label)
    if result:
        path = "compact_repaired" if state.get("repaired") else "compact"
        return _expand_or_canonical(result), raw, elapsed, path
    fb_result, _fb_raw, fb_elapsed = analyzer.analyze_with_retry(
        fallback_prompt, parse_json_object, video=video, audio=audio,
        max_tokens=MAX_TOKENS["window_all"],
        label=label + " fallback")
    elapsed += fb_elapsed
    if fb_result:
        fb_result = _expand_or_canonical(fb_result)
    return fb_result, raw, elapsed, "full_fallback"


def analyze_windows(analyzer, video_clips, duration, temporal_index, transcript,
                    fps=None):
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
            f"Window [{w_start:.0f}-{w_end:.0f}s]")

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


def analyze_objects_coarse(analyzer, frames, duration):
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
        batch_frames = frames[start_i:end_i]

        batch_paths = [f["path"] for f in batch_frames]
        batch_ts = [f["timestamp"] for f in batch_frames]
        ts_str = ", ".join(f"{ts:.1f}s" for ts in batch_ts)

        prompt = PROMPT_OBJECTS_COARSE.format(
            n_frames=len(batch_frames),
            duration=duration,
            frame_timestamps=ts_str,
        )

        result, _raw, elapsed = analyzer.analyze_with_retry(
            prompt, parse_json_array, images=batch_paths,
            max_tokens=MAX_TOKENS["objects_coarse"],
            label=f"Objects coarse batch {batch_idx+1}/{n_batches}"
        )
        total_elapsed += elapsed
        all_objects.extend(result)

    # Merge across batches
    merged = merge_objects(all_objects, max_duration=duration)
    return merged, total_elapsed


def analyze_objects_detail(analyzer, clip_path, cache_dir, coarse_objects, duration):
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

        # Summarize what coarse sweep found in this range
        relevant_entities = []
        for obj in coarse_objects:
            for app in obj.get("appearances", []):
                if isinstance(app, (list, tuple)) and len(app) == 2:
                    if app[1] > r_start and app[0] < r_end:
                        relevant_entities.append(obj.get("label", "unknown"))
                        break
        entities_str = ", ".join(relevant_entities) if relevant_entities else "(none previously detected)"

        # Batch frames for this range
        frame_paths = [f["path"] for f in frames]
        frame_ts = [f["timestamp"] for f in frames]

        for batch_start in range(0, len(frame_paths), DETAIL_BATCH_SIZE):
            batch_end = min(batch_start + DETAIL_BATCH_SIZE, len(frame_paths))
            b_paths = frame_paths[batch_start:batch_end]
            b_ts = frame_ts[batch_start:batch_end]
            ts_str = ", ".join(f"{ts:.1f}s" for ts in b_ts)

            prompt = PROMPT_OBJECTS_DETAIL.format(
                n_frames=len(b_paths),
                duration=duration,
                range_start=r_start,
                range_end=r_end,
                entities_summary=entities_str,
            )

            result, _raw, elapsed = analyzer.analyze_with_retry(
                prompt, parse_json_array, images=b_paths,
                max_tokens=MAX_TOKENS["objects_detail"],
                label=f"Objects detail [{r_start:.0f}-{r_end:.0f}s]"
            )
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
                 temporal_index, cache_dir):
    """Orchestrate all dimension passes for a single clip.

    Execution order:
      Group A (independent): Folded windows (actions + scene + camera +
        assessment sections, one call per 10 s window), Objects coarse
      Group B (dependent):   Objects detail (depends on coarse results)
      Group C (final):       Assessment merge (votes + deterministic tail)

    Every native-video (`video=`) pass runs on the 10 s windows, so each
    call sees 20 frames at 2 fps - no pass samples below that floor.
    """
    clip_id = clip_meta["clip_id"]
    duration = clip_meta["duration_s"]
    video_path = clip_meta["file_path"]
    clip_path = Path(clip_meta["file_path"])
    fps = clip_meta.get("fps") or 30.0

    n_window_calls = len(video_clips)
    n_obj_coarse_calls = max(1, int(math.ceil(len(frames) / COARSE_BATCH_SIZE)))
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

    # ── Group A: Independent passes ──────────────────────────────────

    # 1. Folded windows: actions + scene + camera + assessment sections
    print(f"  [Windows] {n_window_calls} windows × {ACTION_WINDOW_S}s (video+audio clips)...")
    windows = analyze_windows(analyzer, video_clips, duration, temporal_index,
                              transcript, fps=fps)
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
        {k: a[k] for k in ("window", "actions", "analysis_time_s",
                           "has_audio", "sampling", "parse_error",
                           "speech_quote_stripped") if k in a}
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

    # 2. Objects — coarse sweep
    print(f"  [Objects] Coarse sweep ({len(frames)} frames, {n_obj_coarse_calls} batch(es))...",
          end=" ", flush=True)
    coarse_objects, t = analyze_objects_coarse(analyzer, frames, duration)
    total_time += t
    total_calls += n_obj_coarse_calls
    print(f"({t:.1f}s) → {len(coarse_objects)} entities")

    # ── Group B: Dependent passes ────────────────────────────────────

    # 3. Objects — detail pass
    detail_ranges = find_detail_ranges(coarse_objects, duration)
    if detail_ranges:
        n_detail_ranges = len(detail_ranges)
        print(f"  [Objects] Detail pass ({n_detail_ranges} range(s))...", end=" ", flush=True)
        objects, t = analyze_objects_detail(
            analyzer, clip_path, cache_dir, coarse_objects, duration
        )
        total_time += t
        detail_calls = max(1, n_detail_ranges)  # Approximate
        total_calls += detail_calls
        print(f"({t:.1f}s) → {len(objects)} entities (refined)")
    else:
        objects = coarse_objects
        print(f"  [Objects] Detail pass skipped (no transient entities)")

    # ── Group C: Final synthesis ─────────────────────────────────────

    # 4. Assessment — hybrid (deterministic + folded votes)
    #
    # The picture is sampled ONCE here and handed to the merge below.
    # `None` back means it could not be sampled, and that is carried
    # through as an absent measurement rather than smoothed into "fine".
    print(f"  [Picture] Sharpness at "
          f"{picture_quality.SAMPLE_RATE_HZ:g}Hz...", end=" ", flush=True)
    t_pic = time.time()
    soft_ranges = picture_quality.measure_soft_picture(video_path, duration)
    t_pic = time.time() - t_pic
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
    t_assess = time.time()
    content_type, primary_subject_visible = _merge_assessment_votes(windows)
    assessment = _finish_assessment(
        deterministic, content_type, primary_subject_visible,
        temporal_index, duration, soft_ranges)
    t_assess = time.time() - t_assess
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
            "frames_extracted": len(frames),
            "video_clips_extracted": len(video_clips),
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
    cache_dir.mkdir(parents=True, exist_ok=True)

    all_profiles = []
    total_start = time.time()
    skipped = 0

    with managed_model("gemma-4", lambda: load(MODEL_ID)) as (model, proc):
        # Load model once
        analyzer = VisionAnalyzer(model, proc, harness=harness,
                                  project_folder=project_folder)

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

            # Extract frames (for objects — 1 per 5s)
            frames = extract_frames(clip_path, duration, cache_dir)
            print(f"  Frames extracted: {len(frames)} (every {COARSE_FRAME_INTERVAL_S}s)")

            # Extract video clips (every native-video pass runs on these
            # 10s segments at 2 fps, 720p, audio kept)
            video_clips = extract_video_clips(clip_path, duration, cache_dir)
            n_aud = sum(1 for c in video_clips if c.get("has_audio"))
            print(f"  Video clips extracted: {len(video_clips)} × {ACTION_WINDOW_S}s "
                  f"({n_aud} with audio)")

            # Run analysis
            profile = analyze_clip(
                analyzer, meta, frames, video_clips, transcript,
                temporal_idx, cache_dir,
            )
            all_profiles.append(profile)

            # Save individual profile
            with open(out_path, "w") as f:
                json.dump(profile, f, indent=2)
            print(f"  Saved: {out_path}")

    # Save combined index
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
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    print(f"\n{'═'*60}")
    print(f"  Pipeline Complete")
    print(f"{'═'*60}")
    print(f"  Clips analyzed: {len(all_profiles) - skipped} (skipped {skipped})")
    print(f"  Total time: {total_time:.1f}s ({total_time/60:.1f} min)")
    if all_profiles:
        total_calls = sum(
            p.get("analysis_metadata", {}).get("total_model_calls", 0)
            for p in all_profiles
        )
        print(f"  Total model calls: {total_calls}")
    print(f"  Index saved: {index_path}")
    print()

    return index


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
