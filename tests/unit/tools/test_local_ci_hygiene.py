"""Resource hygiene: checkouts die, floors refuse, slots exclude, leaks fail.

Each test names the scratch-script failure it prevents: the disk that
filled to 100%, the checkout nobody deleted, the third slot that
stuttered the editor, the broker a test run left behind.
"""

import os
import subprocess

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
    with pytest.raises(RuntimeError):
        with hygiene.disposable_checkout(mirror, rev, state) as checkout:
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
    def __init__(self, code=0):
        self.returncode = code


def _pytest_fake(log_text, code=0):
    def fake(cmd, **kwargs):
        out = kwargs.get("stdout")
        if hasattr(out, "write"):
            out.write(log_text)
        if kwargs.get("capture_output"):
            return subprocess.CompletedProcess(cmd, code, stdout="", stderr="")
        return _Proc(code)
    return fake


def test_a_new_broker_after_the_run_fails_the_check(monkeypatch, tmp_path):
    monkeypatch.setattr(hygiene, "broker_serving", _sequence([False, True]))
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr("subprocess.run", _pytest_fake("3 passed in 1s\n"))
    result = runner.run(["tests/contracts"], str(tmp_path),
                        str(tmp_path / "run.log"))
    assert result["broker_leaked"] is True
    assert result["ok"] is False


def test_no_new_broker_passes_a_green_run(monkeypatch, tmp_path):
    monkeypatch.setattr(hygiene, "broker_serving", _sequence([False, False]))
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr("subprocess.run", _pytest_fake("3 passed in 1s\n"))
    result = runner.run(["tests/contracts"], str(tmp_path),
                        str(tmp_path / "run.log"))
    assert result == {"passed": 3, "failed": 0, "ok": True, "exit": 0,
                      "elapsed": result["elapsed"], "timed_out": False,
                      "broker_leaked": False, "log": result["log"]}


def test_a_run_that_never_prints_a_summary_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(hygiene, "broker_serving", _sequence([False, False]))
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr("subprocess.run",
                        _pytest_fake("ERROR collecting tests\n", code=2))
    result = runner.run(["tests/contracts"], str(tmp_path),
                        str(tmp_path / "run.log"))
    assert result["ok"] is False
    assert result["failed"] == 1  # 0/0 is a failed run, never a pass


def test_a_hung_run_times_out_and_still_reports(monkeypatch, tmp_path):
    def hanging(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(hygiene, "broker_serving", _sequence([False, False]))
    monkeypatch.setattr(hygiene, "gate_running", lambda: False)
    monkeypatch.setattr("subprocess.run", hanging)
    result = runner.run(["tests/contracts"], str(tmp_path),
                        str(tmp_path / "run.log"), timeout=1)
    assert result["timed_out"] is True
    assert result["ok"] is False


def test_gate_deference_refuses_instead_of_fighting(monkeypatch, tmp_path):
    monkeypatch.setattr(hygiene, "gate_running", lambda: True)
    with pytest.raises(hygiene.HygieneRefused, match="full-suite gate"):
        runner.run(["tests/contracts"], str(tmp_path),
                   str(tmp_path / "run.log"))


def _sequence(values):
    iterator = iter(values)
    return lambda: next(iterator)
