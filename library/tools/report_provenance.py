"""What produced a report, where it ran, and how far the claim reaches.

A report is a CLAIM. This module is the claim's provenance: the source
revision it was produced from, the environment it ran in, the fixture it
ran against, the Resolve build it ran on, when it ran, the schema its
numbers are written in, and - the part that stops one number from being
read as another - the KIND of evidence behind it and the SCOPE it was
measured in.

The five evidence kinds, and what each one is worth:

``implementation``
    The code path exists and its own contract test passes. It says
    nothing about the backend.
``simulated``
    A fixture or a double answered. No real backend was exercised.
``live-qualification``
    A real backend was exercised, on a real fixture, and the evidence
    was read back from it.
``orchestration``
    The agent chose the operations and the targets itself. A lower kind
    measured with a typed request in the driver's hand is not this.
``editorial-acceptance``
    A delivered file was judged against a creative brief.

Two rules hold over all of them.

**A number without its denominator is not a number.** Every report that
carries a count carries the SCOPE it was counted in, and two reports
whose scopes differ are never merged, averaged or compared silently.

**Newer does not mean superseding.** A report measured in one scope
never replaces a report measured in another; the writer keeps them side
by side (`write_report`) and records what a report supersedes
explicitly. The incident this exists for: the E2E workflow assessment
in `tests/live/reports/editorial-acceptance.json` was replaced, in the
same file, by an ordinary-request measurement from a different scope -
and the older evidence was gone.

Nothing here invents a measurement. A field nobody measured reads
`not_recorded`, never a zero and never a default that looks like a
reading (`docs/REPORT_PROVENANCE.md`).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

#: The kinds of evidence a report may rest on. The order is the order of
#: strength: a report that names a weaker kind is not thereby wrong, it
#: is answering a narrower question.
EVIDENCE_KINDS: tuple[str, ...] = (
    "implementation",
    "simulated",
    "live-qualification",
    "orchestration",
    "editorial-acceptance",
)

#: The fields every canonical report carries. A report missing one of
#: them is refused rather than read as a report about nothing.
PROVENANCE_FIELDS: tuple[str, ...] = (
    "evidence_kind",
    "source_commit",
    "environment",
    "fixture",
    "resolve_version",
    "run_timestamp",
    "evaluation_schema",
)

#: What a field nobody measured reads. It is a reading - "not recorded"
#: is a fact about the report - and it is never a zero.
NOT_RECORDED = "not_recorded"

#: The fields of a SCOPE. The scope is what makes two numbers about the
#: same thing comparable; two reports whose scopes differ are two
#: measurements, not a newer and an older version of one measurement.
SCOPE_FIELDS: tuple[str, ...] = (
    "measurement",
    "evaluation_level",
    "task_set",
    "fixture",
)

#: The environment variable a run sets when it knows the Resolve build
#: it measured on, so a report does not have to reach into Resolve to
#: name it.
RESOLVE_VERSION_ENV = "REN_RESOLVE_VERSION"

#: The environment variable a run sets when it knows the revision it
#: measured from, for a checkout that is not a git working tree.
SOURCE_COMMIT_ENV = "REN_SOURCE_COMMIT"


class ProvenanceError(ValueError):
    """A report carries no provenance, or carries one that is not true."""


# ── The readings ───────────────────────────────────────────────────


def source_commit(repo_root: str | Path | None = None) -> str:
    """The revision the working tree is at, or `not_recorded`.

    A report that cannot name the revision it was produced from says so
    rather than naming the branch, the directory or the day.
    """
    declared = str(os.environ.get(SOURCE_COMMIT_ENV) or "").strip()
    if declared:
        return declared
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root) if repo_root else None,
            capture_output=True, encoding="utf-8", timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return NOT_RECORDED
    if completed.returncode != 0:
        return NOT_RECORDED
    return completed.stdout.strip() or NOT_RECORDED


#: The reading, bound once, so a parameter of the same name cannot
#: shadow the function that takes it.
_read_source_commit = source_commit


def commit_for_path(path: str | Path) -> str:
    """The revision that recorded a file, or `not_recorded`.

    A report recovered from history names the commit that recorded it:
    that is the revision whose evidence it is, and it is the only
    revision a reader can check the file against.
    """
    target = Path(path)
    if not target.exists():
        return NOT_RECORDED
    try:
        completed = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(target)],
            capture_output=True, encoding="utf-8", timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return NOT_RECORDED
    if completed.returncode != 0:
        return NOT_RECORDED
    return completed.stdout.strip() or NOT_RECORDED


def machine_environment() -> dict[str, str]:
    """The machine a report was produced on.

    The readings are what the running process can see for itself. A
    report recovered from history carries `not_recorded` here instead -
    the machine that produced it is not the machine reading the file.
    """
    return {
        "platform": platform.platform() or NOT_RECORDED,
        "machine": platform.machine() or NOT_RECORDED,
        "python_version": platform.python_version() or NOT_RECORDED,
    }


def utc_now() -> str:
    """The current time, in UTC, in the one format reports carry."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_version(value: Any = None) -> str:
    """The Resolve build a measurement ran on.

    The reading comes from the caller (the run knows), then from the
    environment, and otherwise reads `not_recorded`. It is never guessed
    from the machine's installed applications: a version nobody measured
    is not a version.
    """
    text = str(value or "").strip()
    if text:
        return text
    declared = str(os.environ.get(RESOLVE_VERSION_ENV) or "").strip()
    return declared or NOT_RECORDED


