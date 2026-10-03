"""Finding 28: verify grades on exported pixels, not on the SetCDL return.

Scout B5 (VX1.2): saturation 0.88 plus power/slope reached SetCDL on
every clip, but the export measured a chroma ratio of 0.96-1.03
(expected ~0.88); B1 (CO2.3): +1.3 stops moved luma 43 -> 82 where
~130 was the target. The build reported "Applied" off the write.

Two halves:

- `cdl_readback.compare_cdl`: the build judges the WRITE by reading
  `GetCDL()` back against the four specified terms - a mismatch
  errors naming the clip, an unreadable read-back warns.
- `render_qa.measure_grade_delivery`: 6.02 judges the PIXELS. Each
  graded span's export window is measured against the SOURCE file it
  was cut from, and the direction the CDL demands is checked with a
  dead zone. REPORTED per span, never gated.

The videos here are generated with ffmpeg (a declared dependency;
skipped where it is absent): a saturated source, a desaturated
"graded" export of it, and an ungraded copy.
"""
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
import os
from unittest.mock import patch
import yaml
from library.steps.step_5_01_color_grade.grade import (
    LUMA_METHOD,
    LUMA_UNMEASURED,
    define_color_grade,
    exposure_gain_for,
)
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.series_look import resolve_look
from library.tools.color_page_grade import (
    ColorPageGradeError,
    apply_power_grade,
    resolve_color_page_grade,
)
import inspect
from library.tools.series_look import (
    ELEMENTS_BY_KEY,
    LookDeclarationError,
)
import json


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ffmpeg = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not ffmpeg, reason="ffmpeg absent")


def _gen(path, sat=None, duration=2.0):
    vf = f",eq=saturation={sat}" if sat is not None else ""
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
           f"color=c=0xC04020:s=64x64:d={duration}:r=10",
           "-vf", f"format=yuv444p{vf},format=yuv444p",
           "-frames:v", "20", "-pix_fmt", "yuv444p", str(path)]
    subprocess.run(cmd, check=True, timeout=120)


@pytest.fixture
def trio(tmp_path):
    _gen(tmp_path / "source.mp4")
    _gen(tmp_path / "graded.mp4", sat=0.5)
    _gen(tmp_path / "ungraded.mp4")
    return tmp_path


def _span(label, export, source, sat=0.88, slope=1.0,
          source_start=0.0, source_end=2.0):
    from library.tools.render_qa import GradeSpan
    return GradeSpan(label=label, timeline_start=0.0, timeline_end=2.0,
                     source_path=str(source),
                     cdl={"slope_r": slope, "slope_g": slope,
                          "slope_b": slope, "offset_r": 0.0,
                          "offset_g": 0.0, "offset_b": 0.0,
                          "power_r": 1.0, "power_g": 1.0,
                          "power_b": 1.0, "saturation": sat},
                     source_start=source_start, source_end=source_end)


# ── cdl_readback: the write is judged ────────────────────────────

def test_mismatched_slope_names_the_channel():
    from library.tools import cdl_readback as cdl
    cdl_values = {"slope_r": 1.08, "slope_g": 1.0, "slope_b": 1.0,
                  "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
                  "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
                  "saturation": 1.0}
    actual = {"Slope": "1.0000 1.0000 1.0000",
              "Offset": "0.0000 0.0000 0.0000",
              "Power": "1.0000 1.0000 1.0000",
              "Saturation": "1.0"}
    (problem,) = cdl.compare_cdl(actual, cdl_values)
    assert "Slope[0]" in problem
    # The same read-back agrees once the write took.
    actual["Slope"] = "1.0800 1.0000 1.0000"
    assert cdl.compare_cdl(actual, cdl_values) == []


def test_unreadable_term_is_unverifiable_not_agreement():
    from library.tools import cdl_readback as cdl
    problems = cdl.compare_cdl({}, {"saturation": 0.88})
    assert problems and all("unverifiable" in p for p in problems)


# ── measure_grade_delivery: the pixels are judged ────────────────

