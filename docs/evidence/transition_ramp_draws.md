# A planned transition ramps over its planned frames, and stops

Tests: `tests/unit/picture/test_transition_ramp_draws.py` (moved from its module docstring, 2026-10-02).

A planned transition ramps over its planned frames, and stops.

This counts the frames a transition is actually DRAWN on, by reading the
comp the renderer writes and evaluating its splines over the frames
Resolve renders for that clip.  It does not assert that a plan exists:
the plan was always right, and that is exactly what made the defect
invisible for the whole life of the drawn-transition route.

The numbers below are project 001's own, off the run of record of
2026-08-26 - the run whose master the captain marked "visibly actually
blurry ... because there was a distinct blur effect on it".  That run
planned two 15-frame ``defocus`` transitions and drew:

    cut 8  tail  speech_7_seg0   0 of 15 planned frames
    cut 8  head  speech_9_seg0   0 of 15, and 71 frames of full defocus
    cut 13 tail  speech_12_seg0  0 of 15 planned frames
    cut 13 head  speech_14_seg0  0 of 15, and 260 frames of full defocus

331 frames - 18.6% of a 1783-frame video - carrying an effect nobody
planned.  See library/tools/fusion/played_window.py for how that was
measured off the shipped master, and docs/RULE_EVIDENCE.md.

Issue #202 (``zoom_blur`` "is held for the whole clip and leaves the
frame uncovered") is the SAME defect on a different transition type, so
its geometry is a case here rather than a second test.
