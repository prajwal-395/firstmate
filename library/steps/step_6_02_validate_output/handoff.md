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

You are validating the RENDERED output. This is the ONLY step in the
pipeline that evaluates the finished file - everything else operates on
specifications and data.

**You receive two things, and they are different.**

`deterministic_validation` is the MEASUREMENTS: duration, resolution,
frame rate, black frames, frame occupancy, face-intact, loudness, the
mix, subtitle timing. Those are numbers off the file and they are not
opinions.

`render_watch_frames` is the PICTURE: real frame strips of the rendered
file on disk, with the directory holding them. **Open them.** They are
the only thing in this context that is a picture; everything else
describes what was PLANNED.

**When `render_watch_frames` is absent, or carries a line saying the
frames were withheld, NOTHING HAS WATCHED THIS RENDER.** Say so in your
answer and judge from the measurements alone. Do not write a visual
verdict you did not look at - that is exactly what this step did before
the frames existed, and it is why a gate whose prose claimed eyes reads
as coverage it does not have.

The captain's own bar is *"would I post this?"* - theirs to apply, and
not a question you can check. Yours are the questions the block lists,
each of which a specific frame answers yes or no.

### The timeline behind the file

The file is the export OF a timeline, and the timeline itself must obey
the SOP (`docs/TIMELINE_SOP.md`) - no empty rows, no default row names,
no two rows carrying one role, a-roll picture linked to its speech, the
placed stream the recorded program stream. The `verify_timeline` gating
skill reads that back off the live timeline in Resolve. Run it BEFORE
you answer, on the build this step is validating:

```
python3 -m library.skills.verify_timeline.skill \
    --project "<rendered_output.resolve_project_name>" \
    --timeline "<rendered_output.timeline_name>" \
    --project-folder <project-folder> \
    --step-id validate \
    --plan-json '<rendered_output.track_plan as JSON>'
```

- All three live on `rendered_output`, which is in your context: the
  exact Resolve project the build opened, the timestamped timeline
  name it placed (not the base project name), and the plan that build
  laid out. The manifest itself never reaches your prompt, so the
  address has to come from the render record - do not guess names.
  `--step-id` is this DAG node's id (`validate`).
- `--plan-json` is the build result's own track plan, which 6.01
  records on `rendered_output.track_plan`. Pass it: without it the
  link and stream checks are skipped openly and the gate reads only
  its structural half. A run whose `rendered_output` carries no plan
  is an old build - say so; the structural half still gates.
- `passed: false` means do not approve the timeline, whether it names
  violations or a refusal (Resolve down, wrong project, no such
  timeline). Say which check failed and what it measured. The step's
  verdict carries a failed timeline gate as a fail no matter what the
  file measurements say.

---

## Task Prompt

Open every strip `render_watch_frames` names, then evaluate the render
and produce a structured validation result.

### Validation checks:

1. **The picture** (from the strips): text clipped by the frame edge,
   captions too large or too small to read, graphics cut off or
   overlapping, a face outside the frame or under an overlay, a shot
   blurred or of nothing, a black bar inside the frame, something that
   appears or jumps inside one continuous shot. Name the span and where
   in the frame. A clean span is a real answer - say it is clean.

   **A declared letterbox is not a defect.** Each strip carries a
   `framing` column: the framing the clip playing over that span
   DECLARES. `letterbox` means bars above/below are the project's stated
   preference - a series that wants bars declares `framing_intent: 0.0`,
   and a landscape source in a portrait frame letterboxes by design. On
   such a strip, bars are the deliverable, not a defect: report the
   strip as cleanly letterboxed rather than flagging the bars. `fill`
   means the frame is asked to be covered - bars on such a strip ARE a
   defect. `partial` means a declared punch-in: the bars are narrower
   but still declared. An empty framing column means the manifest
   declared nothing for that span - judge the bars on their own merits
   and say the declaration was absent. The deterministic
   `frame_occupancy` check in `deterministic_validation.qa_report`
   already measured the bars against the declared framing; read it for
   the numbers and do not re-derive them.
2. **Audio quality** (from the measurements): speech audible, music
   balanced, no clipping. You have no sound; report what was measured
   and do not infer.
3. **Subtitle accuracy**: readable and placed clear of what matters -
   from the strips. Timing is measured, not watched: the strips sample
   seconds apart and cannot see a few frames of drift.
4. **Timing accuracy**: **read the verdict, do not re-derive it.** The
   bridge already compared the rendered duration against the project's
   declared target and wrote the answer to
   `deterministic_validation.checks.duration` as a `pass` flag plus the
   `issues` that explain a failure. Report that verdict and what it means
   for the deliverable. Do not restate the arithmetic, and do not reach a
   different conclusion from the same numbers - the measurement is the
   measurement.

**What the strips CANNOT tell you** is stated in the block itself, and
it binds: anything shorter than the sampling resolution was never
sampled, motion between two samples was never seen, and nothing here
carries sound. Reporting on those is inventing a finding.

---

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->

---

## Evaluation Criteria

1. Status is "pass" only if ALL checks pass
2. `distribution_ready` is `true` only if status is "pass"
3. Issues must be specific (timestamps, descriptions)
4. A visual issue names the span it is in and where in the frame it is,
   because a reader has to be able to open the same strip and see it
5. If the picture was not seen, the answer says so - an unwatched render
   is never reported as a watched one that passed

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `rendered_output` (the actual file) |
| Reads | `deterministic_validation` (the measurements off that file) |
| Reads | `render_watch_frames` (frame strips of that file, on disk) |
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
