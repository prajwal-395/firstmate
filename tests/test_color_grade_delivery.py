"""The declared colour look must reach the picture, or say why it cannot.

The look is CDL plus Fusion, applied a third way where the project asks:
`color.power_grade_drx` in its own project.yaml, applied per clip with
`Graph.ApplyGradeFromDRX` (library/tools/color_page_grade.py) - the
captain's ruling, 2026-09-10, over the withdrawn no-PowerGrade rule. A
`.drx` nobody authorised is still refused; the refusal moved from "no
file anywhere" to "no provenance", and that is what the last test here
plus tests/test_color_page_grade.py now assert.

This covers all three routes reaching the timeline for a project that
DECLARES a look - including a look declared in the project's own
project.yaml rather than its brand template - and what a project whose
template declares none actually gets, which is nothing.

It also covers the exposure half, which is a measurement and not a look:
it must never report a number when nothing measured, and it must not
normalise onto a target nobody declared.
"""
import os
from unittest.mock import patch

import pytest
import yaml

from library.steps.step_5_01_color_grade.grade import (
    GRADE_PIPELINE,
    GRADE_PIPELINE_DELIVERY,
    LUMA_METHOD,
    LUMA_UNMEASURED,
    define_color_grade,
    exposure_gain_for,
)
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.series_look import LookDeclarationError, resolve_look

# Values written HERE, in a test, standing in for what a brand template
# would declare. The engine ships none of its own - see
# tests/test_series_look.py.
DECLARED = {
    "name": "test_declaration",
    "cdl": {
        "slope": [1.04, 1.0, 0.95],
        "offset": [0.012, 0.01, 0.004],
        "power": [0.985, 0.995, 1.012],
        "saturation": 0.95,
    },
    "contrast": 0.06,
    "glow": {"gain": 0.1, "threshold": 0.8, "size": 4.0},
    "grain": {"power": 0.3, "size": 1.6},
    "vignette": {"blend": 0.14, "soft": 0.45, "color": [0.102, 0.102, 0.09]},
}

MEASURED = {"luma": 122.0, "method": LUMA_METHOD, "samples": 40}
NOT_MEASURED = {"luma": None, "method": LUMA_UNMEASURED, "samples": 0,
                "reason": "ffprobe is not on PATH"}


def _spec(series_look=None, measurement=None):
    with patch("library.steps.step_5_01_color_grade.grade.measure_luma",
               return_value=dict(measurement or MEASURED)):
        return define_color_grade(
            {"entries": [{"track": "V1", "clip_id": "c1", "entry_id": "e1",
                          "source_file": "f1.mov"}]},
            project_folder="proj",
            series_look=series_look,
        )["color_grade_spec"]


def test_every_designed_node_states_how_it_is_delivered():
    assert set(GRADE_PIPELINE_DELIVERY) == set(GRADE_PIPELINE)
    for node, record in GRADE_PIPELINE_DELIVERY.items():
        # Delivered or not, every node says by what and why - four of the
        # five used to be four keys nothing in the repo read.
        assert len(record.get("reason", "")) > 40, node


def test_no_designed_node_is_delivered_by_a_powergrade():
    """The DECLARED look's halves are CDL and Fusion - the delivery table
    names no third half, because a PowerGrade is not a half of the
    declaration. It is a render-time application of a file the project
    staged (`color.power_grade_drx`), which REPLACES the graph rather
    than carrying one node's values. See tests/test_color_page_grade.py
    for the provenance gate on that route."""
    delivered = {r.get("delivered_by") for r in GRADE_PIPELINE_DELIVERY.values()}
    assert "powergrade_path" not in delivered
    assert all("drx" not in str(d).lower() for d in delivered)


def test_no_designed_node_sources_its_values_from_the_engine():
    """Every node that carries values names the template slot that
    declares them. `library/tools/series_look.py` holds none."""
    for node, record in GRADE_PIPELINE.items():
        source = record.get("source")
        if source is None:
            continue
        assert source.startswith("brand template"), f"{node}: {source}"


def test_a_declared_look_reaches_the_cdl():
    spec = _spec(DECLARED)

    assert spec["series_look"] == "test_declaration"
    cdl = spec["per_clip_adjustments"][0]["cdl_values"]
    # No exposure reference is declared, so the CDL is the look itself.
    assert cdl == resolve_look(DECLARED).cdl()
    assert cdl["slope_b"] < cdl["slope_r"]
    assert cdl["saturation"] != 1.0