@needs_ffmpeg
def test_desaturated_export_delivers_a_desat_demand(trio):
    """The B5 demand (sat 0.88) against pixels that really moved:
    delivered."""
    from library.tools.render_qa import measure_grade_delivery
    result = measure_grade_delivery(
        str(trio / "graded.mp4"),
        [_span("clip_a", trio / "graded.mp4", trio / "source.mp4")],
        sample_fps=5.0)
    assert result.passed
    (row,) = result.value["spans"]
    assert row["verdict"] == "delivered"
    assert row["chroma_ratio"] < 0.99


@needs_ffmpeg
def test_unmoved_export_contradicts_a_desat_demand(trio):
    """Finding 28's exact shape: sat 0.88 specified, export chroma
    unmoved - contradicts, and the detail says the direction."""
    from library.tools.render_qa import measure_grade_delivery
    result = measure_grade_delivery(
        str(trio / "ungraded.mp4"),
        [_span("clip_a", trio / "ungraded.mp4", trio / "source.mp4")],
        sample_fps=5.0)
    assert not result.passed
    (row,) = result.value["spans"]
    assert row["verdict"] == "contradicts"
    assert "down" in row["detail"]


@needs_ffmpeg
def test_identity_cdl_demands_nothing_and_a_missing_source_is_unverifiable(
        trio):
    from library.tools.render_qa import measure_grade_delivery
    result = measure_grade_delivery(
        str(trio / "ungraded.mp4"),
        [_span("clip_a", trio / "ungraded.mp4", trio / "source.mp4",
               sat=1.0)],
        sample_fps=5.0)
    assert result.passed
    assert result.value["spans"][0]["verdict"] == "no demand"
    # A span whose source is gone cannot be judged, and says so.
    result = measure_grade_delivery(
        str(trio / "graded.mp4"),
        [_span("clip_a", trio / "graded.mp4", trio / "gone.mp4")],
        sample_fps=5.0)
    (row,) = result.value["spans"]
    assert row["verdict"] == "unverifiable"


def test_no_spans_is_info_not_failure():
    from library.tools.render_qa import measure_grade_delivery
    result = measure_grade_delivery("/nonexistent.mp4", [])
    assert result.passed and result.severity == "info"


@needs_ffmpeg
def test_source_reference_is_the_played_range_not_the_file(tmp_path):
    """The proof-timeline lesson: four items playing source 0-5s read
    1.11/2.01/2.23/2.23 against the whole-file median (content
    aliasing), 0.499/0.904/1.0 same-range. A two-tone source proves
    the window: the second-half reference must read dark."""
    from library.tools.render_qa import _median_luma_and_chroma
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
           "color=c=0xFFFFFF:s=64x64:d=1:r=10",
           "-f", "lavfi", "-i", "color=c=0x202020:s=64x64:d=1:r=10",
           "-filter_complex",
           "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv444p",
           "-frames:v", "20", "-pix_fmt", "yuv444p",
           str(tmp_path / "duo.mp4")]
    subprocess.run(cmd, check=True, timeout=120)
    size = (64, 64)
    first, _ = _median_luma_and_chroma(
        str(tmp_path / "duo.mp4"), *size, 10.0,
        start_seconds=0.0, duration_seconds=1.0)
    second, _ = _median_luma_and_chroma(
        str(tmp_path / "duo.mp4"), *size, 10.0,
        start_seconds=1.0, duration_seconds=1.0)
    assert first > 200.0 and second < 60.0


# ── _grade_spans: the manifest join ──────────────────────────────

