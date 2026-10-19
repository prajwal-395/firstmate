"""Sign-off lifecycle events: the QC-passed and QC-rejected operations.

The spec's "everything should be event-sourced": a sign-off is the act
that says a built reel is done, and a withdrawal takes that back. The
sign-off record alone says WHAT is approved; the event says WHEN, BY
WHOM and in which round, in the operation history a resumed worker
reads. Every test names a defect it catches: on origin/main the
sign-off writes no event, so the operation history of the most
important act on a reel is empty.
"""

import pytest

from library.tools import event_log, reel_signoff
from library.tools.event_log import read_events


@pytest.fixture
def project(tmp_path):
    """A project folder with a LOGS area, never a real one."""
    folder = tmp_path / "project"
    (folder / "pipeline_output" / "logs").mkdir(parents=True)
    return str(folder)


def test_sign_off_leaves_a_signed_off_event(project):
    """An approval is an operation: the log names the reel, who, which round.

    Defect caught: the sign-off record says WHAT is approved but the
    event log - the history a resumed worker filters by task - never
    mentions it, so the approval is invisible in the operation history.
    """
    entry = reel_signoff.sign_off(project, "Reel 09", note="ships")
    signed = [e for e in read_events(project)
              if e["type"] == event_log.REEL_SIGNED_OFF]
    assert len(signed) == 1
    fields = signed[0]["fields"]
    assert fields["reel"] == entry["reel"] == "Reel 09"
    assert fields["by"] == entry["by"] == "captain"
    assert fields["round"] == entry["round"]
    assert fields["rows_digest"] == entry["rows_digest"]


def test_withdraw_leaves_a_withdrawn_event_and_keeps_the_approval(project):
    """A taken-back approval is a fact: the log records it without erasing.

    Defect caught: a withdrawal that leaves no event makes "approved
    once and then un-approved" unanswerable from the operation
    history - the superseded record holds it, the log does not.
    """
    reel_signoff.sign_off(project, "Reel 09", note="ships")
    assert reel_signoff.withdraw(project, "Reel 09",
                                 why="grade shifted") is True
    events = read_events(project)
    # Sign-off now runs inside the project activity lease; lease lifecycle
    # events are expected alongside the editorial events.
    editorial = [e for e in events if e["type"] in {
        event_log.REEL_SIGNED_OFF, event_log.SIGNOFF_WITHDRAWN}]
    assert [e["type"] for e in editorial] == [
        event_log.REEL_SIGNED_OFF, event_log.SIGNOFF_WITHDRAWN]
    assert editorial[1]["fields"] == {"reel": "Reel 09",
                                       "why": "grade shifted"}


def test_withdraw_of_nothing_leaves_no_event(project):
    """No invented operations: a withdrawal that removed nothing emits nothing.

    Defect caught: an unconditional emit in `withdraw` would record a
    rejection for a reel that was never approved, and a resumed worker
    would read a history of things that never happened.
    """
    assert reel_signoff.withdraw(project, "Reel 09", why="oops") is False
    assert read_events(project) == []


def test_a_log_failure_never_takes_down_an_approval(project, monkeypatch):
    """The sign-off is the act, the event is its record.

    Defect caught: an emit that raises through `sign_off` turns a
    logging failure into a refused approval - the same refusal the
    lease layer already refuses to make (`resource_leases._emit`).
    """
    def _boom(*args, **kwargs):
        raise OSError("disk gone")
    monkeypatch.setattr(event_log, "emit", _boom)
    entry = reel_signoff.sign_off(project, "Reel 09", note="ships")
    assert entry["reel"] == "Reel 09"
    assert reel_signoff.signoff_for(project, "Reel 09") is not None
