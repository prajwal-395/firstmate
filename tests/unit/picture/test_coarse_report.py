"""The coarse answer is kept, so the escalation does not rank again.

Every test here names a defect.  A test that cannot fail is worse than
no test, because it reads as coverage (AGENTS.md 10.4).

The defect these name: the frame lane's coarse answer was printed and
gone, so a caller that asked for the cheap answer first and the
verified answer second paid for the ranking TWICE - and the cost of the
question was decided by how the caller split it into commands.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from library.tools import coarse_report
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
                         "frame_files": [f"frames/{clip_id}_0.0.jpg",
                                         f"frames/{clip_id}_10.0.jpg"]},
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


# ─── The store ────────────────────────────────────────────────────


def test_the_coarse_answer_is_kept_beside_the_index(index):
    """The escalation needs the coarse answer to still be there.

    The defect this names: a coarse answer that exists only in the
    call that produced it, so the next call has to rank again.
    """
    report = index.answer_at_tier("a laptop on a desk", tier="coarse",
                                  top_ranges=2)
    assert report["coarse_report"]["status"] == "stored"
    path = index.index_dir / coarse_report.STORE_DIRNAME
    assert path.is_dir()
    assert list(path.glob("*.json"))


def test_a_stored_coarse_answer_is_refused_when_the_index_has_moved_on(
        index, monkeypatch):
    """A ranking over frames that are no longer there is not this
    index's ranking.

    The defect this names: a stored coarse answer reused after the
    index was rebuilt, so the escalation judged a ranking of frames
    that are not on disk.
    """
    index.answer_at_tier("a laptop on a desk", tier="coarse", top_ranges=2)
    # The index changes under the store.
    path = index.index_dir / footage_frames.FRAME_INDEX_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["frames"] = payload["frames"][:1]
    path.write_text(json.dumps(payload), encoding="utf-8")
    policy = index.policy_for("a laptop on a desk", top_ranges=2)
    stored = coarse_report.load(index.index_dir, query="a laptop on a desk",
                                policy=policy)
    assert stored["status"] == "stale"
    assert stored["report"] is None
    assert "index has changed" in stored["reason"]


def test_a_stored_coarse_answer_under_another_policy_is_not_reused(index):
    """Three windows and five windows are two questions.

    The defect this names: a store keyed on the query alone, so a
    caller who asked for five windows was served the three it had
    already seen.
    """
    index.answer_at_tier("a laptop on a desk", tier="coarse", top_ranges=2)
    policy = index.policy_for("a laptop on a desk", top_ranges=5)
    stored = coarse_report.load(index.index_dir, query="a laptop on a desk",
                                policy=policy)
    assert stored["status"] == "absent"


# ─── The second call ──────────────────────────────────────────────


def test_the_second_call_does_not_rank_the_index_again(index, monkeypatch):
    """The whole point: the escalation does not pay for the ranking.

    The defect this names: a second call that ranks the index again
    to get the candidates the first call had already ranked.
    """
    index.answer_at_tier("a laptop on a desk", tier="coarse", top_ranges=2)

    def refuse(*args, **kwargs):
        raise AssertionError("the second call ranked the index again")

    monkeypatch.setattr(FrameIndex, "_encode_text", refuse)
    calls = []
    _stub_pass(monkeypatch, calls)
    report = index.answer_at_tier("a laptop on a desk", tier="detailed",
                                  top_ranges=2)
    assert calls, "the verification pass did not run"
    assert report["coarse_report"]["status"] == "reused"
    assert report["fidelity"]["tier"] == "detailed"


def test_the_second_call_answers_exactly_what_the_one_call_answers(
        index, monkeypatch):
    """The two paths must end on the SAME answer.

    The defect this names: an escalation that reaches a different
    answer from the one-call path - the same windows with different
    verdicts, or a different set of windows - which is a fidelity
    regression and not a refinement.
    """
    _stub_pass(monkeypatch, [])
    one_call = index.answer_at_tier("a laptop on a desk", tier="detailed",
                                    top_ranges=2)
    index.answer_at_tier("a laptop on a desk", tier="coarse", top_ranges=2)
    second = index.answer_at_tier("a laptop on a desk", tier="detailed",
                                  top_ranges=2)
    assert second["ranges"] == one_call["ranges"]
    assert second["fidelity"] == one_call["fidelity"]
    assert second["escalation"] == one_call["escalation"]


def test_both_paths_name_the_same_ranking(index, monkeypatch):
    """One ranking, one name.

    The defect this names: a reader comparing two answers - one made
    in one call, one made in two - unable to see that they came from
    the same ranking, because the name was computed over the windows
    a report happened to present.
    """
    _stub_pass(monkeypatch, [])
    one_call = index.answer_at_tier("a laptop on a desk", tier="detailed",
                                    top_ranges=2)
    coarse = index.answer_at_tier("a laptop on a desk", tier="coarse",
                                  top_ranges=2)
    second = index.answer_at_tier("a laptop on a desk", tier="detailed",
                                  top_ranges=2)
    assert (second["fidelity"]["derived_from"]["report_id"]
            == one_call["fidelity"]["derived_from"]["report_id"])
    assert coarse["coarse_report_id"] == second["coarse_report"]["report_id"]


def test_a_detailed_answer_does_not_carry_the_coarse_answers_escalation(
        index, monkeypatch):
    """The way up from a ranking is not the way up from a verdict.

    The defect this names: a detailed answer that still carries the
    coarse tier's escalation, so a reader who follows it is sent to
    verify an answer that is already verified.
    """
    _stub_pass(monkeypatch, [])
    index.answer_at_tier("a laptop on a desk", tier="coarse", top_ranges=2)
    detailed = index.answer_at_tier("a laptop on a desk", tier="detailed",
                                    top_ranges=2)
    assert detailed["escalation"]["from"] == "detailed"
    assert detailed["escalation"]["to"] == "original"
    assert detailed["escalation"]["request"]["lane"] == "original_media"


def test_a_caller_that_declines_the_store_pays_for_the_ranking_again(
        index, monkeypatch):
    """Declining the store is allowed, and it is said out loud.

    The defect this names: a caller that declined to keep the coarse
    answer served an escalation that silently re-ranked, with nothing
    in the answer to say so.
    """
    _stub_pass(monkeypatch, [])
    index.answer_at_tier("a laptop on a desk", tier="coarse", top_ranges=2,
                         store=False)
    second = index.answer_at_tier("a laptop on a desk", tier="detailed",
                                  top_ranges=2)
    assert second["coarse_report"]["status"] == "absent"
    assert second["fidelity"]["tier"] == "detailed"
