# Local Video Analysis Pipeline — Process Guide

## Overview

A dimension-indexed local video analysis pipeline using Gemma 4 12B (MLX) that produces structured, objective clip profiles for downstream editing decisions. Each annotation dimension uses its own optimal temporal indexing strategy. Zero cost, unlimited runs, full privacy.

---

## Quick Start

```bash
# Analyze all clips in raw/
cd video_analysis_docs
python3 vision_pipeline_v3.py --raw-dir ../raw --output-dir ../pipeline_output

# Analyze specific clips
python3 vision_pipeline_v3.py --clip ../raw/IMG_1807.MOV ../raw/IMG_1813.MOV

# Re-analyze (overwrite existing profiles)
python3 vision_pipeline_v3.py --raw-dir ../raw --force

# Output goes to pipeline_output/clip_profile_{CLIP_ID}_v3.json
# Combined index at pipeline_output/vision_index_v3.json
```

---

## Dimension-Indexed Architecture

Each dimension of video content has its own natural temporal structure. Instead of forcing uniform windows or a fixed number of passes, each dimension is analyzed on the temporal index that best captures its information.

| # | Dimension | Temporal Index | Input | Model Calls |
|---|---|---|---|---|
| 1 | **Scene/Environment** | Event-driven (ffmpeg boundaries + model) | Video (full clip) | 1 |
| 2 | **Camera** | Event-driven (one-shot) | Video (full clip) | 1 |
| 3 | **Actions/Behavior** | Fixed 10s windows | Video (10s clips) + transcript | ceil(duration/10) |
| 4 | **Objects + OCR** | Entity-indexed, two-tier | Frames (coarse + detail) | Variable |
| 5 | **Assessment** | Clip-level | Video + deterministic signals | 1 |

### Why different temporal indexes?

- **Scene** changes rarely — a 3-minute clip might have 1 location. No need to describe it 18 times.
- **Actions** change constantly — 10s windows give regular coverage without losing temporal fidelity.
- **Camera** changes infrequently — one-shot captures the 2-3 mode changes in a clip.
- **Objects** appear and disappear independently — entity-indexed tracking follows each one separately.

### What's NOT in the vision pipeline

- **Energy/mood** — these are inferences, not observations. The planning LLM derives them from the objective annotations.
- **Audio prediction** — the model can't hear audio. Removed entirely.
- **Narrative summary** — editorial prose belongs in the planning layer, not analysis.

---

## Input Modality Rules

A critical lesson from testing: video and frames serve different purposes.

| Modality | Good For | Bad For |
|---|---|---|
| **Video** (direct file) | Temporal flow, motion, camera behavior | OCR, fine object detail |
| **Frames** (extracted stills) | OCR, text reading, object identification | Motion (model says "camera remains stationary") |

- Scene, Camera, Actions → **video** (they need temporal flow)
- Objects, OCR → **frames** (they need visual detail)

---

## Critical Technical Lessons

### 1. Chat Template is REQUIRED
```python
# ❌ WRONG — produces gibberish
generate(model, proc, prompt="Describe this image", image=["frame.jpg"])

# ✅ CORRECT — must wrap in chat template
formatted = apply_chat_template(proc, model.config, prompt, num_images=1)
generate(model, proc, prompt=formatted, image=["frame.jpg"])
```

### 2. Focused Prompts Close the Gap with Cloud Models
A generic "analyze this video" prompt produces mediocre results. Focused, single-task prompts ("identify every visible entity and when they appear") close ~60% of the quality gap with cloud models.

### 3. JSON Output Needs Explicit Instruction
The model will wrap JSON in markdown code blocks or add explanation text unless explicitly told not to. Every prompt includes "no markdown, no explanation, no extra text."

### 4. Retry on Parse Failure
~5-10% of model calls produce malformed JSON. A single retry with a reinforced "respond with ONLY valid JSON" instruction fixes most of these.

### 5. Objective Annotation Only
Tell the model to describe what it observes, not what it interprets:
- ✅ "person is frowning, arms crossed, looking down"
- ❌ "person feels upset and defensive"

---

## Output Format

Each clip produces a JSON profile:

```json
{
  "clip_id": "IMG_1816",
  "duration_s": 188.578,
  "transcript": "...",

  "scene": [
    {"start": 0.0, "end": 145.2, "location": "outdoor parking lot",
     "type": "outdoor", "lighting": "late afternoon, warm sunlight",
     "notable_features": ["TopGolf sign on building"]}
  ],

  "camera": [
    {"start": 0.0, "end": 135.0, "mode": "selfie", "framing": "close-up",
     "stability": "mostly stable, slight drift", "movement": "stationary"}
  ],

  "actions": [
    {"window": [0.0, 10.0], "actions": [
      {"start": 0.0, "end": 10.0, "action": "standing, talking to camera",
       "speech_cue": "moderate volume, gesturing with right hand",
       "body_language": "upright, direct eye contact with lens"}
    ]}
  ],

  "objects": [
    {"label": "young man in black baseball cap", "appearances": [[0.0, 188.6]],
     "role": "primary_subject", "category": "person",
     "readable_text": null}
  ],

  "assessment": {
    "content_type": "person_talking_to_camera",
    "speech_present": true,
    "speech_coverage": 0.85,
    "camera_stability": "stable",
    "usable_ranges": [[0.0, 188.6]],
    "unusable_ranges": [],
    "primary_subject_visible": [[0.0, 188.6]]
  }
}
```

---

## Performance

| Metric | Value |
|---|---|
| Model | mlx-community/gemma-4-12b-it-4bit |
| RAM | ~8-9.3 GB peak |
| Load time | ~3s |
| Per clip (short <10s) | ~30-50s (~6 calls) |
| Per clip (medium 30s) | ~90-120s (~10 calls) |
| Per clip (long 180s) | ~200-300s (~29 calls) |
| 17-clip project | ~30-45 min (estimated) |
| Cost | Free |
| Privacy | Fully on-device |

---

## Files

| File | Purpose |
|---|---|
| `vision_pipeline_v3.py` | Active pipeline script (dimension-indexed) |
| `vision_architecture.md` | Architecture documentation |
| `../pipeline_output/clip_profile_*_v3.json` | Individual clip profiles |
| `../pipeline_output/vision_index_v3.json` | Combined index of all analyzed clips |
| `../.vision_cache/*/frames/` | Cached extracted frames |
| `../.vision_cache/*/clips/` | Cached 10s video clips for actions |
| `../.vision_cache/*/detail_frames/` | Cached detail-pass frames |
