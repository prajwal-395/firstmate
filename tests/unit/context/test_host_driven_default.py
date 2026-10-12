"""T2: the agent backend is the default when a host drives.

`ren edit` (manage_project `run`) selects `--full-auto agent` when
`ren.hosts.driving_host` says a chat harness drives this process -
the explicit `REN_HOST` override or a documented harness marker.
An explicit `--full-auto` value always wins, and `--full-auto none`
opts out. `ren new`'s Next steps prints the command that will
actually interview.

Each test names the defect it pins: a host-driven run that stayed
manual (the interview never fired), and a printed command without
the backend that interviews.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO))

import manage_project  # noqa: E402
from ren import hosts  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_host_env(monkeypatch):
    """Clear every marker driving_host reads, so the harness running
    the suite (Claude Code, OpenCode, Codex) never leaks into the test.
    Tests then opt back in with setenv for the marker they pin."""
    monkeypatch.delenv(hosts.REN_HOST_ENV_VAR, raising=False)
    for _, markers in hosts.HOST_RUNTIME_MARKERS:
        for marker in markers:
            monkeypatch.delenv(marker, raising=False)


# ── driving_host ─────────────────────────────────────────────────

def test_no_override_no_marker_means_no_driver(monkeypatch):
    monkeypatch.delenv("REN_HOST", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    assert hosts.driving_host() is None


def test_empty_ren_host_is_not_a_driver(monkeypatch):
    monkeypatch.setenv("REN_HOST", "  ")
    assert hosts.driving_host() is None


def test_ren_host_names_the_driver(monkeypatch):
    monkeypatch.setenv("REN_HOST", "opencode")
    assert hosts.driving_host() == "opencode"


def test_ren_host_canonicalizes_a_supported_binary(monkeypatch):
    monkeypatch.setenv("REN_HOST", "claude")
    assert hosts.driving_host() == "claude-code"


def test_ren_host_wins_over_markers(monkeypatch):
    monkeypatch.setenv("REN_HOST", "opencode")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert hosts.driving_host() == "opencode"


def test_claude_code_marker_drives(monkeypatch):
    monkeypatch.delenv("REN_HOST", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert hosts.driving_host() == "claude-code"


def test_a_switched_off_marker_does_not_drive(monkeypatch):
    monkeypatch.delenv("REN_HOST", raising=False)
    monkeypatch.setenv("CLAUDECODE", "0")
    assert hosts.driving_host() is None


def test_mere_installation_is_not_driving(monkeypatch):
    """A harness on PATH but not driving changes nothing.

    `detected()` may list idle installs; `driving_host()` must not
    read that as a driver, or every machine with a harness
    installed would flip its default.
    """
    monkeypatch.delenv("REN_HOST", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(hosts, "detected",
                        lambda: list(hosts.ADAPTERS))
    assert hosts.driving_host() is None


# ── --full-auto values ───────────────────────────────────────────

def test_none_is_an_accepted_backend_value():
    assert manage_project._full_auto_backend("none") == "none"


def test_hidden_compatibility_values_still_pass():
    assert manage_project._full_auto_backend("agy") == "agy"
    assert manage_project._full_auto_backend("api") == "api"


def test_junk_backend_is_still_refused():
    with pytest.raises(Exception):
        manage_project._full_auto_backend("carrier-pigeon")


# ── cmd_run defaulting ───────────────────────────────────────────

def _run_args(**overrides):
    base = dict(slug="demo", from_step=None, step=None, dry_run=True,
                auto=False, review=False, resume=False, rerun=[],
                target=None, only=[], skip=[], with_steps=[],
                overrides=[], profile=None, break_at=[],
                no_break_at=[], full_auto=None, llm_timeout=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def _drive_run(monkeypatch, args):
    """Run cmd_run with the runner stubbed; return (cmd, stderr)."""
    seen = {}

    def fake_get_project(slug):
        assert slug == "demo"
        return SimpleNamespace(name="Demo",
                               project_root=Path("/tmp/demo"))

    class Done:
        returncode = 0

    def fake_run(cmd, env=None, check=False):
        seen["cmd"] = list(cmd)
        return Done()

    monkeypatch.setattr(manage_project, "get_project", fake_get_project)
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as exit_info:
        manage_project.cmd_run(args)
    assert exit_info.value.code == 0
    return seen["cmd"]


def test_host_driven_run_defaults_to_agent(monkeypatch, capsys):
    monkeypatch.setenv("REN_HOST", "opencode")
    cmd = _drive_run(monkeypatch, _run_args())
    assert "--full-auto" in cmd
    assert cmd[cmd.index("--full-auto") + 1] == "agent"
    err = capsys.readouterr().err
    assert "--full-auto agent" in err
    assert "--full-auto none" in err


def test_no_host_keeps_todays_default(monkeypatch):
    monkeypatch.delenv("REN_HOST", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    cmd = _drive_run(monkeypatch, _run_args())
    assert "--full-auto" not in cmd


def test_none_opts_out_under_a_host(monkeypatch):
    monkeypatch.setenv("REN_HOST", "opencode")
    cmd = _drive_run(monkeypatch, _run_args(full_auto="none"))
    assert "--full-auto" not in cmd


def test_explicit_backend_wins_over_the_host(monkeypatch):
    monkeypatch.setenv("REN_HOST", "opencode")
    cmd = _drive_run(monkeypatch, _run_args(full_auto="api"))
    assert cmd[cmd.index("--full-auto") + 1] == "api"


# ── ren new Next steps ───────────────────────────────────────────

def _new_args(**overrides):
    base = dict(slug="demo", name="Demo", client="", template="",
                fps="", resolve_name="", tags="", description="",
                shape="speech", language="en", speaker=["Ann"],
                no_speakers=False, brief_title="T", brand_series="s",
                non_interactive=True)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_new_next_steps_prints_the_command_that_interviews(
        monkeypatch, capsys):
    def fake_create_project(**kwargs):
        return SimpleNamespace(
            name="Demo", slug="demo",
            project_root=Path("/tmp/demo"),
            raw_dir=Path("/tmp/demo/raw"),
            resolve=SimpleNamespace(project_name="Demo"),
            pipeline=SimpleNamespace(brand_template=""),
            source=SimpleNamespace(language="en", shape="speech",
                                   speakers=[{"name": "Ann"}]))

    monkeypatch.setattr(manage_project, "create_project",
                        fake_create_project)
    manage_project.cmd_new(_new_args())
    out = capsys.readouterr().out
    assert "--full-auto agent" in out
    assert "or just tell your chat what you want" in out
