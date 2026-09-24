# Step 3.3: Review Rough Cut — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 3.3 |
| Name | Review Rough Cut |
| Determinism | **Hybrid** |
| Archetype | Quality Gate |
| Encoding Format | Mechanical checks (Python) + Narrative review (LLM) |
| Idempotent | Yes |
| Dependencies | Step 3.1, Step 3.2 |

---

## System Context

You are a rough-cut reviewer — the last gate before the assembled edit
moves into planning and finishing phases. Your job is to catch structural
problems that would cascade into broken subtitles, misaligned audio, or
incoherent narrative BEFORE any rendering work begins.

This step has two components:
1. **Mechanical validation** (deterministic, run first)
2. **Narrative coherence review** (LLM judgment, run only if mechanical
   checks pass)

If either component fails, the rough cut is REJECTED with specific,
actionable feedback that identifies which upstream step needs to re-run.

---

## Part 1: Mechanical Validation

These checks are deterministic and non-negotiable. Any failure here is a
hard reject.

### Check 1: Duration Invariant

For every A-roll assignment, assert:

```
source_out - source_in == timeline_end - timeline_start
```

**Tolerance:** `DURATION_TOLERANCE` in
`library/tools/duration_tolerance.py` - 0.15 seconds, which steps 3.01 and
3.03 both import rather than carrying their own literal. It is there for
float rounding across segment summation (a full frame at 23.976 fps is
0.042s, and three rounded segments can stack past a frame), not for
editorial latitude. This check is run in Python; you are not asked to
perform the comparison.

**Why this matters:** If source duration > timeline duration, the clip is
silently truncated — speech gets cut off mid-sentence. If source duration
< timeline duration, black frames or frozen frames appear. Neither is
acceptable.

**If this fails:** The problem originates in Step 2.5 (Mesh Spine). The
audio spine allocated a timeline duration that doesn't match the source
material. There are two valid resolutions:

1. **Expand timeline duration** to match source duration (accept the full
   speech block). This increases the total video duration.
2. **Produce sub-segments** with specific in/out points for each kept
   portion (actual trimming). This requires identifying which words to
   cut and producing multiple `video_segments` entries instead of one
   continuous range. This is the correct approach when the source contains
   filler words, stutters, or tangents that should be removed.

**What is NOT valid:** Setting `source_out` to the full speech block end
while setting a shorter `timeline_duration` and hoping something downstream
will "trim it." If nobody performs the trim, the timeline builder will
play from `source_in` for `timeline_duration` seconds and silently chop
off the end.

### Check 2: Timeline Continuity

A-roll assignments must form a continuous sequence on V1 with no gaps or
overlaps (B-roll and transition slots fill the visual gaps on separate
tracks, but the A-roll timeline positions must be contiguous for each
speech block in the spine that has A-roll).

### Check 3: Source File Existence

Every `source_file` referenced in A-roll and B-roll assignments must
resolve to a file that exists on disk relative to the project folder.

### Check 4: No Duplicate Timeline Ranges

No two clips on the same track should occupy the same timeline range.

---

## Part 2: Narrative Coherence Review

These checks require judgment. Run them ONLY after mechanical validation
passes.

### What you are shown

`roughcut_window_frames` maps one frame strip per window the cut PLAYS -
every A-roll segment and every B-roll overlay, in timeline order - to
the directory holding them. The prose in this context says what HAPPENS
in a clip; only the strips say what it LOOKS like.

You have a shell and your own file tools. OPEN the strip for any window
you are judging before you decide it - in particular before calling a
cut smooth or a passage visually covered. An A-roll row and a B-roll
row at the same `timeline_start` are the same moment of the cut: the
B-roll is what the viewer SEES and the A-roll is what they HEAR.

A window the map names as having no strip is one you cannot see: judge
it from the prose and say so. If the map is absent entirely, no strips
were drawn and the whole review proceeds from the prose.

### Check 5: Read the Actual Script

