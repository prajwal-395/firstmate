# Live OTIO before/after: mismatch fallback + matching import - 2026-10-08

Follow-up to `2026-10-08-otio-placement-coverage.md`, whose unit-level
story this run corrects: the sizing-before-current path never happens on
a live Resolve (see finding 1). What ships instead: `auto` falls back to
append on a mismatched project default (as before 2181), explicit `otio`
refuses before importing, and the post-import check makes no write when
the import already reads back the reel size.

## Provenance

- Machine: Darwin 25.3.0 arm64 (Mac.attlocal.net), Apple Silicon.
- Code: branch `fm/vep-otio-placement-coverage` (follow-up to merged 2181).
- Resolve: live, broker-served (`ren-resolved` responsive, exclusive lease
  via `resolve_lease`), inside `qualification_project.borrowed()`:
  borrowed from `Podcast (field test)`, switched to `Ren Qualification`
  read back by name, captain's project + timeline restored with read-back.
- Cold/warm: subject-probe preparation ran cold (detector decoded frames:
  1.675 s for 2 windows, outcome `measured`); warm reuse added zero
  detector calls. Placement sections ran once each (warm cache N/A - tiny
  synthetic plan, no grades/comps).
- Reproduce (unit half): `bin/vep -m pytest
  tests/unit/reels/test_reel_otio_compatibility.py
  tests/unit/reels/test_subject_measure_preparation.py
  tests/scenarios/test_reel_otio_placement.py -q -n 2`
- Reproduce (live half): the A/B script is not checked in (one-off
  scratch under `/tmp`); the procedure is fully described below and every
  number comes with its counting rule. Heavy runs went through
  `state/vep-verify-run.sh`.

## Procedure (Ren Qualification, read-only except noted)

Free (no lease, 0 Resolve calls for compile): `ensure_media` (qual_a/b/c),
`_media_facts` (ffprobe, 0.046 s), `otio_compile.compile_timeline` + write
for a 3-clip 1080x1920 V1 plan, Zoom 1.5 only (Zoom is identical in both
vocabularies; Pan/Tilt follow the project on mismatch), 48 f/clip
(0.0 s). Subject preparation via `_prepare_subject_measurements` on two
2 s windows (1.675 s cold, 2 detector calls; reuse: still 2).

One exclusive hold, borrowed: project default read (1920x1080), qual
clips found pooled (no import needed), one source marker placed on the
pooled qual_a item with `customData` and read back (PoolItem.AddMarker
works live), project default set to 3840x2160 with read-back (mismatch).

- A (append, timed, calls counted): `CreateEmptyTimeline`, make current,
  production `apply_timeline_resolution` to 1080x1920, per item: FPS read,
  `AppendToTimeline`, ZoomX/ZoomY set + read-back. Full state read-back.
- B-select (free): production `select_placement` on the mismatch
  (auto -> append + reason; explicit otio -> (otio, reasons)); asserted
  zero Resolve calls and zero timelines created.
- C (matching default 1080x1920 set transiently, timed, counted):
  select -> (otio, ()); `ImportTimelineFromFile` (`importSourceClips:
  False`); production `size_imported_timeline_before_current` with
  `apply_timeline_resolution` stubbed to raise (proving no write);
  make current + read-back; production `restore_pool_markers` with
  hand-built specs carrying the pool marker; full state read-back.
- Diff A vs C field by field (record spans, source starts, ZoomX/Y,
  Pan/Tilt to 1e-4, markers incl. `customData`, settings, duration).
- Cleanup (own debris only, verified): both timelines deleted (18
  timelines before and after), pool marker removed (read-back empty),
  project default restored to the 1920x1080 recipe value with read-back.

## Numbers

| Section | wall in hold | counted Resolve calls |
|---|---|---|
| free compile + write | 0.0 s | 0 |
| free probe prep (cold, 2 windows) | 1.675 s | 0 |
| setup (reads, marker, default switch) | 0.019 s | 57 |
| A append (3 clips + verify reads) | 0.422 s | 55 |
| B select checks (free) | 0.002 s | 0 |
| C import + restore (3 clips + verify reads) | 0.259 s | 43 |
| cleanup | 0.165 s | 44 |
| probe detector calls inside hold | - | 0 |

Call counters cover the placement sections only (plus one pool-handle
acquisition counted under free); borrow/switch/settle machinery is common
overhead outside them. Both sections include my verification read-backs
(~27 reads each), so placement-only is ~28 (A) vs ~16 (C). The plan is tiny
on purpose; the production-scale reference stays Reel 09: append
13.35 s / 457 calls vs import + restore 1.26 s / 101 calls. Append grows
per item (lookup + FPS + append + 2x(set + read-back)); the import path is
one import plus per-item read-backs.

## Findings

1. **The import makes the new timeline current during the import**
   (observed twice, `import_landed_current: true`). The 2181 sizing path
   (size a noncurrent import) therefore refuses live - and repairing a
   wrong-sized current timeline is the measured settings-sync deadlock.
   Consequence: a mismatched import must never be attempted. Fixed in
   this branch: `auto` falls back to append pre-import (as before 2181);
   explicit `otio` refuses pre-import (`project timeline resolution is
   3840x2160...`); the post-import check reads back and writes nothing
   when the import already matches.
2. **Matching-default OTIO works end to end and spins nothing extra**:
   1 marker restored through the production restore (`b_markers_restored:
   1`), size check provably write-free, no fallback, no debris.
3. **Field diff A vs C**: record spans identical, transforms identical,
   markers identical (inherited vs restored, `customData` included).
   Two documented non-differences: `useCustomSettings` reads `1` (append
   sizes explicitly) vs `0` (import inherits the matching default) with
   identical 1080x1920 dimensions; `GetSourceStartFrame` reads `0` on
   appended items vs `None` on unlinked import items - production's
   `relink_imported_items` re-appends those through the pool (covered by
   `test_unlinked_import_is_relinked_to_the_pooled_clip`), which this
   scratch script skipped.
4. **Probe outside the hold**: free preparation 1.675 s cold, warm reuse
   zero, and zero `measure_subject_in_window` calls across the entire
   exclusive hold (was: 96.6 s of a 124.6 s hold, Reel 06 cold).

## Limits

- Tiny synthetic plan (3 clips, Zoom-only, no audio rows/captions/grade):
  proves the mechanism and the refusal, not production-scale holds.
- Fakes (`tests/resolve_double.py`) do not model cursor theft on import;
  the live run above is the coverage for it.
- Broker-wide KPI attribution was not attempted (other lanes active);
  holds are per-section wall time inside one exclusive lease.
