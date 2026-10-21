"""The task record a run leaves behind, and the shape the harness reads.

A record is what one attempt at one brief produced. It is written by
whoever ran the brief - the live lane's driver, or an agent working the
`ren-co-editor` loop - and read by the acceptance runner, which knows
nothing about how it was produced.

Five sections, and each answers one question:

- ``interpretation`` - what the run thought the request was, and which
  work it named. It may also carry the canonical intent the run read
  the request into (``intent``), the constraints the request stated
  that the reading carried (``constraints_covered``), and the evidence
  behind each resolved target (``resolved_targets[].evidence``); the
  harness MEASURES each of those rather than believing it.
- ``retrieval`` - the source moments it found, and the evidence behind
  each one.
- ``planning`` - the operations it meant to run, and which of the
  brief's requirements they cover.
- ``execution`` - the receipt, the checks, the collateral, the route and
  the number of attempts.
- ``delivery`` - the delivered file, its digest, and the technical
  checks taken off it.

The record also carries the PROJECT it was worked in, and the substrate
that project's footage came from: the acceptance harness reads the
substrate to decide whether the brief's premise is in the footage at
all, so a record that names no substrate is a record whose premise
nobody has checked.

The record is DATA, and it is validated rather than trusted: a section
that says what it must say is checked when it is read, so a run that
wrote half a record produces a `not_measured` stage rather than a
silent pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from library.tools import report_provenance

SCHEMA = "ren-editorial-task-record/1"

#: The sections the harness reads, in the order a run produces them.
SECTIONS: tuple[str, ...] = (
    "interpretation",
    "retrieval",
    "planning",
    "execution",
    "delivery",
)

#: The sections a record may carry beyond the five. The assessment is
#: filled by the harness, not by the run. The PROVENANCE says which
#: revision produced the record, on what, and when - without it a
#: record is a claim nobody can trace to a run.
OPTIONAL_SECTIONS: tuple[str, ...] = ("assessment", "resource", "run",
                                      "provenance")


class RecordError(ValueError):
    """The record is not a record this harness can read."""


def new_record(*, task_id: str, brief_id: str, instruction: str,
               interpretation: dict | None = None,
               retrieval: dict | None = None,
               planning: dict | None = None,
               execution: dict | None = None,
               delivery: dict | None = None,
               **extra: Any) -> dict[str, Any]:
    """A record with its sections in place, empty where nothing was seen.

    An absent section is an empty section, not a missing key: the
    harness reads `not_measured` out of it, and a record that omitted
    the key would read as a crash instead.
    """
    record = {
        "schema": SCHEMA,
        "task_id": task_id,
        "brief_id": brief_id,
        "instruction": instruction,
        "interpretation": dict(interpretation or {}),
        "retrieval": dict(retrieval or {}),
        "planning": dict(planning or {}),
        "execution": dict(execution or {}),
        "delivery": dict(delivery or {}),
    }
    for key, value in extra.items():
        record[key] = value
    return record


def validate(record: Any) -> list[str]:
    """Every way the record breaks its contract, named. Empty means it holds."""
    problems: list[str] = []
    if not isinstance(record, dict):
        return ["the record is not an object"]
    if record.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r}, not "
                        f"{record.get('schema')!r}")
    for key in ("task_id", "brief_id", "instruction"):
        if not str(record.get(key) or "").strip():
            problems.append(f"the record has no {key}")
    for section in SECTIONS:
        if section not in record:
            problems.append(f"the record has no {section} section")
        elif not isinstance(record[section], dict):
            problems.append(f"the {section} section is not an object")
    execution = record.get("execution")
    if isinstance(execution, dict) and execution:
        receipt = execution.get("receipt")
        if receipt is not None and not isinstance(receipt, dict):
            problems.append("the execution receipt is not an object")
        elif isinstance(receipt, dict) and not str(
                receipt.get("status") or "").strip():
            problems.append("the execution receipt carries no status")
    delivery = record.get("delivery")
    if isinstance(delivery, dict) and delivery:
        path = delivery.get("path")
        if not str(path or "").strip():
            problems.append("the delivery section names no file")
    project = record.get("project")
    if project is not None and not isinstance(project, dict):
        problems.append("the project section is not an object")
    elif isinstance(project, dict):
        substrate = project.get("substrate")
        if substrate is not None and not isinstance(substrate, str):
            problems.append("the project substrate is not a string")
    provenance = record.get("provenance")
    if provenance is not None:
        problems.extend(
            f"provenance: {problem}"
            for problem in report_provenance.validate(provenance))
    return problems


def stamp_record(record: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """The record carrying its provenance.

    The record is the RUN's account of itself, so the provenance is the
    run's: the revision it ran from, the environment it ran in and the
    fixture it ran against. A record with no provenance is a record
    nobody can trace back to a run, which is why the writer stamps one.
    """
    stamped = dict(record)
    stamped["provenance"] = report_provenance.header(
        evaluation_schema=SCHEMA, **kwargs)
    return stamped


def read(path: str | Path) -> dict[str, Any]:
    """Read one record from disk, and refuse a record that breaks it."""
    target = Path(path)
    raw = json.loads(target.read_text(encoding="utf-8"))
    problems = validate(raw)
    if problems:
        raise RecordError(
            f"{target} is not a record this harness can read:\n  "
            + "\n  ".join(problems))
    return raw


def write(path: str | Path, record: dict[str, Any]) -> Path:
    """Write one record to disk, and refuse to write a broken one."""
    problems = validate(record)
    if problems:
        raise RecordError(
            "the record breaks its contract:\n  " + "\n  ".join(problems))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return target
