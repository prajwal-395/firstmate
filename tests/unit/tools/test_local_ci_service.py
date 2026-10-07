"""The launchd agent contract: where it runs, where it logs, that it restarts.

Defect caught: an agent that runs on login but never comes back from a
crash, logs where nobody looks, or points at a disposable worktree that
is gone by the time it restarts. Install itself is manual (only after
the delivering PR merges), so this pins the plist RENDER, not the
machine's launchd state.
"""

from library.tools.local_ci import config, service


def test_plist_restarts_logs_and_names_the_service():
    text = service.render_plist("/repo/checkout")
    assert f"<string>{service.LABEL}</string>" in text
    assert "<key>KeepAlive</key><true/>" in text
    assert "<key>RunAtLoad</key><true/>" in text
    assert f"{config.log_dir()}/daemon.log" in text
    assert f"{config.log_dir()}/daemon.err" in text


def test_plist_points_at_the_repo_checkout_not_a_worktree():
    text = service.render_plist("/repo/checkout")
    assert "/repo/checkout/bin/vep" in text
    assert "library.tools.local_ci" in text
    assert "daemon" in text


def test_plist_carries_a_bootstrap_capable_path():
    # launchd's default PATH finds macOS python 3.9, which cannot parse
    # ren/__init__.py - the crash that met the first install. The plist
    # must lead with a 3.10+ python dir.
    import plistlib
    text = service.render_plist("/repo/checkout")
    assert "<key>PATH</key>" in text
    parsed = plistlib.loads(text.encode("utf-8"))
    path = parsed["EnvironmentVariables"]["PATH"]
    first, _, _ = path.partition(":")
    import subprocess
    version = subprocess.run(
        [f"{first}/python3", "-c", "import sys; print(sys.version_info[0])"],
        capture_output=True, encoding="utf-8", check=False, timeout=30)
    assert version.returncode == 0 and int(version.stdout.strip()) >= 3
    minor = subprocess.run(
        [f"{first}/python3", "-c",
         "import sys; print(sys.version_info[1])"],
        capture_output=True, encoding="utf-8", check=False, timeout=30)
    assert int(minor.stdout.strip()) >= 10


def test_agent_lives_in_the_user_domain_with_machine_state():
    assert service.plist_path().endswith(
        "Library/LaunchAgents/com.ren.ci.plist")
    assert config.state_dir().startswith("/"), "state must be absolute"
    assert "video-editing-pilot" not in config.state_dir(), (
        "daemon state must not live inside any repo checkout")


def test_launchd_state_checks_the_user_domain(monkeypatch):
    import subprocess

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(service.os, "getuid", lambda: 501)
    monkeypatch.setattr(service.subprocess, "run", run)

    assert service.launchd_state() == "loaded"
    assert calls == [(
        ["launchctl", "print", f"gui/501/{service.LABEL}"],
        {"capture_output": True, "encoding": "utf-8",
         "check": False, "timeout": 30})]


def test_launchd_state_reports_a_missing_job(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        service.subprocess, "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 113, "", ""))

    assert service.launchd_state() == "not loaded"


def test_launchd_state_reports_unknown_when_launchctl_is_unavailable(
        monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("launchctl")

    monkeypatch.setattr(service.subprocess, "run", unavailable)

    assert service.launchd_state() == "unknown"


def _git_repo(path, branch="main"):
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", branch, path],
                   check=True, timeout=30)
    subprocess.run(["git", "-C", path, "config", "user.email", "t@t"],
                   check=True, timeout=30)
    subprocess.run(["git", "-C", path, "config", "user.name", "t"],
                   check=True, timeout=30)
    (path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", path, "add", "."],
                   check=True, timeout=30)
    subprocess.run(["git", "-C", path, "commit", "-qm", "init"],
                   check=True, timeout=30)
    return path


def test_install_guard_accepts_clean_main(tmp_path):
    repo = str(_git_repo(tmp_path / "repo"))
    service.check_install_repo(repo)  # raises nothing


def test_install_guard_refuses_a_branch(tmp_path):
    import subprocess
    repo = str(_git_repo(tmp_path / "repo"))
    subprocess.run(["git", "-C", repo, "checkout", "-qb", "feature"],
                   check=True, timeout=30)
    try:
        service.check_install_repo(repo)
    except RuntimeError as exc:
        assert "install from main" in str(exc)
    else:
        raise AssertionError("branched checkout installed")


def test_install_guard_refuses_dirt(tmp_path):
    repo = str(_git_repo(tmp_path / "repo"))
    (tmp_path / "repo" / "dirty.txt").write_text("x", encoding="utf-8")
    try:
        service.check_install_repo(repo)
    except RuntimeError as exc:
        assert "dirty" in str(exc)
    else:
        raise AssertionError("dirty checkout installed")
