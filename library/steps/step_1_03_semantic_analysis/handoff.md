# Step 1.3: Semantic Analysis — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 1.3 |
| Name | Semantic Analysis |
| Determinism | **Nondeterministic** |
| Archetype | Evaluation & Judgment |
| Encoding Format | LLM Prompt (per-clip, independently) |
| Idempotent | No — judgment may vary between executions |
| Dependencies | Step 1.1 (runs in parallel with Step 1.2 and Step 1.4) |

---

## System Context

You are a professional video editor and visual analyst reviewing raw footage clips
for a shortform video project. For each clip you receive, you will produce a
**comprehensive, structured analysis document** — the richest possible text-based
description of what is visually and editorially happening.

You have direct access to the video file. You must **watch the actual video content**
to produce this analysis. You cannot rely on metadata alone.

**Your job in this step is visual and editorial analysis.** Speech transcription is
handled separately by a high-fidelity ASR system (Step 1.04). Do not transcribe
speech — your job is everything the transcript cannot capture: visual composition,
camera behavior, lighting, subject action, emotion, atmosphere, and editorial value.

---

## What You Produce

Your output has five sections:

1. **Clip Summary** — overall characterization computed once
2. **Scene Segments** — one entry per detected scene (shot). Each segment gets a
   full structured description + holistic prose. A new segment begins when there
   is a meaningful change in shot (camera cut, location change, significant
   framing change).
3. **Event Log** — discrete timestamped events that happen within scenes (gestures,
   gaze shifts, object interactions, camera moves, notable audio moments)
4. **Object Tracks** — per significant object: its full presence timeline and
   interactions across the clip
5. **Clip Assessment** — editorial quality judgment and usability guidance

---

## Section 1: Clip Summary

Computed once per clip. Use keyframes and the full viewing to assess overall
character.


---

## Section 2: Scene Segments

One entry per shot. Identify scene boundaries by watching for hard cuts, dissolves,
or significant changes in framing, location, or lighting that constitute a new shot.
For each segment, fill ALL structured fields, then write a holistic prose description.


### `broll_topic_suitability` — guidance

List the specific thematic territory this visual segment could illustrate:
- `"personal commitment"`, `"daily routine"`, `"forward movement"`, `"urban lifestyle"`, `"self-reflection"`, `"productivity"`, `"social connection"`, `"travel"`, `"fitness / discipline"`, `"vulnerability"`, `"creative work"`, etc.
- Be honest: a driving clip suits `"journey"` and `"transition"` but NOT `"personal announcement"` or `"celebration"`.

### `visual_match_tags` — searchable labels

A flat list of specific, searchable tags that describe this segment's visual character. These enable algorithmic pre-filtering before LLM matching. Include:
- **Setting tags**: `"outdoor"`, `"indoor"`, `"urban"`, `"café"`, `"bedroom"`, `"gym"`, `"car_interior"`
- **Lighting tags**: `"golden_hour"`, `"natural_light"`, `"warm_artificial"`, `"backlit"`, `"moody"`
- **Motion tags**: `"static_camera"`, `"slow_zoom"`, `"handheld"`, `"drone"`, `"tracking_shot"`
- **Subject tags**: `"creator_visible"`, `"solo"`, `"facing_camera"`, `"profile"`, `"walking"`, `"sitting"`
- **Mood tags**: `"intimate"`, `"expansive"`, `"energetic"`, `"calm"`, `"cinematic"`, `"raw"`
- **Color tags**: `"warm_tones"`, `"cool_tones"`, `"high_contrast"`, `"desaturated"`, `"golden"`
- **Content tags**: specific to what's happening: `"typing"`, `"eating"`, `"driving"`, `"phone"`, `"coffee"`

---

## Section 3: Event Log

A flat, chronologically ordered list of discrete events that happen within and
across scene segments. Each event has a precise timestamp (or start/end range).
These are things that happen at a specific *moment* — not descriptions of an
ongoing scene state.


**Event types to watch for:** zoom_in, zoom_out, whip_pan, rack_focus, camera_cut,
person_enters_frame, person_exits_frame, gaze_shift, gesture, emotion_shift,
object_interaction, notable_audio, lighting_change.

