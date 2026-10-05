"""Fidelity tests for manual-edit intent verification (E10).

The defect this fix prevents: a manual edit that crops the subject's
face is attributed to the captain and the mismatch is downgraded to a
warning - but the creative degradation is not flagged. Ren preserves
the edit but does not verify it against intent.

These tests name that defect: a manual edit that crops the face is
flagged as a creative-intent regression; a manual edit that improves
the framing is not flagged.
"""

import json

import pytest

from library.tools import manual_edit_intent


def _make_item(name="clip", zoom=1.0, track="video", track_name="V1",
               source_in=0, source_out=100):
    """A timeline item with a transform."""
    transform = {"Zoom": zoom} if zoom is not None else {}
    return {
        "name": name,
        "track_type": track,
        "track_name": track_name,
        "source_in_frame": source_in,
        "source_out_frame": source_out,
        "record_in": 0,
        "record_out": 100,
        "transform": transform,
    }


def _make_editor_change(record_id, before_items, after_items):
    """An editor change record with before/after snapshots."""
    return {
        "id": record_id,
        "recorded_at": "2026-10-05T12:00:00+00:00",
        "before_snapshot": {"items": before_items},
        "after_snapshot": {"items": after_items},
        "changes": [],
        "status": "pending",
    }


def _make_edit_entry(entry_id, editor_change_id, summary="captain edit"):
    """A manual edit history entry."""
    return {
        "id": entry_id,
        "at": "2026-10-05T12:00:00+00:00",
        "actor": "captain",
        "act": "manual_edit",
        "summary": summary,
        "refs": {"editor_change_id": editor_change_id},
    }


def _write_provenance(tmp_path, reel_name, records):
    """Write a plan_provenance.json with editor change records."""
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    doc = {
        "unattributed_editor_changes": {
            reel_name: records,
        }
    }
    (review_dir / "plan_provenance.json").write_text(
        json.dumps(doc), encoding="utf-8")
    return str(review_dir)


class TestFaceCropDetection:
    """A manual edit that crops the subject's face is flagged."""

    def test_zoom_in_beyond_threshold_is_flagged(self, tmp_path):
        """Zoom from 1.0 to 1.6 crops the face - flagged."""
        before = [_make_item(name="speaker", zoom=1.0)]
        after = [_make_item(name="speaker", zoom=1.6)]
        record = _make_editor_change("change-1", before, after)
        review_dir = _write_provenance(tmp_path, "Reel 01", [record])
        entry = _make_edit_entry("edit-1", "change-1")
        findings = manual_edit_intent.verify_manual_edit_intent(
            [entry], review_dir=review_dir, reel_name="Reel 01")
        assert len(findings) == 1
        assert findings[0]["check"] == "face_crop"
        assert "crops the subject's face" in findings[0]["regression"]

    def test_zoom_out_is_not_flagged(self, tmp_path):
        """Zoom from 1.6 to 1.0 improves framing - not flagged."""
        before = [_make_item(name="speaker", zoom=1.6)]
        after = [_make_item(name="speaker", zoom=1.0)]
        record = _make_editor_change("change-2", before, after)
        review_dir = _write_provenance(tmp_path, "Reel 02", [record])
        entry = _make_edit_entry("edit-2", "change-2")
        findings = manual_edit_intent.verify_manual_edit_intent(
            [entry], review_dir=review_dir, reel_name="Reel 02")
        assert findings == []

    def test_small_zoom_in_is_not_flagged(self, tmp_path):
        """Zoom from 1.0 to 1.2 is within tolerance - not flagged."""
        before = [_make_item(name="speaker", zoom=1.0)]
        after = [_make_item(name="speaker", zoom=1.2)]
        record = _make_editor_change("change-3", before, after)
        review_dir = _write_provenance(tmp_path, "Reel 03", [record])
        entry = _make_edit_entry("edit-3", "change-3")
        findings = manual_edit_intent.verify_manual_edit_intent(
            [entry], review_dir=review_dir, reel_name="Reel 03")
        assert findings == []

    def test_no_transform_change_is_not_flagged(self, tmp_path):
        """A non-transform change (e.g. caption edit) is not flagged."""
        before = [_make_item(name="speaker", zoom=1.0)]
        after = [_make_item(name="speaker", zoom=1.0)]
        record = _make_editor_change("change-4", before, after)
        review_dir = _write_provenance(tmp_path, "Reel 04", [record])
        entry = _make_edit_entry("edit-4", "change-4")
        findings = manual_edit_intent.verify_manual_edit_intent(
            [entry], review_dir=review_dir, reel_name="Reel 04")
        assert findings == []

    def test_edit_without_editor_change_record_is_not_flagged(self, tmp_path):
        """A manual edit with no matching editor change record is skipped."""
        review_dir = _write_provenance(tmp_path, "Reel 05", [])
        entry = _make_edit_entry("edit-5", "nonexistent-change")
        findings = manual_edit_intent.verify_manual_edit_intent(
            [entry], review_dir=review_dir, reel_name="Reel 05")
        assert findings == []

    def test_ren_edit_is_not_checked(self, tmp_path):
        """A Ren build entry is not a manual edit - not checked."""
        review_dir = _write_provenance(tmp_path, "Reel 06", [])
        entry = {
            "id": "build-1",
            "at": "2026-10-05T12:00:00+00:00",
            "actor": "ren",
            "act": "build",
            "summary": "Ren build",
            "refs": {},
        }
        findings = manual_edit_intent.verify_manual_edit_intent(
            [entry], review_dir=review_dir, reel_name="Reel 06")
        assert findings == []

    def test_empty_history_returns_no_findings(self):
        """No edit history means no findings."""
        findings = manual_edit_intent.verify_manual_edit_intent(
            [], review_dir="", reel_name="Reel 07")
        assert findings == []

    def test_finding_names_the_edit(self, tmp_path):
        """The finding names the edit it is attributed to."""
        before = [_make_item(name="speaker", zoom=1.0)]
        after = [_make_item(name="speaker", zoom=1.8)]
        record = _make_editor_change("change-8", before, after)
        review_dir = _write_provenance(tmp_path, "Reel 08", [record])
        entry = _make_edit_entry("edit-8", "change-8",
                                 summary="captain zoomed in")
        findings = manual_edit_intent.verify_manual_edit_intent(
            [entry], review_dir=review_dir, reel_name="Reel 08")
        assert len(findings) == 1
        assert findings[0]["edit_id"] == "edit-8"
        assert findings[0]["edit_at"] == "2026-10-05T12:00:00+00:00"
        assert findings[0]["edit_summary"] == "captain zoomed in"
