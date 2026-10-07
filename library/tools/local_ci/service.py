"""Run as a service: a user-level launchd agent with auto-restart.

Install ONLY after the delivering PR merges (the brief's ordering):
the plist points at the checkout it was installed from, and a worktree
checkout is disposable by design. `ren ci install` records the repo it
ran in; installing from anywhere but main risks serving a branch.

- Label: com.ren.ci (user agent, ~/Library/LaunchAgents).
- Restart: KeepAlive + RunAtLoad + ThrottleInterval: a crash comes
  back, a reboot comes back, a tight crash loop does not spin.
- Logs: ~/Library/Logs/ren-ci/daemon.log (stdout) and .err (stderr).
- Uninstall: bootout + remove the plist. State and logs stay: a future
  install resumes the ledger, and history is never deleted by removal.
"""

from __future__ import annotations

import os
import subprocess

from library.tools.local_ci import config

LABEL = "com.ren.ci"
PLIST_NAME = f"{LABEL}.plist"


def plist_path() -> str:
    """The user agent's plist location."""
    return os.path.join(os.path.expanduser("~"), "Library",
                        "LaunchAgents", PLIST_NAME)


def _launchd_path() -> str:
    """A PATH under which bin/vep's bootstrap answers.

    bin/vep probes the engine with a bare `python3`, and launchd's
    default PATH finds macOS's 3.9 - which cannot even parse
    `ren/__init__.py` (`Path | None` needs 3.10+). The installing
    interpreter answered this machine, so its directory leads; the
    tool dirs behind it carry gh-axi, ruff, git, ps and vm_stat.
    Rendered at install time: this path is machine-local, like the
    plist itself.
    """
    import shutil
    import sys
    candidates = []
    if sys.version_info >= (3, 10):
        candidates.append(os.path.dirname(sys.executable))
    which = shutil.which("python3")
    if which:
        candidates.append(os.path.dirname(which))
    candidates.extend(["/opt/homebrew/bin", "/usr/bin", "/bin",
                       "/usr/sbin", "/sbin"])
    seen = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.append(candidate)
    return ":".join(seen)


def render_plist(repo: str) -> str:
    """The agent plist text for a repo checkout."""
    log = config.log_dir()
    engine = os.path.join(repo, "bin", "vep")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{engine}</string>
    <string>-m</string>
    <string>library.tools.local_ci</string>
    <string>daemon</string>
    <string>--repo</string>
    <string>{repo}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>{_launchd_path()}</string>
  </dict>
  <key>WorkingDirectory</key><string>{config.state_dir()}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>{log}/daemon.log</string>
  <key>StandardErrorPath</key><string>{log}/daemon.err</string>
</dict>
</plist>
"""


def check_install_repo(repo: str) -> None:
    """Refuse anything but a clean main checkout: the daemon serves
    main's integration, and a dirty or branched tree is not main."""
    branch = _git(repo, "branch", "--show-current").strip()
    if branch != "main":
        raise RuntimeError(f"refusing: install from main, not {branch!r} - "
                           "the daemon serves main's integration")
    dirty = _git(repo, "status", "--porcelain=v1")
    if dirty.strip():
        raise RuntimeError("refusing: checkout is dirty - commit or clean it")


def install(repo: str) -> str:
    """Write the plist, bootstrap it, return the plist path.

    Refuses a dirty checkout: the daemon serves main's integration, and
    a dirty tree is not main.
    """
    check_install_repo(repo)
    os.makedirs(config.log_dir(), exist_ok=True)
    os.makedirs(config.state_dir(), exist_ok=True)
    path = plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_plist(repo))
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    _launchctl("bootstrap", f"gui/{os.getuid()}", path)
    return path


def uninstall() -> None:
    """Stop the agent and remove its plist. State and logs survive."""
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    path = plist_path()
    if os.path.exists(path):
        os.remove(path)


def installed() -> bool:
    """Whether the agent plist exists (not whether it runs)."""
    return os.path.exists(plist_path())


def launchd_state() -> str:
    """Whether launchd loaded the agent, or whether its state is unknown."""
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, encoding="utf-8", check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return "loaded" if result.returncode == 0 else "not loaded"


def _git(repo: str, *argv: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *argv], capture_output=True,
        encoding="utf-8", check=False, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(argv)} failed: "
                           f"{result.stderr.strip()}")
    return result.stdout


def _launchctl(*argv: str) -> None:
    # bootout on a non-loaded agent exits nonzero: that is not failure.
    subprocess.run(["launchctl", *argv], capture_output=True,
                   encoding="utf-8", check=False, timeout=30)
