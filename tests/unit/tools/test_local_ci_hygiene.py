"""Resource hygiene: checkouts die, floors refuse, slots exclude, leaks fail.

Each test names the scratch-script failure it prevents: the disk that
filled to 100%, the checkout nobody deleted, the third slot that
stuttered the editor, the broker a test run left behind.
"""

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from library.tools.local_ci import config, hygiene, runner


def _git(cwd, *argv):
    result = subprocess.run(["git", "-C", cwd, *argv], capture_output=True,
                            encoding="utf-8", check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def mirror(tmp_path):
    repo = str(tmp_path / "mirror")
    os.makedirs(repo)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ren-ci@test")
    _git(repo, "config", "user.name", "ren-ci")
    (tmp_path / "mirror" / "file.txt").write_text("v1", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def test_a_checkout_is_deleted_when_the_body_fails(mirror, tmp_path):
    state = str(tmp_path / "state")
    rev = _git(mirror, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError), hygiene.disposable_checkout(
            mirror, rev, state) as checkout:
        assert os.path.isfile(os.path.join(checkout, "file.txt"))
        raise RuntimeError("the tests failed")
    leftovers = os.listdir(os.path.join(state, "checkouts"))
    assert leftovers == [], f"checkout debris: {leftovers}"
    # The mirror forgot it too: no phantom worktree entries accumulate.
    assert "run-" not in _git(mirror, "worktree", "list")


def test_a_checkout_is_deleted_on_success(mirror, tmp_path):
    state = str(tmp_path / "state")
    rev = _git(mirror, "rev-parse", "HEAD")
    with hygiene.disposable_checkout(mirror, rev, state):
        pass
    assert os.listdir(os.path.join(state, "checkouts")) == []


def test_parallel_checkouts_get_unique_paths_in_the_same_second(
        mirror, tmp_path, monkeypatch):
    state = str(tmp_path / "state")
    rev = _git(mirror, "rev-parse", "HEAD")
    add_barrier = threading.Barrier(2)
    entered = threading.Event()
    entered_paths = []
    entered_lock = threading.Lock()
    original_git = hygiene._git

    def synchronized_git(repo, *argv, **kwargs):
        if argv[:2] == ("worktree", "add"):
            add_barrier.wait(timeout=10)
        return original_git(repo, *argv, **kwargs)

    def hold_checkout():
        with hygiene.disposable_checkout(mirror, rev, state) as checkout:
            with entered_lock:
                entered_paths.append(checkout)
                if len(entered_paths) == 2:
                    entered.set()
            assert entered.wait(timeout=3), "both worktrees must be live together"
            return checkout

    monkeypatch.setattr(hygiene.time, "time", lambda: 1_800_000_000.25)
    monkeypatch.setattr(hygiene, "_git", synchronized_git)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(hold_checkout) for _ in range(2)]
        paths = [future.result(timeout=10) for future in futures]

    assert len(set(paths)) == 2
    assert os.listdir(os.path.join(state, "checkouts")) == []


def test_parallel_workers_share_one_safely_created_mirror(mirror, tmp_path,
                                                           monkeypatch):
    remote = str(tmp_path / "remote.git")
    _git(str(tmp_path), "clone", "--bare", mirror, remote)
    state = str(tmp_path / "state")
    mirror_git_dir = os.path.join(state, "mirror", ".git")
    original_isdir = os.path.isdir
    both_observed_missing = threading.Barrier(2)

    def observe_same_missing_path(path):
        exists = original_isdir(path)
        if os.fspath(path) == mirror_git_dir and not exists:
            try:
                both_observed_missing.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                # The mirror lock lets only one worker inspect a missing
                # path at a time; the second observes the completed clone.
                pass
        return exists

    monkeypatch.setattr(hygiene.os.path, "isdir", observe_same_missing_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(hygiene.ensure_mirror, remote, state)
                   for _ in range(2)]
        errors = [future.exception(timeout=30) for future in futures]

    assert errors == [None, None]


def test_parallel_workers_serialize_fetches_into_the_shared_mirror(
        mirror, monkeypatch):
    start_together = threading.Barrier(2)
    guard = threading.Lock()
    active = 0
    maximum = 0

    def tracked_git(_repo, *argv, **_kwargs):
        nonlocal active, maximum
        if argv[0] == "fetch":
            assert argv[2].startswith("+pull/")
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.1)
            with guard:
                active -= 1
            return ""
        if argv[0] == "rev-parse":
            return "a" * 40
        raise AssertionError(f"unexpected git call: {argv}")

    monkeypatch.setattr(hygiene, "_git", tracked_git)

    def fetch(number):
        start_together.wait(timeout=10)
        return hygiene.fetch_pr(mirror, number)

    with ThreadPoolExecutor(max_workers=2) as pool:
        heads = list(pool.map(fetch, (1, 2)))

    assert heads == ["a" * 40, "a" * 40]
    assert maximum == 1


