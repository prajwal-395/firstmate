"""Atmospheric SFX arrive as a LAYER, reasoned like any other sound.

Captain's ruling 2026-09-08: the pipeline may use risers, drones and
crackles as a layer - a sound that plays UNDER the picture rather than
marking a visible event - not only literal sound tied to one. 001 used
only literal SFX.

Two mechanics make a layer different from a literal placement, and both
are in step 4.04's post-bridge:

  * a layer is NOT shifted off speech. `_avoid_speech_collision` moves a
    literal sound into a word gap; a bed-like drone under a passage that
    got moved into a gap would stop being a layer. `role: "layer"` keeps
    the envelope-placed position.
  * a layer without a stated reason is dropped. Whatever reasons a
    literal choice - a real `sfx_id` out of the catalogue and the plan's
    own `volume_db` - reasons a layer too, PLUS the `rationale` naming
    what in the library entry made it right, because a layer is not tied
    to a visible event and there is nothing else holding it to the
    moment. A layer with no rationale is sprinkling, and it goes.

A `role` nobody reads is dropped the same way an effect type nobody
reads is: the plan asked for something the engine would silently not do.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

SFX = REPO / "library" / "steps" / "step_4_04_plan_sfx"

DRONE = "test_drone.wav"
RISER = "test_riser.wav"
IMPACT = "test_impact.wav"


def _library(tmp_path):
    """Three sounds - a sustained drone, a swelling riser, a punchy
    impact - never the captain's."""
    lib = tmp_path / "sfx_library"
    lib.mkdir()
    entries = []
    for name, duration, shape in ((DRONE, 8.0, "sustained"),
                                  (RISER, 4.0, "swelling"),
                                  (IMPACT, 0.3, "punchy")):
        audio = lib / name
        audio.write_bytes(b"RIFF....WAVEfmt ")
        entries.append({
            "file": name,
            "path": str(audio),
            "folder_category": "Accents",
            "description": f"a {shape} test sound",
            "technical": {
                "basic": {"duration": duration},
                "energy_profile": {"envelope_shape": shape},
            },
            "transient_offset_sec": 0.0,
        })
    (lib / "sfx_index.json").write_text(json.dumps(entries))
    return {"PIPELINE_SFX_LIBRARY": str(lib)}


def _block(position, tl_start, tl_end, words=()):
    """One speech block with word ends at the given TIMELINE seconds."""
    return {
        "position": position, "block_type": "speech",
        "clip_id": "clip_001",
        "source_start": tl_start, "source_end": tl_end,
        "timeline_start": float(tl_start), "timeline_end": float(tl_end),
        "word_timestamps": [{"source_end": float(w)} for w in words],
        "alignment_method": "whisperx",
    }


def _payload(plan, blocks):
    return {
        "sfx_creative": plan,
        "timed_spine": {"structure": blocks},
        "temporal_event_indices": [{
            "clip_id": "clip_001",
            "onset_times": [],
            "energy_curve": {"peak_times": []},
            "scene_boundaries": [],
        }],
        "music_analysis": {},
        "music_selection": {"audio_path": ""},
        "project_fps": 30.0,
        "creative_direction": {},
    }


def _run(payload, env_lib):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_lib)
    proc = subprocess.run(
        [sys.executable, str(SFX / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env, check=False)
    return proc


def test_layer_plays_under_speech_instead_of_being_shifted_off_it(tmp_path):
    """The refusal's counterpart: a reasoned layer is KEPT where a
    literal sound would be moved. The block opens with two seconds of
    unbroken words; the literal impact is shifted past them into the
    gap at 2.0 and the layer stays at the envelope-placed position
    (the block start), under the words."""
    lib = _library(tmp_path)
    words = [round(0.1 * i, 3) for i in range(1, 21)]
    proc = _run(_payload([
        {"spine_block_position": 1, "sfx_id": IMPACT, "volume_db": -8,
         "rationale": "a hit on the opening"},
        {"spine_block_position": 1, "sfx_id": IMPACT, "volume_db": -8,
         "role": "layer",
         "rationale": "a textural tick under the opening words"},
    ], [_block(1, 0, 12, words)]), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert len(placed) == 2
    literal = [s for s in placed if s.get("role") != "layer"]
    layer = [s for s in placed if s.get("role") == "layer"]
    assert len(literal) == 1 and len(layer) == 1
    # The literal sound was moved off the words; the layer was not.
    assert literal[0]["timeline_in"] == 2.0
    assert layer[0]["timeline_in"] == 0.0
    assert layer[0]["duration_seconds"] == 0.3


def test_layer_without_a_stated_reason_is_dropped(tmp_path):
    """A layer is not tied to a visible event, so its rationale is the
    only thing holding it to the moment. Without one it goes."""
    lib = _library(tmp_path)
    proc = _run(_payload([
        {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -20,
         "role": "layer"},
    ], [_block(1, 0, 12)]), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert placed == [], f"a reasonless layer survived: {placed}"
    assert "states no reason" in proc.stderr


def test_blank_rationale_is_no_rationale(tmp_path):
    lib = _library(tmp_path)
    proc = _run(_payload([
        {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -20,
         "role": "layer", "rationale": "   "},
    ], [_block(1, 0, 12)]), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert placed == [], f"a blank-rationale layer survived: {placed}"


def test_unknown_role_is_dropped_not_silently_literal(tmp_path):
    """A role the engine does not read would ship something other than
    asked - the plan asked for a behaviour and got a literal placement."""
    lib = _library(tmp_path)
    proc = _run(_payload([
        {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -20,
         "role": "sting", "rationale": "a sting on the cut"},
    ], [_block(1, 0, 12)]), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert placed == [], f"an unknown role survived: {placed}"
    assert "sting" in proc.stderr


def test_layer_may_span_past_its_own_block(tmp_path):
    """An 8s drone placed on a 4s block plays all 8s. A layer that was
    cut at its block edge could never be a bed."""
    lib = _library(tmp_path)
    proc = _run(_payload([
        {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -20,
         "role": "layer",
         "rationale": "a bed that carries across the cut"},
    ], [_block(1, 0, 4), _block(2, 4, 8)]), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert len(placed) == 1
    assert placed[0]["timeline_in"] == 0.0
    assert placed[0]["timeline_out"] == 8.0
