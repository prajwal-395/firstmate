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
    from library.tools.local_ci import github
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


def test_failed_status_delivery_retries_without_rerunning_tests(
        monkeypatch, tmp_path, faked_github):
    from library.tools.local_ci import github
    pr = _pr(4)
    faked_github.prs = [pr]
    runs = []
    posts = []

    def run_once(world, current_pr, workers):
        runs.append(current_pr["head"])
        return _outcome(current_pr)

    def post_status(*args, **kwargs):
        posts.append((args, kwargs))
        if len(posts) == 1:
            raise github.GitHubError("temporary API refusal")

    monkeypatch.setattr(daemon, "run_tier1", run_once)
    monkeypatch.setattr(github, "post_status", post_status)
    world = queue.World(state_dir=str(tmp_path))
    plan = {"tier1_jobs": 1, "pytest_n": 2, "window": 0,
            "headroom": 0.0, "heavy": []}

    first = daemon.pass_once(world, plan=plan)
    record = state.read_checks(str(tmp_path))[pr["head"]]
    assert first["checked"] == [4]
    assert record["ok"] is True and record["status_posted"] is False
    assert "temporary API refusal" in record["status_error"]

    second = daemon.pass_once(world, plan=plan)
    record = state.read_checks(str(tmp_path))[pr["head"]]
    assert second["checked"] == []
    assert runs == [pr["head"]]
    assert record["status_posted"] is True
    assert posts[1][0][3] == pr["url"]


def test_legacy_local_log_url_is_replaced_before_status_retry(
        tmp_path, faked_github):
    from library.tools.local_ci import github

    pr = _pr(5)
    record = {**_outcome(pr),
              "url": "/Users/prajwal/.ren-ci/logs/pr-5-head.log",
              "status_posted": False, "status_error": ""}
    state.record_check(record, str(tmp_path))

    assert daemon._publish_check_status(record, str(tmp_path)) is True

    canonical_url = github.pull_url(pr["number"])
    stored = state.read_checks(str(tmp_path))[pr["head"]]
    assert faked_github.posts[-1][3] == canonical_url
    assert stored["url"] == canonical_url
    assert stored["status_posted"] is True


def test_zero_test_run_posts_an_explicit_failure(
        tmp_path, faked_github):
    pr = _pr(6)
    outcome = {**_outcome(pr, ok=False), "passed": 0, "failed": 1,
               "reason": "no tests executed"}

    daemon.finish_tier1(queue.World(state_dir=str(tmp_path)), pr, outcome,
                        seen_at=1.0, slot=0)

    assert faked_github.posts[-1][1] == "failure"
    assert faked_github.posts[-1][2] == "ren-ci: no tests executed"


def test_a_crash_posts_pending_never_error(monkeypatch, tmp_path,
                                           faked_github):
    # PRs #2087-2089: the invalid-range crash posted error states that
    # block merges, on PRs never judged. A crash is the daemon's
    # failure: pending (retry), with the crash visible in the refused
    # list, never an error verdict on the PR.
    faked_github.prs = [_pr(9)]

    def boom(world, pr, workers):
        raise RuntimeError("git diff deadbee..f00 failed: invalid range")

    monkeypatch.setattr(daemon, "run_tier1", boom)
    world = queue.World(state_dir=str(tmp_path))
    done = daemon.pass_once(world, plan={"tier1_jobs": 1, "pytest_n": 2,
                                         "window": 0, "headroom": 0.0,
                                         "heavy": []})
    assert done["checked"] == []
    assert any("crashed" in line for line in done["refused"])
    pending = [call for call in faked_github.posts
               if len(call) >= 2 and call[1] == "pending"]
    assert pending, faked_github.posts
    assert not any(len(call) >= 2 and call[1] == "error"
                   for call in faked_github.posts)
