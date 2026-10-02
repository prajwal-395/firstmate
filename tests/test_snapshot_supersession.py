"""Snapshot supersession: which snapshot a live timeline came from.

Gap G6 (2026-09-19): the `__batch-1050_`, `__final_` and base snapshots
disagreed on Reel 09's bound (36510 against 36490) with nothing on disk
saying which was authoritative, and a check graded the stale one.  The
record under test is `plan_provenance.snapshot_provenance`, written by
`record_snapshot_supersession` at promotion time and read back through
`built_from_snapshot` (which snapshot a live timeline names) and
`is_snapshot_superseded` (which snapshots read as superseded).

``library/tools/plan_provenance.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from library.tools.plan_provenance import (
    SNAPSHOT_PROVENANCE_KEY,
    built_from_snapshot,
    is_snapshot_superseded,
    read_provenance,
    record_snapshot_supersession,
    rename_reel_entries,
    write_provenance,
)

REEL_09 = "Reel 09 - your-website-is-only-20-percent"


# ── Helpers ──────────────────────────────────────────────────────────

def _write_snapshot(review_dir: Path, filename: str,
                    timeline_name: str, source_out: int) -> Path:
    """A minimal serializer-shaped snapshot carrying one bound."""
    doc = {
        "schema_version": "1.0",
        "timestamp": "2026-09-19T00:00:00+00:00",
        "metadata": {"name": timeline_name},
        "tracks": [
            {"type": "video", "index": 1, "name": "V1",
             "clips": [
                 {"unique_id": "clip-1", "name": "LC4932.MXF",
                  "record_in": 643, "record_out": 1419,
                  "source_in": 35714, "source_out": source_out},
             ]},
        ],
    }
    path = review_dir / filename
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _write_plan(path: Path) -> Path:
    doc = {
        "format": "reel_proposal/1",
        "derived_from": {},
        "instruction": "test",
        "moment_count": 1,
        "moments": [
            {"number": 9, "slug": "test-moment", "reason": "test",
             "timeline_start": 690.0, "timeline_end": 710.0,
             "approval": "approved", "speakers": ["Akshita"]},
        ],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _g6_review(tmp_path: Path):
    """The G6 disagreement: three files, one timeline, two bounds."""
    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True)
    base = _write_snapshot(
        review, "Reel_09_-_your-website-is-only-20-percent.timeline.json",
        REEL_09, 36490)
    batch = _write_snapshot(
        review,
        "Reel_09_-_your-website-is-only-20-percent__batch-1050_.timeline.json",
        REEL_09, 36510)
    final = _write_snapshot(
        review,
        "Reel_09_-_your-website-is-only-20-percent__final_.timeline.json",
        REEL_09, 36510)
    return review, base, batch, final


# ── The G6 case ──────────────────────────────────────────────────────

class TestG6Disagreement:
    def test_live_timeline_names_its_snapshot(self, tmp_path):
        """The live reel names the file it was built from, and the stale
        snapshots beside it read as superseded by that file."""
        review, base, batch, final = _g6_review(tmp_path)
        report = record_snapshot_supersession(
            str(review), {REEL_09: str(final)})
        entry, reason = built_from_snapshot(str(review), REEL_09)
        assert entry is not None, reason
        assert entry["snapshot"] == final.name
        assert "built from" in reason

        assert sorted(report[REEL_09]["superseded_now"]) == sorted(
            [base.name, batch.name])
        for stale in (base, batch):
            superseded, reason = is_snapshot_superseded(
                str(review), stale.name)
            assert superseded, reason
            assert final.name in reason
        # And the authoritative file does not read as superseded.
        superseded, reason = is_snapshot_superseded(
            str(review), final.name)
        assert not superseded, reason
        assert "authoritative" in reason

# ── The record over time ─────────────────────────────────────────────

class TestSupersessionOverTime:
    def test_second_promotion_supersedes_the_first(self, tmp_path):
        review = tmp_path / "pipeline_output" / "review"
        review.mkdir(parents=True)
        first = _write_snapshot(review, "Reel_09_v1.timeline.json",
                                REEL_09, 36490)
        record_snapshot_supersession(str(review), {REEL_09: str(first)})
        second = _write_snapshot(review, "Reel_09_v2.timeline.json",
                                 REEL_09, 36510)
        record_snapshot_supersession(str(review), {REEL_09: str(second)})
        entry, _reason = built_from_snapshot(str(review), REEL_09)
        assert entry["snapshot"] == second.name
        superseded, _reason = is_snapshot_superseded(
            str(review), first.name)
        assert superseded

    def test_partial_promotion_keeps_neighbours(self, tmp_path):
        review = tmp_path / "pipeline_output" / "review"
        review.mkdir(parents=True)
        reel_10 = "Reel 10 - missing-answer"
        snap_09 = _write_snapshot(review, "Reel_09.timeline.json",
                                  REEL_09, 36510)
        snap_10 = _write_snapshot(review, "Reel_10.timeline.json",
                                  reel_10, 39202)
        record_snapshot_supersession(str(review), {REEL_09: str(snap_09)})
        record_snapshot_supersession(str(review), {reel_10: str(snap_10)})
        entry, reason = built_from_snapshot(str(review), REEL_09)
        assert entry is not None, reason
        assert entry["snapshot"] == snap_09.name
        entry, reason = built_from_snapshot(str(review), reel_10)
        assert entry is not None, reason

# ── Refusals, never silent passes ────────────────────────────────────

class TestRefusals:
    def test_changed_or_missing_bytes_are_not_current(self, tmp_path):
        review, _base, _batch, final = _g6_review(tmp_path)
        record_snapshot_supersession(str(review), {REEL_09: str(final)})
        _write_snapshot(review, final.name, REEL_09, 36490)
        entry, reason = built_from_snapshot(str(review), REEL_09)
        assert entry is None, reason
        assert "changed" in reason
        # ...and a file no longer on disk refuses too.
        final.unlink()
        entry, reason = built_from_snapshot(str(review), REEL_09)
        assert entry is None, reason
        assert "no longer on disk" in reason

    def test_no_record_refuses_both_readers(self, tmp_path):
        review = tmp_path / "pipeline_output" / "review"
        review.mkdir(parents=True)
        entry, reason = built_from_snapshot(str(review), REEL_09)
        assert entry is None
        assert "no provenance record" in reason
        superseded, reason = is_snapshot_superseded(
            str(review), "Reel_09.timeline.json")
        assert not superseded
        assert "no provenance record" in reason

    def test_unreadable_and_nameless_files_are_never_marked(self, tmp_path):
        review, _base, _batch, final = _g6_review(tmp_path)
        (review / "broken.timeline.json").write_text(
            "{not json", encoding="utf-8")
        (review / "nameless.timeline.json").write_text(
            json.dumps({"metadata": {}, "tracks": []}), encoding="utf-8")
        report = record_snapshot_supersession(
            str(review), {REEL_09: str(final)})
        assert "broken.timeline.json" not in report[REEL_09]["superseded_now"]
        assert "nameless.timeline.json" not in report[REEL_09][
            "superseded_now"]
        superseded, _reason = is_snapshot_superseded(
            str(review), "broken.timeline.json")
        assert not superseded


# ── The table survives the provenance lifecycle ──────────────────────

class TestProvenanceLifecycle:
    def test_build_preserves_snapshot_table(self, tmp_path):
        review, _base, _batch, final = _g6_review(tmp_path)
        plan = _write_plan(tmp_path / "plan.json")
        write_provenance(str(review), str(plan), [REEL_09])
        record_snapshot_supersession(str(review), {REEL_09: str(final)})
        # Same plan: a rebuild merges and keeps the table.
        write_provenance(str(review), str(plan), [REEL_09])
        entry, reason = built_from_snapshot(str(review), REEL_09)
        assert entry is not None, reason
        # Different plan: reels reset, snapshots still describe timelines.
        other = tmp_path / "other.json"
        other.write_text(
            json.dumps({"format": "reel_proposal/1",
                        "moments": [{"number": 1, "other": True}]}),
            encoding="utf-8")
        write_provenance(str(review), str(other), [REEL_09])
        entry, reason = built_from_snapshot(str(review), REEL_09)
        assert entry is not None, reason

    def test_rename_moves_table_keys(self, tmp_path):
        review = tmp_path / "pipeline_output" / "review"
        review.mkdir(parents=True)
        staging = f"{REEL_09} (rebuild staging)"
        snap = _write_snapshot(review, "Reel_09_staging.timeline.json",
                               staging, 36510)
        # The real order: the build records provenance under the
        # staging name, the snapshot lands, promotion renames.
        plan = _write_plan(tmp_path / "plan.json")
        write_provenance(str(review), str(plan), [staging])
        record_snapshot_supersession(str(review), {staging: str(snap)})
        rename_reel_entries(str(review), {staging: REEL_09})
        provenance = read_provenance(str(review))
        assert REEL_09 in provenance[SNAPSHOT_PROVENANCE_KEY]
        assert staging not in provenance[SNAPSHOT_PROVENANCE_KEY]
