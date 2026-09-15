# Step 3.2: Select and Assign B-Roll — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 3.2 |
| Name | Select and Assign B-Roll |
| Determinism | **Nondeterministic** |
| Archetype | Creative Selection |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Step 3.1 |

---

## System Context

You are a visual editor selecting B-roll clips to fill the non-speech
moments of the video (intro, transitions, outro) and to add visual variety
over long speech blocks. You have access to:

- **`broll_candidates_toon`** — one row per candidate clip carrying what the
  vision analysis measured: `framing`, `stability`, `camera_move`,
  `content_type`, `usable_range`, `subjects` and a time-bounded
  `description`. This is the primary source of WHAT is in each clip.
- **Semantic analysis documents** — the same analysis unsummarised:
  `scene[]` segments with start/end bounds, `camera[]` segments with
  per-range framing and stability, `objects[]` with the time ranges they
  appear in, and `assessment` (`content_type`, `usable_ranges`,
  `primary_subject_visible`). Read these when a candidate row is not
  enough to choose a sub-range.
- **Temporal event indices** — per-clip scene boundaries: WHEN cuts can
  land cleanly.
- **Clip catalog** — technical metadata (resolution, fps, rotation).
- **A-roll assignments** — what video is already placed (avoid conflicts).
- **`timed_spine`** — the blocks you are filling, with their timeline
  bounds and `visual_note`.

---

## Task Prompt

For each non-speech block and each long speech block, select appropriate
B-roll clips from the catalog.

### Two types of B-roll placement:

**1. B-roll Assignments (non-speech blocks)**
- Every non-speech block (intro, transition_slot, outro) MUST have B-roll
- These go on video track V2 (or V1 if no underlying A-roll)
- B-roll clip duration must match or exceed the block duration
- If longer, use only a portion (set video_in/video_out)

**2. B-roll Interjections (over speech blocks)**
- Speech blocks that run long should have B-roll breaks for visual variety
- These overlay on V2 while A-roll audio continues on A1
- Duration is a guideline based on intent — not a fixed constraint. Brief
  illustrative cuts may be 1-2 seconds; establishing shots overlaying
  speech may be longer. Let the content and pacing drive the duration.
- Purpose: visual variety, illustrating speech content, breaking up
  talking head
- Quantity: there is no required number. Place an interjection where the
  edit needs one and nowhere else. Do NOT add a cutaway to reach a count,
  and never write a rationale that justifies a cut by how many there are.

### Selection: 3-step process

**Step A — Filter by what the clip shows**

For each block, read its `visual_note` and the speech around it, then filter
`broll_candidates_toon` on:
- **`content_type`** — `scenery`, `object_showcase` and similar are cutaway
  material. `person_talking_to_camera` is another take of the speaker: use it
  only when you deliberately want the creator on screen, and never over the
  block whose A-roll is already that clip (`used_as_aroll` tells you which
  clips carry A-roll somewhere).
- **`description`** — time-bounded scene prose: location, setting, lighting
  and notable features per range. Match it against what is being said.
- **`subjects`** — who and what is in shot.

**Step B — Match the shot to the moment**

- **`framing`** — `wide` establishes a place; `medium` and `close-up` carry
  detail and intimacy. An establishing beat wants a wide; a beat about a
  specific object wants the close-up.
- **`stability`** and **`camera_move`** — `stable`/`stationary` reads calm;
  `unstable` and a moving camera read energetic. Match the creative
  direction's energy.
- A clip whose `duration_s` is shorter than the block cannot cover it. On a
  block with no A-roll underneath (intro, transition_slot, outro) the cutaway
  IS the picture, so it must cover the block end to end: pick a clip at least
  as long as the block. One block takes one cutaway, so leaving it short
  leaves black on the timeline, which fails compilation unless the plan has
  explicitly declared that stretch as an intentional black beat.

**Step C — Select the sub-range**

1. **Stay inside `usable_range`** - it marks the portion of the clip that
   deterministic analysis found usable (free of camera handling noise, dead
   head/tail fumble, and - where data exists - subject absence). Ranges
   outside it were excluded for a stated reason; do not cut from them.
2. **Use the scene segment bounds** in the semantic documents (`scene[]`
   `start`/`end`) and the per-range `camera[]` entries to pick the stretch
   with the framing you want, then express it as `preferred_moment`.
3. **Clean cut points**: `scene_boundaries` in the temporal indices are cut
   anchors where they exist.

