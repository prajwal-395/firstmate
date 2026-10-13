"""Accepted plan_vfx toolkit entries reach a Project Model as moves.

Step 4.03 resolves the plan for Fusion comps; this file proves the same
accepted entries also mint `move.*` model effects on the step path: it
drives the REAL post-bridge as a subprocess (the way `run_pipeline`
runs it), finds one minted effect per surviving toolkit entry in the
written `enhancement_spec`, and projects each through the EffectCraft
adapter onto transform keyframe ops - so the gap "toolkit entries die
in Resolve/Fusion land and never reach a model" is refuted by a minted
model, not by an argument. The Fusion resolve travels unchanged
beside it (`test_plan_reaches_the_manifest.py`).
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
POST_BRIDGE = "library.steps.step_4_03_plan_vfx.post_bridge"
FIXTURES = REPO / "tests/fixtures/effectcraft_fframes_conformance"

FPS = 10.0


def _spine():
    return {"structure": [
        {"position": position, "block_type": "speech",
         "timeline_start": float((position - 1) * 5),
         "timeline_end": float(position * 5)}
        for position in (1, 2, 3, 4)
    ]}


def _assignments():
    clips = ("A", "B", "A", "B")
    return [{
        "spine_block_position": position,
        "timeline_start": float((position - 1) * 5),
        "timeline_end": float(position * 5),
        "video_segments": [{
            "clip_id": clips[position - 1],
            "source_file": f"/footage/{clips[position - 1]}.mov",
            "source_start": 0.0, "source_end": 5.0,
            "frame_rate": FPS,
        }],
    } for position in (1, 2, 3, 4)]


def _plan():
    return [
        {"target_block_position": 1, "effect_type": "slow_zoom_in",
         "params": {"zoom_start": 1.0, "zoom_end": 1.03,
                    "zoom_easing": "Sine"},
         "rationale": "a locked hold that goes dead under the point"},
        {"target_block_position": 2, "effect_type": "zoom_emphasis",
         "params": {"zoom_start": 1.0, "zoom_mid": 1.08,
                    "zoom_end": 1.0, "zoom_in_seconds": 1.0,
                    "zoom_out_seconds": 1.0},
         "anchor": {"frame": 70}, "anchor_end": {"frame": 80},
         "rationale": "a landing to punch"},
        {"target_block_position": 3, "effect_type": "screen_shake",
         "params": {"shake_x": 0.02, "shake_y": 0.01},
         "rationale": "an impact to sell"},
        {"target_block_position": 4, "effect_type": "cut_in",
         "params": {"zoom_start": 1.06, "zoom_mid": 1.06,
                    "zoom_end": 1.06},
         "rationale": "a face to hold closer"},
        # Withdrawn, not motion: dropped with the toolkit listed, while
        # the four real entries resolve and mint beside it. Anchored to
        # its own span so it reaches the alias check instead of the
        # duplicate-block drop.
        {"target_block_position": 1, "effect_type": "slow_zoom",
         "params": {"zoom_start": 1.0, "zoom_end": 1.03},
         "anchor": {"frame": 10},
         "rationale": "names no direction"},
    ]


def _run_step():
    payload = {"a_roll_assignments": _assignments(),
               "timed_spine": _spine(),
               "vfx_creative": _plan(),
               "frame_rate": FPS}
    proc = subprocess.run(
        [sys.executable, "-m", POST_BRIDGE],
        input=json.dumps(payload), capture_output=True,
        encoding="utf-8", cwd=str(REPO), timeout=300,
        check=False)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)["enhancement_spec"]


@pytest.fixture(scope="module")
def spec():
    return _run_step()


def test_every_surviving_toolkit_entry_mints_one_move(spec):
    assert [entry["effect_type"] for entry in
            spec["visual_effects"]] == [
        "slow_zoom_in", "zoom_emphasis", "screen_shake", "cut_in"]
    assert [effect["class_id"] for effect in
            spec["move_effects"]] == [
        "move.slow_zoom_in", "move.zoom_emphasis",
        "move.screen_shake", "move.cut_in"]
    assert spec["move_effects_skipped"] == []
    # The withdrawn alias is still a drop, not a mint and not a skip.
    assert [drop["reason"] for drop in
            spec["planning_basis"]["dropped"]] == ["withdrawn_alias"]


def test_minted_targets_are_the_step_assignments_migrated(spec):
    """Target ids are post-migration stable clip-use ids, not block ids.

    Re-running the Unit-4 migration over the same assignments names
    the same uses the step minted against.
    """
    from library.tools.project_model_editorial import (
        clip_uses_from_assignments,
    )
    adapted = clip_uses_from_assignments(_assignments(),
                                         timeline_tps=FPS)
    assert adapted["skipped"] == []
    assert {effect["target_id"] for effect in spec["move_effects"]} == {
        use["id"] for use in adapted["uses_by_block"].values()}
    for effect in spec["move_effects"]:
        use = next(use for use in adapted["uses_by_block"].values()
                   if use["id"] == effect["target_id"])
        assert effect["interval"] == use["timeline_interval"]
        assert effect["interval"]["clock_id"] == "clock-project"
    drift = next(effect for effect in spec["move_effects"]
                 if effect["class_id"] == "move.slow_zoom_in")
    assert drift["parameters"]["zoom_easing"] == "Sine"


def _model_with_moves(spec):
    """The minted effects seated in a valid model over migrated uses."""
    from library.tools.project_model_editorial import (
        clip_uses_from_assignments,
    )
    adapted = clip_uses_from_assignments(_assignments(),
                                         timeline_tps=FPS)
    model = json.loads((FIXTURES / "model-move-drift.json")
                       .read_text(encoding="utf-8"))
    fixture_clocks = list(model["clocks"])
    tps = int(FPS)
    clocks = []
    for entry in adapted["clocks"]:
        domain = ({"kind": "project"} if entry["id"] == "clock-project"
                  else {"kind": "source",
                        "asset_id": "asset-%s" % entry["id"].split(
                            "clock-src-", 1)[1]})
        clocks.append({"id": entry["id"], "domain": domain,
                       "ticks_per_second": entry["ticks_per_second"],
                       "frame_rate": {"numerator": entry[
                           "ticks_per_second"], "denominator": 1}})
    model["clocks"] = clocks
    # The fixture's checker stream still lives on its own clock: keep any
    # fixture clock retained streams reference, so the seating validates
    # with every stream clock resolvable instead of dangling.
    retained = {stream["clock_id"] for stream in model.get("streams", [])}
    have = {clock["id"] for clock in model["clocks"]}
    model["clocks"].extend(
        clock for clock in fixture_clocks
        if clock["id"] in retained and clock["id"] not in have)
    assets = [asset for asset in model["assets"]]
    for use in adapted["uses_by_block"].values():
        asset_id = use["asset_id"]
        if asset_id not in {asset["id"] for asset in assets}:
            assets.append({"id": asset_id, "kind": "image",
                           "uri": "checker.bmp", "content_digest": None,
                           "metadata": {"license": "ren-owned test pattern"},
                           "provenance": ["prov-ec-conf"]})
    model["assets"] = assets
    model["clip_uses"] = list(adapted["uses_by_block"].values())
    model["effects"] = spec["move_effects"]
    return model, tps


def test_minted_moves_validate_and_key_their_transforms(spec):
    """The written move effects ARE a projectable model.

    Seated over the migrated uses, they validate as a Project Model and
    each projects to keyframe ops on its expected transform path.
    """
    from library.tools.project_model import validate_project_model
    from library.tools.project_model_effectcraft import (
        CompSpec,
        RenderSpec,
        project_to_request,
    )
    model, tps = _model_with_moves(spec)
    validate_project_model(model)
    for effect in spec["move_effects"]:
        solo = copy.deepcopy(model)
        use = next(use for use in model["clip_uses"]
                   if use["id"] == effect["target_id"])
        solo["clip_uses"] = [use]
        solo["effects"] = [effect]
        interval = effect["interval"]
        ticks = next(clock["ticks_per_second"] for clock in
                     model["clocks"]
                     if clock["id"] == interval["clock_id"])
        comp = CompSpec(name="PMProof", width=96, height=64,
                        frame_rate=float(tps),
                        duration_seconds=interval["end"] / ticks)
        projection = project_to_request(
            solo, comp, [RenderSpec(format="rgba_png_sequence",
                                    output="pmproof_#####.png")],
            media_root=str(FIXTURES),
            project_id=f"pm-proof-{effect['id']}")
        keyed = [op["path"] for op in projection.request["operations"]
                 if op.get("op") == "keyframe"]
        assert keyed, effect["id"]
        want = ("transform/position"
                if effect["class_id"] == "move.screen_shake"
                else "transform/scale")
        assert want in set(keyed), effect["id"]
