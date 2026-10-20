"""The editorial policy and the objective it declares, proved against named defects.

EDIT-025 found no place for a project to say how its edit should be cut,
so every threshold lived in the module that used it and "make the cut
tighter" could only be carried in a brief no measurement read.  EDIT-024
found no way to say what KIND of video a project is making, so the seven
objectives the punch list names existed only as words.

Each test below names one defect that the policy or the objective closes:

- a policy that names an objective nobody implements is ACCEPTED, so an
  editor believes a rule is in force and it is not;
- a policy that names a slot the engine cannot read is ACCEPTED, so the
  same thing happens to a number;
- an undeclared slot is FILLED IN, so a threshold nobody chose becomes
  indistinguishable from one the captain typed;
- an objective that carries a constraint nothing CHECKS is accepted, so
  the constraint is a promise with no reader;
- a constraint whose facts were never measured reads SATISFIED, so a
  missing measurement is indistinguishable from a passing one;
- a project that declares no policy gets a DEFAULT objective, so a
  house style arrives wearing a declaration's clothes.

None of them asserts that the registry contains a particular entry or a
particular count: the policy is built, mutated and checked, so a test
passes only when the module actually does what the defect requires.
"""
from __future__ import annotations

import pytest

from library.tools import editorial_objective, editorial_policy
from library.tools.editorial_policy import (
    OBJECTIVE_NAMES,
    SLOTS,
    EditorialPolicyError,
)


def _policy(**overrides):
    declared = {"objective": "interview", "slots": {}}
    declared.update(overrides)
    return editorial_policy.parse_policy(declared, project_id="demo")


def _context(**overrides):
    fields = {
        "source_passages": (
            editorial_objective.Passage("p1", speaker="Ana", text="what then"),
            editorial_objective.Passage("p2", speaker="Bo", text="yes"),
        ),
        "cut_passages": (
            editorial_objective.Passage("p1", speaker="Ana", text="what then"),
        ),
        "cutaways": (),
        "claims": (),
        "hook_passage_ids": (),
        "demonstration_passage_ids": (),
    }
    fields.update(overrides)
    return editorial_objective.ObjectiveContext(**fields)


# ── EDIT-025: the declaration surface ────────────────────────────

def test_a_policy_naming_an_unknown_objective_is_refused():
    """An objective nobody implements must not become a silent default."""
    with pytest.raises(EditorialPolicyError) as caught:
        editorial_policy.parse_policy({"objective": "vlog"},
                                      project_id="demo")
    assert "vlog" in str(caught.value)
    assert "interview" in str(caught.value)


def test_a_policy_naming_an_unknown_slot_is_refused():
    """A slot the engine cannot read is a slot the editor believes is in force."""
    with pytest.raises(EditorialPolicyError) as caught:
        editorial_policy.parse_policy(
            {"objective": "interview",
             "slots": {"silence": {"max_gap_second": 0.9}}},
            project_id="demo")
    assert "silence.max_gap_second" in str(caught.value)


def test_an_unknown_slot_group_is_refused():
    """A group nobody owns is refused before its contents are read."""
    with pytest.raises(EditorialPolicyError) as caught:
        editorial_policy.parse_policy(
            {"objective": "interview", "slots": {"colour": {"warm": True}}},
            project_id="demo")
    assert "colour" in str(caught.value)


def test_an_undeclared_slot_reads_as_undeclared_and_is_never_filled_in():
    """A threshold nobody chose must be distinguishable from one that was."""
    policy = _policy()
    assert policy.declared("silence.max_gap_seconds") is False
    assert policy.slot("silence.max_gap_seconds") is None
    described = editorial_policy.describe_policy(policy)
    assert "silence.max_gap_seconds" in described
    assert "UNDECLARED" in described


def test_a_declared_slot_is_read_and_coerced_to_its_kind():
    """A declared slot keeps the value the project typed."""
    policy = _policy(slots={"silence": {"max_gap_seconds": 0.9,
                                        "remove_fillers": True}})
    assert policy.slot("silence.max_gap_seconds") == 0.9
    assert policy.slot("silence.remove_fillers") is True
    assert policy.slots["silence.max_gap_seconds"] == 0.9


def test_a_slot_of_the_wrong_kind_is_refused():
    """A number declared as true is a declaration nobody can read."""
    with pytest.raises(EditorialPolicyError):
        _policy(slots={"silence": {"max_gap_seconds": "ninety"}})


