"""Tests for plan provenance: archival, identity and the refusal check.

The critical test is ``test_mismatched_plan_refuses``: it hands
the verifier a plan whose content hash does not match the provenance
record and asserts the verifier produces a PLAN-MISMATCH error instead
of meaningless F1-F11 findings.

``tests/test_plan_provenance.py``.
"""

from __future__ import annotations

import json
import pytest

from library.tools.plan_provenance import (
    assert_not_editor_timeline,
    begin_timeline_inventory,
    check_reels_in_provenance,
    finish_timeline_inventory,
    protected_timeline_names,
    record_timeline_snapshot,
    read_provenance,
    write_provenance,
)
from library.tools.reel_conformance_verifier import (
    FindingClass,
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


class TestTimelinePreservation:
    def test_first_inventory_protects_timelines_outside_the_ren_plan(
            self, tmp_path):
        review = tmp_path / "review"
        review.mkdir()
        inventory = [
            {"name": "Reel 01", "unique_id": "ren-1", "settings": {}},
            {"name": "Captain's selects", "unique_id": "user-1",
             "settings": {}},
            {"name": "Reel 01 (archived round 004)",
             "unique_id": "user-archive", "settings": {}},
        ]

        begin_timeline_inventory(
            str(review), "first build", inventory,
            ren_created_names={"Reel 01"})

        assert protected_timeline_names(str(review), inventory) == {
            "Captain's selects", "Reel 01 (archived round 004)"}

    def test_new_and_renamed_timelines_are_protected(self, tmp_path):
        review = tmp_path / "review"
        review.mkdir()
        baseline = [{"name": "Reel 01", "unique_id": "ren-1",
                     "settings": {"timelineFrameRate": "23.976"}}]
        operation = begin_timeline_inventory(str(review), "baseline",
                                             baseline,
                                             ren_created_names={"Reel 01"})
        finish_timeline_inventory(str(review), operation, baseline)

        live = [{"name": "Captain Cut", "unique_id": "user-1",
                 "settings": {"timelineFrameRate": "23.976"}},
                {"name": "Reel 01 renamed by editor", "unique_id": "ren-1",
                 "settings": {"timelineFrameRate": "23.976"}}]
        operation = begin_timeline_inventory(str(review), "next build", live)

        assert protected_timeline_names(str(review), live) == {
            "Captain Cut", "Reel 01 renamed by editor"}
        with pytest.raises(RuntimeError, match="created by the editor"):
            assert_not_editor_timeline(
                str(review), _Timeline("Captain Cut", "user-1"))
        finish_timeline_inventory(str(review), operation, live)

    def test_ren_snapshot_history_does_not_replace_last_known(self, tmp_path):
        review = tmp_path / "review"
        review.mkdir()
        first = {"timeline": {"name": "Reel 01"}, "items": [],
                 "markers": []}
        before = {"timeline": {"name": "Reel 01", "start_frame": 12},
                  "items": [], "markers": []}
        record_timeline_snapshot(str(review), "Reel 01", first,
                                 action="build_promotion")
        record_timeline_snapshot(str(review), "Reel 01", before,
                                 action="before_variant_choice",
                                 last_known=False)

        entry = read_provenance(str(review))["ren_timeline_snapshots"][
            "Reel 01"]
        assert entry["snapshot"] == first
        assert [item["action"] for item in entry["history"]] == [
            "build_promotion", "before_variant_choice"]

    def test_plan_write_preserves_editor_timeline_ownership(self, tmp_path):
        review = tmp_path / "review"
        review.mkdir()
        baseline = [{"name": "Reel 01", "unique_id": "ren-1",
                     "settings": {}}]
        operation = begin_timeline_inventory(
            str(review), "baseline", baseline,
            ren_created_names={"Reel 01"})
        finish_timeline_inventory(str(review), operation, baseline)
        live = baseline + [{"name": "Captain Cut", "unique_id": "user-1",
                            "settings": {}}]
        begin_timeline_inventory(str(review), "next build", live)
        plan = _write_plan(tmp_path / "proposal.json")

        write_provenance(str(review), str(plan), ["Reel 01"])

        assert protected_timeline_names(str(review), live) == {"Captain Cut"}


class _Timeline:
    def __init__(self, name, unique_id):
        self.name = name
        self.unique_id = unique_id

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.unique_id


# ── check_reels_in_provenance ────────────────────────────────────────

class TestCheckReelsInProvenance:
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
    # The per-reel build stamps follow the same merge rule.
    untouched = "Reel 01 - slug-1"
    assert (after["built_at_reels"][untouched]
            == before["built_at_reels"][untouched])
    assert (after["built_at_reels"]["Reel 07 - slug-7"]
            >= before["built_at_reels"]["Reel 07 - slug-7"])
    assert after["built_with"][untouched] == before["built_with"][untouched]


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

    # A v0 hash (a bare hex digest from the old hollow hasher) is
    # treated as absent, so grading refuses rather than falsely passes.
    ok, why = pp.check_captions_match_provenance(
        "Reel 01", cards, {"caption_hashes": {"Reel 01": "a" * 64}})
    assert ok is False
    assert "hollow" in why or "v0" in why, why


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
    """A restyle is not a different caption plan (AGENTS.md 14); a text
    change is."""
    from library.tools import plan_provenance as pp

    a = _Card(0.0, 24, "hello")
    b = _Card(0.0, 24, "hello")
    b.speaker, b.font_size = "Craig", 99
    assert pp.caption_content_hash([a]) == pp.caption_content_hash([b])

    # ...but a change to one card's text alone is.
    cards_a = [_Card(0.0, 24, "hello world"), _Card(1.0, 48, "foo bar")]
    cards_b = [_Card(0.0, 24, "hello world"), _Card(1.0, 48, "foo baz")]
    assert pp.caption_content_hash(cards_a) != pp.caption_content_hash(cards_b)


# ── Property tests: the hash cannot be hollow again ──────────────────

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

    # None keeps what is there: an older caller must not empty the
    # record for not knowing about it.
    pp.write_provenance(str(review), str(plan), ["Reel 02 - b"])
    assert (pp.read_provenance(str(review))["asset_hashes"]
            == {"/x/other.mov": "bbb"})
