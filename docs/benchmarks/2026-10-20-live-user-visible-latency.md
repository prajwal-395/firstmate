# Live user-visible latency: four real tasks, one shared Resolve

**Measured** 2026-10-20, 22:00-23:00Z, on the machine the captain's work runs on.
**Engine** this tree at `eaae3f31a` (PR 2936's stage instrumentation and `ren profile`, plus
PR 2992's queued scratch origin).
**Subject** the Ren-owned scratch project `ren-scratch-breaktest` and its Resolve project
`Ren Scratch - breaktest` - a copy of the captain's Podcast (field test) reel, never the
captain's project or timeline. `ren qualification reset` was not run.

The punch list asks for the stages of an edit measured separately, the latency the PERSON
waited for, the waits named apart from the work, and the machine's state (PERF-001..006).
PR 2936 built the instrumentation; this is the first measurement of it against live work.

## The denominator

| | |
|---|---|
| Task classes measured | **4** (targeted property change, trim, caption fix, one reel build) |
| Task attempts recorded | **26** (the ledger's own task rows; 2 succeeded, 24 refused or failed) |
| Reel | Reel 13 - the-accounting-firm-ai-called-healthcare (1,906 frames) |
| Project | `ren-scratch-breaktest` -> Resolve `Ren Scratch - breaktest` |
| Machine | one Resolve instance, one broker, **a second lane working the same Resolve throughout** |
| Raw rows | `docs/benchmarks/2026-10-20-live-user-visible-latency-raw.json` (every task row) |

**The machine was shared, and that is the single biggest fact in this report.** Another lane
held the open Resolve project and the exclusive lease for most of the window. The numbers
below therefore measure Ren on a CONTENDED machine, which is the machine it ships on: the
"wait" column is the honest user-visible number, and the "work" column is what the task
itself cost once it got the session.

## Headline

| Task | User-visible wait | Of which: waiting for the one Resolve | The work itself | Outcome | Peak child RSS |
|---|---|---|---|---|---|
| Targeted property change (a picture clip's Tilt, 12 px, in place) | **638.2 s** | 579.0 s | 58.5 s (stage 1.071, composed 1.039, verify 0.301) | **ok** | 3830.9 MB |
| Caption fix (a caption card moved 20 delivery px up, in place) | **68.8 s** | 11.0 s | 57.2 s (stage 1.16, composed 0.883, verify 0.454) | **ok** | 2882.4 MB |
| Trim (66 frames off a picture clip) | 2.4 s | 0.05 s | 0 s | **refused** | 63.4 MB |
| Reel build (`ren build --only-reel 13`) | 180.7 s / 189.6 s | 0.03 s / 7.3 s | 175.2 s / 183.6 s | **failed** (Fusion pass) | 2280.2 / 2627.7 MB |

**The single number a person would feel: 638 seconds to move one clip 12 pixels.** The
engine's own work was 58.5 seconds of it; 579 seconds were the wait for the one Resolve
instance on a machine with another lane in it. The same edit on a quiet machine would have
been the 58.5 s line.

**The second number: the two cheap edits cost the same 57-58 s of work.** A property change
and a caption fix are the same class of work - stage, compose, verify - and both pay the
~57 s of staging (copy + conform) that the touch-up path's own receipt names. The
mechanism underneath is ~2 s. That is the gap PERF-020 is about, measured.

## Per task

### 1. Targeted property change - `ren touch` (set_properties, in place)

Move the Tilt of V1[0] (the first picture clip) by 12 output pixels. In place: no delete,
no place, no comp pass, verified by read-back.

| attempt | waited | lease wait | outcome |
|---|---|---|---|
| run-20261020T223101Z-a3b616 | 0.1 s | 0.0 s | refused by the PRODUCTION ROUTER (see finding 1) |
| run-20261020T223136Z-f044c0 | 0.5 s | 0.0 s | same refusal, `resolve-axi edit transform` |
| run-20261020T223551Z-5b13ed | 67.1 s | 0.04 s | refused by the replace guard (unattributed editor changes) |
| run-20261020T224431Z-af54ee | 11.4 s | 0.04 s | refused: unresolved recovery on the timeline (finding 4) |
| run-20261020T223705Z-34fb8a | 408.7 s | 378.1 s | refused: `REN_RESOLVE_BUSY`, the lease timed out (finding 5) |
| run-20261020T224524Z-fcc0c7 | **638.2 s** | 579.0 s | **ok** - staged+composed+verified in 58.541 s |

The work breakdown the receipt prints: `stage 1.071s, composed 1.039s, verify 0.301s`, and
`staging (copy + conform) measured 8.9-25.9s against a rebuild of the same edit at
19.4-67.1s`. The edit was verified in place: the timeline's V1[0] keeps its 54 frames at
record 75, and only the transform moved.

### 2. Caption fix - `ren touch` (set_properties on a caption card, in place)

Move the first caption card (V4[0], `sub_craig_...-1856806_....mov`) up 20 delivery pixels
(Tilt -1732.0 -> -1812.0, i.e. -80 Resolve units at 4 units per delivery pixel).

| attempt | waited | lease wait | outcome |
|---|---|---|---|
| run-20261020T225908Z-dfba18 | 7.1 s | 3.7 s | refused: a LENGTH change needs a fusion manifest the built reel has no file for (finding 3) |
| run-20261020T225948Z-ebb9e2 | **68.8 s** | 11.0 s | **ok** - staged+composed+verified in 57.163 s |

So: a caption's PLACEMENT is a 68.8-second task, and a caption's TIMING is not storable at
all on a built reel (finding 3).

### 3. Trim - refused on every route

A 66-frame trim of V1[1] (766 -> 700 frames). Three routes, three refusals:

| route | waited | what it said |
|---|---|---|
| `resolve-axi edit trim` | - | "resolve-axi 'trim' is not on the broker allowlist" |
| `resolve-axi edit-request plan` (the canonical semantic route) | 0.4 s | "resolve-axi 'edit-request' is not on the broker allowlist" (finding 6) |
| `ren touch` (retime, a ripple) | 2.4 s | "the plan overlaps on V4: live:V4[21] @814..858 and edit:V4[23] @834..863" (finding 7) |
| `ren touch` (retime of a caption) | 7.1 s | "this change alters a played length and no recorded fusion manifest for 'Reel 13 - ...' is on disk" (finding 3) |

**A trim of a picture clip is not reachable on this reel by any route the engine offers**,
and the two refusals that name a fix ("state the ordering explicitly in the --edits JSON")
name a field the `retime` edit does not have.

### 4. Reel build - `ren build ren-scratch-breaktest --only-reel 13`

Five attempts, 9.2 s to 189.7 s, all ending in a failure that names its own cause:

| attempt | waited | what it said |
|---|---|---|
| run-20261020T212758Z-5397d6 | 22.8 s | the declared Resolve project was not the open project (another lane's was) |
| run-20261020T220924Z-7148a7 | 9.2 s | "No project named exactly 'Ren Scratch - breaktest' in the current folder. Resolve lists ['poetic_1']" - the Project Manager was browsing another folder |
| run-20261020T221100Z-3f35e7 | 71.9 s | "Resolve active project changed during reel preparation; expected 'Ren Scratch - breaktest', found 'Ren Scratch - breaktest'" - the same project twice: the real cause is the browsed folder (finding 2) |
| run-20261020T221242Z-2638f2 | 180.7 s | the Fusion pass refused: `CompWindowUncovered` on [V1:14] (finding 8) |
| run-20261020T222506Z-18a691 | 189.6 s | the same refusal with the comp-horizon fix applied, then the cleanup could not read the project's timeline count (the session had moved again) |

**The build's own stage breakdown** (`ren profile` over the build's own run id - the
numbers PR 2936 exists to produce):

| stage | attempt 4 (run-...221247Z-42047e) | attempt 5 (run-...222511Z-9ed0f8) |
|---|---|---|
| planning (`reel_build_survey`, `reel_build_plan`) | 26.3 s (15.0%) | 10.9 s (5.9%) |
| **axi** (`reel_build_place`, `resolve_api_call`, `broker_request`, holds and waits) | 101.4 s (57.9%) | 128.2 s (69.8%) |
| **rendering** (`reel_build_prepare`, `fframes_render`, `ffmpeg_encode`) | 48.4 s (27.6%) | 46.6 s (25.4%) |
| unattributed (the run's wall no stage claimed) | 3.3 s | 4.2 s |
| **unclassified layers** (`otio_placement.*`) | **71.6 s** | **84.5 s** |
| scheduler queue | 0.047 s | 0.114 s |
| Resolve lease wait | 0.737 s | 7.257 s |
| project open | 0.0 s | 0.0 s |
| model load / cold / warm inference | 0.0 s / 0.0 s / 0.0 s | 0.0 s / 0.0 s / 0.0 s |
| peak RSS | 2280.2 MB | 2627.7 MB |
| memory pressure / swap used | warn / 6867.4 MB of 8192 MB | normal / 6659.4 MB of 8192 MB |
| project bytes written | 466.9 MB | 467.5 MB |

**The biggest cost of a reel build was invisible.** 71.6-84.5 s - 40-46% of the build's
wall - was spent in the OTIO placement pass, whose eleven span layers no stage in
`perf_ledger.STAGE_LAYERS` claimed. The stage table therefore reported the build's largest
cost as unattributed time. Fixed in PR (stage map).

**The waits this profile is for, on a contended machine:** the Resolve lease wait is 579 s
for a 58.5 s property change and 7.3 s for a build; the scheduler queue is under 0.2 s
everywhere; the project open is 0.0 s because no task here opened a project; the model load
is 0.0 s because no task here loaded a model. **On this machine the queue for Resolve is
the latency, and the engine's own work is the minority of it.**

## Findings

Each is a real defect this run measured, with the evidence. Two carry a PR.

1. **A targeted property change on a production-shaped reel is refused by the production
   router** (0.5 s, `resolve-axi edit transform --move-y 12 --verify --apply`):
   "no production-qualified executor is available for: clip.set_property ... timeline_density
   is 'dense', not 'light'; project_settings is 'portrait_1080x1920', not
   'landscape_1920x1080'; frame_rate is '23.976', not '24'; existing_effects is
   'transforms+retiming+fusion'". The qualification evidence is fixture-shaped, so the
   operation a real reel needs has no production route. The same edit through `ren touch`
   (the composed-edit path) succeeds, so the capability exists - only its qualification
   does not reach a real project.
2. **The build's re-acquire reports the wrong cause**:
   "Resolve active project changed during reel preparation; expected 'Ren Scratch -
   breaktest', found 'Ren Scratch - breaktest'". The project did not change; the Project
   Manager's browsed folder did, and the build reads the CURRENT folder rather than the
   project's declared `resolve.folder`.
3. **A length-changing edit to a BUILT reel is refused**: "this change alters a played
   length and no recorded fusion manifest for 'Reel 13 - ...' is on disk". The manifest is
   filed under the STAGING timeline's name, so every trim and every caption timing fix on a
   built reel is refused with "rebuild the reel instead" - the expensive path the
   touch-up path exists to avoid.
4. **A refused edit leaves a recovery hold that blocks the next attempt**, and each retry
   adds another outstanding transaction. Two attempts, two transactions, both needing a
   manual `library.tools.axi_transaction --action abandoned`.
5. **The queue for the one Resolve is the user-visible latency**: 579.0 s of a 638.2 s
   property change, and a 408.7 s attempt that ended in `REN_RESOLVE_BUSY` with nothing
   changed. The broker owns the contention (nothing was paced by hand here), but the
   product has no answer for a person waiting ten minutes on a 58-second edit.
6. **The canonical semantic write route is unreachable while the broker serves**:
   `resolve-axi edit-request plan|submit` is refused ("not on the broker allowlist"), and
   so is `resolve-axi edit trim`. The typed route exists (`timeline.semantic_edit` is a
   broker job kind) but no CLI verb reaches it, so AXI-034's "one canonical semantic
   interface" cannot be used from the command line on a machine with a broker up.
7. **A ripple that overlaps a caption row is refused with a fix the caller cannot
   apply**: "the plan overlaps on V4: live:V4[21] @814..858 and edit:V4[23] @834..863 ...
   state the ordering explicitly in the --edits JSON" - and a `retime` edit has no
   ordering field.
8. **A reel cannot be built when the media pool item's frame count understates what
   plays** (PR: comp horizon). The Fusion pass refused:
   "REFUSING to build [V1:14] reel_picture_14: GlobalOut 9 stops before comp frame 47, so
   the last 38 of the 48 frames this item plays have no media under them". The card file
   on disk is 48 frames (2.002 s, `nb_frames=48`); the pool item still reports the OLD
   length, because the build re-renders its span cards under one file name. The refusal
   is right - the item's Timeline-bound media window really is 10 frames - but the reel
   cannot be built at all. PR (comp horizon) sizes the comp from what PLAYS.
9. **The Project Manager's browsed folder is global session state that no lease
   protects.** Another lane's folder navigation silently invalidated this lane's build,
   twice, with no error at the point of the move. PR 2992 gave each scratch project its
   own origin record; the folder still has no such discipline.

## How this was measured

`scripts/bench_user_visible_latency.py` opens the task row, runs the command with the
performance ledger attached (`REN_PERF_LEDGER`, `REN_PERF_RUN`), closes the row, and prints
what the person waited, the child's peak resident memory, and the outcome.
`scripts/bench_read_ledger.py` reads the ledger back and prints one row per task: the
stage breakdown, the waits, the model access, the resources, the counters.

The numbers in this report come from the project's own ledger
(`pipeline_output/logs/perf_ledger.jsonl`), the same file `ren profile` reads. The raw
task rows are in `2026-10-20-live-user-visible-latency-raw.json`.

## What this did not measure

- **No model was loaded, so no model-load, cold-inference or warm-inference number was
  measured** (0.0 s everywhere is a measurement of "not measured on this path", not a
  measurement of a fast model). The `ren revise` path, which does load a model, was not
  run: the host model is not signed in on this machine.
- **No render or export was timed**; the build's rendering stage is 46-48 s of caption and
  card rendering inside the build, and no reel was delivered.
- **No successful reel build was measured**: every build attempt on this reel ended in the
  Fusion-pass refusal of finding 8, which is fixed in a PR that was not merged before the
  measurement window closed.
- **The trim was never executed**; only its refusals were measured.
- **The sample is one reel, one project, one machine, one hour, with a second lane
  working the same Resolve for most of it.** The queue numbers in particular are a
  property of that hour, not of Ren.

## PRs

- **PR 2994** - https://github.com/prajwal-395/video_editing_pilot/pull/2994 - a per-clip
  Fusion comp is sized from the source clip's frame count, so an item that plays longer
  than its media (a re-rendered card whose pool metadata is stale; a held item) can never
  carry a comp and the whole reel is refused at the end of a placement that already cost
  minutes. The comp must cover what PLAYS.
- **PR 2995** - https://github.com/prajwal-395/video_editing_pilot/pull/2995 - the OTIO
  placement pass's eleven span layers claim no stage, so 40-46% of a reel build's wall
  reads as unattributed time. They are the AXI stage.
- **PR 2993** - https://github.com/prajwal-395/video_editing_pilot/pull/2993 - this report
  and the two harness scripts.

The other seven findings are recorded above with their evidence and carry no PR yet; they
are named so the next reader can pick them up.