def test_grade_spans_join_tracks_to_per_clip_cdl():
    from library.steps.step_6_02_validate_output.bridge import _grade_spans
    manifest = {
        "project": {"frame_rate": 30.0},
        "tracks": {
            "V1": {"clips": [
                {"label": "hook", "source_file": "/raw/IMG_A.MOV",
                 "timeline_in": 0.0, "timeline_end_missing": True,
                 "timeline_out": 2.4}]},
            "V2": {"clips": [
                {"label": "broll", "source_file": "/raw/IMG_B.MOV",
                 "timeline_in": 1.0, "timeline_out": 2.0}]}},
        "color_grade": {"per_clip_adjustments": [
            {"source_file": "/raw/IMG_A.MOV",
             "cdl_values": {"saturation": 0.88}},
            {"source_file": "/raw/IMG_B.MOV", "cdl_values": {}}]},
    }
    spans = _grade_spans(manifest)
    assert [(s.label, s.timeline_start, s.timeline_end) for s in spans] == [
        ("hook", 0.0, 2.4), ("broll", 1.0, 2.0)]
    assert spans[0].cdl["saturation"] == 0.88
    assert (spans[0].source_start, spans[0].source_end) == (None, None)
    # Track ranges ride into the span: the reference is the played
    # range, never the whole file.
    manifest["tracks"]["V1"]["clips"][0]["source_in"] = 0.836
    manifest["tracks"]["V1"]["clips"][0]["source_out"] = 3.234
    spans = _grade_spans(manifest)
    assert (spans[0].source_start, spans[0].source_end) == (0.836, 3.234)
    # Without a declared grade there is nothing to join.
    assert _grade_spans({"tracks": {}}) is None


# --------------------------------------------------------------------------
# From test_color_grade_delivery.py
#
# The declared colour look must reach the picture, or say why it cannot.
#
# The look is CDL plus Fusion, applied a third way where the project asks:
# `color.power_grade_drx` in its own project.yaml, applied per clip with
# `Graph.ApplyGradeFromDRX` (library/tools/color_page_grade.py) - the
# captain's ruling, 2026-09-10, over the withdrawn no-PowerGrade rule. A
# `.drx` nobody authorised is still refused; the refusal moved from "no
# file anywhere" to "no provenance", and that is what the last test here
# plus tests/unit/picture/test_grade_delivery.py now assert.
#
# This covers all three routes reaching the timeline for a project that
# DECLARES a look - including a look declared in the project's own
# project.yaml rather than its brand template - and what a project whose
# template declares none actually gets, which is nothing.
#
# It also covers the exposure half, which is a measurement and not a look:
# it must never report a number when nothing measured, and it must not
# normalise onto a target nobody declared.

# Values written HERE, in a test, standing in for what a brand template
# would declare. The engine ships none of its own - see
# tests/unit/picture/test_grade_delivery.py.
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


def test_the_offset_is_the_definition_of_a_stop_and_is_not_clamped():
    """No bound: a clamp is a decision about how far the engine may
    overrule the footage, and the 0.5 that used to sit there was picked
    by nobody."""
    offset, gain = exposure_gain_for(30.0, 240.0)
    assert offset == pytest.approx(3.0)
    assert gain == pytest.approx(8.0)


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


# --------------------------------------------------------------------------
# From test_color_page_grade.py
#
# A project's PowerGrade declaration: provenance-gated, refused when bare.
#
# Covers `library/tools/color_page_grade.py`: an absent declaration applies
# nothing, a malformed one raises before any Resolve call, and the apply
# record says what happened rather than asserting it.

def _project(tmp_path, color_block) -> str:
    """A project dir whose project.yaml carries `color:` as given."""
    if color_block is None:
        (tmp_path / "project.yaml").write_text(
            yaml.safe_dump({"name": "t", "pipeline": {}}))
    else:
        (tmp_path / "project.yaml").write_text(
            yaml.safe_dump({"name": "t", "color": color_block}))
    return str(tmp_path)


def _drx(tmp_path, name="grade.drx") -> str:
    path = tmp_path / name
    path.write_bytes(b"DRX")
    return str(path)


PROVENANCE = {"source": "GUI-built", "authorised_by": "captain, 2026-09-10",
              "licence": "captain's own asset"}


def test_no_declaration_applies_nothing(tmp_path):
    assert resolve_color_page_grade(_project(tmp_path, None)) is None


def test_a_declared_grade_resolves_to_an_absolute_path(tmp_path):
    drx = _drx(tmp_path)
    resolved = resolve_color_page_grade(_project(
        tmp_path, {"power_grade_drx": {"path": drx,
                                       "provenance": dict(PROVENANCE)}}))
    assert resolved["path"] == drx
    assert resolved["provenance"]["authorised_by"] == "captain, 2026-09-10"
    # A relative path resolves inside the project.
    (tmp_path / "brand_assets").mkdir()
    (tmp_path / "brand_assets" / "v04.drx").write_bytes(b"DRX")
    resolved = resolve_color_page_grade(_project(
        tmp_path, {"power_grade_drx": {
            "path": "brand_assets/v04.drx",
            "provenance": dict(PROVENANCE)}}))
    assert resolved["path"] == str(tmp_path / "brand_assets" / "v04.drx")


