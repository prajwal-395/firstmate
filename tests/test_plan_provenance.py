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


# ── A partial rebuild must not delete the record it did not write ────

def test_partial_rebuild_merges_and_leaves_the_others_intact(tmp_path):
    """Rebuilding one reel must not destroy provenance for eighteen.

    `write_provenance` wrote `built_reels: sorted(reel_names)`
    unconditionally, so a one-reel rebuild replaced a nineteen-reel
    record with a one-reel record. After that
    `check_reels_in_provenance` reports the other eighteen missing and
    every check depending on it grades against a baseline that was
    silently deleted - data loss wearing the shape of a write.
    """
    from library.tools import plan_provenance as pp

    review = tmp_path / "review"
    review.mkdir()
    plan = tmp_path / "reel_proposals_v2.json"
    plan.write_text('{"moments": []}', encoding="utf-8")

    nineteen = [f"Reel {n:02d} - slug-{n}" for n in range(1, 20)]
    pp.write_provenance(str(review), str(plan), nineteen,
                        caption_hashes={n: f"hash-{n}" for n in nineteen})
    before = pp.read_provenance(str(review))
    assert len(before["built_reels"]) == 19

    # Rebuild ONE, with a new caption plan for it alone.
    pp.write_provenance(str(review), str(plan), ["Reel 07 - slug-7"],
                        caption_hashes={"Reel 07 - slug-7": "rebuilt"})
    after = pp.read_provenance(str(review))

    assert after["built_reels"] == before["built_reels"], (
        "the eighteen reels this rebuild did not touch must survive")
    assert after["caption_hashes"]["Reel 07 - slug-7"] == "rebuilt"
    for n in nineteen:
        if n != "Reel 07 - slug-7":
            assert after["caption_hashes"][n] == f"hash-{n}", (
                f"{n} lost its recorded caption plan to a rebuild of "
                f"another reel")
    assert after["plan_content_hash"] == before["plan_content_hash"]


def test_a_different_plan_supersedes_rather_than_merging(tmp_path):
    """Old entries describe reels a NEW plan did not build."""
    from library.tools import plan_provenance as pp

    review = tmp_path / "review"
    review.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text('{"moments": [1]}', encoding="utf-8")
    pp.write_provenance(str(review), str(plan), ["Reel 01 - a"])
    first = pp.read_provenance(str(review))["plan_content_hash"]

    plan.write_text('{"moments": [2]}', encoding="utf-8")
    pp.write_provenance(str(review), str(plan), ["Reel 02 - b"])
    after = pp.read_provenance(str(review))

    assert after["built_reels"] == ["Reel 02 - b"], (
        "carrying the old reels forward would assert a provenance that "
        "never existed")
    assert after["superseded_plan_hash"] == first


# ── Captions may not be graded against an unknown baseline ───────────

class _Card:
    def __init__(self, start, frames, text):
        self.start_seconds, self.frames, self.text = start, frames, text


def test_caption_grading_refuses_without_a_recorded_plan():
    from library.tools import plan_provenance as pp

    cards = [_Card(0.0, 24, "one"), _Card(1.0, 24, "two")]
    ok, why = pp.check_captions_match_provenance("Reel 01", cards, None)
    assert ok is False and "no provenance record" in why

    ok, why = pp.check_captions_match_provenance(
        "Reel 01", cards, {"plan_content_hash": "x"})
    assert ok is False and "no caption plan" in why


def test_caption_grading_refuses_when_the_grouping_changed():
    """The captain's nineteen, in miniature: same moments, new cards."""
    from library.tools import plan_provenance as pp

    built = [_Card(0.0, 24, "one two"), _Card(1.0, 24, "three four")]
    regrouped = [_Card(0.0, 12, "one"), _Card(0.5, 12, "two"),
                 _Card(1.0, 24, "three four")]
    record = {"caption_hashes": {"Reel 01": pp.caption_content_hash(built)}}

    ok, _ = pp.check_captions_match_provenance("Reel 01", built, record)
    assert ok is True

    ok, why = pp.check_captions_match_provenance("Reel 01", regrouped, record)
    assert ok is False and "card grouping has changed" in why


