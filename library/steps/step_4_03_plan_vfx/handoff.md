# Step 4.3: Plan Visual Effects — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 4.3 |
| Name | Plan Visual Effects |
| Determinism | **Nondeterministic** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Phase 3 complete (Step 3.4) |

---

## System Context

You are a motion designer adding subtle visual effects to keep the frame
alive. Static holds kill engagement in shortform content — "the frame
should always be moving." These effects add dynamism without changing the
content or clip placement. They're felt more than seen.

---

## Task Prompt

For each clip in the shot list, decide which visual effects (if any) to
apply. Focus on talking head clips that need subtle movement.

### Effect toolkit:

| Type | Parameters | When to use |
|------|-----------|------------|
| `slow_zoom` | direction (in/out), zoom_percent (5-10%) | Nearly always on talking head clips — makes static shots feel alive |
| `screen_shake` | intensity_px (2-3), duration_frames (3-4), trigger_reason | Emphasis moments — use sparingly (max 2-3 per video) |
| `zoom_emphasis` | zoom_percent (5%), trigger_time, duration_ms (150-300) | Key words/moments — punctuates important statements |
| `cut_in` | scale_factor (1.2-1.4) | Tighter framing on same shot — simulates multi-cam |
| `cut_out` | scale_factor (0.85-0.95) | Wider framing — creates visual variety |

### Rules:
- Every A-roll talking head clip >3 seconds MUST have at least slow_zoom
- Screen shake: sparingly — max 2-3 per video
- Zoom emphasis: only for genuinely important moments
- Animation timing: 100-200ms for micro-animations, never >500ms
- Easing: Bezier curves, not linear
- Effects modify display, not timeline positions

### Precision tool: Motion detection (embedded)

To determine if a clip is already dynamic (and doesn't need added movement),
use OpenCV optical flow:

```python
import cv2
import numpy as np

cap = cv2.VideoCapture("clip.mp4")
ret, prev = cap.read()
prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
motion_scores = []

while True:
    ret, frame = cap.read()
    if not ret:
        break
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    magnitude = np.sqrt(flow[..., 0]**2 + flow[..., 1]**2)
    motion_scores.append(np.mean(magnitude))
    prev_gray = gray

avg_motion = np.mean(motion_scores)
# avg_motion < 1.0 → static (needs slow_zoom)
# avg_motion > 3.0 → dynamic (skip effects)
```

### Precision tool: Face/pose landmarks (embedded)

To find exact frames of smiles, head turns, or hand gestures for
`zoom_emphasis` timing:

```python
import mediapipe as mp

mp_face = mp.solutions.face_mesh
face_mesh = mp_face.FaceMesh(static_image_mode=False)
# Process frames to find expression changes (smile onset, head turn)
# Use the frame timestamp as the trigger_time for zoom_emphasis
```

---

## Output Format

```json
[
  {
    "vfx_id": "vfx_001",
    "target_entry_id": "shot_005",
    "timeline_start": 5.0,
    "timeline_end": 13.5,
    "effect_type": "slow_zoom",
    "parameters": {
      "direction": "in",
      "zoom_percent": 7.0
    }
  }
]
```

---

## Evaluation Criteria

1. Every talking head clip >3s has at least slow_zoom
2. Screen shake used ≤3 times
3. Parameters within style spec ranges
4. No effect changes clip in/out points or timeline position

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `shot_list`, `style_specification` |
| Writes | `vfx_plan` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| No applicable clips | Valid — proceed with empty vfx_plan |
| Already-dynamic footage | Skip effects — footage doesn't need additional movement |
