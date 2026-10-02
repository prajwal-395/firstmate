"""Bridges preserve sparse creative answers rather than completing a quota.

The registry's `creative_policy` declarations are audited by
`capabilities.problems()` (contract_audit section 1); these two examples
exercise sparse answers through real bridge entry points so the policy is
not only metadata. The ruling and the quotas it removed:
`docs/evidence/creative_policy.md`.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
STEPS = REPO / "library" / "steps"
BROLL = STEPS / "step_3_02_select_broll"
SFX = STEPS / "step_4_04_plan_sfx"


def _run_bridge(script: Path, payload: dict, extra_env: dict = None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO),
        env=env,
    )


@pytest.fixture(scope="module")
def sfx_library(tmp_path_factory):
    lib = tmp_path_factory.mktemp("sfx_library")
    audio = lib / "test_whoosh.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    (lib / "sfx_index.json").write_text(
        json.dumps(
            [
                {
                    "file": audio.name,
                    "path": str(audio),
                    "folder_category": "Accents",
                    "description": "a soft air movement",
                    "technical": {
                        "basic": {"duration": 0.4},
                        "energy_profile": {"envelope_shape": "fading"},
                    },
                    "transient_offset_sec": 0.05,
                }
            ]
        ),
        encoding="utf-8",
    )
    return {"PIPELINE_SFX_LIBRARY": str(lib)}


def test_select_broll_accepts_a_single_cutaway():
    payload = {
        "clip_catalog": [
            {
                "clip_id": "clip_001",
                "source_file": "/tmp/a.mov",
                "duration_seconds": 30.0,
                "width": 1080,
                "height": 1920,
            },
            {
                "clip_id": "clip_002",
                "source_file": "/tmp/b.mov",
                "duration_seconds": 30.0,
                "width": 1080,
                "height": 1920,
            },
        ],
        "semantic_analysis_documents": [],
        "temporal_event_indices": [],
        "timed_spine": {
            "structure": [
                {
                    "position": 1,
                    "block_type": "speech",
                    "clip_id": "clip_001",
                    "timeline_start": 0.0,
                    "timeline_end": 4.0,
                }
            ]
        },
        "broll_creative": [
            {
                "clip_id": "clip_002",
                "spine_block_position": 1,
                "preferred_moment": "the wide establishing shot",
                "selection_rationale": "illustrates the line about the shop",
            }
        ],
        "b_roll_interjections": [],
    }
    proc = _run_bridge(BROLL / "post_bridge.py", payload)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert len(result["b_roll_assignments"]) == 1


def test_plan_sfx_accepts_a_sparse_plan(sfx_library):
    payload = {
        "sfx_creative": [
            {
                "spine_block_position": 1,
                "sfx_id": "test_whoosh.wav",
                "volume_db": -18,
                "rationale": "marks the cut",
            }
        ],
        "timed_spine": {
            "structure": [
                {
                    "position": 1,
                    "block_type": "speech",
                    "clip_id": "clip_001",
                    "source_start": 0.0,
                    "source_end": 2.0,
                    "timeline_start": 0.0,
                    "timeline_end": 2.0,
                    "word_timestamps": [],
                    "alignment_method": "whisperx",
                }
            ]
        },
        "temporal_event_indices": [],
        "music_analysis": {},
        "music_selection": {"audio_path": ""},
        "project_fps": 30.0,
        "creative_direction": {},
    }
    proc = _run_bridge(SFX / "post_bridge.py", payload, sfx_library)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert len(result["sfx_spec"]["sfx_list"]) == 1
