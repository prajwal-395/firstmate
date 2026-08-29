"""A look is DECLARED, whole, by a template - or it is not drawn at all.

`library/tools/house_look.py` used to be a catalogue of four looks whose
strengths this repository authored. It is now a reader for a declaration
a brand template writes, and these tests hold it to three things:

1. the engine ships no look values, and none were relocated into a
   template;
2. every parameter a declaration emits draws a real Fusion node, which is
   the failure mode this pipeline keeps hitting (`zoom_percent`,
   `intensity_px` and `scale_factor` killed three VFX types that way, and
   four of the colour grade's five nodes had no reader at all);
3. a declaration that is missing part of an element is REFUSED, because
   the only way to finish it is for the engine to choose a strength.
"""
import inspect
import os
import re

import pytest
import yaml

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.house_look import (
    ELEMENTS_BY_KEY,
    LOOK_ELEMENTS,
    NEUTRAL_CDL,
    DeclaredLook,
    LookDeclarationError,
    describe_declaration_shape,
    describe_look,
    resolve_look,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(REPO_ROOT, "library", "templates")

# A declaration written HERE, in a test, on purpose: the point of the
# test is that the mechanism delivers whatever a template declares, and
# the point of the change is that no such numbers live in the engine.
# These are not a look; they are three arbitrary values used to prove
# that a declared value reaches a node.
FULL_DECLARATION = {
    "name": "test_declaration",
    "intent": "Values invented by this test to prove the route, not a look.",
    "cdl": {
        "slope": [1.1, 1.0, 0.9],
        "offset": [0.01, 0.0, -0.01],
        "power": [0.99, 1.0, 1.01],
        "saturation": 1.2,
    },
    "contrast": 0.2,
    "glow": {"gain": 0.3, "threshold": 0.7, "size": 4.0},
    "grain": {"power": 0.25, "size": 1.4},
    "vignette": {"blend": 0.3, "soft": 0.5, "color": [0.1, 0.05, 0.02]},
    "exposure_reference": 118.0,
}


# ── The engine ships no look ────────────────────────────────────────────

def test_the_module_carries_no_look_values_of_its_own():
    """The four authored looks are gone, not renamed or made private.

    The captain, 2026-08-28: "there are no house glow looks, there are no
    settled house grain or anything". A module-level float in here is a
    strength nobody chose, whatever it is called.
    """
    import library.tools.house_look as module

    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, float), (
            f"house_look.{name} is a bare number: {value!r}. Every "
            f"strength belongs in a template's declaration."
        )

    source = inspect.getsource(module)
    for gone in ("pmk_default", "warm_reflection", "electric_contrast",
                 "film_stock_warmth"):
        # Named in the docstring as history, never as a value.
        assert f'"{gone}"' not in source and f"'{gone}'" not in source, (
            f"{gone} is still a look this engine can hand out")


def test_the_only_numbers_in_the_module_are_identity():
    """NEUTRAL_CDL is the one table of numbers, and it changes nothing."""
    assert set(NEUTRAL_CDL) == {
        "slope_r", "slope_g", "slope_b",
        "offset_r", "offset_g", "offset_b",
        "power_r", "power_g", "power_b", "saturation",
    }
    for key, value in NEUTRAL_CDL.items():
        expected = 0.0 if key.startswith("offset") else 1.0
        assert value == expected, f"{key} is not identity"


