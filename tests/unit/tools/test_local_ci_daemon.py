"""Tier-1 orchestration: parallel checks, serial ledger, latency.

Defects caught: two new heads where only one gets checked (serial
leftover), a ledger write lost to a thread race, a red ready-PR that
keeps its queue label, latency nobody recorded (untunable), ready PRs
starved of Tier-1 by a ripe Tier-2 batch (never entering the queue),
timed-out Tier-1 runs posted as failures with no retry, a Tier-2 gate
that stalls the dispatch pass (no new-head detection until the gate
exits), two passes starting two Tier-2 clusters at once, and an
interrupted-queue recovery that rewrites the ledger under a running
Tier-2 worker.
GitHub is faked; the ledger is real (tmp state dir).
"""

import json
import threading
import time
from pathlib import Path
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
    from library.tools.local_ci import github, upstream
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
    # Dispatch tests pin head detection and slotting, never upstream
    # scheduling: the weekly engine intake (network refs + heavy
    # qualification, pinned in test_local_ci_upstream.py) must not
    # fire from an idle-looking scripted pass.
    monkeypatch.setattr(upstream, "run_due",
                        lambda state_dir, *, idle: {"poll": None,
                                                   "intake": None})
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


def test_zero_test_run_posts_pending_not_a_code_failure(
        tmp_path, faked_github):
    pr = _pr(6)
    outcome = {**_outcome(pr, ok=False), "passed": 0, "failed": 1,
               "reason": "no tests executed"}

    daemon.finish_tier1(queue.World(state_dir=str(tmp_path)), pr, outcome,
                        seen_at=1.0, slot=0)

    assert faked_github.posts[-1][1] == "pending"
    assert "no tests executed" in faked_github.posts[-1][2]


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


def test_timeout_exhaustion_stays_pending_and_keeps_the_label(
        monkeypatch, tmp_path, faked_github):
    # Retry counts are diagnostic only: an incomplete run never judges
    # code and therefore cannot become a red status at exhaustion.
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
    assert not any(len(call) >= 2 and call[1] == "failure"
                   for call in faked_github.posts)
    assert any(len(call) >= 2 and call[1] == "pending"
               for call in faked_github.posts)
    assert ("unlabel", 22, config.READY_LABEL) not in faked_github.posts
    assert record["result_class"] == "timeout_no_tests"


def test_no_tests_and_stale_base_are_non_verdicts(
        tmp_path, faked_github):
    world = queue.World(state_dir=str(tmp_path))
    for number, kind, reason in ((23, "timeout_no_tests", "no tests executed"),
                                 (24, "stale_base", "stale base")):
        pr = _pr(number, labels=[config.READY_LABEL])
        outcome = {**_outcome(pr, ok=False), "passed": 0, "failed": 0,
                   "result_class": kind, "reason": reason}
        record = daemon.finish_tier1(world, pr, outcome,
                                     seen_at=time.time(), slot=0)
        assert record["result_class"] == kind
        assert not any(call[0] == pr["head"] and call[1] == "failure"
                       for call in faked_github.posts)
        assert not any(call[0] == "unlabel" and call[1] == number
                       for call in faked_github.posts)


def test_three_day_ledger_fixture_classifies_each_head_once():
    fixture = json.loads(Path("tests/fixtures/local_ci_three_day_tier1_ledger.json")
                         .read_text(encoding="utf-8"))
    heads = fixture["heads"]
    assert len({row["head"] for row in heads}) == len(heads)
    categories = [daemon.classify_tier1(row["outcome"]) for row in heads]
    assert categories == [row["expected"] for row in heads]
    assert {name: categories.count(name) for name in set(categories)} == {
        "pass": 34, "test_failure": 19, "timeout_no_tests": 3}


def test_later_full_gate_failure_links_to_green_tier1_head(tmp_path):
    head = "green-head"
    state.record_check({"pr": 31, "head": head, "ok": True,
                        "selected_files": ["tests/unit/example.py"],
                        "skipped_files": {"tests/unit/deferred.py": "budget"}},
                       str(tmp_path))
    state.link_full_run([head], {"run_id": "tier2-1.log",
                                 "base_sha": "base", "stack_sha": "stack",
                                 "verdict": "FAIL",
                                 "failed_tests": ["tests/unit/example.py"],
                                 "log": "/logs/tier2-1.log"}, str(tmp_path))
    record = state.read_checks(str(tmp_path))[head]
    assert record["full_run_results"][0]["failed_tests"] == [
        "tests/unit/example.py"]
    assert state.read_queue(str(tmp_path)) == []


