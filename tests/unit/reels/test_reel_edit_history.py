"""Per-reel edit history: Ren acts and the captain's manual edits, in order.

Regression context: the captain, 2026-10-05, about reels whose rebuilds
fail plan-versus-timeline verification (Reel 28: plan 1978 frames vs
timeline 1403; Reel 26: plan 1022 vs timeline 1104) - manual edits after
the plan left Ren unable to tell a captain's change from an unexplained
divergence. Every test here builds its project under `tmp_path`; none
reaches a real project, Resolve, or the primary checkout.
"""

import json
from pathlib import Path

import pytest

from library.tools import plan_provenance, reel_edit_history as history
from library.tools.reel_conformance_verifier import (
    FindingClass,
    ReelPlan,
    ReelTimeline,
    TimelineItem,
    verify_reel,
)

FPS = 24000 / 1001

FINAL_28 = "Reel 28 - the-closer-that-moved"
FINAL_26 = "Reel 26 - the-opener-that-moved"


def _review_dir(tmp_path):
    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    return str(review)


def _plan_file(tmp_path, name="reel_proposals_v2.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"moments": []}), encoding="utf-8")
    return str(path)


def _item(start_frame, end_frame):
    return TimelineItem(
        track_type="video", track_index=1,
        start_frame=start_frame, end_frame=end_frame,
        duration_frames=end_frame - start_frame,
        source_start_frame=0, source_end_frame=end_frame - start_frame,
        source_file="/m/a.MXF", speaker="SpeakerOne", name="clip")


def _mismatch_plan(name, number, planned_frames):
    plan_seconds = planned_frames / FPS
    return ReelPlan(
        reel_name=name, reel_number=number,
        plan_seconds=plan_seconds,
        plan_frames=round(plan_seconds * FPS, 1),
        span_start=0.0, span_end=plan_seconds,
        placements=(), captions=(), cuts=(),
        keep_ranges=((0.0, plan_seconds),))


def _mismatch_timeline(name, actual_frames):
    return ReelTimeline(
        reel_name=name, fps=FPS, total_frames=actual_frames,
        picture_frames=actual_frames,
        video_items=(_item(0, actual_frames),),
        audio_items=(), caption_items=(), markers={})


def _plan_mismatch_findings(result):
    return [f for f in result.findings
            if f.finding_class == FindingClass.PLAN_MISMATCH
            and f.reel != "(all)"]


class TestAppendOnlyHistory:
    def test_build_promotion_touch_and_manual_edit_order_oldest_first(
            self, tmp_path):
        review = _review_dir(tmp_path)
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN,
            act=history.ACT_BUILD, summary="Ren build from plan abc",
            plan_version={"plan_content_hash": "abc"},
            at="2026-10-01T10:00:00+00:00")
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN,
            act=history.ACT_PROMOTION, summary="Ren promotion (version 1)",
            refs={"version": 1}, at="2026-10-01T10:05:00+00:00")
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN,
            act=history.ACT_TOUCH, summary="Ren touch-up (journal j1)",
            refs={"journal": "j1"}, at="2026-10-01T11:00:00+00:00")
        history.record_manual_edit(
            review, FINAL_28, summary="captain trimmed the closer",
            frame_delta=-575, at="2026-10-02T09:00:00+00:00")

        entries = history.history_for(review, FINAL_28)
        assert [e["act"] for e in entries] == [
            "build", "promotion", "touch", "manual_edit"]
        assert [e["actor"] for e in entries] == [
            "ren", "ren", "ren", "captain"]
        assert entries[0]["plan_version"] == {
            "plan_content_hash": "abc"}
        assert entries[1]["refs"] == {"version": 1}
        assert entries[3]["frame_delta"] == -575

    def test_recording_never_overwrites(self, tmp_path):
        review = _review_dir(tmp_path)
        first = history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN,
            act=history.ACT_BUILD, summary="Ren build from plan abc",
            at="2026-10-01T10:00:00+00:00")
        # The identical act recorded again is the same entry, not two.
        again = history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN,
            act=history.ACT_BUILD, summary="Ren build from plan abc",
            at="2026-10-01T10:00:00+00:00")
        assert again["id"] == first["id"]
        assert len(history.recorded_history(review, FINAL_28)) == 1
        # A later build appends; the first entry stays byte-identical.
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN,
            act=history.ACT_BUILD, summary="Ren build from plan def",
            at="2026-10-03T10:00:00+00:00")
        entries = history.recorded_history(review, FINAL_28)
        assert len(entries) == 2
        assert entries[0]["id"] == first["id"]
        assert entries[0]["summary"] == "Ren build from plan abc"

    def test_histories_are_per_reel(self, tmp_path):
        review = _review_dir(tmp_path)
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN,
            act=history.ACT_BUILD, summary="build 28",
            at="2026-10-01T10:00:00+00:00")
        assert history.history_for(review, FINAL_26) == []
        assert len(history.history_for(review, FINAL_28)) == 1


