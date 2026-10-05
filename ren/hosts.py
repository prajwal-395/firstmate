"""One adapter record per supported chat harness.

Ren is driven from a chat harness the person is signed in to with a
subscription (OAuth), never through an API key (captain's phase-one
scope: no direct API calls). This module is the single registry of the
hosts Ren supports: how each is detected, how its version is read, how
its sign-in is CHECKED (read-only - a token is never printed), where Ren
installs its skills and marker hook for it, and what Ren capabilities
need from it.

`ren doctor` reports every detected host with its version and auth
state, and for an unsupported or unauthenticated host says exactly what
is missing and the command that fixes it. `ren setup` configures the
detected host. The parallel Rust front-door lane delegates here, so
these records stay in Python.

Stdlib only: detection and auth-checking must run before the engine's
interpreter exists, and the tests drive it against fakes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROBE_TIMEOUT_S = 30
"""How long a host binary may take to answer before it is 'unreadable'."""

_OAUTH_CLAUDE_METHODS = ("claude.ai", "oauth", "oauth_token")
"""Claude Code `auth status` values that mean a subscription sign-in."""

_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")
"""The first version-like token in a `--version` line, whatever prefixes it."""


@dataclass(frozen=True)
class HostAdapter:
    """One supported host: what Ren needs from it and what it installs."""

    name: str
    """The `ren setup-hooks --app` id and the registry key."""

    label: str
    """How the doctor and the customer docs name it."""

    binary: str
    """What `shutil.which` looks for on PATH."""

    min_version: str
    """The host version Ren's setup was verified against - the oldest
    host Ren is declared to work with (measured 2026-10-05)."""

    install: str
    """How a person installs it, for the fix message."""

    skills: str
    """Where Ren's skills live for this host."""

    hook: str
    """Where the marker hook is installed, for the customer docs."""


ADAPTERS = (
    HostAdapter(
        name="claude-code",
        label="Claude Code",
        binary="claude",
        min_version="2.1.289",
        install="install Claude Code from https://claude.com/claude-code",
        skills="`.claude/skills` in the engine checkout, symlinked to the "
               "one skill source `.agents/skills/`",
        hook="a SessionStart entry merged into `~/.claude/settings.json`",
    ),
    HostAdapter(
        name="opencode",
        label="OpenCode",
        binary="opencode",
        min_version="1.18.34",
        install="install OpenCode from https://opencode.ai",
        skills="`.agents/skills/` in the engine checkout, symlinked into "
               "`~/.config/opencode/skills/`",
        hook="the plugin copied to `~/.config/opencode/plugins/ren-marker-hook.js`",
    ),
    HostAdapter(
        name="codex",
        label="Codex",
        binary="codex",
        min_version="0.159.3",
        install="install the Codex CLI from https://openai.com/codex",
        skills="`.agents/skills/` in the engine checkout, symlinked into "
               "`~/.codex/skills/`",
        hook="a session_start entry merged into `~/.codex/hooks.json`",
    ),
)
"""Every host Ren supports, in the order the doctor reports them."""

BY_NAME = {adapter.name: adapter for adapter in ADAPTERS}


@dataclass
class AuthState:
    """What a host's sign-in check found. `detail` is safe to print."""

    authenticated: bool
    detail: str
    fix: str = ""


@dataclass
class HostProbe:
    """One detected host: where it is, its version, its sign-in state."""

    adapter: HostAdapter
    path: str
    version: str
    version_ok: bool
    auth: AuthState

    @property
    def ok(self) -> bool:
        return self.version_ok and self.auth.authenticated


def detected() -> list:
    """Every supported host whose binary is on PATH, in registry order."""
    return [adapter for adapter in ADAPTERS if shutil.which(adapter.binary)]


def parse_version(text: str) -> str:
    """The version in a `--version` line, or "" when there is none."""
    match = _VERSION_RE.search(text or "")
    return match.group(0) if match else ""


def version_at_least(have: str, minimum: str) -> bool:
    """Compare dotted versions by number, so 1.10.0 is above 1.9.0."""
    def parts(version: str) -> list:
        return [int(p) for p in version.split(".")]
    return parts(have) >= parts(minimum)


def _ask(binary: str, args: tuple) -> str:
    """What a host binary answered, or "" when it could not be asked."""
    try:
        done = subprocess.run([binary, *args], capture_output=True,
                              encoding="utf-8", timeout=PROBE_TIMEOUT_S,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout + done.stderr).strip()


def _claude_auth(binary: str) -> AuthState:
    """Claude Code sign-in, from its own `auth status` (read-only).

    The answer carries an email and an org id; only the auth method and
    the subscription plan are read, and nothing else is printed.
    """
    fix = (f"run `{binary}` and sign in with your Claude account "
            "(an API key does not count)")
    try:
        done = subprocess.run([binary, "auth", "status"], capture_output=True,
                              encoding="utf-8", timeout=PROBE_TIMEOUT_S,
                              check=False)
        status = json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return AuthState(False, "could not ask its sign-in state", fix)
    if not isinstance(status, dict) or not status.get("loggedIn"):
        return AuthState(False, "not signed in", fix)
    method = status.get("authMethod", "")
    if method in _OAUTH_CLAUDE_METHODS:
        plan = status.get("subscriptionType", "")
        return AuthState(True, f"{method} OAuth" + (f", {plan}" if plan else ""))
    return AuthState(False,
                     f"signed in with {method or 'an API key'}, not a subscription",
                     fix)


