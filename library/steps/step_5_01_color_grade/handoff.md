# Step 5.1: Colour Grade - Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 5.1 |
| Name | Colour Grade |
| Determinism | **Hybrid** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Phase 3 complete, creative direction |

---

## Task Prompt

Decide the colour correction for this cut.

The `clip_exposure` table has one row per clip, in the order the clips
first play: the measured average luma, how it was measured, how many
times the clip is placed, and the vision pass's own description of where
the shot is and how it is lit.

Each row also carries the CHROMA half - read it before the luma.
`mean_rgb` is the per-channel mean with the whole-frame balance
(`rb_all`, `gb_all`); `neutral_rgb` with `neutral_rb` / `neutral_gb` is
the balance on the DETECTED NEUTRAL region, the grey the shot carries.
The whole frame says what the picture contains, the neutral says what
the camera did: a warm face on a neutral set reads warm in `rb_all`
even when the camera is balanced. A null neutral with a
`colour_unmeasured_because` means no grey cleared the floor - balance
nothing off it.

`shot_stills` shows one representative still per graded shot, drawn from
the seconds the colour numbers describe. Open each still with your own
vision and judge the cast against the numbers: where they disagree, say
so in `grade_assessment`. `still_colour_notes` is what the still router
saw in the same stills - a second reading beside yours, never a
replacement for it.

`camera_match` is the measured proposal that brings two angles covering
one set onto one balance: per-camera neutral balances, the reference
(nearest true neutral), one slope triple per other camera and the
predicted after. The engine applies nothing on its own. To ACCEPT a
slope, copy its three numbers into a `color_correction` entry with a
`why`; to OVERRIDE, write your own. Verify the angles share the set
before accepting - two angles that never meet cost nothing.

The `cut_adjacency` table has one row per cut where the clip changes,
with the gap between the two shots in stops. These are the pairs a
viewer sees back to back.

`declared_look` is what the project's brand template declared, if
anything. Read `what_that_means_for_you` on it before you decide: where
a look is declared it is applied whatever you do, and your correction is
composed underneath it; where none is declared, your correction is the
whole grade this video ships with.

`grade_terms_legend` defines every term you may write, what neutral is
for each, and the exact order they compose in.

### What to answer

* `color_correction` - a list of per-clip corrections. Name only the
  clips you are moving; a clip you are content with is said by leaving
  it out. Each entry carries `clip_id`, the terms you are moving, and
  `why` in your own words. An entry with no `why` is dropped, and so is
  one whose terms are all neutral.
* `grade_assessment` - what you saw across the cut as a whole and why
  the corrections are what they are. Write this whether or not you
  correct anything: an empty `color_correction` is recorded as your
  DECISION that this footage needs none, which is a different fact from
  nobody having looked, and `grade_assessment` is where that decision is
  explained.

### Rules

* Correct what the cut needs. There is no target luma, no house look and
  no reference brightness in this engine, and none is being withheld from
  you - where a brand template declares an `exposure_reference` you can
  see it in `declared_look`, and where it does not, there is none.
* Say what a difference IS before you close it. A gap between two shots
  can be a fault in the lighting or it can be the content, and deciding
  it is the content and leaving it is an answer this step records.
* Every value you write is applied. There is no clamp, no bound and no
  substitute for a term you leave out.

---

## Creative Brief

When a `creative_brief` is provided in the input, read it before
deciding. It carries the captain's own words about the colour of this
channel - how saturated, how warm, how much contrast the series wants -
and that is the direction your correction should serve.

If no creative brief is provided, decide from the creative direction and
the footage in front of you.

---

### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. Every correction names the clip it moves and the reason it moves it
2. Shots that touch at a cut are reasoned about as pairs
3. A difference left alone is left alone on purpose, and says so
4. No term is written that the moment does not call for

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `a_roll_assignments`, `b_roll_assignments`, `b_roll_interjections`, `creative_direction`, `semantic_analysis_documents`, `clip_catalog`, `brand_template` |
| Writes | `color_grade_spec` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| A clip's luma could not be measured | The row says so; decide from the scene description or leave the clip alone |
| A term is written in the wrong shape | The step refuses by name and the violation comes back to you |