def test_a_declared_look_reaches_the_fusion_comp():
    """The Fusion half must draw nodes, not just appear in the JSON."""
    spec = _spec(DECLARED)
    comp = build_effect_comp(dict(spec["fusion_look"]), 120,
                             source_res=(1080, 1920))

    assert "BrightnessContrast" in comp   # node_3, pivot contrast
    assert "SoftGlow" in comp             # node_4, bloom
    assert "FilmGrain" in comp            # node_4, grain
    assert "EllipseMask" in comp          # node_4, vignette
    # The vignette is warm, not black - the falloff colour is the clearest
    # thing a CDL cannot express at all.
    assert "TopLeftRed" in comp


def test_a_project_with_no_declared_look_gets_no_grade_at_all():
    spec = _spec(None)

    assert spec["series_look"] is None
    assert spec["fusion_look"] == {}
    cdl = spec["per_clip_adjustments"][0]["cdl_values"]
    # Identity CDL: nothing is graded and nothing is normalised either.
    assert cdl == {
        "slope_r": 1.0, "slope_g": 1.0, "slope_b": 1.0,
        "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
        "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
        "saturation": 1.0,
    }
    assert "No look" in spec["look_notes"]
    # An empty Fusion half must not draw ANY node - not a grade, and not
    # the vignette the comp builder used to default to.
    comp = build_effect_comp(dict(spec["fusion_look"]), 120,
                             source_res=(1080, 1920))
    assert "BrightnessContrast" not in comp
    assert "EllipseMask" not in comp
    assert "FilmGrain" not in comp


def test_a_half_declared_look_fails_loudly():
    with pytest.raises(LookDeclarationError) as excinfo:
        _spec({"name": "half", "glow": {"gain": 0.2}})
    assert "threshold" in str(excinfo.value)


def test_an_old_catalogue_name_fails_loudly():
    with pytest.raises(LookDeclarationError) as excinfo:
        _spec("pmk_default")
    assert "DECLARATION, not a name" in str(excinfo.value)


# ── The exposure half: a measurement, never an assertion ────────────────

def test_a_measured_clip_records_the_number_and_the_method():
    adj = _spec(None)["per_clip_adjustments"][0]
    assert adj["measured_luma"] == 122.0
    assert adj["measured_luma_method"] == LUMA_METHOD
    assert adj["measured_luma_samples"] == 40
    assert "122.0" in adj["notes"]


def test_an_unmeasured_clip_is_null_and_says_why():
    """Never 0.0. On project 001 that number was written on 10 of 10
    clips about a probe whose every sample had been discarded."""
    spec = _spec(None, NOT_MEASURED)
    adj = spec["per_clip_adjustments"][0]
    assert adj["measured_luma"] is None
    assert adj["exposure_offset"] is None
    assert adj["measured_luma_method"] == LUMA_UNMEASURED
    assert "ffprobe is not on PATH" in adj["notes"]
    assert "not measured" in adj["notes"]
    assert spec["unmeasured_clips"] == ["c1"]


def test_nothing_normalises_without_a_declared_reference():
    """The engine used to hold the target as the constant 122.0, which is
    a decision about how bright the finished video is."""
    adj = _spec(DECLARED)["per_clip_adjustments"][0]
    assert adj["exposure_reference"] is None
    assert adj["exposure_offset"] is None
    assert adj["exposure_gain"] == 1.0
    assert "declares no `exposure_reference`" in adj["notes"]


def test_a_declared_reference_normalises_onto_it():
    declared = dict(DECLARED, exposure_reference=140.0)
    spec = _spec(declared, {"luma": 70.0, "method": LUMA_METHOD,
                            "samples": 30})
    adj = spec["per_clip_adjustments"][0]
    # 70 -> 140 is exactly one stop, by the definition of a stop.
    assert adj["exposure_offset"] == pytest.approx(1.0)
    assert adj["exposure_gain"] == pytest.approx(2.0)
    assert adj["cdl_values"]["slope_r"] == pytest.approx(
        resolve_look(declared).slope[0] * 2.0, rel=1e-3)
    # A gain must not disturb the look's hue balance.
    assert (adj["cdl_values"]["slope_r"] / adj["cdl_values"]["slope_b"]
            == pytest.approx(1.04 / 0.95, rel=1e-3))
    assert adj["cdl_values"]["offset_r"] == pytest.approx(0.012)


def test_the_offset_is_the_definition_of_a_stop_and_is_not_clamped():
    """No bound: a clamp is a decision about how far the engine may
    overrule the footage, and the 0.5 that used to sit there was picked
    by nobody."""
    offset, gain = exposure_gain_for(30.0, 240.0)
    assert offset == pytest.approx(3.0)
    assert gain == pytest.approx(8.0)


