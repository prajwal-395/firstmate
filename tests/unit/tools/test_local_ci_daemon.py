"""Tier-1 orchestration: parallel checks, serial ledger, latency.

Defects caught: two new heads where only one gets checked (serial
leftover), a ledger write lost to a thread race, a red ready-PR that
keeps its queue label, latency nobody recorded (untunable), a ripe
Tier-2 cluster starved by steady Tier-1 arrivals, and timed-out Tier-1
runs posted as failures with no retry.
GitHub is faked; the ledger is real (tmp state dir).
"""

import threading
import time
from types import SimpleNamespace

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


def test_daemon_polls_and_dispatches_while_a_check_is_running(
        monkeypatch, tmp_path, faked_github):
    first, second = _pr(1), _pr(2)
    faked_github.prs = [first]
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def run_tier1(world, pr, workers):
        if pr["number"] == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
        else:
            second_started.set()
        return _outcome(pr)

    monkeypatch.setattr(daemon, "run_tier1", run_tier1)
    monkeypatch.setattr(daemon.hygiene, "gate_running", lambda: False)
    scheduler = daemon.Tier1Scheduler(queue.World(state_dir=str(tmp_path)))
    plan = {"tier1_jobs": 2, "pytest_n": 1, "window": 0,
            "headroom": 9.0, "heavy": []}
    try:
        first_poll = scheduler.poll_once(plan)
        assert first_poll["started"] == [1]
        assert first_started.wait(timeout=2)

        faked_github.prs = [first, second]
        second_poll = scheduler.poll_once(plan)
        assert second_poll["started"] == [2]
        assert second_started.wait(timeout=2)

        deadline = time.monotonic() + 2
        while not scheduler.has_completed() and time.monotonic() < deadline:
            time.sleep(0.01)
        completed = scheduler.poll_once(plan)
        checks = state.read_checks(str(tmp_path))
        assert completed["checked"] == [2]
        assert "head-2" in checks
        assert "head-1" not in checks
        assert not release_first.is_set()
    finally:
        release_first.set()
        scheduler.shutdown(wait=True)
        cleanup = {"checked": [], "queued": [], "refused": [], "queue": {}}
        scheduler.collect(cleanup)


def test_serve_polls_immediately_when_a_worker_finishes(
        monkeypatch, tmp_path):
    class StopLoop(Exception):
        pass

    class FakeScheduler:
        def __init__(self, world):
            self.polls = 0

        def has_completed(self):
            return self.polls == 1

        def poll_once(self, plan):
            self.polls += 1
            return {"plan": "test", "started": [], "checked": [],
                    "queued": [], "refused": [], "queue": {}}

    clock = [0.0]
    scheduler = FakeScheduler(None)

    def fake_sleep(seconds):
        clock[0] += seconds
        if scheduler.polls == 2:
            raise StopLoop

    monkeypatch.setattr(daemon, "Tier1Scheduler", lambda world: scheduler)
    monkeypatch.setattr(daemon.load, "measure", dict)
    monkeypatch.setattr(daemon.load, "targets", lambda snapshot: {})
    monkeypatch.setattr(
        daemon, "time",
        SimpleNamespace(monotonic=lambda: clock[0], sleep=fake_sleep))

    with pytest.raises(StopLoop):
        daemon.serve(str(tmp_path), poll=30)

    assert scheduler.polls == 2


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


def test_red_tier1_removes_stale_queue_membership(
        monkeypatch, tmp_path, faked_github):
    pr = _pr(8, head="new-head", labels=[config.READY_LABEL])
    state.enqueue(pr["number"], "old-head", str(tmp_path))
    faked_github.prs = [pr]
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, current_pr, workers:
                        _outcome(current_pr, ok=False))
    world = queue.World(state_dir=str(tmp_path))

    done = daemon.pass_once(world, plan={"tier1_jobs": 1, "pytest_n": 2,
                                         "window": 0, "headroom": 0.0,
                                         "heavy": []})

    assert done["checked"] == [8]
    assert state.read_queue(str(tmp_path)) == []
    assert ("unlabel", 8, config.READY_LABEL) in faked_github.posts


