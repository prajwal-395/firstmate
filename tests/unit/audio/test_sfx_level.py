"""The sound effect's level is the model's, and no number replaces it.

An entry naming no `volume_db` is dropped with the reason; a level no clip
can carry is refused, never clamped. History: docs/evidence/sfx.md.
"""

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, REPO)

from library.tools.sfx_level import (  # noqa: E402
    SfxLevelRefused,
    read_volume_db,
)

SFX = os.path.join(REPO, "library", "steps", "step_4_04_plan_sfx")


def test_a_level_outside_what_a_clip_can_carry_is_refused_not_clamped():
    with pytest.raises(SfxLevelRefused, match="not clamped"):
        read_volume_db({"volume_db": -400})


def test_the_plan_step_really_places_the_level_the_model_wrote(tmp_path):
    """End to end through step 4.04's own post-bridge: the written level
    is placed verbatim, and the entry naming none is dropped with the
    reason rather than given one."""
    library = tmp_path / "sfx"
    library.mkdir()
    sound = library / "camera soft click.wav"
    sound.write_bytes(b"RIFF....WAVEfmt ")
    (library / "sfx_index.json").write_text(json.dumps([{
        "file": "camera soft click.wav",
        "path": str(sound),
        "folder_category": "Accents",
        "description": "a soft camera shutter",
        "technical": {
            "basic": {"duration": 0.459},
            "energy_profile": {"envelope_shape": "fading"},
        },
        "transient_offset_sec": 0.0,
    }]), encoding="utf-8")

    payload = {
        "timed_spine": {"frame_rate": 30.0, "structure": [
            {"position": 1, "block_type": "speech", "clip_id": "clip_1",
             "timeline_start": 0.0, "timeline_end": 10.0,
             "source_start": 0.117, "source_end": 10.117,
             "music_behavior": "background", "word_timestamps": [],
             "alignment_method": "whisperx",
             "content": {"clip_id": "clip_1"}}]},
        "temporal_event_indices": [], "music_analysis": {},
        "music_selection": {},
        "sfx_creative": [
            {"spine_block_position": 1, "sfx_id": "camera soft click.wav",
             "volume_db": -4, "rationale": "the shutter under the line"},
            {"spine_block_position": 1, "sfx_id": "camera soft click.wav",
             "rationale": "named no level"},
        ],
    }
    env = dict(os.environ, PIPELINE_SFX_LIBRARY=str(library))
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, os.path.join(SFX, "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", env=env, cwd=REPO, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert [p["volume_db"] for p in placed] == [-4.0]
    assert "names no volume_db" in proc.stderr
