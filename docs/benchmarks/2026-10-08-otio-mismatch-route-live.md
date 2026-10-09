# Live mismatch OTIO route: placement without the fallback - 2026-10-08

Follow-up to `2026-10-08-otio-placement-live.md`, which restored the
append fallback because the import makes the new timeline current and
a wrong-sized current timeline cannot be repaired. What ships instead:
where the project default does not match the reel, the importer
switches the project default to the reel size for exactly the import,
parks the cursor on the previous timeline, sizes the imported (now
non-current) timeline to CUSTOM under the per-call deadline, returns
the cursor to the imported timeline, and restores the project default
read-back. Net mutation: none. No write ever lands on a current
timeline. `auto` proves the route (`prove_mismatch_route`) before
anything is recorded and falls back to append where it cannot be
proven; explicit `otio` refuses there rather than mid-import.

## Provenance

- Machine: Darwin arm64 (Mac.attlocal.net), Apple Silicon.
- Code: branch `fm/vep-otio-mismatch-placement`.
- Resolve: live, broker-served (shared `ren-resolved` responsive, not
  busy, no lease held at entry), inside
  `qualification_project.borrowed()`: borrowed from `Podcast (field
  test)` / `Reel 01 - geo-is-comprehension-not-position`, switched to
  `Ren Qualification` read back by name, captain's project + timeline
  restored with read-back (`borrowed` exited without raising).
- Cold/warm: qualification media rendered once (`ensure_media`);
  placement sections ran once each (tiny synthetic plan, no
  grades/comps/look).
- Reproduce (unit half): `bin/vep -m pytest
  tests/unit/reels/test_reel_otio_compatibility.py
  tests/scenarios/test_reel_otio_placement.py -q -n 2`
- Reproduce (live half): the A/B script is one-off scratch at
  `/tmp/otio-mismatch-proof/proof.py` (outside the worktree, as
  required); `result.json` beside it. Heavy run went through
  `state/vep-verify-run.sh`.

## Procedure (Ren Qualification, read-only except noted)

Free (no lease): `ensure_media` (qual_a/b/c). One exclusive hold,
borrowed: project default read (1920x1080 recipe), stale probe
timelines from an interrupted run deleted by exact name (2), qual
clips pooled by reuse-or-import, project default set to 3840x2160
with read-back (the mismatch; the captain default).

- A (append, timed): production `build_reel_timeline`
  `placement_mode="append"` for a 2-angle 1080x1920 V1/V2 + A1/A2
  plan, 48 f/clip, no captions/overlays/look.
- B (mismatch OTIO, timed): production `build_reel_timeline`
  `placement_mode="auto"` for the same plan; free check selected
  `otio`, the probe proved the switch, the route ran.
- Diff A vs B field by field (row names, item counts, record spans,
  Pan/Tilt/ZoomX/ZoomY to 1e-4, pool file paths, timeline size,
  duration).
- Cleanup (own debris only, verified): both probe timelines deleted
  (31 timelines before and after), project default restored to the
  1920x1080 recipe with read-back.

## Numbers

| Section | wall in hold | notes |
|---|---|---|
| A append (4 items + full build) | 4.280 s | per-item pool + FPS + append + transforms + links |
| B mismatch OTIO (4 items + full build) | 1.241 s | of which phases below |
| B `timeline_import_s` | 0.313 s | the one import |
| B `mismatch_default_switch_s` | 0.001 s | project default to reel size, read back |
| B `mismatch_cursor_park_s` | 0.174 s | cursor to previous timeline |
| B `mismatch_custom_size_s` | 0.117 s | non-current custom sizing + verify |
| B `mismatch_cursor_return_s` | 0.137 s | cursor to imported timeline |
| B `mismatch_default_restore_s` | 0.016 s | project default restored, read back |
| borrow + switch + settle + restore | ~22 s | common overhead outside the sections |

Route overhead over a matching-default import: ~0.45 s of phases
plus the pre-record probe (~13 Resolve calls, all reverted). Call
delta counted from the implementation: probe 13 + switch 6 +
park/return 4 + size+verify 8 + restore+verify 10 = ~41 calls over
the matching path's 101 (Reel 09 scale) - against append's 457, most
of them per-item. The plan is tiny on purpose; the production-scale
reference stays Reel 09: append 13.35 s / 457 calls vs matching
import + restore 1.26 s / 101 calls, so the mismatch route projects
to ~1.7 s / ~142 calls.

## Findings

1. **The mismatch route lands custom-sized with no net mutation.**
   Imported timeline reads `useCustomSettings=1`, 1080x1920; project
   default read 3840x2160 after the route (the setup value) and
   1920x1080 recipe after cleanup; 31 timelines before and after;
   cursor ended on `MM OTIO Probe` (the reel's timeline, where the
   default path leaves it too); captain's project + timeline restored
   with read-back.
2. **Field diff A vs B is empty**: rows, items, spans, transforms,
   pool files, timeline size, duration - all identical. The live
   import reported every item unlinked (`GetMediaPoolItem()` None, as
   measured 2026-10-06) and production's `relink_imported_items`
   re-placed all 4 through the pool before the diff.
3. **No timeline-setting write ever landed on a current timeline.**
   The sizing writes ran while the cursor was parked on the previous
   timeline (the append probe's); the unit half asserts this per
   write (`refuse_if_current` on all four reel shapes).
4. **The park target was the append probe's timeline.** Qualification
   had no current timeline at entry; the append build ran first and
   left one current. With nowhere to park, the probe falls back to
   append (unit-covered) - the route needs one current timeline.

## Limits

- Tiny synthetic plan (4 items, no captions/audio spill/grade):
  proves the mechanism and the refusal paths, not
  production-scale holds. Four reel shapes (captions x semantic
  rows) prove field-identical placement in
  `tests/scenarios/test_reel_otio_placement.py` on the double with
  the live landing modelled.
- Fakes model the landing only where `otio_import_lands_current` is
  on; the live run above is the coverage for the real landing.
- Broker-wide KPI attribution was not attempted (other lanes active);
  holds are per-section wall time inside one exclusive lease.
