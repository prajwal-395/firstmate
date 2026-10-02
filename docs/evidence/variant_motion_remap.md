# Variant motion follows the picture shot

`tests/test_reel_look.py` keeps the motion remap scenarios in one small
table. `library/tools/reel_look.py::remap_motion_positions` maps motion
answers from base picture positions onto the cutaway-offset picture.

## The incident

On Reel 09, a variant inserted a cutaway into a long Akshita shot. The
motion answer was anchored to the base shot, but the inserted piece
shifted positions. Looking up the old position in the variant landed
the move on Craig's preceding picture and made a valid window appear to
fall outside its shot.

The remap matches source identity and played record span, so the answer
follows the same shot after insertion. If the cutaway splits the
anchored window, or splits a whole-shot locked move, the plan refuses
with the affected shot named rather than choosing one side.
