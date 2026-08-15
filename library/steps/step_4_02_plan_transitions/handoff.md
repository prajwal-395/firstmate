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

This is the complete list. Every type here is drawn by the renderer; the
list is checked against `library/tools/transition_vocabulary.py` in CI, so
nothing is offered that the finished video cannot show.

| Type | Parameters | When to use |
|------|-----------|------------|
| `hard_cut` | None | Default — instant cut |
| `jump_cut` | None | Same subject, different moment (implies time skip) |
| `match_cut` | None | Shape/motion/composition matching across the cut |
| `fade_to_black` | duration_feel | Dip to black. Chapter breaks, time passing |
| `zoom_blur` | duration_feel | Crash zoom. Energy spikes, punches into a line |
| `defocus` | duration_feel | Blur through the cut. Mood shifts, soft scene changes |
| `flash` | duration_feel | Brightness flash. Beat hits, hard energy changes |

`duration_feel` is one of `instant`, `quick`, `medium`, `slow`.

### Not available — do not use:

`cross_dissolve`, `dissolve` and `wipe` need the outgoing and incoming
clips mixed in one composition. The renderer draws each clip's effects on
that clip alone, so it cannot mix two. `fade_to_black` is a dip to black,
not a dissolve — it is not a substitute, so do not ask for one expecting
the other.

`whip_pan`, `light_leak`, `j_cut` and `l_cut` have no implementation.
J/L cuts are audio edits and are handled by the audio pass, not here.

### Rules:
- You MUST output a transition entry for EVERY single cut point in the shot list.
- Hard cuts dominate — use `hard_cut` as the default for most cuts.
- Never repeat the same creative transition type consecutively
- Beat-align major transitions when BPM data is available
- Match energy of surrounding content

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `cuts_toon` table provides a summarized list of cut points with the following fields:
- `cut_point_position`: The exact position identifier of the incoming block. Use this value for `cut_point_position` in your response.
- `cut_time`: The timeline position of the cut.
- `type`: The classification of the cut (e.g. speech-to-speech, speech-to-broll).
- `beat_near_cut`: Summary of whether a musical beat is near the cut.
- `outgoing_footage`: The mood and tags of the clip ending at the cut.
- `incoming_footage`: The mood and tags of the clip starting at the cut.

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

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

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
| A type outside the toolkit is requested | The bridge downgrades it to `hard_cut` and records `downgrade_reason` on the entry |