def test_tier1_runs_while_tier2_is_ripe(
        monkeypatch, tmp_path, faked_github):
    # PRs 2287-2302 sat queued-ready for hours without a Tier-1 run,
    # so they never entered the Tier-2 queue: a ripe batch (with
    # chained bisects behind it) refused every new head. Tier-1 keeps
    # running while a cluster is ripe; the heavy-work lock - not the
    # dispatcher - serializes heavy execution (runner.run refuses
    # while a full-suite gate holds the machine).
    old = time.time() - 1000
    state.write_queue(
        [{"pr": 31, "head": "head-31", "status": state.ENTRY_QUEUED,
          "reason": "", "enqueued_at": old}], str(tmp_path))
    faked_github.prs = [_pr(32)]
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, pr, workers: _outcome(pr))
    world = queue.World(state_dir=str(tmp_path))
    done = daemon.pass_once(world, plan={"tier1_jobs": 2, "pytest_n": 1,
                                         "window": 90, "headroom": 9.0,
                                         "heavy": []})
    assert done["checked"] == [32]
    assert not any("Tier-2 batch" in line for line in done["refused"])
    assert done["queue"]["stubbed"] is True


def test_green_ready_head_joins_the_queue_while_tier2_is_ripe(
        monkeypatch, tmp_path, faked_github):
    # The captain's demand: every PR labelled ready actually enters
    # the queue, even with a ripe batch ahead of it - so the next
    # Tier-2 run tests it instead of leaving it unqueued for hours.
    old = time.time() - 1000
    state.write_queue(
        [{"pr": 31, "head": "head-31", "status": state.ENTRY_QUEUED,
          "reason": "", "enqueued_at": old}], str(tmp_path))
    faked_github.prs = [_pr(32, labels=[config.READY_LABEL])]
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, pr, workers: _outcome(pr))
    world = queue.World(state_dir=str(tmp_path))
    done = daemon.pass_once(world, plan={"tier1_jobs": 2, "pytest_n": 1,
                                         "window": 90, "headroom": 9.0,
                                         "heavy": []})
    assert done["checked"] == [32]
    assert done["queued"] == [32]
    queued = {entry["pr"]: entry
              for entry in state.read_queue(str(tmp_path))}
    assert queued[32]["head"] == "head-32"


def test_tier2_fires_as_soon_as_running_tier1_drains(
        monkeypatch, tmp_path):
    # The idle gate still holds: the sync path runs Tier-1 inline and
    # the same pass must still run the Tier-2 batch once the slots
    # have drained. (No faked_github fixture here: it stubs
    # queue.process_once, which is exactly what this test needs real.)
    from library.tools.local_ci import github
    old = time.time() - 1000
    state.write_queue(
        [{"pr": 33, "head": "head-33", "status": state.ENTRY_QUEUED,
          "reason": "", "enqueued_at": old}], str(tmp_path))
    monkeypatch.setattr(github, "list_open_prs", lambda: [_pr(34)])
    monkeypatch.setattr(github, "post_status", lambda *a, **k: None)
    monkeypatch.setattr(github, "remove_label", lambda *a, **k: None)
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, pr, workers: _outcome(pr))

    class BatchWorld(queue.World):
        def main_sha(self):
            return "base"

        def pr_head(self, pr):
            return f"head-{pr}"

        def proof_requirements(self, pr):
            # Scripted worlds owe no live proofs, and no test spawns
            # a real gh-axi read at merge time.
            return []

        def missing_proofs(self, pr, head, required):
            return []

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
    assert done["checked"] == [34]
    assert done["queue"]["ripe"] is True
    assert done["queue"]["merged"] == [33]


