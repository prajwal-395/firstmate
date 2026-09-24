"""Fusion Background nodes are built at the SOURCE frame, not the delivery frame.

Every Background node `build_effect_comp` draws - the vignette, the fade,
both halves of a transition - is a solid image merged over `MediaIn`. It
therefore has to be the size of the image Fusion sees, which is the
SOURCE clip's own frame. It defaulted to 1080x1920 while project 001's
A-roll is 1920x1080, so every graded clip carried a 1080-wide,
full-height dark rectangle down the centre of the picture, measurable in
the export as a ~15-level step at source columns 419 and 1499. Nothing
warned: a Background of the wrong size is a perfectly valid comp.

`CompEngine.from_params` already had a `source_res` for exactly this
reason, with a comment explaining it - but the renderer calls
`build_effect_comp`, which did not. A correct mechanism on a path nothing
executes is the failure mode this pipeline keeps re-finding, so this file
tests the path the renderer actually takes.
"""
import pathlib
import re

import pytest

from library.tools.custom_asset_bank import comp_asset_key
from library.tools.fusion.comp_builder import (
    MissingSourceFrame, build_effect_comp)

APPLY_FUSION_COMPS = (pathlib.Path(__file__).resolve().parent.parent
                      / "library" / "tools" / "execution"
                      / "apply_fusion_comps.py")

CLIP_DUR = 120
LANDSCAPE = (1920, 1080)
VERTICAL = (1080, 1920)

# Every effect whose block contains a Background node.
BACKGROUND_EFFECTS = {
    "vignette": {"vignette": True, "vignette_blend": 0.25,
                 "vignette_soft": 0.35},
    "fade": {"fade_in_frames": 8, "fade_out_frames": 8, "vignette": False},
    "tail_fade_to_black": {"tail_transition": "fade_to_black",
                           "tail_transition_frames": 7, "vignette": False},
    "head_fade_to_black": {"head_transition": "fade_to_black",
                           "head_transition_frames": 7, "vignette": False},
}


def _background_sizes(comp: str):
    """(Width, Height) of every Background node in a serialized comp."""
    sizes = []
    for block in re.split(r"\bBackground\b", comp)[1:]:
        w = re.search(r"Width\s*=\s*Input\s*{\s*Value\s*=\s*([\d.]+)", block)
        h = re.search(r"Height\s*=\s*Input\s*{\s*Value\s*=\s*([\d.]+)", block)
        if w and h:
            sizes.append((int(float(w.group(1))), int(float(h.group(1)))))
    return sizes


@pytest.mark.parametrize("name", sorted(BACKGROUND_EFFECTS))
def test_backgrounds_match_the_landscape_source(name):
    comp = build_effect_comp(dict(BACKGROUND_EFFECTS[name]), CLIP_DUR,
                             source_res=LANDSCAPE)
    sizes = _background_sizes(comp)
    assert sizes, f"{name} drew no Background node"
    assert all(s == LANDSCAPE for s in sizes), (
        f"{name} drew {sizes} over a {LANDSCAPE} source - a Background "
        "smaller than the frame is a hard-edged rectangle in the picture"
    )




def test_an_unknown_source_refuses_rather_than_guessing():
    """None means "could not tell" - and an unstated frame refuses.

    The builder used to fall back to a documented vertical default;
    a 3840x2160 source built at that size carries a hard-edged
    rectangle down the middle of the picture, so the fallback was a
    defect that shipped silently. The renderer reads the size off the
    MediaPoolItem and refuses where Resolve will not state one.
    """
    with pytest.raises(MissingSourceFrame, match="source_res"):
        build_effect_comp(
            {"vignette": True, "vignette_blend": 0.25,
             "vignette_soft": 0.35},
            CLIP_DUR, source_res=None)


def test_the_asset_bank_key_changes_with_the_source_frame():
    """Same effects, different frame - different comp, so different key.

    Without this a comp banked for a 1920x1080 clip would be replayed on
    a 1080x1920 one, putting the rectangle straight back.  The key is
    taken over the comp the builder really emitted, so this reads the
    difference off the bytes rather than off a restatement of the
    inputs.
    """
    effects = {"vignette": True, "vignette_blend": 0.25,
               "vignette_soft": 0.35}
    landscape = comp_asset_key("speech_3_seg0", build_effect_comp(
        dict(effects), CLIP_DUR, source_res=LANDSCAPE))
    vertical = comp_asset_key("speech_3_seg0", build_effect_comp(
        dict(effects), CLIP_DUR, source_res=VERTICAL))
    assert landscape != vertical


def test_the_renderer_reads_the_source_frame_off_the_media_pool_item():
    """`_source_resolution` judges Resolve by what it RETURNS.

    `hasattr` is always True on a Resolve proxy, so the reader has to
    handle a property that answers nothing - and answer None rather than
    a fabricated size.
    """
    # The module imports DaVinciResolveScript at import time, so the
    # function is read out of the source rather than imported.
    source = APPLY_FUSION_COMPS.read_text()
    body = source.split("def _source_resolution(mpi):", 1)[1].split("\ndef ", 1)[0]
    namespace = {}
    exec("def _source_resolution(mpi):" + body, namespace)
    read = namespace["_source_resolution"]

    class Item:
        def __init__(self, value):
            self.value = value

        def GetClipProperty(self, key):
            assert key == "Resolution"
            return self.value

    assert read(Item("1920x1080")) == (1920, 1080)
    assert read(Item("1080x1920")) == (1080, 1920)
    assert read(Item("")) is None
    assert read(Item(None)) is None
    assert read(Item("unknown")) is None
    assert read(Item("0x0")) is None
    assert read(None) is None