def test_slots_report_the_live_job_and_clear_it_on_exit(tmp_path):
    state = str(tmp_path / "state")
    slots = hygiene.Slots(state, jobs=2)
    job = {"kind": "tier1", "pr": 42, "head": "a" * 40}

    with slots.claim(job) as slot:
        assert slot == 0
        active = hygiene.running_jobs(state)
        assert len(active) == 1
        assert active[0]["kind"] == "tier1"
        assert active[0]["pr"] == 42
        assert active[0]["head"] == "a" * 40
        assert active[0]["elapsed_seconds"] >= 0

    assert hygiene.running_jobs(state) == []


def test_running_jobs_ignores_metadata_from_a_dead_process(tmp_path):
    slots_dir = tmp_path / "state" / "slots"
    slots_dir.mkdir(parents=True)
    (slots_dir / "slot-0.json").write_text(
        '{"pid": 2147483647, "slot": 0, "started_at": 1, '
        '"kind": "tier1", "pr": 42, "head": "' + "a" * 40 + '"}\n',
        encoding="utf-8")

    assert hygiene.running_jobs(str(tmp_path / "state")) == []


def test_slots_exclude_the_third_acquirer(tmp_path):
    slots = hygiene.Slots(str(tmp_path), jobs=2)
    with slots.claim() as first:
        assert first == 0
        with slots.claim() as second:
            assert second == 1
            with slots.claim() as third:
                assert third is None  # all busy: schedule, never wait
        with slots.claim() as again:
            assert again == 1  # inner exit freed exactly its slot


def test_disk_floor_refuses_and_names_the_shortfall(monkeypatch, tmp_path):
    class Usage:
        free = config.DISK_FLOOR_BYTES - 1
    monkeypatch.setattr(hygiene.shutil, "disk_usage", lambda path: Usage())
    assert not hygiene.disk_ok(str(tmp_path))
    assert hygiene.disk_free(str(tmp_path)) == config.DISK_FLOOR_BYTES - 1


class _Proc:
    def __init__(self, code=0, timeout=False):
        self.returncode = code
        self.pid = 2_000_000_000
        self.timeout = timeout

    def wait(self, timeout=None):
        if self.timeout:
            raise subprocess.TimeoutExpired("pytest", timeout)
        return self.returncode


def _pytest_fake(log_text, code=0):
    def fake(cmd, **kwargs):
        out = kwargs.get("stdout")
        if hasattr(out, "write"):
            out.write(log_text)
        return _Proc(code)
    return fake


def test_a_new_broker_after_the_run_fails_the_check(monkeypatch, tmp_path):
    monkeypatch.setattr(hygiene, "broker_serving", _sequence([False, True]))
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(runner, "_pytest_python", lambda: sys.executable)
    monkeypatch.setattr("subprocess.Popen", _pytest_fake("3 passed in 1s\n"))
    result = runner.run(["tests/contracts"], _checkout_with(tmp_path),
                        str(tmp_path / "run.log"))
    assert result["broker_leaked"] is True
    assert result["ok"] is False


def test_no_new_broker_passes_a_green_run(monkeypatch, tmp_path):
    monkeypatch.setattr(hygiene, "broker_serving", _sequence([False, False]))
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(runner, "_pytest_python", lambda: sys.executable)
    monkeypatch.setattr("subprocess.Popen", _pytest_fake("3 passed in 1s\n"))
    result = runner.run(["tests/contracts"], _checkout_with(tmp_path),
                        str(tmp_path / "run.log"))
    assert result == {"passed": 3, "failed": 0, "ok": True, "exit": 0,
                      "elapsed": result["elapsed"], "timed_out": False,
                      "timeout_diagnostic": "",
                      "process_group_leaked": False,
                      "broker_leaked": False, "log": result["log"]}


