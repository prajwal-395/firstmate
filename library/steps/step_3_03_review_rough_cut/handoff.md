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

**Tolerance:** 0.1 seconds (to account for float rounding).

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

### Check 5: Reconstruct the Actual Script

For each A-roll assignment, determine what speech actually plays:

- The source audio from `source_in` to `source_in + timeline_duration` is
  what the viewer hears.
- Look up the temporal index for the corresponding clip_id to find the
  word-level transcript within that source range.
- Concatenate all blocks in timeline order to produce the **actual script**
  — the verbatim text the viewer will hear.

**This is the most important step.** Everything else in Part 2 evaluates
this reconstructed script, NOT the intended text from the speech sequence.

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

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. **Zero false passes**: If the mechanical checks fail, the step MUST
   reject. No "it's close enough" — the invariants exist for a reason.
2. **Actionable feedback**: Every rejection must name the specific upstream
   step that needs to change and what the change should be.
3. **Script reconstruction accuracy**: The reconstructed script must be
   derived from actual temporal index data, not from the speech_sequence's
   intended text.
4. **Arc sensitivity**: The review should understand that shortform video
   doesn't need every arc element to be elaborate — but it does need the
   key emotional beats to land.

---

## Parameters

| Parameter | Value |
|-----------|-------|
| Duration tolerance | 0.1 seconds |
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
| Reads | `temporal_index` |
| Reads | `creative_direction` |
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
