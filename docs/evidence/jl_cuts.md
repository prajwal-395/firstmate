# Jl cuts

History moved out of test module docstrings; the tests keep the invariant.

## `tests/unit/reels/test_jcut_lead.py` (moved from its module docstring, 2026-10-02)

Sound may LEAD picture: a J-cut lead is declarable and bounded.

Captain's ruling 2026-09-08: a riser can swell BEFORE the cut it belongs
to rather than starting on it. Today SFX are anchored strictly to cut
boundaries. The declaration is `lead_seconds` on an `sfx_creative` entry:
the whole sound starts that many seconds earlier, at the same duration,
so a build arrives early instead of landing on the cut.

Two boundaries refuse it, and both are about not reaching into a moment
the plan did not name:

  * past the previous cut - the lead start must not be earlier than the
    timeline start of the nearest preceding spine block. A sound that
    starts before the previous cut spans two boundaries and belongs to
    neither block.
  * off the top of the reel - the lead start must not be negative. The
    first block has no previous cut, so zero is its only boundary.

A non-numeric or negative lead is refused the same way: a lead that is
not a positive number of seconds states no timing.

This is purely a placement offset - `compile_manifest` reads
`timeline_in` / `timeline_out` / `source_in` whatever produced them, so
the mix step needs to know nothing. And it composes with the atmospheric
layer: a reasoned `role: "layer"` with a lead is the riser swelling
before the cut, under the previous block's tail, unmoved by speech
avoidance.