# ── The header ─────────────────────────────────────────────────────


def header(*, evidence_kind: str,
           evaluation_schema: str,
           fixture: Any = NOT_RECORDED,
           resolve_build: Any = None,
           source_commit: Any = None,
           run_timestamp: Any = None,
           environment: Mapping[str, Any] | None = None,
           producer: str | None = None,
           scope: Mapping[str, Any] | None = None,
           **extra: Any) -> dict[str, Any]:
    """The provenance block a report carries.

    Every field is stated, including the ones nobody measured: a reader
    that finds `not_recorded` knows the report did not measure it,
    which is different from a report that measured a zero.
    """
    if evidence_kind not in EVIDENCE_KINDS:
        raise ProvenanceError(
            f"evidence kind {evidence_kind!r} is not one of "
            f"{', '.join(EVIDENCE_KINDS)}")
    the_environment = dict(environment) if environment else machine_environment()
    the_environment.setdefault("platform", NOT_RECORDED)
    the_environment.setdefault("machine", NOT_RECORDED)
    the_environment.setdefault("python_version", NOT_RECORDED)
    the_scope = {field: NOT_RECORDED for field in SCOPE_FIELDS}
    for field, value in (scope or {}).items():
        the_scope[field] = value
    block: dict[str, Any] = {
        "evidence_kind": evidence_kind,
        "source_commit": str(source_commit or "").strip()
        or _read_source_commit(),
        "environment": the_environment,
        "fixture": fixture if fixture else NOT_RECORDED,
        "resolve_version": resolve_version(resolve_build),
        "run_timestamp": str(run_timestamp or "").strip() or utc_now(),
        "evaluation_schema": str(evaluation_schema or "").strip(),
        "scope": the_scope,
    }
    if producer:
        block["producer"] = producer
    for key, value in extra.items():
        block[key] = value
    problems = validate(block)
    if problems:
        raise ProvenanceError(
            "the provenance breaks its contract:\n  " + "\n  ".join(problems))
    return block


def validate(provenance: Any) -> list[str]:
    """Every way a provenance block breaks its contract, named."""
    problems: list[str] = []
    if not isinstance(provenance, Mapping):
        return ["the provenance is not an object"]
    for field in PROVENANCE_FIELDS:
        if field not in provenance:
            problems.append(f"the provenance has no {field}")
        elif provenance.get(field) in (None, ""):
            problems.append(f"the provenance carries no {field}")
    kind = provenance.get("evidence_kind")
    if kind is not None and kind not in EVIDENCE_KINDS:
        problems.append(
            f"evidence kind {kind!r} is not one of "
            f"{', '.join(EVIDENCE_KINDS)}")
    environment = provenance.get("environment")
    if environment is not None and not isinstance(environment, Mapping):
        problems.append("the provenance environment is not an object")
    scope = provenance.get("scope")
    if scope is not None and not isinstance(scope, Mapping):
        problems.append("the provenance scope is not an object")
    elif isinstance(scope, Mapping):
        for field in SCOPE_FIELDS:
            if field not in scope:
                problems.append(f"the provenance scope has no {field}")
    return problems