class TestBuildAndPromotionHooks:
    def test_write_provenance_files_build_entries_with_plan_version(
            self, tmp_path):
        review = _review_dir(tmp_path)
        plan = _plan_file(tmp_path)
        plan_provenance.write_provenance(
            review, plan, [FINAL_28],
            caption_hashes={FINAL_28: "v1:caption"},
            footage_binding_hashes={FINAL_28: "v1:binding"})
        entries = history.recorded_history(review, FINAL_28)
        assert len(entries) == 1
        build = entries[0]
        assert (build["actor"], build["act"]) == ("ren", "build")
        assert (build["plan_version"]["plan_content_hash"]
                == plan_provenance.plan_content_hash(plan))
        assert build["plan_version"]["caption_hash"] == "v1:caption"
        assert build["plan_version"]["footage_binding_hash"] == "v1:binding"

    def test_partial_rebuild_merges_and_history_survives_plan_change(
            self, tmp_path):
        review = _review_dir(tmp_path)
        plan = _plan_file(tmp_path)
        plan_provenance.write_provenance(review, plan, [FINAL_28])
        plan_provenance.write_provenance(review, plan, [FINAL_26])
        assert len(history.recorded_history(review, FINAL_28)) == 1
        assert len(history.recorded_history(review, FINAL_26)) == 1
        # A different plan drops plan-scoped entries but carries the
        # history whole: a plan change must not delete the account.
        other = _plan_file(tmp_path, name="other_plan.json")
        (tmp_path / "other_plan.json").write_text(
            json.dumps({"moments": [{"number": 1}]}), encoding="utf-8")
        plan_provenance.write_provenance(review, other, [FINAL_28])
        entries = history.recorded_history(review, FINAL_28)
        assert len(entries) == 2
        assert entries[0]["summary"].startswith("Ren build from plan")
        assert entries[0]["plan_version"]["plan_content_hash"] != (
            entries[1]["plan_version"]["plan_content_hash"])

    def test_rename_moves_history_staging_to_final(self, tmp_path):
        review = _review_dir(tmp_path)
        plan = _plan_file(tmp_path)
        staging = FINAL_28 + " (rebuild staging)"
        plan_provenance.write_provenance(review, plan, [staging])
        assert history.recorded_history(review, staging) != []
        plan_provenance.rename_reel_entries(review, {staging: FINAL_28})
        assert history.recorded_history(review, staging) == []
        entries = history.recorded_history(review, FINAL_28)
        assert len(entries) == 1
        assert entries[0]["act"] == "build"


class TestManualEditDetection:
    def test_detect_files_live_difference_and_dedupes(self, tmp_path):
        review = _review_dir(tmp_path)
        assert history.ren_recorded_digest(review, FINAL_28) is None
        entry = history.detect_manual_edit(
            review, FINAL_28, "live-digest-1",
            summary="captain trimmed the closer", frame_delta=-575)
        assert entry["actor"] == "captain"
        assert entry["refs"]["rows_digest_after"] == "live-digest-1"
        # The same live state detected again files nothing new.
        assert history.detect_manual_edit(
            review, FINAL_28, "live-digest-1",
            summary="captain trimmed the closer")["id"] == entry["id"]
        assert len(history.recorded_history(review, FINAL_28)) == 1
        # A changed live state is a new entry, not a rewrite.
        second = history.detect_manual_edit(
            review, FINAL_28, "live-digest-2", summary="captain moved on")
        assert second["id"] != entry["id"]
        assert len(history.recorded_history(review, FINAL_28)) == 2

    def test_detect_reads_ren_digest_from_versions(self, tmp_path):
        from library.tools.versions import reel_versions

        review = _review_dir(tmp_path)
        reel_versions.record(str(tmp_path), FINAL_28,
                             kind=reel_versions.KIND_BUILD,
                             rows={"video:V1": []})
        act = reel_versions.latest_act(str(tmp_path), FINAL_28)
        assert history.ren_recorded_digest(review, FINAL_28) == (
            act["rows_digest"])
        # Live matching Ren's record files nothing.
        assert history.detect_manual_edit(
            review, FINAL_28, act["rows_digest"],
            summary="nothing moved") is None
        assert history.recorded_history(review, FINAL_28) == []

    def test_editor_change_table_reads_as_manual_history(self, tmp_path):
        review = _review_dir(tmp_path)
        plan_provenance.record_editor_changes(review, FINAL_28, [{
            "id": "ec1", "recorded_at": "2026-10-02T09:00:00+00:00",
            "timeline": FINAL_28, "baseline": "ren_last_known_snapshot",
            "changes": [{"kind": "item_removed",
                         "before": {"track_type": "video",
                                    "track_name": "V1", "name": "closer"},
                         "after": None, "changed": {}}],
            "status": "pending"}])
        entries = history.history_for(review, FINAL_28)
        assert len(entries) == 1
        assert entries[0]["actor"] == "captain"
        assert "removed" in entries[0]["summary"]
        assert (entries[0]["refs"]["editor_change_id"] == "ec1")
        # An explicit entry for the same editor change suppresses the
        # derived twin: one edit, one entry.
        history.record_manual_edit(
            review, FINAL_28, summary="explicit filing",
            editor_change_id="ec1")
        assert len(history.history_for(review, FINAL_28)) == 1


