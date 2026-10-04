"""The colourist decides the grade, and the two halves compose exactly.

Project 001 measured nine clips across a 2.7x luma spread and wrote the
IDENTITY CDL on all nine, because normalisation was reachable only
through an `exposure_reference` that only a brand template declares and
001 names no template.
"""
import sys
from pathlib import Path
import pytest
import json
import pathlib
import shutil
import subprocess
from unittest.mock import patch
from library.steps.step_5_01_color_grade import bridge as bridge_501
from library.steps.step_5_01_color_grade.grade import (
    collect_entries,
    measure_clips,
)
import os
from library.tools import camera_match as cm
import numpy as np
from library.tools import shot_colour as sc
import tempfile
import unittest
from library.tools import look_matcher
from library.tools.look_matcher import (
    LookMatchUnavailable,
    analyze_frame_colors,
    match_clips_to_reference,
)
from library.tools import subject_grade
from library.tools.analysis.object_segmentation import (
    FACE_SEED_LABEL,
    encode_rle,
)
from library.tools.fusion import comp_builder
from library.tools.fusion.effects import fx
from library.tools.fusion.engine import CompEngine


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import color_correction as cc  # noqa: E402
from library.tools.series_look import NEUTRAL_CDL, resolve_look  # noqa: E402

DECLARED = {
    "name": "test_declaration",
    "cdl": {
        "slope": [1.04, 1.0, 0.95],
        "offset": [0.012, 0.01, 0.004],
        "power": [0.985, 0.995, 1.012],
        "saturation": 0.95,
    },
}

CLIPS = ["clip_011", "clip_013", "clip_017"]


def _read(entries):
    return cc.read_corrections(entries, CLIPS)


# ── The composition is exact, in a stated order ──────────────────────

def test_a_project_with_no_look_gets_the_correction_as_the_whole_grade():
    corrections, dropped = _read([
        {"clip_id": "clip_017", "exposure_stops": 1.0,
         "why": "car interior at dusk, brought up under the plaza"},
    ])
    assert not dropped
    cdl = cc.compose_cdl(None, corrections[0], NEUTRAL_CDL)
    # 2 ** 1.0 == 2.0, moving all three channels equally so the hue
    # balance is untouched.
    assert cdl["slope_r"] == cdl["slope_g"] == cdl["slope_b"] == 2.0
    assert cdl["offset_r"] == 0.0 and cdl["power_r"] == 1.0


def test_every_term_composes_the_way_the_legend_says():
    """The arithmetic this module exists for. Slope multiplies, offset
    adds, power multiplies, saturations multiply - exact for the serial
    order the docstring fixes."""
    look = resolve_look(DECLARED)
    corrections, _ = _read([{
        "clip_id": "clip_013",
        "exposure_stops": -0.5,
        "slope": [1.1, 1.0, 0.9],
        "offset": [0.01, 0.0, -0.005],
        "power": [1.05, 1.0, 0.95],
        "saturation": 1.2,
        "why": "warming a flat midday shot",
    }])
    cdl = cc.compose_cdl(look, corrections[0], NEUTRAL_CDL)
    gain = 2.0 ** -0.5
    assert cdl["slope_r"] == round(gain * 1.1 * 1.04, 4)
    assert cdl["offset_r"] == round(0.012 + 0.01, 4)
    assert cdl["power_r"] == round(0.985 * 1.05, 4)
    assert cdl["saturation"] == round(0.95 * 1.2, 4)


# ── Refused reaches the model; under-specified is dropped ────────────

def test_a_value_the_cdl_cannot_take_is_refused_by_name():
    """Refusing is how a violation reaches the model that wrote it
    (post_bridge_retry); dropping is how it goes quiet."""
    with pytest.raises(cc.ColorCorrectionRefused, match="slope"):
        _read([{"clip_id": "clip_011", "slope": [1.0, 1.0], "why": "two"}])
    # A term nothing reads is refused too, and says where it lives.
    with pytest.raises(cc.ColorCorrectionRefused, match="temperature"):
        _read([{"clip_id": "clip_011", "temperature": 200, "why": "warmer"}])


def test_an_all_neutral_entry_is_dropped_rather_than_written_as_a_no_op():
    """A no-op CDL is what the last run wrote on all nine clips, and
    Resolve drew no node for any of them."""
    corrections, dropped = _read([
        {"clip_id": "clip_011", "exposure_stops": 0.0,
         "slope": [1.0, 1.0, 1.0], "why": "leave it"}])
    assert not corrections
    assert [d.reason for d in dropped] == ["no_correction_terms"]


# ── The four absences are four, not one ──────────────────────────────

def test_a_judged_no_correction_is_not_an_absent_decision():
    """The whole point. `[]` from a colourist who looked and `[]` because
    no colourist ran are different facts; the old output could not tell
    them apart."""
    assert cc.planning_basis(True, [], []) == cc.JUDGED_NO_CORRECTION_NEEDED
    assert cc.planning_basis(False, [], []) == cc.NO_CORRECTION_DECISION
    # And a plan whose every entry was discarded is a third thing again.
    assert cc.planning_basis(
        True, [], [cc.Dropped("no_reason_given", "x", {})]
    ) == cc.EVERY_ENTRY_DROPPED


# --------------------------------------------------------------------------
# From test_color_grade_camera_match.py
#
# 5.01 sees colour, stills and the camera match - rung 4c.
#
# The colourist step used to see mean luma only and was shown no frames,
# so a cast between two angles covering one set had no number and no
# picture. The bridge now measures per-shot chroma
# (`library/tools/shot_colour.py`), draws one still per shot, asks the
# still router what it sees (`library/tools/still_vision.py`), and derives
# the measured camera-match proposal (`library/tools/camera_match.py`).
#
# The vision half is stubbed here: what the router answers is the
# router's own business (tests/unit/resolve/test_still_vision.py). What this step owns
# is that the stills exist where the block says, the notes record WHO
# answered, and the match proposal is stated, never applied.

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _clip(path, color):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=160x120:d=12",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


