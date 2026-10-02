"""Step 5.01 asks a colourist, and the answer reaches the CDL.

The step measured project 001's nine clips correctly - clip_011 at
145.495 luma, clip_017 at 53.116, a 2.7x spread across shots that touch
at cuts - and wrote the identity CDL on all nine, because the only route
to a correction was an `exposure_reference` that only a brand template
declares and 001 declares no template.

This drives the real `bridge.py` and `post_bridge.py` as subprocesses,
the way `run_hybrid_step` does, so what is asserted is what the step
really produces.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
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