class TestAttribution:
    def _history_28(self):
        build = history.make_entry(
            FINAL_28, actor=history.ACTOR_REN, act=history.ACT_BUILD,
            summary="Ren build from plan deadbeef...",
            plan_version={"plan_content_hash": "deadbeef"},
            at="2026-10-01T10:00:00+00:00")
        manual = history.make_entry(
            FINAL_28, actor=history.ACTOR_CAPTAIN,
            act=history.ACT_MANUAL_EDIT,
            summary="captain trimmed the closer by 575 frames",
            frame_delta=-575, at="2026-10-02T09:00:00+00:00")
        return [build, manual]

    def test_matching_delta_attributes(self):
        edit = history.attribute_plan_mismatch(self._history_28(), -575)
        assert edit["summary"].startswith("captain trimmed")

    def test_contradicting_delta_does_not_attribute(self):
        assert history.attribute_plan_mismatch(
            self._history_28(), +10) is None

    def test_manual_edit_before_last_build_does_not_attribute(self):
        build = history.make_entry(
            FINAL_28, actor=history.ACTOR_REN, act=history.ACT_BUILD,
            summary="Ren rebuild from plan cafe...",
            at="2026-10-03T10:00:00+00:00")
        stale_manual = history.make_entry(
            FINAL_28, actor=history.ACTOR_CAPTAIN,
            act=history.ACT_MANUAL_EDIT,
            summary="captain's older edit, superseded by the rebuild",
            frame_delta=-575, at="2026-10-02T09:00:00+00:00")
        assert history.attribute_plan_mismatch(
            [stale_manual, build], -575) is None

    def test_nothing_recorded_attributes_nothing(self):
        assert history.attribute_plan_mismatch([], -575) is None
        build = self._history_28()[0]
        assert history.attribute_plan_mismatch([build], -575) is None


class TestVerificationNamesTheEdit:
    """Reel 28 (plan 1978 vs timeline 1403) and Reel 26 (plan 1022 vs
    timeline 1104): the captain's real regression cases."""

    @pytest.mark.parametrize(
        "final,number,planned,actual,delta",
        [(FINAL_28, 28, 1978, 1403, -575),
         (FINAL_26, 26, 1022, 1104, +82)])
    def test_mismatch_without_history_is_an_unexplained_error(
            self, final, number, planned, actual, delta):
        result = verify_reel(
            _mismatch_plan(final, number, planned),
            _mismatch_timeline(final, actual))
        findings = _plan_mismatch_findings(result)
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert findings[0].detail["delta_frames"] == delta

    @pytest.mark.parametrize(
        "final,number,planned,actual,delta",
        [(FINAL_28, 28, 1978, 1403, -575),
         (FINAL_26, 26, 1022, 1104, +82)])
    def test_mismatch_with_recorded_edit_names_it(
            self, final, number, planned, actual, delta):
        build = history.make_entry(
            final, actor=history.ACTOR_REN, act=history.ACT_BUILD,
            summary="Ren build from plan deadbeef...",
            plan_version={"plan_content_hash": "deadbeef"},
            at="2026-10-01T10:00:00+00:00")
        manual = history.make_entry(
            final, actor=history.ACTOR_CAPTAIN,
            act=history.ACT_MANUAL_EDIT,
            summary="captain re-cut the reel by hand",
            frame_delta=delta, at="2026-10-02T09:00:00+00:00")
        result = verify_reel(
            _mismatch_plan(final, number, planned),
            _mismatch_timeline(final, actual),
            edit_history=[build, manual])
        findings = _plan_mismatch_findings(result)
        assert len(findings) == 1
        named = findings[0]
        assert named.severity == "warning"
        assert manual["id"] in named.message
        assert "manual" in named.message.lower()
        assert "captain" in named.message.lower()
        assert named.detail["attributed_to"] == manual["id"]
        assert named.detail["delta_frames"] == delta


