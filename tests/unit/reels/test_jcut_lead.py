"""Sound may LEAD picture: `lead_seconds` on an `sfx_creative` entry starts
the whole sound earlier, bounded by the previous cut and the top of the
reel; a lead outside those bounds is refused.

History: docs/evidence/jl_cuts.md.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

SFX = REPO / "library" / "steps" / "step_4_04_plan_sfx"

DRONE = "test_drone.wav"
IMPACT = "test_impact.wav"


def _library(tmp_path):
    lib = tmp_path / "sfx_library"
    lib.mkdir()
    entries = []
    for name, duration, shape in ((DRONE, 8.0, "sustained"),
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
    return {
        "position": position, "block_type": "speech",
        "clip_id": "clip_001",
        "source_start": tl_start, "source_end": tl_end,
        "timeline_start": float(tl_start), "timeline_end": float(tl_end),
        "word_timestamps": [{"source_end": float(w)} for w in words],
        "alignment_method": "whisperx",
    }


def _blocks():
    return [_block(1, 0, 6), _block(2, 6, 12), _block(3, 12, 18)]


def _payload(plan, blocks):
    return {
        "sfx_creative": plan,
        "timed_spine": {"structure": blocks},
        # No onsets, peaks or scenes: a sustained sound stays at the
        # block start, so the lead arithmetic reads plainly.
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


def test_lead_starts_the_sound_before_the_cut(tmp_path):
    """Block 2 starts at 6.0; a 1.5s lead starts the 8s drone at 4.5
    and the duration is preserved, not re-anchored. The boundary is
    inclusive: block 3's hit led 6.0s lands exactly ON the previous cut
    and reaches into no earlier moment."""
    lib = _library(tmp_path)
    proc = _run(_payload([
        {"spine_block_position": 2, "sfx_id": DRONE, "volume_db": -16,
         "lead_seconds": 1.5,
         "rationale": "the riser swells before the cut it belongs to"},
        {"spine_block_position": 3, "sfx_id": IMPACT, "volume_db": -8,
         "lead_seconds": 6.0, "rationale": "a hit across the whole tail"},
    ], _blocks()), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = {s["sfx_id"]: s
              for s in json.loads(proc.stdout)["sfx_spec"]["sfx_list"]}
    assert set(placed) == {DRONE, IMPACT}
    assert placed[DRONE]["timeline_in"] == 4.5
    assert placed[DRONE]["timeline_out"] == 12.5
    assert placed[DRONE]["duration_seconds"] == 8.0
    assert placed[DRONE]["lead_seconds"] == 1.5
    assert placed[IMPACT]["timeline_in"] == 6.0


def test_a_lead_reaching_outside_its_block_is_refused(tmp_path):
    """Past the previous cut (block 2 at 6.0 led 8s starts at -2.0),
    off the top of the reel (block 1 has no previous cut), and a
    negative lead each drop their entry by name; the run survives and
    an unled hit still lands."""
    lib = _library(tmp_path)
    proc = _run(_payload([
        {"spine_block_position": 2, "sfx_id": DRONE, "volume_db": -16,
         "lead_seconds": 8.0, "rationale": "too early a swell"},
        {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -16,
         "lead_seconds": 1.0, "rationale": "a swell with nowhere early"},
        {"spine_block_position": 2, "sfx_id": DRONE, "volume_db": -16,
         "lead_seconds": -1.0, "rationale": "a lead backwards"},
        {"spine_block_position": 3, "sfx_id": IMPACT, "volume_db": -8,
         "rationale": "the hit that still lands"},
    ], _blocks()), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert [s["sfx_id"] for s in placed] == [IMPACT]
    assert "previous cut" in proc.stderr
    assert "top of the reel" in proc.stderr


def test_layer_with_a_lead_swells_early_and_under_speech(tmp_path):
    """The three compose: a reasoned layer with a lead starts before
    the cut AND is not shifted off the previous block's words."""
    lib = _library(tmp_path)
    words = [4.5, 5.0, 5.5, 6.5, 7.5, 8.5]
    blocks = [_block(1, 0, 6, [w for w in words if w < 6]),
              _block(2, 6, 12, [w for w in words if w >= 6]),
              _block(3, 12, 18)]
    proc = _run(_payload([
        {"spine_block_position": 2, "sfx_id": DRONE, "volume_db": -20,
         "role": "layer", "lead_seconds": 1.5,
         "rationale": "the bed arrives early under the tail"},
    ], blocks), lib)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert len(placed) == 1
    assert placed[0]["timeline_in"] == 4.5
    assert placed[0]["role"] == "layer"