def test_idle_daemon_recovers_interrupted_tier2_entries(
        monkeypatch, tmp_path, faked_github):
    faked_github.prs = []
    monkeypatch.setattr(daemon.hygiene, "running_jobs", lambda _state: [])
    monkeypatch.setattr(daemon.hygiene, "gate_running", lambda: False)
    state.write_queue([{"pr": 41, "head": "head-41",
                        "status": state.ENTRY_TESTING,
                        "reason": "", "enqueued_at": 1.0}],
                      str(tmp_path))
    scheduler = daemon.Tier1Scheduler(queue.World(state_dir=str(tmp_path)))
    try:
        scheduler.poll_once({"tier1_jobs": 1, "pytest_n": 1,
                             "window": 90, "headroom": 9.0,
                             "heavy": []})
    finally:
        scheduler.shutdown(wait=True)

    entry, = state.read_queue(str(tmp_path))
    assert entry["status"] == state.ENTRY_QUEUED
    assert "interrupted" in entry["reason"]


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


def test_zero_count_cached_success_is_retested(
        monkeypatch, tmp_path, faked_github):
    pr = _pr(7)
    stale = {**_outcome(pr), "ok": True, "passed": 0, "failed": 0,
             "status_posted": True, "seen_at": 1.0, "done_at": 2.0}
    state.record_check(stale, str(tmp_path))
    faked_github.prs = [pr]
    calls = []

    def run_once(world, current_pr, workers):
        calls.append(current_pr["head"])
        return _outcome(current_pr)

    monkeypatch.setattr(daemon, "run_tier1", run_once)
    world = queue.World(state_dir=str(tmp_path))
    done = daemon.pass_once(world, plan={"tier1_jobs": 1, "pytest_n": 2,
                                         "window": 0, "headroom": 0.0,
                                         "heavy": []})

    assert done["checked"] == [7]
    assert calls == [pr["head"]]
    assert state.read_checks(str(tmp_path))[pr["head"]]["passed"] == 5


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
              "url": str(tmp_path / "pr-5-head.log"),
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


def _timeout_outcome(pr):
    outcome = _outcome(pr, ok=False)
    outcome.update({"timed_out": True, "timeout_diagnostic": "tests/x.py::t",
                    "reason": ""})
    return outcome


def test_timeout_posts_pending_and_retries_after_backoff(
        monkeypatch, tmp_path, faked_github):
    # PRs 2264/2271/2272: a 30-minute timeout under load posted failure
    # and the head was marked seen, so the PR stayed red with no verdict
    # about its code. A timeout judges nothing: pending, re-queue once
    # the backoff elapses, keep the ready label and queue membership.
    pr = _pr(21, labels=[config.READY_LABEL])
    faked_github.prs = [pr]
    world = queue.World(state_dir=str(tmp_path))
    record = daemon.finish_tier1(world, pr, _timeout_outcome(pr),
                                 seen_at=time.time(), slot=0)
    assert record["timeout_retries"] == 1
    assert record["ok"] is False
    pending = [call for call in faked_github.posts
               if len(call) >= 2 and call[1] == "pending"]
    assert pending and "timed out" in pending[-1][2]
    assert ("unlabel", 21, config.READY_LABEL) not in faked_github.posts

    done = {"refused": []}
    due, _ = daemon._discover_due(world, done)
    assert due == []  # backing off: not due yet
    assert any("backoff" in line for line in done["refused"])

    future = {"timeout_retry_at": time.time() - 1}
    state.update_check(pr["head"], future, str(tmp_path))
    done = {"refused": []}
    due, _ = daemon._discover_due(world, done)
    assert [item[0]["number"] for item in due] == [21]


def test_timeout_exhaustion_posts_failure_and_drops_the_label(
        monkeypatch, tmp_path, faked_github):
    # At most two retries: the third timeout is a final failure, and
    # only then does the PR lose its ready label and queue membership.
    pr = _pr(22, labels=[config.READY_LABEL])
    faked_github.prs = [pr]
    world = queue.World(state_dir=str(tmp_path))
    state.enqueue(pr["number"], pr["head"], str(tmp_path))
    state.record_check({**_timeout_outcome(pr), "seen_at": 1.0,
                        "done_at": 2.0, "slot": 0, "timeout_retries": 2,
                        "timeout_retry_at": 0.0, "status_posted": True},
                       str(tmp_path))
    record = daemon.finish_tier1(world, pr, _timeout_outcome(pr),
                                 seen_at=time.time(), slot=0)
    assert int(record.get("timeout_retries", 0)) == 3
    failures = [call for call in faked_github.posts
                if len(call) >= 2 and call[1] == "failure"]
    assert failures and "timed out" in failures[-1][2]
    assert ("unlabel", 22, config.READY_LABEL) in faked_github.posts
    assert state.read_queue(str(tmp_path)) == []


