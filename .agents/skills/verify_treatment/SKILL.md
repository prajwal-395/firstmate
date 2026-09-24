---
name: verify_treatment
description: >
  See what a visual treatment really draws before the captain has to:
  build the clip's comp with and without it, and report which rendered
  frames changed and whether each sits inside the animation's declared
  window. Use when planning or approving a per-clip comp treatment (drift,
  switch animation, crop).
---

# verify_treatment

Look at what your visual treatment drew - before the captain has to.

## What it is

The before/after half of `library/tools/treatment_verify.py`, wrapped
as a skill: it builds the clip's comp WITH your treatment and WITHOUT
it, evaluates the treatment's own splines over the frames the timeline
really renders, and reports which frames changed and whether every
changed frame sits inside the animation's declared window. Proven on
PowerCrop - the switch-on/off Crop the pipeline shipped without ever
looking at: the head opens as declared, the tail on a 24000/1001 reel
cut from 30 fps pool footage keyed its whole animation past everything
rendered (0 of 72 frames changed), and a head on a clip shorter than
its own animation never reaches neutral.

Every check reads the comp the renderer writes - not the plan that
asked for it. A gate that reads the plan cannot tell whether anything
it declared actually reached the picture; these can.

## WHEN to reach for it

- You are planning a visual treatment (drift, switch animation, any
  per-clip comp key) and you want to know it draws what you claim
  before you answer: run this FIRST, on your own params.
- A treatment just landed and you are about to approve, revise, or
  build on it. If this fails, the treatment is wrong no matter what
  the plan says.

Do NOT reach for it to judge a finished file - that is
`verify_render`. Do NOT reach for it when the question is answerable
from numbers already in front of you.

## What it costs

The deterministic half is free: two comp builds (string ops, no
Resolve, no model) plus a spline evaluation per rendered frame -
milliseconds per clip, and the receipt records what your call cost.
Stills are cheap ffmpeg seeks into the source file. The Gemma half is
NOT run by default: it loads a ~7.5GB model and spends ~6s per still,
so it runs only with `--ask-vision`, and its answer is recorded as an
opinion, never enforced (the captain's 2026-09-09 cost ruling).

## What it returns, and what it means

A verdict that GATES, plus an observation that reports:

- `passed` - the deterministic half: every changed frame inside the
  declared window, the treatment drew something, and it settles to
  neutral where it claims to. `passed: false` names the failure
  (`outside_window`, `drew_nothing`, `never_settles`) - say which and
  what it measured, and do not ship the treatment. The applier undoes
  a failed treatment itself (drops the key, rebuilds, records the
  receipt); at plan time, change or drop the entry instead.
- `min_kept_fraction` / `max_gain` - measurements for your judgement:
  how much picture the treatment keeps at its tightest, how hot its
  brightest frame is. "The picture changed by N%" is evidence; "badly
  framed" is your call to record, never the gate's to compute.
- `stills` - frames of the source footage at the window's timecodes,
  on disk, for you to open yourself where your harness shows pictures;
  a withholding notice where it cannot (the `window_frames` rule: a
  picture has no smaller textual form, so nothing stands in for it).
- `vision` - the model's recorded opinion with its confidence, or
  `available: false` with the reason. Recorded, never enforced.

## How to call it

You have a shell. Run it yourself - do not describe the comp from
memory and do not assert you checked:

```
python3 -m library.skills.verify_treatment.skill \
    --effects '{"tv_power_head": true}' \
    --clip-dur 600 \
    --treatment-key tv_power_head \
    --played-frames 72 \
    --project-folder /path/to/project \
    --step-id <your-step-id>
```

Optional: `--source-file /path/to/clip.mov --source-fps 30` to capture
window stills from the footage; `--ask-vision --question "..."` to put
them to the local model (on request only - never on every build).

The run writes a receipt to
`<project>/pipeline_output/skill_runs/<step-id>/verify_treatment.json`.
The pipeline reads that receipt back to confirm the check ran: only an
actual invocation writes one, so saying you checked without invoking
fails the step. If you have no shell, this skill cannot run for you -
it needs your plan's own effect params, which exist only in your
answer - so say so and do not answer until the check runs.