def _inputs(project, warm, cool):
    return {
        "project_folder": str(project),
        "a_roll_assignments": [
            {"clip_id": "clip_warm", "timeline_start": 0.0,
             "source_file": str(warm)},
            {"clip_id": "clip_cool", "timeline_start": 5.0,
             "source_file": str(cool)},
        ],
        "b_roll_assignments": [],
        "creative_direction": {},
        "clip_catalog": [],
        "semantic_analysis_documents": [],
    }


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_rows_carry_the_chroma_half(tmp_path):
    """Each measured row carries per-channel means and the neutral
    balance - the numbers the cast-match grounds in."""
    warm = tmp_path / "warm.mp4"
    cool = tmp_path / "cool.mp4"
    _clip(warm, "0x787673")
    _clip(cool, "0x74767A")
    data = _inputs(tmp_path / "proj", warm, cool)
    rows = bridge_501.attach_sources(
        measure_clips(collect_entries(data), ""),
        collect_entries(data), "")
    by_clip = {row["clip_id"]: row for row in rows}
    assert by_clip["clip_warm"]["neutral_rb"] is not None
    assert by_clip["clip_warm"]["neutral_rb"] > 1.0
    assert by_clip["clip_cool"]["neutral_rb"] is not None
    assert by_clip["clip_cool"]["neutral_rb"] < 1.0
    assert by_clip["clip_warm"]["mean_rgb"][0] > \
        by_clip["clip_warm"]["mean_rgb"][2]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_bridge_draws_stills_and_derives_the_match(tmp_path, monkeypatch):
    """Stills land where the block says, the notes record who answered,
    and the match proposes one slope - stated, never applied."""
    monkeypatch.delenv("PIPELINE_HOST_HARNESS", raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    warm = tmp_path / "warm.mp4"
    cool = tmp_path / "cool.mp4"
    _clip(warm, "0x787673")
    _clip(cool, "0x74767A")
    data = _inputs(project, warm, cool)
    entries = collect_entries(data)
    rows = bridge_501.attach_sources(
        measure_clips(entries, str(project)), entries, str(project))

    block, paths = bridge_501.draw_shot_stills(rows, str(project))
    assert len(paths) == 2
    assert "clip_warm" in block and "clip_cool" in block
    for clip in ("clip_warm", "clip_cool"):
        still = project / "pipeline_output" / "steps" / "5_01_color_grade" \
            / "shot_stills" / f"{clip}__shot.jpg"
        assert still.stat().st_size > 0

    with patch.object(bridge_501.still_router, "inspect_stills",
                      return_value="warm cast on the wall") as seen:
        notes = bridge_501.observe_shot_stills(paths, str(project))
    seen.assert_called_once()
    assert notes["text"] == "warm cast on the wall"
    assert notes["observed_by"] != "none"

    match = bridge_501.derive_camera_match(rows)
    assert match["reference"] is not None
    assert len(match["matches"]) == 1
    slope = match["matches"][0]["slope"]
    assert len(slope) == 3
    after = match["matches"][0]["neutral_after"]
    assert after["rb"] == pytest.approx(
        match["reference"] and match["matches"][0]
        ["reference_neutral"]["rb"], abs=1e-3)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_vision_failure_is_a_stated_absence(tmp_path):
    """A router that raises does not fail the colour step: the absence
    is recorded with the reason, and the stills stay in the block for
    the driver to open."""
    project = tmp_path / "proj"
    project.mkdir()
    warm = tmp_path / "warm.mp4"
    _clip(warm, "0x787673")
    data = _inputs(project, warm, tmp_path / "missing.mp4")
    entries = collect_entries(data)
    rows = bridge_501.attach_sources(
        measure_clips(entries, str(project)), entries, str(project))
    block, paths = bridge_501.draw_shot_stills(rows, str(project))
    assert "NOT DRAWN" in block

    with patch.object(bridge_501.still_router, "inspect_stills",
                      side_effect=RuntimeError("host silent")):
        notes = bridge_501.observe_shot_stills(paths, str(project))
    assert notes["observed_by"] == "none"
    assert "host silent" in notes["reason"]
    assert notes["text"] == ""


def test_empty_inputs_emit_only_declared_keys():
    """The gate-test path: no clips, no project, no vision call - every
    emitted key is in the manifest, so validate_step_output stays
    quiet (tests/test_bridge_outputs_are_declared.py)."""
    manifest = json.loads(
        pathlib.Path(bridge_501.__file__).parent.joinpath(
            "manifest.json").read_text(encoding="utf-8"))
    declared = {o["name"] for o in manifest["interface"]["outputs"]}
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):
        rows = measure_clips([], "")
    assert rows == []
    match = bridge_501.derive_camera_match(rows)
    assert match["matches"] == []
    notes = bridge_501.observe_shot_stills([], "")
    assert notes["observed_by"] == "none"
    for key in ("clip_exposure", "cut_adjacency", "declared_look",
                "shot_stills", "still_colour_notes", "camera_match",
                "grade_terms_legend"):
        assert key in declared, f"bridge emits {key!r} no manifest declares"


# --------------------------------------------------------------------------
# From test_color_grade_is_decided.py
#
# Step 5.01 asks a colourist, and the answer reaches the CDL.
#
# The step measured project 001's nine clips correctly - clip_011 at
# 145.495 luma, clip_017 at 53.116, a 2.7x spread across shots that touch
# at cuts - and wrote the identity CDL on all nine, because the only route
# to a correction was an `exposure_reference` that only a brand template
# declares and 001 declares no template.
#
# This drives the real `bridge.py` and `post_bridge.py` as subprocesses,
# the way `run_hybrid_step` does, so what is asserted is what the step
# really produces.

sys.path.insert(0, str(REPO))

STEP = REPO / "library" / "steps" / "step_5_01_color_grade"

# The three clips the brief names, with 001's own measured luma.
LUMA = {"clip_011": 145.495, "clip_013": 130.691, "clip_017": 53.116}

DECLARED_LOOK = {
    "name": "test_declaration",
    "cdl": {
        "slope": [1.04, 1.0, 0.95],
        "offset": [0.012, 0.01, 0.004],
        "power": [0.985, 0.995, 1.012],
        "saturation": 0.95,
    },
    "grain": {"power": 0.3, "size": 1.6},
}