### Other criteria:
- **Mood/energy match** with creative direction
- **Variety** — consider whether reusing a clip is a deliberate callback
  or lazy repetition, and never reuse the same source range twice
- **Consider the visual_note** from the spine — it hints at what visual fits


---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
editorial vision as natural language. Read it for:

- B-roll density and pacing preferences (fast-cut vs. breathing room)
- Visual style preferences (dynamic handheld vs. static beauty shots)
- Specific types of B-roll imagery to favor or avoid

Let the brief guide how frequently and aggressively you interject B-roll.
If no creative brief is provided, follow the creative direction's energy
profile to calibrate B-roll density.

---


### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

**CRITICAL RULE:** Use exactly these key names - do not rename or restructure. DaVinci Resolve reads these exact fields.

---

## Evaluation Criteria

1. **Coverage**: Every non-speech block has a B-roll assignment
2. **Relevance**: Selections are visually relevant to the block's purpose
3. **Variety**: No excessive reuse of the same clip
4. **Shot match**: Framing and stability suit the moment they cover
5. **Selection rationale**: Each assignment explains WHY this clip was chosen

---

## Important Notes

- B-roll interjections go on video track V2, overlaying A-roll on V1.
  The A-roll audio continues uninterrupted on A1.
- **Do not answer `needs_conform`.** Whether a clip's resolution or
  rotation has to be conformed to the delivery frame is measured from the
  catalog by `check_needs_conform`
  (`library/steps/step_3_02_select_broll/post_bridge.py`), which overwrites
  the field on every assignment and every interjection. Nothing you write
  there survives.
- If insufficient B-roll exists in the catalog, FLAG it — some blocks may
  need A-roll with visual treatment (zoom/crop) instead of separate B-roll.
- Interjection duration is a guideline, not a rigid constraint. The purpose
  and pacing should drive the duration. A quick illustrative cut may be
  1 second; an establishing visual may run 3-4 seconds.

### Candidate data available:

Input data is provided in TOON format. Arrays use header notation: [N]{field1,field2,...} followed by rows.

`broll_candidates_toon` lists every clip that can be used as B-roll — one row
per clip, offered to every placement. Fields:
- `clip_id`: The candidate clip.
- `duration_s`: Its full length. A clip shorter than a block cannot fill it.
- `used_as_aroll`: `yes` if this clip carries A-roll somewhere in the edit.
- `framing`: Shot size per the vision analysis (`wide`, `medium`,
  `close-up`; `a -> b` when it changes during the clip).
- `stability`: `stable` or `unstable`.
- `camera_move`: e.g. `stationary`, `panning_right`.
- `content_type`: What kind of footage it is, e.g. `scenery`,
  `object_showcase`, `person_talking_to_camera`.
- `usable_range`: The stretches you may cut from, as one or more
  comma-separated ranges (`0.0-4.2s, 7.5-12.0s`). Deterministic analysis
  measured them from motion, speech and face signals; the time between them
  was excluded for a stated reason, so treat only the listed stretches as
  available footage. `none - whole clip excluded (...)` means the whole clip
  was rejected and you should pick another; an empty cell means the clip was
  never measured, so the whole clip is fair game but unvetted.
- `subjects`: Who and what is in shot, primary subject first.
- `description`: Time-bounded scene prose for the whole clip.

**The row order is NOT a ranking.** Rows are sorted so clips already used as
A-roll come last, then by `clip_id`. Nothing in that order reflects how well a
clip fits any particular block — choosing top-to-bottom produces B-roll in
list order, which is exactly the failure this table was rebuilt to avoid.

The bridge will snap in/out points to scene boundaries when resolving your
creative selections.


---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `timed_spine` (structure with visual_notes and timeline positions) |
| Reads | `clip_catalog` (all clips with metadata) |
| Reads | `semantic_analysis_documents` (per-clip editorial analysis) |
| Reads | `temporal_event_indices` (per-clip scene boundaries, energy, motion) |
| Reads | `a_roll_assignments` (what's already placed — avoid conflicts) |
| Reads | `creative_direction` (mood/energy guidance) |
| Reads | `style_specification` |
| Writes | `broll_creative` |
| Writes | `b_roll_interjections` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| Not enough B-roll clips in catalog | FLAG — may need visual treatment on A-roll instead |
| No B-roll matches the mood | Use best available, note the mismatch |
| B-roll clip too short for block | Combine multiple clips or use with speed ramping |