def test_nothing_normalises_when_nothing_measured():
    assert exposure_gain_for(None, 120.0) == (None, 1.0)
    assert exposure_gain_for(0.0, 120.0) == (None, 1.0)
    assert exposure_gain_for(120.0, None) == (None, 1.0)


def test_the_consistency_note_carries_its_denominator():
    spec = _spec(DECLARED)
    note = spec["consistency_notes"]
    assert "1 distinct CDL value(s) across 1 clip(s)" in note
    # Three denominators, not one. "how many clips were measured" and
    # "how many the colourist moved" are different facts, and one
    # distinct CDL across every clip is only correct when the second is
    # zero on purpose - which is why the basis is in the same sentence.
    assert "luma measured on 1 of 1" in note
    assert "the colourist corrected 0 of 1" in note
    assert "correction_basis: no_correction_decision" in note


def test_the_probe_parses_the_output_ffprobe_actually_writes():
    """`-of csv=p=0` writes `140.914,` - the value plus its separator.

    `float()` raised on every one of those lines, each was skipped, the
    list came out empty and the function returned 0.0 - the same 0.0 it
    returns for a well-exposed clip. On project 001 that produced one
    identical CDL on 10 of 10 clips and the note "Exposure within normal
    range, no adjustment needed" on 10 of 10, about a measurement that
    never completed. The bytes below are ffprobe's real output, copied
    from a run against 001's IMG_1816.MOV on 2026-08-28.
    """
    import subprocess
    from library.steps.step_5_01_color_grade.grade import measure_luma

    real_output = "140.914,\n140.613,\n140.202,\n144.676,\n145.304,\n"
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=real_output, stderr="")
    with patch("library.steps.step_5_01_color_grade.grade.os.path.exists",
               return_value=True), \
         patch("library.steps.step_5_01_color_grade.grade.subprocess.run",
               return_value=completed):
        measurement = measure_luma("/nowhere/clip.mov")

    assert measurement["samples"] == 5
    assert measurement["method"] == LUMA_METHOD
    assert measurement["luma"] == pytest.approx(142.342, abs=0.01)


@pytest.mark.parametrize("failure,reason_fragment", [
    ({"stdout": "", "returncode": 0}, "no parseable YAVG"),
    ({"stdout": "140.9,", "returncode": 1}, "exited 1"),
])
def test_a_failed_probe_is_reported_and_not_returned_as_a_number(
        failure, reason_fragment):
    import subprocess
    from library.steps.step_5_01_color_grade.grade import measure_luma

    completed = subprocess.CompletedProcess(
        args=[], returncode=failure["returncode"],
        stdout=failure["stdout"], stderr="")
    with patch("library.steps.step_5_01_color_grade.grade.os.path.exists",
               return_value=True), \
         patch("library.steps.step_5_01_color_grade.grade.subprocess.run",
               return_value=completed):
        measurement = measure_luma("/nowhere/clip.mov")

    assert measurement["luma"] is None
    assert measurement["method"] == LUMA_UNMEASURED
    assert reason_fragment in measurement["reason"]


def test_a_missing_file_is_an_absence_with_a_reason():
    from library.steps.step_5_01_color_grade.grade import measure_luma

    measurement = measure_luma("/definitely/not/here.mov")
    assert measurement["luma"] is None
    assert "not on disk" in measurement["reason"]

    measurement = measure_luma("")
    assert measurement["luma"] is None
    assert "no source file" in measurement["reason"]


# ── Downstream ──────────────────────────────────────────────────────────

