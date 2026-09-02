"""A non-empty VFX plan reaches the manifest, and the picture.

Step 4.03 has emitted `{"visual_effects": []}` on every run the
repository can show, and the question that had to be settled before
anything was built was WHICH of four things that means: nothing asks it
for effects in a form it can answer, it answers and the answer is
dropped, the vocabulary does not exist, or it decided none on the merits.

This file is the half of the answer that has to be RUN rather than read.
It drives the real post-bridge as a subprocess, feeds its actual output
into the real `compile_manifest`, and follows the effect through to the
Fusion comp string the renderer would import - so "the answer is dropped"
is refuted by a plan arriving in the picture, not by an argument.

`tests/test_vfx_plan_basis.py` is the other half: an empty plan is still
accepted, and now says why it is empty.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.project_layout import ProjectLayout

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_compile_manifest_without_the_decoration import (  # noqa: E402
    _step_outputs,
)

REPO = Path(__file__).resolve().parent.parent
POST_BRIDGE = "library.steps.step_4_03_plan_vfx.post_bridge"


@pytest.fixture
def recorded_run(tmp_path):
    """One recorded run on file, under tmp_path only (AGENTS.md 8)."""
    media = tmp_path / "media"
    media.mkdir()
    names = {}
    for name in ("a_roll.mov", "b_roll.mov", "bed.wav", "whoosh.wav",
                 "sub_seg_000.mov"):
        path = media / name
        path.write_bytes(b"\x00" * 64)
        names[name] = str(path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    layout = ProjectLayout(str(project_dir))
    layout.ensure()
    outputs = _step_outputs(names["a_roll.mov"], names["b_roll.mov"],
                            names["bed.wav"], names["whoosh.wav"],
                            names["sub_seg_000.mov"])
    return project_dir, layout, outputs, names["whoosh.wav"]


def _spine_of(outputs):
    return {"structure": outputs["mesh_spine"]["audio_spine"]["structure"]}


def _plan_through_the_post_bridge(outputs, plan):
    """The REAL post-bridge, run the way `run_pipeline` runs it."""
    payload = {
        "a_roll_assignments": outputs["assign_aroll"]["a_roll_assignments"],
        "timed_spine": _spine_of(outputs),
        "vfx_creative": plan,
    }
    proc = subprocess.run(
        [sys.executable, "-m", POST_BRIDGE],
        input=json.dumps(payload), capture_output=True,
        encoding="utf-8", cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["enhancement_spec"], proc.stderr


def _compile_with(recorded_run, enhancement_spec):
    """The REAL compile_manifest, over that post-bridge's own output."""
    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, sfx_file = recorded_run
    outputs = copy.deepcopy(outputs)
    outputs["plan_vfx"] = {"enhancement_spec": enhancement_spec}
    layout.pipeline_data_path.write_text(
        json.dumps({"step_outputs": outputs,
                    "project_folder": str(project_dir)}),
        encoding="utf-8")
    with patch.object(step, "load_sfx_catalog", return_value=[
            {"sfx_id": "whoosh.wav", "path": sfx_file,
             "duration_seconds": 0.5, "transient_offset_sec": 0.0}]):
        return step.compile_manifest(str(layout.output_root))


