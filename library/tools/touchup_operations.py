"""Canonical operation names accepted by reel touch-ups.

This tiny module has no Resolve or journal imports. The touch gate and the
undo journal can both read the vocabulary without importing each other while
one of them is still being initialized.
"""

TOUCH_OPS = frozenset({
    "move",
    "swap_pixels",
    "add_overlay",
    "add_row",
    "set_enabled",
    "audio_fade",
    "native_transition",
    "remove_overlay",
    "retime",
    "slip",
    "resize_still",
    "resize_overlay",
    "position_overlay",
    "split",
    "slide",
    "roll",
    "set_properties",
    "entry_motion",
    "repair_hold_window",
})