def _payload(tmp_path, brand_template=None):
    return {
        "project_folder": str(tmp_path),
        "brand_template": brand_template or {},
        "a_roll_assignments": [
            {"spine_block_position": "hook", "timeline_start": 0.0,
             "video_segments": [{"clip_id": "clip_011",
                                 "source_file": "raw/a.mov"}]},
            {"spine_block_position": 2, "timeline_start": 4.0,
             "video_segments": [{"clip_id": "clip_013",
                                 "source_file": "raw/b.mov"}]},
            {"spine_block_position": 3, "timeline_start": 8.0,
             "video_segments": [{"clip_id": "clip_017",
                                 "source_file": "raw/c.mov"}]},
        ],
        "b_roll_assignments": [],
        "b_roll_interjections": [],
        "clip_catalog": [
            {"clip_id": cid, "source_file": f"raw/{cid}.mov"} for cid in LUMA
        ],
        "semantic_analysis_documents": [
            {"clip_id": "clip_017", "file_path": "raw/clip_017.mov",
             "analysis": {"scene": "[0.0-3.0s] car interior. indoor. dusk."}},
        ],
        "creative_direction": {"target_mood": "reflective"},
    }


def _run(script, payload, extra_env=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(STEP / script)],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env, check=False)


@pytest.fixture
def bridge_output(tmp_path):
    """The bridge's real output, with `measure_luma` answering 001's
    numbers - patched in-process, since the measurement is the one part
    that needs media on disk."""
    from unittest.mock import patch

    from library.steps.step_5_01_color_grade import grade

    payload = _payload(tmp_path)
    calls = {"n": 0}
    order = ["clip_011", "clip_013", "clip_017"]

    def _by_order(path):
        clip = order[min(calls["n"], len(order) - 1)]
        calls["n"] += 1
        return {"luma": LUMA[clip], "method": grade.LUMA_METHOD,
                "samples": 40}

    with patch.object(grade, "measure_luma", _by_order):
        entries = grade.collect_entries(payload)
        rows = grade.measure_clips(entries, str(tmp_path))
    grade.add_scene_descriptions(
        rows, payload["semantic_analysis_documents"], payload["clip_catalog"])
    return payload, rows, grade.cut_adjacency(entries, rows)


def test_the_cut_adjacency_names_the_pairs_a_viewer_sees(bridge_output):
    """A 2.7x spread matters because the shots touch, not because the
    ratio is large. Nothing produced this axis before."""
    _payload_, rows, pairs = bridge_output
    assert [r["clip_id"] for r in rows] == ["clip_011", "clip_013",
                                            "clip_017"]
    assert rows[0]["luma"] == 145.495
    assert [(p["outgoing_clip"], p["incoming_clip"]) for p in pairs] == [
        ("clip_011", "clip_013"), ("clip_013", "clip_017")]
    # log2(53.116 / 130.691) - the gap the captain saw in the render.
    assert pairs[1]["stops_between"] == pytest.approx(-1.299, abs=0.01)


# ── The answer reaches the CDL ───────────────────────────────────────

def _post_bridge(tmp_path, rows, answer, brand_template=None):
    payload = _payload(tmp_path, brand_template)
    payload["clip_exposure"] = rows
    payload.update(answer)
    proc = _run("post_bridge.py", payload)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["color_grade_spec"]


def test_a_project_with_no_brand_template_still_gets_a_reasoned_grade(
        tmp_path, bridge_output):
    """The defect, in one test. 001 names no template; before this the
    answer was the identity CDL on every clip."""
    _payload_, rows, _ = bridge_output
    spec = _post_bridge(tmp_path, rows, {
        "color_correction": [
            {"clip_id": "clip_011", "exposure_stops": -0.35,
             "why": "the plaza is the brightest thing in the cut"},
            {"clip_id": "clip_017", "exposure_stops": 0.55,
             "why": "the car interior is meant to be dark, lifted part way"},
        ],
        "grade_assessment": "one stop and a third across the last cut",
    })

    assert spec["series_look"] is None and spec["fusion_look"] == {}
    by_clip = {a["clip_id"]: a for a in spec["per_clip_adjustments"]}
    assert by_clip["clip_011"]["cdl_values"]["slope_r"] == round(
        2.0 ** -0.35, 4)
    assert by_clip["clip_017"]["cdl_values"]["slope_r"] == round(
        2.0 ** 0.55, 4)
    # The one clip the colourist left alone is still identity, and says
    # so rather than reading as an ungraded run.
    assert by_clip["clip_013"]["cdl_values"]["slope_r"] == 1.0
    assert "left this clip alone" in by_clip["clip_013"]["notes"]

    # And the reasoning is recorded next to the values.
    assert "brightest thing in the cut" in by_clip["clip_011"][
        "correction_reason"]
    assert by_clip["clip_011"]["correction_terms"] == ["exposure_stops"]
    assert spec["correction_basis"]["basis"] == "corrected"
    assert "last cut" in spec["correction_basis"]["assessment"]


def test_a_project_with_a_template_still_gets_that_templates_look(
        tmp_path, bridge_output):
    """The house look is not removed - it gains a craft layer under it."""
    _payload_, rows, _ = bridge_output
    spec = _post_bridge(tmp_path, rows, {
        "color_correction": [
            {"clip_id": "clip_017", "exposure_stops": 1.0, "why": "dark"}],
        "grade_assessment": "one clip under",
    }, brand_template={"style": {"series_look": DECLARED_LOOK}})

    assert spec["series_look"] == "test_declaration"
    assert spec["fusion_look"]["film_grain"] is True
    by_clip = {a["clip_id"]: a for a in spec["per_clip_adjustments"]}
    # Untouched clips carry EXACTLY the declared look.
    assert by_clip["clip_011"]["cdl_values"] == {
        "slope_r": 1.04, "slope_g": 1.0, "slope_b": 0.95,
        "offset_r": 0.012, "offset_g": 0.01, "offset_b": 0.004,
        "power_r": 0.985, "power_g": 0.995, "power_b": 1.012,
        "saturation": 0.95}
    # The corrected one carries the look with the correction under it -
    # slope doubled, every other term the look's own.
    corrected = by_clip["clip_017"]["cdl_values"]
    assert corrected["slope_r"] == round(1.04 * 2.0, 4)
    assert corrected["offset_r"] == 0.012
    assert corrected["power_g"] == 0.995
    assert corrected["saturation"] == 0.95


def test_a_judged_no_correction_is_not_an_ungraded_run(tmp_path,
                                                       bridge_output):
    _p, rows, _ = bridge_output
    spec = _post_bridge(tmp_path, rows, {
        "color_correction": [],
        "grade_assessment": "these three sit together already",
    })
    assert spec["correction_basis"]["basis"] == "judged_no_correction_needed"
    assert "sit together" in spec["correction_basis"]["assessment"]

    # And a run that reached no colourist at all says THAT instead. The
    # distinction the old output could not make: an identity CDL read the
    # same whether a colourist approved the footage or none existed.
    from library.steps.step_5_01_color_grade.grade import (
        collect_entries, define_color_grade,
    )
    payload, _rows_, _ = bridge_output
    absent = define_color_grade(
        {"entries": collect_entries(payload)}, str(tmp_path),
        measured_clips=rows)["color_grade_spec"]
    assert absent["correction_basis"]["basis"] == "no_correction_decision"


