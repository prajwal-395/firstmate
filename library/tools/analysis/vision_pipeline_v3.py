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

from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template


# ═══════════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════════

MODEL_ID = "mlx-community/gemma-4-12b-it-4bit"
CACHE_DIR = Path(".vision_cache")
OUTPUT_DIR = Path("pipeline_output")

# Action windows
ACTION_WINDOW_S = 10

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
- speech_cue should be null (not the string "null") if the person is not speaking."""

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

    def __init__(self):
        print(f"\n{'─'*60}")
        print(f"  Loading {MODEL_ID}...")
        print(f"{'─'*60}")
        t0 = time.time()
        self.model, self.proc = load(MODEL_ID)
        self.load_time = time.time() - t0
        print(f"  Model loaded in {self.load_time:.1f}s")

    def analyze(self, prompt, images=None, video=None, max_tokens=512):
        """Run a single analysis pass. Returns (text, elapsed_seconds).

        Args:
            prompt: The text prompt.
            images: List of image file paths (for frame-based passes).
            video: Path to a video file (for video-based passes).
            max_tokens: Maximum tokens to generate.
        """
        if images:
            formatted = apply_chat_template(
                self.proc, self.model.config, prompt,
                num_images=len(images)
            )
        elif video:
            prompt = f"<|video|>{prompt}"
            formatted = apply_chat_template(
                self.proc, self.model.config, prompt, num_images=0
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
            max_tokens=max_tokens,
            temperature=0.1,
            verbose=False,
        )
        elapsed = time.time() - t0
        text = r.text if hasattr(r, "text") else str(r)
        return text, elapsed

    def analyze_with_retry(self, prompt, parse_fn, images=None, video=None,
                           max_tokens=512, label="pass"):
        """Run analysis with one retry on parse failure.

        Args:
            prompt: The text prompt.
            parse_fn: Function to parse the output (parse_json_array or parse_json_object).
            images, video, max_tokens: Passed to analyze().
            label: Label for logging.

        Returns:
            (parsed_result, raw_text, total_elapsed)
        """
        text, elapsed = self.analyze(prompt, images=images, video=video,
                                     max_tokens=max_tokens)
        result = parse_fn(text.strip())

        # Retry once if parse produced empty result
        if not result:
            retry_suffix = "\n\nIMPORTANT: Respond with ONLY valid JSON. No markdown, no explanation, no text before or after the JSON."
            text2, elapsed2 = self.analyze(prompt + retry_suffix, images=images,
                                           video=video, max_tokens=max_tokens)
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
            capture_output=True, text=True,
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
    """Extract frames at a fixed interval. Returns list of {timestamp, path}."""
    frame_dir = cache_dir / clip_path.stem / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    n_frames = max(1, int(math.ceil(duration / interval_s))) + 1
    frames = []

    for i in range(n_frames):
        timestamp = min(i * interval_s, duration - 0.1)
        out_path = frame_dir / f"frame_{i:04d}.jpg"

        if not out_path.exists():
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(clip_path),
                 "-vframes", "1", "-q:v", "2", str(out_path)],
                capture_output=True,
            )

        if out_path.exists():
            frames.append({
                "timestamp": round(timestamp, 3),
                "path": str(out_path),
            })

    return frames


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

            if not out_path.exists():
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(clip_path),
                     "-vframes", "1", "-q:v", "2", str(out_path)],
                    capture_output=True,
                )

            if out_path.exists():
                range_frames.append({
                    "timestamp": round(timestamp, 3),
                    "path": str(out_path),
                })
            frame_counter += 1

        result[(range_start, range_end)] = range_frames

    return result


def extract_video_clips(clip_path, duration, cache_dir, window_s=ACTION_WINDOW_S):
    """Extract video clips for action analysis windows.

    Uses ffmpeg to cut the source video into ~10s segments. Each segment
    is re-encoded (ultrafast) to ensure clean start/end boundaries.

    Returns list of {index, start, end, path}.
    """
    clip_dir = cache_dir / clip_path.stem / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)

    n_windows = max(1, int(math.ceil(duration / window_s)))
    clips = []

    for i in range(n_windows):
        start = i * window_s
        end = min((i + 1) * window_s, duration)
        out_path = clip_dir / f"clip_{i:03d}.mp4"

        if not out_path.exists():
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(clip_path),
                 "-ss", str(start), "-t", str(end - start),
                 "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                 "-an",  # No audio needed for visual analysis
                 "-loglevel", "error",
                 str(out_path)],
                capture_output=True,
            )

        if out_path.exists():
            clips.append({
                "index": i,
                "start": round(start, 2),
                "end": round(end, 2),
                "path": str(out_path),
            })

    return clips


# ═══════════════════════════════════════════════════════════════════════
#  Temporal Index & Transcript Loading
# ═══════════════════════════════════════════════════════════════════════

def load_temporal_index(clip_path):
    """Load temporal index data from raw/analysis/temporal_index/.

    Returns the full index dict, or None if not found.
    """
    temporal_dir = clip_path.parent / "analysis" / "temporal_index"
    if not temporal_dir.is_dir():
        return None

    clip_stem = clip_path.stem
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
    """
    if temporal_index:
        texts = []
        for region in temporal_index.get("speech_regions", []):
            if region["end"] > window_start and region["start"] < window_end:
                t = region.get("text", "").strip()
                if t:
                    texts.append(t)
        if texts:
            return " ".join(texts)

    # Fallback: proportion-based
    if full_transcript:
        duration = max(1, temporal_index.get("duration_s", 1)) if temporal_index else 1
        chars_per_sec = len(full_transcript) / duration
        excerpt = full_transcript[
            int(window_start * chars_per_sec):int(window_end * chars_per_sec)
        ].strip()
        if excerpt:
            return excerpt

    return ""


