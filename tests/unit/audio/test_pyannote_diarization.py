import pytest

from library.tools.diarization_eval import diarization_metrics
from library.tools.pyannote_diarization import (
    PyannoteUnavailable,
    build_turns,
    require_access,
)


class _Segment:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class _Annotation:
    """Duck-typed pyannote Annotation: itertracks(yield_label=True)."""

    def __init__(self, rows):
        self._rows = rows

    def itertracks(self, yield_label=False):
        assert yield_label
        return iter(self._rows)


def _ann(*triples):
    return _Annotation([(_Segment(s, e), f"track-{i}", lab)
                        for i, (s, e, lab) in enumerate(triples)])


def test_overlap_stays_overlapping_turns_and_scores():
    turns = build_turns(_ann((0.0, 2.0, "A"), (1.0, 3.0, "B")))
    assert turns == [
        {"start": 0.0, "end": 2.0, "speaker": "A"},
        {"start": 1.0, "end": 3.0, "speaker": "B"},
    ]
    result = diarization_metrics(turns, turns, collar_seconds=0)
    assert result["der"] == 0.0


def test_contiguous_same_label_merges():
    turns = build_turns(_ann((0.0, 1.0, "A"), (1.0, 2.0, "A"), (3.0, 4.0, "A")))
    assert turns == [
        {"start": 0.0, "end": 2.0, "speaker": "A"},
        {"start": 3.0, "end": 4.0, "speaker": "A"},
    ]


def test_probabilities_are_stored_raw_and_uncertain_marked():
    turns = build_turns(
        _ann((0.0, 2.0, "A"), (2.0, 4.0, "B")),
        probabilities={"A": 0.61},
        uncertain_segments=[(2.0, 4.0)],
    )
    assert turns[0]["speaker_probabilities"] == {"A": 0.61}
    assert "uncertain" not in turns[0]
    assert turns[1]["uncertain"] is True
    result = diarization_metrics(
        [{"start": 0, "end": 4, "speaker": "r"}],
        [{"start": 0, "end": 4, "speaker": "x"}],
        collar_seconds=0,
    )
    assert result["status"] == "measured"


def test_bad_probability_and_interval_refuse():
    with pytest.raises(PyannoteUnavailable, match="0\\.\\.1"):
        build_turns(_ann((0.0, 1.0, "A")), probabilities={"A": 1.5})
    with pytest.raises(PyannoteUnavailable, match="invalid interval"):
        build_turns(_ann((2.0, 1.0, "A")))


def test_require_access_refuses_before_any_network(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(PyannoteUnavailable, match="HF_TOKEN"):
        require_access()
    assert require_access(token="hf_present_but_conditions_unaccepted") == \
        "hf_present_but_conditions_unaccepted"
