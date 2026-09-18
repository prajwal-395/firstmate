"""Per-reel phase log: when each reel was asked, answered, built, verified, consolidated.

2026-09-18 the captain asked why reel builds take as long as they do.
The investigation (`firstmate` lane `vep-where-the-reel-wall-clock-goes`)
apportioned batch 5 and hit a wall on the largest item: reel M05 waited
85 minutes between its plan answers arriving (10:06:58) and its build
landing (11:32:08), with a 64-minute window, 10:17 to 11:21, with zero
writes to the project from any lane. The cause could not be recovered,
because nothing written to disk distinguishes deep verification reading
(pure reads leave no trace), Resolve contention from the two other lanes
running that day, or a worker stalled on throttled model turns.

This module is the instrument that makes the next such question
answerable. The LANE writes one line per phase transition, per reel, at
the moment it happens:

- `plan_asked`: the engine wrote a plan request for the reel (one line
  per channel: semantic, span, motion).
- `answers_arrived`: the engine read the model's answers for the reel,
  naming each channel's basis (`planned`, `awaiting_model_answer`, ...).
- `build_started` / `build_finished`: the Resolve placement began and
  ended. `build_started` carries seconds since `answers_arrived`, so an
  M05-class stall names itself on the line.
- `verified`: the conformance gate graded the reel's staging and passed.
- `consolidated`: promotion moved the staging onto the final name.
- `wait`: a one-line reason for any wait, written by the thing doing the
  waiting - no model answer on file, a reel left alone, a gate refusal,
  a build failure. A reel with `plan_asked` but no `build_finished`
  must have a `wait` line saying why, or the silence is back.

THE TRAP THIS MUST NOT WALK BACK INTO: the request files under
`pipeline_output/llm_requests` are REWRITTEN on every build
(`reel_semantic_visual.write_request`), so their mtime is the last
rewrite and not the ask. Every event here carries its OWN timestamp
taken at the moment the phase happens (`_utcnow`), and nothing is ever
inferred from a file time later. A phase whose timing cannot be captured
honestly is absent rather than estimated.

CHEAP ON PURPOSE: append-only JSON lines, one helper, call sites at the
places that already know. The file is JSONL rather than JSON so
concurrent lanes append without a read-modify-write race. Filing never
fails a build: a write that cannot land is said on stderr and the build
continues, because an instrument that refuses correct output is worse
than a missing line (AGENTS.md 10.4) - and a missing line is visible as
a gap, while a fabricated timestamp would be trusted.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional

FORMAT = "reel_phase_log/1"
FILENAME = "reel_phase_log.jsonl"

PLAN_ASKED = "plan_asked"
ANSWERS_ARRIVED = "answers_arrived"
BUILD_STARTED = "build_started"
BUILD_FINISHED = "build_finished"
VERIFIED = "verified"
CONSOLIDATED = "consolidated"
WAIT = "wait"

PHASES = (PLAN_ASKED, ANSWERS_ARRIVED, BUILD_STARTED, BUILD_FINISHED,
          VERIFIED, CONSOLIDATED, WAIT)
"""Every phase a line may carry. Unknown phases are refused, because a
line whose phase nothing reads is another silence."""


def _utcnow() -> str:
    """This instant, UTC, ISO-8601. The single place timestamps are taken."""
    return datetime.datetime.now(
        datetime.timezone.utc).isoformat()


def _log_path(project_folder: str) -> str:
    from library.tools.project_layout import Area, ProjectLayout

    return os.path.join(
        str(ProjectLayout(project_folder).write_dir(Area.REVIEW)),
        FILENAME)


def log_event(project_folder: str, reel_number: int, reel_name: str,
              phase: str, detail: str = "") -> Dict[str, Any]:
    """Write one phase line for one reel, and return it.

    The timestamp is taken HERE, at the moment the caller transitions -
    never passed in, never read off a file. Callers wrap this in
    try/except-free code on purpose: a filing failure is said on stderr
    and the event (with `"unfiled": True`) is still returned, so the
    build never fails over its own instrument.
    """
    if phase not in PHASES:
        raise ValueError(
            f"{phase!r} is not a reel phase this log may carry: "
            f"{list(PHASES)}. A line whose phase nothing reads is "
            f"another silence.")
    event: Dict[str, Any] = {
        "format": FORMAT,
        "reel_number": int(reel_number),
        "reel": str(reel_name or ""),
        "phase": phase,
        "at": _utcnow(),
        "detail": str(detail or ""),
    }
    if not project_folder:
        event["unfiled"] = True
        print("  reel phase log: no project folder, "
              f"line for reel {reel_number} phase {phase} not filed",
              file=sys.stderr)
        return event
    try:
        path = _log_path(project_folder)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
    except (OSError, ValueError) as exc:
        event["unfiled"] = True
        print(f"  reel phase log unavailable ({exc}) - reel {reel_number} "
              f"phase {phase} said here and not filed",
              file=sys.stderr)
    return event


def log_wait(project_folder: str, reel_number: int, reel_name: str,
             reason: str) -> Dict[str, Any]:
    """One line saying why this reel is not moving, by the waiter.

    `reason` is one line naming what is waited on or what decided
    against moving: "no model answer on file (...), building without
    visuals", "LEAVING ALONE: ...", "verification refused: ...".
    """
    return log_event(project_folder, reel_number, reel_name, WAIT,
                     detail=reason)


def read_events(project_folder: str) -> List[Dict[str, Any]]:
    """Every phase line filed for this project, in filed order.

    Malformed lines are skipped, never fatal: a half-written tail from
    a killed build must not take the whole instrument with it.
    """
    from library.tools.project_layout import Area, ProjectLayout

    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.REVIEW)),
        FILENAME)
    if not os.path.isfile(path):
        return []
    events: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("phase") in PHASES:
                events.append(row)
    return events


def _parse_at(value: Any) -> Optional[datetime.datetime]:
    try:
        at = datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.timezone.utc)
    return at


def seconds_since(event: Dict[str, Any]) -> Optional[float]:
    """Seconds from this event's own timestamp to now, or None.

    Both endpoints are recorded, never estimated: the event's `at`
    (taken when the phase happened) and this instant. What the
    `build_started` line uses to say how long the reel waited for its
    build since its answers arrived.
    """
    at = _parse_at((event or {}).get("at"))
    if at is None:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    return round((now - at).total_seconds(), 1)


def summarize(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-reel reading of the log: phase times, gaps, waits between.

    The next investigation's starting point. For each reel (keyed by
    name, falling back to number): the first timestamp of each phase,
    the seconds from `answers_arrived` to `build_started` (the M05
    gap lives here), the build duration, and every `wait` reason filed
    between the first ask and the build start - which is what names a
    silence as engine-caused or upstream of the engine. A reel whose
    answers arrived and whose build started much later with no `wait`
    in between stalled upstream of every wait the engine records:
    worker loop, model turns, or contention from another lane, each of
    which now has its own line to confirm or exclude.
    """
    by_reel: Dict[str, Dict[str, Any]] = {}
    for event in events or ():
        key = str(event.get("reel") or event.get("reel_number"))
        slot = by_reel.setdefault(key, {
            "reel": event.get("reel") or "",
            "reel_number": event.get("reel_number"),
            "phases": {}, "waits": [],
        })
        phase = event.get("phase")
        at = _parse_at(event.get("at"))
        if phase == WAIT:
            slot["waits"].append({
                "at": event.get("at"),
                "detail": event.get("detail") or "",
            })
        elif phase in PHASES and phase not in slot["phases"] and at is not None:
            slot["phases"][phase] = event.get("at")

    for slot in by_reel.values():
        phases = slot["phases"]
        first_ask = _parse_at(phases.get(PLAN_ASKED))
        answers = _parse_at(phases.get(ANSWERS_ARRIVED))
        started = _parse_at(phases.get(BUILD_STARTED))
        finished = _parse_at(phases.get(BUILD_FINISHED))
        slot["seconds_ask_to_answers"] = (
            round((answers - first_ask).total_seconds(), 1)
            if first_ask is not None and answers is not None else None)
        slot["seconds_answers_to_build"] = (
            round((started - answers).total_seconds(), 1)
            if answers is not None and started is not None else None)
        slot["seconds_build"] = (
            round((finished - started).total_seconds(), 1)
            if started is not None and finished is not None else None)
        if first_ask is not None and started is not None:
            slot["waits_between_answers_and_build"] = [
                w for w in slot["waits"]
                if (answers is None or _parse_at(w.get("at")) is None
                    or _parse_at(w.get("at")) >= answers)
                and (_parse_at(w.get("at")) is None
                     or _parse_at(w.get("at")) <= started)]
        else:
            slot["waits_between_answers_and_build"] = list(slot["waits"])
    return by_reel
