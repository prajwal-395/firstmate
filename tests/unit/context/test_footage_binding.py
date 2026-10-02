"""Tests for footage-binding provenance: the durable link between
captions and the footage they were computed against.

Proves BOTH directions:
- A spine whose footage changes produces a DIFFERENT hash (defect fires).
- A spine whose footage stays the same produces the SAME hash (silent).

``tests/unit/context/test_footage_binding.py``.
"""

from __future__ import annotations


import pytest

from library.tools.plan_provenance import (
    check_footage_binding_matches_provenance,
    footage_binding_hash,
    write_provenance,
    read_provenance,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _spine(blocks):
    """Wrap blocks in a minimal spine dict."""
    return {"structure": blocks}


def _speech_block(clip_id, source_start, source_end,
                  timeline_start=0.0, timeline_end=10.0, text="hello"):
    """A minimal speech block with footage identity."""
    return {
        "position": 0,
        "block_type": "speech",
        "clip_id": clip_id,
        "source_start": source_start,
        "source_end": source_end,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "word_timestamps": [],
        "alignment_method": "test",
        "speaker": "Alice",
        "content": {"text": text},
    }


def _hook_block(clip_id, source_start, source_end,
                timeline_start=0.0, timeline_end=5.0, text="hook"):
    """A minimal hook block."""
    return {
        "position": 0,
        "block_type": "hook",
        "clip_id": clip_id,
        "source_start": source_start,
        "source_end": source_end,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "content": {"text": text},
    }


# ── footage_binding_hash and its check ───────────────────────────────

def test_the_binding_hash_covers_footage_identity_and_nothing_else():
    """Clip, source range, timeline position and block order are each
    part of the binding; a non-caption block is not; a speech block with
    no clip_id has no binding and raises."""
    base = _spine([_speech_block("clip_001", 10.0, 20.0)])
    changed = {
        "clip_id": _spine([_speech_block("clip_002", 10.0, 20.0)]),
        "source_start": _spine([_speech_block("clip_001", 11.0, 20.0)]),
        "timeline": _spine([_speech_block("clip_001", 10.0, 20.0,
                                          timeline_start=5.0,
                                          timeline_end=15.0)]),
    }
    for what, spine in changed.items():
        assert footage_binding_hash(spine) != footage_binding_hash(base), what

    block_a = _speech_block("clip_001", 10.0, 20.0,
                            timeline_start=0.0, timeline_end=10.0)
    block_b = _speech_block("clip_002", 30.0, 40.0,
                            timeline_start=10.0, timeline_end=20.0)
    assert (footage_binding_hash(_spine([block_a, block_b]))
            != footage_binding_hash(_spine([block_b, block_a])))

    with_gap = _spine([
        _speech_block("clip_001", 10.0, 20.0),
        {"block_type": "gap", "timeline_start": 20.0, "timeline_end": 22.0},
    ])
    assert footage_binding_hash(with_gap) == footage_binding_hash(base)

    block = _speech_block("clip_001", 10.0, 20.0)
    block["clip_id"] = None
    with pytest.raises(ValueError, match="no clip_id"):
        footage_binding_hash(_spine([block]))


def test_the_check_refuses_without_provenance_and_reads_both_directions():
    spine = _spine([_speech_block("clip_001", 10.0, 20.0)])
    ok, why = check_footage_binding_matches_provenance("Reel 01", spine, None)
    assert ok is False and "no provenance" in why.lower()

    provenance = {"footage_binding_hashes": {
        "Reel 01": footage_binding_hash(spine)}}
    ok, why = check_footage_binding_matches_provenance(
        "Reel 01", spine, provenance)
    assert ok is True and "matches" in why.lower()

    moved = _spine([_speech_block("clip_001", 12.0, 22.0)])
    ok, why = check_footage_binding_matches_provenance(
        "Reel 01", moved, provenance)
    assert ok is False and "changed" in why.lower()


# ── write_provenance with footage bindings ───────────────────────────

class TestWriteProvenanceWithBindings:
    """The provenance writer records and merges footage bindings."""

    def test_partial_rebuild_merges_bindings(self, tmp_path):
        """Rebuilding one reel keeps the other's footage binding."""
        plan = tmp_path / "plan.json"
        plan.write_text('{"moments": []}', encoding="utf-8")
        review = str(tmp_path / "review")

        # First build: two reels
        write_provenance(review, str(plan), ["Reel 01", "Reel 02"],
                         footage_binding_hashes={
                             "Reel 01": "v1:aaa",
                             "Reel 02": "v1:bbb"})

        # Partial rebuild: only reel 01
        write_provenance(review, str(plan), ["Reel 01"],
                         footage_binding_hashes={"Reel 01": "v1:ccc"})

        prov = read_provenance(review)
        assert prov["footage_binding_hashes"]["Reel 01"] == "v1:ccc"
        assert prov["footage_binding_hashes"]["Reel 02"] == "v1:bbb"

    def test_superseded_plan_clears_bindings(self, tmp_path):
        """A different plan supersedes all bindings."""
        plan_a = tmp_path / "plan_a.json"
        plan_a.write_text('{"moments": [1]}', encoding="utf-8")
        plan_b = tmp_path / "plan_b.json"
        plan_b.write_text('{"moments": [2]}', encoding="utf-8")
        review = str(tmp_path / "review")

        write_provenance(review, str(plan_a), ["Reel 01"],
                         footage_binding_hashes={"Reel 01": "v1:aaa"})
        write_provenance(review, str(plan_b), ["Reel 01"],
                         footage_binding_hashes={"Reel 01": "v1:bbb"})

        prov = read_provenance(review)
        assert prov["footage_binding_hashes"]["Reel 01"] == "v1:bbb"
        assert "Reel 01" not in (prov.get("superseded_bindings") or {})
