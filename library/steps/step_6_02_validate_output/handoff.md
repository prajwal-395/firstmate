# Step 6.2: Validate Output — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 6.2 |
| Name | Validate Output |
| Determinism | **Nondeterministic** |
| Archetype | Evaluation & Judgment |
| Encoding Format | LLM Prompt (requires video review capability) |
| Idempotent | No |
| Dependencies | Step 6.1 |

---

## System Context

You are watching the RENDERED video and validating it against quality
criteria. This is the ONLY step in the pipeline that evaluates the actual
rendered output — everything else operates on specifications and data.

The ultimate test: **"Would I post this?"**

---

## Task Prompt

Watch the rendered video file and evaluate it against the quality criteria
below. Produce a structured validation result.

### Validation checks:

1. **Visual quality**: Video plays smoothly — no stuttering, artifacts,
   wrong clips, bad conform, or corruption
2. **Audio quality**: Speech is audible, music is balanced, no clipping,
   no sync issues
3. **Subtitle accuracy**: Subtitles are readable, correctly timed, don't
   overlap important visual content
4. **Timing accuracy**: Duration is within the 30-60 second target,
   transitions land correctly

---

## Output Format

```json
{
  "status": "pass | fail",
  "visual_quality": { "pass": true, "issues": [] },
  "audio_quality": { "pass": true, "issues": [] },
  "subtitle_accuracy": { "pass": true, "issues": [] },
  "timing_accuracy": { "pass": true, "issues": [] },
  "overall_impression": "string — would you post this?",
  "distribution_ready": true,
  "recommended_action": "string or null"
}
```

---

## Evaluation Criteria

1. Status is "pass" only if ALL checks pass
2. `distribution_ready` is `true` only if status is "pass"
3. Issues must be specific (timestamps, descriptions)
4. The "would I post this?" gut check is the ultimate quality bar

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `rendered_output` (the actual file) |
| Writes | `validation_result` |

---

## Error Handling

| Failure Mode | Return To |
|-------------|-----------|
| Visual glitch | Step 6.1 (fix render) or trace to source spec |
| Audio sync issue | Step 6.1 or trace to Phase 2/3 |
| Subtitle error | Step 4.1 |
| Overall quality low | Identify weakest element, trace to appropriate phase |
| 2 failed re-renders | Escalate to human review |

---

## When This Passes

**The pipeline is COMPLETE.** `distribution_ready: true` means the video
is ready for upload/distribution.
