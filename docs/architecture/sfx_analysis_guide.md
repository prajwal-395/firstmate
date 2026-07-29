# SFX Analysis Pipeline — Process Guide

## Overview

A local SFX profiling pipeline that analyzes every sound effect in your library across 7 dimensions — from raw acoustic properties to rich semantic descriptions. Runs entirely on-device using Apple Silicon, produces a searchable index for automated SFX placement.

**Stack**: Audio Flamingo Next (int4) + librosa + sentence-transformers + FAISS

---

## Quick Start

```bash
# Profile entire SFX library (full mode — includes AI descriptions, ~90s per file)
cd video_analysis_docs
python3 sfx_pipeline.py --sfx-dir "/path/to/sfx library" --output-dir ../pipeline_output/sfx_profiles

# Fast mode — librosa only, skips AF-Next (instant)
python3 sfx_pipeline.py --sfx-dir "/path/to/sfx library" --fast

# Profile a single file
python3 sfx_pipeline.py --file "/path/to/metal-pipe-falling.mp3"

# Search the indexed library
python3 sfx_pipeline.py --search "short punchy metallic impact"
```

---

## The 7 Dimensions of SFX Analysis

Every sound effect is profiled across 7 complementary dimensions:

| # | Dimension | Tool | What It Captures | Speed |
|---|---|---|---|---|
| 1 | **Semantic Description** | Audio Flamingo Next | Natural language description of the sound | ~90s/file |
| 2 | **Technical Properties** | librosa | Duration, loudness, frequency content | Instant |
| 3 | **Energy Profile** | librosa | Attack time, decay, envelope shape (ADSR) | Instant |
| 4 | **Tonal vs Noise** | librosa | Pitched or unpitched, fundamental frequency | Instant |
| 5 | **Temporal Structure** | librosa | One-shot vs multi-hit, number of events | Instant |
| 6 | **Folder Category** | File system | User-defined categories from folder names | Instant |
| 7 | **Similarity Embedding** | sentence-transformers | Searchable vector from AF-Next description | Instant |

### Why these 7?

We intentionally **excluded** mood and use-case classification because they can be **derived at edit time** from the technical + semantic data. For example:
- *"Punchy + short + metallic"* → accent/emphasis (derived from dimensions 2-5)
- *"Deep rumble + long + swelling"* → tension builder (derived from dimensions 1-3)
- Mood is subjective and context-dependent — the same SFX can be "comedic" in one edit and "dramatic" in another

---

## Tool Deep Dive

### Audio Flamingo Next (Semantic Descriptions)

**What**: NVIDIA's 7B-parameter audio-language model. Listens to audio and writes detailed natural language descriptions.

**Why this model**: After testing Qwen2-Audio, MiniCPM-o, Phi-4, and CLAP, Audio Flamingo Next was the only model that:
- Runs on 24GB Apple Silicon
- Actually understands non-speech audio (SFX, foley, environmental sounds)
- Produces detailed, accurate descriptions

**How it runs on Mac**:

| Setting | Value |
|---|---|
| Model | `nvidia/audio-flamingo-next-captioner-hf` |
| Quantization | `QuantoConfig(weights="int4")` via optimum-quanto |
| dtype | `torch.float16` |
| Device | MPS (Apple GPU) |
| GPU Memory | ~5.6 GB |
| Load time | ~25 seconds |
| Per-file speed | ~90 seconds |

**Required patches for Apple Silicon**:
1. **Float64 → Float32**: MPS doesn't support float64. The model's `apply_rotary_time_emb` function must be monkey-patched to use float32 instead.
2. **Audio padding**: The Whisper-based audio encoder expects 30-second inputs. Short SFX files are zero-padded to 30 seconds.
3. **Input dtype casting**: The processor outputs float32 features, but the model runs in float16. Inputs must be `.half()`'d before inference.