**It is already built, and you must not rebuild it.** `actual_script` in
your context is what the viewer HEARS, assembled by the deterministic half
(`build_actual_script` in `library/steps/step_3_03_review_rough_cut/step.py`)
from the spine's own measured word timings, restricted to the source range
each A-roll segment actually plays, in timeline order:

- `actual_script.blocks[]` - one entry per played A-roll segment, carrying
  `spine_block_position`, `block_type`, `clip_id`, `timeline_start`,
  `source_in`, `source_out`, the `text` and its `word_count`.
- `actual_script.full_text` - those texts joined, in timeline order. This is
  the monologue.
- `actual_script.unvoiced[]` - every played range that produced no words,
  with the reason (no measured word timings on the block, no words inside
  the range, or a segment naming no source range at all). **A range here is
  audio the viewer hears that nothing transcribed.** It is named rather than
  skipped precisely so you can weigh it; do not read a gap in the joined
  text as a gap in the audio.

Reading it is the point. Joining words inside a range is clerical work the
script does exactly; judging whether the result is a coherent piece of
speech is not, and that is Checks 6 to 9. Everything else in Part 2
evaluates THIS script, NOT the intended text from the speech sequence -
and NOT a reconstruction of your own, which would be a second answer to a
question that already has one.

### Cuts with no speech

