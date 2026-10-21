"""A DELIVERED reel carries a durable acceptance (Reel 26 audit §2.1).

Defect: promotion replaced the accepted look without the captain's
approval. A sign-off protects a reel the captain APPROVED; nothing
protected a reel he TOOK - delivered, verified, and then replaced by
the next rebuild without a word from him.

These tests name the defect and assert the guard that closes it: the
acceptance is recorded when a reel is delivered, it is durable, and a
promotion over it refuses until the supersession is declared.
"""

from __future__ import annotations

import json
import os

import pytest

from library.tools import reel_acceptance
from library.tools.ren_refusal import RenRefusal

REEL = "Reel 26 - write-for-the-question-your-customer-ask"


def _project(tmp_path):
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    return project


def test_a_delivered_reel_carries_a_durable_acceptance(tmp_path):
    """The delivery is recorded, and it survives on disk."""
    project = _project(tmp_path)

    entry = reel_acceptance.record(
        project, REEL, timeline_id="a0c4856c-ade2-4429-980c-2a39a0d541a3",
        delivered_file="/exports/reel26.mp4", by="delivery")

    assert entry["reel"] == REEL
    assert entry["timeline_id"] == "a0c4856c-ade2-4429-980c-2a39a0d541a3"
    assert entry["delivered_file"] == "/exports/reel26.mp4"
    assert os.path.isfile(reel_acceptance.path_for(project))


def test_the_acceptance_survives_the_reel_being_staged_or_backed_up(tmp_path):
    """One reel, three names: the acceptance follows the reel."""
    project = _project(tmp_path)
    reel_acceptance.record(project, REEL)

    for spelling in (REEL, f"{REEL} (rebuild staging)",
                     f"{REEL} (archived round 003)"):
        assert reel_acceptance.acceptance_for(project, spelling) is not None


def test_promotion_over_an_accepted_reel_refuses_without_a_declaration(
        tmp_path):
    """The defect: the accepted look was replaced silently."""
    project = _project(tmp_path)
    reel_acceptance.record(project, REEL)

    with pytest.raises(RenRefusal) as refused:
        reel_acceptance.assert_declared(project, REEL, [])

    message = str(refused.value)
    assert REEL in message
    assert "--supersede" in message


def test_a_declared_supersession_ends_the_acceptance_without_deleting_it(
        tmp_path):
    """A supersession is recorded, never erased."""
    project = _project(tmp_path)
    reel_acceptance.record(project, REEL, delivered_file="/exports/r.mp4")

    ended = reel_acceptance.assert_declared(project, REEL, [REEL])

    assert ended is not None
    # The declaration is accepted; the promotion ENDS the acceptance
    # after its rename lands, and never before.
    assert reel_acceptance.acceptance_for(project, REEL) is not None
    reel_acceptance.supersede(project, REEL)
    assert reel_acceptance.acceptance_for(project, REEL) is None
    document = json.loads(
        open(reel_acceptance.path_for(project), encoding="utf-8").read())
    superseded = document["superseded"]
    assert len(superseded) == 1
    assert superseded[0]["reel"] == REEL
    assert superseded[0]["ended_by"] == "superseded"


def test_a_reel_that_was_never_delivered_raises_no_refusal(tmp_path):
    """The guard is about DELIVERED reels, and says nothing else."""
    project = _project(tmp_path)

    assert reel_acceptance.assert_declared(project, REEL, []) is None


def test_an_unreadable_acceptance_record_refuses_rather_than_reads_empty(
        tmp_path):
    """An unreadable acceptance is not an absent acceptance."""
    project = _project(tmp_path)
    with open(reel_acceptance.path_for(project), "w",
              encoding="utf-8") as handle:
        handle.write("{not json")

    with pytest.raises(RenRefusal):
        reel_acceptance.read(project)
