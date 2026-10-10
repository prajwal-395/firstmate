"""The daemon reloads itself when the checkout it serves moves on.

Observed 2026-10-10: the launchd agent (com.ren.ci) had been running
since the day before on the code it started with. When the
upstream-engine intake merged and the served checkout fast-forwarded,
the daemon kept running the OLD code, so the daily poll and weekly
intake would never have run until a human restarted it by hand.

So before every dispatch the daemon compares the commit it STARTED on
with the served checkout's HEAD. When HEAD moved AND the range
touches the daemon's own code, the daemon re-execs onto the new tree -
but only while IDLE (no Tier-1 worker in flight, no slot owner, no
full-suite gate, no Tier-2 member under test). A busy daemon defers
to the next pass: in-flight verification is never killed for a
reload.

Periodic-job state needs no migration: the daily-poll timestamp and
the weekly-intake windows live in `upstream-intake.json` under the
state dir (the machine, never the repo), and `due()`/`begin_weekly()`
already make those windows idempotent across restarts. The re-exec
keeps the same state dir, so no window is skipped or doubled; an
interrupted weekly run is retried by `recover_weekly()` at startup,
exactly as after any restart.
"""

from __future__ import annotations

import os
import subprocess
import sys

# The daemon's own code: its package plus everything outside it that
# the daemon loop imports. A move that touches none of these needs no
# reload - the running image already behaves identically.
DAEMON_PACKAGE = "library/tools/local_ci/"
EXTRA_WATCHED = (
    "bin/vep",
    "ren/engine_root.py",
    "library/tools/heavy_work_lock.py",
    "library/tools/resource_scheduler.py",
    "library/tools/resolved/client.py",
)


def served_commit(repo: str) -> str:
    """The served checkout's HEAD, or "" when it cannot be read.

    Unreadable is not a reload: a missing git binary or a checkout
    mid-fetch must never crash or exec-loop the daemon.
    """
    if not repo:
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else ""


def code_changed(repo: str, old: str, new: str) -> bool:
    """Whether old..new touches the daemon's own watched paths."""
    if not repo or not old or not new or old == new:
        return False
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                repo,
                "diff",
                "--name-only",
                old,
                new,
                "--",
                DAEMON_PACKAGE,
                *EXTRA_WATCHED,
            ],
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def should_reload(repo: str, running_sha: str) -> tuple[str | None, str, str]:
    """Decide whether to reload. Returns (reload_sha, seen_head, reason).

    reload_sha is the new HEAD when the daemon must re-exec, else None.
    seen_head is the checkout's current HEAD ("" when unreadable).
    """
    if not repo:
        return None, "", "no served checkout configured"
    head = served_commit(repo)
    if not head:
        return None, "", "served HEAD unreadable - staying on current code"
    if head == running_sha:
        return None, head, "served checkout unchanged"
    if not running_sha:
        return None, head, "no running commit recorded - staying on current code"
    if not code_changed(repo, running_sha, head):
        return (
            None,
            head,
            (
                f"served checkout moved {running_sha[:12]} -> "
                f"{head[:12]} without daemon-code changes"
            ),
        )
    return (
        head,
        head,
        (
            f"served checkout moved {running_sha[:12]} -> "
            f"{head[:12]} with daemon-code changes"
        ),
    )


def daemon_idle(scheduler, state_dir: str) -> tuple[bool, str]:
    """Whether a reload may happen now: nothing in flight anywhere."""
    from library.tools.local_ci import hygiene, state

    if scheduler is not None and not scheduler.idle():
        return False, "Tier-1 workers in flight"
    if hygiene.running_jobs(state_dir):
        return False, "slot owners active"
    if hygiene.gate_running() or hygiene.running_gate_jobs():
        return False, "full-suite gate active"
    testing = [
        entry
        for entry in state.read_queue(state_dir)
        if entry.get("status") == state.ENTRY_TESTING
    ]
    if testing:
        return False, f"Tier-2 testing PRs {[e['pr'] for e in testing]}"
    return True, "idle"


def check_and_reload(
    scheduler, repo: str, running_sha: str, state_dir: str, poll: int
) -> str:
    """Reload onto the served HEAD when due and idle. Returns the
    commit the daemon now runs (the new HEAD after a reload, else the
    input). A real reload never returns: the process image is
    replaced by os.execve."""
    reload_sha, head, reason = should_reload(repo, running_sha)
    if reload_sha is None:
        if head and head != running_sha:
            print(f"ren-ci self-update deferred: {reason}", flush=True)
        return running_sha
    ok, why = daemon_idle(scheduler, state_dir)
    if not ok:
        print(
            f"ren-ci self-update deferred onto {reload_sha[:12]}: {why} - "
            "retrying next pass",
            flush=True,
        )
        return running_sha
    print(f"ren-ci self-update: {reason} - re-execing", flush=True)
    reexec(repo, state_dir, poll)
    return reload_sha


def reexec(repo: str, state_dir: str, poll: int) -> None:
    """Replace this process with the served checkout's daemon."""
    engine = sys.executable
    argv = [
        engine,
        "-m",
        "library.tools.local_ci",
        "daemon",
        "--state",
        state_dir,
        "--poll",
        str(poll),
        "--repo",
        repo,
    ]
    env = dict(os.environ)
    old_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    path = env.get("PYTHONPATH", "")
    parts = [
        entry
        for entry in path.split(os.pathsep)
        if entry and os.path.abspath(entry) != old_root
    ]
    env["PYTHONPATH"] = os.pathsep.join([repo, *parts])
    os.execve(engine, argv, env)
