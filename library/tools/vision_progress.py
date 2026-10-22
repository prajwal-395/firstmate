"""The progress channel between the vision child and the semantic step.

The step that owns the vision pass cannot see inside its child: it sees a
pipe and a directory.  It used to read SILENCE as a wedge - no log line
and no new profile for `VISION_STALL_TIMEOUT_S` - and killed the child.
The vision pass is silent through its long phases (frame extraction, the
model load, a coarse sweep of hundreds of batches), so a long clip was
destroyed in the middle of its measurement and reported as a MISSING
profile: 600 s x 2 clips of nothing (scout report 2026-10-20, FIX 7).

This module is the channel that ends the guessing.  The child records
what it is doing and how many units of work it has finished; the parent
reads that and calls the child wedged only when NOTHING has moved:

  - a finished unit advanced        -> the child is working
  - a declared unit ran too long     -> the child is wedged INSIDE it
  - nothing moved at all             -> the child is wedged (the rule
    that survives a child too old to write this file)

SILENCE is not evidence.  A rule that reads silence as a wedge kills
exactly the footage that takes longest to measure, and reports the loss
as a missing measurement rather than as the timeout it was.

The record is one small JSON file, written atomically:

    {"schema_version": 1, "pid": 4242, "stage": "objects_coarse",
     "units": 37, "inflight": [{"label": "...", "started_at": 1234.5}],
     "updated_at": 1234.6}

`units` only ever grows within a child, and it counts FINISHED work - a
unit that is still running has not been counted yet, so a counter that
stands still means the child is not getting anywhere.  `inflight` lists
the units in flight RIGHT NOW, so a child that runs two passes at once
still says how long the oldest of them has been running.

[why](docs/RULE_EVIDENCE.md#the-vision-pass-was-killed-for-being-quiet)
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
PROGRESS_FILENAME = "vision_progress.json"


def progress_path(analysis_dir: str | os.PathLike) -> Path:
    """Where the progress record of a vision child lives."""
    return Path(analysis_dir) / PROGRESS_FILENAME


def read_progress(path: str | os.PathLike) -> dict | None:
    """The child's last recorded progress, or None when there is none.

    A record that is missing, unreadable or not an object reads as None:
    the parent then falls back to the silence rule, which is the rule
    that applies to a child too old to write this file.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get("schema_version") != SCHEMA_VERSION:
        return None
    return record


def units_of(record: dict | None) -> int:
    """The number of finished units in a record (0 when there is none)."""
    if not record:
        return 0
    try:
        return int(record.get("units") or 0)
    except (TypeError, ValueError):
        return 0


def inflight_of(record: dict | None) -> list[dict]:
    """The units in flight in a record (empty when there is none)."""
    if not record:
        return []
    inflight = record.get("inflight")
    if not isinstance(inflight, list):
        return []
    return [unit for unit in inflight if isinstance(unit, dict)]


def oldest_inflight(record: dict | None) -> tuple[str, float] | None:
    """The label and start time of the longest-running unit in flight."""
    oldest: tuple[str, float] | None = None
    for unit in inflight_of(record):
        label = str(unit.get("label") or "")
        try:
            started = float(unit.get("started_at") or 0.0)
        except (TypeError, ValueError):
            started = 0.0
        if not started:
            continue
        if oldest is None or started < oldest[1]:
            oldest = (label, started)
    return oldest


