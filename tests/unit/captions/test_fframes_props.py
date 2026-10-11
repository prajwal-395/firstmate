"""What the FFrames adapter hands the lowering: faces resolved, never guessed.

The FFrames review on PR 1805 found text drawn in a system fallback face
because the lowering received a family and no file. The adapter now
resolves the face the HyperFrames template would load, and refuses a
project face it cannot find rather than drawing in another.
"""

import pytest

from library.tools import fframes_render
from library.tools.hyperframes_render import bundled_font_file


@pytest.mark.skipif(not bundled_font_file().is_file(), reason="bundled Montserrat not in this checkout")
def test_bundled_family_resolves_to_the_bundled_file_everywhere_it_is_read():
    props = {"style": {"fontFamily": "Montserrat"}}
    resolved = fframes_render.resolve_props("SubtitleOverlay", props, "")
    assert resolved["fontFile"] == str(bundled_font_file())
    assert resolved["style"]["fontFile"] == str(bundled_font_file())
    assert "fontFile" not in props["style"], "the caller's props are not mutated"


def test_an_undeclared_system_family_stays_a_family():
    resolved = fframes_render.resolve_props("TimedTextOverlay", {}, "")
    assert "fontFile" not in resolved


def test_a_project_face_the_project_lacks_refuses(tmp_path):
    with pytest.raises(fframes_render.FFramesRenderError, match="brand_assets"):
        fframes_render.resolve_props(
            "PostHeader", {"fontFile": "brand/Missing.ttf"}, str(tmp_path))


def test_brand_motion_has_an_fframes_form():
    """BrandMotion lowers - the last composition without an FFrames path.

    The Remotion ``OffthreadVideo`` path stays as the selectable engine;
    this form is what ``bookend_render`` draws when FFrames is selected.
    """
    assert fframes_render.fframes_has_form("BrandMotion")
    scene = fframes_render.lower_composition(
        "BrandMotion",
        {"src": "/tmp/repro-sting.webm", "fps": 30, "width": 320,
         "height": 240, "durationInFrames": 30, "muted": True})
    assert scene.nodes[0].type == "video"


def test_brand_motion_src_resolves_absolute_and_refuses_missing(tmp_path):
    """The ``brand/<file>`` reference becomes a path the renderer can read.

    Remotion wraps it in ``staticFile``; the FFrames renderer decodes the
    file itself, so it needs the absolute staged mezzanine - and a
    reference the project does not have refuses rather than drawing
    without it.
    """
    staged = tmp_path / "repro-sting.webm"
    staged.write_bytes(b"not a real sting, only a path probe")
    resolved = fframes_render.resolve_props(
        "BrandMotion", {"src": str(staged)}, str(tmp_path))
    assert resolved["src"] == str(staged)
    with pytest.raises(fframes_render.FFramesRenderError, match="brand_assets"):
        fframes_render.resolve_props(
            "BrandMotion", {"src": "brand/Missing.webm"}, str(tmp_path))
