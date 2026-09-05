"""Tests for plan provenance: archival, identity and the refusal check.

The critical test is ``test_verifier_refuses_mismatched_plan``: it hands
the verifier a plan whose content hash does not match the provenance
record and asserts the verifier produces a PLAN-MISMATCH error instead
of meaningless F1-F11 findings.

``tests/test_plan_provenance.py``.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from library.tools.plan_provenance import (
    archive_plan,
    check_plan_matches_provenance,
    check_reels_in_provenance,
    plan_content_hash,
    read_provenance,
    write_provenance,
    PROVENANCE_FILENAME,
)
from library.tools.reel_conformance_verifier import (
    Finding,
    FindingClass,
    VerificationReport,
    check_plan_provenance as verifier_check_plan_provenance,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _write_plan(path, moments=None):
    """Write a minimal valid proposal file."""
    if moments is None:
        moments = [
            {
                "number": 1,
                "slug": "test-moment",
                "reason": "test",
                "timeline_start": 10.0,
                "timeline_end": 70.0,
                "approval": "approved",
                "speakers": ["Alice", "Bob"],
            }
        ]
    doc = {
        "format": "reel_proposal/1",
        "derived_from": {},
        "instruction": "test",
        "moment_count": len(moments),
        "moments": moments,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _write_different_plan(path):
    """Write a plan with different content from the default."""
    return _write_plan(path, moments=[
        {
            "number": 1,
            "slug": "different-moment",
            "reason": "different",
            "timeline_start": 100.0,
            "timeline_end": 160.0,
            "approval": "approved",
            "speakers": ["Charlie", "Dana"],
        },
        {
            "number": 2,
            "slug": "another-moment",
            "reason": "another",
            "timeline_start": 200.0,
            "timeline_end": 260.0,
            "approval": "approved",
            "speakers": ["Charlie", "Dana"],
        },
    ])


# ── plan_content_hash ────────────────────────────────────────────────

class TestPlanContentHash:
    def test_deterministic(self, tmp_path):
        plan = _write_plan(tmp_path / "plan.json")
        h1 = plan_content_hash(str(plan))
        h2 = plan_content_hash(str(plan))
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path):
        plan_a = _write_plan(tmp_path / "a.json")
        plan_b = _write_different_plan(tmp_path / "b.json")
        assert plan_content_hash(str(plan_a)) != plan_content_hash(str(plan_b))

    def test_same_content_same_hash(self, tmp_path):
        plan_a = _write_plan(tmp_path / "a.json")
        plan_b = _write_plan(tmp_path / "b.json")
        assert plan_content_hash(str(plan_a)) == plan_content_hash(str(plan_b))


# ── archive_plan ─────────────────────────────────────────────────────

class TestArchivePlan:
    def test_creates_timestamped_copy(self, tmp_path):
        plan = _write_plan(tmp_path / "plan.json")
        archived = archive_plan(str(plan))
        assert os.path.isfile(archived)
        assert archived != str(plan)
        assert "plan_" in os.path.basename(archived)
        assert archived.endswith(".json")
        # Content is identical
        assert plan.read_bytes() == open(archived, "rb").read()

    def test_original_unchanged(self, tmp_path):
        plan = _write_plan(tmp_path / "plan.json")
        original_content = plan.read_bytes()
        archive_plan(str(plan))
        assert plan.read_bytes() == original_content

    def test_custom_archive_dir(self, tmp_path):
        plan = _write_plan(tmp_path / "plan.json")
        archive_dir = tmp_path / "archives"
        archived = archive_plan(str(plan), archive_dir=str(archive_dir))
        assert archived.startswith(str(archive_dir))
        assert os.path.isfile(archived)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            archive_plan(str(tmp_path / "does_not_exist.json"))


# ── write_provenance / read_provenance ───────────────────────────────

class TestProvenance:
    def test_roundtrip(self, tmp_path):
        plan = _write_plan(tmp_path / "plan.json")
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        reel_names = ["Reel 01 - test-moment", "Reel 02 - other"]

        out_path = write_provenance(str(review_dir), str(plan), reel_names)
        assert os.path.isfile(out_path)

        prov = read_provenance(str(review_dir))
        assert prov is not None
        assert prov["plan_content_hash"] == plan_content_hash(str(plan))
        assert sorted(prov["built_reels"]) == sorted(reel_names)
        assert "built_at" in prov
        assert os.path.isabs(prov["plan_path"])

    def test_read_missing_returns_none(self, tmp_path):
        assert read_provenance(str(tmp_path)) is None


# ── check_plan_matches_provenance ────────────────────────────────────

class TestCheckPlanMatchesProvenance:
    def test_matching_plan(self, tmp_path):
        plan = _write_plan(tmp_path / "plan.json")
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        write_provenance(str(review_dir), str(plan), ["Reel 01"])
        prov = read_provenance(str(review_dir))

        matches, reason = check_plan_matches_provenance(str(plan), prov)
        assert matches
        assert "matches" in reason

    def test_changed_plan(self, tmp_path):
        plan = _write_plan(tmp_path / "plan.json")
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        write_provenance(str(review_dir), str(plan), ["Reel 01"])

        # Overwrite the plan with different content
        _write_different_plan(tmp_path / "plan.json")

        prov = read_provenance(str(review_dir))
        matches, reason = check_plan_matches_provenance(str(plan), prov)
        assert not matches
        assert "does not match" in reason


# ── check_reels_in_provenance ────────────────────────────────────────

class TestCheckReelsInProvenance:
    def test_all_present(self, tmp_path):
        plan = _write_plan(tmp_path / "plan.json")
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        write_provenance(str(review_dir), str(plan),
                         ["Reel 01", "Reel 02"])
        prov = read_provenance(str(review_dir))

        all_present, missing = check_reels_in_provenance(
            ["Reel 01", "Reel 02"], prov)
        assert all_present
        assert missing == []

    def test_extra_reels_missing(self, tmp_path):
        plan = _write_plan(tmp_path / "plan.json")
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        write_provenance(str(review_dir), str(plan), ["Reel 01"])
        prov = read_provenance(str(review_dir))

        all_present, missing = check_reels_in_provenance(
            ["Reel 01", "Reel 99"], prov)
        assert not all_present
        assert "Reel 99" in missing


# ── Verifier integration: check_plan_provenance ──────────────────────

class TestVerifierCheckPlanProvenance:
    """The verifier-level function that returns Finding objects."""

    def test_no_provenance_warns(self, tmp_path):
        """No provenance file - reels predate this change. Warn, not error."""
        plan = _write_plan(tmp_path / "plan.json")
        findings = verifier_check_plan_provenance(
            str(plan), ["Reel 01"], str(tmp_path))
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.PLAN_MISMATCH
        assert findings[0].severity == "warning"

    def test_matching_plan_no_findings(self, tmp_path):
        """Plan matches provenance - no findings."""
        plan = _write_plan(tmp_path / "plan.json")
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        write_provenance(str(review_dir), str(plan),
                         ["Reel 01 - test-moment"])

        findings = verifier_check_plan_provenance(
            str(plan), ["Reel 01 - test-moment"], str(review_dir))
        errors = [f for f in findings if f.severity == "error"]
        assert errors == []

    def test_mismatched_plan_refuses(self, tmp_path):
        """THE CRITICAL TEST: a mismatched plan produces a PLAN-MISMATCH
        error, not F1-F11 findings about a plan that doesn't describe
        these timelines.

        This reproduces the exact failure mode: firstmate ran the verifier
        against 16 reels using a plan that described 14 different moments.
        The verifier must REFUSE rather than producing 42 confident,
        precise, meaningless errors.
        """
        plan = _write_plan(tmp_path / "plan.json")
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        write_provenance(str(review_dir), str(plan),
                         ["Reel 01 - test-moment"])

        # The selector runs again and overwrites the plan
        _write_different_plan(tmp_path / "plan.json")

        findings = verifier_check_plan_provenance(
            str(plan), ["Reel 01 - test-moment"], str(review_dir))
        errors = [f for f in findings if f.severity == "error"]
        assert len(errors) == 1
        assert errors[0].finding_class == FindingClass.PLAN_MISMATCH
        assert "REFUSING" in errors[0].message
        assert "content_hash_mismatch" == errors[0].detail["reason"]

    def test_extra_reels_on_timeline_warns(self, tmp_path):
        """Reels on the timeline that the build did not produce."""
        plan = _write_plan(tmp_path / "plan.json")
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        write_provenance(str(review_dir), str(plan), ["Reel 01"])

        findings = verifier_check_plan_provenance(
            str(plan), ["Reel 01", "Reel 99 - unknown"], str(review_dir))
        # Reel 99 is not in provenance - warning
        warn_findings = [f for f in findings if f.severity == "warning"]
        assert any("Reel 99 - unknown" in f.message for f in warn_findings)


# ── VerificationReport with provenance findings ─────────────────────

class TestVerificationReportProvenance:
    def test_provenance_findings_in_all_findings(self):
        pf = Finding(
            finding_class=FindingClass.PLAN_MISMATCH,
            reel="(all)",
            message="test mismatch",
            severity="error",
        )
        report = VerificationReport(
            project_name="test",
            master_timeline="master",
            reel_results=[],
            read_only_proof={},
            provenance_findings=[pf],
        )
        assert report.has_errors
        assert report.total_errors == 1
        assert pf in report.all_findings

    def test_no_provenance_findings_no_change(self):
        """Normal path: no provenance findings, report works as before."""
        report = VerificationReport(
            project_name="test",
            master_timeline="master",
            reel_results=[],
            read_only_proof={},
        )
        assert not report.has_errors
        assert report.total_errors == 0

    def test_as_dict_includes_provenance(self):
        pf = Finding(
            finding_class=FindingClass.PLAN_MISMATCH,
            reel="(all)",
            message="test",
            severity="error",
        )
        report = VerificationReport(
            project_name="test",
            master_timeline="master",
            reel_results=[],
            read_only_proof={},
            provenance_findings=[pf],
        )
        d = report.as_dict()
        assert "provenance_findings" in d
        assert len(d["provenance_findings"]) == 1
        assert d["summary"]["passed"] is False