def test_caption_hash_ignores_styling():
    """A restyle is not a different caption plan (AGENTS.md 14)."""
    from library.tools import plan_provenance as pp

    a = _Card(0.0, 24, "hello")
    b = _Card(0.0, 24, "hello")
    b.speaker, b.font_size = "Craig", 99
    assert pp.caption_content_hash([a]) == pp.caption_content_hash([b])


# ── Property tests: the hash cannot be hollow again ──────────────────

def test_text_change_produces_different_hash():
    """Two card sets differing ONLY in one card's text hash differently."""
    from library.tools import plan_provenance as pp

    cards_a = [_Card(0.0, 24, "hello world"), _Card(1.0, 48, "foo bar")]
    cards_b = [_Card(0.0, 24, "hello world"), _Card(1.0, 48, "foo baz")]
    assert pp.caption_content_hash(cards_a) != pp.caption_content_hash(cards_b)


def test_length_change_produces_different_hash():
    """Two card sets differing ONLY in one card's length hash differently."""
    from library.tools import plan_provenance as pp

    cards_a = [_Card(0.0, 24, "hello"), _Card(1.0, 48, "world")]
    cards_b = [_Card(0.0, 24, "hello"), _Card(1.0, 49, "world")]
    assert pp.caption_content_hash(cards_a) != pp.caption_content_hash(cards_b)


def test_styling_change_produces_same_hash():
    """Two card sets differing ONLY in styling hash the SAME."""
    from library.tools import plan_provenance as pp

    a = _Card(5.0, 120, "example text")
    b = _Card(5.0, 120, "example text")
    b.speaker = "Alice"
    b.font_size = 72
    b.color = "#FF0000"
    b.font_family = "Comic Sans"
    assert pp.caption_content_hash([a]) == pp.caption_content_hash([b])


def test_contentless_entries_cannot_collide_with_real_cards():
    """A set of empty or contentless entries must NOT produce the same
    digest as real cards with the same count.

    This reproduces the fourteen-empty-dicts case from the end-to-end
    build: hashing rendered segments (which have overlay_path and
    segment_id but no text, start_seconds or frames) produced a hash
    that was indistinguishable from real cards.
    """
    from library.tools import plan_provenance as pp

    # Fourteen rendered segments (the exact shape the old bug hashed).
    fourteen_rendered_segments = [
        {"overlay_path": f"/tmp/seg_{i}.mov", "segment_id": f"seg_{i}",
         "timeline_start": i * 2.0, "timeline_end": i * 2.0 + 1.5,
         "block_position": str(i), "provenance": "rendered"}
        for i in range(14)
    ]

    # Fourteen real caption cards.
    fourteen_real_cards = [
        _Card(i * 2.0, 36, f"caption text for card {i}")
        for i in range(14)
    ]
    real_hash = pp.caption_content_hash(fourteen_real_cards)

    # The fourteen EMPTY dicts that the old code effectively produced
    # (all fields resolved to 0.0/0/"").
    fourteen_empty_dicts = [{} for _ in range(14)]

    # Empty dicts must raise ValueError because they are contentless.
    with pytest.raises(ValueError, match="none carried text"):
        pp.caption_content_hash(fourteen_empty_dicts)

    # Rendered segments have timeline_start/timeline_end so they carry
    # some content and won't raise - but they hash differently from
    # real cards because they don't have text or frame counts matching.
    segment_hash = pp.caption_content_hash(fourteen_rendered_segments)
    assert segment_hash != real_hash, (
        "rendered segments must not hash the same as real caption cards")


def test_hollow_v0_hash_treated_as_absent():
    """An existing v0 hash (produced by the old buggy code) must be
    treated as absent so the duration checks refuse rather than falsely
    pass.  The v0 hash is a bare hex string; the v1 hash starts with
    'v1:'.
    """
    from library.tools import plan_provenance as pp

    cards = [_Card(0.0, 24, "hello"), _Card(1.0, 48, "world")]
    # Simulate a v0 hash: a bare SHA-256 hex digest (64 hex chars).
    v0_hash = "a" * 64
    record = {"caption_hashes": {"Reel 01": v0_hash}}

    ok, why = pp.check_captions_match_provenance("Reel 01", cards, record)
    assert ok is False, "a v0 hash must not allow grading"
    assert "hollow" in why or "v0" in why, (
        f"expected 'hollow' or 'v0' in refusal, got: {why}")