def test_a_path_with_no_provenance_is_refused(tmp_path):
    """The replacement for the withdrawn no-drx rule: the old test failed
    on any `.drx` anywhere; the new rule fails on a `.drx` nobody
    authorised."""
    drx = _drx(tmp_path)
    with pytest.raises(ColorPageGradeError) as excinfo:
        resolve_color_page_grade(_project(
            tmp_path, {"power_grade_drx": {"path": drx}}))
    assert "provenance" in str(excinfo.value)
    # Provenance with a hole is refused the same way.
    for provenance in ({}, {"source": "x", "authorised_by": "y"}):
        with pytest.raises(ColorPageGradeError) as excinfo:
            resolve_color_page_grade(_project(
                tmp_path, {"power_grade_drx": {"path": drx,
                                               "provenance": provenance}}))
        assert "provenance" in str(excinfo.value)


# ── The apply record ─────────────────────────────────────────────


class _Graph:
    def __init__(self, applied=True):
        self._applied = applied

    def ApplyGradeFromDRX(self, path, mode):
        assert mode == 0
        self.calls = (path, mode)
        return self._applied


class _Item:
    def __init__(self, graph):
        self._graph = graph

    def GetNodeGraph(self):
        return self._graph


def test_a_false_return_or_no_graph_is_reported_not_raised():
    for item in (_Item(_Graph(applied=False)), _Item(None)):
        record = apply_power_grade(item, "/grade/v04.drx")
        assert record["applied"] is False
        assert "reason" in record


def test_repo_wide_drx_files_need_recorded_provenance():
    """Any `.drx` IN THIS REPO must be named in AGENTS.md 11 with its
    source, authorisation and licence. The captain's own file proved the
    route from outside the repo; nothing ships one without the recording
    the captain's ruling still requires."""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout.split("\0")
    found = [os.path.join(repo_root, path) for path in tracked
             if path.endswith(".drx")]
    if not found:
        return
    with open(os.path.join(repo_root, "AGENTS.md"),
              encoding="utf-8") as handle:
        agents = handle.read()
    for path in found:
        name = os.path.basename(path)
        assert name in agents, (
            f"{path} ships with no provenance recorded in AGENTS.md 11")


# --------------------------------------------------------------------------
# From test_series_look.py
#
# A look is DECLARED, whole, by a template - or it is not drawn at all.
#
# `library/tools/series_look.py` used to be a catalogue of four looks whose
# strengths this repository authored. It is now a reader for a declaration
# a brand template writes, and these tests hold it to three things:
#
# 1. the engine ships no look values, and none were relocated into a
#    template;
# 2. every parameter a declaration emits draws a real Fusion node, which is
#    the failure mode this pipeline keeps hitting (`zoom_percent`,
#    `intensity_px` and `scale_factor` killed three VFX types that way, and
#    four of the colour grade's five nodes had no reader at all);
# 3. a declaration that is missing part of an element is REFUSED, because
#    the only way to finish it is for the engine to choose a strength.

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
    import library.tools.series_look as module

    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, float), (
            f"series_look.{name} is a bare number: {value!r}. Every "
            f"strength belongs in a template's declaration."
        )

    source = inspect.getsource(module)
    for gone in ("pmk_default", "warm_reflection", "electric_contrast",
                 "film_stock_warmth"):
        # Named in the docstring as history, never as a value.
        assert f'"{gone}"' not in source and f"'{gone}'" not in source, (
            f"{gone} is still a look this engine can hand out")


# ── A declaration reaches the picture ───────────────────────────────────

def test_saturation_is_never_delivered_twice():
    """Saturation is a CDL term. Sending it to fx.grade as well would
    multiply it a second time in the picture."""
    look = resolve_look(FULL_DECLARATION)
    assert "grade_saturation" not in look.fusion()
    assert look.cdl()["saturation"] == 1.2