class TestBackfillAndDrift:
    def test_backfill_derives_versions_and_editor_changes(self, tmp_path):
        from library.tools.versions import reel_versions

        review = _review_dir(tmp_path)
        reel_versions.record(str(tmp_path), FINAL_28,
                             kind=reel_versions.KIND_BUILD,
                             rows={"video:V1": []}, round=7)
        reel_versions.record(str(tmp_path), FINAL_28,
                             kind=reel_versions.KIND_TOUCH,
                             rows={"video:V1": []}, journal="j1")
        plan_provenance.record_editor_changes(review, FINAL_28, [{
            "id": "ec9", "recorded_at": "2026-10-02T09:00:00+00:00",
            "timeline": FINAL_28, "changes": [], "status": "carried"}])
        added = history.backfill(review, FINAL_28)
        assert added == 3
        entries = history.recorded_history(review, FINAL_28)
        by_act = {e["act"]: e for e in entries}
        assert set(by_act) == {"build", "touch", "manual_edit"}
        assert all(e["backfilled"] for e in entries)
        assert by_act["build"]["refs"]["round"] == 7
        assert by_act["touch"]["refs"]["journal"] == "j1"
        assert (by_act["manual_edit"]["refs"]["editor_change_id"]
                == "ec9")
        # Re-running backfills nothing.
        assert history.backfill(review, FINAL_28) == 0

    def test_drift_findings_file_drifted_reels_only(self, tmp_path):
        review = _review_dir(tmp_path)
        report = {"reels": {
            FINAL_28: {"compared": True, "drifted": True,
                       "line": f"{FINAL_28}: 3 of 10 moved"},
            FINAL_26: {"compared": True, "drifted": False,
                       "line": f"{FINAL_26}: unmoved"},
            "Reel 09 - x": {"compared": False,
                            "line": "Reel 09 - x: unreadable"}}}
        assert history.record_drift_findings(review, report) == 1
        entries = history.recorded_history(review, FINAL_28)
        assert len(entries) == 1
        assert entries[0]["summary"].startswith("drift check:")
        assert history.recorded_history(review, FINAL_26) == []
        # A second identical drift report files nothing new.
        assert history.record_drift_findings(review, report) == 0


