# Section 12, run once: one complete professional edit on a Ren-owned scratch copy

**Status: measured live on 2026-10-20. The milestone was NOT reached, and the
report says which of the twenty rows the run established and which it did not.**

The punch list's section 12 asks for one recurring demonstration: one brief,
ten obligations, ten pieces of evidence, a delivered file, and an independent
judge's reading of it. This is the first run of that demonstration on a
Ren-owned scratch copy, and it is the run the report below describes.

The brief is `the-whole-job` from the shipped acceptance set
(`library/tools/editorial_acceptance/brief_set.json`). The agent was handed the
brief's public half - the user's words, the project state, the acceptance
criteria - and the state of the project it was working on, and nothing else.
The answer key stayed with the evaluator.

## What was run, and where

| | |
|---|---|
| Ren project | `ren-scratch-bench-demo` (a copy of the Ren-owned `ren-scratch-breaktest` scratch, pointed at its own Resolve project) |
| Resolve project | `Ren Scratch - vep-bench-demo` - a **Ren-owned scratch copy** of one reel out of the captain's `Podcast (field test)` project |
| Timeline | `Reel 13 - the-accounting-firm-ai-called-healthcare`, 1907 frames at 23.976, 65 clips, 10 rows, 1080x1920 |
| How the copy was made | `Timeline.Export` to a `.drt`, then the guarded scratch import: `resolve-axi project scratch import /tmp/bench/reel13.drt "Ren Scratch - vep-bench-demo" --fps 23.976 --width 1080 --height 1920 --apply` |
| What was never touched | the captain's `Podcast (field test)` project and its timelines; `ren qualification reset` never ran; the qualification fixtures were not edited |
| Machine | the one Resolve, reached through the serving broker's lease |

The copy carries the reel's own media (the dialogue MXFs, the caption cards,
the graphics) with its links intact: the imported timeline reads 65 clips with
the same source ranges and the same transforms as the reel it was copied from.

## The ten obligations, one by one

The plan is the run's own: eight operations, each named for its meaning, each
targeted at a moment measured off the imported timeline. The table says what
happened to each.

