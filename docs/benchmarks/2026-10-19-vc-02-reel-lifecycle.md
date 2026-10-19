# VC-02 reel lifecycle profile baseline

Status: **measured live 2026-10-19** after the firstmate Resolve go decision
(steer `001.msg`). Ten workflows ran each as one locked borrowed session in
Ren Qualification from `Podcast (field test)` as the open project, restored
after every session, on one controlled disposable reel (`VC-02 disposable`,
duplicated for the multi-reel workflow, both deleted at the end).
`ren qualification reset` never ran.

## Measurement protocol

Harness: `scripts/vc02_live_profile.py` (committed). Each workflow holds the
exclusive Resolve lease, borrows Resolve (`qualification_project.borrowed`),
switches to Ren Qualification, works only on `VC-02 disposable*` timelines
(any other project or timeline refuses before the first mutation), verifies by
read-back, and restores the captain's project. Mutations go through verified
`EditPatch` receipts (`apply_live_patch`); a non-committed receipt raises and
fails the workflow. Ledger: one `perf_ledger.jsonl` per campaign; aggregate
per workflow with `scripts/profile_reel_lifecycle.py`. Raw aggregates:
`docs/benchmarks/2026-10-19-vc-02-reel-lifecycle.jsonl` (one row per
workflow, `run_id` inside).

Run ids (ledger `/tmp/vc02_ledger.jsonl` at capture time):

- `vc02-no_op-20261019T021756`, `vc02-single_property_40px-20261019T021953`,
  `vc02-lower_third_replacement-20261019T021953`,
  `vc02-caption_text_timing_fix-20261019T021953`,
  `vc02-clip_insert_replace-20261019T022804`,
  `vc02-ripple_edit-20261019T022804`, `vc02-full_rebuild-20261019T022804`,
  `vc02-undo_redo_recovery-20261019T022804`,
  `vc02-rebuild_with_human_edits-20261019T022804`,
  `vc02-multi_reel_one_changed-20261019T022952`.

Wall is the whole borrowed session (project switch settle, save/restore
included); CPU and peak RSS are the harness process (Resolve's own process is
not measured here); API calls are counted `resolve_api_call` spans; lease
queue wait vs exclusive hold come from the lease rows; a missing value is
reported as unmeasured, never zero.

## Baseline

| Workflow | Wall | CPU | Peak memory | Bytes read / written | Resolve API calls | Timeline switches | Lease queue wait | Exclusive lease hold | Outcome |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| No-op | 49.0 s | 0.08 s | 40.6 MB | unmeasured | 308 | 0 | 36.42 s | 12.55 s | already satisfied, no mutation |
| Single property edit (40 px) | 12.9 s | 0.32 s | 64.6 MB | unmeasured | 180 | 1 | 0.01 s | 12.89 s | moved 40 px, verified |
| Lower-third replacement | 12.6 s | 0.04 s | 64.6 MB | unmeasured | 284 | 0 | 0.15 s | 12.47 s | refused: no title route |
| Caption text/timing fix | 12.4 s | 0.04 s | 64.6 MB | unmeasured | 127 | 0 | 0.02 s | 12.39 s | refused: no caption route |
| Clip insert/replace | 13.2 s | 0.32 s | 53.6 MB | unmeasured | 243 | 1 | 0.01 s | 13.14 s | inserted + replaced, verified |
| Ripple edit | 13.4 s | 0.26 s | 53.6 MB | unmeasured | 360 | 1 | 0.02 s | 13.36 s | rippled +48 f downstream, verified |
| Full rebuild | 12.9 s | 0.17 s | 53.6 MB | unmeasured | 360 | 1 | 0.01 s | 12.87 s | rebuilt, matches base |
| Undo/redo/recovery | 12.8 s | 0.16 s | 53.6 MB | unmeasured | 291 | 1 | 0.14 s | 12.68 s | edit + inverse undo, base restored |
| Rebuild with prior human edits | 13.0 s | 0.71 s | 53.6 MB | unmeasured | 361 | 1 | 0.01 s | 12.95 s | rebuild kept the human note, verified |
| Multi-reel build, one changed reel | 13.2 s | 0.26 s | 52.9 MB | unmeasured | 667 | 1 | 0.12 s | 13.10 s | one reel changed, other verified untouched |

Per-phase wall (seconds) inside each session - planning / resolve_ops /
verification (staging only where a candidate is duplicated):

| Workflow | Planning | Resolve ops | Verification | Staging |
|---|---:|---:|---:|---|
| No-op | 0.02 | 0.24 | 0.02 | - |
| Single property edit (40 px) | 0.02 | 0.57 | 0.00 | - |
| Lower-third replacement | 0.02 | 0.19 | - | - |
| Caption text/timing fix | 0.01 | 0.14 | - | - |
| Clip insert/replace | 0.02 | 0.92 | 0.00 | - |
| Ripple edit | 0.03 | 1.13 | 0.01 | - |
| Full rebuild | 0.02 | 0.64 | 0.00 | - |
| Undo/redo/recovery | 0.01 | 0.41 | 0.00 | - |
| Rebuild with prior human edits | 0.02 | 0.80 | 0.00 | - |
| Multi-reel build, one changed reel | 0.01 | 0.72 | 0.01 | 0.38 |

The cleanup session (not a baseline row) exported both disposables to `.drt`
(29,646 + 29,269 bytes), deleted both timelines, and `qp.verify` reported the
Qualification project still matching its declaration.

## What the numbers say

- Session overhead dominates: ~11-12 s of every ~13 s session is project
  switch settle plus save/restore; the edit work itself is 0.1-1.1 s. Any
  optimization that does not touch the borrow/switch path cannot move the
  session wall (VC-26).
- The no-op session queued 36.4 s behind another lane's Resolve hold
  (`lost_ratio` 0.74) while the other nine queued 0.01-0.15 s. Contention,
  not op cost, is the wall variance.
- `cache`, `model_access`, `promotion`, `version_commits` have zero rows on
  all ten workflows: no model or render cache sits in these paths, and a
  disposable reel is never promoted or versioned. Bytes are unmeasured (no
  byte-counting producer on these paths except the `.drt` export above).
- Two workflows refused with an actionable unblock, measured at the same
  cost as running: no title op/verb/source/item exists for lower-thirds, and
  no subtitle track plus no caption op exists for captions. The unblock for
  both is a fixture reel carrying the element plus a qualified op.

## Findings for the routing lane (not baseline)

- `clip.insert` is declared by `reel.build`, not `reel.touchup` (which
  declares `clip.delete`, `clip.set_property`, `marker.add` and the rest but
  not `clip.insert`). A delete+insert plan must split into one patch per
  capability; a mixed patch is refused. The fix hint on the refusal names
  `affected_spans`, but the cause is the capability declaration.
- An insert whose source span exceeds the media length commits (presence is
  verified) but lands appended at the reel end instead of at the stated
  record frame. The harness clamps every source span to the measured media
  length; position read-back stays the workflow's own check. Recommends a
  position term in the patch presence verify.
- `timeline.DuplicateTimeline` moves the cursor onto the new timeline; the
  cursor fence correctly fires on exit unless the caller restores the cursor
  first. The harness restores then fences.
