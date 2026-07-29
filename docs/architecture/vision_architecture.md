# Vision Pipeline v3 — Architecture

## Core Principle

Each dimension of video analysis has its own natural temporal structure.
The temporal index strategy matches the dimension's information density.

| Dimension | Temporal Index | Input | Boundaries Change When… |
|-----------|---------------|-------|------------------------|
| **Scene** | Event-driven (ffmpeg-guided) | Video (full clip) | Physical location or lighting shifts |
| **Camera** | Event-driven (one-shot) | Video (full clip) | Framing, mode, or stability changes |
| **Actions** | Fixed 10s windows | Video (10s clips) | N/A — uniform sampling |
| **Objects + OCR** | Entity-indexed, two-tier | Frames | Each entity appears/disappears independently |
| **Assessment** | Clip-level | Video + deterministic | N/A — one per clip |

## Why Different Temporal Indexes?

A scene that spans the entire 3-minute clip shouldn't be re-described every 10 seconds.
An object that appears for 2 seconds shouldn't be lost in a 10s window description.
Actions that change every few seconds need regular coverage.

```
Scene:   |---------- outdoor parking lot (0-145s) ----------|-- sidewalk (145-188s) --|
Camera:  |--- selfie, static (0-135s) ---|--- selfie, walking (135-188s) ---|
Actions: |win1|win2|win3|win4|win5|win6|win7|win8|win9|...|win19|  (19 × 10s)
Objects: |== creator (0-188s) ==| |= SUV (90-105s) =|  |= sign (0-15s, 130-140s) =|
```

## Execution Order

```
┌─────────────────────────────────────────────────┐
│  PREP (deterministic, fast)                     │
│  • ffprobe metadata                             │
│  • Load temporal index + transcript             │
│  • Extract frames (1 per 5s, cached)            │
│  • Extract 10s video clips (ffmpeg, cached)     │
└─────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────┐
│  GROUP A — Independent (any order, single GPU)  │
│                                                 │
│  1. Scene    (1 call, video + ffmpeg hints)      │
│  2. Camera   (1 call, video)                     │
│  3. Actions  (N calls, 10s video clips)          │
│  4. Objects  (M calls, batched frames — coarse)  │
└─────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────┐
│  GROUP B — Depends on Objects coarse             │
│                                                 │
│  5. Objects detail (targeted frames at ~1fps)    │
│     Only for transient entities (< 30s visible) │
└─────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────┐
│  GROUP C — Final                                 │
│                                                 │
│  6. Assessment (1 call + deterministic fields)   │
└─────────────────────────────────────────────────┘
```

## Model Call Counts

| Clip Length | Scene | Camera | Actions | Obj Coarse | Obj Detail | Assessment | Total |
|-------------|-------|--------|---------|------------|------------|------------|-------|
| 3.6s        | 1     | 1      | 1       | 1          | ~1         | 1          | ~6    |
| 17s         | 1     | 1      | 2       | 1          | ~1         | 1          | ~7    |
| 60s         | 1     | 1      | 6       | 1          | ~2         | 1          | ~12   |
| 120s        | 1     | 1      | 12      | 2          | ~3         | 1          | ~20   |
| 188s        | 1     | 1      | 19      | 3          | ~4         | 1          | ~29   |

## Design Decisions

1. **Video for temporal flow, frames for visual detail** — the model catches motion
   from video but reads text and identifies objects better from still frames.

2. **Objective annotation only** — describe observable facts ("frowning, arms crossed")
   not inferences ("feeling upset"). Energy, mood, and editorial judgment are derived
   downstream by the planning LLM.

3. **Strict JSON for all passes** — every model call requests a specific JSON schema
   with retry-on-parse-failure. No free-form prose in the output.

4. **Transcript as input, not output** — WhisperX provides word-level timestamped
   transcript. The vision pipeline receives it as context for the Actions pass but
   never re-transcribes.

5. **Two-tier object detection** — coarse sweep (1 frame/5s) catches everything;
   detail pass (1fps targeted) refines transient entities. The primary subject
   (visible the whole clip) doesn't need refinement.

6. **Scene boundaries from ffmpeg** — the temporal index's scene filter provides
   hard-cut boundaries. The vision model validates these and catches gradual
   transitions the detector misses.

7. **No Energy/Mood/Summary/Audio passes** — these were subjective synthesis that
   the planning LLM handles better with the full structured data.
