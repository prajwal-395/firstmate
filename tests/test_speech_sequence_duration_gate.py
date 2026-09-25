"""Step 2.02's duration verdict belongs to the script, not the model.

The handoff tells the model the sequence MUST fit the target and asks
it to pre-add passage durations - exact summation work. The
post-bridge computed the same total and printed a WARNING when it
missed, so an over-long sequence sailed through here and failed a
stage later at step 3.03's total-duration gate, where the only
recovery is a re-run. The gate is now HARD: `refuse_out_of_zone_sequence`
raises, `main` exits 1 with the numbers, and the post-bridge retry
path carries them back to the model that chose the passages - which is
the step that can still fix it by cutting.

Which passages to cut stays judgement: the refusal names the total
and the zone, never which passage goes.

The enrichment fixture is 001's own clip_011 data (see
`tests/test_aligner_leading_gap.py`): five passages enriching to
~20.8s of real word timings. Nothing here reaches a real project.
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


def _zone(target):
    return (target * 0.9, float(target), target * 1.1)


def _speech_zone(target):
    floor, target_f, _ceiling = _zone(target)
    return (floor, target_f, target_f)


# ── The total is measured off aligned words, not the hint ────────────

def test_the_total_is_read_off_the_aligned_timings(enriched):
    assert pb.total_speech_seconds(enriched["body_sequence"]) == pytest.approx(
        20.773, abs=0.01)


# ── The gate, both directions ─────────────────────────────────────────

def test_a_sequence_short_of_a_declared_target_is_refused(enriched):
    total = pb.total_speech_seconds(enriched["body_sequence"])
    with pytest.raises(pb.SpeechDurationError) as excinfo:
        pb.refuse_out_of_zone_sequence(total, _speech_zone(60), _zone(60))
    message = str(excinfo.value)
    assert f"{total:.1f}s" in message
    assert "54.0-66.0s" in message


def test_no_declared_target_is_unchecked_not_judged(enriched):
    total = pb.total_speech_seconds(enriched["body_sequence"])
    assert pb.refuse_out_of_zone_sequence(total, None) is None


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
