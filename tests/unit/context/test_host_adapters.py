"""Detection, version and auth checks for the supported chat harnesses.

Every test runs against fakes: a fake HOME, a fake XDG_DATA_HOME and
fake harness binaries on PATH. No real harness is asked, no real
config is read, and a token planted in a fake auth file must never
reach a doctor line.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import machine_needs  # noqa: E402
from ren import doctor, hosts  # noqa: E402


def _fake_env(tmp_path, monkeypatch):
    """A fake HOME, XDG_DATA_HOME and a bin dir on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return bin_dir


def _write_executable(script, body):
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return script


def _fake_claude(bin_dir, version, auth):
    return _write_executable(bin_dir / "claude",
        "#!/bin/sh\n"
        'case "$1" in\n'
        f'  --version) echo "{version} (Claude Code)";;\n'
        f"  auth) echo '{auth}';;\n"
        "esac\n")


def _fake_codex(bin_dir, version, login):
    return _write_executable(bin_dir / "codex",
        "#!/bin/sh\n"
        'case "$1" in\n'
        f'  --version) echo "codex-cli {version}";;\n'
        f'  login) echo "{login}";;\n'
        "esac\n")


def _fake_opencode(bin_dir, entries):
    _write_executable(bin_dir / "opencode", "#!/bin/sh\necho '1.18.34'\n")
    directory = bin_dir.parent / "xdg" / "opencode"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "auth.json").write_text(json.dumps(entries),
                                         encoding="utf-8")


def _checks_by_name(checks):
    return {check.name: check for check in checks}


def test_a_signed_in_host_reports_its_version_and_subscription(tmp_path, monkeypatch):
    """A detected host is one detail line: where it is, its version, its
    sign-in."""
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _fake_claude(bin_dir, "2.1.289",
                 '{"loggedIn": true, "authMethod": "claude.ai", '
                 '"subscriptionType": "max"}')

    checks = _checks_by_name(hosts.harness_checks())

    assert hosts.detected() == [hosts.BY_NAME["claude-code"]]
    line = checks["chat harness Claude Code"]
    assert line.ok and line.need == "chat_harness.claude-code"
    assert "2.1.289" in line.detail and "claude.ai OAuth" in line.detail
    assert line.fix == ""


def test_an_unauthenticated_host_says_what_is_missing_and_the_fix(tmp_path, monkeypatch):
    """An API-key sign-in is not a subscription: the detail line fails
    with the exact command that fixes it."""
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _fake_claude(bin_dir, "2.1.289",
                 '{"loggedIn": true, "authMethod": "api-key"}')

    line = _checks_by_name(hosts.harness_checks())["chat harness Claude Code"]

    assert not line.ok
    assert "not a subscription" in line.detail
    assert line.fix == ("run `claude` and sign in with your Claude account "
                        "(an API key does not count)")


def test_a_host_below_the_minimum_version_fails_with_the_version(tmp_path, monkeypatch):
    """An old host is a failure that names the minimum, not a pass."""
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _fake_claude(bin_dir, "2.1.288",
                 '{"loggedIn": true, "authMethod": "claude.ai"}')

    line = _checks_by_name(hosts.harness_checks())["chat harness Claude Code"]

    assert not line.ok
    assert "2.1.288" in line.detail and "2.1.289" in line.detail
    assert "upgrade Claude Code" in line.fix


def test_an_unreadable_version_is_a_failure_not_a_pass(tmp_path, monkeypatch):
    """A binary that will not say its version is reported as such."""
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _write_executable(bin_dir / "claude", "#!/bin/sh\necho 'garbage'\n")

    line = _checks_by_name(hosts.harness_checks())["chat harness Claude Code"]

    assert not line.ok
    assert "unreadable" in line.detail


def test_no_host_detected_names_the_install_and_the_sign_in(tmp_path, monkeypatch):
    """With nothing on PATH the aggregate line says what is missing and
    the command that fixes it."""
    _fake_env(tmp_path, monkeypatch)

    checks = hosts.harness_checks()

    assert hosts.detected() == []
    assert len(checks) == 1
    line = checks[0]
    assert not line.ok and line.need == "chat_harness"
    assert "no chat harness installed" in line.detail
    assert "Claude Code" in line.detail
    assert "claude" in line.fix and "API key does not count" in line.fix


