"""Progressive fidelity in the frame lane: the coarse answer, and the
way to the verified one.

Every test here names a defect.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from library.tools.analysis import footage_frames
from library.tools.analysis.footage_frames import FrameIndex


def _index(tmp_path, monkeypatch, frames, matrix, vector):
    """A frame index written by hand: no model, no ffmpeg, no project."""
    index_dir = tmp_path / "findex"
    index_dir.mkdir()
    (index_dir / footage_frames.FRAME_INDEX_FILE).write_text(
        json.dumps({"clip_model": "test-stub", "embed_backend": "test-stub",
                    "sampling": "uniform", "step_s": 10.0, "frames": frames}),
        encoding="utf-8")
    np.save(index_dir / footage_frames.FRAME_EMBEDDINGS_FILE, matrix)
    monkeypatch.setattr(FrameIndex, "_encode_text",
                        lambda self, query: vector)
    return FrameIndex(tmp_path / "proj", index_dir=index_dir)


@pytest.fixture
def index(tmp_path, monkeypatch):
    frames = [
        {"file": "frames/frame_000001.jpg", "clip_id": "clip_001",
         "filename": "A.MXF", "t": 0.0},
        {"file": "frames/frame_000002.jpg", "clip_id": "clip_001",
         "filename": "A.MXF", "t": 10.0},
        {"file": "frames/frame_000003.jpg", "clip_id": "clip_002",
         "filename": "B.MXF", "t": 5.0},
    ]
    matrix = np.array([[1.0, 0.0], [0.9, 0.1], [0.8, 0.2]], dtype="float32")
    vector = np.array([1.0, 0.0], dtype="float32")
    # The verification pass reads the source memory's own frame sample.
    # A hand-written index has none, so the pass is given one: without
    # it the pass reports "unavailable" and the tests below would
    # assert against an answer that was never verified.
    monkeypatch.setattr(
        FrameIndex, "_source_frames",
        lambda self, clip_ids: {
            clip_id: [{"t": t, "file": f"frames/{clip_id}_{t}.jpg"}
                      for t in (0.0, 10.0)]
            for clip_id in clip_ids})
    return _index(tmp_path, monkeypatch, frames, matrix, vector)


def _verified_window(clip_id="clip_001", start=0.0, end=10.0):
    return {"clip_id": clip_id, "filename": "A.MXF", "start": start,
            "end": end, "duration": round(end - start, 3),
            "timecode": "00:00.000-00:10.000", "best_score": 1.0,
            "n_frames": 2, "verified": True,
            "original": {"media": "A.MXF",
                         "frame_files": ["frames/clip_001_0.0.jpg",
                                         "frames/clip_001_10.0.jpg"]},
            "justification": {"verdict": "yes", "reason": "the lid is up",
                              "frames_shown": [0.0, 10.0],
                              "statement": "a laptop on a desk"}}


def _stub_pass(monkeypatch, calls):
    """A verification pass that answers without a model."""
    from library.tools.retrieval import temporal_rerank

    def fake_rerank(**kwargs):
        calls.append(kwargs)
        return {"ranges": [_verified_window()], "candidates": [],
                "status": "ran",
                "cost": {"calls": 1, "cached_calls": 0, "candidates": 1,
                         "verified": 1, "rejected": 0, "seconds": 1.0,
                         "seconds_per_call": 1.0, "wall_seconds": 1.0},
                "classification": temporal_rerank.classify(
                    kwargs.get("query", ""))}

    monkeypatch.setattr(temporal_rerank, "rerank", fake_rerank)


def test_the_coarse_answer_says_which_resolution_answered_it(index):
    """A ranking that does not say it is a ranking reads as a finding.

    The defect this names: an answer that carries its candidates and
    nothing else, so a caller cannot tell a place to look from a place
    that was verified.
    """
    report = index.search_ranges("a laptop on a desk", top_ranges=2)
    assert report["fidelity"]["tier"] == "coarse"
    assert report["fidelity"]["establishes"] == "where to look"
    assert report["fidelity"]["lane"] == "visual_frame_index"
    assert report["fidelity"]["representation"]["frames"] == 3
    assert report["fidelity"]["coverage"]["ran"] is True
    assert "does_not_establish" in report["fidelity"]


def test_the_coarse_answer_carries_the_escalation_and_its_measured_cost(index):
    """The way to the verified answer is part of the answer.

    The defect this names: a caller that is told the candidates are
    unverified and is given no way to verify them, so it either
    believes them or gives up.
    """
    report = index.search_ranges("a laptop on a desk", top_ranges=2)
    escalation = report["escalation"]
    assert escalation["from"] == "coarse"
    assert escalation["to"] == "detailed"
    assert escalation["request"]["query"] == "a laptop on a desk"
    assert escalation["request"]["candidates_at_most"] >= 1
    assert escalation["cost_seconds_estimate"] > 0
    assert escalation["cost_basis"]


def test_the_escalation_does_not_rank_the_index_again(index, monkeypatch):
    """The escalation reuses the coarse answer's candidates.

    The defect this names: the escalation re-running the ranking, so
    the caller pays for the coarse tier twice - once for the answer it
    already has and once for the answer it asked for.
    """
    coarse = index.search_ranges("a laptop on a desk", top_ranges=2)

    def refuse(*args, **kwargs):
        raise AssertionError("the escalation re-ranked the index")

    monkeypatch.setattr(FrameIndex, "_encode_text", refuse)
    calls = []
    _stub_pass(monkeypatch, calls)
    detailed = index.verify_ranges("a laptop on a desk", coarse)
    assert calls, "the verification pass did not run"
    assert detailed["fidelity"]["tier"] == "detailed"


def test_the_detailed_answer_names_the_coarse_answer_it_came_from(
        index, monkeypatch):
    """Provenance travels down the ladder.

    The defect this names: a verified answer that cannot be walked back
    to the ranking that proposed the candidate, so a reader who doubts
    the verdict cannot see what the verdict was about.
    """
    coarse = index.search_ranges("a laptop on a desk", top_ranges=2)
    coarse_id = coarse["fidelity"] and index._report_id(coarse)
    _stub_pass(monkeypatch, [])
    detailed = index.verify_ranges("a laptop on a desk", coarse)
    derived = detailed["fidelity"]["derived_from"]
    assert derived["tier"] == "coarse"
    assert derived["report_id"] == coarse_id


def test_the_escalation_judges_the_same_candidates_the_one_call_path_judges(
        index, monkeypatch):
    """The escalation must not NARROW the pool it verifies.

    The defect this names: the coarse answer keeps the windows it
    PRESENTED and drops the candidates it did not, so the escalation
    verifies a smaller set than the one-call path did - fewer calls, a
    different answer, and a cheaper answer that is not the same answer.
    """
    calls = []
    _stub_pass(monkeypatch, calls)
    index.search_ranges("a laptop on a desk", top_ranges=2, temporal=True)
    one_call = calls[0]["ranges"]
    calls.clear()
    coarse = index.search_ranges("a laptop on a desk", top_ranges=2)
    index.verify_ranges("a laptop on a desk", coarse)
    assert calls[0]["ranges"] == one_call


def test_the_escalated_answer_is_the_answer_the_verified_tier_gives(
        index, monkeypatch):
    """The two paths must end on the SAME answer.

    The defect this names: the escalation reaching a different answer
    from the verified tier - the same windows with different verdicts,
    or a different set of windows - which is a fidelity regression and
    not a refinement.
    """
    _stub_pass(monkeypatch, [])
    one_step = index.search_ranges("a laptop on a desk", top_ranges=2,
                                   temporal=True)
    coarse = index.search_ranges("a laptop on a desk", top_ranges=2)
    escalated = index.verify_ranges("a laptop on a desk", coarse)
    assert escalated["ranges"] == one_step["ranges"]
    assert escalated["ranges"][0]["verified"] is True, (
        "the compared answers are the VERIFIED ones: a test that compared "
        "two unverified answers would pass on a broken escalation")
    assert escalated["fidelity"]["tier"] == one_step["fidelity"]["tier"]


def test_escalating_a_verified_answer_changes_nothing(index, monkeypatch):
    """Twice is not twice the work.

    The defect this names: an escalation that verifies the same
    candidates a second time, because the caller asked for the
    detailed tier on an answer that already was the detailed tier.
    """
    _stub_pass(monkeypatch, [])
    detailed = index.search_ranges("a laptop on a desk", top_ranges=2,
                                   temporal=True)
    calls = []
    _stub_pass(monkeypatch, calls)
    again = index.verify_ranges("a laptop on a desk", detailed)
    assert calls == [], "a verified answer was verified a second time"
    assert again["ranges"] == detailed["ranges"]


def test_a_candidate_points_at_the_original_media(index):
    """The answer reaches the pixels it is about.

    The defect this names: an answer that names a camera and a second
    and leaves the reader to find the footage, so the ORIGINAL tier of
    the ladder is a description instead of a pointer.
    """
    report = index.search_ranges("a laptop on a desk", top_ranges=2)
    window = report["ranges"][0]
    assert window["original"]["media"] == "A.MXF"
    assert window["original"]["frame_file"]


def test_a_verified_window_points_at_the_frames_the_model_saw(index,
                                                              monkeypatch):
    """The verdict's own evidence, in the original media.

    The defect this names: a verified window that carries the verdict
    but not the frames the verdict was about, so a reader who doubts
    it cannot look at what the model looked at.
    """
    _stub_pass(monkeypatch, [])
    coarse = index.search_ranges("a laptop on a desk", top_ranges=2)
    detailed = index.verify_ranges("a laptop on a desk", coarse)
    window = detailed["ranges"][0]
    assert window["original"]["media"] == "A.MXF"
    assert window["original"]["frame_files"]


def test_a_refused_query_is_not_presented_as_a_coarse_answer(index):
    """A refusal measured nothing, and says so.

    The defect this names: a refused query reported as a coarse tier
    answer with an empty candidate list, which reads as "the footage
    does not show it".
    """
    report = index.search_ranges("covering the mouth", top_ranges=2)
    assert report["refused"] is not None
    assert report["fidelity"]["coverage"]["ran"] is False
    assert report["fidelity"]["tier"] == "coarse"


def test_asking_for_two_resolutions_at_once_is_refused():
    """The coarse tier and the verified tier are two different answers.

    The defect this names: `--no-temporal` and `--fidelity detailed`
    read as two preferences and the lane picking one of them, so the
    caller is answered at a resolution it did not ask for.
    """
    from library.tools.analysis import footage_query
    from library.tools.ren_refusal import RenRefusal

    with pytest.raises(RenRefusal):
        footage_query.main(["search", "/nowhere", "a laptop on a desk",
                            "--visual", "--no-temporal", "--fidelity",
                            "detailed"])
