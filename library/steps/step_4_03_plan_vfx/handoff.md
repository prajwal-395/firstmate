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
- You MUST plan at least 3-7 VFX items across the video. An empty list is a failure.
- Every A-roll talking head clip >3 seconds MUST have at least slow_zoom
- Screen shake: sparingly — max 2-3 per video
- Zoom emphasis: only for genuinely important moments
- Animation timing: 100-200ms for micro-animations, never >500ms
- Easing: Bezier curves, not linear
- Effects modify display, not timeline positions

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `vfx_candidates_toon` table provides a summarized list of clips with the following fields:
- `segment_id`: The ID of the segment.
- `text`: Summary text for the segment.
- `vfx_suggested`: Pre-computed suggestion on whether VFX are needed based on motion/pose data.

Use this data to decide which effects to apply.

---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
VFX and motion design preferences as natural language. Read it for:

- Desired motion intensity (subtle and minimal vs. aggressive and dynamic)
- Specific effect types to use or avoid
- How visually restrained or energetic the final video should feel

Let the brief guide how many effects you apply and how aggressive the
parameters are. If no creative brief is provided, match the VFX intensity
to the creative direction's energy profile.

---

## Output Format

```json
{
  "vfx_creative": [
  {
    "timeline_start": 5.0,
    "effect_type": "slow_zoom",
    "params": {
      "direction": "in",
      "zoom_percent": 7.0
    }
  }
]
}
```

**CRITICAL RULE:** Use exactly these key names - do not rename or restructure. DaVinci Resolve reads these exact fields.

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
| Writes | `enhancement_spec` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| No applicable clips | Valid — proceed with empty vfx_plan |
| Already-dynamic footage | Skip effects — footage doesn't need additional movement |
