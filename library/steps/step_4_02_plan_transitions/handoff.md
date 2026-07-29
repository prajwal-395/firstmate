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
- Hard cuts dominate — creative transitions are the minority
- Never repeat the same creative transition type consecutively
- Beat-align major transitions when BPM data is available
- J/L-cut audio overlaps max 1 second
- Match energy of surrounding content

### Beat grid calculation (for beat alignment):

If the `music_selections` include a BPM value, compute the beat grid:

```
beat_interval = 60.0 / bpm
beat_positions = [music_start + (i * beat_interval) for i in range(num_beats)]
```

A cut point is "beat-aligned" if it falls within ±50ms of a beat position.
For a 120 BPM track, beats fall every 0.5s. Prefer placing major creative
transitions on these positions. Hard cuts don't need beat alignment.

---

## Output Format

```json
[
  {
    "transition_id": "trans_001",
    "cut_point_timeline": 5.0,
    "from_entry_id": "shot_003",
    "to_entry_id": "shot_004",
    "transition_type": "hard_cut",
    "duration_frames": 0,
    "parameters": {},
    "beat_aligned": true,
    "rationale": "string"
  }
]
```

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
| Writes | `transition_plan` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| BPM data unavailable | Skip beat alignment, place at existing cut points |
| VFX asset unavailable for light leak | Fall back to cross dissolve or hard cut |
