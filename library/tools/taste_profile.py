"""One user's explicitly stated creative preferences, shared across projects.

The profile is optional user data beside Ren's per-user config, never a
project file and never seeded with a value. `ren taste set` records a value
only when a person states it, including who said it and why.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROFILE_VERSION = 1
PROFILE_FILENAME = "taste_profile.json"


class TasteProfileError(ValueError):
    """A per-user taste profile is malformed or cannot hold this preference."""


def _slot(key: str):
    from library.tools import decided_value

    try:
        return decided_value.slot(key)
    except decided_value.UnknownSlot as exc:
        raise TasteProfileError(str(exc)) from exc


def profile_path() -> Path:
    """The per-user profile alongside Ren's user config, outside projects."""
    from library.tools.paths import user_config_path

    return user_config_path().parent / PROFILE_FILENAME


def _read(path: Path) -> dict:
    if not path.exists():
        return {"version": PROFILE_VERSION, "preferences": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TasteProfileError(f"cannot read taste profile {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TasteProfileError(f"taste profile {path} must contain a JSON object")
    if value.get("version") != PROFILE_VERSION:
        raise TasteProfileError(
            f"taste profile {path} has unsupported version {value.get('version')!r}; "
            f"expected {PROFILE_VERSION}")
    preferences = value.get("preferences")
    if not isinstance(preferences, dict):
        raise TasteProfileError(
            f"taste profile {path} must contain an object at 'preferences'")
    return value


def stated_preference(key: str, scope: str = "", *,
                      path: Path | None = None):
    """Return `(value, source)` for a recorded preference, or `(None, "")."""
    row = _slot(key)
    if row.scopes and scope not in row.scopes:
        raise TasteProfileError(
            f"slot {key!r} requires one of scopes {row.scopes!r}; got {scope!r}")
    if not row.scopes and scope:
        raise TasteProfileError(f"slot {key!r} has no scoped preferences")

    location = path or profile_path()
    profile = _read(location)
    slot_preferences = profile["preferences"].get(key)
    if slot_preferences is None:
        return None, ""
    if not isinstance(slot_preferences, dict):
        raise TasteProfileError(
            f"taste profile {location} entry {key!r} must be an object")
    entry = slot_preferences.get(scope)
    if entry is None:
        return None, ""
    if not isinstance(entry, dict):
        raise TasteProfileError(
            f"taste profile {location} entry {key!r}/{scope!r} must be an object")

    try:
        value = entry["value"]
        stated_by = entry["stated_by"]
        reason = entry["reason"]
    except KeyError as exc:
        raise TasteProfileError(
            f"taste profile {location} entry {key!r}/{scope!r} "
            f"is missing {exc.args[0]!r}") from exc
    _validate_value(key, value)
    if not isinstance(stated_by, str) or not stated_by.strip():
        raise TasteProfileError(
            f"taste profile {location} entry {key!r}/{scope!r} needs stated_by")
    if not isinstance(reason, str) or not reason.strip():
        raise TasteProfileError(
            f"taste profile {location} entry {key!r}/{scope!r} needs reason")
    source = (f"{location}#/preferences/{key}/{scope}/value "
              f"(stated by {stated_by.strip()}: {reason.strip()})")
    return value, source


def _validate_value(key: str, value: Any) -> None:
    row = _slot(key)
    if row.solver:
        valid = (isinstance(value, (int, float))
                 and not isinstance(value, bool)
                 and math.isfinite(value))
    else:
        valid = (isinstance(value, (str, int, float))
                 and not isinstance(value, bool)
                 and (not isinstance(value, float) or math.isfinite(value)))
    if not valid:
        units = row.answer_units or row.value_units
        raise TasteProfileError(
            f"preference for {key!r} must be a finite number in {units}")


def _write(path: Path, profile: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        descriptor, raw_temp = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(raw_temp)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(profile, handle, indent=2, ensure_ascii=False,
                      allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        os.chmod(path, 0o600)
    except (OSError, TypeError, ValueError) as exc:
        raise TasteProfileError(f"cannot write taste profile {path}: {exc}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def set_preference(key: str, scope: str, value: Any, *, stated_by: str,
                   reason: str, path: Path | None = None) -> Path:
    """Record one explicit preference in the per-user profile."""
    row = _slot(key)
    if row.scopes and scope not in row.scopes:
        raise TasteProfileError(
            f"slot {key!r} requires one of scopes {row.scopes!r}; got {scope!r}")
    if not row.scopes and scope:
        raise TasteProfileError(f"slot {key!r} has no scoped preferences")
    if not isinstance(stated_by, str) or not stated_by.strip():
        raise TasteProfileError("stated_by must name the person who stated the preference")
    if not isinstance(reason, str) or not reason.strip():
        raise TasteProfileError("reason must preserve why the person stated this preference")
    _validate_value(key, value)

    location = path or profile_path()
    profile = _read(location)
    preferences = profile["preferences"]
    slot_preferences = preferences.setdefault(key, {})
    if not isinstance(slot_preferences, dict):
        raise TasteProfileError(
            f"taste profile {location} entry {key!r} must be an object")
    slot_preferences[scope] = {
        "value": value,
        "stated_by": stated_by.strip(),
        "reason": reason.strip(),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _write(location, profile)
    return location


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren taste",
        description="Record an explicit creative preference for this user across projects.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    set_parser = subparsers.add_parser(
        "set", help="Record one stated preference in the per-user profile")
    set_parser.add_argument("slot", help="Registered decided_value slot")
    set_parser.add_argument("--scope", default="",
                            help="The slot scope, when the value is scoped")
    set_parser.add_argument("--value", required=True, type=float,
                            help="A stated number in the slot's answer units")
    set_parser.add_argument("--stated-by", required=True,
                            help="Name of the person who stated the preference")
    set_parser.add_argument("--reason", required=True,
                            help="Why the person stated this preference")
    args = parser.parse_args(argv)

    try:
        location = set_preference(
            args.slot, args.scope, args.value,
            stated_by=args.stated_by, reason=args.reason)
    except TasteProfileError as exc:
        parser.error(str(exc))
    print(f"Recorded stated preference in {location}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