def test_scheduler_starts_tier1_while_tier2_is_ripe(
        monkeypatch, tmp_path, faked_github):
    # Async path: poll_once starts new Tier-1 jobs even with a ripe
    # cluster waiting, and Tier-2 starts on its own lane once those
    # workers drain and their outcomes are collected - the starting
    # pass returns while the cluster evaluates, and the next pass
    # collects its summary.
    old = time.time() - 1000
    state.write_queue(
        [{"pr": 35, "head": "head-35", "status": state.ENTRY_QUEUED,
          "reason": "", "enqueued_at": old}], str(tmp_path))
    faked_github.prs = [_pr(36)]
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, pr, workers: _outcome(pr))
    monkeypatch.setattr(daemon.hygiene, "running_jobs", lambda _s: [])
    monkeypatch.setattr(daemon.hygiene, "gate_running", lambda: False)
    scheduler = daemon.Tier1Scheduler(queue.World(state_dir=str(tmp_path)))
    plan = {"tier1_jobs": 2, "pytest_n": 1, "window": 90,
            "headroom": 9.0, "heavy": []}
    try:
        done = scheduler.poll_once(plan)
        assert done["started"] == [36]
        assert "tier2_priority" not in done
        deadline = time.monotonic() + 5
        while not scheduler.has_completed() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert scheduler.has_completed()
        collected = scheduler.poll_once(plan)
        assert collected["checked"] == [36]
        assert collected["queue"] == {"ripe": True, "started": True}
        deadline = time.monotonic() + 5
        while not scheduler.has_completed() and time.monotonic() < deadline:
            time.sleep(0.01)
        finished = scheduler.poll_once(plan)
        assert finished["queue"]["stubbed"] is True
    finally:
        scheduler.shutdown(wait=True)


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


def test_success_status_names_the_tier2_only_files(tmp_path, faked_github):
    # The four slow files Tier-1 skips on purpose are named on the
    # status line, by slug (full paths cannot fit the 140-char
    # description); the report line's marker carries the full paths.
    # (The contract audit was fifth until its fast graph half moved
    # into the universal set; only its source surveys are selected
    # for library changes now.)
    assert daemon.tier2_only_slugs() == [
        "gate-smoke", "resolve-watchdog",
        "golden-monologue", "golden-conversation"]
    marker = daemon.tier2_only_marker()
    for path in config.TIER1_TIER2_ONLY:
        assert path in marker
    pr = _pr(38)
    record = daemon.finish_tier1(queue.World(state_dir=str(tmp_path)), pr,
                                 _outcome(pr), seen_at=1.0, slot=0)
    assert record["ok"] is True
    description = faked_github.posts[-1][2]
    assert "gate-smoke" in description
    assert "resolve-watchdog" in description
    assert len(description) <= 140


class _BlockingGateWorld(queue.World):
    """Scripted Tier-2: the gate blocks until the test releases it.

    The queue.World seam already supports this - test_stack is the
    only gate the tests script, so no live gate ever runs here.
    """

    def __init__(self, state_dir, entered, release):
        super().__init__(state_dir=state_dir)
        self._entered = entered
        self._release = release
        self.stacks = 0

    def main_sha(self):
        return "base"

    def pr_head(self, pr):
        return f"head-{pr}"

    def proof_requirements(self, pr):
        # Scripted worlds owe no live proofs, and no test spawns a
        # real gh-axi read at merge time.
        return []

    def missing_proofs(self, pr, head, required):
        return []

    def test_stack(self, base, members):
        self.stacks += 1
        self._entered.set()
        assert self._release.wait(timeout=30)
        return queue.TestResult(True, 3, 0)

    def merge_pr(self, pr, head):
        return f"merge-{pr}"

    def report(self, pr, head, url, ok, reason):
        pass


def _mock_scripted_github(monkeypatch, prs):
    from library.tools.local_ci import github, upstream
    monkeypatch.setattr(github, "list_open_prs", lambda: list(prs))
    monkeypatch.setattr(github, "post_status", lambda *a, **k: None)
    monkeypatch.setattr(github, "remove_label", lambda *a, **k: None)
    monkeypatch.setattr(github, "main_sha", lambda: "base")
    monkeypatch.setattr(daemon.hygiene, "running_jobs", lambda _s: [])
    monkeypatch.setattr(daemon.hygiene, "gate_running", lambda: False)
    # Dispatch tests pin head detection and slotting, never upstream
    # scheduling: the weekly engine intake (network refs + heavy
    # qualification, pinned in test_local_ci_upstream.py) must not
    # fire from an idle-looking scripted pass.
    monkeypatch.setattr(upstream, "run_due",
                        lambda state_dir, *, idle: {"poll": None,
                                                   "intake": None})