**Example output**:
| SFX File | AF-Next Description |
|---|---|
| `metal-pipe-falling.mp3` | "A loud, sharp, and high-pitched metallic clang followed by a rapid series of metallic clinks and rattles." |
| `minecraft eating.mp3` | "A sharp, high-pitched squeak, immediately followed by a rapid series of rhythmic, high-frequency squeaks and creaks." |
| `Bass_riser.wav` | "A deep, resonant low-frequency rumble that quickly swells in intensity, creating a sense of immense scale and power." |

### librosa (Technical Analysis)

Extracts measurable acoustic properties — instant per file:

**Basic Properties**:
- `duration_s` — length in seconds
- `sample_rate` — original sample rate

**Loudness**:
- `rms_mean` / `rms_max` — average and peak loudness
- `peak_amplitude` — absolute maximum sample value

**Spectral (frequency content)**:
- `spectral_centroid_mean` — "brightness" (higher = brighter)
- `spectral_bandwidth_mean` — frequency spread
- `spectral_rolloff_mean` — frequency below which most energy sits
- `spectral_flatness_mean` — 0 = tonal, 1 = noise-like
- `zero_crossing_rate_mean` — rough noisiness indicator

**Energy Profile**:
- `attack_time_ms` — time from start to peak loudness
- `decay_time_ms` — time from peak to -20dB below
- `envelope_shape` — classified as: `punchy` | `sustained` | `swelling` | `fading`

**Tonal Analysis**:
- `is_tonal` — boolean, based on spectral flatness < 0.1
- `estimated_pitch_hz` — fundamental frequency (if tonal)

**Temporal Structure**:
- `num_onsets` — number of distinct sound events
- `is_one_shot` — true if ≤2 onsets and duration < 3 seconds

### sentence-transformers + FAISS (Search Index)

**Why not CLAP?** CLAP provides generic 512-dim audio embeddings. But since we already have AF-Next's rich text descriptions, we can:
1. Embed those descriptions with a text model → **richer semantic vectors**
2. Search using natural language → *"find me a short punchy metallic impact"*
3. Combine with metadata filtering → *"...that's under 2 seconds"*

