# Cleanup manifest - stale 001 / field-test artifacts (2026-09-08)

Scope rule (captain, 2026-09-08): delete ONLY what is stale AND traceable to
the 001 testing or the Podcast (field test) work. Everything else PROPOSED.

## DELETED - Resolve timelines (via scripting API, 16 total)

Project `Pipeline_Edit` (kept: 1 timeline `Pipeline_Edit` + whole project,
because it carries the captain's blue marker "why are the subtitles so big?"
and their Text+ block per 001/project.yaml 2026-08-29 ruling):
- FramingProof_a_letterbox, FramingProof_b_fill_centre, FramingProof_d_fill_wrongsign,
  FramingProof_c_fill_subject, FramingProof_qa_live, FramingProof_e_fill_centre_face,
  FramingProof_f_fill_tracked_face, FramingProof_g_letterbox_face (0 markers, 2 items each;
  documented in docs/PIPELINE_PLAN.md as leftover proof timelines; media incl. 001 footage)
- Gate_A_comp_present, Gate_B_comp_missing (0 markers, 2 items each)
- P51_Scope (0 markers, 5 items)
- pytest_capture_60884_63a57d03 (0 markers, 1 item; matches
  tests/test_marker_capture_against_resolve.py naming pytest_capture_{pid}_{hex8})

Project `Pipeline_Edit_Replanned` (kept: timeline `Pipeline_Edit_Replanned`,
the Sep-03 master build measured in library/tools/master_loudness.py):
- Pipeline_Edit_Replanned_ABORTED_20260903_oomkill (crashed build, only a
  pipeline-generated purple Master Limiter marker from resolve_build_timeline.py:1570)
- TestProject_01 (0 items, 0 markers)
- LiveTestProject_01, LiveTestProject_02 (0 items, pipeline marker only)
- Pipeline_Edit_Replanned_20260903_155448_58s (superseded 58s test rebuild;
  lowest-certainty item, flagged)

Guard: every deleted timeline was marker-checked; any non-pipeline marker
would have refused the batch. No timeline carrying captain content was removed.

## DELETED - /tmp scratch (88,475,915 bytes reclaimed)
- /tmp/vep-rebuild-nineteen (70,802,281; field-test reel coverage + 4x Project_*.db copies)
- /tmp/vep-current-state (17,668,021; field-test Project_copy.db + debug scripts)
- /tmp/vep-rebuild-before.json (2,858) + /tmp/vep-rebuild-after1.json (2,755;
  both `"project": "Podcast (field test)"` snapshots)
- /tmp/vep-001-scratch (0 bytes; empty 001-named scratch dirs)

## FIELD TEST PROJECT - verified, NO moves needed
- 53 timelines before and after; name-hash 4b146128c158de5d both sides (API + DB copy agree)
- 66 folders before and after; tiers intact: Fully approved 15, 50-50 4, Didn't make the cut 28
- VOX test: 77 items = 3 review timelines + 74 caption segments PLACED on those
  timelines (checked via GetMediaPoolItem per vox timeline). Brief assumed residue;
  measurement says none. Nothing moved.
- `Not placed on any timeline` (1210 unplaced sub_ renders): correct home per
  library/tools/resolve_organization.py (filed by timeline fact, never filename).
  Left as-is. Empty per-reel bins are that module's known accepted byproduct.
- Master `GEO Podcast - Synced` untouched; current timeline restored to Reel 19.

## PROPOSED (captain decides) - moved here by the narrowed scope
- fm_tightbox_probe: provably ours (tightbox Sep-08 scratch probe, `fm_` prefix) but
  tied to subtitle-tightbox work, NOT to 001/field test. Was delete-list under wide rule.
- Pipeline_Edit + Pipeline_Edit_Replanned PROJECTS: 001-traceable but each keeps one
  live timeline (captain-annotated / master build). Was delete-list under wide rule.
- Test1 (tl created 2024-11-22), trial and error (2021-05-28), plz work lol (2024-12-08),
  trials/poetic_1 (2021-05-28), .poetic_1 (2021-05-27): test-shaped names, but all
  predate pipeline work (Aug 2026+) - cannot tie to 001/field test.
- /tmp/vep-region-demo (5.1M frame PNGs, no tie), /tmp/vep-deletion-manifest.txt
  (another lane's 540-file plan, NOT executed), /tmp/vep-head.md (another brief),
  content_stuff/video_editing_pilot/_stage_tmp/vep_src.tgz (7.3M source snapshot, no tie).
- Field-test empty bins (Archive/Current plan/Earlier plans/Reels = 0 items; 4 empty
  per-reel caption bins): resolve_organization.py forbids the engine cleaning these.

## Test economy
No repo code changed. Per the captain's rule: ran nothing. Saying so.