def test_v1_hash_has_version_prefix():
    """The v1 hash starts with 'v1:' so it is distinguishable from v0."""
    from library.tools import plan_provenance as pp

    cards = [_Card(0.0, 24, "hello")]
    h = pp.caption_content_hash(cards)
    assert h.startswith("v1:"), f"expected v1: prefix, got {h[:10]}..."


def test_subtitle_entry_dicts_hash_correctly():
    """Subtitle entries from step 4.01 (timeline_start/timeline_end/text)
    produce a meaningful hash, not a hollow one.
    """
    from library.tools import plan_provenance as pp

    entries = [
        {"timeline_start": 0.0, "timeline_end": 1.0, "text": "hello",
         "speaker": "Alice", "word_count": 1},
        {"timeline_start": 1.5, "timeline_end": 3.0, "text": "world",
         "speaker": "Bob", "word_count": 1},
    ]
    h = pp.caption_content_hash(entries)
    assert h.startswith("v1:")

    # Changing text must change the hash.
    entries_b = [
        {"timeline_start": 0.0, "timeline_end": 1.0, "text": "goodbye",
         "speaker": "Alice", "word_count": 1},
        {"timeline_start": 1.5, "timeline_end": 3.0, "text": "world",
         "speaker": "Bob", "word_count": 1},
    ]
    assert pp.caption_content_hash(entries_b) != h


# ── Per-reel build stamps: WHEN and WITH WHAT ──────────────────────

def _plan_file(tmp_path, body='{"moments": []}'):
    plan = tmp_path / "reel_proposals_v2.json"
    plan.write_text(body, encoding="utf-8")
    return plan


def test_a_build_records_per_reel_built_at_and_built_with(tmp_path):
    """Remove the stamps and nobody can say which engine built a reel.

    The file-level `built_at` this extends cannot answer it: merge
    time is not build time, and six silently diverged reels were
    unanswerable for exactly that reason.
    """
    from library.tools import plan_provenance as pp

    review = tmp_path / "review"
    review.mkdir()
    pp.write_provenance(str(review), str(_plan_file(tmp_path)),
                        ["Reel 01 - a", "Reel 02 - b"])
    doc = pp.read_provenance(str(review))

    for reel in ("Reel 01 - a", "Reel 02 - b"):
        assert doc["built_at_reels"][reel]  # an ISO timestamp
        assert "T" in doc["built_at_reels"][reel]
    assert doc["built_with"]["Reel 01 - a"]
    assert (doc["built_with"]["Reel 01 - a"]
            == doc["built_with"]["Reel 02 - b"]
            == pp.reel_code_hash())


def test_a_partial_rebuild_restamps_only_the_reel_it_touched(tmp_path):
    """A neighbour's stamp must survive a rebuild it did not take part
    in - the same merge rule caption hashes keep, or the stamp is a
    file-level one wearing per-reel clothes."""
    from library.tools import plan_provenance as pp

    review = tmp_path / "review"
    review.mkdir()
    plan = _plan_file(tmp_path)
    pp.write_provenance(str(review), str(plan),
                        ["Reel 01 - a", "Reel 02 - b"])
    before = pp.read_provenance(str(review))

    time.sleep(0.01)
    pp.write_provenance(str(review), str(plan), ["Reel 02 - b"])
    after = pp.read_provenance(str(review))

    assert (after["built_at_reels"]["Reel 01 - a"]
            == before["built_at_reels"]["Reel 01 - a"])
    assert (after["built_at_reels"]["Reel 02 - b"]
            >= before["built_at_reels"]["Reel 02 - b"])
    assert (after["built_with"]["Reel 01 - a"]
            == before["built_with"]["Reel 01 - a"])


