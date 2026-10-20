"""What the machine was doing while a run worked.

The performance ledger already measured the process's peak resident
memory. That is one number about one process; it says nothing about
whether the machine was under pressure, whether it was swapping, or how
much of the run's cost was disk.

This module reads those three from the OS, and it reads them the same
way everywhere: a value the OS did not expose is `None`, never a zero.
"No swap counter here" and "no swap was used" are different facts, and
a profile that cannot tell them apart is worse than one that says
nothing.

The readings are cheap enough to take at a capability boundary:
  * memory pressure - macOS `kern.memorystatus_level`, Linux
    `/proc/pressure/memory`;
  * swap - macOS `vm.swapusage`, Linux `/proc/meminfo`;
  * disk - `os.statvfs` for the volume, and `/proc/self/io` for this
    process's own counters where the OS exposes them.

Nothing here writes, and nothing here decides anything: the readings
are evidence for a reader, not an admission gate. The scheduler's own
memory admission (`library/tools/resource_scheduler.py`) is where a
live reading GATES work, and it is deliberately a separate reading
from this one.

    python3 -m library.tools.perf_resources
"""

from __future__ import annotations

import ctypes
import os
import re
import struct
import sys
from pathlib import Path
from typing import Any

#: The pressure bands, worst last. The label is a READING of a measured
#: percentage, and the percentage itself is always reported beside it,
#: so a reader can re-derive the band rather than trust it.
PRESSURE_NORMAL = "normal"
PRESSURE_WARN = "warn"
PRESSURE_CRITICAL = "critical"
PRESSURE_UNKNOWN = "unknown"

#: Where the bands cut, as a percentage of memory UNDER pressure. A
#: fixed convention, not a tuned value: the percentage is reported next
#: to the label. macOS reports the FREE percentage (higher is calmer),
#: Linux reports a stall percentage (higher is worse); both are
#: normalised to "higher is worse" before the band is read.
WARN_PCT = 50.0
CRITICAL_PCT = 80.0


def _sysctl(name: str) -> bytes | None:
    """One macOS sysctl value, read without spawning a process."""
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.dylib", use_errno=True)
        libc.sysctlbyname.argtypes = [
            ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p, ctypes.c_size_t]
        libc.sysctlbyname.restype = ctypes.c_int
    except (OSError, AttributeError):  # pragma: no cover - non-macOS
        return None
    encoded = name.encode("utf-8")
    size = ctypes.c_size_t(0)
    if libc.sysctlbyname(encoded, None, ctypes.byref(size), None, 0) != 0:
        return None
    if not size.value:
        return b""
    buffer = ctypes.create_string_buffer(size.value)
    if libc.sysctlbyname(encoded, buffer, ctypes.byref(size), None, 0) != 0:
        return None
    return bytes(buffer.raw)[:size.value]


def memory_pressure() -> dict[str, Any]:
    """How much memory pressure the machine is under.

    `pct` is the pressure percentage - higher is worse - and `level`
    is the band it falls in. Both are `None` where the OS does not
    expose them.
    """
    if sys.platform == "darwin":
        raw = _sysctl("kern.memorystatus_level")
        if raw is None or len(raw) < 4:
            return {"pct": None, "level": PRESSURE_UNKNOWN}
        # macOS reports the FREE percentage; pressure is what is left.
        free_pct = float(int.from_bytes(raw[:4], "little", signed=True))
        pct = max(0.0, min(100.0, 100.0 - free_pct))
        return {"pct": pct, "level": _band(pct)}
    pressure = Path("/proc/pressure/memory")
    if pressure.is_file():
        try:
            text = pressure.read_text(encoding="utf-8")
        except OSError:
            text = ""
        match = re.search(r"^some\s+avg60=(\d+(?:\.\d+)?)", text, re.MULTILINE)
        if match:
            pct = min(100.0, float(match.group(1)))
            return {"pct": pct, "level": _band(pct)}
    return {"pct": None, "level": PRESSURE_UNKNOWN}


def _band(pct: float) -> str:
    if pct >= CRITICAL_PCT:
        return PRESSURE_CRITICAL
    if pct >= WARN_PCT:
        return PRESSURE_WARN
    return PRESSURE_NORMAL


