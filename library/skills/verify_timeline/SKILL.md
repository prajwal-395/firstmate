# verify_timeline

The deterministic gate over a built timeline. Run it before you sign
off on any timeline.

## What it is

`library/tools/timeline_conformance.py`'s `verify_timeline`, wrapped
as a skill: it reads the live Resolve timeline back against the SOP
(`docs/TIMELINE_SOP.md`) - no empty rows, no default row names, no two
rows carrying one role, a-roll picture linked to its speech, captions
inside a speech span joined to it, the placed stream the recorded
program stream. Every check reads the timeline Resolve holds - not the
plan that built it. A gate that reads the plan cannot tell whether
anything it declared actually reached the timeline; these can.

## WHEN to reach for it

- You just built or placed a timeline, or you are about to approve,
  revise, or build on one: run this FIRST, before writing any
  judgement of your own.
- A timeline just landed and something downstream depends on its
  shape. If this fails, the timeline is wrong no matter what the plan
  says.

Do NOT reach for it to judge a plan on paper - that is
`verify_treatment`'s territory - or to judge a rendered file - that is
`verify_render`. It reads the live timeline in Resolve, and with no
timeline it refuses rather than passes.

## What it costs

A shared read lease on the one Resolve instance - seconds, no render,
no model, no GPU. Several reads run together; a build writing through
the cursor waits its turn, and so do you. Resolve must be running with
the exact project open, or the skill refuses rather than passes.

## What it returns, and what it means

A verdict that GATES: `passed`, one row per check it ran, the checks
it openly skipped, and the violations in `issues`. `passed: false`
means the timeline disobeys the SOP - say which check failed and what
it measured, and do not approve the timeline.

A check that needs the track plan's roles (the link and stream checks)
is SKIPPED openly without the plan, never passed silently: pass
`--plan-json` with the build result's `track_plan` so the whole SOP is
read, not just its structural half.

## How to call it

You have a shell. Run it yourself - do not describe the timeline from
memory and do not assert you checked:

```
python3 -m library.skills.verify_timeline.skill \
    --project "Exact Project Name" \
    --timeline "Reel 09" \
    --project-folder /path/to/project \
    --step-id <your-step-id>
```

Optional: `--plan-json '{"video_tracks": [...], "audio_tracks": [...],
"material": {...}}'` from the build result's `track_plan`. With it the
link and stream checks run; without it they are reported as skipped.

The run writes a receipt to
`<project>/pipeline_output/skill_runs/<step-id>/verify_timeline.json`.
The pipeline reads that receipt back to confirm the check ran: only an
actual invocation writes one, so saying you checked without invoking
fails the step. If you have no shell, this skill cannot run for you -
it needs your build's own timeline name and track plan, which exist
only once you answer - so say so and do not answer until the check
runs.
