# A4 reference workflow: first production analyze-to-deliver, twice

Date: 2026-10-15
Worktree base: `fa16aa5f6` (detached HEAD, no code changes; this PR is the benchmark doc only)
Task: `vep-a4-reference-workflow` (Phase A4 of the 2026-10-13 consolidated plan:
"Complete the first production workflow" - run Analyze -> Edit -> Build ->
Preview -> Revise -> Render -> Verify -> Deliver on real footage, twice,
with Resolve as the render path).

## Method

Verb: `ren qualification workflow --index <lucie geo-podcast footage_index.json>
--runs 2` (default query `"marketing directors talking about marketing"`).
Each of the two runs does a FRESH `ren analyze` on the copied source
(semantic analysis, temporal index, prosody - no lane skipped), then one
broker job (edit construction, scripted human change, Ren revision,
save, Resolve render), then loudness mastering and the `verify_render`
deterministic gate.

Source selection (identical both runs, deterministic): `clip_004`,
`LCATL0011.MXF`, 214.55 s, copy reused by content hash at
`~/.local/share/vep/qualification/622307e41719/real_footage/LCATL0011-9b5ec750c8db.MXF`
(sha256 `9b5ec75...01c30bc`). Fresh-analysis hit both runs:
`clip_001` 96.39-98.95 s, placed span [91.39, 106.39] (15 s + 1 s revision
extension = 16.0 s timeline).

Resolve path: the linked-worktree guard refuses to START the production broker
(`client.ensure` raises `RenRefusal: not starting the production Resolve
broker` where no broker serves, as the 2026-10-09 lane hit). This run used an
own scratch broker on an isolated socket
(`PIPELINE_RESOLVE_LOCK_DIR=/tmp/vep-a4-reference-workflow-resolve`, Rust
`ren-resolved` rebuilt fresh from this tree, pid 12740), which the guard
explicitly permits. Pre-run read-only probe found `Ren Qualification` open
on `Q Markers` with known timelines `Q Spine`, `Q Markers`.

## Result: passed twice, 15/15 stages, zero refusals, zero in-run manual steps

Report: `~/.local/share/vep/qualification/622307e41719/workflow-runs/20261015T053235520145Z/qualification-report.json`
Ledger: `.../20261015T053235520145Z/perf_ledger.jsonl` (182 rows)
Wall time: 05:32:35 -> 06:05:35 UTC (33.0 min for both runs).

| Stage | Run 1 | Run 2 |
|---|---|---|
| source copy | passed (hash reuse) | - (shared) |
| analysis (fresh `ren analyze`) | passed, semantic 686.0 s / 18 model calls, temporal index 89.7 s | passed, semantic 581.8 s / 18 model calls, temporal index 75.8 s |
| edit construction (V1 + A1 placed, read back) | passed | passed |
| scripted human change (Blue marker, read back) | passed | passed |
| Ren revision preservation (marker intact, 2v + 2a clips) | passed | passed |
| Resolve render (H264 mp4, job Complete) | passed, started 05:50:06 UTC | passed, started 06:05:24 UTC |
| export + loudness master | passed, 23.8 MB raw / 23.5 MB master | passed, 23.8 MB raw / 23.6 MB master |
| final render QA (`verify_render`) | passed, no issues | passed, no issues |

Render QA both runs: 1920x1080, 24.00 fps, 16.02 s vs 16.00 s expected,
1 audio stream. Timelines recorded in Ren Qualification generation store:
`Q Workflow 20261015T053235520145Z 1` / `2`, 385 frames, 1 Blue marker each.
Run-1 wall ~17.5 min, run-2 wall ~15.5 min; the Resolve edit-to-export leg is
seconds per run (16 s H264), analysis dominates (fresh model passes both runs).

Heavy-work admission worked as designed: analysis phases self-admit through
`heavy_work_lock`, and the log shows orderly waits on the concurrent
`scripts/full_suite_gate.sh` (pid 5520) and sibling model tenants - no
deadlock, no manual slot management.

## A4 acceptance criteria, with evidence

- Original project recoverable: Ren Qualification intact; `Q Spine` / `Q Markers`
  untouched; only the two `Q Workflow ...` timelines added. Each broker job
  recorded its `borrowed_from` project/timeline and restored it on the way out,
  including on the failure path (`borrowed` context manager).