def _ripe_queue(tmp_path, *members):
    old = time.time() - 1000
    state.write_queue(
        [{"pr": number, "head": f"head-{number}",
          "status": status, "reason": "", "enqueued_at": old}
         for number, status in members], str(tmp_path))


def test_dispatch_detects_new_heads_while_tier2_gate_runs(
        monkeypatch, tmp_path):
    # CI-04 acceptance: on main the pass that starts a Tier-2 cluster
    # blocks inside the gate, so no new-head detection happens until
    # the gate exits. After: the starting pass returns while the
    # scripted gate is still running, and the next pass detects and
    # slots new heads (within load.targets) beside it.
    prs: list = []
    _mock_scripted_github(monkeypatch, prs)
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, pr, workers: _outcome(pr))
    _ripe_queue(tmp_path, (60, state.ENTRY_QUEUED))
    entered = threading.Event()
    release = threading.Event()
    world = _BlockingGateWorld(str(tmp_path), entered, release)
    scheduler = daemon.Tier1Scheduler(world)
    plan = {"tier1_jobs": 2, "pytest_n": 1, "window": 90,
            "headroom": 9.0, "heavy": []}
    first: dict = {}
    errors: list = []

    def start():
        try:
            first.update(scheduler.poll_once(plan))
        except Exception as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    starter = threading.Thread(target=start)
    try:
        starter.start()
        # The starting pass returns while the gate is still in flight -
        # on main it stays stuck inside process_once past this join.
        starter.join(timeout=10)
        assert not errors
        assert not starter.is_alive()
        assert first["queue"] == {"ripe": True, "started": True}
        assert entered.wait(timeout=10)
        testing = [entry for entry in state.read_queue(str(tmp_path))
                   if entry["status"] == state.ENTRY_TESTING]
        assert [entry["pr"] for entry in testing] == [60]

        prs.append(_pr(61))
        second = scheduler.poll_once(plan)
        assert entered.is_set() and not release.is_set()
        assert second["started"] == [61]
        assert second["queue"]["in_flight"] is True
        assert second["queue"]["testing"] == [60]
        assert world.stacks == 1

        release.set()
        deadline = time.monotonic() + 30
        third: dict = {}
        while time.monotonic() < deadline:
            third = scheduler.poll_once(plan)
            if third["queue"].get("merged") == [60]:
                break
            time.sleep(0.05)
        assert third["queue"].get("merged") == [60]
        assert world.stacks == 1
    finally:
        release.set()
        starter.join(timeout=30)
        scheduler.shutdown(wait=True)


def test_concurrent_passes_start_only_one_tier2_cluster(
        monkeypatch, tmp_path):
    # Two passes racing on a ripe cluster start exactly one
    # evaluation: Tier-2 owns the machine alone.
    prs: list = []
    _mock_scripted_github(monkeypatch, prs)
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, pr, workers: _outcome(pr))
    _ripe_queue(tmp_path, (62, state.ENTRY_QUEUED))
    entered = threading.Event()
    release = threading.Event()
    world = _BlockingGateWorld(str(tmp_path), entered, release)
    scheduler = daemon.Tier1Scheduler(world)
    plan = {"tier1_jobs": 2, "pytest_n": 1, "window": 90,
            "headroom": 9.0, "heavy": []}
    try:
        racers = [threading.Thread(
            target=scheduler.poll_once, args=(plan,)) for _ in range(2)]
        for racer in racers:
            racer.start()
        assert entered.wait(timeout=10)
        time.sleep(0.5)
        for racer in racers:
            racer.join(timeout=10)
            assert not racer.is_alive()
        assert world.stacks == 1
    finally:
        release.set()
        for racer in racers:
            racer.join(timeout=30)
        scheduler.shutdown(wait=True)