def stamp(report: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """A report carrying its provenance, or an error naming what is wrong.

    The report is copied, so a caller's own dict is not changed under
    it: the provenance is the report's, and the caller decides whether
    to keep it.
    """
    if "evaluation_schema" not in kwargs:
        kwargs["evaluation_schema"] = str(report.get("schema") or "")
    if "evidence_kind" not in kwargs:
        raise ProvenanceError(
            "a stamped report says what kind of evidence it rests on; "
            "name the evidence kind")
    stamped = dict(report)
    stamped["provenance"] = header(**kwargs)
    return stamped


# ── The scope ──────────────────────────────────────────────────────


def scope_of(report: Mapping[str, Any]) -> dict[str, Any]:
    """The scope a report was measured in.

    A report that declares no scope is scoped by its own schema: it
    measured whatever that schema measures, and nothing else.
    """
    provenance = report.get("provenance")
    if isinstance(provenance, Mapping):
        scope = provenance.get("scope")
        if isinstance(scope, Mapping) and scope:
            return {field: scope.get(field, NOT_RECORDED)
                    for field in SCOPE_FIELDS}
    return {"measurement": str(report.get("schema") or NOT_RECORDED),
            "evaluation_level": NOT_RECORDED,
            "task_set": NOT_RECORDED,
            "fixture": NOT_RECORDED}


def scope_key(report: Mapping[str, Any]) -> str:
    """The key two reports must share before one may replace the other."""
    scope = scope_of(report)
    return "|".join(str(scope.get(field, NOT_RECORDED))
                    for field in SCOPE_FIELDS)


def scope_digest(key: str) -> str:
    """A short digest of a scope key, for a file name that tells two apart."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def scope_slug(report: Mapping[str, Any]) -> str:
    """A file-name-safe name for a scope, for a report kept side by side."""
    scope = scope_of(report)
    parts = [str(scope.get("measurement") or "report"),
             str(scope.get("evaluation_level") or "")]
    slug = "-".join(part for part in parts if part and part != NOT_RECORDED)
    cleaned = "".join(char if char.isalnum() or char in "-_." else "-"
                      for char in slug).strip("-")
    return cleaned.lower() or "report"


# ── The writer ─────────────────────────────────────────────────────


def write_report(path: str | Path, report: Mapping[str, Any]) -> Path:
    """Write a report, and never overwrite a DIFFERENTLY SCOPED one.

    A report measured in one scope replaces a report measured in the
    same scope (and says what it superseded). A report measured in
    another scope is written BESIDE it, under a name that carries the
    scope, so the older evidence stays readable and the two are not
    read as one number that changed.
    """
    target = Path(path)
    key = scope_key(report)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, Mapping):
            if scope_key(existing) == key:
                stamped = dict(report)
                provenance = dict(stamped.get("provenance") or {})
                superseded = provenance.get("supersedes")
                provenance["supersedes"] = _superseded(existing, superseded)
                stamped["provenance"] = provenance
                return _write(target, stamped)
            sibling = target.with_name(
                f"{target.stem}--{scope_slug(report)}{target.suffix}")
            if sibling.exists():
                try:
                    other = json.loads(sibling.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    other = None
                if isinstance(other, Mapping) and scope_key(other) != key:
                    # Two measurements share a name and a slug: the
                    # scope's digest tells them apart, so both stand.
                    sibling = target.with_name(
                        f"{target.stem}--{scope_slug(report)}-"
                        f"{scope_digest(key)}{target.suffix}")
                    if sibling.exists():
                        raise ProvenanceError(
                            f"{sibling} holds a report of another scope and "
                            f"this report's scope {key!r} has no name of "
                            "its own. Choose the name before writing.")
            return _write(sibling, report)
    return _write(target, report)


def _superseded(existing: Mapping[str, Any],
                already: Any) -> list[dict[str, Any]]:
    """What the report being replaced was, stated in the new one."""
    history = [entry for entry in (already or [])
               if isinstance(entry, Mapping)]
    provenance = existing.get("provenance")
    if isinstance(provenance, Mapping):
        entry = {
            "source_commit": provenance.get("source_commit"),
            "run_timestamp": provenance.get("run_timestamp"),
            "evaluation_schema": provenance.get("evaluation_schema"),
        }
        if entry not in history:
            history.append(entry)
    return history


def _write(target: Path, report: Mapping[str, Any]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return target


def read_report(path: str | Path) -> dict[str, Any]:
    """Read a report and refuse one whose provenance does not hold."""
    target = Path(path)
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ProvenanceError(f"{target} is not a report")
    problems = validate(raw.get("provenance"))
    if problems:
        raise ProvenanceError(
            f"{target} carries no usable provenance:\n  "
            + "\n  ".join(problems))
    return raw


def python_version() -> str:
    """The interpreter that produced a report."""
    return platform.python_version() or sys.version.split()[0]