def test_a_removed_element_leaves_the_rest_drawn():
    """The captain refused grain 2026-09-10, so the declaration carries
    every other element and no `grain` key at all - not a zeroed one.
    The comp must draw every remaining node and no FilmGrain stub: a
    declared element that draws nothing is the ghost this project keeps
    tripping over, and a removal that drops its neighbours is the
    mis-weighting a chain edit risks."""
    declaration = {k: v for k, v in FULL_DECLARATION.items() if k != "grain"}
    look = resolve_look(declaration)
    fusion = look.fusion()
    assert not any("grain" in key for key in fusion)
    assert fusion["grade_contrast"] == 0.2
    assert fusion["glow_gain"] == 0.3
    assert fusion["vignette_blend"] == 0.3
    comp = build_effect_comp(dict(fusion), 120,
                             source_res=(1080, 1920))
    assert "BrightnessContrast" in comp, "contrast lost with the grain removal"
    assert "SoftGlow" in comp, "glow lost with the grain removal"
    assert "EllipseMask" in comp, "vignette lost with the grain removal"
    assert "FilmGrain" not in comp, "a removed grain still draws a node"


# ── Absence is nothing, not a substitute ────────────────────────────────

@pytest.mark.parametrize("declaration", [{}])
def test_no_declaration_resolves_to_none(declaration):
    """A project that declares nothing must not silently get a grade."""
    assert resolve_look(declaration) is None


# ── A partial declaration is refused, never completed ───────────────────

def test_a_malformed_declaration_is_refused_by_name():
    """Never completed, never ignored: the only way to finish a partial
    look is for the engine to choose a strength."""
    def refusal(declaration):
        with pytest.raises(LookDeclarationError) as excinfo:
            resolve_look(declaration)
        return str(excinfo.value)

    # A half-declared element names every key it is missing.
    message = refusal({"name": "half", "glow": {"gain": 0.2}})
    assert "glow" in message
    for key in ELEMENTS_BY_KEY["glow"].required:
        if key != "gain":
            assert key in message, f"{key} not named in the refusal"
    # An unknown element lists the known ones.
    message = refusal({"name": "typo", "halation": {"amount": 0.2}})
    assert "halation" in message
    for known in ELEMENTS_BY_KEY:
        assert known in message
    # A named look drawing no pixel reads as a grade in every report.
    assert "draws nothing" in refusal({"name": "empty"})
    # A value is a number in the declaration, never a path to a file.
    assert "must be a number" in refusal(
        {"name": "path", "contrast": "/some/look.drx"})
    # A template still naming the old catalogue says what replaced it,
    # and carries the shape, so the fix is in the error.
    message = refusal("pmk_default")
    assert "DECLARATION, not a name" in message
    assert "series_look:" in message
    assert "glow" in message


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


# ── Project-level override: the project's own config wins ────────────

def _project_with_look(tmp_path, look):
    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump({"name": "t", "style": {"series_look": look}}))
    return str(tmp_path)


def test_a_project_declaration_replaces_the_template_slot(tmp_path):
    """Whole-slot replace, never a merge: half a look from each of two
    sources is a look nobody designed."""
    from library.tools.series_look import effective_series_look

    folder = _project_with_look(tmp_path, {"name": "proj", "contrast": 0.1})
    assert effective_series_look({"series_look": {"name": "tmpl"}},
                                folder) == {"name": "proj", "contrast": 0.1}


def test_a_malformed_project_declaration_is_refused(tmp_path):
    """A project block that is not a mapping cannot be resolved, so it
    raises here rather than rendering nothing at 2am."""
    from library.tools.series_look import (
        LookDeclarationError,
        project_series_look,
    )

    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump({"name": "t",
                        "style": {"series_look": "a_named_look"}}))
    with pytest.raises(LookDeclarationError):
        project_series_look(str(tmp_path))


# ── The rename: `house_look` is gone as a name, alive as a reading ────────