**Model**: `all-MiniLM-L6-v2` (384-dim embeddings, runs on CPU, ~50MB)
**Index**: FAISS `IndexFlatIP` (cosine similarity on L2-normalized vectors)

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    SFX Library                       │
│  /sfx library/                                       │
│    ├── Accents/ (Glitch, Risers, Slide, Whoosh)     │
│    ├── Action/ (Camera Shutter, engine sounds, ...)  │
│    ├── Environment/                                  │
│    └── DJ Mixes/                                     │
└──────────────────┬───────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│              Stage 1: librosa (instant)              │
│  For each .mp3/.wav file:                            │
│  → Extract 15+ acoustic features                    │
│  → Classify envelope shape, tonal/noise, structure   │
└──────────────────┬───────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│         Stage 2: Audio Flamingo Next (~90s/file)     │
│  For each file (can be skipped with --fast):         │
│  → Load audio, pad to 30s                           │
│  → Run int4 quantized model on MPS                  │
│  → Generate natural language description             │
└──────────────────┬───────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│       Stage 3: Embedding + Index (instant)           │
│  → Embed AF-Next descriptions with MiniLM            │
│  → Build FAISS index for similarity search           │
│  → Save profiles + index to pipeline_output/         │
└──────────────────┬───────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│                    Output                            │
│  pipeline_output/sfx_profiles/                       │
│    ├── metal-pipe-falling.json    (per-file profile) │
│    ├── minecraft eating.json                         │
│    ├── ...                                           │
│    ├── sfx_index.json             (combined index)   │
│    └── sfx.faiss                  (vector index)     │
└──────────────────────────────────────────────────────┘
```

---

## Output Format

Each SFX file produces a JSON profile:

```json
{
  "file": "metal-pipe-falling.mp3",
  "path": "/full/path/to/metal-pipe-falling.mp3",
  "folder_category": "Action",
  "technical": {
    "duration_s": 3.2,
    "sample_rate": 44100,
    "rms_mean": 0.045,
    "rms_max": 0.312,
    "peak_amplitude": 0.89,
    "spectral_centroid_mean": 4521.3,
    "spectral_bandwidth_mean": 3200.1,
    "spectral_rolloff_mean": 8100.5,
    "spectral_flatness_mean": 0.42,
    "zero_crossing_rate_mean": 0.15,
    "attack_time_ms": 12.0,
    "decay_time_ms": 850.0,
    "envelope_shape": "punchy",
    "is_tonal": false,
    "estimated_pitch_hz": null,
    "num_onsets": 5,
    "is_one_shot": false
  },
  "description": "A loud, sharp, and high-pitched metallic clang followed by a rapid series of metallic clinks and rattles.",
  "embedding": [0.12, -0.34, ...]
}
```

---

## Performance

| Metric | Value |
|---|---|
| **Fast mode** (librosa only) | ~5 seconds for 74 files |
| **Full mode** (with AF-Next) | ~110 minutes for 74 files |
| **AF-Next load time** | ~25 seconds |
| **AF-Next per file** | ~90 seconds |
| **librosa per file** | <0.1 seconds |
| **Embedding per file** | <0.01 seconds |
| **GPU memory** | ~5.6 GB |
| **Total RAM** | <10 GB |
| **Cost** | Free (fully on-device) |

---

## What We Tried (and Why We Landed Here)

### Models That Didn't Work on Mac

| Model | Why It Failed |
|---|---|
| Qwen2-Audio | Multimodal MPS support broken in transformers |
| MiniCPM-o 4.5 | Missing audio processing config |
| Phi-4 Multimodal | No audio input support |
| CLAP | Works but only gives generic embeddings — AF-Next descriptions are richer |
| Ollama Gemma4 MLX | MLX backend doesn't support audio modality |

### Quantization Journey

| Approach | Result |
|---|---|
| `bitsandbytes` load_in_4bit | ❌ CUDA only |
| `torchao` Int4WeightOnlyConfig | ❌ Requires `mslk` (not released) |
| `torchao` Int8WeightOnlyConfig | ⚠️ Hangs on `.to("mps")` |
| **`QuantoConfig(weights="int4")`** | ✅ **Works on CPU and MPS** |

### MPS Compatibility Fixes

| Issue | Fix |
|---|---|
| `float64` not supported on MPS | Monkey-patch `apply_rotary_time_emb` → float32 |
| Processor outputs float32, model expects float16 | Cast inputs with `.half()` |
| Short audio < 30s crashes Whisper encoder | Zero-pad to 30 seconds |

---

## Files

| File | Purpose |
|---|---|
| `sfx_pipeline.py` | Main pipeline script |
| `../pipeline_output/sfx_profiles/*.json` | Individual SFX profiles |
| `../pipeline_output/sfx_profiles/sfx_index.json` | Combined index of all analyzed SFX |
| `../pipeline_output/sfx_profiles/sfx.faiss` | FAISS vector index for similarity search |

---

## Integration with Video Pipeline

The SFX profiles integrate with the existing vision pipeline (`vision_pipeline.py`):

1. **Vision pipeline** analyzes video clips → produces `clip_profile_*.json` with audio predictions
2. **SFX pipeline** profiles the SFX library → produces `sfx_index.json` with descriptions + embeddings
3. **At edit time**: match video clip audio predictions against SFX descriptions via FAISS search, filtered by technical properties (duration, energy, etc.)

Example workflow:
```
Video clip audio prediction: "Door handle click, leather seat creak"
                    ↓
        Embed with MiniLM → query vector
                    ↓
        FAISS search SFX index → top 5 matches
                    ↓
        Filter: duration < 2s, envelope_shape = "punchy"
                    ↓
        Result: camera_soft_click.wav (score: 0.82)
```
