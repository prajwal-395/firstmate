from argparse import Namespace

from library.tools.local_ci import __main__ as local_ci_cli
from library.tools.local_ci import hygiene


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
