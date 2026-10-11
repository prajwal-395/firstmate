"""Load-aware scheduling: what the machine can take RIGHT NOW.

Tier-1 checks run several PRs at once and Tier-2 clusters in a timebox -
but "at once" and the box are MEASURED per dispatch, never fixed. A
quiet machine runs more jobs with short windows (small clusters, low
latency); a busy machine runs fewer jobs with long windows (bigger
clusters, less load). The captain's foreground work is never starved:
memory pressure or paging serialises to one job, and the heavy-work lock
(a batch full-suite gate in flight) still defers everything.

Parallelism is HARM-based, not load-average-based: a raw 1-minute load
counts every runnable agent lane, even idle ones, so ~15 lanes pin the
load at 20-25 on 10 cores while the CPUs are mostly idle. Dispatch
instead reads busy CPU cores (summed %CPU - idle lanes cost nothing),
free memory, and CURRENT paging activity (the recent pageout/swapout
rate). Swap USED is kept only for observability: on macOS it stays high
long after pressure clears (2026-10-10: ~12 GB swap used while ~6 GB of
pages were free), so it must never gate dispatch. The 1-minute load
survives only as a fallback when the CPU sample is unavailable, so old
snapshots and unit matrices still yield a plan.
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

# Paging pressure (pages/sec, macOS vm_stat deltas): the harm signal
# load average cannot see, and the CURRENT one - unlike swap used,
# which stays high for hours after pressure clears (2026-10-10: ~12 GB
# swap used beside ~6 GB free). Swapout pages live anonymous memory to
# disk, so even tens/sec sustained means the machine is swapping;
# pageout includes file-backed paging (normal I/O), so its thresholds
# sit an order of magnitude higher. Above *_DEGRADE the machine has
# started paging, so cap fan-out; above *_FLOOR paging already hurts
# foreground work, so serialise. First measure() after start reports
# zero rates (no previous sample): dispatch then rests on free memory
# alone rather than guessing pressure.
SWAPOUT_DEGRADE_PER_SEC = 30.0
SWAPOUT_FLOOR_PER_SEC = 150.0
PAGEOUT_DEGRADE_PER_SEC = 500.0
PAGEOUT_FLOOR_PER_SEC = 2000.0

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
    pageout_rate, swapout_rate = _paging_rates()
    snapshot: dict = {"load1": load1, "cpus": cpus,
                      "mem_free_gb": _mem_free_gb(),
                      "swap_used_gb": _swap_used_gb(),
                      "pageout_per_sec": pageout_rate,
                      "swapout_per_sec": swapout_rate,
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
    one job always moves). Memory pressure (< 4GB free), CURRENT paging
    (swapout/pageout rates at or above their floors), or an unreadable
    machine halves ambition to the floor; paging at or above the degrade
    band caps fan-out at two. pytest -n follows jobs (one job may fan
    out, three jobs may not). The window stretches as jobs shrink: few
    jobs, long box, big clusters. Swap USED never gates: it lingers
    after pressure clears, so it is observability only.
    """
    cpus = max(int(snapshot.get("cpus") or 1), 1)
    headroom, source = _pressure_headroom(snapshot)
    swapout_rate = _rate(snapshot.get("swapout_per_sec"))
    pageout_rate = _rate(snapshot.get("pageout_per_sec"))
    paging_floor = (swapout_rate >= SWAPOUT_FLOOR_PER_SEC
                    or pageout_rate >= PAGEOUT_FLOOR_PER_SEC)
    paging_degrade = (swapout_rate >= SWAPOUT_DEGRADE_PER_SEC
                      or pageout_rate >= PAGEOUT_DEGRADE_PER_SEC)
    if snapshot.get("blind") or (snapshot.get("mem_free_gb") or 0) < MEM_FLOOR_GB:
        jobs = 1
    elif paging_floor:
        jobs = 1
    elif headroom >= 3 and not snapshot.get("heavy"):
        jobs = TIER1_MAX
    elif headroom >= 1.5:
        jobs = 2
    else:
        jobs = 1
    if paging_degrade:
        jobs = min(jobs, 2)
        if paging_floor:
            jobs = 1
    jobs = max(1, min(TIER1_MAX, jobs))
    pytest_n = PYTEST_N_MAX if jobs == 1 else 1
    if jobs >= TIER1_MAX:
        window = WINDOW_MIN
    elif jobs == 2:
        window = (WINDOW_MIN + WINDOW_MAX) // 2
    else:
        window = WINDOW_MAX
    swap_used = snapshot.get("swap_used_gb") or 0.0
    try:
        swap_used = float(swap_used)
    except (TypeError, ValueError):
        swap_used = 0.0
    return {"tier1_jobs": jobs, "pytest_n": pytest_n, "window": window,
            "headroom": round(headroom, 2), "headroom_source": source,
            "swap_used_gb": round(swap_used, 2),
            "pageout_per_sec": round(pageout_rate, 1),
            "swapout_per_sec": round(swapout_rate, 1),
            "heavy": list(snapshot.get("heavy") or [])}


