# Effect windows are frame-exact (finding 27); unmapped comps fail by name (finding 15)

Tests: `tests/test_vfx_window_frame_exact.py` (moved from its module docstring, 2026-10-02).

Effect windows are frame-exact (finding 27), and unmapped comps fail
by name (finding 15).

Finding 27: a VFX planned on block 4 (V2 starts at frame 767) was
drawn on the b-roll AND over the whole preceding 10 s Casey A-roll
clip (`[V1:3] speech_3_seg0: zoom 1.000->1.040`): V1 block 3 ends at
frame 768, so a one-frame seconds-to-frames overlap put the
neighbour inside the effect window, and the legacy re-derivation in
the Fusion pass matched on the start edge alone while
compile_manifest had keyed the same entry to the b-roll by label -
two writers disagreeing.

Finding 15: a Fusion comp that fails to map is dropped while the
step stays green ("Fusion .comp: 1 VFX" then nothing, QA flagging
"expected a comp on speech_12_seg0, got no comp" with no failure).

The fix is single-writer plus frame-exact plus named failure: with
per_clip present compile is the only writer, the legacy fallback
matches only a window sitting WHOLLY inside one placed item, and a
planned comp reaching no timeline item fails the pass naming the
clip.

These tests use plain fakes - no Resolve writes.
