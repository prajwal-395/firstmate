"""A duration that cannot be true is an ANOMALY, never a number.

The performance ledger is append-only, so a row written with a wrong
duration stays in the file forever, and every reader of a cost
comparison believes it.  The defect this module closes is that shape of
wrongness: a PHASE that claims more wall time than the RUN that
recorded it had.

Measured on the shipped `geo-podcast` ledger (read only, 2026-10-21):
`otio_placement.fusion_comps_s` recorded 131,422-167,228 s per
placement - 17 rows, 2,643,694.8 s, 734.4 hours - for a phase that took
under a minute.  The number was the MACHINE'S UPTIME: the producer
subtracted an elapsed AVERAGE from a monotonic clock, and macOS's
`time.perf_counter()` IS the uptime.  Both cost readers charged those
seconds to Resolve (`perf_ledger.STAGE_LAYERS` -> "axi",
`critical_path.BUCKET_BY_LAYER` -> RESOLVE), so every "Resolve or the
native renderer?" comparison read 36 hours of phantom Resolve time.

So a phase is checked against the wall of the run that recorded it, and
a phase that fails the check becomes a TYPED ANOMALY: it keeps the
number that was claimed, and it is charged to no stage, no engine and
no resource.  The number is not deleted - it is demoted from a
measurement to a claim.

Two bounds, one rule:

- at RECORD time the run's wall so far is `now - the run's start`, and
  the run's start is in the run's own id (`run_id.started_at`).  A phase
  that claims more than the run has been alive is impossible, so it is
  written as an anomaly and never as a duration.
- at READ time the run's wall is what the run RECORDED - the sum of its
  capability walls - which is the only bound a stored row can be
  checked against.  A stored phase longer than that is an anomaly too,
  which is how the 17 historical rows stop reading as Resolve time.

One enumeration: `library/tools/ledger_anomaly.py`.
"""

from __future__ import annotations

import time
from typing import Any

from library.tools import run_id as run_identity

#: The row kind an impossible measurement is recorded under.  A reader
#: that charges `wall_s` to a stage never sees one, because an anomaly
#: row carries no `wall_s` and no `started_at`.
ANOMALY = "anomaly"

#: The name of the defect: a phase that claims more wall time than the
#: run that recorded it had.
PHASE_EXCEEDS_RUN_WALL = "phase_exceeds_run_wall_time"

#: The row kinds this module reasons about.  Spelled out here because
#: `perf_ledger` imports this module, and the import must not be a
#: cycle.
_SPAN = "span"
_CAPABILITY = "capability"


def run_wall_s(rows: list[dict[str, Any]], run_id: str) -> float | None:
    """The wall time the run RECORDED for itself, in seconds.

    The ledger's own definition of a run's whole: the sum of its
    capability walls (`perf_ledger.profile` charges a run's stages
    against exactly this).  `None` when the run recorded no capability
    wall - then nothing bounds a phase, and no phase is called
    impossible.  Unknown is never zero.
    """
    total = 0.0
    recorded = False
    for row in rows:
        if row.get("run_id") != run_id or row.get("kind") != _CAPABILITY:
            continue
        wall = row.get("wall_s")
        if not isinstance(wall, (int, float)) or isinstance(wall, bool):
            continue
        total += float(wall)
        recorded = True
    return total if recorded else None


def claim_exceeds_run_wall(wall_s: float, run_id: str | None, *,
                           now: float | None = None) -> float | None:
    """The run's wall so far, when a phase claims more than the run lived.

    Returns the bound (the run's wall so far) when the claim exceeds it,
    and `None` when the claim fits, or when the run's start is unknown
    (a legacy id carries no timestamp).  A run whose start is unknown
    is not a run whose phases are impossible.
    """
    start = run_identity.started_at(run_id)
    if start is None:
        return None
    elapsed = (time.time() if now is None else float(now)) - start
    if elapsed <= 0:
        return None
    return elapsed if float(wall_s) > elapsed else None


def anomaly_row(row: dict[str, Any], *, run_wall_s: float,
                claimed_wall_s: float | None = None) -> dict[str, Any]:
    """The typed anomaly a row becomes when its duration cannot be true.

    The claimed number is KEPT, under a name that says what it is
    (`claimed_wall_s`), so nothing is lost and nothing is measured.  A
    reader that charges `wall_s` to a stage cannot charge this row: it
    carries no `wall_s` and no `started_at`.
    """
    claimed = row.get("wall_s") if claimed_wall_s is None else claimed_wall_s
    anomaly: dict[str, Any] = {
        "kind": ANOMALY,
        "anomaly": PHASE_EXCEEDS_RUN_WALL,
        "run_id": row.get("run_id"),
        "capability": row.get("capability"),
        "layer": row.get("layer"),
        "claimed_wall_s": (round(float(claimed), 3)
                           if isinstance(claimed, (int, float))
                           and not isinstance(claimed, bool) else None),
        "run_wall_s": round(float(run_wall_s), 3),
    }
    for key in ("phase", "reel", "placement"):
        if row.get(key) is not None:
            anomaly[key] = row[key]
    if row.get("started_at") is not None:
        anomaly["claimed_started_at"] = row["started_at"]
    return anomaly


def clamp_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows, with every impossible phase replaced by its anomaly.

    A phase is impossible when it claims more wall time than the run
    that recorded it had.  The bound is the run's RECORDED wall (its
    capability walls), so a run that recorded no wall is never clamped:
    unknown bounds nothing.
    """
    walls: dict[Any, float] = {}
    bounded: set[Any] = set()
    for row in rows:
        run = row.get("run_id")
        if run is None or row.get("kind") != _CAPABILITY:
            continue
        wall = row.get("wall_s")
        if not isinstance(wall, (int, float)) or isinstance(wall, bool):
            continue
        walls[run] = walls.get(run, 0.0) + float(wall)
        bounded.add(run)
    clamped: list[dict[str, Any]] = []
    for row in rows:
        run = row.get("run_id")
        wall = row.get("wall_s")
        if (row.get("kind") != _SPAN or run not in bounded
                or not isinstance(wall, (int, float))
                or isinstance(wall, bool)):
            clamped.append(row)
            continue
        bound = walls[run]
        if float(wall) > bound:
            clamped.append(anomaly_row(row, run_wall_s=bound))
        else:
            clamped.append(row)
    return clamped


def anomalies_of(rows: list[dict[str, Any]], run_id: str | None = None
                  ) -> list[dict[str, Any]]:
    """The anomaly rows of a run (of every run when `run_id` is None)."""
    return [row for row in rows
            if row.get("kind") == ANOMALY
            and (run_id is None or row.get("run_id") == run_id)]


def summarise(rows: list[dict[str, Any]], run_id: str | None = None
              ) -> dict[str, Any]:
    """What the anomalies of a run amount to, in one shape.

    `claimed_wall_s` is what the impossible phases claimed, NOT time
    that was spent: it is the size of the mistake, reported so a reader
    can see it, never charged to anything.
    """
    anomalies = anomalies_of(rows, run_id)
    claimed = sum(float(row.get("claimed_wall_s") or 0.0)
                  for row in anomalies)
    return {
        "count": len(anomalies),
        "claimed_wall_s": round(claimed, 3),
        "layers": sorted({str(row.get("layer")) for row in anomalies}),
    }