def _opencode_auth() -> AuthState:
    """OpenCode sign-in, from its auth file (read-only).

    Only each provider's NAME and type are read; the file also carries
    tokens, and neither this function nor the doctor ever prints one.
    """
    fix = ("run `opencode` and sign in with a provider account "
           "(an API key does not count)")
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    auth_path = Path(data_home) / "opencode" / "auth.json"
    try:
        entries = json.loads(auth_path.read_text(encoding="utf-8"))
        kinds = {name: (entry or {}).get("type", "")
                 for name, entry in entries.items()}
    except (OSError, ValueError, AttributeError):
        return AuthState(False, "not signed in", fix)
    oauth = sorted(name for name, kind in kinds.items() if kind == "oauth")
    if oauth:
        return AuthState(True, f"OAuth: {', '.join(oauth)}")
    if kinds:
        return AuthState(False,
                         "signed in with an API key, not a subscription", fix)
    return AuthState(False, "not signed in", fix)


def _codex_auth(binary: str) -> AuthState:
    """Codex sign-in, from its own `login status` (read-only)."""
    fix = (f"run `{binary} login` and sign in with your ChatGPT account "
           "(an API key does not count)")
    text = _ask(binary, ("login", "status"))
    if not text:
        return AuthState(False, "could not ask its sign-in state", fix)
    if "ChatGPT" in text:
        return AuthState(True, "ChatGPT sign-in")
    return AuthState(False, text.splitlines()[0], fix)


def probe(adapter: HostAdapter) -> HostProbe:
    """Ask one detected host its version and sign-in state."""
    path = shutil.which(adapter.binary) or ""
    version = parse_version(_ask(adapter.binary, ("--version",)))
    if not version:
        auth = AuthState(False, "installed but its version is unreadable",
                         f"upgrade {adapter.label} to {adapter.min_version}+")
        return HostProbe(adapter, path, "", False, auth)
    version_ok = version_at_least(version, adapter.min_version)
    if adapter.name == "claude-code":
        auth = _claude_auth(adapter.binary)
    elif adapter.name == "opencode":
        auth = _opencode_auth()
    else:
        auth = _codex_auth(adapter.binary)
    return HostProbe(adapter, path, version, version_ok, auth)


def harness_checks() -> list:
    """The doctor's chat-harness lines: one aggregate, then one per host.

    The aggregate `chat_harness` line is the need the model capabilities
    read: it fails only when NO detected host is ok, so one
    unauthenticated host degrades nothing - Ren runs on the host that
    is signed in. Each per-host line is detail: its version, its auth
    state, and the command that fixes it. A per-host line carries the
    informational need `chat_harness.<host>`, which no capability
    requires and `machine_needs.NEEDS` does not list - a host that is
    not signed in is a MISS about that host, never a failure of the
    whole harness.
    """
    from ren.doctor import Check

    probes = [probe(adapter) for adapter in detected()]
    if not probes:
        first = ADAPTERS[0]
        return [Check(
            "chat harness", False,
            f"no chat harness installed ({', '.join(a.label for a in ADAPTERS)})",
            f"{first.install}, then run `{first.binary}` to sign in with "
            "your Claude account (an API key does not count)",
            need="chat_harness")]

    ok_hosts = []
    not_counted = []
    for result in probes:
        if result.ok:
            ok_hosts.append(f"{result.adapter.label} ({result.auth.detail})")
        else:
            not_counted.append(f"{result.adapter.label}: {result.auth.detail}")

    if ok_hosts:
        detail = "; ".join(ok_hosts)
        if not_counted:
            detail += f"; not counted: {', '.join(not_counted)}"
        aggregate = Check("chat harness", True, detail, need="chat_harness")
    else:
        aggregate = Check(
            "chat harness", False,
            "no harness signed in with a subscription"
            f" ({'; '.join(not_counted)})",
            f"{ADAPTERS[0].install}, then run `{ADAPTERS[0].binary}` to sign in "
            "with your Claude account (an API key does not count)",
            need="chat_harness")

    checks = [aggregate]
    for result in probes:
        label = f"chat harness {result.adapter.label}"
        if result.ok:
            checks.append(Check(label, True,
                                f"{result.path} {result.version}; {result.auth.detail}",
                                need=f"chat_harness.{result.adapter.name}"))
            continue
        problems = []
        if not result.version_ok:
            problems.append(f"{result.path} {result.version} is below the "
                            f"{result.adapter.min_version} minimum")
        if not result.auth.authenticated:
            problems.append(result.auth.detail)
        fix = result.auth.fix or f"upgrade {result.adapter.label} to {result.adapter.min_version}+"
        checks.append(Check(label, False, "; ".join(problems), fix,
                            need=f"chat_harness.{result.adapter.name}"))
    return checks