| # | Obligation | Operation | Outcome |
|---|---|---|---|
| 1 | Find the most engaging opening | `reel.opening` | **not executed.** The request was prepared and its context read: the context carried no transcript, no footage observations, and only the FILE NAME of the creative brief, so the opening could not be decided from the evidence the request carried. The strongest line in the reel is the closing one - "Search didn't change, the question changed" - at 66s. |
| 2 | Take out the pauses and the repetition | `clip.trim` + `clip.remove` | **not executed.** Both targets were found and measured: the pause before Craig's "it's not necessarily AI's fault", and the second, redundant "this is not AI's fault" turn that Akshita says 3s later. The write was not attempted: the machine's single Resolve was held by another lane for the whole window. |
| 3 | Replace the weak camera angle | `multicam.select_angle` | **not available for this timeline.** The reel carries two separate camera sources, not a multicam clip: at record 1038..1141 Craig speaks while the shot is Akshita's camera, and the other angle of that same moment (LCATL0013.MXF) is already on V2. The angle turn is a source swap, not a multicam selection. |
| 4 | Insert the matching cutaway | `broll.insert` | **not executed.** The cover range was identified (Akshita's case-study passage, record 248..1821) and the only other footage the project holds is the other camera. The write was not attempted (machine contention). |
| 5 | Correct the wrong caption | `caption_fix` | **not available for this copy.** Every caption card in the reel was read: none carries a number. The only quantity in the reel is "a couple years ago". The request's premise does not hold here. |
| 6 | Move the name bar off the captions | `overlay.position` | **not executed, and the premise does not hold either.** Translated, prepared, routed (protected experimental, `reel_touchup`) and resolved to the lower third `2e9f8de7-9cc6-46ff-91af-19b75c66fd48` - the first attempt failed on a file the scratch copy was missing, the next forty on the machine's open project being another lane's. The name bar does not fight the captions here: in the delivered file it sits inside the picture, clear of the caption row. |
| 7 | Balance the levels | `audio.gain` | **not available for this copy.** The reel carries no music bed: its 65 clips are dialogue, captions, graphics, overlays and their linked audio. "Her voice on top of the music" has no music to sit on top of. The export's own loudness measurement is -23.18 LUFS integrated, 9.18 dB under the -14.0 target. |
| 8 | Preserve the grade, the transitions, the framing, the linked audio, the human edits | - | **established, trivially.** The shadow generation of the reel did not move (2 before and after the run) and no collateral change was recorded: the timeline is the imported copy, byte for byte. Nothing was disturbed, because nothing was written. |
| 9 | Export the finished reel | `resolve-axi render inspect` | **delivered.** 111,267,627 bytes, 79.573s, 1080x1920 at 23.976, sha256 `04343989cec1dface5631e9666a55816d4b38f00b638629d464799b429368f4f`, rendered through the broker's exclusive render job (195s). |
| 10 | Verify the exported video | `delivery.build_render_evidence` | **inspected, and it FAILS.** The FILE was read, not the timeline: audio streams present, no freeze frames, but the loudness check fails, one undeclared black frame segment is present, and 3.253s of picture plays over digital silence at 75.701-79.529s. Resolution, frame rate and duration were not graded, because nothing was declared for them. |

## The ten pieces of evidence

Scored by the engine's own scorer (`library.tools.editorial_acceptance.demo`),
from the task record `tests/live/reports/editorial-demonstration/the-whole-job.json`.

| Evidence | Established | Why |
|---|---|---|
| The brief is interpreted without evaluator-supplied edit commands | yes | the run restated the outcome in its own words and named the eight operations the request implies |
| All selected source moments are supported by recorded evidence | yes | every moment carries the caption card and the timeline item it was read from |
| The edit plan is semantically valid | yes | the plan names only the operations the brief allows, and nothing outside them |
| Every requested operation is routed through qualified execution | **no** | no request reached a verified or committed receipt: the six editorial changes never reached the timeline |
| Unrelated approved state remains unchanged | yes | no collateral change was recorded - and nothing was written, so the row is thin |
| No unresolved transaction or recovery state remains | yes | the last receipt is a refusal with no recovery block, and the read-back after the earlier partial failure showed the generation had not moved |
| The exported video contains the requested changes | **no** | the judge read the file and it carries none of them: it is the imported copy unchanged |
| Technical audio/video constraints pass | **no** | the loudness check fails, one undeclared black segment is present, 3.253s of picture plays over digital silence |
| An independent editor judges the treatment acceptable against the brief | **no** | the judge (the crewmate agent, shown only the brief's public half, the digest, four stills and the file's own measurements) returns editorial `fail`: 3 criteria pass, 4 fail, 3 are not measured |
| Total latency and peak resource usage are measured | yes | see below |

**6 of 10 pieces of evidence established. The milestone is not reached** - and
the harness's own rule is why: the milestone needs every obligation
established, every piece of evidence produced, AND the independent judge
passing the delivered file against the brief. The plan covers all ten
obligations; the FILE does not, and the file is the point.

## Latency and peak memory

| | |
|---|---|
| Wall, first live read to the end of the verification | **3309 s** (55m) - from 2026-10-20T22:19:59Z to 23:15:08Z. It includes the waits for the machine's single Resolve. |
| Instruction to verified | **none** - no edit was verified, so there is no such number |
| The export render | 195 s (one broker job, EXCLUSIVE) |
| The export's verification | 28.9 s |
| Peak RSS, the delivery-evidence build | **379,944,960 bytes** (362 MiB) |
| Peak RSS, the `ren revise` front door | 50,315,264 bytes (48 MiB) |
| Resolve's own process, and the broker's render | **unmeasured** - never zero |

## The defect this run found, and fixed

The first `ren revise` of the run refused every program written to the
engine's own published schema:

```
request base generation 1 is stale; recorded head is 1
```

The pin had arrived as a STRING - the `edit_spec` schema declares
`program.base_generation` a non-empty string and the validator enforces it -
and the staleness check compared it with an int. `1 != "1"`, so a request
pinned to the head was stale by construction, and the wording named the same
number on both sides. Every editorial change in the run stopped at that check
before the engine could route it or refuse it for a real reason.

The fix reads the pin as the number it names, where the request's own
invariant lives (`library/tools/edit_request.py`), so every way in gets one
meaning; a pin that is not a number refuses rather than being dropped. With
it, the request prepares, routes and reaches the executor - and the real
refusals behind this run became visible where before only the stale check was.
Test: `tests/unit/resolve/test_edit_request_generation_pin.py`.

(A sibling lane fixed the same defect at the `revise.py` boundary on the same
day, in `fm/revise-base-generation-int`.)

## What this run did NOT establish

- **Six of the seven editorial changes did not reach the timeline.** They were
  interpreted, planned, targeted and routed, and the machine's one Resolve was
  held by another lane for the windows in which they could have been written.
  A rerun on a quiet machine is the next step, and the record carries every
  target and every operation needed to run it.
- **Three of the brief's premises do not hold on this reel**: there is no
  caption with a wrong number, no music bed, and no name bar fighting the
  captions. The report says so rather than manufacturing an edit for them.
- **The export of the unedited copy fails its own technical checks** (loudness,
  an undeclared black segment, 3.25s of picture over digital silence). That is
  a finding about the reel, not about the run, and it is the first thing a
  rerun has to fix.
- **The independent judgement is one judge**, not a panel. The panel machinery
  (`judgement.panel`) reads two or more readings; this run has one.

## The files

- `tests/live/reports/editorial-demonstration/steps-the-whole-job.json` - the
  run's measured steps, the moments, the plan, the receipts and the numbers
- `tests/live/reports/editorial-demonstration/the-whole-job.json` - the task
  record the acceptance harness reads
- `tests/live/reports/editorial-demonstration/the-whole-job-scored.json` - the
  scored demonstration (obligations, evidence, latency, resource)
- `scripts/vep_bench_demo.py` - the seam: the run's measurements in, the
  record and the scored report out. It drives no edit and touches no Resolve.