def test_no_shipped_template_declares_a_look():
    """The catalogue was removed, not relocated.

    If a future template legitimately declares one, this test is the
    place to record that decision - by name, with who made it. Until then
    a look appearing in a shipped template is the old numbers coming back
    through the other door.
    """
    declaring = []
    for filename in sorted(os.listdir(TEMPLATES_DIR)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        with open(os.path.join(TEMPLATES_DIR, filename)) as handle:
            template = yaml.safe_load(handle) or {}
        if (template.get("style") or {}).get("house_look"):
            declaring.append(filename)

    assert not declaring, (
        "shipped templates declare a look: " + ", ".join(declaring) + ". "
        "That is legitimate only as a stated decision; record it here."
    )


def test_every_shipped_template_says_why_it_declares_none():
    """Absence has to read as a decision, not as an omission."""
    for filename in sorted(os.listdir(TEMPLATES_DIR)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        text = open(os.path.join(TEMPLATES_DIR, filename)).read()
        assert "NO LOOK IS DECLARED" in text, (
            f"{filename} declares no look and does not say so")
        assert "house_look" in text, (
            f"{filename} does not point at the declaration shape")


# ── A declaration reaches the picture ───────────────────────────────────

def test_a_declaration_draws_every_node_it_names():
    """build_effect_comp dispatches on parameter NAMES, so a declared
    element that emits a name it does not read yields a comp without that
    effect and no warning."""
    look = resolve_look(FULL_DECLARATION)
    comp = build_effect_comp(dict(look.fusion()), 120)
    assert "BrightnessContrast" in comp, "contrast drew nothing"
    assert "SoftGlow" in comp, "glow drew nothing"
    assert "FilmGrain" in comp, "grain drew nothing"
    assert "EllipseMask" in comp, "vignette drew nothing"
    # A coloured vignette is the clearest thing a CDL cannot express.
    assert "TopLeftRed" in comp


def test_every_emitted_key_is_read_by_the_comp_builder():
    """The emitted names must literally appear in the dispatcher's source."""
    source = inspect.getsource(build_effect_comp)
    look = resolve_look(FULL_DECLARATION)
    for key in look.fusion():
        assert f"'{key}'" in source or f'"{key}"' in source, (
            f"a declaration emits {key!r}, which build_effect_comp never reads"
        )


def test_every_element_declares_the_names_it_emits():
    """LOOK_ELEMENTS is the table an author and a test both read; a key
    it forgets to list is a key nothing can be held to."""
    look = resolve_look(FULL_DECLARATION)
    emitted = set(look.fusion()) | set(look.cdl())
    declared = {
        name
        for element in LOOK_ELEMENTS
        for name in element.emits
    }
    assert emitted <= declared, sorted(emitted - declared)


def test_the_declared_values_are_the_values_that_travel():
    look = resolve_look(FULL_DECLARATION)
    fusion = look.fusion()
    assert fusion["glow_gain"] == 0.3
    assert fusion["glow_threshold"] == 0.7
    assert fusion["film_grain_power"] == 0.25
    assert fusion["vignette_blend"] == 0.3
    assert fusion["grade_contrast"] == 0.2
    cdl = look.cdl()
    assert cdl["slope_r"] == 1.1
    assert cdl["saturation"] == 1.2


def test_saturation_is_never_delivered_twice():
    """Saturation is a CDL term. Sending it to fx.grade as well would
    multiply it a second time in the picture."""
    look = resolve_look(FULL_DECLARATION)
    assert "grade_saturation" not in look.fusion()
    assert look.cdl()["saturation"] == 1.2


def test_an_undeclared_element_emits_no_key_at_all():
    """Not a key set to a quiet value - the comp builder dispatches on
    presence, so absence is the only thing that means 'not drawn'."""
    look = resolve_look({"name": "contrast_only", "contrast": 0.1})
    assert look.fusion() == {"grade_contrast": 0.1}
    comp = build_effect_comp(dict(look.fusion()), 120)
    assert "SoftGlow" not in comp
    assert "FilmGrain" not in comp
    assert "EllipseMask" not in comp


def test_a_look_with_no_cdl_leaves_the_cdl_neutral():
    look = resolve_look({"name": "fusion_only", "grain": {"power": 0.2,
                                                          "size": 1.0}})
    assert look.cdl() == NEUTRAL_CDL
    assert look.has_cdl is False


def test_exposure_gain_rides_on_the_slope_without_moving_the_hue():
    look = resolve_look(FULL_DECLARATION)
    graded = look.cdl(exposure_gain=1.5)
    assert graded["slope_r"] == pytest.approx(1.1 * 1.5)
    assert graded["slope_r"] / graded["slope_b"] == pytest.approx(
        1.1 / 0.9, rel=1e-3)
    # A gain is not a grade: the offsets and powers are untouched.
    assert graded["offset_r"] == pytest.approx(0.01)
    assert graded["power_b"] == pytest.approx(1.01)


def test_a_neutral_cdl_still_carries_the_exposure_gain():
    look = resolve_look({"name": "grain_only",
                         "grain": {"power": 0.2, "size": 1.0}})
    graded = look.cdl(exposure_gain=0.8)
    assert graded["slope_r"] == graded["slope_g"] == graded["slope_b"] == 0.8
    assert graded["saturation"] == 1.0


# ── Absence is nothing, not a substitute ────────────────────────────────

@pytest.mark.parametrize("declaration", [None, "", {}])
def test_no_declaration_resolves_to_none(declaration):
    """A project that declares nothing must not silently get a grade."""
    assert resolve_look(declaration) is None


def test_the_absence_is_described_as_an_absence():
    text = describe_look(None)
    assert "No look" in text
    for element in ("glow", "grain", "vignette"):
        assert element in text
    assert "no house look to fall back to" in text


# ── A partial declaration is refused, never completed ───────────────────

@pytest.mark.parametrize("element,partial", [
    ("glow", {"gain": 0.2}),
    ("glow", {"gain": 0.2, "threshold": 0.7}),
    ("grain", {"power": 0.2}),
    ("vignette", {"blend": 0.2}),
    ("cdl", {"slope": [1, 1, 1], "offset": [0, 0, 0], "power": [1, 1, 1]}),
])
def test_a_half_declared_element_is_refused_by_name(element, partial):
    with pytest.raises(LookDeclarationError) as excinfo:
        resolve_look({"name": "half", element: partial})
    message = str(excinfo.value)
    assert element in message
    missing = [k for k in ELEMENTS_BY_KEY[element].required if k not in partial]
    for key in missing:
        assert key in message, f"{key} not named in the refusal"


def test_a_partial_element_is_not_quietly_dropped():
    """Dropping it ships a video missing a grade somebody asked for."""
    with pytest.raises(LookDeclarationError):
        resolve_look({"name": "half", "glow": {"gain": 0.2}})


def test_a_three_channel_term_with_two_channels_is_refused():
    with pytest.raises(LookDeclarationError) as excinfo:
        resolve_look({"name": "short", "cdl": {
            "slope": [1.0, 1.0], "offset": [0, 0, 0],
            "power": [1, 1, 1], "saturation": 1.0}})
    assert "slope" in str(excinfo.value)


def test_an_unknown_element_is_refused_rather_than_ignored():
    with pytest.raises(LookDeclarationError) as excinfo:
        resolve_look({"name": "typo", "halation": {"amount": 0.2}})
    assert "halation" in str(excinfo.value)
    for known in ELEMENTS_BY_KEY:
        assert known in str(excinfo.value)


def test_an_unknown_sub_key_is_refused():
    with pytest.raises(LookDeclarationError) as excinfo:
        resolve_look({"name": "typo", "glow": {
            "gain": 0.2, "threshold": 0.7, "size": 3.0, "spread": 1.0}})
    assert "spread" in str(excinfo.value)


def test_a_look_that_declares_nothing_is_refused():
    """A named look drawing no pixel reads as a grade in every report."""
    with pytest.raises(LookDeclarationError) as excinfo:
        resolve_look({"name": "empty"})
    assert "draws nothing" in str(excinfo.value)


def test_a_look_with_no_name_is_refused():
    with pytest.raises(LookDeclarationError) as excinfo:
        resolve_look({"contrast": 0.1})
    assert "name" in str(excinfo.value)


def test_the_old_catalogue_name_says_what_replaced_it():
    """A template still naming pmk_default must not fail obscurely."""
    with pytest.raises(LookDeclarationError) as excinfo:
        resolve_look("pmk_default")
    message = str(excinfo.value)
    assert "DECLARATION, not a name" in message
    # And the refusal carries the shape, so the fix is in the error.
    assert "house_look:" in message
    assert "glow" in message


def test_a_declared_value_must_be_a_number_and_not_a_path():
    """The whole point of dropping the PowerGrade: a look is values in
    the declaration, reproducible with no file inside Resolve."""
    with pytest.raises(LookDeclarationError) as excinfo:
        resolve_look({"name": "path", "contrast": "/some/look.drx"})
    assert "must be a number" in str(excinfo.value)


def test_no_element_carries_a_default_or_a_bound():
    """How strong a glow is, and how far a slope may travel, are the
    declaring author's decisions. An engine-supplied range is a strength
    nobody chose arriving one level up."""
    source = inspect.getsource(resolve_look)
    for suspicious in ("min(", "max(", "clamp"):
        assert suspicious not in source, (
            f"resolve_look bounds a declared value with {suspicious!r}")
    look = resolve_look({"name": "extreme", "glow": {
        "gain": 9.0, "threshold": 0.0, "size": 40.0}})
    assert look.fusion()["glow_gain"] == 9.0


# ── The shape a template author reads ───────────────────────────────────

def test_the_documented_shape_names_every_element():
    text = describe_declaration_shape()
    for element in LOOK_ELEMENTS:
        assert element.key in text
        for key in element.required:
            assert key in text


def test_every_element_says_why_its_mechanism_carries_it():
    for element in LOOK_ELEMENTS:
        assert element.delivered_by in ("cdl", "fusion"), element.key
        assert len(element.why_here) > 60, element.key


def test_the_shape_in_the_docs_is_the_shape_the_reader_accepts():
    """Parse the documented example back through resolve_look's rules.

    A documented shape that the reader refuses is worse than none: it is
    the error message telling an author to write something that fails.
    """
    text = describe_declaration_shape()
    keys = set(re.findall(r"^\s{4}(\w+):", text, re.MULTILINE))
    assert keys == set(ELEMENTS_BY_KEY) | {"name", "intent"}


def test_a_resolved_look_is_a_declared_look():
    assert isinstance(resolve_look(FULL_DECLARATION), DeclaredLook)
    assert resolve_look(FULL_DECLARATION).declared == (
        "cdl", "contrast", "glow", "grain", "vignette", "exposure_reference")