# --------------------------------------------------------------------------
# From test_grade_assessment_checked.py
#
# D11: nothing checked `grade_assessment` against `color_correction`.
#
# On the 2026-09-03 run of project 001 the colourist's assessment claimed a
# saturation of 1.10 on *every* placed clip while two entries carried no
# `saturation` term and shipped at neutral. Both halves were stored, both
# were read by a human, and nothing compared them.
#
# The check lives in `library/tools/color_correction.py` - the one module
# that owns both spellings (`FIELD` and `ASSESSMENT_FIELD`) - and its
# finding is recorded on `correction_basis.assessment_mismatches`, beside
# the claim, so the run summary route (`step_exporter`) reads it where the
# human reads the assessment. It REPORTS, never refuses: a prose claim is
# a model judgement, and a gate that fails correct output is no coverage
# (AGENTS.md 10.4).

sys.path.insert(0, str(REPO))


# The D11 shape: nine placed clips, seven corrected with a saturation
# term, two left without one. Expected values below are derived from the
# expressions the code uses - the Correction objects and the term table -
# never copied literals.
CUT = [f"clip_{n:03d}" for n in (11, 13, 17, 2, 8, 5, 1, 14, 6)]
SATURATED = CUT[:7]
UNSATURATED = CUT[7:]
CLAIMED_SATURATION = 1.10


def _corrections():
    entries = [
        {"clip_id": clip_id, "saturation": CLAIMED_SATURATION,
         "why": "a gentle lift across the cut"}
        for clip_id in SATURATED
    ]
    # The two D11 clips: corrected on another axis, carrying no
    # saturation term, so they ship at the term's neutral.
    entries.append({"clip_id": UNSATURATED[0], "exposure_stops": 0.3,
                    "why": "same lot, same light, a third of a stop adrift"})
    entries.append({"clip_id": UNSATURATED[1], "exposure_stops": 0.2,
                    "why": "sits under the other daylight exteriors"})
    corrections, dropped = cc.read_corrections(entries, CUT)
    assert not dropped
    return corrections


def test_a_universal_claim_the_cdl_does_not_carry_is_named():
    corrections = _corrections()
    assessment = (
        f"I applied one global saturation of {CLAIMED_SATURATION} "
        f"to every placed clip"
    )
    mismatches = cc.check_assessment(assessment, corrections, CUT)
    assert len(mismatches) == 1
    mismatch = mismatches[0]
    assert mismatch["term"] == "saturation"
    assert mismatch["claimed"] == corrections[0].saturation
    # Derived from the code's own structures, not literals: every cut
    # clip whose correction declares no saturation term ships neutral.
    by_clip = {c.clip_id: c for c in corrections}
    expected = [c for c in CUT
                if by_clip.get(c) is None
                or "saturation" not in by_clip[c].declared]
    assert sorted(mismatch["clips"]) == sorted(expected)
    assert sorted(expected) == sorted(UNSATURATED)
    neutral = cc.TERMS_BY_KEY["saturation"].neutral
    for clip_id in UNSATURATED:
        assert f"{clip_id}" in mismatch["detail"]
    assert f"{neutral}" in mismatch["detail"]


def test_a_universal_claim_the_cdl_carries_is_quiet():
    entries = [
        {"clip_id": clip_id, "saturation": CLAIMED_SATURATION,
         "why": "a gentle lift across the cut"}
        for clip_id in CUT
    ]
    corrections, dropped = cc.read_corrections(entries, CUT)
    assert not dropped
    assert cc.check_assessment(
        f"saturation {CLAIMED_SATURATION} across every clip in the cut",
        corrections, CUT) == []


def test_prose_without_a_checkable_claim_is_not_read():
    corrections = _corrections()
    # The three assessments the existing fixtures already carry: no
    # universal scope plus term plus number together, so no claim.
    assert cc.check_assessment(
        "one stop and a third across the last cut", corrections, CUT) == []
    assert cc.check_assessment(
        "these three sit together already", corrections, CUT) == []
    assert cc.check_assessment("one clip under", corrections, CUT) == []
    assert cc.check_assessment("", corrections, CUT) == []
    # Complex prose - two terms or two numbers - is declined: pairing
    # them would invent a reading.
    assert cc.check_assessment(
        f"saturation {CLAIMED_SATURATION} on every clip and exposure "
        f"+0.5 on the interior",
        corrections, CUT) == []
    assert cc.check_assessment(
        "every clip got saturation 1.10 except the two at 1.0",
        corrections, CUT) == []


def test_define_color_grade_threads_the_cut_clips_into_the_record():
    from library.steps.step_5_01_color_grade.grade import define_color_grade

    corrections = _corrections()
    assessment = (
        f"I applied one global saturation of {CLAIMED_SATURATION} "
        f"to every placed clip"
    )
    rows = [{"clip_id": clip_id, "luma": 120.0,
             "luma_method": "test", "luma_samples": 10} for clip_id in CUT]
    spec = define_color_grade(
        {"entries": [{"track": "V1", "clip_id": clip_id,
                      "entry_id": clip_id, "source_file": ""}
                     for clip_id in CUT]},
        measured_clips=rows, corrections=corrections, decided=True,
        assessment=assessment)["color_grade_spec"]
    assert sorted(
        spec["correction_basis"]["assessment_mismatches"][0]["clips"]
    ) == sorted(UNSATURATED)


# --------------------------------------------------------------------------
# From test_camera_match.py
#
# Camera matching: the measured proposal that brings two angles together.
#
# Step 5.01 had no number for a difference in CAST, so when two angles
# covered one set under different white balance nothing grounded the CDL
# that fixes it. `library/tools/camera_match.py` derives one slope triple
# per camera off the shared neutral - reference over measured, per
# channel - as a stated proposal the colourist accepts or overrides,
# never an engine default.
#
# Each test names what it stops: a default wearing a measurement's name,
# a match off content instead of neutral, a silent no-op.

