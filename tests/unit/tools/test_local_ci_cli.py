from argparse import Namespace
import json
import os
import time

from library.tools.local_ci import __main__ as local_ci_cli
from library.tools.local_ci import hygiene, state


def test_status_lists_a_running_tier1_pr(monkeypatch, tmp_path, capsys):
    from library.tools.local_ci import load, service

    state = str(tmp_path / "state")
    plan = {"tier1_jobs": 1, "pytest_n": 2, "window": 600,
            "headroom": 0.0, "heavy": []}
    monkeypatch.setattr(load, "measure", dict)
    monkeypatch.setattr(load, "targets", lambda _measurement: plan)
    monkeypatch.setattr(load, "describe", lambda _plan: "jobs=1")
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "plist_path",
                        lambda: "/tmp/com.ren.ci.plist")

    slots = hygiene.Slots(state, jobs=1)
    with slots.claim({"kind": "tier1", "pr": 2083,
                      "head": "a" * 40}) as slot:
        assert slot == 0
        assert local_ci_cli.cmd_status(Namespace(state=state)) == 0

    output = capsys.readouterr().out
    assert "running jobs:" in output
    assert "tier1 PR #2083 aaaaaaaaaaaa slot 0" in output
    assert "pid " in output


def test_status_ignores_unlocked_slot_metadata_for_a_live_pid(
        monkeypatch, tmp_path, capsys):
    from library.tools.local_ci import load, service

    state = tmp_path / "state"
    slots_dir = state / "slots"
    slots_dir.mkdir(parents=True)
    (slots_dir / "slot-0.lock").touch()
    (slots_dir / "slot-0.json").write_text(json.dumps({
        "pid": os.getpid(), "slot": 0, "started_at": time.time(),
        "kind": "tier1", "pr": 42, "head": "b" * 40,
    }), encoding="utf-8")
    plan = {"tier1_jobs": 1, "pytest_n": 2, "window": 600,
            "headroom": 0.0, "heavy": []}
    monkeypatch.setattr(load, "measure", dict)
    monkeypatch.setattr(load, "targets", lambda _measurement: plan)
    monkeypatch.setattr(load, "describe", lambda _plan: "jobs=1")
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "plist_path",
                        lambda: "/tmp/com.ren.ci.plist")

    assert local_ci_cli.cmd_status(Namespace(state=str(state))) == 0

    output = capsys.readouterr().out
    assert "running jobs: none" in output
    assert "PR #42" not in output


def test_status_lists_the_testing_tier2_stack(monkeypatch, tmp_path, capsys):
    from library.tools.local_ci import load, service

    state_dir = str(tmp_path / "state")
    state.write_queue([
        {"pr": 2083, "head": "a" * 40, "status": "testing"},
        {"pr": 2084, "head": "b" * 40, "status": "testing"},
    ], state_dir)
    plan = {"tier1_jobs": 1, "pytest_n": 2, "window": 600,
            "headroom": 0.0, "heavy": []}
    monkeypatch.setattr(load, "measure", dict)
    monkeypatch.setattr(load, "targets", lambda _measurement: plan)
    monkeypatch.setattr(load, "describe", lambda _plan: "jobs=1")
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(service, "plist_path",
                        lambda: "/tmp/com.ren.ci.plist")
    monkeypatch.setattr(hygiene, "running_jobs", lambda _state: [])
    monkeypatch.setattr(hygiene, "running_gate_jobs", lambda: [{
        "pid": 12345, "started_at": time.time() - 91,
        "elapsed_seconds": 91.0,
    }])

    assert local_ci_cli.cmd_status(Namespace(state=state_dir)) == 0

    output = capsys.readouterr().out
    assert "Tier-2 full-suite gate for PRs #2083, #2084 91s pid 12345" in output
