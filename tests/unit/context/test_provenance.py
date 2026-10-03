"""Tests for plan provenance: archival, identity and the refusal check.

The critical test is ``test_mismatched_plan_refuses``: it hands
the verifier a plan whose content hash does not match the provenance
record and asserts the verifier produces a PLAN-MISMATCH error instead
of meaningless F1-F11 findings.

``tests/unit/context/test_provenance.py``.
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
from library.tools.project_layout import Area, ProjectLayout
from library.tools.provenance import (
    PRODUCER_OPERATION,
    PRODUCER_STEP,
    ProvenanceError,
    ProvenanceLedger,
)
from library.tools.plan_provenance import (
    check_footage_binding_matches_provenance,
    footage_binding_hash,
)
from pathlib import Path
from library.tools.plan_provenance import (
    SNAPSHOT_PROVENANCE_KEY,
    built_from_snapshot,
    is_snapshot_superseded,
    record_snapshot_supersession,
    rename_reel_entries,
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
    b.speaker, b.font_size = "SpeakerTwo", 99
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


# --------------------------------------------------------------------------
# From test_provenance_operations.py
#
# An OPERATION that writes a file is recorded as truthfully as a step is.
#
# Provenance attributes a file by diffing a directory snapshot, so it
# generalises to operations. The properties pinned are the REFUSALS, each
# paired with the same call succeeding once the missing thing is supplied.
# History: docs/evidence/provenance.md

@pytest.fixture
def project(tmp_path):
    """A project under tmp_path. Never a real one (AGENTS.md 8)."""
    ProjectLayout(tmp_path).ensure()
    (tmp_path / "project.yaml").write_text("slug: t\n", encoding="utf-8")
    return tmp_path


def _write(project, area, name, text="x"):
    path = ProjectLayout(project).write_path(area, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _observe(ledger, project, area, name, **kwargs):
    """Watch one file appear, the way the runner watches a step."""
    before = ledger.snapshot()
    _write(project, area, name)
    return ledger.observe(after=ledger.snapshot(), before=before, **kwargs)


# ── The generalisation ──────────────────────────────────────────────

def test_an_operation_is_recorded_as_an_operation(project):
    ledger = ProvenanceLedger(project, step_ids=["plan_subtitles"],
                              operation_ids=["subtitles.plan"])
    records = _observe(ledger, project, Area.SUBTITLE_SEGMENTS, "seg.mov",
                       step_id="plan_subtitles", run_id="r1",
                       operation_id="subtitles.plan")

    assert len(records) == 1
    rec = records[0]
    assert rec.producer_kind == PRODUCER_OPERATION
    assert rec.operation_id == "subtitles.plan"
    assert rec.step_id == "plan_subtitles"
    assert rec.is_attributed


def test_a_step_is_still_recorded_as_a_step(project):
    ledger = ProvenanceLedger(project)
    records = _observe(ledger, project, Area.PROSODY, "p.json",
                       step_id="prosody_analysis", run_id="r1")

    assert records[0].producer_kind == PRODUCER_STEP
    assert records[0].operation_id is None
    assert records[0].producer == "prosody_analysis"


# ── The refusals, each paired with the passing case ─────────────────

def test_an_unverifiable_producer_is_refused_by_name(project):
    """Each row is a producer the ledger cannot CHECK - no owning step,
    an undeclared operation, an unknown node, or a ledger that declares
    nothing at all (once off by default: `if self.operation_ids and ...`
    checked nothing). The passing mirror is
    `test_an_operation_is_recorded_as_an_operation`."""
    declared = dict(step_ids=["plan_subtitles"],
                    operation_ids=["subtitles.plan"])
    cases = [
        (declared, "", "subtitles.plan", ["subtitles.plan", "owning step"]),
        (declared, "plan_subtitles", "subtitles.invented",
         ["subtitles.invented"]),
        (declared, "not_a_node", "subtitles.plan", ["not_a_node"]),
        ({}, "plan_subtitles", "totally.made.up",
         ["no declared operations", "operations.names()"]),
    ]
    for n, (declarations, step_id, operation_id, words) in enumerate(cases):
        ledger = ProvenanceLedger(project, **declarations)
        with pytest.raises(ProvenanceError) as exc:
            _observe(ledger, project, Area.SUBTITLE_SEGMENTS, f"r{n}.mov",
                     step_id=step_id, run_id="r1",
                     operation_id=operation_id)
        for word in words:
            assert word in str(exc.value), (operation_id, str(exc.value))


# ── The receipt a capability run leaves ─────────────────────────────

def test_running_a_capability_records_its_id_and_derives_the_node(
        project, monkeypatch):
    """The defect: no caller ever passed `operation_id`, so a capability
    run from the CLI or the reels process wrote files provenance never
    attributed. The id is the caller's only input; the node is derived."""
    from library.tools import operations

    def writes_a_segment(self, project_folder, scope=None, **overrides):
        _write(project, Area.SUBTITLE_SEGMENTS, "seg.mov")
        return operations.OperationResult(
            operation=self.name, legacy_node=self.legacy_node,
            scope=scope, status=operations.COMPLETED, payload={})

    monkeypatch.setattr(operations.Operation, "execute", writes_a_segment)
    assert operations.main(["subtitles.render", "--project",
                            str(project)]) == 0

    rows = ProvenanceLedger(project)._read("artifacts.jsonl")
    assert [(r["operation_id"], r["step_id"], r["producer_kind"])
            for r in rows] == [
        ("subtitles.render", "render_subtitles", PRODUCER_OPERATION)]


# --------------------------------------------------------------------------
# From test_footage_binding.py
#
# Tests for footage-binding provenance: the durable link between
# captions and the footage they were computed against.
#
# Proves BOTH directions:
# - A spine whose footage changes produces a DIFFERENT hash (defect fires).
# - A spine whose footage stays the same produces the SAME hash (silent).
#
# ``tests/unit/context/test_provenance.py``.

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


# --------------------------------------------------------------------------
# From test_snapshot_supersession.py
#
# Snapshot supersession: which snapshot a live timeline came from.
#
# Gap G6 (2026-09-19): the `__batch-1050_`, `__final_` and base snapshots
# disagreed on Reel 09's bound (36510 against 36490) with nothing on disk
# saying which was authoritative, and a check graded the stale one.  The
# record under test is `plan_provenance.snapshot_provenance`, written by
# `record_snapshot_supersession` at promotion time and read back through
# `built_from_snapshot` (which snapshot a live timeline names) and
# `is_snapshot_superseded` (which snapshots read as superseded).
#
# ``library/tools/plan_provenance.py``.

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


def _write_plan_2(path: Path) -> Path:
    doc = {
        "format": "reel_proposal/1",
        "derived_from": {},
        "instruction": "test",
        "moment_count": 1,
        "moments": [
            {"number": 9, "slug": "test-moment", "reason": "test",
             "timeline_start": 690.0, "timeline_end": 710.0,
             "approval": "approved", "speakers": ["SpeakerOne"]},
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
        plan = _write_plan_2(tmp_path / "plan.json")
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
        plan = _write_plan_2(tmp_path / "plan.json")
        write_provenance(str(review), str(plan), [staging])
        record_snapshot_supersession(str(review), {staging: str(snap)})
        rename_reel_entries(str(review), {staging: REEL_09})
        provenance = read_provenance(str(review))
        assert REEL_09 in provenance[SNAPSHOT_PROVENANCE_KEY]
        assert staging not in provenance[SNAPSHOT_PROVENANCE_KEY]
