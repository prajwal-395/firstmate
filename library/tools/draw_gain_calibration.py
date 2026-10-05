"""A qualified per-format draw-gain calibration record.

2026-10-04: the live draw-gain probe wrote timeline resolution settings
on a scratch timeline in the captain's open project. That is the same
``SetSetting`` / ``FusionApp::SyncProjectSettings`` ->
``RenderTask::ObtainRenderLock`` deadlock seen in the 2026-10-01 and
2026-10-02 Resolve crash reports. The probe no longer mutates
resolution on the live project; it reads a qualified calibration record
instead.

The record is keyed by ``(resolve_version, width, height)`` - the gain is
renderer state that can move between Resolve versions and delivery
geometries, so a calibration is only valid for the exact combination it
was measured under. A missing entry is a fallback, never a guess.

The record lives beside the broker database (``resolve_lock.lock_dir()``),
never in a project or in git.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

CALIBRATION_FILENAME = "draw_gain_calibration.json"

_GAIN_SANE_MIN, _GAIN_SANE_MAX = 0.25, 4.0


def calibration_path() -> Path:
    """The calibration record's path, beside the broker database."""
    from library.tools.resolve_lock import lock_dir
    return lock_dir() / CALIBRATION_FILENAME


def resolve_version(resolve) -> str:
    """The Resolve version string, or ``"unknown"`` where unreadable."""
    try:
        return str(resolve.GetVersionString())
    except Exception:  # noqa: BLE001 - version is a key, not a gate
        return "unknown"


def _read_record(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


_record_lock = threading.Lock()


def lookup(resolve, width: int, height: int) -> float | None:
    """The qualified gain for ``(version, width, height)``, or None.

    Returns None where no calibration exists for this exact combination,
    where the stored gain is outside the sane range, or where the record
    is unreadable. A caller treats None as "fall back with a warning".
    """
    version = resolve_version(resolve)
    path = calibration_path()
    with _record_lock:
        record = _read_record(path)
    entries = record.get("calibrations", {})
    key = f"{version}:{int(width)}x{int(height)}"
    entry = entries.get(key)
    if entry is None:
        return None
    try:
        gain = float(entry["gain"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (_GAIN_SANE_MIN <= gain <= _GAIN_SANE_MAX):
        return None
    return gain


def store(resolve, width: int, height: int, gain: float) -> None:
    """Record a measured gain for ``(version, width, height)``.

    Refuses to store a gain outside the sane range - a calibration is a
    measurement, not a guess, and a wild value would poison every build
    that trusts it.
    """
    gain = float(gain)
    if not (_GAIN_SANE_MIN <= gain <= _GAIN_SANE_MAX):
        raise ValueError(
            f"refusing to store draw gain {gain}: outside "
            f"[{_GAIN_SANE_MIN}, {_GAIN_SANE_MAX}]")
    version = resolve_version(resolve)
    path = calibration_path()
    key = f"{version}:{int(width)}x{int(height)}"
    with _record_lock:
        record = _read_record(path)
        entries = record.setdefault("calibrations", {})
        entries[key] = {
            "gain": gain,
            "resolve_version": version,
            "width": int(width),
            "height": int(height),
            "measured_at": time.time(),
        }
        _write_record(path, record)


def all_calibrations(resolve) -> dict:
    """All qualified calibrations, keyed by ``version:widthxheight``.

    Read-only: the caller reports what is qualified, never writes.
    """
    path = calibration_path()
    with _record_lock:
        record = _read_record(path)
    return dict(record.get("calibrations", {}))
