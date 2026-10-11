"""Orphaned Tier-2 gates: the restart the daemon did not survive.

2026-10-10: `launchctl kickstart -k com.ren.ci` killed the daemon but
left its Tier-2 full_suite_gate process group running under
heavy_work_lock, reparented to pid 1. For an hour it ran old code and
held the machine grant beside the new daemon's batch, while queue
entries 2275/2277/2278/2281 stayed status testing.

Two layers cover it. The daemon's shutdown handler (daemon.serve)
terminates the process groups it started on SIGTERM. This module is
the second layer: on startup the daemon reaps gates a dead daemon left
behind - exact PIDs discovered by enumeration, never a name or pattern
kill - then recovers queue entries left testing whose gate is gone.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time


GATE_TOKEN = "full_suite_gate"


def _default_process_table() -> list[tuple[int, int, str]]:
    """Live (pid, ppid, command) rows via read-only ps, else []."""
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid=,command="],
            capture_output=True, encoding="utf-8",
            check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    rows: list[tuple[int, int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def find_orphaned_gate_pgids(
    rows: list[tuple[int, int, str]] | None = None,
) -> list[int]:
    """Exact pgids of orphaned gates: reparented (ppid 1) full-suite gates.

    A gate runs in its own session (runner.start_new_session), so its
    pgid equals its pid. Only rows whose command names the gate script
    AND whose parent is init qualify: a live daemon's own gate has the
    daemon as parent, and an unrelated checkout's gate is not ours to
    reap here - the startup path additionally scopes by state dir (see
    find_orphaned_gate_pgids_for_state). Pure: tests script `rows`.
    """
    table = list(rows) if rows is not None else _default_process_table()
    found = []
    for pid, ppid, command in table:
        if pid <= 1 or ppid != 1:
            continue
        if GATE_TOKEN not in command:
            continue
        found.append(int(pid))
    return sorted(set(found))


def find_orphaned_gate_pgids_for_state(
    state_dir: str,
    rows: list[tuple[int, int, str]] | None = None,
    checkout_cwd: dict[int, str] | None = None,
) -> list[int]:
    """Orphaned gate pgids belonging to this state dir's checkouts.

    When a cwd map is available (pid -> cwd, e.g. via lsof in
    production), only orphans whose cwd sits under this state dir's
    checkouts are returned. Without one, every reparented gate is
    returned: a stale gate holding the machine grant beside the new
    daemon's batch is worse than an over-broad reap, and the command
    token already scopes to full-suite gates. Pure: tests script both.
    """
    from library.tools.local_ci import hygiene

    orphans = find_orphaned_gate_pgids(rows)
    if checkout_cwd is None:
        return orphans
    root = os.path.abspath(hygiene.checkouts_root(state_dir))
    scoped = []
    for pid in orphans:
        cwd = checkout_cwd.get(pid, "")
        if cwd and os.path.abspath(cwd).startswith(root + os.sep):
            scoped.append(pid)
    return sorted(scoped)


def find_own_orphaned_gate_pgids(state_dir: str) -> list[int]:
    """Orphaned gate pgids belonging to this state dir, resolved live.

    The production scope for "ours": reparented full-suite gates whose
    cwd sits under this state dir's checkouts. lsof runs only when a
    reparented gate exists, so the common tick cost is one ps. Tick and
    reap both resolve through here, so they always agree on what ours
    means: a foreign gate (a worker worktree, the nightly run) never
    blocks this daemon's dispatch and is never this daemon's to reap.
    """
    probe = find_orphaned_gate_pgids(None)
    if not probe:
        return []
    return find_orphaned_gate_pgids_for_state(
        state_dir, None, _cwd_for_pids(probe))


def _cwd_for_pids(pids: list[int]) -> dict[int, str]:
    """Map pid -> cwd via lsof, read-only, best effort."""
    if not pids:
        return {}
    try:
        result = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-F", "pcn",
             *[str(pid) for pid in pids]],
            capture_output=True, encoding="utf-8",
            check=False, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return {}
    out: dict[int, str] = {}
    pid: int | None = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line.startswith("n") and pid is not None:
            out[pid] = line[1:]
            pid = None
    return out


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap_orphaned_gates(
    state_dir: str,
    rows: list[tuple[int, int, str]] | None = None,
    checkout_cwd: dict[int, str] | None = None,
    kill=None,
    grace_seconds: float = 5.0,
) -> dict[int, bool]:
    """Terminate orphaned gates by exact pgid. Returns pgid -> gone.

    SIGTERM first, SIGKILL past the grace - the same bounded sequence
    as a live run's cleanup, applied to pgids a dead daemon left
    behind. `kill` is the seam (default os.killpg): tests script it.
    `checkout_cwd=None` means resolve live through
    find_own_orphaned_gate_pgids (cwd-scoped, what the per-tick check
    uses); pass an explicit map - even {} - to require a checkout cwd
    match.
    """
    if rows is None and checkout_cwd is None:
        # Production: only our own checkouts are reaped - the same
        # scope the per-tick check uses, so tick and reap agree.
        targets = find_own_orphaned_gate_pgids(state_dir)
    else:
        targets = find_orphaned_gate_pgids_for_state(
            state_dir, rows, checkout_cwd)
    send = kill if kill is not None else os.killpg
    results: dict[int, bool] = {}
    for pgid in targets:
        gone = _terminate_pgid(pgid, send, grace_seconds)
        results[pgid] = gone
    return results


def _terminate_pgid(pgid: int, send, grace_seconds: float) -> bool:
    if not _process_group_exists(pgid):
        return True
    try:
        send(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _process_group_exists(pgid):
        try:
            send(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError:
            return False
    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _process_group_exists(pgid)


def gate_still_active(state_dir: str) -> bool:
    """Whether any gate still holds the machine: live rows or orphans.

    Orphans are cwd-scoped to this state dir (find_own_*): a foreign
    gate elsewhere on the machine is not this daemon's business.
    """
    from library.tools.local_ci import hygiene

    if hygiene.gate_running() or hygiene.running_gate_jobs():
        return True
    return bool(find_own_orphaned_gate_pgids(state_dir))


def recover_testing_entries_if_gate_gone(state_dir: str) -> int:
    """Requeue testing entries only when no gate survives. Returns count.

    The caller must have reaped orphans first: entries left testing
    while a gate still runs belong to that gate, not to a restart.
    """
    from library.tools.local_ci import state

    if gate_still_active(state_dir):
        return 0
    return state.recover_interrupted_queue(state_dir)
