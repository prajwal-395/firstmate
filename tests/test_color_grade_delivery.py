"""The designed colour look must reach the picture, or say why it cannot.

The look is CDL plus Fusion and nothing else - there is no PowerGrade
route and no `.drx` in the repo. This covers both halves reaching the
timeline for a template that names a look, and what a template that
names none actually gets.
"""
import os
from unittest.mock import patch

import pytest
import yaml

from library.steps.step_5_01_color_grade.step import (
    GRADE_PIPELINE,
    GRADE_PIPELINE_DELIVERY,
    define_color_grade,
)
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.house_look import HOUSE_LOOKS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(REPO_ROOT, "library", "templates")


def _spec(house_look=""):
    with patch("library.steps.step_5_01_color_grade.step._estimate_exposure",
               return_value=0.0):
        return define_color_grade(
            {"entries": [{"track": "V1", "clip_id": "c1", "entry_id": "e1",
                          "source_file": "f1.mov"}]},
            project_folder="proj",
            house_look=house_look,
        )["color_grade_spec"]


def test_every_designed_node_states_how_it_is_delivered():
    assert set(GRADE_PIPELINE_DELIVERY) == set(GRADE_PIPELINE)
    for node, record in GRADE_PIPELINE_DELIVERY.items():
        # Delivered or not, every node says by what and why - four of the
        # five used to be four keys nothing in the repo read.
        assert len(record.get("reason", "")) > 40, node


def test_no_designed_node_is_delivered_by_a_powergrade():
    delivered = {r.get("delivered_by") for r in GRADE_PIPELINE_DELIVERY.values()}
    assert "powergrade_path" not in delivered
    assert all("drx" not in str(d).lower() for d in delivered)


def test_a_named_look_reaches_the_cdl():
    look = HOUSE_LOOKS["film_stock_warmth"]
    spec = _spec("film_stock_warmth")

    assert spec["house_look"] == "film_stock_warmth"
    cdl = spec["per_clip_adjustments"][0]["cdl_values"]
    # Exposure is 0.0 here, so the CDL is the look itself.
    assert cdl == look.cdl()
    # And it is a real look, not an identity: cream highlights means the
    # blue slope sits below the red one.
    assert cdl["slope_b"] < cdl["slope_r"]
    assert cdl["saturation"] != 1.0


def test_a_named_look_reaches_the_fusion_comp():
    """The Fusion half must draw nodes, not just appear in the JSON."""
    spec = _spec("warm_reflection")
    comp = build_effect_comp(dict(spec["fusion_look"]), 120)

    assert "BrightnessContrast" in comp   # node_3, pivot contrast
    assert "SoftGlow" in comp             # node_4, bloom
    assert "FilmGrain" in comp            # node_4, grain
    assert "EllipseMask" in comp          # node_4, vignette
    # The vignette is warm, not black - the falloff colour is the clearest
    # thing a CDL cannot express at all.
    assert "TopLeftRed" in comp


def test_exposure_normalisation_rides_on_top_of_the_look():
    look = HOUSE_LOOKS["pmk_default"]
    with patch("library.steps.step_5_01_color_grade.step._estimate_exposure",
               return_value=0.5):
        spec = define_color_grade(
            {"entries": [{"track": "V1", "clip_id": "c1", "entry_id": "e1",
                          "source_file": "f1.mov"}]},
            project_folder="proj",
            house_look="pmk_default",
        )["color_grade_spec"]

    cdl = spec["per_clip_adjustments"][0]["cdl_values"]
    gain = 2.0 ** 0.5
    assert cdl["slope_r"] == pytest.approx(round(look.slope[0] * gain, 4))
    # Exposure is a gain, so it must not disturb the look's hue balance.
    assert cdl["slope_r"] / cdl["slope_b"] == pytest.approx(
        look.slope[0] / look.slope[2], rel=1e-3)
    assert cdl["offset_r"] == pytest.approx(look.offset[0])