def _row(clip, camera, neutral, rb, gb, source=""):
    return {
        "clip_id": clip,
        "camera": camera,
        "source_file": source or f"/footage/{camera}.MXF",
        "neutral_rgb": list(neutral),
        "neutral_rb": rb,
        "neutral_gb": gb,
        "neutral_fraction": 0.5,
    }


def test_slope_lands_the_neutral_on_the_reference():
    """The geo-podcast correction, exactly: reference over measured per
    channel, and the predicted after IS the reference balance.

    The reference is the angle nearest true neutral - camA at R/B 1.003,
    not the first row and not the brightest."""
    rows = [_row("b1", "camB", (44.25, 45.52, 46.42), 0.9532, 0.9806),
            _row("a1", "camA", (53.36, 52.53, 53.18), 1.0033, 0.9877)]
    out = cm.derive_camera_match(rows)
    assert out["reference"]["camera"] == "camA"
    assert len(out["matches"]) == 1
    match = out["matches"][0]
    assert match["camera"] == "camB"
    assert match["slope"] == [
        pytest.approx(round(53.36 / 44.25, 4)),
        pytest.approx(round(52.53 / 45.52, 4)),
        pytest.approx(round(53.18 / 46.42, 4))]
    assert match["neutral_before"]["rb"] == pytest.approx(0.9532)
    assert match["neutral_after"]["rb"] == pytest.approx(1.0033, abs=1e-3)
    assert match["neutral_after"]["gb"] == pytest.approx(0.9877, abs=1e-3)
    assert match["already_matched"] is False
    # The level half of the slope is stated beside it, in stops.
    import math
    ref_mean = (53.36 + 52.53 + 53.18) / 3
    other_mean = (44.25 + 45.52 + 46.42) / 3
    assert match["level_stops"] == pytest.approx(
        round(math.log2(ref_mean / other_mean), 3))


def test_row_without_neutral_is_skipped_never_content_matched():
    """A shot with no grey in it cannot be matched off its whole-frame
    mean - it is skipped with the reason."""
    rows = [_row("a1", "camA", (53.0, 52.0, 53.0), 1.0, 0.98),
            {"clip_id": "c1", "camera": "camC",
             "source_file": "/footage/camC.MXF",
             "neutral_rgb": None, "neutral_rb": None, "neutral_gb": None,
             "neutral_fraction": 0.0,
             "colour_unmeasured_because": "no grey in this shot"}]
    out = cm.derive_camera_match(rows)
    assert out["reference"] is None
    assert out["matches"] == []
    assert any(s["clip_id"] == "c1" for s in out["skipped"])
    assert "nothing to match" in out["note"]
    # One angle alone is the same stated absence, not an identity
    # proposal.
    out = cm.derive_camera_match(
        [_row("a1", "camA", (53.0, 52.0, 53.0), 1.0, 0.98)])
    assert out["matches"] == []
    assert out["reference"] is None


def test_already_matched_angles_say_so():
    """Two angles already together are reported with a neutral slope -
    'these match' is the measurement's own conclusion, and leaving the
    row out would read as 'not examined'."""
    rows = [_row("a1", "camA", (53.0, 52.0, 53.0), 1.0, 0.98),
            _row("a2", "camA2", (53.1, 52.0, 53.0), 1.0019, 0.9811)]
    out = cm.derive_camera_match(rows)
    assert len(out["matches"]) == 1
    assert out["matches"][0]["already_matched"] is True


def test_groups_fall_back_to_file_stem_and_say_so():
    """Rows with no camera label group by source file stem - and the
    row records that, so a reader knows two files from one body read
    as two groups."""
    rows = [dict(_row("a1", "", (53.0, 52.0, 53.0), 1.0, 0.98,
                      source="/footage/LC4932.MXF")),
            dict(_row("b1", "", (44.0, 45.0, 46.0), 0.9565, 0.9783,
                      source="/footage/LCATL0013.MXF"))]
    out = cm.derive_camera_match(rows)
    assert out["grouped_by"] == "source file stem"
    cameras = {out["reference"]["camera"]}
    cameras |= {m["camera"] for m in out["matches"]}
    assert cameras == {"LC4932", "LCATL0013"}


# --------------------------------------------------------------------------
# From test_shot_colour.py
#
# Per-shot colour measurement: what each graded shot IS in RGB.
#
# Step 5.01 saw mean luma only and could not see a cast. On geo-podcast
# camera A reads about 15% warmer than camera B on the same dark neutral
# set, and luma has no number for that. `library/tools/shot_colour.py`
# measures per-channel means plus the R/B and G/B balance on a detected
# neutral region, or says plainly that nothing measured.
#
# Each test names the defect it stops: a zeros row reading as measured
# black, a whole-frame mean wearing a neutral's name, a cast hiding
# inside content.

FFMPEG_2 = shutil.which("ffmpeg")


def _swatch(rgb, width=64, height=48):
    return (np.ones((height, width, 3), dtype=np.float64)
            * np.array(rgb, dtype=np.float64))


def test_neutral_of_grey_is_one_and_a_slight_cast_reports_it():
    """A true grey reads R/B 1.0 and G/B 1.0 - the reference everything
    else is a distance from."""
    out = sc.neutral_of(_swatch((120, 120, 120)))
    assert out["absent"] is False
    assert out["neutral_rb"] == pytest.approx(1.0)
    assert out["neutral_gb"] == pytest.approx(1.0)
    assert out["neutral_fraction"] == pytest.approx(1.0)
    # A near-grey surface with a slight warm push reads R/B above 1: the
    # number the colourist was never shown.
    out = sc.neutral_of(_swatch((123, 120, 117)))
    assert out["absent"] is False
    assert out["neutral_rb"] == pytest.approx(round(123 / 117, 4))
    assert out["neutral_gb"] == pytest.approx(round(120 / 117, 4))


def test_content_is_not_a_reference():
    """A wall 20% warm is a warm wall, not a misbalanced camera: its
    spread clears the neutral rule, so correcting it to grey would
    destroy the content, not the cast."""
    out = sc.neutral_of(_swatch((132, 120, 110)))
    assert out["absent"] is True
    # A saturated red field is content with a hue: the absence SAYS so
    # rather than borrowing the whole-frame mean.
    out = sc.neutral_of(_swatch((200, 20, 20)))
    assert out["absent"] is True
    assert out["neutral_rb"] is None
    assert "near-neutral" in out["reason"]
    # Noise votes warm down in the blacks and there is nothing to read in
    # clipped whites - both sit outside the band.
    pixels = np.concatenate([_swatch((4, 3, 5)).reshape(-1, 3),
                             _swatch((250, 250, 250)).reshape(-1, 3)])
    assert sc.neutral_of(pixels)["absent"] is True


