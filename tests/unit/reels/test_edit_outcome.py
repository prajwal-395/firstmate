"""PROD-008: did the captain take the generated edit, or did he change it?

Regression context: the punch list's PROD-008 asked for product-level
feedback and outcome tracking - whether a user accepted or modified a
generated edit, what kind of correction it was, how much was repaired,
how much time was saved, and whether the correction was an objective
system failure or a stylistic disagreement. Every part of a hand
correction was recorded (the edit history, the detected change, the
adopted version, the delivery acceptance) and none of it was joined
into an outcome.

Every test here builds its project under `tmp_path`; none reaches a
real project, Resolve, or the primary checkout.

The tests name DEFECTS, not features: each one says what would break
if the behaviour it pins were lost.
"""

import json
import os

import pytest

from library.tools import edit_outcome, manual_edit_intent, plan_provenance
from library.tools import reel_edit_history as history
from library.tools.versions import reel_versions

REEL = "Reel 26"
FPS = 25


def _project(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    (root / "project.yaml").write_text(
        f"source:\n  fps: {FPS}\n", encoding="utf-8")
    return root


def _review(root):
    return os.path.join(str(root), "pipeline_output", "review")


def _rows(frames=250):
    return {"video:V1": {
        "media_type": "video", "index": 1, "name": "V1",
        "items": [{"name": "a.mov", "start": 0, "end": frames,
                   "duration": frames, "enabled": True}],
        "count": 1, "frames": frames}}


def _build(root, *, at=None):
    """A Ren build of the reel: the edit the captain was handed."""
    entry = reel_versions.record(str(root), REEL, kind=reel_versions.KIND_BUILD,
                                 rows=_rows(), at=at)
    return entry


def _item(**overrides):
    item = {"track_type": "video", "track_name": "V1", "name": "a.mov",
            "source_identity": "a.mov", "source_in_frame": 0,
            "source_out_frame": 250, "record_in": 0, "record_out": 250,
            "duration": 250, "enabled": True, "transform": {"Zoom": 1.0}}
    item.update(overrides)
    return item


def _record_change(root, *, change_id="change-1", before=None, after=None,
                   changed=None, kind="item_changed"):
    """The DETECTED difference: what the replace guard recorded."""
    before = before or _item()
    after = after or _item()
    changed = changed if changed is not None else {
        "transform": {"before": before.get("transform"),
                      "after": after.get("transform")}}
    plan_provenance.record_editor_changes(_review(root), REEL, [{
        "id": change_id, "timeline": REEL, "status": "pending",
        "changes": [{"kind": kind, "identity": "a.mov", "before": before,
                     "after": after, "changed": changed}],
        "before_snapshot": {"items": [before]},
        "after_snapshot": {"items": [after]},
    }])


def _manual_edit(root, *, change_id="change-1", summary="the captain edited",
                 at="2026-10-21T12:00:00+00:00"):
    return history.record_manual_edit(
        _review(root), REEL, summary=summary, editor_change_id=change_id,
        at=at)


class TestTheOutcome:
    def test_a_reel_with_no_acceptance_and_no_correction_is_unmeasured(
            self, tmp_path):
        """A reel nobody answered for is not an accepted reel.

        The defect this pins: the outcome defaulted to `accepted`, so
        every reel the captain never spoke about counted as a success
        and the product measured itself on records nobody made.
        """
        root = _project(tmp_path)
        _build(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        assert record["outcome"] == edit_outcome.OUTCOME_UNMEASURED
        assert "no delivery acceptance" in record["reason"]

    def test_a_hand_correction_makes_the_outcome_modified(self, tmp_path):
        """The captain's change is the fact that decides the outcome."""
        root = _project(tmp_path)
        _build(root)
        _record_change(root)
        _manual_edit(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        assert record["outcome"] == edit_outcome.OUTCOME_MODIFIED
        assert record["amount"]["corrections"] == 1

    def test_a_delivered_reel_with_no_correction_is_accepted(self, tmp_path):
        """A delivery the captain took is an acceptance."""
        root = _project(tmp_path)
        _build(root)
        from library.tools import reel_acceptance

        reel_acceptance.record(root, REEL, by="captain",
                               delivered_file="/deliverables/reel26.mp4")
        record = edit_outcome.derive_outcome(str(root), REEL)
        assert record["outcome"] == edit_outcome.OUTCOME_ACCEPTED
        assert record["acceptance"]["delivered_file"] == (
            "/deliverables/reel26.mp4")

    def test_a_correction_after_an_acceptance_still_reads_as_modified(
            self, tmp_path):
        """Took the delivery, then changed the edit: the change is later."""
        root = _project(tmp_path)
        _build(root)
        from library.tools import reel_acceptance

        reel_acceptance.record(root, REEL, by="captain")
        _record_change(root)
        _manual_edit(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        assert record["outcome"] == edit_outcome.OUTCOME_MODIFIED
        assert record["acceptance"] is not None


class TestTheClassification:
    def test_a_look_correction_with_nothing_wrong_is_a_disagreement(
            self, tmp_path):
        """A reframe that cuts nothing is the captain's call, not a fault.

        The defect this pins: every hand correction was counted as an
        objective failure, so the product chased the captain's taste as
        if the engine had broken something.
        """
        root = _project(tmp_path)
        _build(root)
        _record_change(root, before=_item(transform={"Zoom": 1.0}),
                       after=_item(transform={"Zoom": 1.2}))
        _manual_edit(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        correction = record["corrections"][0]
        assert correction["axis"] == edit_outcome.AXIS_LOOK
        assert (correction["classification"]
                == edit_outcome.CLASS_STYLISTIC_DISAGREEMENT)

    def test_a_zoom_that_cuts_the_face_is_an_objective_failure(
            self, tmp_path):
        """A crop that cuts the subject is a defect in the output.

        The defect this pins: the engine's own measurement of the
        subject framing was ignored, so a correction the engine could
        PROVE was wrong was filed as taste.
        """
        root = _project(tmp_path)
        _build(root)
        _record_change(root, before=_item(transform={"Zoom": 1.0}),
                       after=_item(transform={"Zoom": 2.0}))
        _manual_edit(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        correction = record["corrections"][0]
        assert (correction["classification"]
                == edit_outcome.CLASS_OBJECTIVE_FAILURE)
        assert correction["classification_source"] == "measurement"
        assert any("crops the subject" in row
                   for row in correction["evidence"])

    def test_a_selection_correction_nobody_classified_stays_unclassified(
            self, tmp_path):
        """Nothing measured it and nobody said: it is not taste.

        The defect this pins: an unread correction was counted as a
        stylistic disagreement, so the unclassified column was empty
        and the product claimed to know something it did not.
        """
        root = _project(tmp_path)
        _build(root)
        _record_change(
            root, before=_item(source_in_frame=0, source_out_frame=250),
            after=_item(source_in_frame=40, source_out_frame=250),
            changed={"source_in_frame": {"before": 0, "after": 40}})
        _manual_edit(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        correction = record["corrections"][0]
        assert correction["axis"] == edit_outcome.AXIS_SELECTION
        assert correction["classification"] == edit_outcome.CLASS_UNCLASSIFIED
        assert record["amount"]["unclassified"] == 1
        assert record["amount"]["stylistic_disagreements"] == 0

    def test_the_captains_own_classification_decides_the_class(
            self, tmp_path):
        """His words decide it, and the record says they did."""
        root = _project(tmp_path)
        _build(root)
        _record_change(
            root, before=_item(source_in_frame=0, source_out_frame=250),
            after=_item(source_in_frame=40, source_out_frame=250),
            changed={"source_in_frame": {"before": 0, "after": 40}})
        entry = _manual_edit(root)
        history.file_manual_edit_reason(
            _review(root), REEL, manual_edit_id=entry["id"],
            reason="that is the wrong take of the question",
            classification=history.CLASSIFICATION_OBJECTIVE)
        record = edit_outcome.derive_outcome(str(root), REEL)
        correction = record["corrections"][0]
        assert (correction["classification"]
                == edit_outcome.CLASS_OBJECTIVE_FAILURE)
        assert correction["classification_source"] == "captain"
        assert correction["reason"] == "that is the wrong take of the question"

    def test_a_classification_that_is_not_one_of_the_two_is_refused(
            self, tmp_path):
        """A class with no meaning in the vocabulary is refused."""
        root = _project(tmp_path)
        entry = _manual_edit(root)
        with pytest.raises(ValueError):
            history.file_manual_edit_reason(
                _review(root), REEL, manual_edit_id=entry["id"],
                reason="because", classification="vibes")


class TestTheAmountAndTheTime:
    def test_the_time_saved_carries_its_denominator(self, tmp_path):
        """Time saved is stated against the program it was saved from.

        The defect this pins: the number was printed on its own, so a
        reader could not tell a reel that was mostly untouched from one
        that was mostly rebuilt.
        """
        root = _project(tmp_path)
        _build(root)
        _record_change(
            root, before=_item(source_in_frame=0, source_out_frame=250),
            after=_item(source_in_frame=40, source_out_frame=250),
            changed={"source_in_frame": {"before": 0, "after": 40}})
        _manual_edit(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        timing = record["time"]
        assert timing["program_seconds"] == pytest.approx(10.0)
        assert timing["repaired_seconds"] == pytest.approx(10.0)
        assert timing["time_saved_seconds"] == pytest.approx(0.0)
        assert "re-cut or re-timed" in timing["time_saved_basis"]

    def test_a_look_change_repairs_no_program(self, tmp_path):
        """Restyling a clip changes no frames of it.

        The defect this pins: a reframe counted as if the whole clip
        had been re-cut, so a reel the captain only restyled reported
        no time saved at all.
        """
        root = _project(tmp_path)
        _build(root)
        _record_change(root, before=_item(transform={"Zoom": 1.0}),
                       after=_item(transform={"Zoom": 1.2}))
        _manual_edit(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        assert record["amount"]["repaired_frames"] == 0
        assert record["time"]["time_saved_seconds"] == pytest.approx(10.0)

    def test_the_window_without_a_build_is_unmeasured(self, tmp_path):
        """No build to measure the window from means no window.

        The defect this pins: a missing timestamp became a zero-second
        repair, which reads as an instant fix.
        """
        root = _project(tmp_path)
        # A hand edit adopted with no Ren build behind it: there is no
        # window to measure, and the record must say so.
        reel_versions.record(root, REEL, kind=reel_versions.KIND_ADOPT,
                             rows=_rows())
        _record_change(root)
        _manual_edit(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        assert record["time"]["elapsed_to_correction_seconds"] is None
        assert "unmeasured" in record["time"]["elapsed_basis"]

    def test_an_unmeasured_outcome_claims_no_time_saved(self, tmp_path):
        """A reel nobody answered for saved nothing that can be claimed.

        The defect this pins: the whole program was reported as saved
        for a reel nobody had answered for, so the product's headline
        number counted reels it knew nothing about.
        """
        root = _project(tmp_path)
        _build(root)
        record = edit_outcome.derive_outcome(str(root), REEL)
        assert record["outcome"] == edit_outcome.OUTCOME_UNMEASURED
        assert record["time"]["time_saved_seconds"] is None
        assert "unmeasured" in record["time"]["time_saved_basis"]

    def test_a_correction_dated_before_its_build_measures_no_window(
            self, tmp_path):
        """A correction before the build it follows measures nothing."""
        root = _project(tmp_path)
        _build(root, at="2026-10-21T18:00:00+00:00")
        _record_change(root)
        _manual_edit(root, at="2026-10-21T12:00:00+00:00")
        record = edit_outcome.derive_outcome(str(root), REEL)
        assert record["time"]["elapsed_to_correction_seconds"] is None
        assert "before the build" in record["time"]["elapsed_basis"]


class TestLearningIsOptIn:
    def test_nothing_is_learned_without_an_opt_in(self, tmp_path):
        """A project that says nothing learns nothing.

        The defect this pins: every correction was copied into the
        project's learned context, so the captain's words ended up in a
        place he never chose and every run read them back.
        """
        root = _project(tmp_path)
        _build(root)
        _record_change(root)
        entry = _manual_edit(root)
        history.file_manual_edit_reason(
            _review(root), REEL, manual_edit_id=entry["id"],
            reason="the framing was too loose")
        record = edit_outcome.derive_outcome(str(root), REEL)
        learning = edit_outcome.record_learnings(str(root), record)
        assert learning["project"] == "not_opted_in"
        assert learning["recorded"] == 0
        assert not (os.path.exists(os.path.join(
            str(root), "learned_context", "learnings.json")))

    def test_an_opted_in_project_learns_and_names_its_reader(self, tmp_path):
        """The learning lands in the project's own store, with a reader."""
        root = _project(tmp_path)
        (root / edit_outcome.POLICY_FILENAME).write_text(json.dumps({
            "format": edit_outcome.POLICY_FORMAT,
            "project_learning": True,
            "media_learning": False,
            "read_by": ["plan_picture"],
        }), encoding="utf-8")
        _build(root)
        _record_change(root)
        entry = _manual_edit(root)
        history.file_manual_edit_reason(
            _review(root), REEL, manual_edit_id=entry["id"],
            reason="the framing was too loose")
        record = edit_outcome.derive_outcome(str(root), REEL)
        learning = edit_outcome.record_learnings(str(root), record)
        assert learning["project"] == "recorded"
        assert learning["recorded"] == 1
        from library.tools import learned_context

        stored = learned_context.active_for_step(str(root), "plan_picture")
        assert any("too loose" in row["statement"] for row in stored)

    def test_a_policy_naming_no_reader_records_nothing(self, tmp_path):
        """A learning nothing consumes is refused, not recorded."""
        root = _project(tmp_path)
        (root / edit_outcome.POLICY_FILENAME).write_text(json.dumps({
            "format": edit_outcome.POLICY_FORMAT,
            "project_learning": True,
            "read_by": [],
        }), encoding="utf-8")
        _build(root)
        _record_change(root)
        entry = _manual_edit(root)
        history.file_manual_edit_reason(
            _review(root), REEL, manual_edit_id=entry["id"],
            reason="the framing was too loose")
        record = edit_outcome.derive_outcome(str(root), REEL)
        learning = edit_outcome.record_learnings(str(root), record)
        assert learning["recorded"] == 0
        assert "no step to read" in learning["reason"]

    def test_a_malformed_policy_refuses(self, tmp_path):
        """A policy nobody can read is not a policy of 'no'."""
        root = _project(tmp_path)
        (root / edit_outcome.POLICY_FILENAME).write_text("{not json",
                                                          encoding="utf-8")
        with pytest.raises(edit_outcome.EditOutcomeError):
            edit_outcome.policy(str(root))


class TestIsolation:
    def test_a_media_learning_carries_no_free_text(self, tmp_path):
        """A shareable fact about media carries no words and no names.

        The defect this pins: the media row carried the captain's words
        and the reel name, so sharing it with another project carried
        the client's words with it.
        """
        root = _project(tmp_path)
        _build(root)
        _record_change(root)
        entry = _manual_edit(root)
        history.file_manual_edit_reason(
            _review(root), REEL, manual_edit_id=entry["id"],
            reason="the framing was too loose")
        record = edit_outcome.derive_outcome(str(root), REEL)
        rows = edit_outcome.media_learning_rows(record)
        assert rows
        for row in rows:
            assert "too loose" not in json.dumps(row)
            assert REEL not in json.dumps(row)
            assert set(row) == {"axis", "kind", "classification", "frames",
                                "row"}

    def test_nothing_is_shared_without_a_mutual_grant(self, tmp_path):
        """The opt-in is checked before the source is read.

        The defect this pins: the rows were read and then the grant was
        checked, so a project that had not granted access still had
        its source opened.
        """
        source = _project(tmp_path / "source")
        target = _project(tmp_path / "target")
        for folder, name in ((source, "project-source"),
                              (target, "project-target")):
            (folder / "cross_project_policy.json").write_text(json.dumps({
                "project_id": name, "share_with": [],
            }), encoding="utf-8")
        record = {"corrections": [{"id": "c1", "axis": "look",
                                   "kind": "item_changed",
                                   "classification": "stylistic_disagreement",
                                   "frames_after": 10, "row": "video:V1"}]}
        shared = edit_outcome.share_media_learnings(str(source), str(target),
                                                     record)
        assert shared["shared"] is False
        assert shared["rows"] == []
        assert "grant each other" in shared["reason"]

    def test_two_projects_that_grant_each_other_share_the_measured_rows(
            self, tmp_path):
        """With the mutual grant, the measured rows travel."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        for folder in (source, target):
            (folder / "pipeline_output" / "review").mkdir(parents=True)
            (folder / "cross_project_policy.json").write_text(json.dumps({
                "project_id": f"project-{(source if folder is source else target).name}",
                "share_with": [f"project-{(target if folder is source else source).name}"],
            }), encoding="utf-8")
        record = {"corrections": [{"id": "c1", "axis": "look",
                                   "kind": "item_changed",
                                   "classification": "stylistic_disagreement",
                                   "frames_after": 10, "row": "video:V1"}]}
        shared = edit_outcome.share_media_learnings(str(source), str(target),
                                                     record)
        assert shared["shared"] is True
        assert shared["rows"]


class TestTheStore:
    def test_the_outcome_is_written_inside_the_project(self, tmp_path):
        """The record belongs to the project and to nobody else."""
        root = _project(tmp_path)
        _build(root)
        _record_change(root)
        _manual_edit(root)
        edit_outcome.record_outcome(str(root), REEL)
        path = edit_outcome.outcomes_path(str(root))
        assert str(path).startswith(str(root))
        stored = edit_outcome.outcome_for(str(root), REEL)
        assert stored["outcome"] == edit_outcome.OUTCOME_MODIFIED

    def test_an_unreadable_outcome_store_refuses(self, tmp_path):
        """An unreadable record reads exactly like no record - so refuse."""
        root = _project(tmp_path)
        path = edit_outcome.outcomes_path(str(root))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(edit_outcome.EditOutcomeError):
            edit_outcome.read_outcomes(str(root))


class TestTheWiring:
    def test_a_recorded_hand_edit_writes_the_product_outcome(self, tmp_path):
        """The correction is a product outcome, not only a history row.

        The defect this pins: the correction was recorded and the
        product question stayed unasked, so nothing ever joined the
        two.
        """
        root = _project(tmp_path)
        _build(root)
        _record_change(root)
        _manual_edit(root)
        stored = edit_outcome.outcome_for(str(root), REEL)
        assert stored is not None
        assert stored["outcome"] == edit_outcome.OUTCOME_MODIFIED

    def test_filing_the_reason_classifies_the_recorded_outcome(self, tmp_path):
        """The captain's words reach the outcome, not only the history."""
        root = _project(tmp_path)
        _build(root)
        _record_change(
            root, before=_item(source_in_frame=0, source_out_frame=250),
            after=_item(source_in_frame=40, source_out_frame=250),
            changed={"source_in_frame": {"before": 0, "after": 40}})
        entry = _manual_edit(root)
        history.file_manual_edit_reason(
            _review(root), REEL, manual_edit_id=entry["id"],
            reason="that is the wrong take",
            classification=history.CLASSIFICATION_OBJECTIVE)
        stored = edit_outcome.outcome_for(str(root), REEL)
        assert stored["corrections"][0]["classification"] == (
            edit_outcome.CLASS_OBJECTIVE_FAILURE)


class TestTheExistingSystemIsUsed:
    def test_the_face_crop_measurement_is_the_engines_own(self, tmp_path):
        """The objective claim about a look is the one that already existed.

        The defect this pins: PROD-008 wrote its own crop rule, so the
        engine held two answers to "did this crop cut the face".
        """
        before = _item(transform={"Zoom": 1.0})
        after = _item(transform={"Zoom": 2.0})
        assert manual_edit_intent.face_crop_regression(before, after)
        assert manual_edit_intent.face_crop_regression(before, before) is None
