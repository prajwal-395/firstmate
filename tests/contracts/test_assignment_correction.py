"""A focused correction updates the request it names, and the task state
shows it.

AGENT-023 asks for a focused user correction that updates the EXISTING
task rather than restarting the process. The correction itself already
existed (`ren revise` attaches to a request and re-translates under the
same note); what it could not do was be pointed at a request. Without a
name the note lands on the request that owns the reel, which is a guess,
and a guess about which request the person means is how a correction
updates the wrong task.

The defects this names:

- a correction named to one request landing on another;
- a correction named to a request that does not exist being accepted
  silently;
- the new verb being on one front door and not the other, so the native
  front door refuses it before it reaches Python.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import assignment_lifecycle as lifecycle
from library.tools import assignment_spine as spine

REPO = Path(__file__).resolve().parents[2]


def _requests(project, *names):
    """One request per name, so a test can name one of several."""
    for name in names:
        lifecycle.create(str(project), name, kind="reels", request=name)


def test_a_correction_named_to_a_request_updates_that_request(tmp_path):
    _requests(tmp_path, "request-one", "request-two")

    attached = spine.attach_revision(tmp_path, "Reel 01 - pricing",
                                     request="the captions are too small",
                                     assignment_id="request-one")
    assert attached["assignment_id"] == "request-one"


def test_a_correction_named_to_a_request_that_does_not_exist_is_refused(tmp_path):
    """Naming a request that is not on record is a question for the
    person, not a correction attached to nothing."""
    from library.tools.ren_refusal import RenRefusal

    _requests(tmp_path, "request-one")
    with pytest.raises(RenRefusal) as refused:
        spine.attach_revision(tmp_path, "Reel 01 - pricing",
                              request="a note", assignment_id="request-nope")
    assert "request-nope" in refused.value.what
    assert "ren assignment-state" in refused.value.fix


def test_a_revision_records_the_request_it_updates(tmp_path):
    """The revision says which request it belongs to, so the task state
    can show the correction on that task and nowhere else."""
    from library.tools import revise

    _requests(tmp_path, "request-one", "request-two")
    result = revise.start(tmp_path, "the captions are too small",
                           assignment_id="request-one")
    assert result["status"] == revise.STATUS_AWAITING
    assert result["assignment_id"] == "request-one"
    assert result["requested_assignment_id"] == "request-one"


def test_without_a_name_the_request_is_still_the_one_that_owns_the_reel(tmp_path):
    """Naming a request replaces the choice, and nothing else about the
    path changes: without a name the request that owns the reel is still
    the one a correction joins."""
    from library.tools import reel_proposal
    from library.tools.reel_proposal import PROPOSAL_FORMAT, ReelMoment

    moment = reel_proposal.ensure_moment_id(
        ReelMoment(number=1, slug="pricing", reason="the price lands",
                   timeline_start=10.0, timeline_end=40.0))
    path = reel_proposal.proposal_path(str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": PROPOSAL_FORMAT,
                                "moments": [moment.as_dict()]}),
                    encoding="utf-8")
    lifecycle.create(str(tmp_path), "request-one", kind="reels",
                     request="one",
                     reels={moment.moment_id: {"number": 1,
                                               "name": moment.timeline_name,
                                               "status": "built"}})
    lifecycle.create(str(tmp_path), "request-two", kind="reels",
                     request="two", reels={})

    owner = spine.revision_owner(str(tmp_path), moment.timeline_name)
    assert owner["assignment_id"] == "request-one"
    attached = spine.attach_revision(str(tmp_path), moment.timeline_name,
                                     request="a note")
    assert attached["assignment_id"] == "request-one"


def test_the_task_state_verb_is_on_both_front_doors():
    """A verb the Python registry knows and the native one does not is
    unreachable: the native front door refuses it before it execs."""
    from ren.commands import VERBS

    verb = next(v for v in VERBS if v.name == "assignment-state")
    assert verb.module_argv == ("library.tools.assignment_view",)
    assert verb.effects == ("read",)
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "assignment-state"' in native