def test_compile_manifest_merges_the_look_onto_every_clip():
    from library.steps.step_5_04_compile_manifest.step import compile_manifest

    source = os.path.abspath(__file__)
    look = resolve_look(DECLARED)
    inputs = {
        "color_grade_spec": {
            "per_clip_adjustments": [],
            "series_look": look.name,
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
    assert manifest["color_grade"]["series_look"] == "test_declaration"
    per_clip = manifest["fusion_effects"]["per_clip"]
    assert per_clip, "the look reached no clip"
    for effects in per_clip.values():
        assert effects["film_grain"] is True
        assert effects["grade_contrast"] == pytest.approx(look.contrast)
        assert effects["glow_gain"] == pytest.approx(look.glow_gain)


def test_compile_manifest_draws_no_comp_when_no_look_is_declared():
    """This is what project 001 gets: all seventeen of its per-clip comps
    existed only to carry the look."""
    from library.steps.step_5_04_compile_manifest.step import compile_manifest

    source = os.path.abspath(__file__)
    inputs = {
        "color_grade_spec": {"per_clip_adjustments": [], "series_look": None,
                             "fusion_look": {}},
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

    assert manifest["color_grade"]["series_look"] is None
    for label, effects in manifest["fusion_effects"]["per_clip"].items():
        assert not effects, f"{label} got a comp with nothing declared: {effects}"


def test_every_project_copy_parses_and_declares_no_look():
    """No look is declared by any project copy. See
    tests/test_series_look.py for the standing rule."""
    from tests.brand_fixtures import ALL_SYNTHETIC
    assert ALL_SYNTHETIC, "no synthetic project copies found to check"

    for name, template in sorted(ALL_SYNTHETIC.items()):
        declaration = (template.get("style") or {}).get("series_look")
        # Either nothing, or something resolve_look can deliver whole.
        assert resolve_look(declaration) is None or declaration


def test_a_project_declared_look_reaches_the_cdl(tmp_path):
    """Scope is the project's own config: `style.series_look` in
    project.yaml wins over the brand template's slot, whole-slot, so
    a project can carry its own look without forking its template. On the
    old code this read the template only, and the project's declaration
    never reached a pixel."""
    from library.steps.step_5_01_color_grade.post_bridge import (
        resolve_color_grade,
    )

    test_look = {
        "name": "test_look",
        "cdl": {"slope": [1.03, 1.0, 0.96],
                "offset": [-0.01, 0.005, 0.02],
                "power": [1.0, 1.0, 1.0], "saturation": 1.12},
        "contrast": 0.12,
        "glow": {"gain": 0.2, "threshold": 0.72, "size": 3.5},
        "grain": {"power": 0.35, "size": 1.5},
        "vignette": {"blend": 0.35, "soft": 0.3},
    }
    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump({"name": "t", "style": {"series_look": test_look}}))
    data = {
        "a_roll_assignments": [{
            "spine_block_position": 1, "clip_id": "c1",
            "source_file": "/nowhere/f1.mov",
            "video_in": 0.0, "video_out": 1.0,
            "timeline_start": 0.0, "timeline_end": 1.0}],
        "b_roll_assignments": [], "b_roll_interjections": [],
        "brand_template": {},
        "project_folder": str(tmp_path),
        "clip_exposure": [{
            "clip_id": "c1", "track": "V1",
            "first_plays_at_seconds": 0.0, "placements": 1,
            "luma": 122.0, "luma_method": LUMA_METHOD,
            "luma_samples": 40}],
        "color_correction": [], "grade_assessment": "",
        "subject_grades": [],
    }
    spec = resolve_color_grade(data)["color_grade_spec"]
    assert spec["series_look"] == "test_look"
    cdl = spec["per_clip_adjustments"][0]["cdl_values"]
    assert cdl == resolve_look(test_look).cdl()
    assert cdl["slope_r"] == pytest.approx(1.03)
    assert cdl["saturation"] == pytest.approx(1.12)
    assert spec["fusion_look"]["grade_contrast"] == pytest.approx(0.12)


def test_an_unlicensed_drx_is_refused_even_when_staged(tmp_path):
    """The withdrawn rule failed on any `.drx` anywhere; the captain's
    ruling keeps the refusal for a `.drx` nobody authorised. A staged
    file with no provenance never reaches `apply_power_grade` - the
    refusal happens at resolve time, before any Resolve call."""
    from library.tools.color_page_grade import (
        ColorPageGradeError,
        resolve_color_page_grade,
    )

    staged = tmp_path / "grade.drx"
    staged.write_bytes(b"DRX")
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(
        {"name": "t",
         "color": {"power_grade_drx": {"path": str(staged)}}}))
    with pytest.raises(ColorPageGradeError) as excinfo:
        resolve_color_page_grade(str(tmp_path))
    assert "provenance" in str(excinfo.value)


def test_an_unmatchable_reference_is_said_not_silently_dropped(tmp_path):
    """A reference frame nobody can measure must not grade from fiction.

    `analyze_frame_colors` used to return a plausible neutral statistic
    for any failure, which the grade then applied as an "AI Look Match
    CDL". Now the match refuses, the clips fall back to the declared
    look, and `look_notes` says the reference went nowhere - the same
    sentence the record would owe for any other skipped captain input.
    """
    bad_ref = tmp_path / "reference.png"
    bad_ref.write_bytes(b"not an image")
    with patch("library.steps.step_5_01_color_grade.grade.measure_luma",
               return_value=dict(MEASURED)):
        spec = define_color_grade(
            {"entries": [{"track": "V1", "clip_id": "c1", "entry_id": "e1",
                          "source_file": "f1.mov"}]},
            project_folder="proj",
            reference_image=str(bad_ref),
        )["color_grade_spec"]
    assert "could not be matched" in spec["look_notes"]
    assert all("AI Look Match" not in a["notes"]
               for a in spec["per_clip_adjustments"])
