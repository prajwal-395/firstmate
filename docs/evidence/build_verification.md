# `step_6_01_render/build_verification` - the two verification defects

Moved from the module docstring of `tests/test_verification_verdict.py` (2026-10-02).

Defect 1: `verification_passed` was hardcoded True and never derived from QA
station outcomes. A failing station printed "Station fusion_comps: Failed" but
the build returned `verification_passed = True`. Fixed by
`derive_verification_verdict`.

Defect 2: the Fusion subprocess only processed V1 clips. Per-clip effects
planned for B-roll on V2 (e.g. broll_1, broll_4, broll_8) were silently dropped
with no error, warning, or explanation. The pass now walks both tracks
(`library/tools/execution/fusion_tracks.py`); `detect_unreachable_fusion_effects`
still catches a label that was never placed at all.

Two source-grep tests (no literal `all_passed = True` line in
`resolve_build_timeline.py`; `results["success"]` not computed from the
verification) were removed on 2026-10-02 as source-text pins.