def test_a_declaration_written_under_the_old_slot_still_works(tmp_path):
    """Every declaration written before the rename keeps working.

    A rename that ungrades a series is worse than the name it fixed.
    """
    from library.tools.series_look import (
        effective_series_look, project_series_look, resolve_look,
    )

    look = {"name": "written_before_the_rename", "contrast": 0.1}
    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump({"name": "t", "style": {"house_look": look}}))

    assert project_series_look(str(tmp_path)) == look
    assert effective_series_look({}, str(tmp_path)) == look
    assert effective_series_look({"house_look": look}, "") == look
    assert resolve_look(look).contrast == 0.1


# --------------------------------------------------------------------------
# From test_series_look_reaches_broll.py
#
# The declared look reaches B-roll, not only A-roll.
#
# `compile_manifest` merges `fusion_look` onto every V1 **and V2** clip, and
# the Fusion pass must draw it on both. Nothing here asserts a look VALUE -
# only that whatever value is declared arrives on both tracks. History:
# `docs/evidence/series_look_reaches_broll.md`.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_6_01_render.build_verification import (
    detect_unreachable_fusion_effects,
)
from library.tools.execution.fusion_tracks import (
    reachable_effect_labels,
)

# A look declared WHOLE: an armed element with no strength is refused by
# `comp_builder.UndeclaredEffectStrength`, which is the same rule
# `series_look` applies at the template. `vignette_intensity` is a name
# the renderer never reads and is kept here on purpose - this file also
# pins that an unreadable name is REPORTED as dropped, not drawn.
LOOK = {"glow_gain": 1.4, "glow_threshold": 0.75, "glow_size": 3.5,
        "film_grain": 0.02, "film_grain_power": 0.2, "film_grain_size": 1.5,
        "vignette_intensity": 0.35}

MANIFEST = {
    "tracks": {
        "V1": {"clips": [
            {"label": "a_roll_0", "source_file": "/tmp/a0.mov",
             "source_in": 0.0, "source_out": 2.0},
            {"label": "a_roll_1", "source_file": "/tmp/a1.mov",
             "source_in": 0.0, "source_out": 2.0},
        ]},
        "V2": {"clips": [
            {"label": "broll_1", "source_file": "/tmp/b1.mov",
             "source_in": 0.0, "source_out": 1.5},
        ]},
    },
    "fusion_effects": {
        "per_clip": {
            "a_roll_0": dict(LOOK),
            "a_roll_1": dict(LOOK),
            "broll_1": dict(LOOK),
        },
        "transitions": [],
    },
}


# ── The enumeration ───────────────────────────────────────────────────

def test_every_looked_clip_is_reachable():
    per_clip = MANIFEST["fusion_effects"]["per_clip"]
    assert set(per_clip) <= reachable_effect_labels(MANIFEST)


def test_a_placed_broll_label_is_no_longer_a_drop():
    """The exact 001 shape: three cutaways carrying the merged look."""
    dropped = detect_unreachable_fusion_effects(
        MANIFEST["fusion_effects"]["per_clip"],
        {1: {"a_roll_0", "a_roll_1"}, 2: {"broll_1"}},
    )
    assert dropped == []


def test_an_unplaced_label_is_still_a_drop():
    """The detection must not become vacuous - a gate that cannot fire is
    worse than no gate."""
    dropped = detect_unreachable_fusion_effects(
        {"broll_9": dict(LOOK)}, {1: {"a_roll_0"}, 2: {"broll_1"}})
    assert [d["label"] for d in dropped] == ["broll_9"]
    assert "glow_gain" in dropped[0]["detail"]


# ── The pass itself, driven against a fake Resolve ────────────────────

class _FakeMediaPoolItem:
    def __init__(self, path, frames=120, fps="30.0", resolution="1920x1080"):
        self._props = {
            "File Path": path, "Frames": str(frames),
            "FPS": fps, "Resolution": resolution,
        }

    def GetClipProperty(self, key=None):
        return self._props if key is None else self._props.get(key, "")


class _FakeComp:
    def __init__(self):
        self.locked = False

    def Lock(self):
        self.locked = True

    def Unlock(self):
        self.locked = False

    def GetToolList(self):
        class _Tool:
            def __init__(self, regid):
                self._regid = regid

            def GetAttrs(self):
                return {"TOOLS_RegID": self._regid}

            def Delete(self):
                return True
        return {1: _Tool("MediaIn"), 2: _Tool("Merge"), 3: _Tool("MediaOut")}

    def AddTool(self, _name):
        assert self.locked, "Fusion node creation must hold comp.Lock()"
        class _Dummy:
            def Delete(self):
                return True
        return _Dummy()

    def FindTool(self, _name):
        return None


