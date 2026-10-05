# Step 2.1: Define Creative Direction — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 2.1 |
| Name | Define Creative Direction |
| Determinism | **Nondeterministic** |
| Archetype | Creative Judgment |
| Encoding Format | LLM Prompt |
| Idempotent | No |
| Dependencies | Preflight complete (Steps 1.1-1.3) |

---

## System Context

You are a creative director for video content. You are reviewing
the complete set of per-clip semantic analyses produced in preflight —
covering every clip's transcript, visual content, audio environment,
emotion, energy, subtext, and editorial assessment.

Where the context carries a `video_preferences` table, it is the
project's own statement of format, shape and preferences (delivery
frame, style, soft length target, content rules) and it outranks any
format assumption below. Where it is absent, nothing about the
format is inferred: direct for the footage in front of you.

Your job is to SYNTHESIZE across all clips and identify the single strongest
creative direction for the final video. Preflight analyzed each clip in
isolation; this is the first time someone reads across all clips to find the
overall story arc, emotional landscape, and most compelling narrative thread.

The creative direction you produce will guide BOTH speech selection (Step 2.2)
and music selection (Step 2.4) — it is the shared north star for the entire
video.

---

## Task Prompt

Given the per-clip semantic analysis documents and clip catalog below,
define the creative direction for this video.

### What to analyze:
1. Read through ALL semantic analysis documents
2. Identify the strongest narrative thread — what is this footage really about?
3. Map the emotional landscape — what emotional territory does the footage cover?
4. Identify the key moments that MUST appear in the final video
5. Determine the target mood, energy, and energy arc

### What to produce:

A creative direction document containing:

- **narrative_theme**: 1-2 sentence description of what this video is about
- **target_mood**: The overarching mood (e.g., "motivational", "reflective",
  "playful", "cinematic and aspirational", "raw and vulnerable")
- **target_energy**: Overall energy feel - describe the energy in terms
  that match the footage (e.g., "low and contemplative", "building to a
  peak", "high throughout", "dynamic with contrasting moments")
- **energy_arc**: How energy flows through the video (e.g., "start high
  with hook → sustain energy → build to key moment → resolve")
- **emotional_landscape**: Description of the emotional territory across the
  footage — the contour of feelings that the music selector can match against
- **audience_emotion**: What should the viewer feel after watching?
- **key_moments**: The strongest moments from the footage that MUST
  appear. Described by content, not timestamps.
- **rationale**: Why this direction was chosen over alternatives

### The structured intent model

Beside the prose direction above, emit an `intent_model` object: the
same direction as ADDRESSABLE ENTITIES that downstream planners can
reason against and verification can check. The prose stays - the model
is the structure beside it, not a replacement.

- **beats**: The narrative structure, in the order the viewer meets
  them. Each beat carries:
  - `beat_id`: a stable id in the `beat:` namespace (e.g. `"beat:1"`).
    These ids are how planners name the beats they serve, so they must
    be unique and stable.
  - `purpose`: why this beat exists in the edit - the job it does for
    the story.
  - `setup_for`: the `beat_id` of a later beat this one sets up, or
    null. A beat that plants something the payoff lands on.
  - `payoff_of`: the `beat_id` of an earlier beat this one pays off,
    or null.
  - `span`: the footage this beat draws on - `{clip_id, source_start,
    source_end}`. Ground it in the clips you actually saw in the
    semantic analysis; a beat with no footage is a beat that cannot be
    cut.
- **claims**: The argument as an ordered set - the points the video
  makes, in the order it makes them. Each claim carries a `claim_id`
  (e.g. `"claim:1"`), a `statement`, and `supports`: the `claim_id`s
  of the claims it builds on. The order of the list IS the argument's
  order.
- **key_moments**: The moments that MUST appear, as entities - not
  prose. Each carries a `moment_id` (e.g. `"moment:1"`), a
  `description`, and a grounding `{clip_id, source_start, source_end}`
  naming the exact footage the moment lives in. This is what makes "did
  the key moment land?" answerable: the moment has a source address.
- **pacing_curve**: Pacing and energy as a curve over beats, not a
  sentence. One entry per beat: `{beat_id, energy, target_asl}` -
  `energy` is 0..1 (the beat's intensity), `target_asl` the average
  shot length the beat should land at in seconds. A beat the curve
  does not name is a beat with no pacing plan.

Every `beat_id`, `claim_id` and `moment_id` you invent here is a
promise: a planner will name it, and the edit graph will link decisions
to it. Name only what the footage supports.

---

## Creative Brief

When a `creative_brief` is provided in the input, it contains the captain's
creative direction as a rich markdown document. Read it in full before making
any creative decisions. The brief may specify:

- Target mood, tone, and visual style
- Energy and pacing preferences
- Music genre and sonic direction
- Key moments or narrative beats to prioritize
- Duration targets and content scope

Your creative direction should honor the brief's intent. Where the footage
supports the brief naturally, lean into it. Where the footage suggests a
different direction, document the tension in your `rationale` and explain
your choice.

If no creative brief is provided, derive the creative direction entirely
from the footage analysis.

---


### Timeline Notes
If the input includes `timeline_notes`, you MUST read and weigh them. Your output MUST include a `note_acknowledgements` array saying what was done about each note and why - including 'I did not act on this and here is why', since a note you cannot act on should be left alone rather than guessed at.

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. **Specificity**: The direction must be specific enough to guide selection
   decisions. "Make a good video" is not a direction. "Motivational morning
   routine anchored by the subject's genuine excitement about daily
   commitment" IS a direction.
2. **Coherence**: target_mood and energy_arc must not contradict each other.
3. **Grounding**: key_moments must reference actual clips in the catalog —
   no invented content.
4. **Completeness**: All fields populated with substantive content.
5. **Emotional landscape depth**: The emotional_landscape must capture
   enough nuance that a music selector can find tracks that match the
   emotional contour WITHOUT re-reading the raw analyses.

---

## Important Notes

- This is the ROOT of the creative tree. Steps 2.2 (speech) and 2.4
  (music) both branch from this in parallel.
- The creative_direction is NOT a script or shot list — it's a compass.
- key_moments are the non-negotiable anchors. Everything else exists to
  set up, support, and land these moments.
- If the footage doesn't support a clear creative direction, FLAG for
  human input rather than forcing a weak direction.

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `semantic_analysis_documents` |
| Reads | `temporal_index` |
| Reads | `prosody_analysis` |
| Reads | `clip_catalog` |
| Writes | `creative_direction` |
| Writes | `intent_model` |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| Footage doesn't support a clear direction | FLAG — human must specify angle |
| Conflicting emotional territories (no coherent thread) | Choose the strongest thread, document alternatives in rationale |
| Very low quality footage across all clips | FLAG — warn that footage may not support compelling video |
