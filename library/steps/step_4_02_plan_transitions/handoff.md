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
| `fade_to_black` | duration_feel, duration_frames, or duration_seconds | Dip to black. Chapter breaks, time passing |
| `zoom_blur` | duration_feel, duration_frames, or duration_seconds | Crash zoom. Energy spikes, punches into a line |
| `defocus` | duration_feel, duration_frames, or duration_seconds | Blur through the cut. Mood shifts, soft scene changes |
| `flash` | duration_feel, duration_frames, or duration_seconds | Brightness flash. Beat hits, hard energy changes |
| `cross_dissolve` | duration_feel, duration_frames, or duration_seconds | True dissolve, mixed by Resolve itself at the cut. Time passing, soft scene changes |
| `slide` | duration_feel, duration_frames, or duration_seconds | Incoming clip slides in. Playful moves, lists, reveals |
| `smooth_cut` | duration_feel, duration_frames, or duration_seconds | Morph across the cut. Invisible joins inside one take |
| `spin` | duration_feel, duration_frames, or duration_seconds | Spin across the cut. High-energy turns, drops |

`duration_feel` is one of `instant`, `quick`, `medium`, `slow`.

E3 - numbers when the request states them, feel words otherwise: when
the request names a hold in frames ("a 12-frame dissolve"), state it
with `duration_frames`; when it names seconds ("a 1-second dip"), state
`duration_seconds`. Keep the requester's value in the resolved plan; the
build also receives its nearest whole-frame representation. If frames
and seconds are both stated, they must agree within half a frame. A
stated number beside a disagreeing feel word refuses. A feel word alone
renders to 0/6/10/15 frames; a request that names no hold gets the feel
word, never a number you invent.

The last four are NATIVE transitions: Resolve draws them itself at the
V1 cut (they are the only transitions that mix two pictures, which no
per-clip effect can do), and each is judged by what Resolve hands back.
The names are exactly what measured as granted on Resolve 21.1 - a
dissolve by any other spelling is still this dissolve (`dissolve` means
`cross_dissolve`).

### The end of the piece:

A transition OUT of the final shot - a dip to black under the last
line, a dissolve trailing into nothing - is its own entry addressed
with `cut_point_position: "end"` (the literal word, never a block
position). Only two types can sit there: `fade_to_black` (drawn on
the last clip alone) and `cross_dissolve` (placed by Resolve itself
at the clip's end). Anything else at `"end"` drops with its reason.
The `cuts_toon` table carries one row addressed `"end"` saying whether
the last block puts a V1 clip on the timeline to fade out of - read it
before planning the dip. No J/L audio offset at the end: a J/L cut
trims the speech row between two blocks, and the end has no incoming
side.

### Not available — do not use:

`wipe` needs the outgoing and incoming clips mixed in one composition
the native route does not draw. `fade_to_black` is a dip to black, not
a dissolve — it is not a substitute, so do not ask for one expecting
the other.

`whip_pan`, `dip`/`dip_to_color`, `push` and the ofx `Blur Dissolve`
REFUSE the step by name: Resolve answered them empty when measured, so
a whip that shipped as a hard cut would be a plan the picture disobeyed
without saying so. Nothing is substituted on your behalf - re-plan the
cut with a granted type, a drawn Fusion transition, or a `hard_cut`
stated in the plan. The one exception is stated, not invented: a
`fallback_type` on the entry names the granted native transition to
ship instead, and only then is it shipped.

`light_leak` has no implementation.

### J/L cuts (audio offsets):

A `j_cut` crosses the join with the EAR first: the outgoing speech
ends early and the incoming room arrives under the outgoing picture. An
`l_cut` LINGERS: the incoming speech starts late and the outgoing room
lingers under the incoming picture. The picture cut stays on the V1
boundary - only the audio edit point moves, and the gap it opens is
filled with measured room tone, never silence.

A J/L cut is its own entry at the boundary (`cut_point_position`, the
INCOMING block), beside any picture entry there - one join may carry
one picture decoration AND one audio offset, never two offsets.
The offset is measured from the V1 boundary FRAME (where the eye cuts
and the incoming speech row starts), in whole frames: a `lead_seconds`
that quantizes onto the boundary is a straight cut and refuses.

| Type | Parameters | When to use |
|------|-----------|------------|
| `j_cut` | `lead_seconds` / `lead_frames` and/or `anchor` | The next moment starts audible before it is visible. Doc dialogue, reactions |
| `l_cut` | `lag_seconds` / `lag_frames` and/or `anchor` | The last moment rings on after the picture has moved on. Pauses that breathe |

State the offset with `lead_seconds` (`j_cut`) or `lag_seconds`
(`l_cut`) - your own number in seconds - or, when the request states
frames ("20 frames before the picture cut"), with `lead_frames` /
`lag_frames` - your own whole number - or with `anchor`, or any of
them agreeing: a word in the block the audio leaves (`anchor: {word:
"yeah"}` - the outgoing block for a J-cut, the incoming block for an
L-cut), a beat/downbeat/bar on the measured grid, or a frame, each
with optional `offset_seconds` / `offset_frames`. Seconds and frames
that disagree past half a frame refuse the step. The offset MUST sit
inside the pause AT the boundary: the J lead has to fit between the
outgoing block's last word and the boundary, the L lag between the
boundary and the incoming block's first word - trimming speech refuses
the step, so pick a join that breathes. Both sides of the join must
reach V1. No `duration_feel`, no `fallback_type`, no `anchor_end` on
an audio cut - all three refuse.

### Rules:
- You MUST output a transition entry for EVERY single cut point in the shot list.
- You MAY output at most one end-of-piece entry addressed `"end"` (see above).
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
grid); the section form lands on a musical span's first downbeat
(`{section: "chorus"}` - labels come from the `sectiongrid` view, with
`occurrence` for the nth span and `edge: end` for its end); the motion
forms land on the outgoing block's measured action (`{motion_peak: 1}`
its first apex, `{action_onset: 1}` its first onset - peaks come from
the `motion` view in your context, `occurrence` for the nth of that
kind, never `edge`: a peak is a point); the event form lands on the
outgoing block's measured sound (`{event: "Laughter"}` its first
laugh - spans come from the `soundevents` view in your context, with
`occurrence` for the nth span and `edge: end` for its end, default
start is the onset); the frame
form names a timeline frame inside the outgoing
block. Any form takes `offset_seconds` / `offset_frames`. An anchor
that names nothing placeable REFUSES the step with the fix, and you
re-plan. A cut is a point, so there is no `anchor_end` on it.