class _FakeTimelineItem:
    def __init__(self, path, start=0, end=60):
        self.mpi = _FakeMediaPoolItem(path)
        self.imported = []
        self._start, self._end = start, end

    def GetMediaPoolItem(self):
        return self.mpi

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetFusionCompNameList(self):
        return ["Composition 1"] if self.imported else []

    def DeleteFusionCompByName(self, _name):
        self.imported = []
        return True

    def ImportFusionComp(self, path):
        # Read it NOW: the pass deletes its temp comp directory on the
        # way out, so the file is gone by the time the test looks.
        with open(path, encoding="utf-8") as f:
            self.imported.append(f.read())
        return _FakeComp()

    def GetFusionCompByName(self, _name):
        return _FakeComp()


class _FakeTimeline:
    def __init__(self, items_by_track):
        self.items_by_track = items_by_track

    def GetSetting(self, _key):
        return "30"

    def GetItemListInTrack(self, _kind, index):
        return self.items_by_track.get(index, [])


class _FakeResolve:
    def __init__(self, timeline):
        self._timeline = timeline
        self.pages = []

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self

    def GetCurrentTimeline(self):
        return self._timeline

    def OpenPage(self, name):
        self.pages.append(name)
        return True


class _FakeDvr:
    def __init__(self, timeline):
        self.timeline = timeline

    def scriptapp(self, _name):
        return _FakeResolve(self.timeline)


@pytest.fixture
def fusion_module():
    import library.tools.execution.apply_fusion_comps as afc
    return afc


def _timeline():
    return _FakeTimeline({
        1: [_FakeTimelineItem("/tmp/a0.mov", 0, 60),
            _FakeTimelineItem("/tmp/a1.mov", 60, 120)],
        2: [_FakeTimelineItem("/tmp/b1.mov", 20, 65)],
    })


def test_a_broll_clip_gets_a_fusion_comp(fusion_module, monkeypatch, tmp_path):
    """The defect, driven through the real pass: eleven merged entries
    used to produce eight comps."""
    timeline = _timeline()
    monkeypatch.setattr(fusion_module, "dvr", _FakeDvr(timeline))

    assert fusion_module.apply_fusion_comps(
        json.loads(json.dumps(MANIFEST)), str(tmp_path)) is True

    v1 = timeline.items_by_track[1]
    v2 = timeline.items_by_track[2]
    assert all(item.imported for item in v1), "A-roll regressed"
    assert all(item.imported for item in v2), (
        "the cutaway carries no Fusion comp, so it plays at a different "
        "contrast with no grain and no vignette beside the A-roll")

    # Not just 'a comp' - the same node types the A-roll got.
    def nodes(item):
        text = item.imported[0]
        return {name for name in
                ("SoftGlow", "FilmGrain", "EllipseMask", "Merge")
                if name in text}

    a_nodes = nodes(v1[0])
    assert a_nodes, "the A-roll comp drew none of the look nodes"
    assert nodes(v2[0]) == a_nodes


def test_transitions_are_not_replayed_onto_broll(fusion_module, monkeypatch,
                                                 tmp_path):
    """A transition's `after_clip` is a V1 index. Applied to V2 it would
    draw a flash at an unrelated cut."""
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["fusion_effects"]["transitions"] = [
        {"type": "flash", "after_clip": 0, "duration_frames": 12}
    ]
    timeline = _timeline()
    monkeypatch.setattr(fusion_module, "dvr", _FakeDvr(timeline))
    fusion_module.apply_fusion_comps(manifest, str(tmp_path))

    broll_comp = timeline.items_by_track[2][0].imported[0]
    a_roll_comp = timeline.items_by_track[1][0].imported[0]
    assert "Brightness" in a_roll_comp, "the flash did not reach the A-roll"
    assert "Brightness" not in broll_comp