def test_a_run_that_never_prints_a_summary_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(hygiene, "broker_serving", _sequence([False, False]))
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(runner, "_pytest_python", lambda: sys.executable)
    monkeypatch.setattr("subprocess.Popen",
                        _pytest_fake("ERROR collecting tests\n", code=2))
    result = runner.run(["tests/contracts"], _checkout_with(tmp_path),
                        str(tmp_path / "run.log"))
    assert result["ok"] is False
    assert result["failed"] == 1  # 0/0 is a failed run, never a pass


def test_a_hung_run_times_out_and_still_reports(monkeypatch, tmp_path):
    def hanging(cmd, **kwargs):
        kwargs["stdout"].write("tests/example.py::test_waiting\n")
        return _Proc(code=None, timeout=True)

    monkeypatch.setattr(hygiene, "broker_serving", _sequence([False, False]))
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(runner, "_pytest_python", lambda: sys.executable)
    monkeypatch.setattr("subprocess.Popen", hanging)
    monkeypatch.setattr(runner, "_terminate_process_group", lambda process: True)
    result = runner.run(["tests/contracts"], _checkout_with(tmp_path),
                        str(tmp_path / "run.log"), timeout=1)
    assert result["timed_out"] is True
    assert result["ok"] is False
    assert "REN-CI TIMEOUT" in (tmp_path / "run.log").read_text()


def test_timeout_terminates_the_isolated_pytest_process_group(tmp_path):
    child_pid = tmp_path / "child.pid"
    child_stopped = tmp_path / "child.stopped"
    child_code = (
        "import signal, sys, time; "
        f"signal.signal(signal.SIGTERM, lambda *_: (open({str(child_stopped)!r}, 'w').close(), sys.exit(0))); "
        "time.sleep(30)"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"open({str(child_pid)!r}, 'w').write(str(child.pid)); "
        "time.sleep(30)"
    )
    result = runner._run_logged(
        [sys.executable, "-c", parent_code], str(tmp_path),
        str(tmp_path / "run.log"), timeout=0.5)
    assert result["timed_out"] is True
    assert result["group_clean"] is True
    assert child_stopped.exists()


def test_pytest_logs_test_names_and_dumps_hung_test_stacks():
    argv = runner.pytest_argv(["tests/contracts"], workers=1)
    assert "-v" in argv
    assert argv[argv.index("-o") + 1] == "faulthandler_timeout=300"


def test_timeout_diagnostic_keeps_the_blocked_test_frame(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(
        'File "tests/unit/tools/test_local_ci_hygiene.py", line 155, '
        "in test_timeout\n", encoding="utf-8")
    assert runner._timeout_diagnostic(str(log)) == (
        'File "tests/unit/tools/test_local_ci_hygiene.py", line 155, '
        "in test_timeout")


def test_gate_deference_refuses_instead_of_fighting(monkeypatch, tmp_path):
    monkeypatch.setattr(hygiene, "gate_running", lambda: True)
    with pytest.raises(hygiene.HygieneRefused, match="full-suite gate"):
        runner.run(["tests/contracts"], str(tmp_path),
                   str(tmp_path / "run.log"))


def test_an_unfetchable_base_defers_instead_of_crashing(tmp_path):
    # PR #2087's crash: main moved between the mirror fetch and the
    # main_sha read, so the base object was missing and the diff died
    # with "invalid revision range" - posted onto the PR as an error.
    # A missing base is a STALE MIRROR, retried next pass, never a
    # verdict on the PR.
    mirror = str(tmp_path / "mirror")
    os.makedirs(mirror)
    subprocess.run(["git", "init", "-q", mirror], check=True, timeout=30)
    with pytest.raises(hygiene.HygieneRefused, match="not fetchable"):
        hygiene.fetch_base(mirror, "a27441728ddca71b9f4af928da4b28881484996f")


def _sequence(values):
    iterator = iter(values)
    return lambda: next(iterator)


def _checkout_with(tmp_path):
    (tmp_path / "tests" / "contracts").mkdir(parents=True)
    return str(tmp_path)
