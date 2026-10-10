"""Measured history for budgeting: what past runs cost.

Tier-1's time budget and Tier-2's scaled timeout are both COMPUTED
from measurements, not tuned by feel. This module owns the reads:

- per-test durations: `scripts/pytest_timing.py` telemetry (written by
  every full-gate run), falling back to a uniform average derived from
  ren-ci Tier-2 logs when the telemetry is missing;
- full-suite wall time: the latest Tier-2 log's measured elapsed,
  else timing-derived, else the documented fallback;
- failure rates: an optional JSON hook, empty when unrecorded.

Every scaling decision stays a pure function of explicit inputs so
the deterministic local_ci tests can pin it without fixtures.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import re

from library.tools.local_ci import config

_WALL_SECONDS = re.compile(r"in (\d+(?:\.\d+)?)s")


def _timing_reader():
    """The telemetry reader without importing the scripts tree.

    `scripts/` is not a package the daemon can rely on from every
    working directory, so load `pytest_timing.py` by path. Anything
    unreadable is a cold start, never an exception.
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        path = os.path.join(repo, "scripts", "pytest_timing.py")
        spec = importlib.util.spec_from_file_location(
            "ren_pytest_timing", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, SyntaxError):
        return None


def test_durations() -> dict:
    """Historical per-test-file durations in seconds, possibly empty.

    Cold (missing/unreadable telemetry) is a normal state, not an
    error: callers estimate unknown files instead of refusing the run.
    """
    reader = _timing_reader()
    if reader is None:
        return {}
    try:
        durations = reader.read_timings()
    except (OSError, ValueError):
        return {}
    return dict(durations) if isinstance(durations, dict) else {}


def tier2_wall_seconds(state_dir: str = "") -> float | None:
    """The longest measured Tier-2 full-suite wall time, or None.

    Reads every `tier2-*.log` under the state dir and parses pytest's
    `in Xs` elapsed, keeping the MAXIMUM. The maximum - not the
    latest - sizes the timeout: a quick red run or a narrowed pass
    must never shrink the bound below the suite's demonstrated cost,
    or the next full run dies at the same fixed wall that killed the
    October batch at 98%. A missing dir, no logs, or no parseable
    elapsed is None - the caller falls back, it never guesses from
    silence.
    """
    directory = state_dir or config.state_dir()
    logs = os.path.join(directory, "logs", "tier2-*.log")
    try:
        candidates = sorted(glob.glob(logs))
    except OSError:
        return None
    best: float | None = None
    for log in candidates:
        try:
            with open(log, encoding="utf-8") as handle:
                text = handle.read()[-8000:]
        except OSError:
            continue
        for match in _WALL_SECONDS.findall(text):
            try:
                elapsed = float(match)
            except ValueError:
                continue
            if elapsed > 0 and (best is None or elapsed > best):
                best = elapsed
    return best


def full_suite_wall_seconds(state_dir: str = "") -> float:
    """The measured full-suite wall time for timeout scaling.

    Latest Tier-2 log first (real wall under real conditions), else
    the timing telemetry mapped to wall time at Tier-2 parallelism
    (`-n 2`), else the documented fallback. Never zero or negative:
    scaling multiplies this, and a zero measurement would pin the
    timeout at the floor while claiming to be measured.
    """
    wall = tier2_wall_seconds(state_dir)
    if wall is not None and wall > 0:
        return wall
    durations = test_durations()
    if durations:
        derived = sum(durations.values()) / 2.0
        if derived > 0:
            return derived
    return float(config.TIER2_MEASURED_FALLBACK_WALL_SECONDS)


def failure_rates(path: str = "") -> dict:
    """Historical per-test failure rates in 0..1, empty when unrecorded.

    No ledger records per-test outcomes yet, so this is a hook: a JSON
    object at VEP_TEST_FAILURE_RATES_FILE (or the given path) mapping
    repo-relative test paths to rates. Malformed or absent is {},
    which orders purely by distance - never a refusal.
    """
    source = path or os.environ.get("VEP_TEST_FAILURE_RATES_FILE", "")
    if not source:
        return {}
    try:
        with open(source, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    rates = {}
    for name, value in data.items():
        try:
            rate = float(value)
        except (TypeError, ValueError):
            continue
        if isinstance(name, str) and 0.0 <= rate <= 1.0:
            rates[name] = rate
    return rates


def tier2_timeout_seconds(measured_wall_seconds: float,
                          load_factor: float | None = None) -> int:
    """Scale a measured suite wall time into a Tier-2 timeout.

    `measured * factor`, clamped to [floor, ceiling]: the floor keeps
    today's bound when the suite is tiny or the measurement thin, the
    ceiling keeps a hung gate from holding the machine forever. A
    non-positive measurement or factor is not a number to scale - it
    is the floor. An omitted factor is the configured default.
    """
    if load_factor is None:
        factor = float(config.TIER2_TIMEOUT_LOAD_FACTOR)
    else:
        factor = load_factor
    try:
        measured = float(measured_wall_seconds)
    except (TypeError, ValueError):
        measured = 0.0
    if not (measured > 0) or not (factor > 0):
        return int(config.TIER2_TIMEOUT_FLOOR_SECONDS)
    scaled = measured * factor
    bounded = min(float(config.TIER2_TIMEOUT_CEILING_SECONDS),
                  max(float(config.TIER2_TIMEOUT_FLOOR_SECONDS), scaled))
    return int(bounded)


def default_tier2_timeout(state_dir: str = "") -> int:
    """The timeout the next Tier-2 run should use, measured just now."""
    return tier2_timeout_seconds(full_suite_wall_seconds(state_dir))
