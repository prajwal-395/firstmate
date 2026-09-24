#!/usr/bin/env python3
"""
vision_pipeline_v3.py — Video Analysis Pipeline v3

Dimension-indexed architecture: each annotation dimension uses its own
optimal temporal indexing strategy.

Dimensions:
  1. Scene/Environment — event-driven (ffmpeg boundaries + model augmentation)
  2. Camera — event-driven (one-shot, full clip)
  3. Actions/Behavior — fixed 10s windows (video clips + transcript)
  4. Objects/Entities + OCR — entity-indexed, two-tier (coarse → detail)
  5. Assessment — clip-level hybrid (deterministic + model)

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

# Native-video frame budget, measured on mlx-vlm 0.7.2 with
# `mlx-community/gemma-4-12b-it-4bit` (2026-09-24, 25.8 GB machine).
# `Gemma4UnifiedVideoProcessor.num_frames` is 32 and its
# `video_sampling_defaults()` caps decoding at that count, after
# `load_video` decodes at fps=2.0 capped to max_frames=32
# (`mlx_vlm/utils.py`: `DEFAULT_VIDEO_SAMPLING`,
# `resolve_video_sampling`, `load_video`). The processor then keeps at
# most 32 (`_sample_frames`). So EVERY `video=` call sees exactly 32
# frames no matter the clip length: 30 s -> ~1.07 fps effective, 120 s
# -> ~0.27 fps (probe: 32 decoded frames for both). Coverage therefore
# thins SILENTLY with clip length - wall time barely grows (30 s clip
# 34 s, 120 s clip 44 s) because the frame count never does.
NATIVE_VIDEO_FRAMES_PER_CALL = 32
NATIVE_VIDEO_DECODE_FPS = 2.0
# One native-video model call covers at most this many seconds, so the
# effective sampling never drops below 32/60 ~= 0.53 fps. A longer clip
# is split into windows of at most this length (the same machinery as
# the action windows) instead of being silently sparse-sampled. Per-call
# cost is flat in length (decode is capped, peak RSS 2-4 GB against
# 25.8 GB) - the ceiling below is a COVERAGE floor, not a memory one:
# a 60 s window costs ~30-45 s wall, measured on the 120 s excerpt.
NATIVE_WINDOW_S = 60

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
    "scene": 600,
    "camera": 400,
    "action_window": 500,
    "objects_coarse": 700,
    "objects_detail": 600,
    "assessment": 400,
}


# ═══════════════════════════════════════════════════════════════════════
#  Prompts
# ═══════════════════════════════════════════════════════════════════════

PROMPT_SCENE = """You are analyzing a {duration:.0f}-second video clip for scene and environment changes.

Pre-detected scene boundaries (from automated visual analysis): {boundaries}

Watch the video and:
1. Describe the physical environment for each segment between boundaries.
2. Identify any additional subtle environment changes the detector may have missed
   (e.g., walking from a parking lot into a building without a hard visual cut,
   significant lighting shifts within the same location).

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
[
  {{"start": 0.0, "end": <seconds>, "location": "<specific physical place>", "type": "<indoor|outdoor|vehicle|mixed>", "lighting": "<observable lighting conditions>", "notable_features": ["<visible sign text>", "<visible structures or landmarks>"]}}
]

Rules:
- Describe ONLY what is physically visible — no mood, atmosphere, or interpretation.
- If the setting never changes, return a single entry spanning the full clip.
- Use precise timestamps based on what you observe.
- notable_features should include any readable text on signs or buildings."""

PROMPT_CAMERA = """Watch this {duration:.0f}-second video clip and describe the camera behavior.

Create a new entry ONLY when the camera mode meaningfully changes (e.g., static to walking,
selfie to rear-facing, close-up to wide shot, stable to shaky).

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
[
  {{"start": <seconds>, "end": <seconds>, "mode": "<selfie|handheld|mounted|panning|tracking>", "framing": "<close-up|medium|wide>", "stability": "<description of how steady or shaky>", "movement": "<stationary|walking|panning_left|panning_right|tilting_up|tilting_down|zooming_in|zooming_out>"}}
]

