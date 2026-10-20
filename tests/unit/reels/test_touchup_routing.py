"""Semantic routing identities used by the staged reel touch-up adapter."""

from library.tools.reel_touchup import _STRUCTURAL_OPERATION_IDS


def test_touchup_routing_preserves_overlay_and_row_operation_identity():
    assert _STRUCTURAL_OPERATION_IDS == {
        "retime": "clip.ripple",
        "add_overlay": "overlay.add",
        "remove_overlay": "overlay.remove",
        "swap_pixels": "overlay.swap_pixels",
        "resize_still": "overlay.resize",
        "resize_overlay": "overlay.resize",
        "position_overlay": "overlay.position",
        "split": "clip.split",
        "slide": "clip.slide",
        "roll": "clip.roll",
        "add_row": "track.add",
        "move": "clip.move",
    }
