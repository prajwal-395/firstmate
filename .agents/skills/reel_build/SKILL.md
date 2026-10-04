---
name: reel_build
description: >
  Building approved reels onto Resolve timelines. Covers the build-reels
  entry point, the ask-before-build loop, which writer owns what, which
  reel CLI verb does what, and the interpreter trap. Use when about to
  build, rebuild, verify, deliver, or touch up reels, or when a reel
  build refuses.
---

# reel_build - the loop that places approved reels

Measured 2026-09-18 (`data/vep-what-is-the-floor-for-a-reel/report.md`):
six of six lanes independently read `library/tools/reel_build.py` to work
out the same three things - which entry point to call, which writer owns
what, which CLI does what. That cost 56 calls and 18.3 minutes in one
batch, paid once per lane forever. This skill is those three answers.
A lane that already knows them builds its last reel in 13 turns; the
first took 118. Everything in here prevents a re-read, a refusal, or a
phantom failure. Nothing else is in here.

Do NOT run this skill's subject to learn it: `reel_build` executes
ffmpeg and drives Resolve, so read it rather than running it.

## 0. The interpreter trap - read this first

Run EVERYTHING through `bin/vep`, never a bare `python3`:

```
bin/vep manage_project.py build-reels <project> --only-reel 21
bin/vep -m pytest tests/test_x.py -q
```

A bare `python3` here is 3.14 with none of the ML stack. The suite run
under it fails in ways that read exactly like a broken main - one
investigation already lost its time to that false alarm. The shell hook
(`.opencode/plugins/vep-env.js`) injects the Resolve variables into
every shell but it cannot choose your interpreter; only `bin/vep` does
that. If a run fails in bulk and nothing in the diff explains it, check
which python ran before doubting main.

## 1. Which entry point to call

ONE entry, always:

```
bin/vep manage_project.py build-reels <project>
```

It runs the `reels` process node by node (`build_reels`, then
`verify_reels`) through the operation registry, which checks each node's
derived requirements first and REFUSES naming what is missing. Never
call `reel_build.rebuild_reels_in_project` directly - that was the old
path, it reaches past the pipeline into a tool, and it skips every
check the registry runs for you.

The operations underneath (you do not call these by hand; the CLI
resolves to the step's own body, which calls `reel_build`):

| operation | owning node | what it does |
|---|---|---|
| `reel.build` | `build_reels` (`step_7_01_build_reels/step.py::build_reels`) | cut every APPROVED moment onto its own Resolve timeline, bad takes removed |
| `reel.ask` | `build_reels` (`step_7_01_build_reels/step.py::ask_reels`) | write every APPROVED reel's three visual asks WITHOUT building anything |
| `reel.verify` | `verify_reels` (`step_7_02_verify_reels/step.py::verify_reels`) | grade the built timelines against the plan they were built from |

## 2. The loop: ask, answer, build, verify

A reel takes two passes, and the first one is free:

1. **Ask without building.** `build-reels` runs `reel.ask` through the
   same node: it snapshots the master timeline read-only (getters only -
   no lease, no Fusion comp, no timeline created) and writes three asks
   per approved reel through ONE spelling the real build shares, so an
   ask written without a build is byte-identical to what a throwaway
   build would have written. The three: the **semantic** ask and the
   **span** ask (`reel_semantic_visual`), plus the **motion** ask
   (`reel_look`) only where the project declares the TV-frame look.
2. **Answer beside the asks.** The answers stay the model's creative
   work - nothing tools the choosing (AGENTS.md 10.5). What is derivable
   is the I/O around them: read all three asks in as few calls as you
   can, write all three answers together, do not re-read one to check
   it. The ask/answer round trip cost ~10 calls per reel where 2
   suffice.
3. **Build.** The real `reel.build` stages each reel onto a staging
   timeline, verifies, then promotes over the final name. A build holds
   Resolve's exclusive cursor lease for its whole body (measured mean
   ~9.6 min) - launch it in the background and answer the NEXT reel's
   asks while it runs. Nothing about reel N+1's take-pick or visual
   answers depends on reel N's build. Every build in the measured batch
   was a blocking foreground call; that blocked window is pure wait.
4. **Verify off the build's record.** `reel.verify` grades only what the
   build placed, addressed off the build's own `reel_build` record -
   never re-derived from `project.yaml`. A `project.yaml` edited between
   the two nodes must not send the verifier somewhere else.

A reel nothing changed about is NOT placed again
(`library/tools/reel_rebuild_need.py`, fail-closed): the build prints
the decision per reel with its reason either way. Do not pass
`--rebuild-all` unless you are measuring the pass or re-placing onto
drift-free state - it re-pays the Resolve pass for identical frames.

## 3. Which writer owns what

- `build_reels` owns the timeline: stage, place, promote. Each op's
  result is recorded under its own capability id in `pipeline_data.json`
  `capability_outputs` (`reel.build`'s `reel_build`, `reel.ask`'s
  `reel_ask` - one cannot overwrite the other), and `reel_build` is what
  `reel.verify` is handed.
- `verify_reels` owns the verdict: pass record or raise. If the build
  placed nothing because nothing needed placing, there is no staging to
  grade and the gate says so openly rather than passing on nothing
  (AGENTS.md 10.4).
- `deliver-reel` owns the rendered file: ONE reel, explicitly named.
  Rendering without naming one is refused.
