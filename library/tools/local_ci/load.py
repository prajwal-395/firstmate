"""Load-aware scheduling: what the machine can take RIGHT NOW.

Tier-1 checks run several PRs at once and Tier-2 clusters in a timebox -
but "at once" and the box are MEASURED per dispatch, never fixed. A
quiet machine runs more jobs with short windows (small clusters, low
latency); a busy machine runs fewer jobs with long windows (bigger
clusters, less load). The captain's foreground work is never starved:
load at/above core count serialises to one job, and the heavy-work lock
(a batch full-suite gate in flight) still defers everything.

`measure()` reads the machine (read-only: load average, memory, disk,
heavy process names). `targets()` turns a snapshot into dispatch
numbers and is pure, so the whole policy is a unit-test matrix. When
any measurement fails, the snapshot says so and targets go
conservative (one job, narrow pytest, long window): an unreadable
machine is a busy machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from library.tools.local_ci import config

# Absolute ceiling: even a fully idle machine never runs more Tier-1
# jobs than this. Spec 3's "at most 2" applied to the serial design;
# the captain's 2026-10-07 refinement explicitly raises it under load
# control ("as many as the machine can take right now"), still bounded
# and still deferring to the heavy-work lock.
TIER1_MAX = 3

# pytest -n per Tier-1 job: never above 2 (spec 3's per-run bound
# stands - the parallelism the refinement adds is ACROSS PRs, not
# within one run).
PYTEST_N_MAX = 2

# Cluster window bounds (seconds): quiet machines cluster briefly (low
# latency, small clusters), busy machines wait longer (fewer full-gate
# runs, bigger clusters).
WINDOW_MIN = 90
WINDOW_MAX = 600


def measure() -> dict:
    """The machine right now: load, cores, memory, disk, heavy names."""
    cpus = os.cpu_count() or 1
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = float(cpus)
    snapshot: dict = {"load1": load1, "cpus": cpus,
                      "mem_free_gb": _mem_free_gb(),
                      "disk_free_gb": shutil.disk_usage(
                          os.path.expanduser("~")).free / 1024 ** 3,
                      "heavy": _heavy_names(), "blind": False}
    if snapshot["mem_free_gb"] is None:
        snapshot["blind"] = True
    return snapshot


def targets(snapshot: dict) -> dict:
    """Dispatch numbers for a snapshot: tier1 jobs, pytest -n, window.

    Headroom is cores minus 1-minute load: each whole free core is a
    Tier-1 job, capped at TIER1_MAX, floored at 1 (progress over
    paralysis - one job always moves). Memory pressure (< 4GB free) or
    an unreadable machine halves ambition to the floor. pytest -n
    follows jobs (one job may fan out, three jobs may not). The window
    stretches as jobs shrink: few jobs, long box, big clusters.
    """
    cpus = max(int(snapshot.get("cpus") or 1), 1)
    load = float(snapshot.get("load1", cpus))
    headroom = cpus - load
    if snapshot.get("blind") or (snapshot.get("mem_free_gb") or 0) < 4:
        jobs = 1
    elif headroom >= 3 and not snapshot.get("heavy"):
        jobs = TIER1_MAX
    elif headroom >= 1.5:
        jobs = 2
    else:
        jobs = 1
    jobs = max(1, min(TIER1_MAX, jobs))
    pytest_n = PYTEST_N_MAX if jobs == 1 else 1
    if jobs >= TIER1_MAX:
        window = WINDOW_MIN
    elif jobs == 2:
        window = (WINDOW_MIN + WINDOW_MAX) // 2
    else:
        window = WINDOW_MAX
    return {"tier1_jobs": jobs, "pytest_n": pytest_n, "window": window,
            "headroom": round(headroom, 2),
            "heavy": list(snapshot.get("heavy") or [])}


def _mem_free_gb() -> float | None:
    """Free + inactive memory in GB (macOS vm_stat), else None (blind)."""
    try:
        result = subprocess.run(["vm_stat"], capture_output=True,
                                encoding="utf-8", check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    pages: dict = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits:
            pages[key.strip()] = int(digits)
    page_size = 16384  # Apple Silicon; Intel's 4096 only UNDER-reads free
    free_pages = pages.get("Pages free", 0) + pages.get("Pages inactive", 0)
    if not free_pages:
        return None
    return free_pages * page_size / 1024 ** 3


def _heavy_names() -> list:
    """Heavy foreground processes by name (read-only ps): Resolve, the
    known renderers, any batch gate. Never matched for killing - this
    list only throttles dispatch (brief rule: stop processes by exact
    PID you started; this module stops nothing)."""
    want = ("Resolve", "ffmpeg", "Remotion", "full_suite_gate")
    try:
        result = subprocess.run(["ps", "-ax", "-o", "comm="],
                                capture_output=True, encoding="utf-8",
                                check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    seen = []
    for line in result.stdout.splitlines():
        name = line.strip()
        for heavy in want:
            if heavy in name and heavy not in seen:
                seen.append(heavy)
    return seen


def describe(plan: dict) -> str:
    """One-line dispatch plan for logs and status."""
    heavy = f" heavy={','.join(plan['heavy'])}" if plan["heavy"] else ""
    return (f"jobs={plan['tier1_jobs']} pytest-n={plan['pytest_n']} "
            f"window={plan['window']}s headroom={plan['headroom']}{heavy}")
