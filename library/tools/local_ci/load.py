"""Load-aware scheduling: what the machine can take RIGHT NOW.

Tier-1 checks run several PRs at once and Tier-2 clusters in a timebox -
but "at once" and the box are MEASURED per dispatch, never fixed. A
quiet machine runs more jobs with short windows (small clusters, low
latency); a busy machine runs fewer jobs with long windows (bigger
clusters, less load). The captain's foreground work is never starved:
memory pressure or swap serialises to one job, and the heavy-work lock
(a batch full-suite gate in flight) still defers everything.

Parallelism is HARM-based, not load-average-based: a raw 1-minute load
counts every runnable agent lane, even idle ones, so ~15 lanes pin the
load at 20-25 on 10 cores while the CPUs are mostly idle. Dispatch
instead reads busy CPU cores (summed %CPU - idle lanes cost nothing),
free memory, and swap used (the signal that memory pressure already
hurts). The 1-minute load survives only as a fallback when the CPU
sample is unavailable, so old snapshots and unit matrices still yield
a plan.
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

# Swap pressure (GB): the harm signal load average cannot see. Above
# SWAP_DEGRADE_GB the machine has started paging, so cap fan-out; above
# SWAP_FLOOR_GB paging already hurts foreground work, so serialise.
SWAP_DEGRADE_GB = 1.0
SWAP_FLOOR_GB = 2.0

# Free-memory floor (GB): below this the machine is one checkout away
# from pressure, so serialise. Unchanged: the existing floor stands.
MEM_FLOOR_GB = 4.0


def measure() -> dict:
    """The machine right now: load, cores, memory, disk, heavy names."""
    cpus = os.cpu_count() or 1
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = float(cpus)
    snapshot: dict = {"load1": load1, "cpus": cpus,
                      "mem_free_gb": _mem_free_gb(),
                      "swap_used_gb": _swap_used_gb(),
                      "cpu_busy": _cpu_busy_cores(),
                      "disk_free_gb": shutil.disk_usage(
                          os.path.expanduser("~")).free / 1024 ** 3,
                      "heavy": _heavy_names(), "blind": False}
    if snapshot["mem_free_gb"] is None:
        snapshot["blind"] = True
    return snapshot


def _pressure_headroom(snapshot: dict) -> tuple[float, str]:
    """Busy-core headroom when measured, else the load-average fallback.

    Returns (headroom_cores, source): source is "cpu" when the %CPU
    sample landed, "load" when only the 1-minute average is available.
    Idle agent lanes inflate the load average while contributing ~0%
    CPU, so the CPU sample governs whenever it exists.
    """
    cpus = max(int(snapshot.get("cpus") or 1), 1)
    cpu_busy = snapshot.get("cpu_busy")
    if isinstance(cpu_busy, (int, float)) and cpu_busy >= 0:
        return cpus - float(cpu_busy), "cpu"
    return cpus - float(snapshot.get("load1", cpus)), "load"


def targets(snapshot: dict) -> dict:
    """Dispatch numbers for a snapshot: tier1 jobs, pytest -n, window.

    Headroom is free cores minus BUSY cores (%CPU sum) when measured,
    else cores minus 1-minute load: each whole free core is a Tier-1
    job, capped at TIER1_MAX, floored at 1 (progress over paralysis -
    one job always moves). Memory pressure (< 4GB free), swap pressure
    (> 2GB used), or an unreadable machine halves ambition to the
    floor; swap above 1GB caps fan-out at two. pytest -n follows jobs
    (one job may fan out, three jobs may not). The window stretches as
    jobs shrink: few jobs, long box, big clusters.
    """
    cpus = max(int(snapshot.get("cpus") or 1), 1)
    headroom, source = _pressure_headroom(snapshot)
    swap_used = snapshot.get("swap_used_gb") or 0.0
    try:
        swap_used = float(swap_used)
    except (TypeError, ValueError):
        swap_used = 0.0
    if snapshot.get("blind") or (snapshot.get("mem_free_gb") or 0) < MEM_FLOOR_GB:
        jobs = 1
    elif swap_used >= SWAP_FLOOR_GB:
        jobs = 1
    elif headroom >= 3 and not snapshot.get("heavy"):
        jobs = TIER1_MAX
    elif headroom >= 1.5:
        jobs = 2
    else:
        jobs = 1
    if swap_used >= SWAP_DEGRADE_GB:
        jobs = min(jobs, 2)
        if swap_used >= SWAP_FLOOR_GB:
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
            "headroom": round(headroom, 2), "headroom_source": source,
            "swap_used_gb": round(swap_used, 2),
            "heavy": list(snapshot.get("heavy") or [])}


def _swap_used_gb() -> float | None:
    """Swap in use in GB (macOS sysctl vm.swapusage), else None.

    None means unmeasured, not zero: targets() treats it as no signal
    rather than no pressure. A parse failure is not pressure.
    """
    try:
        result = subprocess.run(["sysctl", "-n", "vm.swapusage"],
                                capture_output=True, encoding="utf-8",
                                check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.replace("=", " ").replace(",", " ")
    tokens = text.split()
    try:
        used_idx = next(i for i, tok in enumerate(tokens)
                        if tok.lower() == "used")
        value = float(tokens[used_idx + 1].rstrip("MmGgKk"))
        unit = tokens[used_idx + 2].upper() if len(tokens) > used_idx + 2 else "M"
        if unit.startswith("G"):
            return value
        if unit.startswith("K"):
            return value / 1024 / 1024
        return value / 1024
    except (StopIteration, IndexError, ValueError):
        return None


def _cpu_busy_cores() -> float | None:
    """Busy CPU cores right now: summed %CPU over 100, else None.

    Read-only `ps`: idle agent lanes contribute ~0% each, so fifteen
    sleeping lanes do not look like load. None on any failure: the
    caller falls back to the load average rather than guessing idle.
    """
    try:
        result = subprocess.run(["ps", "-ax", "-o", "%cpu="],
                                capture_output=True, encoding="utf-8",
                                check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    total = 0.0
    seen = False
    for line in result.stdout.splitlines():
        try:
            total += float(line.strip().rstrip("%"))
            seen = True
        except ValueError:
            continue
    return total / 100.0 if seen else None


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
    source = plan.get("headroom_source", "load")
    swap = plan.get("swap_used_gb")
    swap_s = f" swap={swap:.1f}G" if isinstance(swap, (int, float)) else ""
    return (f"jobs={plan['tier1_jobs']} pytest-n={plan['pytest_n']} "
            f"window={plan['window']}s headroom={plan['headroom']}"
            f"({source}){swap_s}{heavy}")