Word emphasis: the `emphasis` view in your context names, per block,
the three most emphasized spoken words as MEASURED from pitch,
loudness and duration - with the formula stated there. Cut after the
word the brief names, or after an emphasized word, through
`anchor: {word, edge: end}` exactly as above. The score is context,
not an order - you still decide.

Cut on action: the `motion` view in your context names, per block,
the measured motion of the picture it plays - the clip's dominant
direction and kind, and the action onsets and apexes inside the
block with their timeline seconds - and the `outgoing_motion` /
`incoming_motion` columns of `cuts_toon` put both sides of every cut
side by side. Cut ON the action through `anchor: {action_onset: 1}`
(the outgoing block's first onset) or `{motion_peak: 1}` (its first
apex): the cut lands exactly where the anchor resolves, winning over
the word-end and beat-snap. Match direction across the cut where the
columns agree (a pan right into a pan right reads continuous) or
contrast them deliberately (motion into stillness is itself a
gesture) - either way the measurement is context, not an order, and
you still decide. A motion anchor on a block whose motion is
unmeasured refuses with the fix, and you re-plan; it never falls
back to the block boundary.

Cut on the sound: the `soundevents` view in your context names, per
block, the non-speech sounds the block's own clip measured -
laughter, impacts, music entrances - with their timeline spans and
confidences. Cut ON the laugh through `anchor: {event: "Laughter"}`
(the outgoing block's first laugh; `occurrence: 2` for its second,
`edge: end` for a span's end): the cut lands exactly where the
anchor resolves. Labels are the model's own AudioSet words - a label
no clip measured stays absent, never guessed. An event anchor on a
block whose sound is unmeasured refuses with the fix, and you
re-plan; it never falls back to the block boundary.

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `cuts_toon` table provides a summarized list of cut points with the following fields:
- `cut_point_position`: The exact position identifier of the incoming block. Use this value for `cut_point_position` in your response.
- `cut_time`: The timeline position of the cut.
- `type`: The classification of the cut (e.g. speech-to-speech, speech-to-broll).
- `beat_near_cut`: Summary of whether a musical beat is near the cut.
- `outgoing_footage`: The mood and tags of the clip ending at the cut.
- `incoming_footage`: The mood and tags of the clip starting at the cut.

Six more columns are DERIVED, two from the spine, two from step 3.03's
review, and two from the temporal motion measurement. Each says what
it measures; none of them says what to conclude.

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
- `outgoing_motion` / `incoming_motion`: **The measured motion** of the
  picture either side of the cut - each clip's dominant motion kind
  and direction (`pan_right, right`), with how many action peaks it
  carries, or `unmeasured` where the clip measured nothing. Read them
  to cut on action and to match direction across the cut (above);
  `unmeasured` is the absence of a measurement and NOT stillness.

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
| A Fusion type outside the toolkit is requested | The bridge downgrades it to `hard_cut` and records `downgrade_reason` on the entry |
| A measured-refused native type (`whip_pan`, `dip`, `push`, ofx blur) is requested | The bridge REFUSES the step by name - re-plan with a granted type or a stated `fallback_type` |
