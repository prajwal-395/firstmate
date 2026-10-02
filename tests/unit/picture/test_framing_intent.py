"""The framing intent declaration: where a clip's number comes from.

`library/tools/framing_intent.py` is the one enumeration. Five things
are tested here and nothing else:

1. the default is FILL, because the heuristic it replaced letterboxed
   every talking-head clip;
2. the precedence - spine block > project.yaml > brand template >
   default - and in particular that a project can still say "letterbox";
3. a malformed declaration RAISES rather than degrading to "no framing";
4. declared is not delivered - a source that already covers the frame
   fills at every intent, and `_conform_fields` records both numbers;
5. `framing_crop_factor`, the additional zoom beyond fill.
"""
import os
import sys
import textwrap

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.schemas.brand_template import BrandTemplate, StyleSlots
from library.steps.step_5_04_compile_manifest.step import _conform_fields
from library.tools.framing_intent import (
    FILL,
    LETTERBOX,
    delivered_framing_intent,
    project_framing_intent,
    resolve_crop_factor,
    resolve_framing_intent,
    source_covers_frame,
    template_framing_intent,
    validate_crop_factor,
    validate_framing_intent,
)

VERTICAL = (1080, 1920)


def write_project(tmp_path, body: str) -> str:
    (tmp_path / "project.yaml").write_text(textwrap.dedent(body))
    return str(tmp_path)


def template(intent):
    return BrandTemplate(series_id="t", style=StyleSlots(framing_intent=intent))


def test_declaring_nothing_anywhere_fills(tmp_path):
    """61.6% of project 001's pixels were black because "declares
    nothing" meant "letterbox". It means "fill the frame you declared"
    now - and a project that declares none is not a project that
    declared zero."""
    assert resolve_framing_intent() == FILL
    assert template_framing_intent(template(None)) is None
    assert resolve_framing_intent(template=template(None)) == FILL
    folder = write_project(tmp_path, """
        name: Silent
        pipeline:
          brand_template: synthetic_default
    """)
    assert project_framing_intent(folder) is None
    assert resolve_framing_intent(project_folder=folder) == FILL


@pytest.mark.parametrize("block, project, tmpl, expected", [
    (None, None, 0.3, 0.3),             # template beats the default
    (None, 0.0, 1.0, LETTERBOX),        # project beats the template
    (1.0, 0.0, 0.0, FILL),              # spine block beats the project
    (None, 0.0, None, LETTERBOX),       # letterbox stays expressible
])
def test_precedence(tmp_path, block, project, tmpl, expected):
    folder = None
    if project is not None:
        folder = write_project(tmp_path, f"""
            name: Precedence
            pipeline:
              framing_intent: {project}
        """)
        assert project_framing_intent(folder) == project
    kwargs = {"block_intent": block, "project_folder": folder}
    if tmpl is not None:
        kwargs["template"] = template(tmpl)
    assert resolve_framing_intent(**kwargs) == expected


def test_a_malformed_declaration_raises():
    """`None` is absent on purpose: it means "not declared", above."""
    for bad, error in [("1.0", TypeError), (True, TypeError),
                       ([1.0], TypeError), ({}, TypeError),
                       (-0.1, ValueError), (1.1, ValueError),
                       (100, ValueError)]:
        with pytest.raises(error):
            validate_framing_intent(bad, "a test")


def test_a_bad_project_declaration_names_the_file(tmp_path):
    folder = write_project(tmp_path, """
        name: Broken
        pipeline:
          framing_intent: 100
    """)
    with pytest.raises(ValueError) as excinfo:
        project_framing_intent(folder)
    assert "project.yaml" in str(excinfo.value)


# ── Declared is not delivered ────────────────────────────────────────

def test_only_a_source_with_bars_delivers_what_it_was_told():
    assert source_covers_frame(1920, 1080, *VERTICAL) is False
    assert source_covers_frame(1080, 1920, *VERTICAL) is True
    for declared in (LETTERBOX, 0.4, FILL):
        assert delivered_framing_intent(declared, True) == FILL
        assert delivered_framing_intent(declared, False) == declared


def test_a_project_declaration_reaches_the_conform_and_both_are_recorded(
        tmp_path):
    """Issue #277/#311: 001 escaped a 3.1605x punch-in by declaring
    ``pipeline.framing_intent: 0.0``. The resolve and the conform must
    meet: the landscape A-roll keeps no fill_zoom and records bars, and
    the portrait cutaways - no bars to give - record a full frame."""
    folder = write_project(tmp_path, """
        name: Project 001 Shape
        slug: project-001-shape
        pipeline:
          framing_intent: 0.0
    """)
    intent = resolve_framing_intent(project_folder=folder)
    meta = {"aroll.MXF": {"width": 1920, "height": 1080, "rotation": 0},
            "cutaway.MOV": {"width": 1920, "height": 1080, "rotation": 90}}

    aroll = _conform_fields(meta, "aroll.MXF", VERTICAL, framing_intent=intent)
    assert aroll["framing_intent"] == LETTERBOX
    assert aroll["framing_delivered"] == LETTERBOX
    assert aroll["needs_conform"] is False
    assert "fill_zoom" not in aroll

    cutaway = _conform_fields(meta, "cutaway.MOV", VERTICAL,
                              framing_intent=intent)
    assert cutaway["framing_intent"] == LETTERBOX
    assert cutaway["framing_delivered"] == FILL
    assert cutaway["needs_conform"] is False


# ── framing_crop_factor: the additional zoom beyond fill ─────────────

def test_a_crop_factor_outside_its_range_raises():
    assert validate_crop_factor(2.0, "test") == 2.0
    for bad, error, fragment in [(0.5, ValueError, "must be between"),
                                 (2.5, ValueError, "must be between"),
                                 ("tight", TypeError, "must be a number")]:
        with pytest.raises(error, match=fragment):
            validate_crop_factor(bad, "test")
    assert resolve_crop_factor(block_crop_factor=1.4) == 1.4
    with pytest.raises(ValueError):
        resolve_crop_factor(block_crop_factor=0.5)


def test_a_crop_factor_multiplies_the_fill_zoom_up_to_its_clamp():
    landscape = {"clip_001": {"width": 1920, "height": 1080}}

    def conform(**kwargs):
        return _conform_fields(landscape, "clip_001", VERTICAL, **kwargs)

    base = conform(framing_intent=1.0)
    assert "framing_crop_factor" not in base
    tight = conform(framing_intent=1.0, framing_crop_factor=1.3)
    assert tight["fill_zoom"] == round(base["fill_zoom"] * 1.3, 4)
    assert tight["framing_crop_factor"] == 1.3
    # Above MAX_CROP_FACTOR it clamps to 2.0.
    assert (conform(framing_intent=1.0, framing_crop_factor=3.0)["fill_zoom"]
            == conform(framing_intent=1.0, framing_crop_factor=2.0)["fill_zoom"])