def test_promotion_renames_and_refusal_drops_the_per_reel_stamps(tmp_path):
    """Staging names become final names; refused staging leaves no
    baseline behind. Either half missing and the stamp points at a
    timeline that does not exist, or haunts one that was refused."""
    from library.tools import plan_provenance as pp

    review = tmp_path / "review"
    review.mkdir()
    pp.write_provenance(str(review), str(_plan_file(tmp_path)),
                        ["Reel 01 - a (rebuild staging)", "Reel 02 - b"])

    pp.rename_reel_entries(
        str(review), {"Reel 01 - a (rebuild staging)": "Reel 01 - a"})
    renamed = pp.read_provenance(str(review))
    assert "Reel 01 - a" in renamed["built_at_reels"]
    assert "Reel 01 - a (rebuild staging)" not in renamed["built_at_reels"]
    assert "Reel 01 - a" in renamed["built_with"]
    assert renamed["built_at_reels"]["Reel 02 - b"]

    pp.drop_reel_entries(str(review), ["Reel 02 - b"])
    dropped = pp.read_provenance(str(review))
    assert "Reel 02 - b" not in dropped["built_at_reels"]
    assert "Reel 02 - b" not in dropped["built_with"]
    assert "Reel 02 - b" not in dropped["built_reels"]


def test_reel_code_hash_covers_the_builder_and_both_reel_steps(tmp_path):
    """The digest must move when the producing code moves - a hash of
    nothing in particular invalidates nothing in particular."""
    from library.tools import plan_provenance as pp

    assert "library/tools/reel_build.py" in pp.REEL_BUILD_CODE_FILES
    assert "library/steps/step_7_01_build_reels" in pp.REEL_BUILD_CODE_FILES
    assert "library/steps/step_7_02_verify_reels" in pp.REEL_BUILD_CODE_FILES

    digest = pp.reel_code_hash()
    assert digest and len(digest) == 64
    # A tree with no engine in it records the absence, never a hollow
    # digest that would match any other absence.
    assert pp.reel_code_hash(repo_root=str(tmp_path)) is None


# ── Declared-asset digests: the bytes at build time ───────────────

def test_asset_hashes_are_replaced_whole_when_provided(tmp_path):
    """The mapping is this build's declaration set, authoritative now -
    a removed declaration must not linger as a digest of nothing."""
    from library.tools import plan_provenance as pp

    review = tmp_path / "review"
    review.mkdir()
    plan = _plan_file(tmp_path)
    pp.write_provenance(str(review), str(plan), ["Reel 01 - a"],
                        asset_hashes={"/x/logo.mov": "aaa"})
    assert (pp.read_provenance(str(review))["asset_hashes"]
            == {"/x/logo.mov": "aaa"})

    pp.write_provenance(str(review), str(plan), ["Reel 01 - a"],
                        asset_hashes={"/x/other.mov": "bbb"})
    assert (pp.read_provenance(str(review))["asset_hashes"]
            == {"/x/other.mov": "bbb"})


def test_asset_hashes_survive_a_caller_with_no_declaration_set(tmp_path):
    """None keeps what is there: an older caller must not empty the
    record for not knowing about it."""
    from library.tools import plan_provenance as pp

    review = tmp_path / "review"
    review.mkdir()
    plan = _plan_file(tmp_path)
    pp.write_provenance(str(review), str(plan), ["Reel 01 - a"],
                        asset_hashes={"/x/logo.mov": "aaa"})
    pp.write_provenance(str(review), str(plan), ["Reel 02 - b"])
    assert (pp.read_provenance(str(review))["asset_hashes"]
            == {"/x/logo.mov": "aaa"})


def test_a_new_plan_drops_old_asset_digests_with_everything_else(tmp_path):
    """Digests describe bytes a plan was built against; a new plan's
    reels were not built against those bytes."""
    from library.tools import plan_provenance as pp

    review = tmp_path / "review"
    review.mkdir()
    plan = _plan_file(tmp_path)
    pp.write_provenance(str(review), str(plan), ["Reel 01 - a"],
                        asset_hashes={"/x/logo.mov": "aaa"})
    before = pp.read_provenance(str(review))
    _plan_file(tmp_path, '{"moments": [1]}')
    pp.write_provenance(str(review), str(plan), ["Reel 02 - b"])
    after = pp.read_provenance(str(review))
    assert after["asset_hashes"] == {}
    # The old reel's stamps describe a plan that no longer exists;
    # the new build's own reel is stamped fresh.
    assert "Reel 01 - a" not in after["built_at_reels"]
    assert "Reel 01 - a" not in after["built_with"]
    assert after["built_at_reels"]["Reel 02 - b"]
    assert after["built_with"]["Reel 02 - b"]
    assert before["built_at_reels"]["Reel 01 - a"]