# ═══════════════════════════════════════════════════════════════════════
#  Deterministic Assessment Fields
# ═══════════════════════════════════════════════════════════════════════

def compute_deterministic_assessment(temporal_index, transcript):
    """Compute assessment fields that don't need the vision model.

    Returns dict with speech_present, speech_coverage, camera_stability.
    """
    result = {
        "speech_present": bool(transcript and transcript.strip()),
        "speech_coverage": 0.0,
        "camera_stability": "unknown",
    }

    if not temporal_index:
        return result

    # Support both key names: older temporal indices use "duration",
    # newer ones may use "duration_s"
    duration = temporal_index.get("duration_s") or temporal_index.get("duration") or 0

    # Speech coverage
    speech_regions = temporal_index.get("speech_regions", [])
    if speech_regions and duration > 0:
        total_speech = sum(
            r.get("end", 0) - r.get("start", 0) for r in speech_regions
        )
        result["speech_coverage"] = round(min(total_speech / duration, 1.0), 2)

    # Camera stability from optical flow variance
    camera_data = temporal_index.get("camera_motion", {})
    if isinstance(camera_data, dict):
        residuals = camera_data.get("residual", [])
        if residuals and len(residuals) > 10:
            arr = np.array(residuals, dtype=float)
            mean_residual = float(np.mean(arr))
            if mean_residual < 0.02:
                result["camera_stability"] = "stable"
            elif mean_residual < 0.08:
                result["camera_stability"] = "handheld"
            else:
                result["camera_stability"] = "unstable"

    # Alternative: use motion energy variance
    if result["camera_stability"] == "unknown":
        motion_data = temporal_index.get("motion_energy", {})
        if isinstance(motion_data, dict):
            values = motion_data.get("values", [])
            if values and len(values) > 30:
                arr = np.array(values, dtype=float)
                std = float(np.std(arr))
                if std < 0.05:
                    result["camera_stability"] = "stable"
                elif std < 0.15:
                    result["camera_stability"] = "handheld"
                else:
                    result["camera_stability"] = "unstable"

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

