"""The Project Model's intent layer, proved against named defects.

The gap: the Project Model declared an `intents` collection - the place
where what was ASKED FOR lives, beside the `edits` that realise it -
and nothing wrote to it, nothing read it and nothing checked it.  An
edit could point at an intent that does not exist and the model said
nothing.

Each test names ONE defect the boundary now closes:

    an edit pointing at no intent               EDIT-009
    a clip use pointing at no intent            EDIT-009
    an intent resting on an unknown observation EDIT-010
    two intents sharing one id                  EDIT-001

The fixture is a real, valid model; each test breaks exactly one thing
in a copy and names what the boundary says about it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from library.tools import project_model

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "project_model_e2e" / \
    "expected-project-model.json"


@pytest.fixture()
def model():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_model_is_valid(model):
    """The contract holds on the shipped fixture, not only on the
    mutations."""
    project_model.validate_project_model(model)


def test_the_fixture_carries_an_intent_and_the_edit_that_serves_it(model):
    """EDIT-001: the intent is a layer of its own, and the edit names
    the one it realises."""
    assert model["intents"], "the fixture must carry an intent to link"
    intent_id = model["intents"][0]["id"]
    assert model["edits"][0]["intent_id"] == intent_id


def test_an_edit_pointing_at_an_unknown_intent_is_refused(model):
    """EDIT-009: an edit with nothing behind it is a decision with no
    record."""
    broken = copy.deepcopy(model)
    broken["edits"][0]["intent_id"] = "intent-nobody-wrote"
    with pytest.raises(ValueError) as raised:
        project_model.validate_project_model(broken)
    assert "unknown intent" in str(raised.value)


def test_a_clip_use_pointing_at_an_unknown_intent_is_refused(model):
    """EDIT-009: the same for the clip use that serves the intent."""
    broken = copy.deepcopy(model)
    broken["clip_uses"][0]["intent_id"] = "intent-nobody-wrote"
    with pytest.raises(ValueError) as raised:
        project_model.validate_project_model(broken)
    assert "unknown intent" in str(raised.value)


def test_an_intent_resting_on_an_unknown_observation_is_refused(model):
    """EDIT-010: the evidence an intent rests on must exist."""
    broken = copy.deepcopy(model)
    broken["intents"][0]["evidence_links"] = ["obs-nobody-measured"]
    with pytest.raises(ValueError) as raised:
        project_model.validate_project_model(broken)
    assert "unknown observation" in str(raised.value)


def test_two_intents_sharing_one_id_are_refused(model):
    """EDIT-001: an intent's id is its identity."""
    broken = copy.deepcopy(model)
    broken["intents"].append(copy.deepcopy(broken["intents"][0]))
    with pytest.raises(ValueError) as raised:
        project_model.validate_project_model(broken)
    assert "unique" in str(raised.value)


def test_an_intent_with_no_id_is_refused(model):
    broken = copy.deepcopy(model)
    broken["intents"][0]["id"] = ""
    with pytest.raises(ValueError) as raised:
        project_model.validate_project_model(broken)
    assert "stable id" in str(raised.value)


def test_a_model_with_no_intents_is_still_valid(model):
    """An absent intent layer is the model's own statement, not a gap
    the boundary fills."""
    without = copy.deepcopy(model)
    without["intents"] = []
    for edit in without["edits"]:
        edit["intent_id"] = None
    for use in without["clip_uses"]:
        use["intent_id"] = None
    project_model.validate_project_model(without)
