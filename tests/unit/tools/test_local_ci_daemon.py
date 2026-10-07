"""Tier-1 orchestration: parallel checks, serial ledger, latency.

Defects caught: two new heads where only one gets checked (serial
leftover), a ledger write lost to a thread race, a red ready-PR that
keeps its queue label, and latency nobody recorded (untunable).
GitHub is faked; the ledger is real (tmp state dir).
"""

import pytest

from library.tools.local_ci import config, daemon, queue, state


def _pr(number, head=None, labels=(), draft=False):
    return {"number": number, "head": head or f"head-{number}",
            "base": "main", "labels": list(labels), "draft": draft,
            "url": f"https://example/pr/{number}",
            "title": f"PR {number}"}


@pytest.fixture
def faked_github(monkeypatch):
    import library.tools.local_ci.github as github
    posts = []
    monkeypatch.setattr(github, "list_open_prs", lambda: faked_github.prs)
    monkeypatch.setattr(github, "post_status",
                        lambda *a, **k: posts.append(a))
    monkeypatch.setattr(github, "remove_label",
                        lambda *a, **k: posts.append(("unlabel",) + a))
    monkeypatch.setattr(github, "main_sha", lambda: "base")
    # Tier-2 is queue's unit-test territory (test_local_ci_queue.py):
    # here it must never clone, fetch, or gate.
    monkeypatch.setattr(queue, "process_once",
                        lambda world, window=0: {"stubbed": True,
                                                 "window": window})
    faked_github.prs = []
    faked_github.posts = posts
    return faked_github


def _outcome(pr, ok=True):
    return {"pr": pr["number"], "head": pr["head"], "url": pr["url"],
            "ok": ok, "passed": 5, "failed": 0 if ok else 1,
            "elapsed": 1.0, "log": "/tmp/x.log",
            "selection": ["tests/contracts"], "command": "pytest tests"}


def test_two_new_heads_both_get_checked(monkeypatch, tmp_path, faked_github):
    faked_github.prs = [_pr(1), _pr(2)]
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, pr, workers: _outcome(pr))
    world = queue.World(state_dir=str(tmp_path))
    done = daemon.pass_once(world, plan={"tier1_jobs": 2, "pytest_n": 1,
                                         "window": 0, "headroom": 9.0,
                                         "heavy": []})
    assert sorted(done["checked"]) == [1, 2]
    checks = state.read_checks(str(tmp_path))
    assert sorted(c["pr"] for c in checks.values()) == [1, 2]
    assert all(c["tier1_latency"] >= 0 for c in checks.values())


def test_green_ready_enters_the_queue_red_loses_its_label(
        monkeypatch, tmp_path, faked_github):
    faked_github.prs = [_pr(1, labels=[config.READY_LABEL]),
                        _pr(2, labels=[config.READY_LABEL])]

    def fake_run(world, pr, workers):
        return _outcome(pr, ok=(pr["number"] == 1))

    monkeypatch.setattr(daemon, "run_tier1", fake_run)
    world = queue.World(state_dir=str(tmp_path))
    done = daemon.pass_once(world, plan={"tier1_jobs": 2, "pytest_n": 1,
                                         "window": 0, "headroom": 9.0,
                                         "heavy": []})
    assert done["queued"] == [1]
    assert ("unlabel", 2, config.READY_LABEL) in faked_github.posts


def test_known_heads_are_not_retested(monkeypatch, tmp_path, faked_github):
    pr = _pr(1)
    state.record_check({**_outcome(pr), "seen_at": 1.0, "done_at": 2.0,
                        "tier1_latency": 1.0, "slot": 0}, str(tmp_path))
    faked_github.prs = [pr]
    calls = []
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda *a: calls.append(a) or _outcome(pr))
    world = queue.World(state_dir=str(tmp_path))
    done = daemon.pass_once(world, plan={"tier1_jobs": 1, "pytest_n": 2,
                                         "window": 0, "headroom": 0.0,
                                         "heavy": []})
    assert done["checked"] == [] and calls == []
