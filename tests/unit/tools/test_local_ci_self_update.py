"""The daemon reloads onto new daemon code by itself, while idle.

Defect 2026-10-10: the launchd agent ran since the day before on the
code it started with. When the served checkout fast-forwarded to a
commit changing the daemon, the daily poll and weekly intake stranded
on stale code until a human restarted it. A reload must happen exactly
once per advance, only while idle, with the persisted poll/intake
windows surviving untouched.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from library.tools.local_ci import daemon, self_update


def _git(repo, *argv):
    subprocess.run(
        ["git", "-C", repo, *argv], check=True, capture_output=True, timeout=30
    )


def _served_repo(path):
    """A fake served checkout with daemon code plus an unrelated file."""
    _git(str(path), "init", "-q", "-b", "main")
    _git(str(path), "config", "user.email", "t@t")
    _git(str(path), "config", "user.name", "t")
    (path / "library" / "tools" / "local_ci").mkdir(parents=True)
    (path / "library" / "tools" / "local_ci" / "daemon.py").write_text(
        "v1", encoding="utf-8"
    )
    (path / "README.md").write_text("docs", encoding="utf-8")
    _git(str(path), "add", ".")
    _git(str(path), "commit", "-qm", "init")
    return _head(path)


def _head(path):
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    return result.stdout.strip()


def _commit(path, name, text):
    (path / name).write_text(text, encoding="utf-8")
    _git(str(path), "add", ".")
    _git(str(path), "commit", "-qm", f"change {name}")
    return _head(path)


def test_reload_onto_daemon_code_advance(tmp_path):
    repo = tmp_path / "served"
    repo.mkdir()
    running = _served_repo(repo)
    new = _commit(repo, "library/tools/local_ci/daemon.py", "v2")

    reload_sha, seen, _reason = self_update.should_reload(str(repo), running)

    assert reload_sha == new and seen == new


def test_no_reload_when_only_unrelated_files_move(tmp_path):
    repo = tmp_path / "served"
    repo.mkdir()
    running = _served_repo(repo)
    new = _commit(repo, "README.md", "more docs")

    reload_sha, seen, reason = self_update.should_reload(str(repo), running)

    assert reload_sha is None and seen == new
    assert "without daemon-code changes" in reason


def test_no_reload_when_head_unchanged(tmp_path):
    repo = tmp_path / "served"
    repo.mkdir()
    running = _served_repo(repo)

    assert self_update.should_reload(str(repo), running)[0] is None


def test_busy_daemon_defers_the_reload(monkeypatch, tmp_path):
    from library.tools.local_ci import hygiene

    monkeypatch.setattr(hygiene, "running_jobs", lambda _state: [{"slot": 0, "pr": 1}])
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(hygiene, "running_gate_jobs", list)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    repo = tmp_path / "served"
    repo.mkdir()
    running = _served_repo(repo)
    _commit(repo, "library/tools/local_ci/daemon.py", "v2")
    calls = []
    monkeypatch.setattr(self_update, "reexec", lambda *args: calls.append(args))
    busy = SimpleNamespace(idle=lambda: False)

    stayed = self_update.check_and_reload(busy, str(repo), running, str(state_dir), 30)

    assert stayed == running and calls == []


def test_unreadable_checkout_never_reloads_or_crashes(tmp_path):
    scheduler = SimpleNamespace(idle=lambda: True)
    reload_sha, seen, _reason = self_update.should_reload(
        str(tmp_path / "missing"), "deadbee" * 5
    )
    assert reload_sha is None and seen == ""
    assert self_update.served_commit("") == ""
    stayed = self_update.check_and_reload(
        scheduler, str(tmp_path / "missing"), "deadbee" * 5, str(tmp_path), 30
    )
    assert stayed == "deadbee" * 5


def test_reexec_targets_the_served_checkout(monkeypatch, tmp_path):
    import sys

    calls = {}
    monkeypatch.setattr(
        self_update.os, "execve", lambda *args: calls.setdefault("exec", args)
    )
    repo, state_dir = str(tmp_path / "served"), str(tmp_path / "state")

    self_update.reexec(repo, state_dir, 45)

    engine, argv, env = calls["exec"]
    assert engine == sys.executable
    assert argv == [
        sys.executable,
        "-m",
        "library.tools.local_ci",
        "daemon",
        "--state",
        state_dir,
        "--poll",
        "45",
        "--repo",
        repo,
    ]
    assert env["PYTHONPATH"].split(":")[0] == repo


def test_serve_reexecs_exactly_once_idle_with_state_preserved(monkeypatch, tmp_path):
    """The brief's fixture: the served checkout advances; the daemon
    re-execs exactly once, idle-only, with poll/intake state intact."""
    from library.tools.local_ci import hygiene

    monkeypatch.setattr(hygiene, "running_jobs", lambda _state: [])
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(hygiene, "running_gate_jobs", list)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    intake = {
        "schema_version": 1,
        "last_daily_poll": 1234.0,
        "weekly": {"2026-W41": {"status": "complete"}},
        "engines": {},
    }
    (state_dir / "upstream-intake.json").write_text(
        json.dumps(intake, indent=2, sort_keys=True), encoding="utf-8"
    )
    before = (state_dir / "upstream-intake.json").read_bytes()

    repo = tmp_path / "served"
    repo.mkdir()
    running = _served_repo(repo)
    _commit(repo, "library/tools/local_ci/daemon.py", "v2")

    exec_calls = []
    monkeypatch.setattr(self_update, "reexec", lambda *args: exec_calls.append(args))

    # StopLoop escapes the daemon's polling-must-continue handler
    # (which catches Exception) so the test can stop the serve loop.
    class StopLoop(BaseException):
        pass

    polls = []

    class FakeScheduler:
        def idle(self):
            return True

        def has_completed(self):
            return False

        def poll_once(self, plan):
            polls.append(1)
            if len(polls) == 2:
                raise StopLoop
            return {
                "plan": "test",
                "started": [],
                "checked": [],
                "queued": [],
                "refused": [],
                "queue": {},
            }

    monkeypatch.setattr(daemon, "Tier1Scheduler", lambda world: FakeScheduler())
    monkeypatch.setattr(daemon.load, "measure", dict)
    monkeypatch.setattr(daemon.load, "targets", lambda snapshot: {})
    clock = [0.0]

    def fake_sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(
        daemon, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=fake_sleep)
    )

    # The daemon started on v1 and the checkout fast-forwarded to v2
    # while it ran: the first HEAD read (startup) sees v1, later reads
    # see the advanced HEAD.
    new_head = _head(repo)
    reads = {"n": 0}

    def fake_served(repo_arg):
        reads["n"] += 1
        return running if reads["n"] == 1 else new_head

    monkeypatch.setattr(self_update, "served_commit", fake_served)
    with pytest.raises(StopLoop):
        daemon.serve(str(state_dir), poll=30, repo=str(repo))

    assert len(exec_calls) == 1
    assert exec_calls[0][0] == str(repo)
    assert (state_dir / "upstream-intake.json").read_bytes() == before