def analyze_scene(analyzer, video_path, duration, temporal_index):
    """Analyze scene/environment — boundary-guided hybrid, 1 model call."""
    boundaries = get_scene_boundaries(temporal_index)
    if boundaries:
        boundaries_str = ", ".join(f"{t:.1f}s" for t in boundaries)
        boundaries_text = f"Detected visual changes at: {boundaries_str}"
    else:
        boundaries_text = "No hard scene boundaries detected (likely continuous)"

    prompt = PROMPT_SCENE.format(duration=duration, boundaries=boundaries_text)
    result, raw, elapsed = analyzer.analyze_with_retry(
        prompt, parse_json_array, video=video_path,
        max_tokens=MAX_TOKENS["scene"], label="Scene"
    )
    return result, elapsed


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
    prompt = PROMPT_CAMERA.format(duration=duration)
    result, raw, elapsed = analyzer.analyze_with_retry(
        prompt, parse_json_array, video=video_path,
        max_tokens=MAX_TOKENS["camera"], label="Camera"
    )
    # Collapse consecutive identical modes
    result = merge_camera_modes(result)
    return result, elapsed


def analyze_actions(analyzer, video_clips, duration, temporal_index, transcript):
    """Analyze actions/behavior — one model call per 10s video clip.

    Returns list of window results.
    """
    results = []

    for clip_info in video_clips:
        w_start = clip_info["start"]
        w_end = clip_info["end"]
        w_dur = w_end - w_start

        # Get transcript for this window
        w_transcript = get_window_transcript(
            temporal_index, w_start, w_end, full_transcript=transcript
        )
        if w_transcript:
            transcript_line = f'\nSPEECH IN THIS SEGMENT: "{w_transcript}"'
        else:
            transcript_line = "\n(No speech in this segment.)"

        prompt = PROMPT_ACTION_WINDOW.format(
            window_start=w_start,
            window_end=w_end,
            window_dur=w_dur,
            duration=duration,
            transcript_line=transcript_line,
        )

        result, raw, elapsed = analyzer.analyze_with_retry(
            prompt, parse_json_object, video=clip_info["path"],
            max_tokens=MAX_TOKENS["action_window"],
            label=f"Actions [{w_start:.0f}-{w_end:.0f}s]"
        )

        # Ensure the result has the window field
        if result and "window" not in result:
            result["window"] = [w_start, w_end]
        if result and "actions" not in result:
            result["actions"] = []

        results.append({
            "window": [w_start, w_end],
            "actions": result.get("actions", []),
            "analysis_time_s": round(elapsed, 2),
        })

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

        result, raw, elapsed = analyzer.analyze_with_retry(
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

            result, raw, elapsed = analyzer.analyze_with_retry(
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


def analyze_assessment(analyzer, video_path, duration, deterministic):
    """Assessment — model call for visual judgment fields.

    Merges with pre-computed deterministic fields.
    """
    prompt = PROMPT_ASSESSMENT.format(duration=duration)
    result, raw, elapsed = analyzer.analyze_with_retry(
        prompt, parse_json_object, video=video_path,
        max_tokens=MAX_TOKENS["assessment"], label="Assessment"
    )

    # Merge deterministic fields into model results
    assessment = dict(deterministic)
    assessment["usable_ranges"] = [[0, duration]]  # Default: entire clip usable
    assessment["unusable_ranges"] = []
    if result:
        assessment["content_type"] = result.get("content_type", "unknown")
        assessment["primary_subject_visible"] = result.get("primary_subject_visible", [])
    else:
        assessment["content_type"] = "unknown"
        assessment["primary_subject_visible"] = []

    return assessment, elapsed


# ═══════════════════════════════════════════════════════════════════════
#  Main Analysis Orchestrator
# ═══════════════════════════════════════════════════════════════════════

def analyze_clip(analyzer, clip_meta, frames, video_clips, transcript,
                 temporal_index, cache_dir):
    """Orchestrate all dimension passes for a single clip.

    Execution order:
      Group A (independent): Scene, Camera, Actions, Objects coarse
      Group B (dependent):   Objects detail (depends on coarse results)
      Group C (final):       Assessment (depends on all dimensions)
    """
    clip_id = clip_meta["clip_id"]
    duration = clip_meta["duration_s"]
    video_path = clip_meta["file_path"]
    clip_path = Path(clip_meta["file_path"])

    n_action_calls = len(video_clips)
    n_obj_coarse_calls = max(1, int(math.ceil(len(frames) / COARSE_BATCH_SIZE)))
    n_estimated = 1 + 1 + n_action_calls + n_obj_coarse_calls + 1  # +detail is variable

    print(f"\n{'═'*60}")
    print(f"  Analyzing: {clip_id} ({duration:.1f}s)")
    print(f"  Estimated model calls: {n_estimated}+ "
          f"(1 scene + 1 camera + {n_action_calls} actions + "
          f"{n_obj_coarse_calls} obj coarse + detail + 1 assessment)")
    print(f"{'═'*60}")

    total_time = 0
    total_calls = 0

    # ── Group A: Independent passes ──────────────────────────────────

    # 1. Scene
    print(f"\n  [Scene] Boundary-guided hybrid (1 call, video)...", end=" ", flush=True)
    scene, t = analyze_scene(analyzer, video_path, duration, temporal_index)
    total_time += t
    total_calls += 1
    print(f"({t:.1f}s) → {len(scene)} scene(s)")

    # 2. Camera
    print(f"  [Camera] One-shot (1 call, video)...", end=" ", flush=True)
    camera, t = analyze_camera(analyzer, video_path, duration)
    total_time += t
    total_calls += 1
    print(f"({t:.1f}s) → {len(camera)} mode(s)")

    # 3. Actions
    print(f"  [Actions] {n_action_calls} windows × {ACTION_WINDOW_S}s (video clips)...")
    actions = analyze_actions(analyzer, video_clips, duration, temporal_index, transcript)
    action_time = sum(a.get("analysis_time_s", 0) for a in actions)
    total_action_count = sum(len(a.get("actions", [])) for a in actions)
    total_time += action_time
    total_calls += n_action_calls
    for a in actions:
        w = a["window"]
        n_acts = len(a.get("actions", []))
        t = a.get("analysis_time_s", 0)
        print(f"    [{w[0]:.0f}-{w[1]:.0f}s] {n_acts} action(s) ({t:.1f}s)")

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
    deterministic = compute_deterministic_assessment(temporal_index, transcript)
    print(f"  [Assessment] Hybrid (1 call, video + deterministic)...", end=" ", flush=True)
    assessment, t = analyze_assessment(analyzer, video_path, duration, deterministic)
    total_time += t
    total_calls += 1
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

        "analysis_metadata": {
            "pipeline_version": "v3",
            "model": MODEL_ID,
            "total_model_calls": total_calls,
            "analysis_time_s": round(total_time, 2),
            "frames_extracted": len(frames),
            "video_clips_extracted": len(video_clips),
        },
    }

    # ── Summary ──────────────────────────────────────────────────────

    print(f"\n  ✓ Complete in {total_time:.1f}s ({total_calls} model calls)")
    print(f"    Scene: {len(scene)} | Camera: {len(camera)} | "
          f"Actions: {total_action_count} across {len(actions)} windows | "
          f"Objects: {len(objects)}")
    print(f"    Type: {assessment.get('content_type', '?')} | "
          f"Speech: {assessment.get('speech_coverage', 0):.0%} coverage | "
          f"Stability: {assessment.get('camera_stability', '?')}")

    return profile


# ═══════════════════════════════════════════════════════════════════════
#  Pipeline Runner
# ═══════════════════════════════════════════════════════════════════════

def run_pipeline(clips, cache_dir=CACHE_DIR, output_dir=OUTPUT_DIR, force=False):
    """Run the v3 vision pipeline on a list of clip paths.

    Args:
        clips: List of Path objects to video files.
        cache_dir: Directory for frame/clip cache.
        output_dir: Directory for output profiles.
        force: If True, re-analyze clips even if profile exists.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Load model once
    analyzer = VisionAnalyzer()

    all_profiles = []
    total_start = time.time()
    skipped = 0

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

        # Extract video clips (for actions — 10s segments)
        video_clips = extract_video_clips(clip_path, duration, cache_dir)
        print(f"  Video clips extracted: {len(video_clips)} × {ACTION_WINDOW_S}s")

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
    )


if __name__ == "__main__":
    main()