class TestParentChain:
    """F-01: every edit names the parent it was applied against.

    The defect: entries carried no parent, so a reel's history was an
    unordered set - given a timeline state, Ren could not answer which
    edit produced it, and a rebuild landing between two touches could
    not say which touch's parent it built on.
    """

    STAMPS = ["2026-10-01T10:00:00+00:00", "2026-10-01T10:05:00+00:00",
              "2026-10-02T09:00:00+00:00", "2026-10-02T10:00:00+00:00"]

    def _chain(self, review):
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN,
            act=history.ACT_BUILD, summary="Ren build from plan abc",
            at=self.STAMPS[0])
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN, act=history.ACT_TOUCH,
            summary="Ren touch-up (journal j1)", at=self.STAMPS[1])
        history.record_manual_edit(
            review, FINAL_28, summary="captain trimmed the closer",
            frame_delta=-575, at=self.STAMPS[2])
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN, act=history.ACT_TOUCH,
            summary="Ren touch-up (journal j2)", at=self.STAMPS[3])

    def test_each_entry_names_the_parent_it_was_applied_against(
            self, tmp_path):
        review = _review_dir(tmp_path)
        self._chain(review)
        entries = history.recorded_history(review, FINAL_28)
        assert [e["act"] for e in entries] == [
            "build", "touch", "manual_edit", "touch"]
        assert entries[0]["parent_entry_id"] is None
        for previous, current in zip(entries, entries[1:]):
            assert current["parent_entry_id"] == previous["id"]
        # The manual edit's parent is the last Ren act (the touch), not
        # some other manual edit.
        assert entries[2]["parent_entry_id"] == entries[1]["id"]
        assert entries[1]["actor"] == history.ACTOR_REN

    def test_history_for_reconstructs_the_chain(self, tmp_path):
        review = _review_dir(tmp_path)
        self._chain(review)
        entries = history.history_for(review, FINAL_28)
        assert [e["act"] for e in entries] == [
            "build", "touch", "manual_edit", "touch"]
        for previous, current in zip(entries, entries[1:]):
            assert current["parent_entry_id"] == previous["id"]

    def test_history_chain_parents_a_parentless_entry(self, tmp_path):
        review = _review_dir(tmp_path)
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN,
            act=history.ACT_BUILD, summary="Ren build from plan abc",
            at=self.STAMPS[0])
        # A parentless entry (as pre-change code wrote them) filed
        # between two parented ones.
        orphan = history.make_entry(
            FINAL_28, actor=history.ACTOR_REN, act=history.ACT_TOUCH,
            summary="Ren touch-up (journal j1)", at=self.STAMPS[1])
        orphan.pop("parent_entry_id")
        path = Path(review) / "plan_provenance.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc[history.HISTORY_KEY][FINAL_28].append(orphan)
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN, act=history.ACT_TOUCH,
            summary="Ren touch-up (journal j2)", at=self.STAMPS[3])
        chain = history.history_chain(review, FINAL_28)
        assert [e["act"] for e in chain] == ["build", "touch", "touch"]
        assert chain[0]["parent_entry_id"] is None
        for previous, current in zip(chain, chain[1:]):
            assert current["parent_entry_id"] == previous["id"]

    def test_backfill_parents_derives_missing_parents_by_ordering(
            self, tmp_path):
        review = _review_dir(tmp_path)
        # Entries as pre-change code wrote them: no parent_entry_id.
        path = Path(review) / "plan_provenance.json"
        filed = []
        for index, stamp in enumerate(self.STAMPS[:3]):
            entry = history.make_entry(
                FINAL_28, actor=history.ACTOR_REN,
                act=history.ACT_BUILD, summary=f"build {index}",
                at=stamp)
            entry.pop("parent_entry_id")
            filed.append(entry)
        path.write_text(json.dumps(
            {history.HISTORY_KEY: {FINAL_28: filed}}, indent=2),
            encoding="utf-8")
        updated = history.backfill_parents(review, FINAL_28)
        assert updated == 2
        entries = history.recorded_history(review, FINAL_28)
        assert entries[0]["parent_entry_id"] is None
        for previous, current in zip(entries, entries[1:]):
            assert current["parent_entry_id"] == previous["id"]
        # Idempotent: a second run changes nothing.
        assert history.backfill_parents(review, FINAL_28) == 0

    def test_write_provenance_chains_builds(self, tmp_path):
        review = _review_dir(tmp_path)
        plan = _plan_file(tmp_path)
        plan_provenance.write_provenance(review, plan, [FINAL_28])
        first = history.recorded_history(review, FINAL_28)
        assert len(first) == 1
        assert first[0]["parent_entry_id"] is None
        plan_provenance.write_provenance(review, plan, [FINAL_28])
        second = history.recorded_history(review, FINAL_28)
        assert len(second) == 2
        assert second[0]["parent_entry_id"] is None
        assert second[1]["parent_entry_id"] == first[0]["id"]