def test_ripe_tier2_defers_new_tier1_until_the_batch_starts(
        monkeypatch, tmp_path, faked_github):
    # PRs 2265/2273 sat queued with Tier-1 green while 2274's Tier-1
    # ran: the event loop started Tier-1 whenever a slot freed, so a
    # steady stream of heads starved Tier-2. When Tier-2 is due, no new
    # Tier-1 starts until the batch has started.
    old = time.time() - 1000
    state.write_queue(
        [{"pr": 31, "head": "head-31", "status": state.ENTRY_QUEUED,
          "reason": "", "enqueued_at": old}], str(tmp_path))
    faked_github.prs = [_pr(32)]
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, pr, workers: _outcome(pr))
    monkeypatch.setattr(daemon.hygiene, "running_jobs", lambda _s: [])
    monkeypatch.setattr(daemon.hygiene, "gate_running", lambda: False)
    world = queue.World(state_dir=str(tmp_path))
    done = daemon.pass_once(world, plan={"tier1_jobs": 2, "pytest_n": 1,
                                         "window": 90, "headroom": 9.0,
                                         "heavy": []})
    assert done["checked"] == []
    assert done["tier2_priority"] is True
    assert any("Tier-2 batch" in line for line in done["refused"])


def test_tier2_fires_as_soon_as_running_tier1_drains(
        monkeypatch, tmp_path):
    # Priority without drain would wedge Tier-1 forever: with a ripe
    # cluster and no Tier-1 in flight, the same pass that defers Tier-1
    # must still run the Tier-2 batch. (No faked_github fixture here:
    # it stubs queue.process_once, which is exactly what this test
    # needs real.)
    from library.tools.local_ci import github
    old = time.time() - 1000
    state.write_queue(
        [{"pr": 33, "head": "head-33", "status": state.ENTRY_QUEUED,
          "reason": "", "enqueued_at": old}], str(tmp_path))
    monkeypatch.setattr(github, "list_open_prs", lambda: [_pr(34)])
    monkeypatch.setattr(github, "post_status", lambda *a, **k: None)
    monkeypatch.setattr(github, "remove_label", lambda *a, **k: None)

    class BatchWorld(queue.World):
        def main_sha(self):
            return "base"

        def pr_head(self, pr):
            return f"head-{pr}"

        def test_stack(self, base, members):
            return queue.TestResult(True, 3, 0)

        def merge_pr(self, pr, head):
            return f"merge-{pr}"

        def report(self, pr, head, url, ok, reason):
            pass

    world = BatchWorld(state_dir=str(tmp_path))
    monkeypatch.setattr(daemon.hygiene, "running_jobs", lambda _s: [])
    monkeypatch.setattr(daemon.hygiene, "gate_running", lambda: False)
    done = daemon.pass_once(world, plan={"tier1_jobs": 2, "pytest_n": 1,
                                         "window": 90, "headroom": 9.0,
                                         "heavy": []})
    assert done["started"] if "started" in done else True
    assert done["checked"] == []
    assert done["queue"]["ripe"] is True
    assert done["queue"]["merged"] == [33]


def test_scheduler_defers_new_tier1_while_tier2_is_ripe(
        monkeypatch, tmp_path, faked_github):
    # Async path: poll_once must also yield to a ripe cluster instead
    # of filling every free slot with new heads.
    old = time.time() - 1000
    state.write_queue(
        [{"pr": 35, "head": "head-35", "status": state.ENTRY_QUEUED,
          "reason": "", "enqueued_at": old}], str(tmp_path))
    faked_github.prs = [_pr(36)]
    monkeypatch.setattr(daemon.hygiene, "running_jobs", lambda _s: [])
    monkeypatch.setattr(daemon.hygiene, "gate_running", lambda: False)
    scheduler = daemon.Tier1Scheduler(queue.World(state_dir=str(tmp_path)))
    try:
        done = scheduler.poll_once({"tier1_jobs": 2, "pytest_n": 1,
                                    "window": 90, "headroom": 9.0,
                                    "heavy": []})
    finally:
        scheduler.shutdown(wait=True)
    assert done["started"] == []
    assert done["tier2_priority"] is True


def test_capped_selection_is_recorded_on_the_status(
        tmp_path, faked_github):
    # A capped Tier-1 run must read as capped, never as full coverage:
    # the success status carries the cap marker.
    pr = _pr(37)
    outcome = {**_outcome(pr), "selection_capped": True,
               "selection_detail": {"capped": True, "dependent_total": 83,
                                    "dependent_kept": 1, "budget": 40}}
    record = daemon.finish_tier1(queue.World(state_dir=str(tmp_path)), pr,
                                 outcome, seen_at=1.0, slot=0)
    assert record["selection_capped"] is True
    assert faked_github.posts[-1][1] == "success"
    assert "capped" in faked_github.posts[-1][2]
