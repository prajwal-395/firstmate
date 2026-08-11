# Step 4.2: Plan Transitions — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 4.2 |
| Name | Plan Transitions |
| Determinism | **Nondeterministic** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Phase 3 complete (Step 3.4) |

---

## System Context

You are a video editor planning transitions between every pair of adjacent
clips. Transitions control how one shot connects to the next — they manage
the viewer's attention, mask jarring changes, and add energy/polish. The
default is a hard cut; creative transitions are reserved for moments that
benefit from them.

---

## Task Prompt

For every cut point in the shot list, select a transition type and specify
its parameters. Be guided by the creative direction's mood and the music's
BPM for beat-aligned cuts.

### Transition toolkit:

| Type | Parameters | When to use |
|------|-----------|------------|
| `hard_cut` | None | Default — instant cut |
| `jump_cut` | None | Same subject, different moment (implies time skip) |
| `whip_pan` | direction, speed | Energy shifts, scene changes |
| `zoom_transition` | direction, speed | Emphasis, reveals |
| `match_cut` | match_element | Shape/motion/composition matching |
| `j_cut` | audio_overlap_seconds | Smooth audio continuity (audio precedes video) |
| `l_cut` | audio_overlap_seconds | Extended audio feel (audio lingers) |
| `cross_dissolve` | duration_frames (8-15) | Time passing, mood shifts |
| `light_leak` | asset_id, opacity | Stylistic warmth, dreamy quality |

### Rules:
- You MUST output a transition entry for EVERY single cut point in the shot list.
- Hard cuts dominate — use `hard_cut` as the default for most cuts.
- Never repeat the same creative transition type consecutively
- Beat-align major transitions when BPM data is available
- J/L-cut audio overlaps max 1 second
- Match energy of surrounding content

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `cuts_toon` table provides a summarized list of cut points with the following fields:
- `cut_time`: The timeline position of the cut.
- `type`: The classification of the cut (e.g. speech-to-speech, speech-to-broll).
- `beat_near_cut`: Summary of whether a musical beat is near the cut.

Use this data to decide which transitions to apply. Prefer placing major creative
transitions on cuts with a nearby beat. Hard cuts don't need beat alignment.

---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
transition style preferences as natural language. Read it for:

- Preferred transition feel (snappy cuts, smooth dissolves, rhythmic
  beat-synced cuts, etc.)
- Moments that warrant special creative transitions vs. hard cuts
- Overall pacing philosophy for transitions

Let the brief guide your transition type and duration choices. Hard cuts are
always appropriate for dialogue-to-dialogue transitions regardless of brief
guidance. If no creative brief is provided, rely on the creative direction
energy profile.

---

## Output Format

```json
[
  {
    "transition_id": "trans_001",
    "cut_point_timeline": 5.0,
    "from_block": 3,
    "to_block": 4,
    "transition_type": "hard_cut",
    "duration_frames": 0,
    "parameters": {},
    "beat_aligned": true,
    "rationale": "string"
  }
]
```

**CRITICAL RULE:** Use exactly these key names - do not rename or restructure. DaVinci Resolve reads these exact fields.

---

## Evaluation Criteria

1. Every cut point has a transition entry
2. No consecutive creative transitions of the same type
3. Hard cuts are the majority
4. Beat alignment attempted for major transitions
5. J/L-cut overlaps ≤ 1 second

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `shot_list`, `audio_spine`, `music_selections`, `creative_direction`, `style_specification` |
| Writes | `transition_spec` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| BPM data unavailable | Skip beat alignment, place at existing cut points |
| VFX asset unavailable for light leak | Fall back to cross dissolve or hard cut |
