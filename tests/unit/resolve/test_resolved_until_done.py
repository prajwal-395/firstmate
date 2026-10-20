"""`ren resolved result --until-done`: the infrastructure waits, not the model.

The defect: the co-editor skill told the agent to "repeat the result
command if its state is still `queued` or `running`".  That is a
PROCEDURE, in prose, that the model had to know and perform by hand -
exactly what the punch list's AGENT-004 asks to be replaced by a
semantic command where the infrastructure can perform the procedure
itself.

`--until-done` is that command: it waits for the job to reach a
terminal state (done, failed, rejected, cancelled) or for the deadline,
and returns the receipt either way.
"""

from library.tools.resolved import client


def _states(monkeypatch, sequence):
    """A broker that answers with each state in turn."""
    asked = []

    def fake_call(payload, path=None, timeout=None):
        asked.append(payload)
        index = min(len(asked) - 1, len(sequence) - 1)
        return {"job": dict(sequence[index])}

    monkeypatch.setattr(client, "call", fake_call)
    return asked


def test_until_done_waits_for_the_terminal_state_and_returns_its_receipt(
        monkeypatch):
    """The whole procedure, in one call: the receipt comes back when the
    job is done, without the caller asking again."""
    asked = _states(monkeypatch, [{"state": "queued"},
                                   {"state": "running"},
                                   {"state": "done", "result": "pool"}])
    job = client.result("job-1", until_done=True, deadline=30.0)
    assert job["state"] == "done"
    assert job["result"] == "pool"
    assert len(asked) == 3, "the wait asked fewer times than the job needed"
    assert all(payload["op"] == "result" for payload in asked)


def test_until_done_stops_on_a_failed_receipt_too(monkeypatch):
    """A job that FAILED is terminal: the wait ends there, so the caller
    is not left polling a job that will never succeed."""
    asked = _states(monkeypatch, [{"state": "running"},
                                   {"state": "failed", "error": "boom"}])
    job = client.result("job-1", until_done=True, deadline=30.0)
    assert job["state"] == "failed"
    assert len(asked) == 2


def test_without_until_done_the_call_stays_a_single_read(monkeypatch):
    """The default is unchanged: one read, the receipt as it stands."""
    asked = _states(monkeypatch, [{"state": "running"}])
    job = client.result("job-1")
    assert job["state"] == "running"
    assert len(asked) == 1


def test_until_done_gives_up_at_its_deadline_and_says_so(monkeypatch):
    """A job that never finishes must not hang the caller: the deadline
    ends the wait and the receipt is returned as it stands."""
    asked = _states(monkeypatch, [{"state": "queued"}])
    job = client.result("job-1", until_done=True, deadline=0.0)
    assert job["state"] == "queued"
    assert len(asked) == 1
