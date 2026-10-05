#!/usr/bin/env python3
"""Resolve memory setting trial: 75 -> 50 with automatic revert.

Takes a before reading, applies the setting change (takes effect at next
Resolve restart), and provides measurement/revert capability after the
captain's natural restart.

Usage:
    python3 scripts/resolve_memory_trial.py before
    python3 scripts/resolve_memory_trial.py apply
    python3 scripts/resolve_memory_trial.py measure --build-time <seconds>
    python3 scripts/resolve_memory_trial.py check --baseline-build-time <seconds> --current-build-time <seconds>
    python3 scripts/resolve_memory_trial.py revert
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

CONFIG_PATH = Path.home() / "Library/Preferences/Blackmagic Design/DaVinci Resolve/config.dat"
STATE_PATH = Path(__file__).parent / ".resolve_memory_trial_state.json"
SETTING_KEY = "Local.Resource.ResolveMemoryPercentage"
ORIGINAL_VALUE = "75"
TRIAL_VALUE = "50"
SLOWDOWN_THRESHOLD = 0.15


def read_config() -> str:
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"config.dat not found at {CONFIG_PATH}")
    return CONFIG_PATH.read_text(encoding="utf-8")


def get_setting_value(config_text: str, key: str) -> str | None:
    for line in config_text.splitlines():
        line = line.strip()
        if line.startswith(f"{key} ="):
            return line.split("=", 1)[1].strip()
    return None


def set_setting_value(config_text: str, key: str, value: str) -> str:
    lines = config_text.splitlines()
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key} ="):
            lines[i] = f"{key} = {value}"
            found = True
            break
    if not found:
        lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def cmd_before() -> int:
    config = read_config()
    current = get_setting_value(config, SETTING_KEY)
    if current is None:
        print(f"ERROR: {SETTING_KEY} not found in config.dat", file=sys.stderr)
        return 1

    state = load_state()
    state["before"] = {
        "setting_value": current,
        "timestamp": time.time(),
        "config_path": str(CONFIG_PATH),
    }
    save_state(state)

    print(f"BEFORE: {SETTING_KEY} = {current}")
    print(f"State saved to {STATE_PATH}")
    return 0


def cmd_apply() -> int:
    config = read_config()
    current = get_setting_value(config, SETTING_KEY)
    if current is None:
        print(f"ERROR: {SETTING_KEY} not found in config.dat", file=sys.stderr)
        return 1

    if current != ORIGINAL_VALUE:
        print(f"WARNING: current value is {current}, expected {ORIGINAL_VALUE}", file=sys.stderr)

    backup_path = CONFIG_PATH.with_suffix(".dat.trial_bak")
    shutil.copy2(CONFIG_PATH, backup_path)

    new_config = set_setting_value(config, SETTING_KEY, TRIAL_VALUE)
    CONFIG_PATH.write_text(new_config, encoding="utf-8")

    verify = get_setting_value(read_config(), SETTING_KEY)
    if verify != TRIAL_VALUE:
        print(f"ERROR: verification failed, read back {verify}", file=sys.stderr)
        shutil.copy2(backup_path, CONFIG_PATH)
        return 1

    state = load_state()
    state["applied"] = {
        "previous_value": current,
        "new_value": TRIAL_VALUE,
        "timestamp": time.time(),
        "backup_path": str(backup_path),
    }
    save_state(state)

    print(f"APPLIED: {SETTING_KEY} {current} -> {TRIAL_VALUE}")
    print(f"Backup at {backup_path}")
    print("Takes effect at next Resolve restart.")
    return 0


def cmd_measure(build_time: float | None = None) -> int:
    config = read_config()
    current = get_setting_value(config, SETTING_KEY)

    state = load_state()
    measurement = {
        "setting_value": current,
        "timestamp": time.time(),
    }
    if build_time is not None:
        measurement["build_time_seconds"] = build_time

    if "measurements" not in state:
        state["measurements"] = []
    state["measurements"].append(measurement)
    save_state(state)

    print(f"MEASURE: {SETTING_KEY} = {current}")
    if build_time is not None:
        print(f"  build_time = {build_time:.3f}s")
    return 0


def cmd_check(baseline: float, current: float) -> int:
    slowdown = (current - baseline) / baseline
    print(f"Baseline: {baseline:.3f}s")
    print(f"Current:  {current:.3f}s")
    print(f"Slowdown: {slowdown:.1%}")

    if slowdown > SLOWDOWN_THRESHOLD:
        print(f"EXCEEDS {SLOWDOWN_THRESHOLD:.0%} threshold - REVERT REQUIRED")
        return 1
    else:
        print(f"Within {SLOWDOWN_THRESHOLD:.0%} threshold - KEEP")
        return 0


def cmd_revert() -> int:
    config = read_config()
    current = get_setting_value(config, SETTING_KEY)

    state = load_state()
    original = state.get("before", {}).get("setting_value", ORIGINAL_VALUE)

    new_config = set_setting_value(config, SETTING_KEY, original)
    CONFIG_PATH.write_text(new_config, encoding="utf-8")

    verify = get_setting_value(read_config(), SETTING_KEY)
    if verify != original:
        print(f"ERROR: verification failed, read back {verify}", file=sys.stderr)
        return 1

    state["reverted"] = {
        "from_value": current,
        "restored_value": original,
        "timestamp": time.time(),
    }
    save_state(state)

    print(f"REVERTED: {SETTING_KEY} {current} -> {original}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve memory setting trial")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("before", help="Take before reading")
    sub.add_parser("apply", help="Apply 75->50 change")

    p_measure = sub.add_parser("measure", help="Take a measurement")
    p_measure.add_argument("--build-time", type=float, default=None)

    p_check = sub.add_parser("check", help="Check if slowdown exceeds threshold")
    p_check.add_argument("--baseline-build-time", type=float, required=True)
    p_check.add_argument("--current-build-time", type=float, required=True)

    sub.add_parser("revert", help="Revert to original value")

    args = parser.parse_args()

    if args.command == "before":
        return cmd_before()
    elif args.command == "apply":
        return cmd_apply()
    elif args.command == "measure":
        return cmd_measure(args.build_time)
    elif args.command == "check":
        return cmd_check(args.baseline_build_time, args.current_build_time)
    elif args.command == "revert":
        return cmd_revert()
    return 1


if __name__ == "__main__":
    sys.exit(main())
