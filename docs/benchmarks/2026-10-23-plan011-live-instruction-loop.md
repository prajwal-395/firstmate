# PLAN-011 - the instruction loop, run live

2026-10-23. The second half of PLAN-011: the loop was wired and measured
in [the first half](2026-10-23-plan011-instruction-loop.md); this is the
loop WORKED, on a Ren-owned scratch copy, over the briefs.

## What was run, and against what

- the scratch copy: `ren-scratch-breaktest` - Ren's own copy of the
  podcast reel (Resolve project "Ren Scratch - breaktest", timeline
  "Reel 13 - the-accounting-firm-ai-called-healthcare"), with the
  session's master, the reel's recorded read-back (65 clips on ten rows:
  two cameras, 45 caption cards, the frame, the post header, the corner
  logo, the name bar, the two speaker audio rows), the media pool's 56
  items, and the master's 477-segment word-timed transcript;
- the briefs: all eight of the shipped acceptance set, and all thirteen
  of the BASE-003 corpus;
- the driver: `evaluation/tools/plan011_live_run.py`. The PLANS in it are
  the agent's judgement, written against the live evidence the run
  gathers; the intent compiler, the plan gate and the routing decision
  are the engine's own code. The corpus's sealed answer key is never
  read by the run - the corpus briefs are worked from their public
  instruction and the live project, and the scorer holds the key.

## The planning benchmark, before and after

The harness's own measurement, read with `ren accept` over the records:
the eight briefs of the shipped set, eight of eight scored, nothing not
run.

| view | before (2026-10-20 records) | after (this run) |
|---|---|---|
| **the eight-brief planning benchmark** | **4/8 = 0.50** | **8/8 = 1.00** |
| the five briefs that expect an EDIT | 1/5 | 5/5 |
| acceptance criteria the plans declared covered | 15/22 | **22/22** |

The before column is the 2026-10-20 run's own records, re-measured with
the same harness. The number the punch list's release gate names - "the
eight-brief planning benchmark should improve beyond 4/8, with
particular emphasis on the briefs that actually require edits" - is met:
4/8 to 8/8, and the edit briefs from 1/5 to 5/5.

**What moved, and why.** The 2026-10-20 plans declared the criterion
they changed and were silent about the ones the change preserved, and
the harness read the silence as a gap: 15 of 22 criteria declared
covered. The rule PLAN-011 added - every stated instruction is CARRIED
or DECLINED, before the write - is what a plan now has to satisfy, so
the plan states the preservation it establishes as well as the change it
makes. The criteria the plan did not cover are not covered BY ACCIDENT
any more: a plan that cannot cover one has to say why it declines it,
and the account at the end of the task reads that decline.

## Instructions accounted for end to end

The account is the engine's own: every stated instruction of a brief
(its acceptance criteria), and whether it reached a VERDICT - met,
refused with a reason, or failed a check. A refusal is a verdict.

| set | stated instructions | declared covered by the plan | reached a verdict | unaccounted |
|---|---|---|---|---|
| the eight-brief acceptance set | 30 | 30/30 | 17/30 | 13 |
| the BASE-003 corpus briefs | 33 | 33/33 | 22/33 | 11 |

The verdicts: 0 met, 39 refused, 0 failed, 24 unaccounted - the refusals
are the engine's own (no production route, or the footage does not carry
what the request names), and the unaccounted are the instructions of the
briefs whose MUTATION never ran.

**Why 24 reached no verdict.** The serving broker's queue is held by
another lane's qualification session: the oldest queued job had waited
5138 seconds, and the read this lane submitted
(`resolve_axi project`) was still `queued` after 300 seconds. A brief
whose plan names a real mutation therefore records its execution as NOT
RUN, with the queue's own words, and the harness reports that stage as
`not_measured` - never a pass, never a zero. The plan was complete and
the write did not happen, and the account says exactly that.

## The corpus briefs, and the substrate they need

The corpus's own premise view, over this run's records: the scratch copy
available here is a `caption_heavy_reel`, and the briefs are written
against a `two_camera_interview` - "the footage is the wrong shape for
the brief". The corpus's substrate project holds fourteen reels; this
scratch copy holds one, cut from LC4932 and LCATL0013, while the moments
the corpus briefs name (Craig's cost line in LCATL0011, the two tellings
of the SEO definition in LC4930 and LC4931) live on the master's other
recordings. So the run's answer to those briefs on THIS copy is the
correct one for this copy - "the footage does not carry it" - and the
brief-level score measures the substrate, not the engine. The pairing
is the work list, and the corpus's 2026-10-21 report already names it.

## The refusals, which are the point

Every brief whose correct answer is a report back reached it, and said
why, with the live evidence behind it:

- **no workshop bench footage** - the pool's 56 items are the two camera
  files, the rendered caption cards, the motion graphics, the stills and
  the reel cards;
- **no screen recording** - the same pool, read;
- **no second angle** - the reel's two camera rows are the two SPEAKERS,
  and each row carries one source file, so there is no angle of one
  moment to cut to;
- **no music bed** - no music clip is on the timeline and no music file is
  in the pool;
- **the cost line is not in this cut** - it is Craig's line in LCATL0011,
  and this reel is cut from LC4932 and LCATL0013;
- **no caption names the wrong word** - the reel's 45 caption cards all
  match the speech they cover;
- **two requests in one** - "leave it exactly as it is, word for word"
  and "tighten it up" are two requests, and the run asks which one.

## What was NOT measured, and what was left undone

- **No Resolve write ran.** The broker's queue was held by another
  lane's qualification session for the whole window; a mutation was
  submitted to the queue and never ran. The execution stage is
  `not_measured` for those briefs, and the 39 refusals are the engine's
  own routing decisions, which need no Resolve.
- **The corpus briefs were worked against the wrong substrate** (one
  reel, not the corpus's fourteen). Their brief-level score therefore
  measures the substrate; the account over them is complete, and the
  plans are the ones the run would make on the right footage.
- **No brief delivered a file**, so `final_quality` has no denominator -
  `not_measured`, as in the 2026-10-20 run.
- **Nothing of the captain's was touched.** The scratch copy is Ren's;
  the master's transcript and the session media were READ. No captain
  project, timeline or media was written, no broker was started, and
  `ren qualification reset` was never run.

## Reproducing

```sh
python3 evaluation/tools/plan011_live_run.py \
    --project "$PIPELINE_PROJECTS_ROOT/ren-scratch-breaktest" \
    --records tests/live/reports/plan011-instruction-loop-records
bin/ren accept --records tests/live/reports/plan011-instruction-loop-records
python3 evaluation/tools/plan011_instruction_loop.py \
    --live-records tests/live/reports/plan011-instruction-loop-records \
    --live-corpus-records tests/live/reports/plan011-instruction-loop-corpus-records
```
