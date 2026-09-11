"""The punched-in TV-frame look is declarable, never hardcoded.

The captain's marker on Reel 20 (frame 538, 2026-09-09) sets the look:
V1 punched to 2.30 under the TV asset at native 1:1, captions above.
`library/tools/tv_frame.py` declares that - the factor, the asset, the
layer order - and this asserts the declarations behave: 2.30 is the
default the project or template can change, no asset means no look, and
a malformed declaration raises rather than degrading silently.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest
import yaml

from library.tools.tv_frame import (
    DEFAULT_PUNCH_IN,
    LAYER_TRACKS,
    MAX_PUNCH_IN,
    MIN_PUNCH_IN,
    TV_FRAME_LAYERS,
    resolve_asset_path,
    resolve_tv_frame,
    screen_window,
    v1_zoom_for_look,
    validate_punch_in,
)


def _project(tmp_path, pipeline_block=None, template_name=None):
    folder = tmp_path / "proj"
    folder.mkdir(exist_ok=True)
    pipeline = dict(pipeline_block or {})
    if template_name is not None:
        pipeline["brand_template"] = template_name
    (folder / "project.yaml").write_text(
        yaml.safe_dump({"pipeline": pipeline}), encoding="utf-8")
    return str(folder)


def _asset(tmp_path, name="frame.png"):
    from PIL import Image
    p = tmp_path / name
    im = Image.new("RGBA", (64, 48), (20, 20, 20, 255))
    px = im.load()
    for y in range(8, 40):
        for x in range(10, 54):
            px[x, y] = (0, 0, 0, 0)
    im.save(p)
    return str(p)


def test_default_punch_in_is_the_captains_chosen_standard():
    """2.30, measured off the Reel 20 reference - a default, not a constant."""
    assert DEFAULT_PUNCH_IN == 2.30


def test_layer_order_is_footage_frame_captions():
    assert TV_FRAME_LAYERS == ("footage", "frame", "captions")
    assert LAYER_TRACKS == {
        "footage": "V1", "frame": "V2", "captions": "V3"}


def test_no_asset_means_no_look(tmp_path):
    """A project declaring nothing gets nothing - there is no fallback bezel."""
    assert resolve_tv_frame(_project(tmp_path), None) is None


def test_bare_punch_in_with_no_asset_is_not_the_look(tmp_path):
    """A zoom with no frame is not the look; it resolves to no look
    rather than punching the footage for no reason."""
    folder = _project(tmp_path, {"tv_frame": {"punch_in": 2.0}})
    assert resolve_tv_frame(folder, None) is None


def test_undeclared_factor_falls_back_to_the_standard(tmp_path):
    asset = _asset(tmp_path)
    folder = _project(tmp_path, {"tv_frame": {"asset": asset}})
    resolved = resolve_tv_frame(folder, None)
    assert resolved["punch_in"] == DEFAULT_PUNCH_IN
    assert resolved["asset"] == os.path.abspath(asset)
    assert "project.yaml" in resolved["origin"]


def test_declared_factor_overrides_the_standard(tmp_path):
    asset = _asset(tmp_path)
    folder = _project(tmp_path,
                      {"tv_frame": {"asset": asset, "punch_in": 1.8}})
    assert resolve_tv_frame(folder, None)["punch_in"] == 1.8


def test_project_beats_template(tmp_path):
    proj_asset = _asset(tmp_path, "proj.png")
    tmpl_asset = _asset(tmp_path, "tmpl.png")
    folder = _project(
        tmp_path, {"tv_frame": {"asset": proj_asset, "punch_in": 1.5}})
    from library.schemas.brand_template import (
        BrandTemplate, ContentSlots, EffectSlots, StyleSlots)
    template = BrandTemplate(
        series_id="t",
        style=StyleSlots(tv_frame={
            "asset": tmpl_asset, "punch_in": 2.9}),
        effect=EffectSlots(),
        content=ContentSlots(),
    )
    resolved = resolve_tv_frame(folder, template)
    assert resolved["punch_in"] == 1.5
    assert resolved["asset"] == os.path.abspath(proj_asset)


def test_template_alone_declares_the_look(tmp_path):
    asset = _asset(tmp_path)
    folder = _project(tmp_path)
    from library.schemas.brand_template import (
        BrandTemplate, ContentSlots, EffectSlots, StyleSlots)
    template = BrandTemplate(
        series_id="t", style=StyleSlots(tv_frame={"asset": asset}),
        effect=EffectSlots(), content=ContentSlots())
    resolved = resolve_tv_frame(folder, template)
    assert resolved["punch_in"] == DEFAULT_PUNCH_IN
    assert "template t" in resolved["origin"]


def test_relative_asset_resolves_against_the_project(tmp_path):
    # A relative asset is project artwork (§14): it resolves against
    # the project folder, never against the engine.
    folder = _project(tmp_path, {"tv_frame": {"asset": "frame.png"}})
    (Path(folder) / "frame.png").write_bytes(
        Path(_asset(tmp_path, "f2.png")).read_bytes())
    resolved = resolve_tv_frame(folder, None)
    assert resolved["asset"] == os.path.abspath(
        os.path.join(folder, "frame.png"))


def test_missing_asset_raises(tmp_path):
    folder = _project(
        tmp_path, {"tv_frame": {"asset": "/no/such/frame.png"}})
    with pytest.raises(FileNotFoundError):
        resolve_tv_frame(folder, None)


def test_out_of_range_factor_raises(tmp_path):
    asset = _asset(tmp_path)
    for bad in (0.5, 9.0):
        folder = _project(
            tmp_path, {"tv_frame": {"asset": asset, "punch_in": bad}})
        with pytest.raises(ValueError):
            resolve_tv_frame(folder, None)
    with pytest.raises(TypeError):
        validate_punch_in("tight", "test")
    assert validate_punch_in(1.0, "test") == 1.0
    assert validate_punch_in(MAX_PUNCH_IN, "test") == MAX_PUNCH_IN
    assert MIN_PUNCH_IN == 1.0


def test_unknown_declaration_key_raises(tmp_path):
    asset = _asset(tmp_path)
    folder = _project(
        tmp_path, {"tv_frame": {"asset": asset, "bezel": "chrome"}})
    with pytest.raises(ValueError):
        resolve_tv_frame(folder, None)


def test_non_mapping_declaration_raises(tmp_path):
    folder = _project(tmp_path, {"tv_frame": "TV 4k.png"})
    with pytest.raises(TypeError):
        resolve_tv_frame(folder, None)


def test_power_timings_travel_with_the_look(tmp_path):
    """The declaration names the SHAPE, and the shape is played both
    ways: a project re-timing the switch re-times the switch-on and the
    switch-off together (captain, 2026-09-11)."""
    asset = _asset(tmp_path)
    folder = _project(tmp_path, {"tv_frame": {
        "asset": asset,
        "power": {"collapse_frames": 2},
    }})
    resolved = resolve_tv_frame(folder, None)
    assert resolved["power"]["collapse_frames"] == 2
    assert resolved["power"]["dot_frames"] == 3
    assert resolved["power"]["decay_frames"] == 9


def test_a_per_half_power_declaration_is_refused(tmp_path):
    """`power.switch_on` / `power.switch_off` were the shape of this
    declaration until 2026-09-11. Honouring one would re-time one
    direction and leave the other behind, so it raises."""
    asset = _asset(tmp_path)
    folder = _project(tmp_path, {"tv_frame": {
        "asset": asset,
        "power": {"switch_on": {"collapse_frames": 2}},
    }})
    with pytest.raises(ValueError):
        resolve_tv_frame(folder, None)


def test_declared_collapse_travels_with_the_look(tmp_path):
    """The depth is declarable, not compiled: a project names it under
    power and the resolved look carries it to the comp builder."""
    asset = _asset(tmp_path)
    folder = _project(tmp_path, {"tv_frame": {
        "asset": asset,
        "power": {"collapse_crop": 0.30},
    }})
    resolved = resolve_tv_frame(folder, None)
    assert resolved["power"]["collapse_crop"] == 0.30


def test_undeclared_power_carries_no_timings(tmp_path):
    """No power declaration means no timings on the look: the module's
    shape applies where the timings MERGE (reel_look and
    compile_manifest seed from the module), not here."""
    asset = _asset(tmp_path)
    folder = _project(tmp_path, {"tv_frame": {"asset": asset}})
    resolved = resolve_tv_frame(folder, None)
    assert resolved["power"] == {}


def test_collapse_at_the_guard_raises(tmp_path):
    """0.5 closes the band entirely - the one-frame flash - so a
    project declaring it is refused, naming the declaration."""
    asset = _asset(tmp_path)
    folder = _project(tmp_path, {"tv_frame": {
        "asset": asset,
        "power": {"collapse_crop": 0.5},
    }})
    with pytest.raises(ValueError):
        resolve_tv_frame(folder, None)


def test_v1_zoom_is_absolute_not_multiplied():
    """Under the frame the bezel IS the framing: the reference shows
    Zoom 2.30 flat, so the punch replaces the conform zoom rather than
    multiplying over it."""
    assert v1_zoom_for_look(2.30) == 2.30


def test_screen_window_measures_the_transparent_hole(tmp_path):
    asset = _asset(tmp_path)
    assert screen_window(asset) == (10, 8, 54, 40)


def test_screen_window_refuses_an_opaque_slate(tmp_path):
    from PIL import Image
    p = tmp_path / "slate.png"
    Image.new("RGBA", (32, 32), (0, 0, 0, 255)).save(p)
    with pytest.raises(ValueError):
        screen_window(str(p))


def test_resolve_asset_path_guards(tmp_path):
    with pytest.raises(TypeError):
        resolve_asset_path("", str(tmp_path), "test")
    with pytest.raises(FileNotFoundError):
        resolve_asset_path("/no/such.png", str(tmp_path), "test")


def test_content_runs_split_around_cards():
    """The frame dresses the show, not the logo cards: one run per
    contiguous stretch of content, so no V2 frame clip ever overlaps a
    declared card."""
    from library.steps.step_5_04_compile_manifest.step import _content_runs
    clips = [
        {"timeline_in": 0.0, "timeline_out": 3.0, "bookend": "intro"},
        {"timeline_in": 3.0, "timeline_out": 7.0},
        {"timeline_in": 7.0, "timeline_out": 9.0},
        {"timeline_in": 9.0, "timeline_out": 12.0, "bookend": "end_card"},
        {"timeline_in": 12.0, "timeline_out": 14.0},
    ]
    runs = _content_runs(clips)
    assert [(r["timeline_in"], r["timeline_out"]) for r in runs] == [
        (3.0, 9.0), (12.0, 14.0)]
    assert [r["index"] for r in runs] == [0, 1]


def test_content_runs_without_cards_is_a_single_run():
    from library.steps.step_5_04_compile_manifest.step import _content_runs
    clips = [
        {"timeline_in": 0.0, "timeline_out": 4.0},
        {"timeline_in": 4.0, "timeline_out": 8.0},
    ]
    runs = _content_runs(clips)
    assert [(r["timeline_in"], r["timeline_out"]) for r in runs] == [
        (0.0, 8.0)]
