"""A captain's standing authorisation to apply one reported tail extension.

The carve-out this narrows
--------------------------
`reel_build._analyze_tail_edge` extends a moment end over whole kept
words standing unplayed inside the speaker's pace - except when the
tail runs LONGER than the closing thought it continues. That is a
further passage absorbed, not a severed thought finished, so the
predicate returns `report`: fully judged, placed as approved, loud on
every surface, never applied (firstmate 2026-09-19, carving Reel 28
out by rule rather than by exception: silently lengthening an accepted,
unmarked reel was the failure mode to avoid).

A `report` is a question held for a human. This module is the human's
answer, for exactly one reel: the captain ruled the extension should
apply, knowing the reel gets longer. It is deliberately NOT a narrowing
of the predicate itself - no threshold moves, no bound is declared, and
every other reel keeps reporting exactly as before. The authorisation
is per reel, recorded in the project (never in engine code, which
serves every project), and carries the measured numbers the ruling was
made on, so a later re-transcription that moves the tail refuses
instead of applying a stale yes to new seconds.

What it records
---------------
`<project>/external/tail_extend_authorizations.json`:

    {"authorizations": [{"reel": 28,
                         "reason": "captain 2026-09-21: extend it",
                         "measured_edge": 2265.6,
                         "measured_tail_end": 2287.14,
                         "measured_gap": 0.02}]}

`reel` and a non-empty `reason` are required; the measured numbers are
required where the ruling was made on them, because the check below
reads them. A malformed file REFUSES (`AuthorizationError`) rather
than building silently past a yes it cannot read - the same standing
as `captain_edits`.

How it is applied
-----------------
The build and the gate both load this file once and hand the
reel-to-reason map to `reel_proposal.snap_moment_to_speech`, which
looks up each moment's own number: a reel answers only its own entry,
and a reel with no entry behaves exactly as before. Where the
predicate would report and an entry exists, the end extends to the
measured sentence end with the authorisation named in the finding, the
WHY and the ledger - an applied obedience, not a re-decision.

`check_applied` then weighs what was applied against what was
authorised: the fresh `tail_end` must land within `TOLERANCE_SECONDS`
of the recorded one, or the build refuses. The captain accepted
"longer"; they did not accept an unbounded number, and word timings
drift between runs.

`tests/unit/reels/test_keep_ranges.py`.
"""

from __future__ import annotations

import json
import os

FILENAME = "tail_extend_authorizations.json"
"""The authorisation store, under the project's `external/` declarations."""

TOLERANCE_SECONDS = 0.5
"""How far the applied tail end may sit from the recorded one.

Word timings drift between runs (Reel 09's approved bound already
covers 0.08s of drift as breath), so byte-exact would refuse correct
output - but the authorised seconds are the point of the ruling, so
anything past half a second is a different ruling and refuses.
"""


class AuthorizationError(ValueError):
    """The authorisation file cannot be read as authorisations."""


def authorizations_path(project_folder: str) -> str:
    """Where this project's authorisations live (may not exist)."""
    return os.path.join(str(project_folder), "external", FILENAME)


def load_authorizations(project_folder: str) -> dict:
    """`{reel_number: entry}` for this project, or `{}` with no file.

    Each entry carries at least `reason` (the captain's words) and the
    measured numbers the ruling was made on (`measured_edge`,
    `measured_tail_end`, `measured_gap`). Anything malformed raises
    `AuthorizationError`: a yes the build cannot read must refuse,
    never build silently past it.
    """
    path = authorizations_path(project_folder)
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise AuthorizationError(
            f"{path} cannot be read as tail-extend authorisations: "
            f"{exc}. A recorded yes the build cannot read must "
            f"refuse, never build silently past it.") from exc
    if isinstance(raw, dict) and isinstance(raw.get("authorizations"),
                                            list):
        entries = raw["authorizations"]
    elif isinstance(raw, list):
        entries = raw
    else:
        raise AuthorizationError(
            f"{path} must hold {{\"authorizations\": [...]}} - "
            f"a yes nobody can find is not a yes.")
    out: dict = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AuthorizationError(
                f"{path} entry {index} is not an object - "
                f"a yes nobody can read is not a yes.")
        try:
            reel = int(entry["reel"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthorizationError(
                f"{path} entry {index} names no integer reel: "
                f"{entry!r}.") from exc
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise AuthorizationError(
                f"{path} entry {index} (reel {reel}) carries no reason "
                f"- an authorisation that does not say whose decision "
                f"it is refuses.")
        numbers: dict = {}
        for key in ("measured_edge", "measured_tail_end",
                    "measured_gap"):
            try:
                numbers[key] = float(entry[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise AuthorizationError(
                    f"{path} entry {index} (reel {reel}) carries no "
                    f"number for {key!r} - the ruling was made on "
                    f"measured seconds, and without them the check "
                    f"below has nothing to weigh.") from exc
        if reel in out:
            raise AuthorizationError(
                f"{path} authorises reel {reel} twice - one reel, "
                f"one ruling.")
        out[reel] = {"reel": reel, "reason": reason.strip(), **numbers}
    return out


def authorized_for(authorizations: dict, reel_number: int) -> dict:
    """This reel's entry, or `{}` - a reel answers only its own entry.

    Values are the entries `load_authorizations` returns (reel,
    reason, measured numbers) - one shape, because `check_applied`
    weighs the applied seconds against the recorded ones and a bare
    reason carries nothing to weigh.
    """
    try:
        key = int(reel_number)
    except (TypeError, ValueError):
        return {}
    entry = (authorizations or {}).get(key)
    if not isinstance(entry, dict):
        return {}
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return {}
    return dict(entry)


def check_applied(reel_number: int, entry: dict, tail_end: float,
                  gap: float) -> None:
    """Weigh what the predicate applied against what was authorised.

    Raises `AuthorizationError` where the fresh `tail_end` lands
    further than `TOLERANCE_SECONDS` from the recorded one: the
    captain accepted "longer", not an unbounded number, and word
    timings drift between runs. The gap travels in the message so the
    refusal names what moved.
    """
    try:
        recorded = float(entry["measured_tail_end"])
        applied = float(tail_end)
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorizationError(
            f"reel {int(reel_number)}: the applied tail extension "
            f"carries no measurable end - an authorisation applied "
            f"to nothing refuses.") from exc
    if abs(applied - recorded) > TOLERANCE_SECONDS:
        raise AuthorizationError(
            f"reel {int(reel_number)}: the authorised tail extension "
            f"ends at {recorded:.2f}s but the predicate now extends "
            f"to {applied:.2f}s (gap {float(gap):.2f}s) - materially "
            f"different seconds than the ruling was made on, so the "
            f"build stops here rather than applying a stale yes. "
            f"Re-weigh and re-record.")
