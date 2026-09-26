# Step 4.4: Plan Sound Effects — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 4.4 |
| Name | Plan Sound Effects |
| Determinism | **Nondeterministic** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Phase 3 complete (Step 3.4) |

---

## System Context

You are a sound designer adding texture, weight, and polish to the edit.
They complement transitions, emphasize moments, and create a professional sound design
layer.

SFX serves MULTIPLE purposes — not just pairing with transitions:
- **Movement definition**: whooshes to guide attention
- **Emotional weight**: bass impacts for emphasis
- **Tension building**: risers spanning multiple clips
- **Texture**: foley/ambient establishing mood
- **Punctuation**: clicks on text appearances

---

## Task Prompt

Select and place sound effects at appropriate moments in the timeline.

### SFX toolkit:

| Type | Character |
|------|-----------|
| `whoosh` / `swish` | Air movement, directional sweep |
| `bass_impact` | Low-frequency weight, thud |
| `riser` | Ascending tension - can span MULTIPLE clips (5-10s) |
| `foley` / `ambient` | Environmental texture, atmosphere |
| `click` / `tick` | Small, precise mechanical accent |
| `reverse_cymbal` / `swell` | Transitional wash, breath between sections |

### Rules:
- There is no required number of SFX. Place a sound where
  a moment earns one and nowhere else. Do NOT add an effect to reach a
  count, and never write a rationale that justifies a sound by how many
  there are.
- **How loud a sound plays is `volume_db`, a NUMBER, and you write it.**
  The four-word ladder this line used to offer - `subtle`, `low`,
  `medium`, `prominent` - is WITHDRAWN, along with the `volume_level` key
  that named it and the engine-side levels it mapped to
  (`library/tools/sfx_level.py` keeps the record). There is no ladder, no
  default and no engine level to fall back on: **an entry naming no
  `volume_db` is DROPPED with the reason**, because a sound at a level
  nobody chose is what made the one sound in a previous run inaudible.
- Be aware of how SFX interact with speech and music - balance is a
  creative decision, not a formula
- **How a sound fades is a NUMBER when the request states one, and you
  write it.** `fade_in_seconds` / `fade_in_frames` ramp the head up
  from silence; `fade_out_seconds` / `fade_out_frames` ramp the tail
  down to it - seconds as requested, whole frames as requested, both
  forms of one fade agreeing. A sound naming none starts at level and
  stops hard (beside the one-frame de-click floor on a truncated
  sound, which always wins and says so). Fades outlasting the sound
  refuse the step.
- Layering is allowed: two or more sounds at the same position is how
  real sound design works (whoosh + bass hit, riser under a textural bed).
  Each layered sound carries its own `volume_db`
- Match the music rhythm and energy

### Sub-block anchors (exact timing inside the block):

Every entry names its block (`spine_block_position`), and a sound that
belongs to a MOMENT inside that block - a word, a beat, a frame - says
so with `anchor`. Without one the sound is placed by its envelope snap
and kept off speech; with one it lands EXACTLY where the anchor
resolves, winning over both. A whoosh timed to the word "quit" IS on
speech, so it is not shifted into a word gap.

One of (exactly one per anchor):

- `anchor: {word: "quit"}` - the word's start in this block's line
  (`occurrence: 2` for its second saying, `edge: end` for its end).
  The word must be spoken in THIS block.
- `anchor: {beat: 17}` - the 17th beat of the measured grid;
  `{bar: 4, beat: 2}` is the 2nd beat of bar 4, `{downbeat: 4}` the
  4th bar start. Bars come from the `beatgrid` view in your context.
  `grid: detected` demands the tracker-heard grid and refuses an
  estimated one.
- `anchor: {frame: 343}` - timeline frame 343. It must fall inside the
  block.
- `anchor: {event: "Laughter"}` - the start (onset) of the first
  laugh the block's own clip measured (`occurrence: 2` for its
  second, `edge: end` for a span's end). Spans come from the
  `soundevents` view in your context; a label the clip did not
  measure refuses with what it carries.

Any form takes `offset_seconds` / `offset_frames` (applied after, and
still inside the block). An anchor that names nothing placeable - a
word the block does not say, a bar past the grid, a frame outside the
block - REFUSES the step with the fix, and you re-plan; it never lands
on the block start. There is no `anchor_end` on a sound: a sound's
extent is its `duration_seconds`.

Word emphasis: the `emphasis` view in your context names, per block,
the three most emphasized spoken words as MEASURED from pitch,
loudness and duration - with the formula stated there. Land a sound on
the word the brief names, or on the block's most emphasized word; name
it through `anchor: {word}` with its `occurrence`, exactly as above.
The score is context, not an order - you still decide.

Sound events: the `soundevents` view in your context names, per
block, the non-speech sounds the block's own clip measured -
laughter, impacts, music entrances - with their timeline spans and
confidences. Land a sound on the moment the footage itself makes:
a sting on the laugh through `anchor: {event: "Laughter"}`, a hit on
the impact through `anchor: {event: "Crash cymbal"}`. Labels are the
model's own AudioSet words - a label no clip measured stays absent,
never guessed. The spans are context, not an order - you still
decide.

### Context data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

The `sfx_candidates_toon` table provides a summarized list of clips and events with the following fields:
- `segment_id`: The ID of the segment.
- `text`: Summary text for the segment.
- `action_sfx_suggested`: Pre-computed suggestion on whether SFX are needed based on audio transients.
- `music_behavior`: What the plan says the bed does under this block -
  `prominent`, `background`, `fade_in`, `fade_out` or `silent`
  (`library/tools/music_behavior.py`).
- `bed_under_it`: The bed's OWN measured loudness under this block, and
  what the plan says it is doing there. A sound placed on this block is
  heard against that. How far the bed is pushed down is decided later, at
  the mix (step 5.02, over what the bed and the speech measure), so this
  column states the bed's level and never a gain. *"unmeasured"* means
  step 2.04 recorded no measurement for the chosen track - **never** that
  the bed is silent.

Use this data to decide which sound effects to apply.

### What a level is measured against

**Speech (A1) is the mix's reference and plays at 0 dB.** Every `volume_db`
you write is against that, and `bed_under_it` says where the music sits
under the same block - so you can see both ends of the relationship a sound
has to land inside.

**Nothing in this pipeline declares how far above or below either a sound
OUGHT to sit.** No separation target is declared anywhere, and none is
supplied here. The relationship is yours to choose; these two facts are
here so that it can be chosen at all, not so a number can be read off them.

---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
sound design preferences as natural language. Read it for:

- Desired SFX density (minimal and clean vs. richly layered)
- Types of sounds to favor or avoid
- Moments that deserve special sonic emphasis

Let the brief guide how many effects you place and their character. If no
creative brief is provided, let the creative direction's energy and the
moments in the spine decide the density - there is no default count.

---


### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. Every SFX earns its place - none exists to reach a count
2. SFX timing aligns with events they accompany
3. Every sound carries a `volume_db` you chose, in dB against speech's
   0 dB reference - not a default, and not a word
4. Layered sounds at the same position each carry a distinct purpose
5. SFX don't compete with prominent music moments

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `shot_list`, `audio_spine`, `transition_plan` (optional), `subtitle_entries` (optional), `vfx_plan` (optional), `style_specification` |
| Writes | `sfx_spec` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| SFX asset not found | Skip and flag for human to source |
| SFX not serving the edit | Re-evaluate - every sound must earn its place |