def test_a_planned_effect_reaches_the_manifest_and_the_comp(recorded_run):
    """End to end: a model answer, through both bridges, into a comp.

    The plan names one block of the recorded spine, one effect from the
    step's own toolkit, and its own values under the parameter names the
    renderer reads - which is the whole of what the handoff asks a
    planner for.
    """
    _, _, outputs, _ = recorded_run
    block = outputs["mesh_spine"]["audio_spine"]["structure"][0]["position"]

    spec, _ = _plan_through_the_post_bridge(outputs, [{
        "target_block_position": block,
        "effect_type": "slow_zoom_in",
        "params": {"zoom_start": 1.0, "zoom_end": 1.03},
        "rationale": "a long static hold that wants a drift",
    }])

    # 1. the post-bridge resolved it, and says the plan was planned.
    assert len(spec["visual_effects"]) == 1
    assert spec["planning_basis"]["basis"] == "planned"
    effect = spec["visual_effects"][0]
    assert effect["effect_type"] == "slow_zoom_in"
    assert effect["params"] == {"zoom_start": 1.0, "zoom_end": 1.03}

    # 2. compile_manifest carried it onto the assembly manifest.
    manifest = _compile_with(recorded_run, spec)
    assert len(manifest["vfx"]) == 1
    assert manifest["vfx"][0]["effect_type"] == "slow_zoom_in"

    # 3. it reached the V1 clip's own Fusion effect, by parameter name -
    #    which is the only thing the renderer dispatches on (AGENTS.md
    #    10.2).
    per_clip = manifest["fusion_effects"]["per_clip"]
    carrying = [c for c in per_clip.values()
                if c.get("_preset") == "slow_zoom_in"]
    assert len(carrying) == 1, per_clip
    assert carrying[0]["zoom_start"] == 1.0
    assert carrying[0]["zoom_end"] == 1.03

    # 4. and those parameters draw real nodes.
    params = {k: v for k, v in carrying[0].items() if not k.startswith("_")}
    params["vignette"] = False
    comp = build_effect_comp(params, 120)
    assert "Tools = {" in comp
    assert "Transform" in comp


def test_an_empty_plan_still_compiles_and_still_says_why(recorded_run):
    """The accepting half is untouched: 275's ruling still holds."""
    _, _, outputs, _ = recorded_run
    spec, _ = _plan_through_the_post_bridge(outputs, [])
    assert spec["visual_effects"] == []
    assert spec["planning_basis"]["basis"] == "no_effects_planned"

    manifest = _compile_with(recorded_run, spec)
    assert manifest["vfx"] == []
    assert manifest["generator_overlays"] == []
    # An empty plan is the absence of decoration, not a broken build.
    assert len(manifest["tracks"]["V1"]["clips"]) == 2


def test_an_unbuildable_plan_compiles_too_and_is_told_apart(recorded_run):
    """The plan the pipeline could not build still reaches the manifest
    as an empty one - the change is that the output now says so."""
    _, _, outputs, _ = recorded_run
    block = outputs["mesh_spine"]["audio_spine"]["structure"][0]["position"]
    spec, _ = _plan_through_the_post_bridge(outputs, [{
        "target_block_position": block,
        "effect_type": "glitch", "params": {"zoom_start": 1.0, "zoom_end": 1.03},
        "rationale": "an effect the toolkit has not got",
    }])
    assert spec["visual_effects"] == []
    assert spec["planning_basis"]["basis"] == "every_entry_dropped"

    manifest = _compile_with(recorded_run, spec)
    assert manifest["vfx"] == []


def test_compile_manifest_reads_the_basis_and_names_the_casualties(
        recorded_run, caplog):
    """The record has a reader outside the step that writes it.

    `compile_manifest` is where an empty VFX plan becomes an absence in
    the picture, so it is where an unbuildable one is said out loud.
    Reading is not gating: the compile still succeeds, and whether a
    dropped entry should refuse the step is recorded in
    `vfx_plan_basis.THE_REFUSAL_QUESTION` rather than answered here.
    """
    import logging

    _, _, outputs, _ = recorded_run
    block = outputs["mesh_spine"]["audio_spine"]["structure"][0]["position"]
    spec, _ = _plan_through_the_post_bridge(outputs, [{
        "target_block_position": block,
        "effect_type": "glitch", "params": {"zoom_start": 1.0, "zoom_end": 1.03},
        "rationale": "an effect the toolkit has not got",
    }])

    with caplog.at_level(logging.INFO):
        manifest = _compile_with(recorded_run, spec)

    assert manifest["vfx"] == []
    text = caplog.text
    assert "every_entry_dropped" in text
    assert "glitch" in text
    assert "unknown_effect_type" in text


def test_a_plan_the_planner_left_empty_is_not_reported_as_a_casualty(
        recorded_run, caplog):
    import logging

    _, _, outputs, _ = recorded_run
    spec, _ = _plan_through_the_post_bridge(outputs, [])
    with caplog.at_level(logging.INFO):
        _compile_with(recorded_run, spec)
    assert "no_effects_planned" in caplog.text
    assert "every_entry_dropped" not in caplog.text