def _rate(value) -> float:
    """A paging rate as a float, defaulting absent/unparseable to zero.

    Zero means no measured pressure, not missing pressure: targets()
    rests on free memory alone until measure() has two samples.
    """
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, rate)


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


def _vm_paging_counters(text: str | None = None) -> tuple[int | None, int | None]:
    """Cumulative (pageouts, swapouts) from vm_stat, else (None, None).

    Pure when `text` is given (tests script it); otherwise reads the
    machine. None means unmeasured, never zero: a parse failure is not
    pressure.
    """
    if text is None:
        try:
            result = subprocess.run(
                ["vm_stat"], capture_output=True, encoding="utf-8",
                check=False, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None, None
        if result.returncode != 0:
            return None, None
        text = result.stdout
    pageouts: int | None = None
    swapouts: int | None = None
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        digits = "".join(ch for ch in value if ch.isdigit())
        if not digits:
            continue
        if key.strip() == "Pageouts":
            pageouts = int(digits)
        elif key.strip() == "Swapouts":
            swapouts = int(digits)
    return pageouts, swapouts


# Last paging sample: (monotonic, pageouts, swapouts). measure() diffs
# against it so dispatch reads the RECENT rate without sleeping for a
# second sample on every pass.
_LAST_PAGING_SAMPLE: tuple | None = None


def _rates_between(previous: tuple | None, now: float,
                   pageouts: int | None,
                   swapouts: int | None) -> tuple[float, float]:
    """Paging rates between two cumulative samples. Pure (tests pin it).

    Returns (pageout_per_sec, swapout_per_sec). A missing sample, a
    missing counter, a non-positive interval, a stale interval (> 120s:
    the daemon was asleep, not paging), or a backwards counter yields
    (0.0, 0.0): no measured pressure, never a guess.
    """
    if previous is None or pageouts is None or swapouts is None:
        return 0.0, 0.0
    try:
        then, prev_pageouts, prev_swapouts = previous
        interval = float(now) - float(then)
    except (TypeError, ValueError):
        return 0.0, 0.0
    if interval <= 0 or interval > 120:
        return 0.0, 0.0
    try:
        page_rate = (float(pageouts) - float(prev_pageouts)) / interval
        swap_rate = (float(swapouts) - float(prev_swapouts)) / interval
    except (TypeError, ValueError):
        return 0.0, 0.0
    return max(0.0, page_rate), max(0.0, swap_rate)


def _paging_rates() -> tuple[float, float]:
    """Current paging rates, diffed against the previous measure()."""
    import time as _time

    global _LAST_PAGING_SAMPLE
    pageouts, swapouts = _vm_paging_counters()
    now = _time.monotonic()
    rates = _rates_between(_LAST_PAGING_SAMPLE, now, pageouts, swapouts)
    if pageouts is not None and swapouts is not None:
        _LAST_PAGING_SAMPLE = (now, pageouts, swapouts)
    return rates


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
    paging_s = ""
    pageout = plan.get("pageout_per_sec")
    swapout = plan.get("swapout_per_sec")
    if isinstance(pageout, (int, float)) and isinstance(
            swapout, (int, float)) and (pageout > 0 or swapout > 0):
        paging_s = f" paging={pageout:.0f}/{swapout:.0f}pg/s"
    return (f"jobs={plan['tier1_jobs']} pytest-n={plan['pytest_n']} "
            f"window={plan['window']}s headroom={plan['headroom']}"
            f"({source}){swap_s}{paging_s}{heavy}")
