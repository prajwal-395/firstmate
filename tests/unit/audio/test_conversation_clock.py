"""The conversation clock (M6): offset recovery, grouping, cross-check.

Every test builds its M1 documents and its memory under `tmp_path`/
`memory_root`. No test reaches a real project (AGENTS.md §8) or runs
voz/MFA: n-gram matching and the Resolve cross-check are pure
functions over small synthetic word lists.
"""


import pytest

from library.tools import conversation_clock as cc
from library.tools import source_memory


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _m1_doc(words_with_times, word_count=None, digest="d") -> dict:
    words = [{"word": w, "start": t, "end": t + 0.4}
             for w, t in words_with_times]
    return {
        "content_digest": digest, "source_file": "/nowhere/FILE.MXF",
        "status": source_memory.M1_STATUS_TRANSCRIBED,
        "utterance_cut": "hybrid-windows",
        "program_track": {"channel": 1, "basis": "single",
                          "measured_levels_db": {"CH1": -20.0}},
        "utterances": [{"start": words[0]["start"], "end": words[-1]["end"],
                        "text": " ".join(w for w, _ in words_with_times),
                        "words": words, "confidence": 0.0,
                        "method": "hybrid-mfa"}] if words else [],
        "utterance_count": 1 if words else 0,
        "word_count": word_count if word_count is not None else len(words),
        "speech_seconds": 0.0,
        "instrument": {"arm": "hybrid", "aligner": "mfa"},
    }


def _distinct_words(count, prefix="w"):
    """Every 4-gram over this vocabulary is unique by construction."""
    return [f"{prefix}{i:04d}" for i in range(count)]


# ── pairwise_offset ──────────────────────────────────────────────────


def test_pairwise_offset_recovers_the_shift_despite_outliers():
    """B transcribed `shift` seconds later than A is reported with that
    sign: `a_time == b_time + offset_seconds`. A contiguous run of
    matches on an unrelated offset must not drag the median -
    `agreeing_matches` says how many were used."""
    vocab = _distinct_words(80)
    a = _m1_doc([(w, float(i)) for i, w in enumerate(vocab)])
    shift = 12.5
    b = _m1_doc([(w, float(i) + shift) for i, w in enumerate(vocab)])
    result = cc.pairwise_offset(a, b)
    assert result["offset_seconds"] == pytest.approx(-shift, abs=0.01)
    assert result["agreement_fraction"] == 1.0
    assert result["agreeing_matches"] == len(vocab) - cc.NGRAM_SIZE + 1

    times_b = [float(i) + 5.0 for i in range(len(vocab))]
    times_b[-5:] = [t + 500 for t in times_b[-5:]]
    result = cc.pairwise_offset(a, _m1_doc(list(zip(vocab, times_b))))
    assert result["offset_seconds"] == pytest.approx(-5.0, abs=0.01)
    assert result["total_candidate_matches"] > result["agreeing_matches"]
    assert result["agreement_fraction"] < 1.0


# ── grouping and the shared clock ───────────────────────────────────


def test_discover_edges_only_keeps_qualifying_pairs():
    """C shares no word with A or B: there is nothing to match, so no
    edge - not a spurious 0 offset."""
    vocab = _distinct_words(80)
    a = _m1_doc([(w, float(i)) for i, w in enumerate(vocab)], digest="A")
    b = _m1_doc([(w, float(i) + 3.0) for i, w in enumerate(vocab)], digest="B")
    c = _m1_doc([(w, float(i)) for i, w in enumerate(_distinct_words(40, "z"))],
               digest="C")

    edges = cc.discover_edges({"A": a, "B": b, "C": c})

    assert set(edges) == {("A", "B")}
    assert cc.pairwise_offset(a, c) is None
    groups = cc.group_digests(["A", "B", "C"], edges)
    assert groups == [["A", "B"]]


def test_clock_for_group_chains_a_transitive_offset():
    """B only connects A and C; C's offset to A must walk through B."""
    edges = {
        ("A", "B"): {"offset_seconds": 2.0, "agreeing_matches": 100,
                    "agreement_fraction": 0.99},
        ("B", "C"): {"offset_seconds": -5.0, "agreeing_matches": 100,
                    "agreement_fraction": 0.99},
    }
    # A_time == B_time + 2; B_time == C_time + (-5) => B_time = C_time - 5
    # => A_time == C_time - 5 + 2 == C_time - 3
    word_counts = {"A": 10, "B": 5, "C": 1}  # A is the reference

    clocks = cc.clock_for_group(["A", "B", "C"], edges, word_counts)

    assert clocks["A"]["reference_digest"] == "A"
    assert clocks["A"]["offset_to_reference_seconds"] == 0.0
    assert clocks["B"]["offset_to_reference_seconds"] == pytest.approx(2.0)
    assert clocks["B"]["direct_measurement"] is not None
    assert clocks["C"]["offset_to_reference_seconds"] == pytest.approx(-3.0)
    assert clocks["C"]["path_to_reference"] == ["C", "B", "A"]
    # C-A was never measured directly - only through B.
    assert clocks["C"]["direct_measurement"] is None


# ── the Resolve cross-check reads saved records, direction-safe ────


def _segment(source_file, source_start, source_end, timeline_start,
            timeline_end):
    return {"source_file": source_file, "source_start": source_start,
           "source_end": source_end, "timeline_start": timeline_start,
           "timeline_end": timeline_end, "resolve_item_id": "x"}


def test_resolve_cut_offsets_is_antisymmetric_and_skips_loose_boundaries():
    """Swapping which source is named `a` negates the result - a cut's
    implied offset does not depend on which side you call A. A boundary
    gap wider than the tolerance is a real edit, not a camera-switch
    instant, and is not read as one."""
    segments = [
        _segment("A.mov", 10.0, 14.0, 0.0, 4.0),
        _segment("B.mov", 6.2, 20.0, 4.1, 17.9),
    ]
    a_minus_b = cc.resolve_cut_offsets(segments, "A.mov", "B.mov")
    b_minus_a = cc.resolve_cut_offsets(segments, "B.mov", "A.mov")
    assert a_minus_b == pytest.approx([14.0 - 6.2])
    assert b_minus_a == pytest.approx([6.2 - 14.0])

    loose = [
        _segment("A.mov", 10.0, 14.0, 0.0, 4.0),
        _segment("B.mov", 6.2, 20.0, 9.0, 22.8),  # 5 s gap
    ]
    assert cc.resolve_cut_offsets(loose, "A.mov", "B.mov") == []


# ── map_time ─────────────────────────────────────────────────────


def test_map_time_round_trips_through_the_reference(memory_root):
    source_memory.write_json(
        source_memory.source_dir("A") / cc.SLOT_CLOCK,
        {"content_digest": "A", "group_id": "g", "group_members": ["A", "B"],
         "reference_digest": "A", "offset_to_reference_seconds": 0.0,
         "path_to_reference": ["A"], "direct_measurement": None,
         "resolve_cross_check": None, "ngram_size": 4, "built_at": "now"})
    source_memory.write_json(
        source_memory.source_dir("B") / cc.SLOT_CLOCK,
        {"content_digest": "B", "group_id": "g", "group_members": ["A", "B"],
         "reference_digest": "A", "offset_to_reference_seconds": 3.8,
         "path_to_reference": ["B", "A"], "direct_measurement": {},
         "resolve_cross_check": None, "ngram_size": 4, "built_at": "now"})

    assert cc.map_time("B", 100.0) == {"A": pytest.approx(103.8)}
    assert cc.map_time("A", 103.8) == {"B": pytest.approx(100.0)}
    assert cc.map_time("unknown-digest", 5.0) == {}
