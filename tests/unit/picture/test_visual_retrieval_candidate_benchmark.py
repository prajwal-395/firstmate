"""Metric math for the report-only visual encoder bakeoff."""

from types import SimpleNamespace

import numpy as np

from library.tools.visual_retrieval_candidate_benchmark import (
    _clip_groups,
    _mrl_truncate,
    _query_metrics,
)


def test_recall_precision_and_ambiguous_frames_are_scored_at_k():
    frames = [
        {"clip_id": "clip-a", "t": 10.0},
        {"clip_id": "clip-a", "t": 20.0},
        {"clip_id": "clip-b", "t": 30.0},
    ]
    corpus = SimpleNamespace(name_of_clip={"clip-a": "A", "clip-b": "B"})
    query = {
        "id": "Q1",
        "query": "a person raises a hand",
        "positive_frames": {"A": [10.0], "B": [30.0]},
        "ambiguous_frames": {"B": [30.0]},
    }

    result = _query_metrics(query, np.array([0.3, 0.2, 0.1]), frames, corpus, 0.0)

    assert result["positive_frame_count"] == 1
    assert result["recall_at_1"] == 1.0
    assert result["recall_at_5"] == 1.0
    assert result["precision_at_1"] == 1.0
    assert result["precision_at_5"] == 0.2
    assert result["negative"] is False
    assert result["temporal_recall_complete_event"] is None


def test_negative_query_is_not_mistaken_for_positive_recall():
    frames = [{"clip_id": "clip-a", "t": 1.0}]
    corpus = SimpleNamespace(name_of_clip={"clip-a": "A"})

    result = _query_metrics(
        {"id": "N1", "query": "a dog on the couch", "positive_frames": {}},
        np.array([0.9]),
        frames,
        corpus,
        0.0,
    )

    assert result["negative"] is True
    assert result["positive_frame_count"] == 0
    assert result["recall_at_1"] is None
    assert result["precision_at_1"] == 0.0


def test_clip_groups_partition_consecutive_frames_without_overlap():
    frames = [{"t": float(t)} for t in range(7)]

    groups = _clip_groups(frames, 3)

    assert groups == [[0, 1, 2], [3, 4, 5], [6]]
    assert sorted(i for group in groups for i in group) == list(range(7))


def test_mrl_truncate_keeps_prefix_and_renormalizes():
    matrix = np.array([[3.0, 4.0, 12.0, 5.0], [1.0, 0.0, 0.0, 0.0]])

    truncated = _mrl_truncate(matrix, 2)

    assert truncated.shape == (2, 2)
    assert truncated.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(truncated, axis=1), np.ones(2), rtol=1e-5)
    np.testing.assert_allclose(truncated[0], [0.6, 0.8], rtol=1e-5)