def test_neutral_separates_wall_from_face():
    """The geo-podcast shape: a neutral wall behind warm content. The
    wall's balance survives the face beside it."""
    wall = _swatch((53, 52, 53), width=64, height=36)
    face = (np.ones((12, 64, 3)) * np.array([150, 100, 80])).astype(float)
    pixels = np.concatenate([wall.reshape(-1, 3),
                             face.reshape(-1, 3)])
    out = sc.neutral_of(pixels)
    assert out["absent"] is False
    assert out["neutral_rb"] == pytest.approx(1.0, abs=0.02)
    assert out["neutral_fraction"] == pytest.approx(0.75, abs=0.01)


def test_unmeasured_is_absent_never_zeros():
    """A missing file reports the absence. A zeros row would read as
    measured black, which balances to nothing."""
    out = sc.measure_shot_colour("/no/such/file.mov")
    assert out["mean_rgb"] is None
    assert out["neutral_rb"] is None
    assert out["colour_method"] == sc.COLOUR_UNMEASURED
    assert "not on disk" in out["colour_unmeasured_because"]
    out = sc.measure_shot_colour("")
    assert out["mean_rgb"] is None
    assert out["colour_method"] == sc.COLOUR_UNMEASURED


@pytest.mark.skipif(not FFMPEG_2, reason="ffmpeg not on PATH")
def test_measured_video_reports_means_and_neutral(tmp_path):
    """End to end off a generated file: a warm grey field measures warm
    on both the whole frame and the neutral, with the method stated."""
    src = str(tmp_path / "warm.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=0x787673:s=160x120:d=12",
         "-pix_fmt", "yuv420p", src],
        check=True)
    out = sc.measure_shot_colour(src)
    assert out["colour_method"] == sc.COLOUR_METHOD
    assert out["colour_samples"] >= 1
    assert out["mean_rgb"][0] > out["mean_rgb"][2]
    assert out["rb_all"] == pytest.approx(
        round(out["mean_rgb"][0] / out["mean_rgb"][2], 4))
    assert out["neutral_rb"] is not None
    assert out["neutral_rb"] > 1.0
    assert "colour_unmeasured_because" not in out


@pytest.mark.skipif(not FFMPEG_2, reason="ffmpeg not on PATH")
def test_extract_still_draws_inside_the_sampled_span(tmp_path):
    """The still the colourist sees is of the seconds the numbers
    describe - and a still that was not drawn is False, never a
    zero-byte file."""
    src = str(tmp_path / "clip.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=s=160x120:d=12",
         "-pix_fmt", "yuv420p", src],
        check=True)
    still = str(tmp_path / "still.jpg")
    assert sc.extract_still(src, still) is True
    assert tmp_path.joinpath("still.jpg").stat().st_size > 0
    assert sc.extract_still(str(tmp_path / "missing.mp4"),
                            str(tmp_path / "nope.jpg")) is False
    assert not (tmp_path / "nope.jpg").exists()


# --------------------------------------------------------------------------
# From test_look_matcher.py

class TestAnalyzeRefusesUnmeasurableFrames(unittest.TestCase):
    """A frame that cannot be measured must refuse the match, never
    stand in for a measured one.

    `analyze_frame_colors` used to catch every failure and return a
    plausible neutral statistic - the same type as its success path -
    which `match_clips_to_reference` then turned into a CDL the grade
    recorded as "AI Look Match CDL applied". A fabricated neutral is
    invented taste wearing a measurement's clothes.
    """

    NEUTRAL = {
        "shadows": [0.1, 0.1, 0.1],
        "midtones": [0.5, 0.5, 0.5],
        "highlights": [0.9, 0.9, 0.9],
    }

    def _red_square(self, directory):
        from PIL import Image
        path = os.path.join(directory, "red.png")
        Image.new("RGB", (16, 16), (200, 20, 20)).save(path)
        return path

    def test_missing_or_undecodable_file_raises_rather_than_returning_neutral(self):
        with self.assertRaises(Exception):
            analyze_frame_colors("/no/such/frame.png")
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "frame.png")
            with open(bad, "wb") as f:
                f.write(b"this is not an image")
            with self.assertRaises(Exception):
                analyze_frame_colors(bad)

    def test_pillow_missing_raises_a_named_error(self):
        real = look_matcher.PIL_AVAILABLE
        look_matcher.PIL_AVAILABLE = False
        try:
            with self.assertRaises(LookMatchUnavailable):
                analyze_frame_colors("whatever.png")
        finally:
            look_matcher.PIL_AVAILABLE = real

    def test_match_propagates_an_unmeasurable_clip_frame(self):
        with tempfile.TemporaryDirectory() as d:
            ref = self._red_square(d)
            bad = os.path.join(d, "clip.png")
            with open(bad, "wb") as f:
                f.write(b"not a frame either")
            with self.assertRaises(Exception):
                match_clips_to_reference(ref, {"clip_001": bad})
            missing = os.path.join(d, "never-extracted.png")
            with self.assertRaises(FileNotFoundError):
                match_clips_to_reference(ref, {"clip_001": missing})


# --------------------------------------------------------------------------
# From test_subject_grade.py
#
# Subject-only colour grading: a plan entry scopes a grade to the tracked subject.
#
# Failing-first contract for `library/tools/subject_grade.py` plus its three
# wiring points (Loader Clip support in `fusion/nodes.py`, the masked-grade
# dispatch in `fusion/comp_builder.py`, the 5.01 -> compile merge).
#
# What this proves, per the region-granularity scout
# (`data/vep-region-granular-editing/report.md` in the firstmate home):
# face-seeded subject-only grading, 2 fps mattes. Generic objects
# ("the laptop") wait on open-vocabulary grounding and are REFUSED here,
# not guessed at.
#
# The captain's hard rule holds throughout: no grade VALUE is hardcoded.
# Neutral (gain 1.0 / contrast 0.0 / saturation 1.0) is the absence of
# decoration, not taste (AGENTS.md 10.5) - it is the only number this
# feature may name. Every other number arrives from the plan entry.

# ─── Fixtures ───

