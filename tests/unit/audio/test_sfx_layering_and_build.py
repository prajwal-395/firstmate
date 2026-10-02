"""Step 4.04's post-bridge places what the plan asked for, and refuses what it cannot read.

Layering is legal, a sound's measured envelope keys its placement, a
`role: "layer"` plays under speech, and a plan key or role nothing reads
is refused or dropped by name. History: docs/evidence/sfx.md.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.steps.step_4_04_plan_sfx.post_bridge import (  # noqa: E402
    UnplayableSfxPlan,
    resolve_sfx,
)
from library.tools import sfx_envelope as se  # noqa: E402
from library.tools.plan_keys import UnreadPlanKey  # noqa: E402

SFX = REPO / "library" / "steps" / "step_4_04_plan_sfx"

RISER = "test_riser.wav"
IMPACT = "test_impact.wav"
DRONE = "test_drone.wav"


@pytest.fixture
def sfx_library(tmp_path):
    """A riser, an impact and a drone - never the captain's."""
    lib = tmp_path / "sfx_library"
    lib.mkdir()
    entries = []
    for name, duration, shape in ((RISER, 4.0, "swelling"),
                                  (IMPACT, 0.3, "punchy"),
                                  (DRONE, 8.0, "sustained")):
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


def _payload(plan, blocks=None, peaks=(1.5,), onsets=(0.2,)):
    return {
        "sfx_creative": plan,
        "timed_spine": {"structure": blocks or [_block(1, 0, 6),
                                                _block(2, 6, 12)]},
        "temporal_event_indices": [{
            "clip_id": "clip_001",
            "onset_times": list(onsets),
            "energy_curve": {"peak_times": list(peaks)},
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
    return json.loads(proc.stdout)["sfx_spec"]["sfx_list"], proc.stderr


# ── Layering and the envelope ─────────────────────────────────────────

def test_two_sounds_on_one_block_survive_as_two_placements(sfx_library):
    """Layering is legal, and the collapse check does not catch it: a
    collapse is ONE distinct position across the whole plan."""
    placed, _ = _run(_payload([
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


def test_the_envelope_decides_which_end_is_anchored(sfx_library):
    """A swelling sound ENDS on the peak - that is what a build is; a
    punchy one STARTS on the transient (the 0.2 s onset)."""
    for sfx_id, peak, envelope, timeline_in, timeline_out in (
            (RISER, 5.0, "swelling", 1.0, 5.0),
            (IMPACT, 1.5, "punchy", 0.2, None)):
        placed, _ = _run(_payload([
            {"spine_block_position": 1, "sfx_id": sfx_id, "volume_db": -14,
             "rationale": "on the moment"},
        ], peaks=(peak,)), sfx_library)
        entry = placed[0]
        assert entry["sfx_envelope"] == envelope
        assert entry["timeline_in"] == pytest.approx(timeline_in, abs=0.05)
        if timeline_out is not None:
            assert entry["timeline_out"] == pytest.approx(
                timeline_out, abs=0.05)


def test_the_placement_code_and_the_prompt_read_the_same_table():
    """The sentence the manifest records and the one the planner reads
    are rendered from one table, so they cannot drift."""
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _describe_placement,
    )
    for envelope in se.ENVELOPES:
        assert _describe_placement(envelope.shape) == envelope.engine_does
        assert envelope.engine_does in se.envelope_legend()[envelope.shape]
    # An unmeasured shape is an absence, not a fifth row.
    assert _describe_placement("") == se.UNMEASURED_PLACEMENT
    assert se.UNMEASURED not in se.ENVELOPES_BY_SHAPE


# ── Atmospheric layers (captain's ruling 2026-09-08) ──────────────────

def test_layer_plays_under_speech_instead_of_being_shifted_off_it(
        sfx_library):
    """A literal sound is moved into the word gap at 2.0; a reasoned
    layer stays at the envelope-placed position, under the words."""
    words = [round(0.1 * i, 3) for i in range(1, 21)]
    placed, _ = _run(_payload([
        {"spine_block_position": 1, "sfx_id": IMPACT, "volume_db": -8,
         "rationale": "a hit on the opening"},
        {"spine_block_position": 1, "sfx_id": IMPACT, "volume_db": -8,
         "role": "layer",
         "rationale": "a textural tick under the opening words"},
    ], [_block(1, 0, 12, words)], peaks=(), onsets=()), sfx_library)
    assert len(placed) == 2
    literal = [s for s in placed if s.get("role") != "layer"]
    layer = [s for s in placed if s.get("role") == "layer"]
    assert len(literal) == 1 and len(layer) == 1
    assert literal[0]["timeline_in"] == 2.0
    assert layer[0]["timeline_in"] == 0.0
    assert layer[0]["duration_seconds"] == 0.3


def test_layer_may_span_past_its_own_block(sfx_library):
    """An 8s drone placed on a 4s block plays all 8s."""
    placed, _ = _run(_payload([
        {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -20,
         "role": "layer",
         "rationale": "a bed that carries across the cut"},
    ], [_block(1, 0, 4), _block(2, 4, 8)], peaks=(), onsets=()),
        sfx_library)
    assert len(placed) == 1
    assert placed[0]["timeline_in"] == 0.0
    assert placed[0]["timeline_out"] == 8.0


def test_an_unreasoned_or_unread_role_is_dropped_by_name(sfx_library):
    """A layer is tied to no visible event, so without a reason it goes;
    a role nothing reads would ship a placement nobody asked for."""
    for entry, said in (
            ({"role": "layer"}, "states no reason"),
            ({"role": "sting", "rationale": "a sting on the cut"}, "sting")):
        placed, stderr = _run(_payload([
            {"spine_block_position": 1, "sfx_id": DRONE, "volume_db": -20,
             **entry},
        ], [_block(1, 0, 12)], peaks=(), onsets=()), sfx_library)
        assert placed == []
        assert said in stderr


# ── Unread plan keys ──────────────────────────────────────────────────

def test_an_unread_plan_key_refuses_loudly():
    """Measured defect: `at_word` was read by nothing and the whoosh
    landed 3.06 s early on the block start."""
    plan = [{
        "sfx_id": "whoosh_impact",
        "spine_block_position": 3,
        "volume_db": -10.0,
        "rationale": "marks the cut",
        "at_word": "watch",
    }]
    with pytest.raises(UnreadPlanKey) as excinfo:
        resolve_sfx(plan, {}, [], {}, {}, 30.0, {}, {}, catalog=[])
    message = str(excinfo.value)
    assert "at_word" in message
    assert "sfx_creative" in message
    for key in ("sfx_id", "spine_block_position", "volume_db"):
        assert key in message


def test_legacy_position_spellings_pass_the_key_gate():
    plan = [{
        "sfx_id": "no-such-sound",
        "target_block_position": 2,
        "timeline_start": 1.5,
        "volume_db": -10.0,
        "rationale": "marks the cut",
    }]
    # Past the key gate: it fails later, on the unplayable id.
    with pytest.raises(UnplayableSfxPlan):
        resolve_sfx(plan, {"structure": []}, [], {}, {}, 30.0, {},
                    {}, catalog=[])
