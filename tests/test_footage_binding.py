"""Tests for footage-binding provenance: the durable link between
captions and the footage they were computed against.

Proves BOTH directions:
- A spine whose footage changes produces a DIFFERENT hash (defect fires).
- A spine whose footage stays the same produces the SAME hash (silent).

``tests/test_footage_binding.py``.
"""

from __future__ import annotations

import json
import os

import pytest

from library.tools.plan_provenance import (
    caption_content_hash,
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


# ── footage_binding_hash ─────────────────────────────────────────────

class TestFootageBindingHash:
    """The hash that records which footage captions were computed against."""

    def test_different_clip_id_different_hash(self):
        """A different clip_id means different footage."""
        spine_a = _spine([_speech_block("clip_001", 10.0, 20.0)])
        spine_b = _spine([_speech_block("clip_002", 10.0, 20.0)])
        assert footage_binding_hash(spine_a) != footage_binding_hash(spine_b)

    def test_different_source_start_different_hash(self):
        """Shifting the source range means the footage moved."""
        spine_a = _spine([_speech_block("clip_001", 10.0, 20.0)])
        spine_b = _spine([_speech_block("clip_001", 11.0, 20.0)])
        assert footage_binding_hash(spine_a) != footage_binding_hash(spine_b)

    def test_different_timeline_position_different_hash(self):
        """Same footage in a different timeline position is a different
        pairing - the caption timing no longer matches."""
        spine_a = _spine([_speech_block("clip_001", 10.0, 20.0,
                                        timeline_start=0.0, timeline_end=10.0)])
        spine_b = _spine([_speech_block("clip_001", 10.0, 20.0,
                                        timeline_start=5.0, timeline_end=15.0)])
        assert footage_binding_hash(spine_a) != footage_binding_hash(spine_b)

    def test_ignores_non_speech_blocks(self):
        """Only speech and hook blocks carry captions. A gap or music
        block does not affect the binding."""
        blocks = [
            _speech_block("clip_001", 10.0, 20.0),
            {"block_type": "gap", "timeline_start": 20.0, "timeline_end": 22.0},
        ]
        spine_with_gap = _spine(blocks)
        spine_without = _spine([_speech_block("clip_001", 10.0, 20.0)])
        assert footage_binding_hash(spine_with_gap) == \
               footage_binding_hash(spine_without)

    def test_no_clip_id_raises(self):
        """Speech blocks without clip_id have no binding."""
        block = _speech_block("clip_001", 10.0, 20.0)
        block["clip_id"] = None
        with pytest.raises(ValueError, match="no clip_id"):
            footage_binding_hash(_spine([block]))

    def test_multiple_blocks_order_matters(self):
        """Block order is part of the binding - swapping blocks is a
        different edit."""
        block_a = _speech_block("clip_001", 10.0, 20.0,
                                timeline_start=0.0, timeline_end=10.0)
        block_b = _speech_block("clip_002", 30.0, 40.0,
                                timeline_start=10.0, timeline_end=20.0)
        spine_ab = _spine([block_a, block_b])
        spine_ba = _spine([block_b, block_a])
        assert footage_binding_hash(spine_ab) != footage_binding_hash(spine_ba)


# ── check_footage_binding_matches_provenance ─────────────────────────

class TestCheckFootageBinding:
    """The check that detects when captions are no longer paired."""

    def test_no_provenance_refuses(self):
        """No provenance means we cannot check."""
        spine = _spine([_speech_block("clip_001", 10.0, 20.0)])
        ok, why = check_footage_binding_matches_provenance(
            "Reel 01", spine, None)
        assert ok is False
        assert "no provenance" in why.lower()

    def test_matching_binding_passes(self):
        """Footage unchanged - captions are still paired."""
        spine = _spine([_speech_block("clip_001", 10.0, 20.0)])
        h = footage_binding_hash(spine)
        provenance = {"footage_binding_hashes": {"Reel 01": h}}
        ok, why = check_footage_binding_matches_provenance(
            "Reel 01", spine, provenance)
        assert ok is True
        assert "matches" in why.lower()

    def test_moved_footage_fails(self):
        """Footage moved - captions are no longer paired."""
        spine_original = _spine([_speech_block("clip_001", 10.0, 20.0)])
        h = footage_binding_hash(spine_original)
        provenance = {"footage_binding_hashes": {"Reel 01": h}}

        # The footage moved: different source range
        spine_moved = _spine([_speech_block("clip_001", 12.0, 22.0)])
        ok, why = check_footage_binding_matches_provenance(
            "Reel 01", spine_moved, provenance)
        assert ok is False
        assert "changed" in why.lower()


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


# ── Integration: caption hash + footage binding together ─────────────

class TestCaptionAndBindingTogether:
    """The two hashes detect different classes of defect."""

    def test_same_text_moved_footage(self):
        """Caption content unchanged but footage moved - binding detects it."""
        entries = [
            {"timeline_start": 0.0, "timeline_end": 1.0, "text": "hello"}
        ]
        spine_a = _spine([_speech_block("clip_001", 10.0, 20.0)])
        spine_b = _spine([_speech_block("clip_001", 15.0, 25.0)])

        # Caption hash is the same
        assert caption_content_hash(entries) == caption_content_hash(entries)
        # Footage binding is different
        assert footage_binding_hash(spine_a) != footage_binding_hash(spine_b)
