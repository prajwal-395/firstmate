"""The two creative floors are gone, and must stay gone.

Captain's ruling 2026-08-20 (`decision-creative-floors.md`): remove the
B-roll minimum and the SFX minimum entirely - not warnings, not a
reconciled range, not per-template minimums. The creative direction
decides how many B-roll cuts and how many sound effects a piece gets;
nothing is padded to satisfy a number. The accepted consequence, in the
captain's own words, is that a thin edit will no longer be caught by a
mechanical check.

These tests fail if either floor comes back, under any name, as a
rejection OR as a warning - and if the prompts start demanding a count
again, because a prompt-level quota pads the edit just as effectively as
a bridge-level one. That is what put two B-roll cuts and one sound effect
into the shipped project 001 with rationales that said so.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BROLL = REPO / "library" / "steps" / "step_3_02_select_broll"
SFX = REPO / "library" / "steps" / "step_4_04_plan_sfx"


def _run_bridge(script: Path, payload: dict):
    """Run a bridge the way the runner does: repo root on the path."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO),
        env=env,
    )


# ── The prompts must not demand a count ───────────────────────────────

QUOTA_PHRASES = [
    "must plan exactly 5",
    "must plan exactly 5-15",
    "must plan exactly 5-10",
    "must select 5",
    "must plan between",
    "exactly 5-10 sfx",
    "5-15 b-roll",
    "default 5-10 sfx",
]


@pytest.mark.parametrize(
    "path",
    [
        BROLL / "handoff.md",
        BROLL / "manifest.json",
        SFX / "handoff.md",
        SFX / "manifest.json",
    ],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_prompt_surfaces_demand_no_count(path):
    text = path.read_text(encoding="utf-8").lower()
    hits = [phrase for phrase in QUOTA_PHRASES if phrase in text]
    assert not hits, (
        f"{path.relative_to(REPO)} demands a B-roll/SFX count again "
        f"({hits}). The floors were removed by ruling; a prompt-level "
        f"quota reintroduces the padding they caused."
    )


# ── The bridges must not reject a sparse plan ─────────────────────────


def test_select_broll_accepts_a_single_cutaway():
    """One B-roll clip is a legitimate edit, not an error."""
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
    assert proc.returncode == 0, (
        f"one B-roll clip was rejected - a floor is back.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    result = json.loads(proc.stdout)
    assert len(result["b_roll_assignments"]) == 1
    combined = (proc.stdout + proc.stderr).lower()
    for word in ("minimum", "at least", "you must select"):
        assert word not in combined, (
            f"the bridge warns about count ({word!r}). The ruling declined "
            f"a warning as well as a rejection."
        )


def _sfx_payload(n_sfx: int):
    spine = {
        "structure": [
            {
                "position": i + 1,
                "block_type": "speech",
                "clip_id": "clip_001",
                "source_start": i * 2.0,
                "source_end": i * 2.0 + 2.0,
                "timeline_start": i * 2.0,
                "timeline_end": i * 2.0 + 2.0,
                "word_timestamps": [],
                "alignment_method": "whisperx",
            }
            for i in range(max(n_sfx, 1))
        ]
    }
    return {
        "sfx_creative": [
            {
                "spine_block_position": i + 1,
                "sfx_type": "whoosh",
                "volume_level": "subtle",
                "rationale": "marks the cut",
            }
            for i in range(n_sfx)
        ],
        "timed_spine": spine,
        "temporal_event_indices": [],
        "music_analysis": {},
        "project_fps": 30.0,
        "creative_direction": {},
        "available_sfx_types": ["whoosh"],
    }


@pytest.mark.parametrize("n_sfx", [1, 2])
def test_plan_sfx_accepts_a_sparse_plan(n_sfx):
    """A one- or two-sound edit passes; there is no minimum."""
    proc = _run_bridge(SFX / "post_bridge.py", _sfx_payload(n_sfx))
    assert proc.returncode == 0, (
        f"{n_sfx} SFX were rejected - a floor is back.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    result = json.loads(proc.stdout)
    assert len(result["sfx_spec"]["sfx_list"]) == n_sfx
    combined = (proc.stdout + proc.stderr).lower()
    for word in ("minimum", "at least", "you must plan"):
        assert word not in combined, (
            f"the bridge warns about count ({word!r}). The ruling declined "
            f"a warning as well as a rejection."
        )


def test_sfx_collapse_is_still_caught():
    """Removing the floor must not remove the collapse check.

    Every SFX landing on one timeline position is a broken plan, not a
    sparse one, and the ruling says nothing about it.
    """
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _assert_sfx_distributed,
    )

    with pytest.raises(ValueError, match="distinct timeline position"):
        _assert_sfx_distributed(
            [
                {"timeline_in": 0.0},
                {"timeline_in": 0.0},
            ]
        )
