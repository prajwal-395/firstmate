"""
The dashboard review return channel: anchored notes, one batched send, and
an agent reply that lands back on the notes it answers.

These guard the two properties the captain ruled the dashboard must have
(2026-08-17). The anchor is browser-computed, so the only thing the server
can enforce is that a note HAS one - a note with no anchor is a page comment,
which is the thing this channel exists not to be.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from library.dashboard import review_channel
from library.dashboard.server import app

ANCHOR_A = {
    "selector": "div#pipeline-content > div:nth-of-type(1) > div:nth-of-type(2)",
    "tag": "div",
    "text": "Scan Project",
    "label": "Scan Project",
    "view": "pipeline",
    "step_id": "scan",
}

ANCHOR_B = {
    "selector": "div#pipeline-content > div:nth-of-type(2) > div:nth-of-type(1)",
    "tag": "div",
    "text": "Catalog Media",
    "label": "Catalog Media",
    "view": "pipeline",
    "step_id": "catalog",
}


@pytest.fixture
def project(tmp_path):
    return str(tmp_path)


# ── Store ───────────────────────────────────────────────────────────

def test_note_without_anchor_is_rejected(project):
    """A note that names no element is not a review note. It must not degrade."""
    with pytest.raises(ValueError):
        review_channel.queue_note(project, "this is vague", {"view": "pipeline"})
    with pytest.raises(ValueError):
        review_channel.queue_note(project, "this is vague", None)






def test_empty_note_is_rejected(project):
    with pytest.raises(ValueError):
        review_channel.queue_note(project, "   ", ANCHOR_A)


def test_queued_notes_are_invisible_to_the_agent_until_sent(project):
    review_channel.queue_note(project, "first", ANCHOR_A)
    assert review_channel.pending_batch(project) is None


def test_one_send_carries_every_queued_note(project):
    first = review_channel.queue_note(project, "first", ANCHOR_A)
    second = review_channel.queue_note(project, "second", ANCHOR_B)

    batch = review_channel.send_queued(project)
    assert batch["note_ids"] == [first["id"], second["id"]]

    notes = review_channel.list_notes(project)
    assert [n["status"] for n in notes] == ["sent", "sent"]
    assert {n["batch_id"] for n in notes} == {batch["id"]}






def test_reply_lands_on_every_note_in_the_batch_by_default(project):
    review_channel.queue_note(project, "first", ANCHOR_A)
    review_channel.queue_note(project, "second", ANCHOR_B)
    batch = review_channel.send_queued(project)

    review_channel.add_reply(project, batch["id"], "both handled")

    notes = review_channel.list_notes(project)
    assert all(n["status"] == "answered" for n in notes)
    assert all(n["replies"][0]["text"] == "both handled" for n in notes)


def test_reply_can_answer_one_note_and_leave_the_batch_open(project):
    first = review_channel.queue_note(project, "first", ANCHOR_A)
    second = review_channel.queue_note(project, "second", ANCHOR_B)
    batch = review_channel.send_queued(project)

    review_channel.add_reply(project, batch["id"], "did the first", note_ids=[first["id"]])

    by_id = {n["id"]: n for n in review_channel.list_notes(project)}
    assert by_id[first["id"]]["status"] == "answered"
    assert by_id[second["id"]]["status"] == "sent"
    assert by_id[second["id"]]["replies"] == []
    # The agent still has the floor: the batch is not done.
    assert review_channel.pending_batch(project)["id"] == batch["id"]

    review_channel.add_reply(project, batch["id"], "and the second", note_ids=[second["id"]])
    assert review_channel.pending_batch(project) is None


def test_reply_to_a_note_outside_the_batch_is_rejected(project):
    review_channel.queue_note(project, "first", ANCHOR_A)
    batch = review_channel.send_queued(project)
    stray = review_channel.queue_note(project, "later", ANCHOR_B)

    with pytest.raises(ValueError):
        review_channel.add_reply(project, batch["id"], "nope", note_ids=[stray["id"]])




def test_a_queued_note_can_be_dropped_but_a_sent_one_is_a_record(project):
    note = review_channel.queue_note(project, "first", ANCHOR_A)
    assert review_channel.delete_note(project, note["id"]) is True
    assert review_channel.list_notes(project) == []

    kept = review_channel.queue_note(project, "second", ANCHOR_B)
    review_channel.send_queued(project)
    with pytest.raises(ValueError):
        review_channel.delete_note(project, kept["id"])




def test_wait_for_batch_wakes_on_a_send(project):
    """The agent's wake-up: parked on an empty channel, released by one send."""
    result = {}

    def agent():
        result["batch"] = review_channel.wait_for_batch(
            project, timeout_s=10, poll_interval_s=0.05
        )

    thread = threading.Thread(target=agent)
    thread.start()
    time.sleep(0.2)
    assert result == {}, "the agent must not wake before anything is sent"

    review_channel.queue_note(project, "wake up", ANCHOR_A)
    sent = review_channel.send_queued(project)
    thread.join(timeout=5)

    assert result["batch"]["id"] == sent["id"]
    assert result["batch"]["notes"][0]["text"] == "wake up"


def test_wait_for_batch_times_out_without_one(project):
    assert review_channel.wait_for_batch(project, timeout_s=0.2, poll_interval_s=0.05) is None


# ── HTTP ────────────────────────────────────────────────────────────

@pytest.fixture
def client(project, monkeypatch):
    monkeypatch.setattr("library.dashboard.server._get_project_dir", lambda: project)
    return TestClient(app)