def test_a_template_naming_no_look_gets_exposure_and_nothing_else():
    spec = _spec("")

    assert spec["house_look"] is None
    assert spec["fusion_look"] == {}
    cdl = spec["per_clip_adjustments"][0]["cdl_values"]
    # Identity CDL: the clip is normalised, not graded.
    assert cdl == {
        "slope_r": 1.0, "slope_g": 1.0, "slope_b": 1.0,
        "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
        "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
        "saturation": 1.0,
    }
    assert "names no house look" in spec["look_notes"]
    # An empty Fusion half must not draw a grade node either.
    assert "BrightnessContrast" not in build_effect_comp(
        dict(spec["fusion_look"]), 120)


def test_an_unknown_look_fails_loudly():
    with pytest.raises(ValueError) as excinfo:
        _spec("moody_desaturated")
    assert "Unknown house look" in str(excinfo.value)


def test_compile_manifest_merges_the_look_onto_every_clip():
    from library.steps.step_5_04_compile_manifest.step import compile_manifest

    source = os.path.abspath(__file__)
    look = HOUSE_LOOKS["electric_contrast"]
    inputs = {
        "color_grade_spec": {
            "per_clip_adjustments": [],
            "house_look": look.name,
            "fusion_look": look.fusion(),
        },
        "audio_spine": {
            "structure": [{
                "block_type": "speech", "position": 1, "clip_id": "c1",
                "source_start": 2.417, "source_end": 12.417,
                "timeline_start": 0.0, "timeline_end": 10.0,
                "content": {"clip_id": "c1"},
            }],
            "frame_rate": 30.0,
        },
        "clip_catalog": [{"clip_id": "c1", "path": source,
                          "width": 1080, "height": 1920}],
        "a_roll_assignments": [{
            "spine_block_position": 1, "clip_id": "c1",
            "source_file": source, "video_in": 2.417, "video_out": 12.417,
            "timeline_start": 0.0, "timeline_end": 10.0,
        }],
        "b_roll_assignments": [], "transition_spec": [],
        "enhancement_spec": [], "sfx_spec": [], "audio_mix_spec": {},
    }
    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        manifest = compile_manifest("dummy")

    # The CDL half travels in color_grade, the Fusion half in the per-clip
    # effects. Both must be in the manifest the renderer reads.
    assert manifest["color_grade"]["house_look"] == "electric_contrast"
    per_clip = manifest["fusion_effects"]["per_clip"]
    assert per_clip, "the look reached no clip"
    for effects in per_clip.values():
        assert effects["film_grain"] is True
        assert effects["grade_contrast"] == pytest.approx(look.contrast)
        assert effects["glow_gain"] == pytest.approx(look.glow_gain)


def test_every_brand_template_names_a_look_in_the_vocabulary():
    """A template may not name a look that does not exist, or none at all.

    Two shipped templates named PowerGrades whose `.drx` was never added,
    which killed every run under them at the renderer; a third named
    nothing and delivered no look at all. Nothing caught either, because
    the templates were only ever read for their transition lists.
    """
    template_files = sorted(
        f for f in os.listdir(TEMPLATES_DIR) if f.endswith((".yaml", ".yml"))
    )
    assert template_files, "no brand templates found to check"

    problems = []
    for filename in template_files:
        with open(os.path.join(TEMPLATES_DIR, filename)) as handle:
            template = yaml.safe_load(handle) or {}
        name = (template.get("style") or {}).get("house_look", "")
        if not name:
            problems.append(f"{filename} names no house_look")
        elif name not in HOUSE_LOOKS:
            problems.append(f"{filename} names unknown look {name!r}")

    assert not problems, "brand template grades are broken:\n  " + "\n  ".join(problems)


def test_no_drx_survives_in_the_repo():
    """The one PowerGrade that shipped was a third-party gift with no
    written commercial licence. Nothing may put one back."""
    found = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv")]
        found.extend(os.path.join(root, f) for f in files if f.endswith(".drx"))
    assert not found, f"unlicensed PowerGrade assets are back: {found}"
