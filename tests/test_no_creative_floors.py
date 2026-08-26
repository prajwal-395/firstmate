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

The ruling is about creative floors, not about B-roll and SFX
specifically, and two prompt-level quotas survived it because this file
only guarded the two steps the ruling was WRITTEN about:

* `step_4_03_plan_vfx/handoff.md` demanded at least three to seven VFX
  items and a slow zoom on every talking-head clip over three seconds.
  001 produced exactly eight effects on exactly eight clips, one each,
  alternating direction.
* `step_2_02_speech_sequence/handoff.md` demanded "strictly select
  exactly 10-15" body passages, contradicting the 75%-of-target-duration
  rule in the same file. The model obeyed the duration and produced 7 -
  it was right, and the prompt was wrong.

Both are gone, and every creative-planning prompt is now under the guard
rather than the two that were named on the day.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEPS = REPO / "library" / "steps"
BROLL = STEPS / "step_3_02_select_broll"
SFX = STEPS / "step_4_04_plan_sfx"
VFX = STEPS / "step_4_03_plan_vfx"
SPEECH = STEPS / "step_2_02_speech_sequence"

# Every step whose prompt asks a model HOW MANY of something to plan.
# Add a creative-planning step here when you add one; the ruling is about
# floors, not about the two steps it was written about.
CREATIVE_PLANNING_STEPS = (BROLL, SFX, VFX, SPEECH)


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
    # The two that survived the ruling until 2026-08-25.
    "must plan at least",
    "strictly select exactly",
    "an empty list is a failure",
]

# A quota does not have to be phrased as one. "at least N", "N-M items"
# and "every clip MUST have" all set a floor, so the guard also refuses a
# bare numeric range next to a plural noun and a per-clip MUST.
QUOTA_PATTERNS = [
    # "at least 3", "at least three"
    r"at least\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    # "3-7 VFX items", "10-15 passages"
    r"\b\d+\s*-\s*\d+\s+(?:vfx|sfx|b-roll|passages|items|effects|cutaways|sounds)\b",
    # "every talking head clip >3s MUST have at least a slow zoom".
    # Deliberately narrower than a bare "every ... must have": select_broll
    # says every non-speech block must have B-roll, and that is a COVERAGE
    # requirement, not a floor - an uncovered block is a black hole that
    # compile_manifest._assert_timeline_fully_covered fails on.
    r"every\b[^.\n]{0,80}\bmust\s+have\s+at\s+least\b",
]


@pytest.mark.parametrize(
    "path",
    [step / name
     for step in CREATIVE_PLANNING_STEPS
     for name in ("handoff.md", "manifest.json")],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_prompt_surfaces_demand_no_count(path):
    if not path.exists():
        pytest.skip(f"{path.name} does not exist for this step")
    text = path.read_text(encoding="utf-8").lower()
    hits = [phrase for phrase in QUOTA_PHRASES if phrase in text]
    hits += [m.group(0) for pattern in QUOTA_PATTERNS
             for m in re.finditer(pattern, text)]
    assert not hits, (
        f"{path.relative_to(REPO)} demands a count again ({hits}). The "
        f"floors were removed by ruling; a prompt-level quota "
        f"reintroduces exactly the padding they caused."
    )


def test_the_guard_can_actually_fire():
    """A gate that cannot fail reads as coverage. These are the literal
    lines removed on 2026-08-25."""
    removed = [
        "you must plan at least 3-7 vfx items across the video. an empty "
        "list is a failure.",
        "target body passages | 10-15 (strictly select exactly 10-15 of "
        "the strongest passages)",
        "every a-roll talking head clip >3 seconds must have at least "
        "`slow_zoom_in` or `slow_zoom_out`",
    ]
    for line in removed:
        hits = [p for p in QUOTA_PHRASES if p in line]
        hits += [m.group(0) for pattern in QUOTA_PATTERNS
                 for m in re.finditer(pattern, line)]
        assert hits, f"the guard does not catch {line!r}"


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


# ── The VFX post-bridge must not pad the plan ─────────────────────────
#
# The third floor, and the one that outlived the ruling by hiding in code
# rather than in a prompt. `inject_default_ken_burns` added a
# `slow_zoom_in`/`slow_zoom_out` to every speech block over three seconds
# that the plan had deliberately left alone, "because the style spec
# requires subtle motion on all A-roll clips >3s", and a second guard
# failed the step outright when the plan was empty. Observed on the run of
# 2026-08-26: a three-effect plan came out of the bridge with seven, three
# of them on blocks the spine had marked "no effect", and the resulting
# manifest failed P7 for putting one zoom family on every V1 clip.


def _vfx_payload(positions, n_blocks=4):
    spine = {
        "structure": [
            {
                "position": i + 1,
                "block_type": "speech",
                "clip_id": "clip_001",
                "source_start": i * 5.0,
                "source_end": i * 5.0 + 5.0,
                "timeline_start": i * 5.0,
                "timeline_end": i * 5.0 + 5.0,
                "word_timestamps": [],
                "alignment_method": "whisperx",
            }
            for i in range(n_blocks)
        ]
    }
    return {
        "vfx_creative": [
            {
                "target_block_position": pos,
                "effect_type": "slow_zoom_in",
                "intensity": "subtle",
                "rationale": "the shot is held long enough to go dead",
            }
            for pos in positions
        ],
        "a_roll_assignments": [],
        "timed_spine": spine,
        "frame_rate": 30.0,
        "creative_direction": {},
    }


def test_plan_vfx_leaves_the_blocks_the_plan_left_alone():
    """One effect on four eligible blocks stays one effect."""
    proc = _run_bridge(VFX / "post_bridge.py", _vfx_payload([1]))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    effects = json.loads(proc.stdout)["enhancement_spec"]["visual_effects"]
    assert [e["target_block_position"] for e in effects] == [1], (
        f"the bridge padded the plan back up - a floor is back: {effects}"
    )
    combined = (proc.stdout + proc.stderr).lower()
    for word in ("ken burns", "default", "requires subtle"):
        assert word not in combined, (
            f"the bridge still talks about a default ({word!r})"
        )


def test_plan_vfx_accepts_an_empty_plan():
    """The handoff says so in as many words: 'an empty list is a
    legitimate answer for a piece that wants stillness'. The bridge used
    to exit 1 with 'You MUST plan at least 3-7 VFX items'."""
    proc = _run_bridge(VFX / "post_bridge.py", _vfx_payload([]))
    assert proc.returncode == 0, (
        "an empty VFX plan was rejected - a floor is back.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert json.loads(proc.stdout)["enhancement_spec"]["visual_effects"] == []
