"""The house look vocabulary: every look must be real where it is read.

`library/tools/house_look.py` is the one enumeration of the looks a brand
template may name. These tests hold it to the same bar as
`transition_vocabulary.py`: a look that names a knob nothing reads, or a
knob that draws no node, is the failure mode this pipeline keeps hitting
(`zoom_percent`, `intensity_px` and `scale_factor` killed three VFX types
that way, and four of the colour grade's five nodes had no reader at all).
"""
import inspect
import os

import pytest
import yaml

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.house_look import (
    HOUSE_LOOKS,
    NEUTRAL_CDL,
    HouseLook,
    normalize_name,
    resolve_look,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALL_LOOKS = sorted(HOUSE_LOOKS)


def test_the_vocabulary_is_not_empty():
    assert ALL_LOOKS


@pytest.mark.parametrize("name", ALL_LOOKS)
def test_every_look_draws_its_fusion_half(name):
    """Every Fusion key a look emits must produce nodes in the comp.

    build_effect_comp dispatches on parameter NAMES, so a look emitting a
    name it does not read yields a comp without that effect and no
    warning.
    """
    comp = build_effect_comp(dict(HOUSE_LOOKS[name].fusion()), 120)
    assert "BrightnessContrast" in comp, f"{name}: contrast drew nothing"
    assert "SoftGlow" in comp, f"{name}: glow drew nothing"
    assert "FilmGrain" in comp, f"{name}: grain drew nothing"
    assert "EllipseMask" in comp, f"{name}: vignette drew nothing"


def test_every_fusion_key_is_read_by_the_comp_builder():
    """The named keys must literally appear in the dispatcher's source.

    A key that only happens to be harmless today is still a key nothing
    reads; this asserts the reader exists rather than inferring it from
    the rendered output.
    """
    source = inspect.getsource(build_effect_comp)
    for name, look in HOUSE_LOOKS.items():
        for key in look.fusion():
            assert f"'{key}'" in source or f'"{key}"' in source, (
                f"{name} emits {key!r}, which build_effect_comp never reads"
            )


def test_no_look_delivers_saturation_twice():
    """Saturation is a CDL term. Sending it to fx.grade as well would
    multiply it a second time in the picture."""
    for name, look in HOUSE_LOOKS.items():
        assert "grade_saturation" not in look.fusion(), name
        assert look.cdl()["saturation"] > 0, name


@pytest.mark.parametrize("name", ALL_LOOKS)
def test_every_look_is_a_look_and_not_an_identity(name):
    """A look that changes nothing is a look that was never authored."""
    look = HOUSE_LOOKS[name]
    cdl = look.cdl()
    assert cdl != NEUTRAL_CDL, f"{name} is an identity CDL"
    assert any(v > 0 for v in (look.contrast, look.glow_gain,
                               look.grain_power, look.vignette_blend))


@pytest.mark.parametrize("name", ALL_LOOKS)
def test_every_look_stays_inside_sane_bounds(name):
    """Values a colourist would recognise as a grade, not a destruction."""
    look = HOUSE_LOOKS[name]
    for channel, value in zip("rgb", look.slope):
        assert 0.8 <= value <= 1.25, f"{name} slope_{channel}={value}"
    for channel, value in zip("rgb", look.offset):
        assert -0.05 <= value <= 0.05, f"{name} offset_{channel}={value}"
    for channel, value in zip("rgb", look.power):
        assert 0.8 <= value <= 1.25, f"{name} power_{channel}={value}"
    assert 0.7 <= look.saturation <= 1.5
    assert 0.0 <= look.vignette_blend <= 0.5
    assert 0.0 <= look.glow_gain <= 1.0


@pytest.mark.parametrize("name", ALL_LOOKS)
def test_every_look_records_where_its_values_came_from(name):
    """The design is traceable to the planning docs, or it is taste.

    The planning documents live outside this repo and are read-only, so
    the citation is the only trail a future session has.
    """
    look = HOUSE_LOOKS[name]
    assert len(look.derived_from) > 80, name
    assert "branding_creative_direction.md" in look.derived_from, name
    assert look.intent.strip(), name


@pytest.mark.parametrize("name", ALL_LOOKS)
def test_withdrawn_design_says_why_it_cannot_be_delivered(name):
    """Withdrawal is a legitimate outcome; a silent gap is not."""
    for note, reason in HOUSE_LOOKS[name].withdrawn.items():
        assert len(reason) > 60, f"{name}: {note} has no real reason"


def test_names_resolve_case_and_space_insensitively():
    assert resolve_look("Film Stock Warmth") is HOUSE_LOOKS["film_stock_warmth"]
    assert resolve_look("film-stock-warmth") is HOUSE_LOOKS["film_stock_warmth"]
    assert normalize_name("  PMK Default ") == "pmk_default"


def test_no_look_resolves_to_none_rather_than_a_default():
    """A template that names nothing must not silently get a grade."""
    assert resolve_look("") is None
    assert resolve_look(None) is None


def test_an_unknown_look_lists_the_known_ones():
    with pytest.raises(ValueError) as excinfo:
        resolve_look("cinematic_warm")
    message = str(excinfo.value)
    for name in ALL_LOOKS:
        assert name in message


def test_every_look_is_named_by_a_shipped_template():
    """A look nothing names is a preset with no reader - the exact thing
    the PowerGrade route was removed for."""
    templates_dir = os.path.join(REPO_ROOT, "library", "templates")
    named = set()
    for filename in os.listdir(templates_dir):
        if not filename.endswith((".yaml", ".yml")):
            continue
        with open(os.path.join(templates_dir, filename)) as handle:
            template = yaml.safe_load(handle) or {}
        name = (template.get("style") or {}).get("house_look", "")
        if name:
            named.add(normalize_name(name))

    orphans = sorted(set(HOUSE_LOOKS) - named)
    assert not orphans, f"looks no brand template names: {orphans}"


@pytest.mark.parametrize("name", ALL_LOOKS)
def test_the_look_is_values_and_not_a_path(name):
    """The whole point of dropping the PowerGrade: the look is numbers in
    this repo, reproducible with no file inside a Resolve installation."""
    look = HOUSE_LOOKS[name]
    delivered = list(look.cdl().values()) + list(look.fusion().values())
    for value in delivered:
        assert not isinstance(value, str), (
            f"{name} delivers {value!r}, which is a name or a path, not a value"
        )
    assert isinstance(look, HouseLook)