def test_one_ok_host_satisfies_chat_harness_even_when_another_is_not(tmp_path, monkeypatch):
    """The regression: a host that is not signed in is a detail line
    that degrades nothing. `chat_harness` is satisfied by any one ok
    host, so the model capabilities stay available and the doctor does
    not fail."""
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _fake_claude(bin_dir, "2.1.289",
                 '{"loggedIn": true, "authMethod": "claude.ai"}')
    _fake_opencode(bin_dir, {"anthropic": {"type": "api", "key": "planted"}})

    checks = hosts.harness_checks()
    by_name = _checks_by_name(checks)

    aggregate = by_name["chat harness"]
    assert aggregate.ok and aggregate.need == "chat_harness"
    assert "Claude Code (claude.ai OAuth)" in aggregate.detail
    assert "not counted: OpenCode" in aggregate.detail

    detail = by_name["chat harness OpenCode"]
    assert not detail.ok and detail.need == "chat_harness.opencode"
    assert "not a subscription" in detail.detail

    verdict, _missing, degraded = doctor.capability_report(checks)["creative.direct"]
    assert verdict == machine_needs.AVAILABLE and not degraded
    assert doctor.required_failures(checks) == []


def test_no_ok_host_fails_the_aggregate_with_the_install_fix(tmp_path, monkeypatch):
    """With hosts detected but none signed in, the aggregate fails and
    names the fix."""
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _fake_claude(bin_dir, "2.1.289",
                 '{"loggedIn": true, "authMethod": "api-key"}')

    aggregate = _checks_by_name(hosts.harness_checks())["chat harness"]

    assert not aggregate.ok and aggregate.need == "chat_harness"
    assert "no harness signed in with a subscription" in aggregate.detail
    assert "Claude Code: signed in with api-key" in aggregate.detail
    assert "API key does not count" in aggregate.fix


def test_opencode_oauth_is_read_from_its_auth_file_without_printing_tokens(
        tmp_path, monkeypatch):
    """The auth file's provider names and types are read; the tokens in
    the same file are never printed."""
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _fake_opencode(bin_dir, {
        "anthropic": {"type": "oauth", "token": "planted-secret-token"},
        "other": {"type": "api", "key": "planted-api-key"},
    })

    line = _checks_by_name(hosts.harness_checks())["chat harness OpenCode"]

    assert line.ok
    assert line.need == "chat_harness.opencode"
    assert "OAuth: anthropic" in line.detail
    assert "planted-secret-token" not in line.detail
    assert "planted-api-key" not in line.detail


def test_opencode_api_key_is_not_a_subscription(tmp_path, monkeypatch):
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _fake_opencode(bin_dir, {"anthropic": {"type": "api", "key": "planted-api-key"}})

    line = _checks_by_name(hosts.harness_checks())["chat harness OpenCode"]

    assert not line.ok
    assert "API key" in line.detail
    assert line.fix == ("run `opencode` and sign in with a provider account "
                        "(an API key does not count)")
    assert "planted-api-key" not in line.detail


def test_codex_chatgpt_sign_in_counts(tmp_path, monkeypatch):
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _fake_codex(bin_dir, "0.159.3", "Logged in using ChatGPT")

    line = _checks_by_name(hosts.harness_checks())["chat harness Codex"]

    assert line.ok
    assert line.need == "chat_harness.codex"
    assert "0.159.3" in line.detail and "ChatGPT" in line.detail


def test_codex_not_signed_in_carries_the_fix(tmp_path, monkeypatch):
    bin_dir = _fake_env(tmp_path, monkeypatch)
    _fake_codex(bin_dir, "0.159.3", "Not logged in. Run /login?")

    line = _checks_by_name(hosts.harness_checks())["chat harness Codex"]

    assert not line.ok
    assert "Not logged in" in line.detail
    assert line.fix == ("run `codex login` and sign in with your ChatGPT "
                        "account (an API key does not count)")


def test_setup_configures_the_detected_host(monkeypatch, capsys):
    """`ren setup` installs the marker hook for every detected host."""
    from ren import setup, setup_hooks

    calls = []
    monkeypatch.setattr(hosts, "detected",
                        lambda: [hosts.BY_NAME["claude-code"],
                                 hosts.BY_NAME["codex"]])
    monkeypatch.setattr(setup_hooks, "install",
                        lambda app, write=False: calls.append((app, write)) or 0)

    setup._configure_host()

    assert calls == [("claude-code", True), ("codex", True)]
    out = capsys.readouterr().out
    assert "configure the detected chat harness" in out


def test_setup_without_a_host_says_so_and_installs_nothing(monkeypatch, capsys):
    """No detected host means no guessed hook: setup names the follow-up."""
    from ren import setup, setup_hooks

    monkeypatch.setattr(hosts, "detected", lambda: [])
    monkeypatch.setattr(setup_hooks, "install",
                        lambda app, write=False: 0)

    setup._configure_host()

    out = capsys.readouterr().out
    assert "no chat harness detected" in out
    assert "ren setup-hooks --list" in out
