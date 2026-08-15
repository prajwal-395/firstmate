"""The designed colour look must reach the picture, or say why it cannot.

The grade is designed in five nodes and one of them was applied. This
covers the two halves of the fix: node_4's film look becomes Fusion
parameters that the comp engine actually draws, and every node records
whether it is delivered and by what.
"""
import os
from unittest.mock import patch

import pytest

from library.steps.step_5_01_color_grade.step import (
    GRADE_PIPELINE,
    GRADE_PIPELINE_DELIVERY,
    _fusion_look,
    _midpoint,
    define_color_grade,
)
from library.tools.fusion.comp_builder import build_effect_comp


def test_every_designed_node_states_how_it_is_delivered():
    assert set(GRADE_PIPELINE_DELIVERY) == set(GRADE_PIPELINE)
    for node, record in GRADE_PIPELINE_DELIVERY.items():
        if record.get("delivered_by"):
            continue
        # An undelivered node must say why, so the gap is visible rather
        # than being four keys nothing in the repo reads.
        assert len(record.get("reason", "")) > 40, node


def test_the_film_look_is_derived_from_the_design_not_invented():
    look = _fusion_look(GRADE_PIPELINE)
    # glow_opacity "10-15%" -> 0.125
    assert look["glow_gain"] == pytest.approx(0.125)
    # grain_amount "0.2-0.3" -> 0.25
    assert look["film_grain_power"] == pytest.approx(0.25)
    # vignette_amount "0.15-0.20" -> 0.175
    assert look["vignette_blend"] == pytest.approx(0.175)


def test_changing_the_design_moves_the_look():
    louder = {**GRADE_PIPELINE, "node_4": {
        **GRADE_PIPELINE["node_4"], "glow_opacity": "40-50%"}}
    assert _fusion_look(louder)["glow_gain"] > _fusion_look(GRADE_PIPELINE)["glow_gain"]


def test_midpoint_handles_both_range_forms():
    assert _midpoint("10-15%", 0.0) == pytest.approx(0.125)
    assert _midpoint("0.2-0.3", 0.0) == pytest.approx(0.25)
    assert _midpoint("optional_subtle", 0.42) == 0.42


def test_the_film_look_draws_glow_grain_and_vignette():
    comp = build_effect_comp(dict(_fusion_look(GRADE_PIPELINE)), 120)
    assert "SoftGlow" in comp
    assert "FilmGrain" in comp or "Grain" in comp
    assert "EllipseMask" in comp


@patch("library.steps.step_5_01_color_grade.step._estimate_exposure",
       return_value=0.0)
def test_the_spec_carries_the_look_and_no_fake_powergrade(_mock):
    spec = define_color_grade(
        {"entries": [{"track": "V1", "clip_id": "c1", "entry_id": "e1",
                      "source_file": "f1.mov"}]},
        project_folder="proj",
    )["color_grade_spec"]

    assert spec["fusion_look"]["film_grain"] is True
    assert spec["grade_pipeline_delivery"]["node_4"]["delivered_by"] == "fusion_look"
    # library/presets/powergrades/default.drx is a placeholder text file,
    # not a real .drx, so pointing at it would be a promise nothing keeps.
    assert spec["powergrade_path"] is None


def test_node_2s_shadow_lift_reaches_the_cdl():
    lift = GRADE_PIPELINE["node_2"]["lift_shadows"]
    assert lift > 0
    with patch("library.steps.step_5_01_color_grade.step._estimate_exposure",
               return_value=0.0):
        spec = define_color_grade(
            {"entries": [{"track": "V1", "clip_id": "c1", "entry_id": "e1",
                          "source_file": "f1.mov"}]},
            project_folder="proj",
        )["color_grade_spec"]
    cdl = spec["per_clip_adjustments"][0]["cdl_values"]
    # The green channel carries the lift alone - no white balance on it.
    assert cdl["offset_g"] == pytest.approx(lift)


def test_compile_manifest_merges_the_look_onto_every_clip():
    from library.steps.step_5_04_compile_manifest.step import compile_manifest

    source = os.path.abspath(__file__)
    inputs = {
        "color_grade_spec": {
            "per_clip_adjustments": [],
            "fusion_look": {"film_grain": True, "glow_gain": 0.125},
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

    per_clip = manifest["fusion_effects"]["per_clip"]
    assert per_clip, "the look reached no clip"
    for effects in per_clip.values():
        assert effects["film_grain"] is True
        assert effects["glow_gain"] == pytest.approx(0.125)


def test_every_brand_template_powergrade_resolves_to_a_real_asset():
    """A template may not name a PowerGrade that is not on disk.

    step_5_01 turns `style.preferred_powergrade` into
    library/presets/powergrades/<slug>.drx and puts that path in the
    manifest even when it does not exist, so the renderer fails loudly
    (resolve_build_timeline raises FileNotFoundError before applying the
    grade). Two of the four shipped templates named grades that were never
    added, which meant no run under those templates could produce a video
    at all - and nothing checked, because the templates were only ever
    read for their transition lists.
    """
    import yaml

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates_dir = os.path.join(repo_root, "library", "templates")
    powergrades_dir = os.path.join(
        repo_root, "library", "presets", "powergrades")

    template_files = sorted(
        f for f in os.listdir(templates_dir) if f.endswith((".yaml", ".yml"))
    )
    assert template_files, "no brand templates found to check"

    missing = []
    for filename in template_files:
        with open(os.path.join(templates_dir, filename)) as handle:
            template = yaml.safe_load(handle) or {}
        name = (template.get("style") or {}).get("preferred_powergrade", "")
        if not name:
            continue
        # Same slug rule as step_5_01_color_grade.define_color_grade.
        slug = name.lower().replace(" ", "_")
        drx = os.path.join(powergrades_dir, f"{slug}.drx")
        if not os.path.exists(drx):
            missing.append(f"{filename} names {name!r} -> missing {drx}")

    assert not missing, (
        "brand templates name PowerGrades with no asset on disk:\n  "
        + "\n  ".join(missing)
    )
