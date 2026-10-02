"""Step 2.02's duration verdict belongs to the script, not the model.

The gate is HARD: `refuse_out_of_zone_sequence` raises and `main` exits 1
with the numbers, so the post-bridge retry carries them back to the
model that chose the passages - the step that can still cut. Speech owns
the lower half of the zone (its ceiling is the declared TARGET, not the
zone max); the band above is the room step 2.05's breaths, intro/outro
and music/picture blocks extend into. Each refusal names its own fix (too
long cuts, too short extends) and never which passage goes. Findings 9
and 30 (execution-frontier report 2026-09-24): an over-long sequence
once passed here and died a stage later at 3.03/2.05, and the too-short
message said "fewer or shorter".

The enrichment fixture is 001's own clip_011 data: five passages
enriching to ~20.8s of real word timings. Nothing reaches a real project.
"""

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import library.steps.step_2_02_speech_sequence.post_bridge as pb  # noqa: E402
from library.tools.duration_targets import (  # noqa: E402
    get_speech_duration_zone,
    get_target_duration_zone,
)

FIXTURE = (REPO_ROOT / "tests" / "fixtures" / "captured_run"
           / "clip_011_speech_regions.json")
ORDER = ["body_0", "body_1", "body_2", "body_3", "body_9"]


@pytest.fixture(scope="module")
def clip_011():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def index_dir(tmp_path, clip_011):
    d = tmp_path / "temporal_index"
    d.mkdir()
    (d / "clip_011.json").write_text(
        json.dumps({"speech_regions": clip_011["speech_regions"]}),
        encoding="utf-8",
    )
    return str(d)


@pytest.fixture
def enriched(clip_011, index_dir):
    seq = {
        "body_sequence": [
            dict(clip_011["passages"][k], position=i)
            for i, k in enumerate(ORDER, start=1)
        ],
    }
    return pb.enrich_speech_sequence(seq, index_dir)


# ── The total is measured off aligned words, not the hint ────────────

def test_the_total_is_read_off_the_aligned_timings(enriched):
    assert pb.total_speech_seconds(enriched["body_sequence"]) == pytest.approx(
        20.773, abs=0.01)


# ── The gate, both directions ─────────────────────────────────────────

def test_the_speech_gate_refuses_each_direction_with_its_own_fix():
    """(speech seconds, target, refusal fragments, forbidden fragment).
    30.3 s at a 30 s target is the B6 shape that passed the old full-zone
    gate; 28 s leaves the 30-33 s band for what extends the total."""
    rows = [
        (30.3, 30, ["30.3s", "fewer or shorter", "30.0-33.0s"], None),
        (28.0, 30, None, None),
        (40.1, 60, ["40.1s", "MORE or LONGER", "54.0-66.0s"],
         "fewer or shorter"),
    ]
    for seconds, target, fragments, forbidden in rows:
        data = {"project_config": {"target_duration_seconds": target}}
        args = (seconds, get_speech_duration_zone(data),
                get_target_duration_zone(data))
        if fragments is None:
            assert pb.refuse_out_of_zone_sequence(*args) is None
            continue
        with pytest.raises(pb.SpeechDurationError) as excinfo:
            pb.refuse_out_of_zone_sequence(*args)
        message = str(excinfo.value)
        for fragment in fragments:
            assert fragment in message, (seconds, message)
        if forbidden:
            assert forbidden not in message
    # No declared target is unchecked, not judged.
    assert pb.refuse_out_of_zone_sequence(30.3, None) is None


# ── The gate is wired into the step's entry point, not defined beside it

def _payload(clip_011, index_dir, target):
    data = {
        "speech_sequence": {
            "body_sequence": [
                dict(clip_011["passages"][k], position=i)
                for i, k in enumerate(ORDER, start=1)
            ],
        },
        "temporal_index": {"index_dir": index_dir},
    }
    if target is not None:
        data["project_config"] = {"target_duration_seconds": target}
    return json.dumps(data)


def test_main_refuses_speech_over_target_inside_the_total_max(
        clip_011, index_dir, capsys, monkeypatch):
    """2.02 must leave the upper band for non-speech the spine adds.

    The aligned speech is about 20.8 s. At a 19 s target it fits inside
    the total zone's 20.9 s maximum, but exceeds the speech ceiling.
    The old full-zone check let it through, leaving no room for breaths.
    """
    monkeypatch.setattr(sys, "stdin",
                        io.StringIO(_payload(clip_011, index_dir, 19)))
    with pytest.raises(SystemExit) as excinfo:
        pb.main()
    assert excinfo.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["step"] == "2.02_bridge"
    assert "above the speech ceiling" in out["error"]