- `touch-reel` owns post-build edits: a structured change through
  `composed_edit`, staged and verified - not a rebuild. It journals
  the reel first and leaves no archived copy behind.
- `undo` owns the way back: the newest touch is reversed IN PLACE from
  its journal, the newest rebuild is rolled back to the version before
  it (`library/tools/undo_journal.py`). A timeline changed since is
  refused by name.
- `watch-reel` / `hear-reel` own REPORTS on a delivered file. They
  render nothing and gate nothing; they refuse without a delivered
  file rather than starting a render.

## 4. Which CLI does what

All through `bin/vep manage_project.py`, project as slug or absolute
path:

| verb | job |
|---|---|
| `propose-reels` | publish step 3.04's chosen moments as PROPOSED (needs `--force` to overwrite a ruled proposal) |
| `build-reels` | the build loop above (ask + build + verify) |
| `drift` | compare each reel's build snapshot against its live timeline; REPORTS, never repairs |
| `shift-rows [N ...] --move ROW[,ROW]=PX --draw-gain G [--skip PREFIX]` | move named rows of accepted reels up/down by delivery pixels as one journaled touch (Tilt per item from its own media size; G measured, 1.0 on geo-podcast 2026-09-25) |
| `scale-rows [N ...] --rows ROWS --move-with ROWS (--by K \| --fit) --draw-gain G` | shrink or grow named rows about a point as one journaled touch: zoom times K and every centre pulled toward the point, riding rows keep their size (`@picture` = the camera rows under the TV frame) |
| `fit-picture [N ...] (--scale S \| --fit) --move-with ROWS --draw-gain G` | put the TV picture at a scale no phone crops: camera rows scaled, the frame swapped for the TV drawn smaller in an opaque black surround, riding rows moved; `--fit` takes the scale `safe-zones --describe` prints; then declare `pipeline.tv_frame.scale` |
| `caption-width [N ...] --draw-gain G [--max-width PX]` | narrow accepted reels' captions as one journaled touch per reel: only cards wrapping wider are re-rendered and swapped in place; the width defaults to the widest centred box clear of every platform's safe zones over the captions' rows, and captions inside a zone vertically are refused (move them with `shift-rows` first) |
| `safe-zones [N ...] [--overlay tiktok\|instagram_reels\|youtube_shorts\|linkedin\|combined] [--remove]` | put a platform UI guide on a reel on its own row, the clip switched OFF so it never renders (enable the clip to look); a rebuild drops it |
| `deliver-reel <project> <N>` | render reel N to a file |
| `watch-reel` / `hear-reel` | show/hear a DELIVERED reel against its plan; report-only |
| `touch-reel <project> <N> --edits ...` | structured edit to a built reel |
| `undo <project> [N]` (`--list`, `--entry ID`) | reverse the newest touch (in place) or rebuild (by version) |
| `variant new\|build\|list\|diff\|choose\|merge` | two live versions of one reel; choosing is an act (AGENTS.md 10.4) |
| `signoff` / `round-diff` | durable captain approval per round; promotion deletes unless retained |

`build-reels` flags (all repeatable except where noted):

- `--only-reel N`: touch one timeline; the build deletes only what it is about to place.
- `--name-suffix TEXT`: label the temporary timeline and caption assets
  while the approved reel stays live. After conformance passes, the
  staging automatically promotes to the plan's exact approved name; a
  refused or interrupted build leaves it held for that target.
- `--skip-captions`: skip subtitle rendering (saves CPU).
- `--allow-drop ROW` (or `FINAL::ROW` for one reel): a row the replace guard may let shrink. Absent means any row loss refuses the promotion - by row, never by blanket.
- `--supersede REEL`: this build may replace a reel carrying the captain's durable sign-off. Absent means it refuses by name and prints this flag.
- `--retain REEL`: retire the superseded generation to the archive instead of deleting it. Default: one timeline per reel, empty archive.
- `--rebuild-all`: place every named reel whatever the state says (see section 2 - default is to leave unchanged reels alone).

## 5. When it refuses, read the message - not the module

Every prerequisite below is CHECKED and the refusal names what is
missing. A refusal is the answer, not a prompt to go read `reel_build.py`:

- **No APPROVED moments.** Only APPROVED builds; PROPOSED fails the
  gate as REJECTED does (AGENTS.md 10.4). The approval is the captain's
  act - no flag overrides it.
- **No `timeline_transcript`.** NO STEP MAKES ONE. It is written by
  `bin/vep -m library.tools.timeline_transcript <project> --write`,
  which needs Resolve open on the project's own timeline. The keep
  ranges, the retake scan and the captions are all cut from it.
- **No `resolve` binding.** `project.yaml` must name the exact Resolve
  project and master timeline. A reel is cut FROM a master timeline and
  a near match lands on another project (AGENTS.md 5).
- **Signed-off reel, no `--supersede`.** The refusal prints the flag;
  pass it.
- **Shrinking row, no `--allow-drop`.** The replace guard refuses by
  row; declare the row.
- **`ResolveBusy`.** The build waits on the exclusive cursor lease
  (default 15 min) and then raises naming the holder. That is the lease
  serialising builds, not a stuck build - wait and re-run; raising the
  timeout is a dispatch call, not a lane call.

What NOT to do around a refusal: do not re-derive the verifier's
addresses from `project.yaml` (they come off the build record), do not
call the tool function directly to skip the check, do not `git stash`
(a shared stack - use a temporary commit).