Rules:
- If the camera stays in one mode the whole clip, return a single entry.
- Describe only observable camera characteristics.
- mode: selfie (front-facing, subject holding camera), handheld (rear-facing, hand-held),
  mounted (tripod/fixed), panning (rotating), tracking (following a subject)."""

PROMPT_ACTION_WINDOW = """This is a {window_dur:.0f}-second segment (seconds {window_start:.0f} to {window_end:.0f}) of a {duration:.0f}-second video clip.
{transcript_line}
Describe what is physically happening in this segment.

This video clip carries its AUDIO TRACK - measured on mlx-vlm 0.7.2,
gemma4-unified accepts audio alongside video (`generate` takes
`audio=` with the audio marker in the prompt, and the checkpoint
carries `embed_audio` weights), so listen to HOW speech is delivered
(pace, effort, pauses, visible effort). The only WORDS that exist are
the transcript text above (if any). NEVER quote speech: do not put
words in quotation marks and do not attribute utterances to the person
on screen. `speech_cue` describes delivery - pace, effort, pauses,
mouth movement, gestures while talking - never words.

For each distinct action or behavior change, report:
- What the person is physically doing
- Observable speech delivery cues (if speaking): mouth movement, apparent volume, gestures while talking
- Observable facial expression and body language: posture, hand position, head orientation, facial muscle state

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
{{
  "window": [{window_start}, {window_end}],
  "actions": [
    {{"start": <seconds>, "end": <seconds>, "action": "<physical description of what they are doing>", "speech_cue": "<observable speech delivery or null if not speaking>", "body_language": "<observable posture, gestures, facial expression>"}}
  ]
}}

Rules:
- All timestamps must be within [{window_start}, {window_end}].
- Describe ONLY what you observe — "frowning, arms crossed" not "feeling upset."
- If one continuous action spans the whole window, return a single action entry.
- speech_cue should be null (not the string "null") if the person is not speaking.
- No quotation marks anywhere in your answer: quoted words cannot be
  verified against the transcript, and a deterministic check strips them."""

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

PROMPT_ASSESSMENT = """This is a {duration:.0f}-second video clip. Classify it.

content_type: one of person_talking_to_camera, scenery, action_sequence, multiple_people, object_showcase, transition
primary_subject_visible: time ranges where the main person is visible

