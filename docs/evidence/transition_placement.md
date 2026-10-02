# Transition placement

History moved out of test module docstrings; the tests keep the invariant.

## `tests/unit/reels/test_reel_placement.py` (moved from its module docstring, 2026-10-02)

A beat snap may adjust a cut. It may not move it somewhere else.

On project 001 the transition planned for the end of the 2.4s hook was
placed at **0.196s** - the end of that block's FIRST word - and the cut
planned for 18.37s moved to 11.33s, which would have dropped seven
seconds of speech. `resolve_cut_point` scanned each block's word ends
from the beginning and took whichever one happened to land on a beat.

Two things made it survive: the record said `snap_delta_seconds: 0.0`
throughout (that field only measures the `snap_to_beat` path, not
word-end matching), and it only ever affected the transitions that get a
Fusion comp, so a run with nothing but hard cuts looked fine. It was
caught by `compile_manifest` refusing the manifest - "Transition
trans_001 at 0.196s does not sit at the end of any V1 clip" - one step
before the render.
