# OTIO placement coverage (resolution mismatch + probe out of lease) - 2026-10-08

> SUPERSEDED in one place by `2026-10-08-otio-placement-live.md`: the
> sizing-before-current path never happens on a live Resolve - the import
> makes the new timeline current during the import (measured live), so a
> mismatched import is never attempted and `auto` falls back to append
> pre-import. The marker-restoration and probe-out-of-lease halves below
> stand as written; the live doc carries the corrected numbers.

## Provenance

- Machine: Darwin 25.3.0 arm64 (Mac.attlocal.net), Apple Silicon.
- Commit: `c92823f8` base + uncommitted branch `fm/vep-otio-placement-coverage`
  (this doc is part of that branch; head SHA is printed in the PR report).
- Cold/warm: unit-level cold and warm are both covered - the subject-preparation
  test probes once per unique window (cold) and reuses the cached value on the
  second call (warm); the probe-failure test caches the refusal outside the
  lease. No model calls, no renders.
- Reproduce: `/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/state/vep-verify-run.sh bin/vep -m pytest tests/unit/reels/test_reel_otio_compatibility.py tests/unit/reels/test_subject_measure_preparation.py tests/scenarios/test_reel_otio_placement.py -q -n 2`
- Live read check (read-only, borrowed, restored with read-back):
  `bin/vep <script>` under `resolve_lease(exclusive=True)` +
  `qualification_project.borrowed()` + `switch_project(manager, "Ren Qualification")`.
  Observed 2026-10-08: open project before `Podcast (field test)`; Qualification
  default `1920x1080 @ 24fps`, 15 timelines (`Q Spine`, `Q Markers`, promotion
  proofs...); restored `Podcast (field test)` /
  `Reel 15 - the-3d-nail-art-salon-beats-the-chains` with read-back.

## What changed

- OTIO placement no longer falls back to append on resolution mismatch. The
  import inherits the project default; the placement path now sizes the
  imported, still-noncurrent timeline to the reel (`useCustomSettings=1`,
  width, height) with read-back before taking the cursor, under the existing
  per-call deadline. If Resolve already made the imported timeline current,
  the build refuses instead of risking the measured settings-sync deadlock.
  `select_placement`/`otio_incompatibilities` no longer take
  `import_resolution`; an invalid reel size is still refused by name.
- Source markers `AppendToTimeline` inherits are now restored on the OTIO path:
  the recorder captures each pooled item's markers (frame, color, name, note,
  duration, customData) and re-applies them with read-back after import,
  identifying the target item by exact timeline span. Count is recorded as
  `placement_profile["source_markers_restored"]`.
- Punch-in subject probes moved out of the exclusive lease. `prepare_reel_timeline`
  (free preparation) pre-measures each unique video window once via
  `_prepare_subject_measurements` and hands `build_reel_timeline` a
  `prepared.subject_measure` lookup; `aim_picture_row` reuses it inside the
  hold without decoding a frame. Probe failures are cached too and raised at
  the same shot-specific refusal boundary. Build record carries
  `subject_measurement_preparation_s` and `subject_measurement_count`.

## Equivalence (proven identical at unit level)

`tests/scenarios/test_reel_otio_placement.py` builds the same reel both ways
against fakes with project default `3840x2160` (the mismatch case) and asserts:

- identical timeline state: rows, items, source/record spans, transforms,
  channel mapping, item markers and timeline markers;
- both paths size before making the timeline current (2 write sets);
- `placement_profile["source_markers_restored"] == 4` (2 source markers x 2 items);
- `stream_enforcement` checked 2, unverified [];
- `profile["counts"] == {"recorded_items": 6, ...}` (unchanged shape).

`tests/unit/reels/test_reel_otio_compatibility.py` asserts `auto` selects `otio`
for both matching and mismatched defaults, sizing-before-current writes the
three settings then reads back, and refusing when the imported timeline is
already current.

## Hold time and call counts

Before (measured, `docs/OTIO_COMPILATION_MEASURED.md`): Reel 09 builder
per-item pattern 13.35 s / 457 calls vs import+restore 1.26 s / 101 calls
(import 0.40 s, restore 0.83 s); warm OTIO place hold 14.27 s vs warm append
32.40 s; cold punch-in probe 96.6 s inside a 124.6 s OTIO hold (Reel 06).

After (this change, fake-level + reasoning): the mismatch case follows the
same import+restore path instead of append, adding 3 sizing writes +
read-backs before the cursor check and one AddMarker + read-back per source
marker. Expected live hold stays near the 1.26 s import+restore shape (plus
milliseconds for sizing and marker restore) instead of the 13-32 s append
shape; a new live placement hold was NOT taken because it would change the
Qualification project default and hold the exclusive lease for minutes during
production - the sizing and marker steps are the only added Resolve calls and
both are verified by read-back in the tests above.

Subject probe: cold windows pay one probe each during free preparation
(`subject_measurement_preparation_s`), warm rebuilds pay zero probes (record
read); the exclusive hold no longer contains probe decoding for punch-in
reels.