def swap_usage_mb() -> dict[str, Any]:
    """Swap in use, and how much the machine has.

    `used_mb` is None where the OS does not expose the counter.
    """
    if sys.platform == "darwin":
        raw = _sysctl("vm.swapusage")
        # struct xsw_usage: total bytes, available bytes, used bytes,
        # then the page size and the encryption flag as two uint32s.
        if raw is None or len(raw) < 24:
            return {"used_mb": None, "total_mb": None}
        total_b, _avail_b, used_b = struct.unpack_from("<3Q", raw, 0)
        scale = 1024 * 1024
        return {"used_mb": used_b / scale, "total_mb": total_b / scale}
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        try:
            text = meminfo.read_text(encoding="utf-8")
        except OSError:
            text = ""
        values = {}
        for line in text.splitlines():
            key, _, value = line.partition(":")
            if key in ("SwapTotal", "SwapFree"):
                values[key] = int(value.strip().split()[0]) / 1024
        total = values.get("SwapTotal")
        free = values.get("SwapFree")
        used = None
        if total is not None and free is not None:
            used = max(0.0, total - free)
        return {"used_mb": used, "total_mb": total}
    return {"used_mb": None, "total_mb": None}


def disk_usage(path: str | os.PathLike) -> dict[str, Any]:
    """The volume holding `path`: free space and total space."""
    try:
        stat = os.statvfs(path)
    except (OSError, ValueError):
        return {"free_bytes": None, "total_bytes": None}
    block = stat.f_frsize or stat.f_bsize
    return {"free_bytes": stat.f_bavail * block,
            "total_bytes": stat.f_blocks * block}


def process_io_bytes() -> dict[str, Any]:
    """This process's own read and written bytes, where the OS says.

    Linux exposes them in `/proc/self/io`. macOS does not, so the
    reading is absent there - and absent means unmeasured, not zero.
    """
    counters = Path("/proc/self/io")
    if not counters.is_file():
        return {"read_bytes": None, "written_bytes": None}
    try:
        text = counters.read_text(encoding="utf-8")
    except OSError:
        return {"read_bytes": None, "written_bytes": None}
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key in ("rchar", "wchar"):
            try:
                values[key] = int(value.strip())
            except ValueError:
                continue
    return {"read_bytes": values.get("rchar"),
            "written_bytes": values.get("wchar")}


def sample(project_folder: str | None = None) -> dict[str, Any]:
    """One reading of the machine, for a capability row.

    The fields the OS did not expose are absent from the dict, so a
    row never carries a zero where the truth is "not measured".
    """
    reading: dict[str, Any] = {}
    pressure = memory_pressure()
    if pressure.get("pct") is not None:
        reading["memory_pressure_pct"] = pressure["pct"]
    reading["memory_pressure"] = pressure["level"]
    swap = swap_usage_mb()
    if swap.get("used_mb") is not None:
        reading["swap_used_mb"] = swap["used_mb"]
    if swap.get("total_mb") is not None:
        reading["swap_total_mb"] = swap["total_mb"]
    if project_folder:
        disk = disk_usage(project_folder)
        if disk.get("free_bytes") is not None:
            reading["disk_free_bytes"] = disk["free_bytes"]
        if disk.get("total_bytes") is not None:
            reading["disk_total_bytes"] = disk["total_bytes"]
    io_bytes = process_io_bytes()
    if io_bytes.get("read_bytes") is not None:
        reading["disk_bytes_read"] = io_bytes["read_bytes"]
    if io_bytes.get("written_bytes") is not None:
        reading["disk_bytes_written"] = io_bytes["written_bytes"]
    return reading


def worse(first: dict[str, Any] | None,
          second: dict[str, Any] | None) -> dict[str, Any]:
    """Merge two samples, keeping the worse of each reading.

    Memory pressure and swap peak somewhere inside a capability, not
    at its edges, so a capability is sampled at both ends and the worse
    of the two is what it reports. The disk reading is a snapshot: the
    later one wins.
    """
    merged: dict[str, Any] = {}
    for sample in (first or {}, second or {}):
        for key, value in sample.items():
            if key in ("memory_pressure", "memory_pressure_pct"):
                if key == "memory_pressure":
                    if _rank(value) >= _rank(merged.get(key)):
                        merged[key] = value
                elif (value is not None
                      and (merged.get(key) is None
                           or value > merged[key])):
                    merged[key] = value
            elif key in ("swap_used_mb", "swap_total_mb"):
                if value is not None:
                    merged[key] = max(merged.get(key) or 0.0, value)
            else:
                merged[key] = value
    return merged


def _rank(level: Any) -> int:
    order = {PRESSURE_UNKNOWN: -1, PRESSURE_NORMAL: 0, PRESSURE_WARN: 1,
             PRESSURE_CRITICAL: 2}
    return order.get(str(level), -1)


def main(argv=None) -> int:
    """Print one reading of the machine, as JSON."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="perf_resources",
        description="One reading of this machine: memory pressure, swap, "
                    "disk. Reads only; changes nothing.")
    parser.add_argument("--path", help="the volume to report (default: .)")
    args = parser.parse_args(argv)
    print(json.dumps(sample(args.path or "."), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