def _seg_result(frames=4, shape=(24, 32), hole=False):
    """A face-seeded 1.06 result dict, as the JSON file carries it."""
    objects = []
    masks_rle = {}
    for f in range(frames):
        mask = np.zeros(shape, dtype=np.uint8)
        mask[6:18, 10:22] = 1
        if hole:
            mask[11, 15] = 0  # single-pixel speckle hole
        masks_rle[str(f)] = encode_rle(mask)
    objects.append({
        "object_id": "obj_1",
        "label": FACE_SEED_LABEL,
        "category": "person",
        "frames": list(range(frames)),
        "masks_rle": masks_rle,
        "bboxes": {str(f): [10, 6, 12, 12] for f in range(frames)},
        "avg_area_ratio": 0.18,
    })
    return {
        "video_path": "/footage/clip_001.mp4",
        "frame_count": frames,
        "resolution": list(shape),
        "sample_fps": 2.0,
        "seed_note": "face_seeded: one subject target",
        "objects": objects,
    }


def _warm_entry(**over):
    entry = {
        "clip_id": "clip_001",
        "target": {"kind": "person", "role": "speaker"},
        "scope": "subject-only",
        # Deliberately odd values: passthrough must be verbatim, never
        # snapped to a rounder number the engine prefers.
        "grade": {"gain": 1.173, "saturation": 1.311},
    }
    entry.update(over)
    return entry


# ─── 1. Vocabulary: what a plan entry may say ───

class TestParsePlanEntry:
    def test_accepts_speaker_subject_only_with_values(self):
        clean, drop = subject_grade.parse_plan_entry(_warm_entry())
        assert drop is None
        assert clean["clip_id"] == "clip_001"
        # Values travel verbatim - the mechanism carries no opinion
        # about how warm warm is.
        assert clean["grade"] == {"gain": 1.173, "saturation": 1.311}

    def test_what_it_cannot_honour_is_dropped_by_reason(self):
        clean, drop = subject_grade.parse_plan_entry(_warm_entry(
            target={"kind": "object", "label": "the laptop"}))
        assert clean is None
        assert "laptop" in drop["detail"]
        assert drop["reason"] == "open_vocabulary_target"
        assert "open-vocabulary" in drop["detail"]

        clean, drop = subject_grade.parse_plan_entry(
            _warm_entry(scope="everything-except"))
        assert clean is None
        assert "subject-only" in drop["detail"]

        clean, drop = subject_grade.parse_plan_entry(_warm_entry(
            grade={"gain": 1.0, "contrast": 0.0, "saturation": 1.0}))
        assert clean is None
        assert "no_readable_parameters" == drop["reason"]

        entry = _warm_entry()
        del entry["clip_id"]
        clean, drop = subject_grade.parse_plan_entry(entry)
        assert clean is None
        assert drop["reason"] == "no_clip_named"

# ─── 2. Grounding: the entry meets a real tracked subject ───

class TestGroundEntry:
    def test_no_face_seeded_object_does_not_ground(self):
        seg = _seg_result()
        seg["objects"][0]["label"] = "auto_object_1"
        seg["objects"][0]["category"] = "unknown"
        clean, _ = subject_grade.parse_plan_entry(_warm_entry())
        grounded, drop = subject_grade.ground_entry(clean, seg)
        assert grounded is None
        assert "face_seeded_subject" in drop["detail"]

# ─── 3. Matte writer: RLE JSON to timeline-rate PNGs ───

class TestWriteSubjectMatte:
    def test_holds_2fps_masks_over_timeline_frames(self, tmp_path):
        seg = _seg_result(frames=4, shape=(24, 32))
        record = subject_grade.write_subject_matte(
            seg, "obj_1", str(tmp_path), "clip_001",
            timeline_fps=30.0, played_frames=60, resolution=(24, 32))
        assert record["frame_count"] == 60
        assert len(record["files"]) == 60
        assert all(os.path.exists(f) for f in record["files"])
        # 2 fps over a 2-second span: mask 0 holds frames 0-14,
        # mask 1 holds 15-29, and so on.
        from PIL import Image
        first = np.array(Image.open(record["files"][0]))
        mid_span = np.array(Image.open(record["files"][20]))
        assert first.max() > 0
        assert mid_span.max() > 0
        # Provenance: which run, which object, which frames.
        assert record["provenance"]["object_id"] == "obj_1"
        assert record["provenance"]["sample_fps"] == 2.0
        assert record["resolution"] == [24, 32]

    def test_hole_fill_repairs_speckle(self, tmp_path):
        seg = _seg_result(frames=2, hole=True)
        record = subject_grade.write_subject_matte(
            seg, "obj_1", str(tmp_path), "clip_001",
            timeline_fps=30.0, played_frames=30, resolution=(24, 32))
        from PIL import Image
        frame = np.array(Image.open(record["files"][0]))
        # The single-pixel hole inside the body is filled: the body
        # rectangle reads solid.
        assert frame[11, 15] > 0

    def test_unknown_object_refuses(self, tmp_path):
        with pytest.raises(subject_grade.UnknownSubjectObject):
            subject_grade.write_subject_matte(
                _seg_result(), "obj_9", str(tmp_path), "clip_001",
                timeline_fps=30.0, played_frames=30, resolution=(24, 32))


# ─── 4. Comp block: Loader matte gates a BrightnessContrast ───

class TestSubjectGradeBlock:
    def test_masked_grade_wires_effect_mask_to_loader(self):
        block = subject_grade.subject_grade_block(
            matte_file="/m/matte_00000.png",
            gain=1.173, contrast=0.0, saturation=1.311)
        comp = (CompEngine(clip_dur=60, width=1080, height=1920)
                .add(fx.grade(gain=1.0, contrast=0.0, saturation=1.0))
                .add(block).serialize())
        assert "Loader" in comp
        assert "matte_00000.png" in comp
        assert "EffectMask" in comp
        assert "1.173" in comp
        assert "1.311" in comp

    def test_neutral_grade_draws_nothing(self):
        block = subject_grade.subject_grade_block(
            matte_file="/m/matte_00000.png",
            gain=1.0, contrast=0.0, saturation=1.0)
        assert block.nodes == []

    def test_comp_builder_dispatches_subject_grade_keys(self):
        comp = comp_builder.build_effect_comp(
            {"subject_grade_matte": "/m/matte_00000.png",
             "subject_grade_gain": 1.173,
             "subject_grade_saturation": 1.311},
            clip_dur=60, source_res=(32, 24))
        assert "Loader" in comp
        assert "EffectMask" in comp
        # Without the keys there is no Loader at all.
        assert "Loader" not in comp_builder.build_effect_comp(
            {"grade_gain": 1.1}, clip_dur=60, source_res=(32, 24))

