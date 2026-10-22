"""The machine-wide guard around a Gemma 4 12B IN-PROCESS load.

On 2026-10-22 the captain force-quit three Python processes over 7 GB
each: `library/tools/vision_model.py` falls back to loading Gemma 4 12B
in-process (~7 GB) whenever :8080 does not answer, so every fallback in
every process loaded its own private copy. Firstmate has since installed
the shim agent (launchd holds :8080, backend on demand, 210 s idle
reaper), and the fallback must never repeat that shape:

* at most ONE process on the machine ever holds Gemma in-process: a
  `flock` on `~/.local/share/vep/gemma-inprocess.lock`, held from the
  load for as long as the weights stay resident (the fd stays open on
  the `VisionModel` instance - closing it releases the lock);
* before any in-process load, free+inactive memory is read and a
  machine below ~8 GB is REFUSED loudly instead of loading.

Both are enforced in `VisionModel._ensure_loaded`, the single funnel
every in-process Gemma load travels through (`analyze_image`,
`analyze_images`, `analyze_video`). The resident-server path (:8080)
never touches this module.

Stdlib only, so importing it loads no model and costs nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

#: The one file the whole machine contends on. One holder, ever: the
#: flock is taken before the weights load and held (fd open) while
#: they stay resident.
DEFAULT_LOCK_FILE = (
    Path.home() / ".local" / "share" / "vep" / "gemma-inprocess.lock")

#: Env override, so tests point the lock at a tmp dir instead of the
#: machine file (tests/conftest.py does the same for the heavy-work
#: lock). Nothing else sets it.
LOCK_FILE_ENV = "GEMMA_INPROCESS_LOCK_FILE"

#: Refuse an in-process load below this much free+inactive memory, in
#: GB. The weights are ~7 GB resident (docs/GEMMA_SERVER.md §5); 8 GB
#: leaves no room for a second copy beside Resolve. Env override for
#: tests that prove the refusal without needing a loaded machine.
MIN_FREE_GB_ENV = "GEMMA_INPROCESS_MIN_GB"
DEFAULT_MIN_FREE_GB = 8.0

#: How long a second fallback waits for the holder before giving up,
#: in seconds. Env override; `0` (or negative) means try once and
#: refuse. A wait that ran out is a refusal with the fix, never a
#: silent second copy.
LOCK_TIMEOUT_ENV = "GEMMA_INPROCESS_LOCK_TIMEOUT_S"
DEFAULT_LOCK_TIMEOUT_S = 600.0


class GemmaMemoryRefused(MemoryError):
    """Free+inactive memory is below the in-process floor: no load."""


class GemmaInprocessBusy(RuntimeError):
    """Another process holds the in-process slot: no second copy."""


def lock_file() -> Path:
    """The lock file this process contends on (env-overridable)."""
    override = os.environ.get(LOCK_FILE_ENV)
    return Path(override).expanduser() if override else DEFAULT_LOCK_FILE


def min_free_gb() -> float:
    """The refusal floor in GB (env-overridable)."""
    try:
        return float(os.environ.get(MIN_FREE_GB_ENV, DEFAULT_MIN_FREE_GB))
    except ValueError:
        return DEFAULT_MIN_FREE_GB


def lock_timeout_s() -> float:
    """How long to wait for the holder before refusing (env-overridable)."""
    try:
        return float(os.environ.get(
            LOCK_TIMEOUT_ENV, DEFAULT_LOCK_TIMEOUT_S))
    except ValueError:
        return DEFAULT_LOCK_TIMEOUT_S


def available_gb() -> float | None:
    """Free+inactive memory in GB, or None when it cannot be read.

    macOS: `vm_stat` free + inactive pages times the page size (the
    memory a 7 GB load can actually take without paging). Linux:
    `MemAvailable` from /proc/meminfo. None is unreadable, never zero -
    the caller refuses on None rather than loading blind.
    """
    if sys.platform == "darwin":
        return _macos_available_gb()
    return _linux_available_gb()


def _macos_available_gb() -> float | None:
    try:
        done = subprocess.run(
            ["vm_stat"], capture_output=True, encoding="utf-8",
            timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    page_bytes = 16384
    free_pages = inactive_pages = None
    for line in done.stdout.splitlines():
        if "page size of" in line:
            try:
                page_bytes = int(line.split("page size of")[1].split()[0])
            except (IndexError, ValueError):
                pass
        elif line.startswith("Pages free:"):
            free_pages = _vm_stat_pages(line)
        elif line.startswith("Pages inactive:"):
            inactive_pages = _vm_stat_pages(line)
    if free_pages is None or inactive_pages is None:
        return None
    return (free_pages + inactive_pages) * page_bytes / 2 ** 30


def _vm_stat_pages(line: str) -> int | None:
    try:
        return int(line.split(":")[1].strip().rstrip("."))
    except (IndexError, ValueError):
        return None


def _linux_available_gb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / 2 ** 30
    except (OSError, ValueError, IndexError):
        return None
    return None


def check_memory() -> float:
    """Refuse loudly below the floor; return the reading otherwise.

    Raises `GemmaMemoryRefused` (a `MemoryError`) naming the reading,
    the floor, and the fix (`gemma up` / `gemma status` - the server
    path holds the weights once, not once per process). An unreadable
    machine refuses too: loading blind is how the three 7 GB copies
    happened.
    """
    floor = min_free_gb()
    reading = available_gb()
    if reading is None:
        raise GemmaMemoryRefused(
            f"gemma in-process load REFUSED: free+inactive memory could "
            f"not be read, and loading blind is what held three ~7 GB "
            f"copies. Free memory first, or serve the model once with "
            f"`gemma up --prewarm` (needs {floor:.0f} GB free+inactive).")
    if reading < floor:
        raise GemmaMemoryRefused(
            f"gemma in-process load REFUSED: {reading:.1f} GB "
            f"free+inactive is below the {floor:.0f} GB floor for a ~7 GB "
            f"private copy. Free memory first, or serve the model once "
            f"with `gemma up --prewarm` instead of once per process.")
    return reading


def hold_inprocess_slot(timeout: float | None = None) -> int:
    """Take the machine-wide in-process slot and hold it.

    Returns the open fd: KEEP IT OPEN (on the `VisionModel` instance)
    for as long as the weights stay resident - closing it releases the
    flock, and a released-while-resident lock is a second copy waiting
    to happen. Raises `GemmaInprocessBusy` past the timeout naming the
    holder's pid and the fix (wait, or `gemma up` so the server path
    answers and no process loads in-process at all).
    """
    import fcntl

    path = lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + (
        DEFAULT_LOCK_TIMEOUT_S if timeout is None else timeout)
    if timeout is not None and timeout <= 0:
        deadline = time.monotonic()
    holder = None
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                holder = _lock_holder_pid(path)
                if time.monotonic() >= deadline:
                    raise GemmaInprocessBusy(
                        f"gemma in-process load REFUSED: another process"
                        f"{f' (pid {holder})' if holder else ''} already "
                        f"holds Gemma in-process. Wait for it, or serve "
                        f"the model once with `gemma up --prewarm` so "
                        f"this process routes via :8080 and loads "
                        f"nothing.") from None
                time.sleep(1.0)
        _record_holder(path)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _record_holder(path: Path) -> None:
    """Note our pid beside the lock, so a refusal names who holds it."""
    try:
        with open(str(path) + ".holder", "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def _lock_holder_pid(path: Path) -> int | None:
    """The pid recorded beside the lock, or None when unknown."""
    try:
        with open(str(path) + ".holder", encoding="utf-8") as fh:
            text = fh.read().strip()
        pid = int(text)
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None
