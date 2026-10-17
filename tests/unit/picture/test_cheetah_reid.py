"""cheetah_reid research adapter: refusal, stats equivalence, sampling parity.

No footage, no model, no download: the stats equivalence test pins that
`cheetah_reid.pair_stats` (which runs in the scratch interpreter without
the library) computes the same FAR/FRR as `face_identity_study.pair_report`.
"""

import math
import random

import pytest

from library.tools import cheetah_reid, face_identity_study, person_entity


def _synthetic(n_people=3, n_each=6, dim=16, seed=7):
    rng = random.Random(seed)
    items = []
    for person in range(n_people):
        center = [rng.gauss(0, 1) for _ in range(dim)]
        norm = math.sqrt(sum(v * v for v in center))
        center = [v / norm for v in center]
        for k in range(n_each):
            vec = [c + rng.gauss(0, 0.05) for c in center]
            norm = math.sqrt(sum(v * v for v in vec))
            items.append(
                (f"p{person}@{k}", f"person_{person}", [v / norm for v in vec])
            )
    return items


def test_research_refusal_without_ack():
    with pytest.raises(cheetah_reid.ResearchOnlyRefused):
        cheetah_reid.load_app("cheetah_s", "/tmp/nowhere", research_ack=False)
    with pytest.raises(cheetah_reid.ResearchOnlyRefused):
        cheetah_reid.require_research(None)


def test_pair_stats_match_pair_report():
    items = _synthetic()
    for threshold in (0.25, 0.45, 0.85):
        got = cheetah_reid.pair_stats(items, threshold)
        want = face_identity_study.pair_report(items, threshold=threshold)
        assert got["faces"] == want["faces"]
        assert got["same_person_pairs"] == want["same_person_pairs"]
        assert got["different_person_pairs"] == want["different_person_pairs"]
        assert got["at_threshold"] == want["at_threshold"]
        assert got["passes"] == want["passes"]
        assert got["weakest_same_person"] == want["weakest_same_person"]
        assert got["closest_different_person"] == want["closest_different_person"]
        assert [
            r["FAR"]
            for r in got["grid"]
            if r["threshold"] in face_identity_study.THRESHOLD_GRID
        ] == [r["FAR"] for r in want["grid"]]


def test_empty_stats_say_so():
    report = cheetah_reid.pair_stats([], 0.25)
    assert report["passes"] is False
    assert "reason" in report


def test_sampling_matches_production():
    for duration in (0.5, 5.0, 61.0, 288.955, 5000.0):
        assert cheetah_reid.sample_timestamps(
            duration
        ) == person_entity.sample_timestamps(duration)
