"""The plan can layer, and a swelling sound really builds into the cut.

Project 001 shipped two camera shutters - 0.46s and 0.34s - at two blocks,
layering nothing. The planner's own `could_not_determine` recorded the
reason: it judged the library *"built for a different kind of edit"*.
That was a judgement made without two facts, both of them measurements
rather than opinions, and both now in its context
(`library/tools/sfx_envelope.py`).

The capability was never missing. This drives the real post-bridge
against a library built under `tmp_path` and asserts it: two sounds on
one block survive as two placements, and a `swelling` sound is anchored
by its END so its climax lands on the measured energy peak.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

SFX = REPO / "library" / "steps" / "step_4_04_plan_sfx"

RISER = "test_riser.wav"
IMPACT = "test_impact.wav"


@pytest.fixture
def sfx_library(tmp_path):
    """Two sounds - a 4s riser and a 0.3s impact - never the captain's."""
    lib = tmp_path / "sfx_library"
    lib.mkdir()
    entries = []
    for name, duration, shape in ((RISER, 4.0, "swelling"),
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


def _payload(plan):
    return {
        "sfx_creative": plan,
        "timed_spine": {"structure": [
            {"position": 1, "block_type": "speech", "clip_id": "clip_001",
             "source_start": 0.0, "source_end": 6.0,
             "timeline_start": 0.0, "timeline_end": 6.0,
             "word_timestamps": [], "alignment_method": "whisperx"},
            {"position": 2, "block_type": "speech", "clip_id": "clip_001",
             "source_start": 6.0, "source_end": 12.0,
             "timeline_start": 6.0, "timeline_end": 12.0,
             "word_timestamps": [], "alignment_method": "whisperx"},
        ]},
        # One measured energy peak, 1.5s into the block. A swelling sound
        # should END there; a punchy one should START on the transient.
        "temporal_event_indices": [{
            "clip_id": "clip_001",
            "onset_times": [0.2],
            "energy_curve": {"peak_times": [1.5]},
            "scene_boundaries": [],
        }],
        "music_analysis": {},
        "music_selection": {"audio_path": ""},
        "project_fps": 30.0,
        "creative_direction": {},
    }


def _run(payload, sfx_library):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(sfx_library)
    proc = subprocess.run(
        [sys.executable, str(SFX / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env, check=False)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)["sfx_spec"]["sfx_list"]


def test_two_sounds_on_one_block_survive_as_two_placements(sfx_library):
    """Layering is legal, and the collapse check does not catch it: a
    collapse is ONE distinct position across the whole plan."""
    placed = _run(_payload([
        {"spine_block_position": 1, "sfx_id": RISER, "volume_db": -14,
         "rationale": "the build under the line"},
        {"spine_block_position": 1, "sfx_id": IMPACT, "volume_db": -8,
         "rationale": "the weight it arrives on"},
        {"spine_block_position": 2, "sfx_id": IMPACT, "volume_db": -10,
         "rationale": "the answering hit"},
    ]), sfx_library)

    assert len(placed) == 3
    on_block_one = [s for s in placed if s["spine_block_position"] == 1]
    assert len(on_block_one) == 2
    assert {s["sfx_id"] for s in on_block_one} == {RISER, IMPACT}
    # Each layer carries its own level - not one bus level for the pair.
    assert sorted(s["volume_db"] for s in on_block_one) == [-14.0, -8.0]


def test_moments_planned_on_different_blocks_cannot_collapse_to_one_position():
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _assert_sfx_distributed,
    )

    with pytest.raises(ValueError, match="collapse"):
        _assert_sfx_distributed([
            {"label": "sfx_001", "timeline_in": 6.0,
             "spine_block_position": 1},
            {"label": "sfx_002", "timeline_in": 6.0,
             "spine_block_position": 4},
        ])


def test_a_swelling_sound_ends_on_the_peak_when_the_block_allows_it(
        sfx_library):
    payload = _payload([
        {"spine_block_position": 1, "sfx_id": RISER, "volume_db": -14,
         "rationale": "rises into the beat"},
    ])
    # Move the peak far enough into the block that a 4s riser fits before
    # it: source 5.0s is timeline 5.0s on block 1.
    payload["temporal_event_indices"][0]["energy_curve"]["peak_times"] = [5.0]
    placed = _run(payload, sfx_library)
    entry = placed[0]
    assert entry["timeline_out"] == pytest.approx(5.0, abs=0.05), (
        "a swelling sound must END on the peak - that is the whole of "
        "what a build is on this route")
    assert entry["timeline_in"] == pytest.approx(1.0, abs=0.05)


def test_a_punchy_sound_is_anchored_by_its_start_instead(sfx_library):
    """The contrast that makes the envelope column worth reading."""
    placed = _run(_payload([
        {"spine_block_position": 1, "sfx_id": IMPACT, "volume_db": -8,
         "rationale": "lands on the transient"},
    ]), sfx_library)
    entry = placed[0]
    assert entry["sfx_envelope"] == "punchy"
    assert entry["timeline_in"] == pytest.approx(0.2, abs=0.05)