class TestManualEditReason:
    """A detected edit is a fact without a motive until the captain's own
    words are filed against it (F-05), and the note that carried them is
    linked to the edit it explains (F-12).

    Regression context: the captain hand-edits a timeline, Ren detects
    the difference and files it, and nothing records WHY - so a later
    run re-derives a plan that contradicts his unrecorded intent, and a
    note that says "I moved this clip because..." is not linked to the
    edit it explains.
    """

    def test_detected_edit_without_reason_is_intent_unknown(self, tmp_path):
        review = _review_dir(tmp_path)
        history.record_manual_edit(
            review, FINAL_28, summary="captain trimmed the closer",
            frame_delta=-575, at="2026-10-02T09:00:00+00:00")
        unexplained = history.unexplained_manual_edits(review, FINAL_28)
        assert len(unexplained) == 1
        assert unexplained[0]["summary"] == "captain trimmed the closer"
        lines = history.intent_unknown_lines(review, FINAL_28)
        assert len(lines) == 1
        assert "intent unknown" in lines[0]
        assert FINAL_28 in lines[0]

    def test_filed_reason_carries_through_history_and_attribution(
            self, tmp_path):
        review = _review_dir(tmp_path)
        history.record_entry(
            review, FINAL_28, actor=history.ACTOR_REN, act=history.ACT_BUILD,
            summary="Ren build", at="2026-10-01T10:00:00+00:00")
        entry = history.record_manual_edit(
            review, FINAL_28, summary="captain trimmed the closer",
            frame_delta=-575, at="2026-10-02T09:00:00+00:00")
        filed = history.file_manual_edit_reason(
            review, FINAL_28, manual_edit_id=entry["id"],
            reason="the closer was dragging the ending")
        assert filed is not None
        assert filed["act"] == history.ACT_MANUAL_EDIT_REASON
        assert (filed["refs"]["reason"]
                == "the closer was dragging the ending")
        # The history carries the reason.
        entries = history.history_for(review, FINAL_28)
        assert history.reason_for(entries, entry["id"]) == (
            "the closer was dragging the ending")
        # The detection entry itself is never rewritten: the reason is a
        # new entry, and the captain's edit stands exactly as measured.
        detection = [e for e in entries if e["id"] == entry["id"]][0]
        assert "reason" not in detection["refs"]
        # Attribution cites the captain's own words.
        attributed = history.attribute_plan_mismatch(entries, -575)
        assert attributed is not None
        assert attributed["id"] == entry["id"]
        assert (attributed["reason"]
                == "the closer was dragging the ending")
        # The edit is no longer unexplained.
        assert history.unexplained_manual_edits(review, FINAL_28) == []

    def test_reason_linked_by_editor_change_id(self, tmp_path):
        review = _review_dir(tmp_path)
        plan_provenance.record_editor_changes(review, FINAL_28, [{
            "id": "ec1", "recorded_at": "2026-10-02T09:00:00+00:00",
            "timeline": FINAL_28, "changes": [], "status": "pending"}])
        filed = history.file_manual_edit_reason(
            review, FINAL_28, editor_change_id="ec1",
            reason="I moved it because the framing was off")
        assert filed is not None
        entries = history.history_for(review, FINAL_28)
        # The derived entry is explained by the reason filed against the
        # editor-change id the replace guard recorded.
        assert history.reason_for(entries, "ec1") == (
            "I moved it because the framing was off")
        assert history.unexplained_manual_edits(review, FINAL_28) == []

    def test_note_that_produces_edit_is_linked(self, tmp_path):
        review = _review_dir(tmp_path)
        entry = history.record_manual_edit(
            review, FINAL_28, summary="captain moved the closer",
            note_id="note_abc123", at="2026-10-02T09:00:00+00:00")
        assert entry["refs"]["note_id"] == "note_abc123"
        entries = history.history_for(review, FINAL_28)
        assert entries[0]["refs"]["note_id"] == "note_abc123"

    def test_reason_carries_the_note_that_prompted_it(self, tmp_path):
        review = _review_dir(tmp_path)
        entry = history.record_manual_edit(
            review, FINAL_28, summary="captain trimmed the closer",
            at="2026-10-02T09:00:00+00:00")
        filed = history.file_manual_edit_reason(
            review, FINAL_28, manual_edit_id=entry["id"],
            reason="the closer was dragging", note_id="note_xyz")
        assert filed is not None
        assert filed["refs"]["note_id"] == "note_xyz"

    def test_filing_a_reason_never_blocks_or_alters_the_edit(self, tmp_path):
        review = _review_dir(tmp_path)
        entry = history.record_manual_edit(
            review, FINAL_28, summary="captain trimmed the closer",
            frame_delta=-575, at="2026-10-02T09:00:00+00:00")
        before = history.recorded_history(review, FINAL_28)
        # An empty reason files nothing and raises nothing.
        assert history.file_manual_edit_reason(
            review, FINAL_28, manual_edit_id=entry["id"], reason="") is None
        # A history that cannot be written files nothing and raises nothing:
        # the reason is instrumentation, never a blocker.
        blocker = tmp_path / "afile"
        blocker.write_text("not a directory", encoding="utf-8")
        assert history.file_manual_edit_reason(
            str(blocker), FINAL_28,
            manual_edit_id=entry["id"], reason="why") is None
        # The edit stands exactly as measured.
        assert history.recorded_history(review, FINAL_28) == before
