"""What the editor's ruling on a treatment must record, and must refuse.

Each test names a defect: a correction nobody explained, an outcome
that is not one of the three, an editor-wide preference that travels on
its own, a ruling nobody can read back, and a correction that never
reaches the project it belongs to.
"""

from __future__ import annotations

import pytest

from library.tools import treatment_rulings as tr

# ── The outcome is one of three ───────────────────────────────────────


def test_an_unknown_outcome_is_refused():
    """A ruling whose outcome is not one of the three says nothing."""
    with pytest.raises(tr.RulingError):
        tr.ruling("maybe", "the editor was not sure")


def test_a_rejection_with_no_reason_is_refused():
    """A correction nobody explained teaches nothing."""
    with pytest.raises(tr.RulingError):
        tr.ruling("rejected", "   ")


def test_a_modification_with_no_reason_is_refused():
    """A modified plan is a rejection AND a new plan; without the reason
    it is only a rejection nobody explained."""
    with pytest.raises(tr.RulingError):
        tr.ruling("modified", "")


def test_an_acceptance_needs_no_reason():
    """Nothing was corrected, so there is nothing to explain."""
    ruling = tr.ruling("accepted")
    assert ruling["outcome"] == tr.ACCEPTED
    assert ruling["reason"] == ""


def test_an_unknown_scope_is_refused():
    """A scope the engine does not know cannot be kept where it was
    stated."""
    with pytest.raises(tr.RulingError):
        tr.ruling("accepted", "", scope="everything")


# ── The editor's own words go in first ───────────────────────────────


def test_the_editor_words_are_kept_verbatim():
    """The quote is what makes the ruling readable later."""
    ruling = tr.ruling("rejected", "the opening hides the claim",
                        editor_words="it buries the point")
    assert ruling["editor_words"] == "it buries the point"


# ── Project scope against editor scope ───────────────────────────────


def test_a_project_scoped_ruling_never_travels():
    """A preference the editor means for THIS project is not a
    preference they hold everywhere."""
    ruling = tr.ruling("rejected", "this reel wants the claim first",
                       scope=tr.SCOPE_PROJECT)
    assert ruling["scope"] == tr.SCOPE_PROJECT
    assert ruling["generalizable"] is False


def test_an_editor_wide_ruling_that_names_no_slot_does_not_travel():
    """Ren does not invent a slot for a subjective choice, so a ruling
    with nowhere to land stays where it was stated."""
    ruling = tr.ruling("accepted", "", scope=tr.SCOPE_EDITOR)
    assert ruling["scope"] == tr.SCOPE_EDITOR
    assert ruling["generalizable"] is False
    assert ruling["generalizable_reason"]


def test_an_editor_wide_ruling_that_names_a_slot_travels():
    """A preference the editor holds everywhere, addressed by a slot the
    engine can read back, is generalizable."""
    slot = tr.editor_wide_preferences()[0]["key"]
    ruling = tr.ruling("accepted", "", scope=tr.SCOPE_EDITOR,
                       preference_slot=slot)
    assert ruling["generalizable"] is True
    assert ruling["preference_slot"] == slot


def test_an_unknown_preference_slot_is_refused():
    """A slot the engine has never heard of cannot be the address a
    preference travels through."""
    with pytest.raises(tr.RulingError):
        tr.ruling("accepted", "", scope=tr.SCOPE_EDITOR,
                  preference_slot="opening.always_leads_with_a_question")


# ── The ruling lands on the decision it belongs to ───────────────────


def test_a_recorded_ruling_is_readable_back_on_the_decision(tmp_path):
    """A ruling nobody can read back is a ruling nobody can act on."""
    from library.tools import candidate_treatments as ct

    candidate_set = ct.build("the opening feels slow", [
        ct.treatment("t1", "Claim first", "The claim lands the point.",
                     criteria=[{"criterion": "the claim lands first",
                                "kind": "brief", "verdict": "pass",
                                "reason": "the claim is spoken first"}])])
    decision = ct.select(candidate_set, "t1")
    revision = ct.record(tmp_path, candidate_set, decision)
    ruling = tr.ruling("rejected", "the opening hides the claim",
                       editor_words="it buries the point")
    tr.record(tmp_path, revision["revision_id"], ruling)
    rows = tr.read_rulings(tmp_path)
    assert len(rows) == 1
    assert rows[0]["outcome"] == tr.REJECTED
    assert rows[0]["reason"] == "the opening hides the claim"
    assert rows[0]["editor_words"] == "it buries the point"
    assert rows[0]["scope"] == tr.SCOPE_PROJECT


def test_a_rejection_becomes_a_learning_the_project_reads(tmp_path):
    """The reason for a correction is the one piece of information the
    next run in this project needs."""
    from library.tools import learned_context

    ruling = tr.ruling("rejected", "the opening hides the claim",
                       editor_words="it buries the point")
    recorded = tr.record(tmp_path, "rev-1", ruling)
    learning = recorded["learning"]
    assert learning is not None
    assert "the opening hides the claim" in learning["statement"]
    assert "it buries the point" in learning["statement"]
    active = learned_context.active_for_step(str(tmp_path),
                                             learned_context.GLOBAL)
    assert any("the opening hides the claim" in row["statement"]
               for row in active)


def test_a_project_learning_stays_in_its_project(tmp_path):
    """The reason a correction teaches is project-scoped, so one reel's
    taste cannot become another project's taste."""
    from library.tools import learned_context

    other = tmp_path / "other"
    other.mkdir()
    ruling = tr.ruling("rejected", "the opening hides the claim")
    tr.record(tmp_path, "rev-1", ruling)
    assert learned_context._load(str(tmp_path))
    assert learned_context._load(str(other)) == []


def test_an_acceptance_teaches_nothing(tmp_path):
    """Nothing was corrected, so there is nothing for the next run to
    learn from."""
    recorded = tr.record(tmp_path, "rev-1",
                         tr.ruling("accepted", "the claim lands first"))
    assert recorded["learning"] is None
