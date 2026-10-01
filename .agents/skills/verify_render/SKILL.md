---
name: verify_render
description: >
  The deterministic gate over a rendered file: loudness without clipping,
  undeclared black, freeze frames, picture over digital silence,
  resolution, frame rate, duration and an audio stream. Use before
  approving, revising or building on any render. Free (ffmpeg only), and a
  failing check is a failed render.
---

# verify_render

The deterministic gate over a rendered file. Run it before you sign off
on any render.

## What it is

The file-only half of `library/tools/render_qa.py`, wrapped as a skill:
loudness without clipping, black frames nobody declared, freeze frames,
picture playing over digital silence, resolution, frame rate, duration
(when you know what it should be), and that an audio stream exists at
all. Every check reads the master file ffmpeg produced - not the plan
that made it. A gate that reads the manifest cannot tell whether
anything it declared actually reached the picture; these can.

## WHEN to reach for it

- You are the `validate` step, or any step asked to judge a render:
  run this FIRST, before writing any judgement of your own.
- A render just landed and you are about to approve, revise, or build
  on it. If this fails, the render is wrong no matter what the plan
  says.

Do NOT reach for it when there is no file yet, or to judge a plan on
paper - it measures a render, and with no render it refuses rather
than passes.

## What it costs

Free. No model loads, no Resolve, no GPU. Seconds of ffmpeg on the
file - the silence-under-picture check costs ~3 seconds on a
twenty-minute 4K master; the rest is ffprobe-scale.

## What it returns, and what it means

A verdict: `passed` plus one row per check with its own `passed` and
`detail`. `passed: false` means the render is wrong - say which check
failed and what it measured, and do not approve the render. This one
GATES: a failing check is a failed render, not an opinion.

## How to call it

You have a shell. Run it yourself - do not describe the render from
memory and do not assert you checked:

```
python3 -m library.skills.verify_render.skill \
    --video /path/to/master.mp4 \
    --project-folder /path/to/project \
    --step-id <your-step-id>
```

Optional: `--expected-duration SECONDS`, `--expected-resolution W H`,
`--expected-fps FPS`. Pass them when you know what was asked for - a
gate checks what was asked for, not what is usual.

After a `ren touch`, pass its receipt (`--dirty-receipt <path>`,
repeatable; `ren touch` prints the path) to re-check only what the touch
changed: black and freeze read only the dirty picture spans, silence is
kept to the dirty spans, loudness runs only when audio is dirty, and what
was not re-checked is named in `not_rechecked` - never passed. A receipt
without a dirty block, or a render older than the touch, is checked whole.
On a 46s reel a caption swap re-checks in 1.0s instead of 4.9s
(`library/tools/dirty_regions.py`).

The run writes a receipt to
`<project>/pipeline_output/skill_runs/<step-id>/verify_render.json`.
The pipeline reads that receipt back to confirm the check ran: only an
actual invocation writes one, so saying you checked without invoking
fails the step. If you have no shell, say which check you need and the
pipeline runs it for you - the receipt is still written, and its
verdict still gates.