def test_interrupted_recovery_waits_for_inflight_tier2(
        monkeypatch, tmp_path):
    # A testing row this worker never saw (a dead daemon's leftover)
    # appears beside a running cluster: while our Tier-2 lane runs,
    # the pass must not recover it - recover_interrupted_queue's
    # contract requires establishing that no gate is still active,
    # and the worker owns the ledger until its verdict lands. Once
    # the lane drains, the next idle pass recovers it.
    prs: list = []
    _mock_scripted_github(monkeypatch, prs)
    monkeypatch.setattr(daemon, "run_tier1",
                        lambda world, pr, workers: _outcome(pr))
    _ripe_queue(tmp_path, (71, state.ENTRY_QUEUED))
    entered = threading.Event()
    release = threading.Event()
    world = _BlockingGateWorld(str(tmp_path), entered, release)
    scheduler = daemon.Tier1Scheduler(world)
    plan = {"tier1_jobs": 2, "pytest_n": 1, "window": 90,
            "headroom": 9.0, "heavy": []}
    first: dict = {}
    errors: list = []

    def start():
        try:
            first.update(scheduler.poll_once(plan))
        except Exception as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    starter = threading.Thread(target=start)
    try:
        starter.start()
        starter.join(timeout=10)
        assert not errors
        assert not starter.is_alive()
        assert first["queue"] == {"ripe": True, "started": True}
        assert entered.wait(timeout=10)

        leftover = {"pr": 70, "head": "head-70",
                    "status": state.ENTRY_TESTING, "reason": "",
                    "enqueued_at": time.time() - 1000}
        entries = state.read_queue(str(tmp_path))
        entries.append(leftover)
        state.write_queue(entries, str(tmp_path))

        second = scheduler.poll_once(plan)
        assert entered.is_set() and not release.is_set()
        assert second["queue"]["in_flight"] is True
        assert set(second["queue"]["testing"]) == {70, 71}
        by_pr = {entry["pr"]: entry
                 for entry in state.read_queue(str(tmp_path))}
        assert by_pr[70]["status"] == state.ENTRY_TESTING
        assert "interrupted" not in by_pr[70]["reason"]

        release.set()
        starter.join(timeout=30)
        deadline = time.monotonic() + 30
        collected: dict = {}
        while time.monotonic() < deadline:
            collected = scheduler.poll_once(plan)
            if collected["queue"].get("merged") == [71]:
                break
            time.sleep(0.05)
        assert collected["queue"].get("merged") == [71]
        # The worker's write-back left the foreign testing row alone;
        # an idle pass recovers it instead of losing it (the collect
        # pass above already counts as idle once the lane drains).
        scheduler.poll_once(plan)
        by_pr = {entry["pr"]: entry
                 for entry in state.read_queue(str(tmp_path))}
        assert by_pr[70]["status"] == state.ENTRY_QUEUED
        assert "interrupted" in by_pr[70]["reason"]
        assert world.stacks == 1
    finally:
        release.set()
        starter.join(timeout=30)
        scheduler.shutdown(wait=True)


def test_success_status_names_which_mandatory_half_ran(
        tmp_path, faked_github):
    # A contracts-skipped success must never read as a
    # contracts-passed one: the status names the half and the why.
    pr = _pr(39)
    skipped = {**_outcome(pr),
               "selection_detail": {"mandatory_contracts": False,
                                    "mandatory_reason": "docs-only change"}}
    daemon.finish_tier1(queue.World(state_dir=str(tmp_path)), pr,
                        skipped, seen_at=1.0, slot=0)
    description = faked_github.posts[-1][2]
    assert "no-contracts: docs-only change" in description
    pr = _pr(40)
    included = {**_outcome(pr),
                "selection_detail": {"mandatory_contracts": True,
                                     "mandatory_reason": "registry"}}
    daemon.finish_tier1(queue.World(state_dir=str(tmp_path)), pr,
                        included, seen_at=1.0, slot=0)
    assert " [+contracts]" in faked_github.posts[-1][2]


def test_legacy_records_keep_the_legacy_description(
        tmp_path, faked_github):
    # Records written before the two-tier policy carry no mandatory
    # key: the description stays exactly as posted then, rather than
    # guessing which half ran.
    pr = _pr(41)
    daemon.finish_tier1(queue.World(state_dir=str(tmp_path)), pr,
                        _outcome(pr), seen_at=1.0, slot=0)
    description = faked_github.posts[-1][2]
    assert "[+contracts]" not in description
    assert "no-contracts" not in description
