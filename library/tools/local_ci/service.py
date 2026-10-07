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
  <key>WorkingDirectory</key><string>{config.state_dir()}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>{log}/daemon.log</string>
  <key>StandardErrorPath</key><string>{log}/daemon.err</string>
</dict>
</plist>
"""


def install(repo: str) -> str:
    """Write the plist, bootstrap it, return the plist path.

    Refuses a dirty checkout: the daemon serves main's integration, and
    a dirty tree is not main.
    """
    branch = _git(repo, "branch", "--show-current")
    dirty = _git(repo, "status", "--porcelain=v1")
    if branch != "main":
        raise RuntimeError(f"refusing: install from main, not {branch!r} - "
                           "the daemon serves main's integration")
    if dirty.strip():
        raise RuntimeError("refusing: checkout is dirty - commit or clean it")
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
