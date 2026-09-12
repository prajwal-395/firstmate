"""Caption pairs that share pixels must share a filename (issue #915).

Measured on geo-podcast: 145 pairs of caption movs holding byte-identical
content under two content digests. Hash reconstruction proved the only
drawing-side difference between each pair was `style.safeArea.bottom` -
331 (the engine's old row: profile 320 + lift 11) versus 540 (the
project's declared caption row 1380) - rendered in two waves either side
of the row declaration. The frame-relative insets move the probe's ink
within the delivery frame, but the tight crop follows the ink, so two
rows of one card cut byte-identical canvases. The digest hashed the
insets anyway, so every row change re-rendered the whole directory
beside itself: same content under two names, and deleting the copies
leaves the producer producing them again.

The fix has two halves, and this file pins both:

- the tight filename is row-invariant (`_drawing_digest` drops
  `style.safeArea` for the tight carrying only - the full carrying
  keeps it, because there the row really moves pixels);
- the box sidecar stamps the row it was measured for, and the reuse
  path refuses a stamp that is missing or moved - because the unified
  file is still placed per row, and a placement measured for the old
  row reads back clean while drawing on the wrong one.

Each test fails with the fix reverted: the digest and filename tests
by producing two digests/files, the sidecar tests by restoring a
placement they must refuse, the re-measure test by serving the stale
file as REUSED.
"""

import copy
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.step import (  # noqa: E402
    RENDERED,
    REUSED,
    _drawing_digest,
    render_one_segment,
)
from library.tools.tight_box import (  # noqa: E402
    TightBoxMismatch,
    restore_reused_placement,
)
from tests.test_subtitle_overlay_modes import (  # noqa: E402
    _measured_frames_setup,
    _props,
    _ServingRenderer,
)

REMOTION = os.path.join(PROJECT_ROOT, "remotion-subtitles")

ROW_OLD = 331
"""The engine's row before the project declared its own: profile
bottom 320 + the old lift 11 (pre-#979)."""
ROW_NEW = 540
"""The project's declared caption row: delivery row 1380 on the
1080x1920 frame (1920 - 1380)."""


def _row_props(bottom: int) -> dict:
    """The same card on one caption row: everything identical except
    the frame-relative bottom inset that positions the probe's ink."""
    props = _props()
    props = copy.deepcopy(props)
    props["style"]["safeArea"] = {
        "top": 120, "right": 120, "bottom": bottom, "left": 90,
    }
    return props


def test_tight_digest_ignores_the_caption_row():
    """The pair-maker, at the unit level: one card on the old row and
    the new row digests identically for the tight carrying."""
    assert _drawing_digest(_row_props(ROW_OLD), "tight") == \
        _drawing_digest(_row_props(ROW_NEW), "tight")


def test_full_digest_still_sees_the_caption_row():
    """The other direction: a full-canvas render really moves pixels
    with the row, so unifying it too would serve wrong pixels as a
    hit. The tight exemption must not leak across carryings."""
    assert _drawing_digest(_row_props(ROW_OLD), "full") != \
        _drawing_digest(_row_props(ROW_NEW), "full")


def test_same_card_on_two_rows_renders_one_file(tmp_path):
    """End to end behind canned frames: the old-row render and the
    new-row render compute one overlay path, so the second overwrites
    rather than duplicating. Without the fix this leaves two
    frames-dirs holding identical pixels - the filed pair."""
    out = str(tmp_path)
    props, probe_canned, tight_canned, _box = _measured_frames_setup(
        tmp_path, "pairs")
    first_props = _row_props(ROW_OLD)
    first_props["durationInFrames"] = \
        props["durationInFrames"]
    first_props["_source_out_frame"] = props["_source_out_frame"]
    second_props = _row_props(ROW_NEW)
    second_props["durationInFrames"] = \
        props["durationInFrames"]
    second_props["_source_out_frame"] = props["_source_out_frame"]

    first = render_one_segment(
        first_props, out, "tl", remotion_dir=REMOTION,
        renderer=_ServingRenderer(probe_canned, tight_canned),
        reuse=False, overlay_geometry="tight",
        overlay_container="frames")
    assert first["provenance"] == RENDERED
    second = render_one_segment(
        second_props, out, "tl", remotion_dir=REMOTION,
        renderer=_ServingRenderer(probe_canned, tight_canned),
        reuse=False, overlay_geometry="tight",
        overlay_container="frames")
    assert second["provenance"] == RENDERED
    assert second["segment_id"] == first["segment_id"], (
        "one card on two rows must compute one filename - two names "
        "for identical pixels is the filed duplication")
    dirs = sorted(n for n in os.listdir(out) if n.endswith("_frames"))
    assert len(dirs) == 1, (
        f"two rows rendered two files holding the same pixels: {dirs}")