JSON only, no other text:
{{"content_type": "type_here", "primary_subject_visible": [[0, {duration:.0f}]]}}"""


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
                checkpoint carries. The video loader (cv2 frames) never
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
    """
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
    """
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
                result = subprocess.run(
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


def extract_video_clips(clip_path, duration, cache_dir, window_s=ACTION_WINDOW_S,
                        subdir="clips", prefix="clip", with_audio=True):
    """Extract video clips for windowed native-video analysis.

    Uses ffmpeg to cut the source video into ~`window_s` segments. Each
    segment is re-encoded (ultrafast) to ensure clean start/end
    boundaries.

    With `with_audio=True` (the default) the window keeps an AAC audio
    track, which the action passes hand to the model alongside the video
    (`analyze(..., audio=...)`) - measured on mlx-vlm 0.7.2, gemma4 hears
    it. With `with_audio=False` the track is stripped (`-an`); only the
    native scene/camera/assessment windows use that, and their prompts
    claim nothing about audio either way.

    A cached window whose audio presence mismatches `with_audio` is
    re-cut: caches written when action windows were stripped (`-an`)
    would otherwise be reused silently, and `load_audio` fails on them
    with "No audio streams found in file".

    Returns list of {index, start, end, path, has_audio}.
    """
    clip_dir = cache_dir / clip_path.stem / subdir
    clip_dir.mkdir(parents=True, exist_ok=True)

    source_has_audio = _file_has_audio(clip_path) if with_audio else False

    n_windows = max(1, int(math.ceil(duration / window_s)))
    clips = []

    for i in range(n_windows):
        start = i * window_s
        end = min((i + 1) * window_s, duration)
        out_path = clip_dir / f"{prefix}_{i:03d}.mp4"

        want_audio = with_audio and source_has_audio
        if out_path.exists():
            cached_has_audio = _file_has_audio(out_path)
            if cached_has_audio != want_audio:
                # Stale cache from the other audio policy - re-cut below.
                try:
                    out_path.unlink()
                except OSError:
                    pass

        if not out_path.exists():
            cmd = ["ffmpeg", "-y", "-i", str(clip_path),
                   "-ss", str(start), "-t", str(end - start),
                   "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"]
            if want_audio:
                cmd += ["-c:a", "aac"]
            else:
                # No audio in the window clip. The model therefore
                # cannot hear anything in it.
                cmd += ["-an"]
            cmd += ["-loglevel", "error", str(out_path)]
            subprocess.run(cmd, capture_output=True, check=False)

        if out_path.exists():
            clips.append({
                "index": i,
                "start": round(start, 2),
                "end": round(end, 2),
                "path": str(out_path),
                "has_audio": _file_has_audio(out_path),
            })

    return clips


def extract_native_windows(clip_path, duration, cache_dir,
                           window_s=NATIVE_WINDOW_S):
    """Windows for the whole-video passes (scene, camera, assessment).

    One native-video model call sees at most 32 frames, so a clip longer
    than `NATIVE_WINDOW_S` is split here - the same cut-and-re-encode
    machinery as the action windows - instead of being silently
    sparse-sampled whole. Clips at or under the ceiling yield one window
    covering the clip, i.e. exactly the single call the passes made
    before. Windows are video-only; their prompts claim nothing about
    audio.
    """
    return extract_video_clips(
        clip_path, duration, cache_dir, window_s=window_s,
        subdir="native_windows", prefix="native", with_audio=False,
    )


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
    # is deferred to analyze_assessment where the model provides it).
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


def _offset_segments(segments, offset):
    """Shift segment start/end by `offset`, keeping every other key verbatim."""
    out = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        seg = dict(seg)
        try:
            seg["start"] = round(float(seg["start"]) + offset, 3)
            seg["end"] = round(float(seg["end"]) + offset, 3)
        except (TypeError, ValueError, KeyError):
            continue
        out.append(seg)
    return out


def analyze_scene(analyzer, video_path, duration, temporal_index):
    """Analyze scene/environment — boundary-guided hybrid, 1 model call."""
    boundaries_text = _scene_boundaries_text(temporal_index)

    prompt = PROMPT_SCENE.format(duration=duration, boundaries=boundaries_text)
    result, _raw, elapsed = analyzer.analyze_with_retry(
        prompt, parse_json_array, video=video_path,
        max_tokens=MAX_TOKENS["scene"], label="Scene"
    )
    return result, elapsed


def analyze_scene_windowed(analyzer, duration, temporal_index, native_windows, fps):
    """Scene pass over native windows; segments offset to clip time.

    Clips at or under `NATIVE_WINDOW_S` yield one window, i.e. exactly the
    single call `analyze_scene` makes. Longer clips get one call per
    window (32 frames each) instead of 32 frames for the whole clip.

    Returns (merged_segments, total_elapsed, sampling_windows).
    """
    segments = []
    total_elapsed = 0
    sampling_windows = []
    for w in native_windows:
        w_dur = w["end"] - w["start"]
        boundaries_text = _scene_boundaries_text(
            temporal_index, start=w["start"], end=w["end"])
        prompt = PROMPT_SCENE.format(duration=w_dur, boundaries=boundaries_text)
        result, _raw, elapsed = analyzer.analyze_with_retry(
            prompt, parse_json_array, video=w["path"],
            max_tokens=MAX_TOKENS["scene"],
            label=f"Scene [{w['start']:.0f}-{w['end']:.0f}s]"
        )
        total_elapsed += elapsed
        segments.extend(_offset_segments(result, w["start"]))
        sampling_windows.append({
            "start": w["start"], "end": w["end"],
            **native_sample_plan(w_dur, fps),
        })
    return segments, total_elapsed, sampling_windows


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


def analyze_camera(analyzer, video_path, duration):
    """Analyze camera behavior — one-shot, 1 model call."""
    result, _raw, elapsed = _camera_one(
        analyzer, video_path, duration, label="Camera")
    # Collapse consecutive identical modes
    result = merge_camera_modes(result)
    return result, elapsed


def _camera_one(analyzer, video_path, duration, label="Camera"):
    """One camera model call; raw modes, unmerged."""
    prompt = PROMPT_CAMERA.format(duration=duration)
    return analyzer.analyze_with_retry(
        prompt, parse_json_array, video=video_path,
        max_tokens=MAX_TOKENS["camera"], label=label
    )


def analyze_camera_windowed(analyzer, duration, native_windows, fps):
    """Camera pass over native windows; modes offset to clip time.

    The single merge runs once over the concatenated windows, so an
    identical mode spanning a window boundary still collapses.

    Returns (merged_modes, total_elapsed, sampling_windows).
    """
    modes = []
    total_elapsed = 0
    sampling_windows = []
    for w in native_windows:
        w_dur = w["end"] - w["start"]
        result, _raw, elapsed = _camera_one(
            analyzer, w["path"], w_dur,
            label=f"Camera [{w['start']:.0f}-{w['end']:.0f}s]")
        total_elapsed += elapsed
        modes.extend(_offset_segments(result, w["start"]))
        sampling_windows.append({
            "start": w["start"], "end": w["end"],
            **native_sample_plan(w_dur, fps),
        })
    return merge_camera_modes(modes), total_elapsed, sampling_windows


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


def analyze_actions(analyzer, video_clips, duration, temporal_index, transcript,
                    fps=None):
    """Analyze actions/behavior - one model call per 10s video clip.

    Each window clip keeps its audio track, which the model hears
    alongside the video (`audio=`); `fps` records the sampling the call
    actually used (`native_sample_plan`) on the window entry.

    Returns list of window results.  Each entry carries ``parse_error``
    when the VLM response could not be parsed into valid JSON, so
    consumers can distinguish *unparsed* (the model returned gibberish)
    from *genuinely empty* (the model saw nothing happening).

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

        prompt = PROMPT_ACTION_WINDOW.format(
            window_start=w_start,
            window_end=w_end,
            window_dur=w_dur,
            duration=duration,
            transcript_line=transcript_line,
        )

        result, _raw, elapsed = analyzer.analyze_with_retry(
            prompt, parse_json_object, video=clip_info["path"],
            audio=clip_info["path"] if clip_info.get("has_audio") else None,
            max_tokens=MAX_TOKENS["action_window"],
            label=f"Actions [{w_start:.0f}-{w_end:.0f}s]"
        )

        # `parse_json_object` returns {} on total parse failure.
        # An empty dict is falsy; a dict with `actions: []` is truthy.
        # The distinction matters: {} means the model's response could
        # not be parsed at all, while {"actions": []} means the model
        # saw nothing happening in this window.
        parse_failed = not result

        # Ensure the result has the window field
        if result and "window" not in result:
            result["window"] = [w_start, w_end]
        if result and "actions" not in result:
            result["actions"] = []

        entry = {
            "window": [w_start, w_end],
            "actions": result.get("actions", []),
            "analysis_time_s": round(elapsed, 2),
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
            print(f"    ⚠ Actions [{w_start:.0f}-{w_end:.0f}s]: "
                  f"window recorded as UNPARSED (VLM response was not "
                  f"valid JSON after retry)", file=sys.stderr)

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
                print(f"    ⚠ Actions [{w_start:.0f}-{w_end:.0f}s]: "
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


def _assess_one(analyzer, video_path, duration, label="Assessment"):
    """One assessment model call; raw result dict ({} when unparsed)."""
    prompt = PROMPT_ASSESSMENT.format(duration=duration)
    return analyzer.analyze_with_retry(
        prompt, parse_json_object, video=video_path,
        max_tokens=MAX_TOKENS["assessment"], label=label
    )


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


def analyze_assessment(analyzer, video_path, duration, deterministic,
                       temporal_index=None, soft_picture_ranges=None):
    """Assessment - model call for visual judgment fields.

    Merges with pre-computed deterministic fields.  Usable ranges come
    from the deterministic dict (Rules 1-2); Rule 3 (subject absence)
    is applied here because it requires `content_type` from the model.
    All usable_ranges fields are set AFTER the model merge so the model
    cannot override them.
    """
    result, _raw, elapsed = _assess_one(analyzer, video_path, duration)

    # Merge deterministic fields into model results.
    #
    # When the call produced nothing, `primary_subject_visible` is None,
    # not `[]`.  `[]` is a claim - "the subject appears nowhere in this
    # clip" - and it reaches the B-roll prompt as one.  This is the same
    # defect `usable_ranges: [[0, duration]]` was, inverted: an answer
    # asserted where no pass ran.  `content_type` says "unknown" for the
    # same reason.
    #
    # The same holds one level in: an answer that came back WITHOUT the
    # key is not an answer of `[]` either, so the key's absence is carried
    # as None.  A model that returned `[]` really did say the subject is
    # nowhere, and that is kept.
    if result:
        content_type = result.get("content_type", "unknown")
        primary_subject_visible = result.get("primary_subject_visible")
    else:
        content_type = "unknown"
        primary_subject_visible = None

    assessment = _finish_assessment(
        deterministic, content_type, primary_subject_visible,
        temporal_index, duration, soft_picture_ranges)

    return assessment, elapsed


def analyze_assessment_windowed(analyzer, duration, deterministic, native_windows,
                                fps, temporal_index=None,
                                soft_picture_ranges=None):
    """Assessment pass over native windows; votes merged to clip level.

    `content_type` is the most frequent window vote (ties go to the
    earliest window); `primary_subject_visible` is the union of the
    windows' ranges offset to clip time. When no window parses,
    `content_type` is "unknown" and `primary_subject_visible` is None -
    the same absent-measurement the single call reports. The
    deterministic tail (`_finish_assessment`) is shared with it.

    Returns (assessment, total_elapsed, sampling_windows).
    """
    from collections import Counter

    votes = []
    psv_all = []
    explicit_empty = 0
    voted_windows = 0
    total_elapsed = 0
    sampling_windows = []
    for w in native_windows:
        w_dur = w["end"] - w["start"]
        result, _raw, elapsed = _assess_one(
            analyzer, w["path"], w_dur,
            label=f"Assessment [{w['start']:.0f}-{w['end']:.0f}s]")
        total_elapsed += elapsed
        if result:
            voted_windows += 1
            votes.append(result.get("content_type", "unknown"))
            psv = result.get("primary_subject_visible")
            if psv == []:
                explicit_empty += 1
            for rng in psv or []:
                try:
                    psv_all.append([round(float(rng[0]) + w["start"], 3),
                                    round(float(rng[1]) + w["start"], 3)])
                except (TypeError, ValueError, IndexError):
                    continue
        sampling_windows.append({
            "start": w["start"], "end": w["end"],
            **native_sample_plan(w_dur, fps),
        })

    if votes:
        counts = Counter(votes)
        first_seen = {v: i for i, v in enumerate(votes)}
        content_type = max(counts, key=lambda v: (counts[v], -first_seen[v]))
        if psv_all:
            primary_subject_visible = sorted(psv_all)
        elif explicit_empty == voted_windows:
            # Every window that answered returned `[]`: the model really
            # did say the subject is nowhere, and that is kept - same as
            # the single call.
            primary_subject_visible = []
        else:
            # An answer WITHOUT the key is not an answer of `[]`:
            # carried as None, same as the single call.
            primary_subject_visible = None
    else:
        content_type = "unknown"
        primary_subject_visible = None

    assessment = _finish_assessment(
        deterministic, content_type, primary_subject_visible,
        temporal_index, duration, soft_picture_ranges)

    return assessment, total_elapsed, sampling_windows


# ═══════════════════════════════════════════════════════════════════════
#  Main Analysis Orchestrator
# ═══════════════════════════════════════════════════════════════════════

def analyze_clip(analyzer, clip_meta, frames, video_clips, transcript,
                 temporal_index, cache_dir, native_windows=None):
    """Orchestrate all dimension passes for a single clip.

    Execution order:
      Group A (independent): Scene, Camera, Actions, Objects coarse
      Group B (dependent):   Objects detail (depends on coarse results)
      Group C (final):       Assessment (depends on all dimensions)

    `native_windows` carries the scene/camera/assessment windows
    (`extract_native_windows`); when None they are cut here, so a long
    clip is never silently sparse-sampled whole.
    """
    clip_id = clip_meta["clip_id"]
    duration = clip_meta["duration_s"]
    video_path = clip_meta["file_path"]
    clip_path = Path(clip_meta["file_path"])
    fps = clip_meta.get("fps") or 30.0

    if native_windows is None:
        native_windows = extract_native_windows(clip_path, duration, cache_dir)
    n_native_calls = len(native_windows)

    n_action_calls = len(video_clips)
    n_obj_coarse_calls = max(1, int(math.ceil(len(frames) / COARSE_BATCH_SIZE)))
    n_estimated = (n_native_calls * 3 + n_action_calls + n_obj_coarse_calls
                   + 1)  # +detail is variable

    print(f"\n{'═'*60}")
    print(f"  Analyzing: {clip_id} ({duration:.1f}s)")
    print(f"  Estimated model calls: {n_estimated}+ "
          f"({n_native_calls} scene + {n_native_calls} camera + "
          f"{n_action_calls} actions + "
          f"{n_obj_coarse_calls} obj coarse + detail + "
          f"{n_native_calls} assessment)")
    print(f"{'═'*60}")

    total_time = 0
    total_calls = 0

    # ── Group A: Independent passes ──────────────────────────────────

    # 1. Scene
    print(f"\n  [Scene] Boundary-guided hybrid "
          f"({n_native_calls} window(s) × ≤{NATIVE_WINDOW_S}s, video)...",
          end=" ", flush=True)
    scene, t, scene_windows = analyze_scene_windowed(
        analyzer, duration, temporal_index, native_windows, fps)
    total_time += t
    total_calls += n_native_calls
    # The model describes whatever span it feels like - on project 001
    # four long clips stop at 13.9-18.9 s while one 85.8 s clip was
    # described whole (issue #302). Normalize once here so one sloppy
    # end does not become fifteen quiet misreads, and record what share
    # of the clip was actually described: nothing downstream may invent
    # a location for the rest.
    scene = normalize_segments(scene, duration)
    scene_coverage = coverage_summary(scene, duration)
    print(f"({t:.1f}s) → {len(scene)} scene(s), "
          f"{scene_coverage['ratio']:.0%} of {duration:.1f}s described")

    # 2. Camera
    print(f"  [Camera] Windowed ({n_native_calls} call(s), video)...",
          end=" ", flush=True)
    camera, t, camera_windows = analyze_camera_windowed(
        analyzer, duration, native_windows, fps)
    total_time += t
    total_calls += n_native_calls
    # The same windowed shape as the scene pass, so the same sloppy
    # bounds - normalized for the same reason.
    camera = normalize_segments(camera, duration)
    print(f"({t:.1f}s) → {len(camera)} mode(s)")

    # 3. Actions
    print(f"  [Actions] {n_action_calls} windows × {ACTION_WINDOW_S}s (video+audio clips)...")
    actions = analyze_actions(analyzer, video_clips, duration, temporal_index,
                              transcript, fps=fps)
    action_time = sum(a.get("analysis_time_s", 0) for a in actions)
    total_action_count = sum(len(a.get("actions", [])) for a in actions)
    total_time += action_time
    total_calls += n_action_calls
    for a in actions:
        w = a["window"]
        n_acts = len(a.get("actions", []))
        t = a.get("analysis_time_s", 0)
        tag = " ⚠ UNPARSED" if a.get("parse_error") else ""
        print(f"    [{w[0]:.0f}-{w[1]:.0f}s] {n_acts} action(s) ({t:.1f}s){tag}")
    unparsed = [a for a in actions if a.get("parse_error")]
    if unparsed:
        print(f"  ⚠ {len(unparsed)} of {len(actions)} action window(s) "
              f"could not be parsed and are recorded as UNPARSED",
              file=sys.stderr)

    # 4. Objects — coarse sweep
    print(f"  [Objects] Coarse sweep ({len(frames)} frames, {n_obj_coarse_calls} batch(es))...",
          end=" ", flush=True)
    coarse_objects, t = analyze_objects_coarse(analyzer, frames, duration)
    total_time += t
    total_calls += n_obj_coarse_calls
    print(f"({t:.1f}s) → {len(coarse_objects)} entities")

    # ── Group B: Dependent passes ────────────────────────────────────

    # 5. Objects — detail pass
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

    # 6. Assessment — hybrid (deterministic + model)
    #
    # The picture is sampled ONCE here and handed to both calls below.
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
    print(f"  [Assessment] Hybrid ({n_native_calls} call(s), video + deterministic)...",
          end=" ", flush=True)
    assessment, t, assessment_windows = analyze_assessment_windowed(
        analyzer, duration, deterministic, native_windows, fps,
        temporal_index=temporal_index, soft_picture_ranges=soft_ranges)
    total_time += t
    total_calls += n_native_calls
    print(f"({t:.1f}s) → {assessment.get('content_type', '?')}")

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
            # gemma4 path), so a clip longer than `native_window_s`
            # takes one call per window instead of 32 frames whole.
            # Each window entry records {start, end, frames,
            # decode_fps, effective_fps} (`native_sample_plan`); each
            # action entry carries its own `sampling` plus `has_audio`.
            "video_sampling": {
                "frames_per_call": NATIVE_VIDEO_FRAMES_PER_CALL,
                "decode_fps": NATIVE_VIDEO_DECODE_FPS,
                "native_window_s": NATIVE_WINDOW_S,
                "windows": {
                    "scene": scene_windows,
                    "camera": camera_windows,
                    "assessment": assessment_windows,
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
    output_dir.mkdir(parents=True, exist_ok=True)
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

            # Extract video clips (for actions — 10s segments, audio kept)
            video_clips = extract_video_clips(clip_path, duration, cache_dir)
            n_aud = sum(1 for c in video_clips if c.get("has_audio"))
            print(f"  Video clips extracted: {len(video_clips)} × {ACTION_WINDOW_S}s "
                  f"({n_aud} with audio)")

            # Extract native windows (for scene/camera/assessment — ≤60s each)
            native_windows = extract_native_windows(clip_path, duration, cache_dir)
            print(f"  Native windows extracted: {len(native_windows)} "
                  f"× ≤{NATIVE_WINDOW_S}s")

            # Run analysis
            profile = analyze_clip(
                analyzer, meta, frames, video_clips, transcript,
                temporal_idx, cache_dir, native_windows=native_windows,
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
