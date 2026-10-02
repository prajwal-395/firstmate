# Rung 7 precision vocabulary on step 4.02

Tests: `tests/unit/picture/test_plan_transitions_precision.py` (moved from its module docstring, 2026-10-02).

Rung 7 precision vocabulary on step 4.02 (K1: TR3.1, TR3.2, C3.1).

Three gaps the execution-frontier scout measured on real runs:

1. Transition holds are feel words only (`duration_feel` 0/6/10/15 f),
   so TR3.1's "12-frame cross dissolve" and C3.1's "12f dissolve" had
   no plan spelling. `duration_frames` carries the stated count; both
   stated and disagreeing refuses.
2. The end of the piece has no slot: TR3.1's "1s dip to black out of
   the final shot" failed the compile ("does not sit at the end of
   any V1 clip") and resolve-axi refuses end transitions although the
   API places one. `cut_point_position: "end"` resolves the tail-only
   / end-placed spec the compile routes to the end build.
3. J/L offsets ride in seconds only. `lead_frames` / `lag_frames`
   (read by library/tools/jl_cut.py) carry TR3.3's "20 frames".