def test_row_change_remeasures_instead_of_serving_stale(tmp_path):
    """The unified file is still placed per row: a build after the row
    moves must re-measure (RENDERED, same path), never pair back to
    the old row's placement as REUSED."""
    out = str(tmp_path)
    props, probe_canned, tight_canned, _box = _measured_frames_setup(
        tmp_path, "rowmove")
    first_props = _row_props(ROW_OLD)
    first_props["durationInFrames"] = \
        props["durationInFrames"]
    first_props["_source_out_frame"] = props["_source_out_frame"]
    second_props = _row_props(ROW_NEW)
    second_props["durationInFrames"] = \
        props["durationInFrames"]
    second_props["_source_out_frame"] = props["_source_out_frame"]

    first = render_one_segment(
        first_props, out, "tl", remotion_dir=REMOTION,
        renderer=_ServingRenderer(probe_canned, tight_canned),
        reuse=True, overlay_geometry="tight",
        overlay_container="frames")
    assert first["provenance"] == RENDERED
    engine = _ServingRenderer(probe_canned, tight_canned)
    second = render_one_segment(
        second_props, out, "tl", remotion_dir=REMOTION,
        renderer=engine, reuse=True, overlay_geometry="tight",
        overlay_container="frames")
    assert second["segment_id"] == first["segment_id"]
    assert second["provenance"] == RENDERED, (
        "the row moved since this file was placed: pairing back to it "
        "as REUSED would serve the old row's placement, which reads "
        "back clean and draws on the wrong row")
    assert engine.calls, "re-measuring must render, not restore"
    assert second["provenance"] != REUSED


def test_sidecar_from_a_moved_row_is_refused():
    """The guard the test above leans on, at the unit level: a sidecar
    stamped for the old row does not restore under the new one."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    props = _row_props(ROW_OLD)
    sidecar = {
        "width": 840,
        "height": 480,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -850.0},
        "carriage": OVERLAY_CARRIAGE,
        "safe_area": {"top": 120, "right": 120,
                      "bottom": ROW_OLD, "left": 90},
        "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596},
    }
    try:
        restore_reused_placement(sidecar, _row_props(ROW_NEW),
                                 (1080, 1920))
    except TightBoxMismatch as exc:
        assert "safeArea" in str(exc)
    else:
        raise AssertionError(
            "a placement measured for the old row restored under the "
            "new one - the unified filename would serve stale rows")


def test_sidecar_without_a_row_stamp_is_refused():
    """A sidecar that predates the row stamp cannot prove which row it
    was measured for - every sidecar on disk from before the stamp -
    so it is refused the same way as a moved one."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    sidecar = {
        "width": 840,
        "height": 480,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -850.0},
        "carriage": OVERLAY_CARRIAGE,
        "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596},
    }
    try:
        restore_reused_placement(sidecar, _row_props(ROW_NEW),
                                 (1080, 1920))
    except TightBoxMismatch as exc:
        assert "safeArea" in str(exc)
    else:
        raise AssertionError(
            "an unstamped sidecar restored - its row is unprovable")


def test_sidecar_on_the_same_row_still_restores():
    """The guard refuses only what moved: an identical row restores,
    so reuse keeps its win on every build that changes nothing."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    sidecar = {
        "width": 840,
        "height": 480,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -850.0},
        "carriage": OVERLAY_CARRIAGE,
        "safe_area": {"top": 120, "right": 120,
                      "bottom": ROW_NEW, "left": 90},
        "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596},
    }
    restored = restore_reused_placement(sidecar, _row_props(ROW_NEW),
                                        (1080, 1920))
    assert restored.placement == {
        "scaling": 1, "pan": 0.0, "tilt": -850.0}


def test_identical_rows_pair_back_without_rendering(tmp_path):
    """Reuse itself is untouched: nothing moving means the second
    build still pairs back with no renderer call."""
    out = str(tmp_path)
    props, probe_canned, tight_canned, _box = _measured_frames_setup(
        tmp_path, "steady")
    steady = _row_props(ROW_NEW)
    steady["durationInFrames"] = props["durationInFrames"]
    steady["_source_out_frame"] = props["_source_out_frame"]

    first = render_one_segment(
        steady, out, "tl", remotion_dir=REMOTION,
        renderer=_ServingRenderer(probe_canned, tight_canned),
        reuse=True, overlay_geometry="tight",
        overlay_container="frames")
    assert first["provenance"] == RENDERED
    engine = _ServingRenderer(probe_canned, tight_canned)
    second = render_one_segment(
        steady, out, "tl", remotion_dir=REMOTION,
        renderer=engine, reuse=True, overlay_geometry="tight",
        overlay_container="frames")
    assert second["provenance"] == REUSED
    assert engine.calls == []
    assert second["tight_box"]["placement"] == \
        first["tight_box"]["placement"]