When `actual_script.full_text` is empty, the cut has no monologue and
Checks 6 and 7 do not run - there is no sentence to complete and no
pair of blocks to rate. Say so in one line ("no speech: checks 6-7
skipped") and judge what is there instead:

- A picture-led cut (A-roll placed, `unvoiced` naming every range) is
  EXPECTED to read this way: the ranges say why each one produced no
  words. Do not fail a picture block for having no transcript.
- A music-only cut (no A-roll at all) is judged on Check 8 alone: does
  the picture and the music deliver the creative direction's arc.
  Check 9 asks after verbatim words, which a speechless cut has none
  of - say which key moments the picture carries instead, and which it
  drops, rather than failing the absence of words.

An empty script on a cut the spine planned AS speech is different: a
speech block that produced no words is a broken alignment, and that
fails here, not as a narrative judgement but as one.

### Check 6: Sentence Completion

Every A-roll block must end on a complete thought. Specifically:

- The last word in the block should be the end of a sentence, clause, or
  intentional trailing-off.
- If a block ends mid-word or mid-clause (e.g., "and the reason i'm
  recording today is because i—"), it **FAILS**.
- Exception: intentional cliffhangers or hook teasers that are resolved
  later in the sequence.

### Check 7: Narrative Flow

Read the reconstructed script as a continuous monologue. For each
consecutive pair of blocks, evaluate:

- **Logical connection:** Does block N+1 follow from block N? Is there a
  causal, temporal, or thematic link?
- **No dangling references:** If block N mentions a concept, name, or
  event, has it been introduced (before or in this block)?
- **No non-sequiturs:** Abrupt topic changes without motivation are a
  failure.

Rate each transition as: `smooth` | `acceptable` | `jarring` | `broken`.
Any `broken` transition is a rejection. A high density of `jarring`
transitions may indicate a structural problem - evaluate in context.

### Check 8: Emotional Arc Integrity

Compare the reconstructed script's emotional trajectory against the
creative direction's `energy_arc` and `emotional_landscape`:

- Does the edit deliver the emotional journey the creative direction
  described?
- Are the key emotional beats landing?
- Is the arc coherent, or does it wander without purpose?

If any major arc element that the creative direction called for is missing
or incoherent, flag it.

### Check 9: Key Moments Preservation

Check the creative direction's `key_moments` list. For each key moment:

- Is the verbatim text (or its core meaning) present in the reconstructed
  script?
- If a key moment was trimmed or cut, is its absence justified?

Flag any key moment that was intended but is not present in the final cut.

---

## `render_qa_findings` - measurements of the LAST render

When this project has been rendered before, `render_qa_findings` carries
what step 6.02 measured on that finished file. **They are not checks on the
rough cut in front of you, and they are not part of the mechanical gate.**

They are here because several of them are consequences of decisions made at
or before this step - which passage was anchored where, and how long each
block holds. Nothing else in the pipeline reads them.

**Do NOT reject the rough cut because of a finding here.** Reject only on
the mechanical checks and the narrative criteria above. A finding is
context: say what it implies for this cut if it implies anything, and
otherwise leave it. An advisory note turned into a hard rejection is a
silent problem converted into a loud wrong one.

What each field holds:

- `metric` - the check's name in `exports/qa_report.json`.
- `severity` - `error | warning | info`: how loud the check itself is.
- `verdict` - `failing` = the check did not pass; `advisory` = it passed
  the gate and still missed its own target, deliberately not gating;
  `clean` = nothing to say.
- `owned_by` - the pipeline step whose decision the finding is about, as a
  DAG node id. An empty owner means no reader is declared for it.
- `detail` - the check's own sentence, verbatim.
- `readings` - one sentence per metric that has something to say, saying
  what a reader does with it. A clean check has no reading because there is
  nothing to read; its row is still in the table.
- `source` / `source_detail` - where the findings were read from.
- `counts` - how many are failing, advisory and clean.
- `unrouted_metrics` - findings for which no reader is declared anywhere.

Every row travels, clean ones included. Nothing was filtered for you.

---


### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. **Zero false passes**: If the mechanical checks fail, the step MUST
   reject. No "it's close enough" — the invariants exist for a reason.
2. **Actionable feedback**: Every rejection must name the specific upstream
   step that needs to change and what the change should be.
3. **The script that was judged is `actual_script`**, not the
   speech_sequence's intended text and not a reconstruction written in the
   review. A finding that quotes words outside `actual_script.full_text` is
   a finding about something the viewer does not hear.
4. **Arc sensitivity**: The review should understand that a short video
   doesn't need every arc element to be elaborate — but it does need the
   key emotional beats to land.

---

## Parameters

| Parameter | Value |
|-----------|-------|
| Duration tolerance | `library/tools/duration_tolerance.DURATION_TOLERANCE` (0.15s) |
| Key moments coverage target | 100% (all must be present or justified) |

---

## Important Notes

- **This step is a GATE, not a fixer.** It does not modify the manifest.
  It either passes it through or rejects it with instructions for upstream
  steps to re-run.
- **The reconstructed script is the truth.** What the speech_sequence
  intended is irrelevant if the timeline doesn't have room for it.
- **Trimming is not this step's job.** If a block needs trimming, this
  step should reject and tell step 2.5 (mesh spine) to either accept
  the full duration or produce sub-segments.
- **This step should be fast.** It reads existing data and evaluates it.
  No media processing, no rendering, no external API calls.
- **Run mechanical checks first.** If they fail, skip the narrative review
  entirely — the data is structurally invalid and narrative evaluation
  would be based on wrong assumptions.

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `a_roll_assignments` |
| Reads | `b_roll_assignments` |
| Reads | `audio_spine` |
| Reads | `speech_sequence` |
| Reads | `actual_script` (Check 5's reconstruction, built by this step's own deterministic half) |
| Reads | `temporal_index` |
| Reads | `creative_direction` |
| Reads | `roughcut_window_frames` (frame strips of the placed windows) |
| Writes | `rough_cut_review` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| Duration invariant violation | REJECT → re-run step 2.5 with constraint: timeline_duration must equal source_duration, or produce sub-segments |
| Sentence cut mid-thought | REJECT → re-run step 2.5 to adjust block boundaries, or re-run step 2.2 to select tighter source ranges |
| Broken narrative flow | REJECT → re-run step 2.2 to reorder or replace speech blocks |
| Missing key moments | FLAG (warning, not hard reject) → document for human review |
| All checks pass | PASS → proceed to Phase 4 (planning) |
