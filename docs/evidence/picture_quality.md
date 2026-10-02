# Picture quality: usable ranges are measured

Tests: `tests/test_picture_quality.py`.

Two things are under test here.

The NON-NEGOTIABLE, and the reason the issue was raised: when nothing
measured a clip, `usable_ranges` must not claim the whole clip.  It used
to read `[[0, full_duration]]` beside `usable_ranges_method: "unmeasured"`
- one field contradicting the three next to it - and the B-roll selector
reads that field to decide which 2.5 seconds of a clip to cut.  On the
2026-08-26 run of project 001 it cut `clip_006` 1.65-4.15s, a whip pan out
of a moving car window, and the master at 25.0s is motion-blurred asphalt.

The MEASUREMENT that replaces the assertion: a sharpness estimate sampled
straight off the video file, which needs no temporal index and therefore
works on a first run - see `library/tools/analysis/picture_quality.py` for
what its sampling rate can and cannot resolve.

Calibration on project 001's own footage
────────────────────────────────────────
Sampled at 5 Hz with the short side bounded to 180px, 17 clips / 807s:

    clip      file           dur     thr   soft   soft ranges (s)
    clip_006  IMG_1811.MOV  22.87   166.3  16.6%  0.0-2.0, 2.8-3.6, 7.2-8.2
    clip_008  IMG_1813.MOV   9.07   512.9  48.5%  3.8-6.2, 6.8-7.8, 8.0-9.0
    clip_007  IMG_1812.MOV 139.13   352.6  15.5%  16 ranges
    clip_011  IMG_1816.MOV 188.58   206.6   2.8%  5 ranges
    ...8 of the 17 come back with no soft range at all.

Verified by eye against extracted frames: clip_006 @3.15s (the source
frame behind the master's 25.0s) and clip_008 @5.0s are both unreadable
motion blur and both fall inside a reported range; clip_006 @5.0s and
clip_008 @2.0s are sharp and legible and neither does.  Cost: 2.79s per
clip, 0.059x realtime.