class VisionProgress:
    """The child's side of the channel: what it is doing, and how far.

    Every declaration writes the record, so a parent polling the file
    sees the child's last declaration rather than a guess.  A write that
    fails is swallowed: a progress record that cannot be written must
    never take down the measurement it describes.
    """

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.pid = os.getpid()
        self.stage = ""
        self.units = 0
        self._inflight: dict[str, dict] = {}
        self._lock = threading.Lock()
        # The WRITES are serialized separately from the state: two
        # threads that finished a unit at the same time each wrote a
        # record, and the file ended up holding the OLDER of the two
        # snapshots - the atomic replace made each write whole, not
        # ordered. The parent then read a counter that stood still
        # while the child was working, and the wave's last frame was
        # never reported (measured 2026-10-21: 26 of 200 extractions
        # of five frames reported four, and a frame left in flight).
        self._write_lock = threading.Lock()
        self._sequence = 0
        self._closed = False
        self._refusal: dict | None = None

    # -- declaring what the child is doing -----------------------------

    def stage_begin(self, stage: str) -> None:
        """Name the phase the child is in (a coarse sweep, a load, ...)."""
        with self._lock:
            self.stage = str(stage)
        self.write()

    def unit_begin(self, label: str) -> str:
        """Declare a unit of work in flight (one call, one frame, ...).

        Returns the token that ends it. The token is what makes the end
        idempotent: a unit that is finished on two paths (a cut that
        fails and the loop that moves on) is counted once.
        """
        with self._lock:
            self._sequence += 1
            key = f"{threading.get_ident()}:{self._sequence}"
            self._inflight[key] = {"label": str(label),
                                   "started_at": time.time()}
        self.write()
        return key

    def unit_done(self, token: str | None) -> None:
        """Record that the unit in flight finished (idempotent)."""
        if not token:
            return
        with self._lock:
            if self._inflight.pop(token, None) is None:
                # Already finished: counting it twice would make the
                # counter a fiction.
                return
            self.units += 1
        self.write()

    def close(self) -> None:
        """Record that the child is done with its work."""
        with self._lock:
            self._closed = True
            self._inflight.clear()
        self.write()

    def refuse(self, reason: str, scope: str = "batch") -> None:
        """Record that the child refused, and why.

        A refusal says the work will not be done, so the parent does not
        have to guess from an exit code or a log line. `scope` is
        "batch" when every clip the child was asked about is unmeasured
        for this one reason, and "clip" when only the clip the child
        died on is.
        """
        with self._lock:
            self._refusal = {"reason": str(reason), "scope": str(scope)}
            self._closed = True
            self._inflight.clear()
        self.write()

    @contextmanager
    def unit(self, label: str):
        """Run one unit of work, counted when it finishes."""
        token = self.unit_begin(label)
        try:
            yield self
        finally:
            self.unit_done(token)

    @contextmanager
    def phase(self, stage: str):
        """Run a phase of work; every unit inside it is counted."""
        self.stage_begin(stage)
        yield self

    # -- writing the record --------------------------------------------

    def record(self) -> dict:
        """The record as it stands, without writing it."""
        with self._lock:
            inflight = [dict(unit) for unit in self._inflight.values()]
            return {
                "schema_version": SCHEMA_VERSION,
                "pid": self.pid,
                "stage": self.stage,
                "units": self.units,
                "inflight": inflight,
                "refusal": dict(self._refusal) if self._refusal else None,
                "closed": self._closed,
                "updated_at": time.time(),
            }

    def write(self) -> None:
        """Write the record atomically; a failed write is not fatal.

        The snapshot and the replace happen under ONE lock, so the file
        always ends up holding the newest state: the last writer to
        arrive reads the counters as they stand and writes them, and
        no earlier snapshot can land after it.
        """
        with self._write_lock:
            payload = json.dumps(self.record(), ensure_ascii=False)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, staged_name = tempfile.mkstemp(
                    prefix=f".{self.path.name}.", suffix=".tmp",
                    dir=str(self.path.parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(payload)
                    os.replace(staged_name, self.path)
                except BaseException:
                    try:
                        os.unlink(staged_name)
                    except OSError:
                        pass
                    raise
            except OSError:
                # A progress record that cannot be written is a missing
                # measurement of the child's progress, not a failed
                # measurement: the parent falls back to the silence rule.
                return


class NullProgress:
    """A channel that records nothing, for a run that asked for no file."""

    def stage_begin(self, stage: str) -> None:
        return None

    def unit_begin(self, label: str) -> str:
        return ""

    def unit_done(self, token: str | None) -> None:
        return None

    def close(self) -> None:
        return None

    def refuse(self, reason: str, scope: str = "batch") -> None:
        return None

    @contextmanager
    def unit(self, label: str):
        yield self

    @contextmanager
    def phase(self, stage: str):
        yield self


class ProgressWatcher:
    """The parent's side of the channel: what has moved since last look.

    `observe()` returns the state the parent needs to decide whether the
    child is working, and how long the unit in flight has been running.
    """

    def __init__(self, path: str | os.PathLike | None):
        self.path = Path(path) if path else None
        self._units = 0
        self._inflight = 0

    def observe(self) -> dict:
        """Read the child's record and report what moved."""
        record = read_progress(self.path) if self.path else None
        if record is None:
            return {"present": False, "advanced": False, "units": self._units,
                    "inflight": 0, "unit_label": "", "unit_age_s": 0.0,
                    "refusal": None}
        units = units_of(record)
        advanced = units > self._units
        self._units = units
        inflight = inflight_of(record)
        oldest = oldest_inflight(record)
        if oldest is None:
            label, age = "", 0.0
        else:
            label = oldest[0]
            age = max(0.0, time.time() - oldest[1])
        refusal = record.get("refusal")
        return {
            "present": True,
            "advanced": advanced,
            "units": units,
            "inflight": len(inflight),
            "unit_label": label,
            "unit_age_s": age,
            "refusal": refusal if isinstance(refusal, dict) else None,
        }
