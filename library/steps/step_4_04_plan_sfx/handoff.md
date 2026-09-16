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
- Layering is allowed: two or more sounds at the same position is how
  real sound design works (whoosh + bass hit, riser under a textural bed).
  Each layered sound carries its own `volume_db`
- Match the music rhythm and energy

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
