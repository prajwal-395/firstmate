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
the viewer's attention, mask jarring changes, and add energy/polish.

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
- A hard cut is the absence of decoration; a creative transition adds it.
  Let the creative direction, the footage and the music guide the mix.
- Beat-align major transitions when BPM data is available
- Match energy of surrounding content

### Sub-block anchors (the exact point of the cut):

Every entry names its cut (`cut_point_position`, the INCOMING block),
and a cut that belongs on a MOMENT inside the outgoing block - the end
of a word, a downbeat - says so with `anchor`. Without one the cut is
placed by the word-end and beat-snap below; with one it lands EXACTLY
where the anchor resolves. The word form addresses the OUTGOING
block's line (`anchor: {word: "quit", edge: end}` cuts right after
"quit"); the beat forms address the measured grid (`{beat: 17}`,
`{bar: 4, beat: 2}`, `{downbeat: 4}` - bars come from the `beatgrid`
view in your context, and `grid: detected` demands the tracker-heard
grid); the frame form names a timeline frame inside the outgoing
block. Any form takes `offset_seconds` / `offset_frames`. An anchor
that names nothing placeable REFUSES the step with the fix, and you
re-plan. A cut is a point, so there is no `anchor_end` on it.

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `cuts_toon` table provides a summarized list of cut points with the following fields:
- `cut_point_position`: The exact position identifier of the incoming block. Use this value for `cut_point_position` in your response.
- `cut_time`: The timeline position of the cut.
- `type`: The classification of the cut (e.g. speech-to-speech, speech-to-broll).
- `beat_near_cut`: Summary of whether a musical beat is near the cut.
- `outgoing_footage`: The mood and tags of the clip ending at the cut.
- `incoming_footage`: The mood and tags of the clip starting at the cut.

Four more columns are DERIVED, two from the spine and two from step 3.03's
review. Each says what it measures; none of them says what to conclude.

- `can_carry_drawn_transition`: Whether a DRAWN transition
  (`fade_to_black`, `zoom_blur`, `defocus`, `flash`) can be BUILT at this
  cut - `yes` or `no`. A drawn transition is a tail on the outgoing V1 clip
  and a head on the next one, so it needs a V1 clip ending at the cut with
  another V1 clip after it. **A cut reading `no` fails compilation if a
  drawn transition is planned on it.** The undrawn types (`hard_cut`,
  `jump_cut`, `match_cut`) place nothing and are unaffected: they are
  buildable at every cut.
- `carry_basis`: Which track the blocks either side of this cut play on,
  and therefore where the effect's two halves land. A speech, hook or
  bookend block puts a clip on V1; every other block - a `transition_slot`
  above all - is covered by B-roll, and every B-roll placement goes on V2.
  *"draws through this cut"* means the tail and the head land on the two
  pictures either side of it. *"the head on the next V1 clip after the
  cutaway"* means the incoming picture is B-roll on V2, so the effect
  brackets the cutaway instead of drawing through the cut - it is
  buildable, and it is not the same gesture. *"ends the V1 track"* means
  there is no V1 clip left to draw the head half onto.
- `narrative_verdict`: **The rough-cut review's judgement**, not a
  measurement - one of `smooth`, `acceptable`, `jarring`, `broken`, written
  by step 3.03 after reconstructing what the viewer actually hears.
  `unjudged` means the review named no verdict for this cut; it is the
  ABSENCE of a judgement and NOT the verdict `smooth`. A word outside those
  four is the review's own and is shown as written, with `unrecognised`
  beside it. This is a reading of the SPEECH either side of the cut, not of
  the picture: `jarring` does not mean a transition belongs here, and
  `smooth` does not mean one does not.
- `verdict_note`: **The review's own sentence** about this cut, verbatim,
  or empty when it gave none. Whitespace is collapsed so the row survives
  the table; nothing else is rewritten.

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


### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

**CRITICAL RULE:** Use exactly these key names - do not rename or restructure. DaVinci Resolve reads these exact fields.

---

## Evaluation Criteria

1. Every cut point has a transition entry
2. Transition choices serve the creative direction, pacing and energy
3. Beat alignment attempted for major transitions

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