- Traceable receipts: build / human-change / revision receipts with idempotency
  keys (`qualification-build:<job>`, `qualification-human:<job>`,
  `qualification-revision:<job>`), Resolve render job UUIDs, mastering record,
  and `verify_render` receipts under each run's `pipeline_output/skill_runs/`.
- Revisions preserve unrelated edits: preservation readback asserted the Blue
  marker name and the 2-video + 2-audio clip count after the revision, both runs.
- No manual intervention on the supported path: the workflow ran end to end
  with no review gate, no retry-by-hand, no Resolve UI touch. The only human
  actions in the whole task were pre/post-run setup (below).
- Final video and audio pass automated QA: `verify_render` green both runs
  (resolution, framerate, duration, audio stream, no issues); loudness mastered
  against the delivery spec before QA.
- Human-inspectable + revisable: master mp4s at
  `.../20261015T053235520145Z/exports/Q_Workflow_20261015T053235520145Z_{1,2}_master.mp4`;
  the scripted Blue marker stands in for the editor's retained change and
  `ren revise` remains the live-revision path (not exercised here).

## Refusals

None during either run. The single refusal in this task was the worktree
broker-start guard before the run (no broker serving on the default socket),
handled by design with the isolated-socket scratch broker above.

## Manual steps a human took

1. `PIPELINE_RESOLVE_LOCK_DIR=/tmp/vep-a4-reference-workflow-resolve bin/vep
   -m library.tools.resolved ensure` - start the own scratch broker (one command).
2. One read-only `resolve-axi run` probe of the open Resolve state before the
   run (found Ren Qualification / Q Markers).
3. One read-only probe after the run, which failed benignly: by then another
   lane had `Ren Scratch - EVAL04 visual 2997` open with NO timeline, and the
   probe script assumes a current timeline (`ResolveUnavailable: Resolve
   project 'Ren Scratch - EVAL04 visual 2997' has no timeline open`). No writes
   were made; state was verified through recorded generations instead.
4. Zero manual steps inside either run.

## Findings (no code change in this PR; reported with the exact call)

F1. Cross-socket lease blindness (scheduling, for firstmate). The isolated-socket
broker's lease does not arbitrate against other sockets' brokers: the EVAL04 lane
switched Resolve onto its scratch project mid-run (both jobs' `borrowed_from`
read `Ren Scratch - EVAL04 visual 2997`). Both sides' read-backs passed and both
of my renders are byte-plausible and QA-green, but two lanes driving one
Resolve instance concurrently is unarbitrated by design today. "The lease
arbitrates" holds per socket only.
F2. Run-1 vision window [70-80 s]: `JSON parse failed after retry`, then the
window still emitted 1 action / 1 scene / 1 mode (74.0 s inference vs ~19-23 s
for sibling windows). Self-recovered, no data loss; a robustness follow-up for
the vision pipeline, not a blocker.
F3. `resolve-axi run` probe scripts should tolerate a project with no open
timeline (exact failure in Manual step 3). Cosmetic; the probe was ad-hoc, not
repo code, so nothing to fix here.

Other observations, not defects: GEMMA SERVER unreachable fell back to
in-process `mlx-community/gemma-4-12b-it-4bit` as designed (both runs);
face presence measured 0% and reported as such (no default masquerading as
measurement); `av` vs `cv2` duplicate-`libavdevice` objc warnings are upstream
packaging noise.

## Fixes in this PR

None required: the supported path passed twice on unmodified HEAD, so there was
no blocking defect to fix. This PR delivers the benchmark doc (this file).

## Reproduce

```
PIPELINE_RESOLVE_LOCK_DIR=<isolated dir> bin/vep -m library.tools.resolved ensure
PIPELINE_RESOLVE_LOCK_DIR=<isolated dir> bin/ren qualification workflow \
  --index "$HOME/Documents/content_stuff/video_projects/lucie/geo-podcast/pipeline_output/scratch/footage_index/footage_index.json" \
  --runs 2
```

Full stdout/stderr of this task's run: `/tmp/vep-a4-workflow-run.log` (2120 lines).