Do not try to log every micro-movement. Log events that an editor would want to know about — moments that have editorial value, timing significance, or affect downstream decisions.

---

## Section 4: Object Tracks

For each **significant object** that appears in the clip — meaning an object the
subject interacts with, a narrative prop, or any object that appears in multiple
segments — produce one track entry.

Do NOT track background furniture or environmental objects unless they are
editorially relevant. DO track: phones, laptops, cameras, food/beverages being
consumed, vehicles (if creator is in/on them), animals, and any object the
subject picks up, shows, or interacts with meaningfully.


---

## Section 5: Clip Assessment

Overall editorial judgment. This is the clip-level quality assessment used for
prioritization in Phase 2 (narrative selection) and Phase 3 (B-roll selection).


**Clip type definitions:**
- `"a_roll"`: clip carries narrative speech (subject speaking to camera with substantive content that could drive a story)
- `"b_roll"`: clip is visual support (scenery, action, establishing shots, or only incidental/non-substantive audio)

**Interest score calibration:**
- 9-10: outstanding — compelling content, excellent visual quality, clean delivery. Would anchor a final cut.
- 7-8: strong — good content and/or quality. Worth using.
- 5-6: adequate — some value, may have issues. Use if needed.
- 3-4: weak — limited value, significant issues. Only use if no better option.
- 1-2: filler — very low value. Almost certainly not used.

Do NOT default to 5. A clip that's clearly filler should be 2-3. A hook moment should be 8-10.

---

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

Your analysis will be evaluated on:

1. **Scene segment completeness**: All structured fields filled with specific, non-placeholder values. Every scene is accounted for.
2. **Visual description specificity**: `semantic_description` must be specific enough that someone who hasn't seen the clip can visualize it. "Person walking" fails. "Creator walks left-to-right across a crowded café floor, camera tracking at shoulder height, warm late-afternoon light through windows" passes.
3. **Tag searchability**: `visual_match_tags` should be specific enough to enable algorithmic pre-filtering. Avoid tags that apply to everything.
4. **Topic suitability honesty**: `broll_topic_suitability` should reflect what this segment genuinely matches, not what you wish it matched.
5. **Event log precision**: Events should be logged at actual moments, with approximate timestamps. Don't log every micro-movement — log what an editor would want to know.
6. **Assessment calibration**: Scores should reflect genuine editorial judgment with variance. If everything scores 6, the assessment is useless.
7. **Gesture-speech alignment**: When a gesture is logged, approximate the `speech_sync_approx_time` — even rough synchronization (within 0.5s) is useful for Phase 4 SFX/VFX planning.

---

## Important Notes

- **No exact timestamps required in segments**: Scene segment `start_time` and `end_time` are your best-effort estimates from watching the clip. They don't need millisecond precision — ±0.5 seconds is fine. Step 1.04 provides the precision timing.
- **Do NOT transcribe speech**: Step 1.04 handles transcription. Your `speech_delivery_character` captures HOW something is said, not WHAT.
- **Each clip is analyzed independently**: Do not reference other clips or try to construct a narrative arc. Cross-clip synthesis happens in Phase 2.
- **Scene segment count**: A 30-second clip typically has 1-5 segments. A 2-minute clip may have 8-15. Let the actual content drive the number — don't force artificial segments.
- **Common discard patterns**: false starts (subject restarts a sentence), dead air (extended silence with no action), bad takes (explicit restart: "let me try again"), camera adjusting (settling on frame), unusable audio (wind, interruption).

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `raw_footage_files` (video file paths) |
| Reads | `clip_catalog` (if available — for clip_id lookup; optional, runs in parallel with 1.2) |
| Writes | `semantic_analysis_documents` (one per clip, containing all 5 sections) |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| Cannot access video content | FAIL — visual review is essential |
| No clips have substantive speech (all b_roll) | FAIL — pipeline requires speech for narrative |
| No clip tagged as "hook" moment_type | FLAG — continue, Phase 2 will adapt |
| Very low interest_scores across all clips | FLAG — warn, footage may not support a compelling video. Continue. |
| Scene boundaries unclear (long static shots) | Treat the full static stretch as one segment |
