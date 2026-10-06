"""Gap 9: the sequence step scores its ordering and re-orders on a low score.

The defect this prevents: speech sequencing is one-pass with no global
optimization. The LLM produces an ordering in one call; there is no
second pass that asks "is this the best order?" Redundancy (two passages
saying the same thing) is not measured, and a setup/payoff pair is not
placed adjacent.

These tests name that defect: a known redundancy is separated, a known
setup/payoff pair is placed adjacent, and the scoring pass is O(n^2).
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import sequence_scoring  # noqa: E402
from library.tools.sequence_scoring import (  # noqa: E402
    content_similarity,
    optimize_ordering,
    reorder_passages,
    score_ordering,
)


def _passage(text, clip_id="clip_001", serves_beats=None, source_start=0.0):
    p = {
        "clip_id": clip_id,
        "text": text,
        "source_start": source_start,
        "source_end": source_start + 5.0,
    }
    if serves_beats is not None:
        p["serves_beats"] = serves_beats
    return p


# ── Redundancy ─────────────────────────────────────────────────────

class TestRedundancyIsSeparated:
    """Two passages saying the same thing must not be adjacent."""

    def test_redundant_adjacent_passages_are_separated(self):
        """The fidelity test: known redundancy -> no redundant adjacent."""
        passages = [
            _passage("the cat sat on the mat and looked at the sun"),
            _passage("the cat sat on the mat and looked at the sun"),
            _passage("a dog ran through the park and barked loudly"),
        ]
        result = optimize_ordering(passages)
        assert result["reordered"] is True
        # The two identical passages must not be adjacent
        texts = [p["text"] for p in result["passages"]]
        for i in range(len(texts) - 1):
            assert content_similarity(texts[i], texts[i + 1]) < 0.65

    def test_non_redundant_ordering_is_unchanged(self):
        """A good ordering is not re-ordered."""
        passages = [
            _passage("the cat sat on the mat"),
            _passage("a dog ran through the park"),
            _passage("the bird flew over the tree"),
        ]
        result = optimize_ordering(passages)
        assert result["reordered"] is False

    def test_redundancy_score_detects_identical_passages(self):
        """Identical passages score 0 on redundancy."""
        passages = [
            _passage("the cat sat on the mat"),
            _passage("the cat sat on the mat"),
        ]
        score = score_ordering(passages)
        assert score["redundancy"] == 0.0
        redundancy_problems = [p for p in score["problems"] if "redundant" in p]
        assert len(redundancy_problems) == 1

    def test_content_similarity_is_symmetric(self):
        """Similarity is symmetric and bounded 0-1."""
        a = "the cat sat on the mat"
        b = "a dog ran through the park"
        assert content_similarity(a, a) == 1.0
        assert content_similarity(a, b) == 0.0
        assert content_similarity(b, a) == content_similarity(a, b)


# ── Setup/payoff ───────────────────────────────────────────────────

class TestSetupPayoffIsAdjacent:
    """A setup passage should be immediately before its payoff."""

    def _model(self):
        return {
            "beats": [
                {"beat_id": "beat:1", "setup_for": "beat:2"},
                {"beat_id": "beat:2"},
            ]
        }

    def test_setup_is_placed_before_payoff(self):
        """The fidelity test: known setup/payoff -> adjacent."""
        passages = [
            _passage("the payoff lands here", serves_beats=["beat:2"]),
            _passage("the setup happens here", serves_beats=["beat:1"]),
        ]
        result = optimize_ordering(passages, self._model())
        assert result["reordered"] is True
        # beat:1 passage must be immediately before beat:2 passage
        beats = [p.get("serves_beats", []) for p in result["passages"]]
        setup_idx = next(i for i, b in enumerate(beats) if "beat:1" in b)
        payoff_idx = next(i for i, b in enumerate(beats) if "beat:2" in b)
        assert payoff_idx - setup_idx == 1

    def test_setup_payoff_already_adjacent_is_unchanged(self):
        """An ordering that already has setup->payoff is not re-ordered."""
        passages = [
            _passage("the setup happens here", serves_beats=["beat:1"]),
            _passage("the payoff lands here", serves_beats=["beat:2"]),
        ]
        result = optimize_ordering(passages, self._model())
        assert result["reordered"] is False

    def test_setup_payoff_score_detects_separation(self):
        """A setup far from its payoff scores below 1."""
        passages = [
            _passage("the setup happens here", serves_beats=["beat:1"]),
            _passage("unrelated content about dogs"),
            _passage("the payoff lands here", serves_beats=["beat:2"]),
        ]
        score = score_ordering(passages, self._model())
        assert score["setup_payoff"] < 1.0
        assert any("setup" in p for p in score["problems"])


# ── Performance ────────────────────────────────────────────────────

class TestPerformance:
    """The scoring pass is O(n^2); <10 ms for 20 passages."""

    def test_scoring_twenty_passages_under_10ms(self):
        """20 passages score in under 10 ms."""
        import time
        passages = [
            _passage(f"passage number {i} about topic {i % 5} and subject {i % 3}")
            for i in range(20)
        ]
        start = time.perf_counter()
        score_ordering(passages)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 10.0, f"scoring took {elapsed_ms:.1f} ms"

    def test_reordering_twenty_passages_under_100ms(self):
        """Re-ordering 20 passages completes in under 100 ms of CPU time.

        Measures process CPU time, not wall clock: the defect this
        catches is the local search becoming accidentally exponential
        (e.g. trying all permutations), which no machine load causes
        and no load average hides. A bounded local search over 20
        passages - max 10 iterations of O(n^2) adjacent swaps and
        single-passage moves - completes in single-digit milliseconds.
        """
        import time
        passages = [
            _passage(f"passage number {i} about topic {i % 5}")
            for i in range(20)
        ]
        start = time.process_time()
        reorder_passages(passages)
        elapsed_ms = (time.process_time() - start) * 1000
        assert elapsed_ms < 100.0, (
            f"re-ordering took {elapsed_ms:.1f} ms of CPU time - the "
            f"local search is bounded (max 10 iterations of O(n^2) "
            f"moves), so this names an accidental exponential")


# ── Continuity and variety ─────────────────────────────────────────

class TestContinuityAndVariety:
    """Continuity: same-clip passages in source order. Variety: no clip over-used."""

    def test_same_clip_out_of_source_order_is_penalized(self):
        """Two passages from one clip out of order score below 1."""
        passages = [
            _passage("first passage", clip_id="clip_001", source_start=10.0),
            _passage("second passage", clip_id="clip_001", source_start=5.0),
        ]
        score = score_ordering(passages)
        assert score["continuity"] < 1.0

    def test_over_used_clip_is_penalized(self):
        """One clip contributing most passages scores below 1 on variety."""
        passages = [
            _passage(f"passage {i}", clip_id="clip_001")
            for i in range(5)
        ] + [_passage("other", clip_id="clip_002")]
        score = score_ordering(passages)
        assert score["variety"] < 1.0
        assert any("variety" in p or "clip" in p for p in score["problems"])