def test_a_project_that_declares_no_policy_gets_none():
    """An absent declaration is an absence, not a default."""
    assert editorial_policy.read_project_policy(None) is None


def test_every_slot_names_a_reader():
    """A slot nothing reads is a declaration with no reader."""
    for slot in SLOTS.values():
        assert slot.readers, f"{slot.name} names no reader"
        for reader in slot.readers:
            assert reader.startswith("library.tools.")


def test_every_objective_names_constraints_that_are_implemented():
    """An objective carrying a constraint nothing checks is a promise with no reader."""
    for objective in editorial_policy.OBJECTIVES.values():
        assert objective.constraints, f"{objective.name} carries no constraint"
        for name in objective.constraints:
            assert name in editorial_objective.CONSTRAINTS, (
                f"{objective.name} carries {name}, which nothing checks")
            assert name in editorial_objective._CHECKS


def test_the_policy_round_trips_through_its_own_dict():
    """A policy written to state and read back is the same policy."""
    policy = _policy(slots={"silence": {"max_gap_seconds": 1.25}})
    again = editorial_policy.policy_from_dict(policy.to_dict())
    assert again.objective == policy.objective
    assert again.slots == policy.slots
    assert again.ref == policy.ref


# ── EDIT-024: the objective's constraints ─────────────────────────

def test_a_project_that_declares_no_objective_checks_nothing():
    """No objective means no constraint applies."""
    findings = editorial_objective.check_constraints(None, _context())
    assert findings == []


def test_a_dropped_speaker_is_a_violated_constraint():
    """An interview that drops one participant's point is a different video."""
    policy = _policy(objective="interview")
    findings = editorial_objective.check_constraints(policy, _context())
    coverage = [row for row in findings
                if row.constraint == "speaker_coverage"]
    assert coverage and coverage[0].status == editorial_objective.VIOLATED
    assert "Bo" in coverage[0].detail


def test_a_constraint_whose_facts_were_never_measured_reads_undetermined():
    """A missing measurement is not a passing one."""
    policy = _policy(objective="interview")
    context = _context(source_passages=(), cut_passages=())
    findings = editorial_objective.check_constraints(policy, context)
    coverage = [row for row in findings
                if row.constraint == "speaker_coverage"]
    assert coverage and coverage[0].status == editorial_objective.UNDETERMINED


def test_a_product_demo_whose_demonstration_is_covered_is_a_violation():
    """A demonstration under a cutaway is not demonstrated."""
    policy = _policy(objective="product_demo")
    context = _context(
        source_passages=(),
        cut_passages=(editorial_objective.Passage(
            "p1", text="look at this", source_start=1.0, source_end=5.0),),
        cutaways=(editorial_objective.Cutaway(
            "cut_1", source_start=2.0, source_end=4.0),),
        demonstration_passage_ids=("p1",))
    findings = editorial_objective.check_constraints(policy, context)
    over = [row for row in findings
            if row.constraint == "cutaway_over_demonstration"]
    assert over and over[0].status == editorial_objective.VIOLATED
    assert "cut_1" in over[0].evidence_ids


def test_a_tutorial_whose_steps_are_reordered_is_a_violation():
    """A tutorial whose steps are reordered cannot be followed."""
    policy = _policy(objective="tutorial")
    context = _context(
        source_passages=(),
        cut_passages=(
            editorial_objective.Passage("p2", text="then open it",
                                        step_index=2),
            editorial_objective.Passage("p1", text="first, take the lid off",
                                        step_index=1),
        ))
    findings = editorial_objective.check_constraints(policy, context)
    order = [row for row in findings if row.constraint == "step_order"]
    assert order and order[0].status == editorial_objective.VIOLATED


def test_a_reel_that_opens_on_a_hook_satisfies_its_constraint():
    """The constraint reads satisfied only when the hook is really there."""
    policy = _policy(objective="social_reel")
    context = _context(
        source_passages=(),
        cut_passages=(editorial_objective.Passage(
            "p1", text="here is the thing", block_type="hook"),))
    findings = editorial_objective.check_constraints(policy, context)
    hook = [row for row in findings if row.constraint == "hook_present"]
    assert hook and hook[0].status == editorial_objective.SATISFIED


def test_the_objectives_the_punch_list_names_are_all_implementable():
    """Every objective the item lists is an objective the engine knows."""
    for name in ("interview", "podcast", "social_reel", "product_demo",
                 "tutorial", "documentary", "promotional"):
        assert name in OBJECTIVE_NAMES
        policy = _policy(objective=name)
        assert policy.constraints
