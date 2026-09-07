# Design Question: Mood, Energy, and Interest Measurements

## What happened

The dashboard was rendering default values (`""`, `0.0`) for `mood`,
`energy`, `overall_mood`, `overall_energy`, `interest_score`, and
`summary` because semantic analysis does not measure them (AGENTS.md 7,
`vision_schema_adapter.py`).  The captain was shown numbers that were
never measured.

The dashboard has been updated to stop presenting these unmeasured
values as findings.  `mood_tags` is always empty, `interest_score`
always returns `0.0` and is no longer displayed, and the summary now
uses the speech transcript or the visual scene description instead of a
nonexistent `assessment.summary`.

## The question for the captain

Should the semantic analysis step **start** measuring mood, energy, and
interest, or should the dashboard **permanently stop** claiming them?

## Option A: Start measuring them

### What it takes

1. **Prompt update** - Add mood, energy, and interest_score fields to
   the VLM prompt in `vision_schema_adapter.py` and the schema
   definition used by step 1.03.  The local model (`gemma-4-12b-it-4bit`)
   can produce structured text fields; whether its mood/energy labels
   are reliable enough for editorial filtering is unknown and would need
   measurement.

2. **Schema plumbing** - Extend `VisionObservation` (or the v3
   equivalent) to carry the new fields, propagate them through
   `semantic_analysis_documents`, and re-enable the dashboard reads
   (currently zeroed out).

3. **Validation** - Run the updated preflight on at least two projects
   and compare the model's mood/energy tags against human labels.
   Without this, the measurement replaces one fabrication with another.

### Cost

- Prompt engineering and schema work: roughly half a day.
- Validation across projects: one to two days, depending on how many
  clips need human labels.
- Each VLM call produces more tokens (minor - a few extra fields per
  clip).

### Benefit

Editors can filter and sort clips by mood and energy in the dashboard,
which is useful for creative decisions on projects with many clips.

## Option B: Permanently stop claiming them

### What it takes

1. Remove `mood_tags` and `interest_score` from `ClipInfo` (or keep
   them as empty/zero for API stability, which is the current state).
2. Remove the "Mood / Energy" section from `footage-library.js` detail
   view (already done - it renders only when `mood_tags` is non-empty,
   which it now never is).
3. Optionally remove the `_extract_interest_score` function entirely.

### Cost

- The dashboard loses mood/energy/interest filtering.  No other
  consumer in the pipeline reads these fields.

### Benefit

- No extra VLM overhead or risk of hallucinated labels.
- The codebase stays simpler and the dashboard displays only what is
  measured.

## Current state

The dashboard is honest now: it shows nothing for mood, energy, or
interest.  Either option can be built on top of this state.
