"""Learned context: what the run writes back so it stops repeating itself.

The captain: *"the LLM can record new bits of information that it learns
like feedback into the context for the project so it doesn't run into
making the same errors again."* The repo's law says captain inputs are
never written to (`project_layout.Kind.INPUT`), so the write-back is a
SEPARATE area - `learned_context/`, pipeline-owned, read alongside the
captain's `context/` on every later run, clearly attributed so the
captain can always tell what they said from what the pipeline concluded.

What a learning IS: one of three kinds, each with a named reader -
AGENTS.md 10.4 ("a learning nothing consumes is refused at record
time" here, the way a declaration nothing refuses is refused elsewhere):

* `correction` - the captain corrected the model (a routed timeline
  note that overturned a decision, review feedback). Read by the step
  that owns the overturned decision.
* `mistake_fix` - the pipeline caught its own error and how it fixed
  it (a QA finding answered, a retry that worked). Read by the step
  that made the mistake.
* `settled_decision` - a choice now fixed, asked once and never again
  (series identity settled, a music direction confirmed, a caption
  style approved). Read by every step deciding that thing.

A learning is RETIRED or CORRECTED when wrong, never silently edited:
an append-only pile of stale conclusions is the clutter the captain is
complaining about. Retirement and correction are operations with a
reason, recorded in the learning's own history.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_a_learning_can_be_recorded_and_read_back(tmp_path):
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="correction",
        statement="The captain rejected the crash zoom on the eating shots.",
        read_by=["plan_transitions"],
        source={"note": "timeline marker, 2026-09-04"},
    )
    assert rec["id"]
    assert rec["status"] == "active"
    store = json.loads(
        (tmp_path / "learned_context" / "learnings.json").read_text(
            encoding="utf-8"))
    assert store[0]["statement"].startswith("The captain rejected")


def test_a_learning_with_no_reader_is_refused(tmp_path):
    """AGENTS.md 10.4's rule, at record time: a learned fact nothing
    consumes is the defect, so it never lands."""
    from library.tools import learned_context
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.record(
            str(tmp_path), kind="correction",
            statement="Something was learned by nobody in particular.",
            read_by=[],
        )
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.record(
            str(tmp_path), kind="correction",
            statement="Something was learned for a step that is not a step.",
            read_by=["plan_everything"],
        )


def test_an_unknown_kind_is_refused(tmp_path):
    from library.tools import learned_context
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.record(
            str(tmp_path), kind="vibe",
            statement="The video has a vibe.",
            read_by=["plan_transitions"],
        )


def test_a_later_run_reads_only_what_is_scoped_to_it(tmp_path):
    from library.tools import learned_context
    learned_context.record(
        str(tmp_path), kind="correction",
        statement="No crash zooms on eating shots.",
        read_by=["plan_transitions"])
    learned_context.record(
        str(tmp_path), kind="settled_decision",
        statement="Captions sit low, never centre.",
        read_by=["plan_subtitles"])
    mine = learned_context.active_for_step(str(tmp_path), "plan_transitions")
    assert [l["statement"] for l in mine] == [
        "No crash zooms on eating shots."]
    assert "Captions sit low" not in learned_context.render_for_prompt(
        str(tmp_path), "plan_transitions")


def test_a_global_learning_reaches_every_declaring_step(tmp_path):
    from library.tools import learned_context
    learned_context.record(
        str(tmp_path), kind="settled_decision",
        statement="This video belongs to no named series.",
        read_by=["*"])
    for step in ("plan_transitions", "music_selection", "color_grade"):
        assert "no named series" in learned_context.render_for_prompt(
            str(tmp_path), step)


def test_a_learning_is_retired_with_a_reason_not_deleted(tmp_path):
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Old claim about the bed length.",
        read_by=["music_selection"])
    learned_context.retire(
        str(tmp_path), rec["id"],
        reason="The bed rule changed; this fix no longer applies.")
    mine = learned_context.active_for_step(str(tmp_path), "music_selection")
    assert mine == []
    store = json.loads(
        (tmp_path / "learned_context" / "learnings.json").read_text(
            encoding="utf-8"))
    assert store[0]["status"] == "retired"
    assert "no longer applies" in store[0]["history"][-1]["reason"]


def test_a_learning_is_corrected_by_supersession(tmp_path):
    from library.tools import learned_context
    old = learned_context.record(
        str(tmp_path), kind="settled_decision",
        statement="Captions sit low.",
        read_by=["plan_subtitles"])
    new = learned_context.correct(
        str(tmp_path), old["id"],
        new_statement="Captions sit low except on food close-ups.",
        reason="The captain moved one card up.")
    assert new["id"] != old["id"]
    assert new["supersedes"] == old["id"]
    mine = learned_context.active_for_step(str(tmp_path), "plan_subtitles")
    assert [l["statement"] for l in mine] == [
        "Captions sit low except on food close-ups."]


def test_retiring_what_is_not_there_is_refused_by_name(tmp_path):
    from library.tools import learned_context
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.retire(str(tmp_path), "lc-0000", reason="typo")


def test_the_captain_can_tell_theirs_from_the_pipelines(tmp_path):
    """Attribution: every rendered learning says who said it - the
    captain's correction, or the pipeline's own conclusion."""
    from library.tools import learned_context
    learned_context.record(
        str(tmp_path), kind="correction",
        statement="No crash zooms on eating shots.",
        read_by=["plan_transitions"])
    learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="The bed was fitted from the wrong second; fit at the "
                  "section that plays.",
        read_by=["music_selection"])
    rendered = learned_context.render_for_prompt(str(tmp_path), "*")
    assert "captain" in rendered.lower()
    assert "pipeline" in rendered.lower()


def test_every_kind_names_the_step_that_reads_it():
    """The reader report: kind -> reading steps. A kind with no reader
    is reported, the way a QA finding with no reader is reported."""
    from library.tools import learned_context
    report = learned_context.kinds_and_readers()
    assert set(report) == {"correction", "mistake_fix", "settled_decision"}
    for kind, readers in report.items():
        assert readers, f"kind {kind!r} names no reading step"
    assert learned_context.unread_kinds() == []


def test_a_pending_proposal_reaches_no_prompt_until_promoted(tmp_path):
    """The 2026-09-19 Sheehan defect: an uncertain model proposal
    recorded ACTIVE auto-applied. Pending is recorded, never enforced -
    until a human promotes it with the confirmation as the reason."""
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Model-proposed spelling: Sheehan reads as she even.",
        read_by=["*"], status="pending")
    assert rec["status"] == "pending"
    assert learned_context.active_for_step(str(tmp_path), "*") == []
    assert learned_context.render_for_prompt(
        str(tmp_path), "music_selection") == ""
    assert [l["id"] for l in learned_context.pending(
        str(tmp_path))] == [rec["id"]]
    promoted = learned_context.promote(
        str(tmp_path), rec["id"],
        reason="Captain 2026-09-19: it's not a name, keep the fix.")
    assert promoted["status"] == "active"
    assert promoted["history"][-1]["event"] == "promoted"
    assert len(learned_context.active_for_step(
        str(tmp_path), "*")) == 1


def test_pending_is_refused_a_reasonless_promotion(tmp_path):
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Unsure guess.", read_by=["*"], status="pending")
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.promote(str(tmp_path), rec["id"], reason="  ")
    # ...and only a pending learning promotes at all.
    active = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Confident fix.", read_by=["*"])
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.promote(
            str(tmp_path), active["id"], reason="Already decided.")
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.record(
            str(tmp_path), kind="mistake_fix",
            statement="A learning that starts ended.",
            read_by=["*"], status="retired")


def test_a_pending_proposal_retires_without_promotion(tmp_path):
    """Rejecting a held proposal must not require confirming it first:
    promoting in order to retire would write a confirmation that never
    happened."""
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Unsure suppression.", read_by=["*"], status="pending")
    retired = learned_context.retire(
        str(tmp_path), rec["id"],
        reason="Captain: that's her voice, not a stutter.")
    assert retired["status"] == "retired"
    assert learned_context.pending(str(tmp_path)) == []


def test_a_pending_proposal_is_corrected_only_after_promotion(tmp_path):
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Unsure guess.", read_by=["*"], status="pending")
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.correct(
            str(tmp_path), rec["id"], new_statement="Better guess.",
            reason="Settled it.")
    learned_context.promote(
        str(tmp_path), rec["id"], reason="Captain: keep, with a tweak.")
    new = learned_context.correct(
        str(tmp_path), rec["id"], new_statement="Better guess.",
        reason="Settled it.")
    assert new["supersedes"] == rec["id"]


def test_learnings_reach_a_declaring_step_through_gather(tmp_path):
    from library.processes.edit_video.run_pipeline import gather_step_inputs
    from library.tools import learned_context
    learned_context.record(
        str(tmp_path), kind="correction",
        statement="No crash zooms on eating shots.",
        read_by=["plan_transitions"])
    dag = {"edges": []}
    manifest = {"interface": {"inputs": [
        {"name": "project_context", "type": "string", "required": False},
        {"name": "project_folder", "type": "string", "required": False},
    ]}}
    inputs = gather_step_inputs(
        "plan_transitions", dag, {"project_folder": str(tmp_path)},
        manifest=manifest, step_type="llm_only")
    assert "No crash zooms" in inputs.get("project_context", "")
