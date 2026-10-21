"""The acceptance set: unfamiliar briefs, and an answer key the agent never sees.

Two ways this set can be wrong are invisible until the numbers come
back, and both make the score meaningless rather than merely wrong.

It can LEAK. If the answer key - the operations the request implies, the
semantic target roles, the source moments - reaches what the agent
reads, the agent recognises the answer instead of working it out, and
the report measures recognition. That is why the split is checked, not
assumed.

It can quietly STOP BEING NEW. A brief that is a reworded request from
the corpus the engine was already tuned against produces a score that
looks like generalisation and is not. The unfamiliarity is therefore
measured, against both the standing corpus and the ordinary request set
the live harness already runs.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from library.tools.editorial_acceptance import briefs, footage


def _shipped() -> dict:
    return briefs.load_brief_set()


def test_the_shipped_brief_set_holds_its_own_contract():
    """A set that breaks its own contract is refused when it is loaded,
    not when a report comes out saying nobody passed."""
    raw = json.loads(briefs.brief_set_path().read_text(encoding="utf-8"))
    assert briefs.validate_brief_set(raw) == []


def test_a_brief_that_leaks_its_answer_key_is_refused():
    """The defect: the public view carries the operation the request
    implies, so the agent is handed the answer."""
    raw = json.loads(briefs.brief_set_path().read_text(encoding="utf-8"))
    leaky = copy.deepcopy(raw["briefs"][0])
    leaky["project_state"]["footage_profile"] = "reel.opening"
    problems = briefs.leak_problems(briefs.public_view(leaky),
                                    leaky["oracle"])
    assert any("reel.opening" in problem for problem in problems)
    with pytest.raises(briefs.BriefSetError):
        briefs.assert_no_leak(briefs.public_view(leaky), leaky["oracle"])


def test_no_shipped_brief_leaks_its_answer_key():
    """Every brief in the set, checked against its own key."""
    for brief in _shipped()["briefs"]:
        view = briefs.public_view(brief)
        briefs.assert_no_leak(view, briefs.oracle(brief))


def test_the_public_view_carries_the_brief_and_nothing_else():
    """The agent reads the instruction, the state and the criteria. It
    does not read the edge case the brief is hiding."""
    brief = briefs.brief(_shipped(), "two-voices-at-once")
    view = briefs.public_view(brief)
    assert set(view) == set(briefs.PUBLIC_KEYS)
    assert set(view["project_state"]) == set(briefs.PROJECT_STATE_KEYS)
    assert "overlapping_speech" not in json.dumps(view)


def test_the_set_covers_every_edge_case_it_names():
    """A set with no brief for an edge case proves nothing about it, and
    the gap has to be visible rather than implied."""
    coverage = briefs.coverage(_shipped())
    assert coverage["edge_cases_missing"] == []
    assert set(coverage["edge_case_counts"]) == set(briefs.EDGE_CASES)
    assert all(count >= 1 for count in coverage["edge_case_counts"].values())


def test_a_set_missing_an_edge_case_says_so():
    """The defect: the coverage report reads as thorough while an edge
    case has no brief at all."""
    raw = _shipped()
    raw["briefs"] = [brief for brief in raw["briefs"]
                     if brief["oracle"]["edge_case"] != "sparse_footage"]
    coverage = briefs.coverage(raw)
    assert coverage["edge_cases_missing"] == ["sparse_footage"]


def test_the_set_includes_projects_with_human_work_and_imperfect_media():
    """The captain's ruling: a project that already carries a human's
    adjustments, and media that is not clean."""
    coverage = briefs.coverage(_shipped())
    assert coverage["briefs_with_existing_adjustments"]
    assert coverage["briefs_with_imperfect_media"]
    assert set(coverage["briefs_with_existing_adjustments"]) & set(
        coverage["briefs_with_imperfect_media"])


def test_no_brief_is_a_copy_of_a_request_the_engine_was_tuned_on():
    """The defect: the acceptance set quietly becomes the corpus, and
    "unfamiliar" becomes a claim rather than a measurement."""
    from library.tools import eval_corpus

    corpus = [str(request["text"]) for request in eval_corpus.REQUESTS]
    assert briefs.unfamiliar_against(_shipped(), corpus) == []


def test_no_brief_is_a_copy_of_the_ordinary_request_set():
    """The live harness already runs ten ordinary requests. A brief that
    restates one of them is not a new test."""
    from tests.live import editorial_acceptance as ordinary

    instructions = [task.instruction for task in ordinary.TASKS]
    instructions += [task.instruction for task in ordinary.COMPOUND_REQUESTS]
    assert briefs.unfamiliar_against(_shipped(), instructions) == []


def test_the_old_interpreter_refuses_every_new_brief():
    """The strongest statement of unfamiliarity available: the rule-based
    interpreter the Level-3 harness runs cannot read a single one of
    them, so none of them can be answered from what was already known."""
    from tests.live import blind_driver

    for brief in _shipped()["briefs"]:
        with pytest.raises(ValueError):
            blind_driver.interpret(str(brief["instruction"]))


def test_a_copied_brief_is_named_rather_than_silently_scored():
    """The defect: a near-copy passes unnoticed and flatters the run."""
    raw = _shipped()
    copy_of = raw["briefs"][0]
    corpus = [str(copy_of["instruction"])]
    assert briefs.unfamiliar_against(raw, corpus) == [str(copy_of["id"])]


def test_an_answer_key_naming_an_operation_nobody_has_is_refused():
    """The defect: the key names an operation the engine does not
    implement, so the brief can never pass and the report blames the
    run."""
    raw = json.loads(briefs.brief_set_path().read_text(encoding="utf-8"))
    raw["briefs"][0]["oracle"]["expected_operations"] = ["timeline.dance"]
    problems = briefs.validate_brief_set(raw)
    assert any("operations the engine does not have" in problem
               for problem in problems)


def test_a_brief_declaring_a_footage_profile_nobody_named_is_refused():
    """The defect: a brief declares the shape of the footage it is
    written against, and nobody checks the shape is one the engine can
    name - so the profile is decoration and every brief is measured
    against whatever reel was open."""
    raw = json.loads(briefs.brief_set_path().read_text(encoding="utf-8"))
    raw["briefs"][0]["project_state"]["footage_profile"] = "a_reel_of_tears"
    problems = briefs.validate_brief_set(raw)
    assert any("footage profile" in problem for problem in problems)


def test_every_shipped_brief_declares_a_profile_the_enumeration_names():
    """The defect: a brief whose profile nobody can name can never be
    paired with a substrate, and the report does not say so."""
    the_set = briefs.load_brief_set()
    for brief in the_set["briefs"]:
        declared = footage.declared_profile(brief)
        assert declared in footage.PROFILES, (
            f"{brief['id']} declares a profile outside the enumeration")
        assert declared, f"{brief['id']} declares no footage profile"


def test_a_brief_repeating_a_criterion_id_is_refused():
    """The defect: two criteria share an id, and the judge scores one of
    them twice."""
    raw = json.loads(briefs.brief_set_path().read_text(encoding="utf-8"))
    criteria = raw["briefs"][0]["acceptance_criteria"]
    criteria.append(copy.deepcopy(criteria[0]))
    problems = briefs.validate_brief_set(raw)
    assert any("repeats criterion id" in problem for problem in problems)


def test_a_brief_with_no_acceptance_criteria_is_refused():
    """The defect: a brief with nothing to be judged against scores
    itself."""
    raw = json.loads(briefs.brief_set_path().read_text(encoding="utf-8"))
    raw["briefs"][0]["acceptance_criteria"] = []
    problems = briefs.validate_brief_set(raw)
    assert any("declares no acceptance criteria" in problem
               for problem in problems)


def test_a_brief_whose_key_is_not_allowed_by_its_own_expectation_is_refused():
    """The defect: the key expects an operation its allowed set forbids,
    so the plan is wrong before it starts."""
    raw = json.loads(briefs.brief_set_path().read_text(encoding="utf-8"))
    raw["briefs"][0]["oracle"]["allowed_operations"] = []
    problems = briefs.validate_brief_set(raw)
    assert any("expects operations it does not allow" in problem
               for problem in problems)


def test_a_shipped_brief_is_found_by_its_id_and_a_missing_one_raises():
    """A brief the set does not carry must fail loudly: a silent default
    would score an unknown brief as an empty one."""
    with pytest.raises(KeyError):
        briefs.brief(_shipped(), "no-such-brief")


def test_the_set_ships_inside_the_package_and_reads_from_disk():
    """The set is data, so it is read from disk: a brief can be added or
    withdrawn without touching code, and the file that ships is the file
    that runs."""
    path = briefs.brief_set_path()
    assert isinstance(path, Path)
    assert path.name == "brief_set.json"
    assert path.exists()
    assert path.parent == Path(briefs.__file__).resolve().parent