# ─── 5. Validator: a named matte must exist and cover the window ───

class TestValidateMatte:
    def test_a_missing_short_or_mis_sized_matte_fails(self, tmp_path):
        record = subject_grade.write_subject_matte(
            _seg_result(frames=4), "obj_1", str(tmp_path), "clip_001",
            timeline_fps=30.0, played_frames=60, resolution=(24, 32))
        errors = subject_grade.validate_matte(
            record, source_resolution=(1080, 1920))
        assert len(errors) == 1
        assert "resolution" in errors[0]
        short = dict(record, files=record["files"][:30])
        errors = subject_grade.validate_matte(short, played_frames=60)
        assert len(errors) == 1
        assert "cover" in errors[0]
        os.remove(record["files"][0])
        errors = subject_grade.validate_matte(record)
        assert len(errors) == 1
        assert "missing" in errors[0]

# ─── 6. Compile merge: entries land on their clip's effects ───

# ─── 7. The plan-to-manifest path: 5.01 carries, compile grounds ───

def _compile_inputs(source, subject_grades):
    return {
        "color_grade_spec": {
            "per_clip_adjustments": [], "series_look": None,
            "fusion_look": {},
            "subject_grades": subject_grades,
            "subject_grade_drops": [],
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
                          "width": 32, "height": 24}],
        "a_roll_assignments": [{
            "spine_block_position": 1, "clip_id": "c1",
            "source_file": source, "video_in": 2.417, "video_out": 12.417,
            "timeline_start": 0.0, "timeline_end": 10.0,
        }],
        "b_roll_assignments": [], "transition_spec": [],
        "enhancement_spec": [], "sfx_spec": [], "audio_mix_spec": {},
    }


class TestCompileMerge:
    def test_grounded_entry_lands_on_its_clips_effects(self, tmp_path):
        import json as _json
        from unittest.mock import patch as _patch

        from library.steps.step_5_04_compile_manifest.step import compile_manifest

        seg_dir = tmp_path / "1_06_object_segmentation"
        seg_dir.mkdir()
        seg = _seg_result(frames=4, shape=(24, 32))
        (seg_dir / "c1_segmentation.json").write_text(_json.dumps(seg))

        source = os.path.abspath(__file__)
        entry = {"clip_id": "c1",
                 "target": {"kind": "person", "role": "speaker"},
                 "scope": "subject-only",
                 "grade": {"gain": 1.173, "saturation": 1.311}}
        inputs = _compile_inputs(source, [entry])
        with _patch(
                "library.steps.step_5_04_compile_manifest.step.load",
                side_effect=lambda out_dir, filename: inputs):
            manifest = compile_manifest(
                str(tmp_path), object_segmentation=[{"clip_id": "c1"}])

        per_clip = manifest["fusion_effects"]["per_clip"]
        assert per_clip, "the subject grade reached no clip"
        eff = next(iter(per_clip.values()))
        assert eff["subject_grade_gain"] == 1.173
        assert eff["subject_grade_saturation"] == 1.311
        assert os.path.exists(eff["subject_grade_matte"])
        assert manifest["subject_grade_drops"] == []
        assert len(manifest["subject_mattes"]) == 1
        assert subject_grade.validate_matte(manifest["subject_mattes"][0]) == []

    def test_unreported_old_mask_is_not_reused(self, tmp_path):
        import json as _json
        from unittest.mock import patch as _patch

        from library.steps.step_5_04_compile_manifest.step import compile_manifest

        seg_dir = tmp_path / "1_06_object_segmentation"
        seg_dir.mkdir()
        seg = _seg_result(frames=4, shape=(24, 32))
        (seg_dir / "c1_segmentation.json").write_text(_json.dumps(seg))

        source = os.path.abspath(__file__)
        entry = {"clip_id": "c1",
                 "target": {"kind": "person", "role": "speaker"},
                 "scope": "subject-only",
                 "grade": {"gain": 1.173}}
        inputs = _compile_inputs(source, [entry])
        with _patch(
                "library.steps.step_5_04_compile_manifest.step.load",
                side_effect=lambda out_dir, filename: inputs):
            manifest = compile_manifest(str(tmp_path), object_segmentation=[])

        assert manifest["fusion_effects"]["per_clip"] == {}
        assert len(manifest["subject_grade_drops"]) == 1
        assert manifest["subject_grade_drops"][0]["reason"] == (
            "no_segmentation")

    def test_ungrounded_entry_is_a_manifest_drop(self, tmp_path):
        from unittest.mock import patch as _patch

        from library.steps.step_5_04_compile_manifest.step import compile_manifest

        source = os.path.abspath(__file__)
        entry = {"clip_id": "c1",
                 "target": {"kind": "person", "role": "speaker"},
                 "scope": "subject-only",
                 "grade": {"gain": 1.173}}
        inputs = _compile_inputs(source, [entry])
        with _patch(
                "library.steps.step_5_04_compile_manifest.step.load",
                side_effect=lambda out_dir, filename: inputs):
            manifest = compile_manifest(str(tmp_path))

        # No 1.06 dir: nothing grounds, nothing is graded whole-frame.
        assert manifest["fusion_effects"]["per_clip"] == {}
        assert manifest["subject_mattes"] == []
        assert len(manifest["subject_grade_drops"]) == 1
        assert manifest["subject_grade_drops"][0]["reason"] == (
            "no_segmentation")


class TestFiveOhOneCarries:
    def test_define_color_grade_keeps_subject_grades(self):
        from unittest.mock import patch as _patch

        from library.steps.step_5_01_color_grade.grade import (
            LUMA_METHOD,
            define_color_grade,
        )

        measured = {"luma": 122.0, "method": LUMA_METHOD, "samples": 40}
        with _patch(
                "library.steps.step_5_01_color_grade.grade.measure_luma",
                return_value=dict(measured)):
            spec = define_color_grade(
                {"entries": [{"track": "V1", "clip_id": "c1",
                              "entry_id": "e1", "source_file": "f1.mov"}]},
                project_folder="proj",
                subject_grades=[_warm_entry()],
            )["color_grade_spec"]
        assert len(spec["subject_grades"]) == 1
        assert spec["subject_grades"][0]["grade"]["gain"] == 1.173
        assert spec["subject_grade_drops"] == []
