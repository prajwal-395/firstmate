"""The Resolve worker's stdout is the PROTOCOL.

The daemon reads ONE LINE from the worker and parses it as the answer.
A job that prints - the touch-up path says its gate class and its cost
out loud - put a non-JSON line where the answer belonged, and the whole
job failed with "the worker's answer was not JSON". That is the error a
`ren revise` touch-up produced before this was fixed: the front door
reached the touch-up backend for the first time and the very first job
came back unparseable.

Each test here names its defect.
"""

from __future__ import annotations

import io
import json
import sys

from library.tools.resolved import worker


def _run_worker(monkeypatch, capsys, request: dict) -> tuple[str, str]:
    """Run the worker's loop over one request; return (stdout, stderr)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps(request) + "\n"))
    worker.main()
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_a_job_that_prints_does_not_corrupt_the_protocol(monkeypatch, capsys):
    """Catches: a job's `print` landing on the protocol channel, so the
    daemon read the printed line as the answer and refused the job with
    "the worker's answer was not JSON"."""
    def fake_execute(request):
        print("touch-reel Reel 01 - a: composed")
        print("  staged, 4.2 s")
        return {"id": request.get("id", ""), "ok": True, "result": {"done": True}}

    monkeypatch.setattr(worker, "execute", fake_execute)
    out, err = _run_worker(monkeypatch, capsys,
                            {"id": "r1", "kind": "health"})

    first = out.splitlines()[0]
    answer = json.loads(first)          # must parse: the daemon's read
    assert answer["ok"] is True
    assert answer["id"] == "r1"
    # The job's own words are not lost: they go to stderr.
    assert "touch-reel Reel 01 - a: composed" in err


def test_the_answer_is_the_first_line_even_when_the_job_prints(
        monkeypatch, capsys):
    """Catches: the answer written AFTER the job's output, so the daemon
    read the job's line first."""
    def fake_execute(request):
        print("a printed line")
        return {"id": request.get("id", ""), "ok": True, "result": {}}

    monkeypatch.setattr(worker, "execute", fake_execute)
    out, _err = _run_worker(monkeypatch, capsys,
                            {"id": "r2", "kind": "health"})

    assert len(out.splitlines()) == 1
    assert json.loads(out.splitlines()[0])["id"] == "r2"


def test_a_quiet_job_still_answers_on_stdout(monkeypatch, capsys):
    """Catches: the redirect swallowing the answer too."""
    def fake_execute(request):
        return {"id": request.get("id", ""), "ok": True, "result": {"n": 1}}

    monkeypatch.setattr(worker, "execute", fake_execute)
    out, err = _run_worker(monkeypatch, capsys,
                            {"id": "r3", "kind": "health"})

    assert json.loads(out.splitlines()[0]) == {
        "id": "r3", "ok": True, "result": {"n": 1}}
    assert err == ""


def test_a_malformed_request_is_answered_not_dropped(monkeypatch, capsys):
    """Catches: a bad request leaving the daemon waiting for an answer
    that never came."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json\n"))
    worker.main()
    out = capsys.readouterr().out
    answer = json.loads(out.splitlines()[0])
    assert answer["ok"] is False
    assert "bad request" in answer["error"]
