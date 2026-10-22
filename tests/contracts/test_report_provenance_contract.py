"""The contract between the report writers and the committed reports.

Every report the engine commits has to carry the provenance a reader
needs to trace it: the revision it came from, the environment, the
fixture, the Resolve build, the time, the evaluation schema, the kind of
evidence and the scope it was measured in.

Each test names a defect it would catch:

- a committed report with no provenance, or with one that does not hold;
- a report whose revision names something the repository does not have;
- the two ordinary-request measurements reading as ONE number that
  changed, instead of two measurements of two environments;
- the E2E workflow assessment missing again, or reading as a live run.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import report_provenance as rp

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "tests/live/reports"
REGISTRY = REPO / "library/tools/axi_qualification_records.json"

#: The canonical reports BASE-001 names: the editorial acceptance
#: measurements, the task records they were measured from, and the AXI
#: qualification registry. The list is EXPLICIT, because a report
#: another lane lands is not this gate's business - the gate is about
#: the ones a reader quotes a number from.
CANONICAL_REPORTS = (
    "tests/live/reports/editorial-acceptance.json",
    "tests/live/reports/editorial-acceptance-blind.json",
    "tests/live/reports/editorial-brief-acceptance.json",
    "tests/live/reports/editorial-request-acceptance.json",
    "tests/live/reports/editorial-request-acceptance-2026-10-20.json",
    "tests/live/reports/finished-video-benchmark.json",
    "library/tools/axi_qualification_records.json",
)

ORDINARY_REQUEST_2026_10_20 = REPORTS / "editorial-request-acceptance-2026-10-20.json"
ORDINARY_REQUEST_LATEST = REPORTS / "editorial-request-acceptance.json"
E2E_ASSESSMENT = REPORTS / "editorial-acceptance.json"


def _committed_reports() -> list[Path]:
    """The canonical reports, plus the task records they were measured from."""
    paths = [REPO / name for name in CANONICAL_REPORTS]
    paths.extend(sorted((REPORTS / "editorial-bench-records").glob("*.json")))
    return paths


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_committed_report_carries_a_provenance_that_holds() -> None:
    """No committed report is a claim nobody can trace."""
    problems: list[str] = []
    for path in _committed_reports():
        payload = _load(path)
        provenance = payload.get("provenance")
        if provenance is None:
            problems.append(f"{path.relative_to(REPO)}: no provenance")
            continue
        for breach in rp.validate(provenance):
            problems.append(f"{path.relative_to(REPO)}: {breach}")
    assert not problems, "\n".join(problems)


def test_every_committed_report_names_a_revision_the_repository_has() -> None:
    """A revision nobody can check is not a revision.

    The revision a committed report names must be one the LANDING
    BRANCH carries: a reader who checks out the branch the report
    landed on must be able to reach the revision the report claims it
    was produced from.

    The check is reachability, not presence in the object database.
    Presence is not a property of the report: a clone that also holds
    the measuring lane's branch finds the commit there, and a clone of
    the branch alone does not - so the same main read green on one
    machine and red on another, and the defect (a report naming a
    revision the branch does not carry) passed unnoticed wherever the
    lane's branch happened to be fetched. Measured 2026-10-21: the
    AXI qualification registry and the finished-video benchmark each
    named a lane-branch commit that no merge carried into main, because
    the lane measured on its branch and the merge was a squash.

    A lane that measures on a branch therefore names, in the report it
    commits, the revision the landing branch carries - the one a
    reader can check the file against (`report_provenance.commit_for_path`
    is that revision's own reading).
    """
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH, so no revision can be checked")
    missing: list[str] = []
    for path in _committed_reports():
        sha = _load(path)["provenance"]["source_commit"]
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", str(sha), "HEAD"],
            cwd=str(REPO), capture_output=True, encoding="utf-8",
            check=False)
        if completed.returncode != 0:
            missing.append(f"{path.relative_to(REPO)}: {sha}")
    assert not missing, (
        "these committed reports name a revision the branch does not "
        "carry, so a reader cannot check them:\n" + "\n".join(missing))


def test_the_two_ordinary_request_measurements_are_two_measurements() -> None:
    """1 of 10 and 6 of 10 are two environments, not one number changing."""
    older = _load(ORDINARY_REQUEST_2026_10_20)
    newer = _load(ORDINARY_REQUEST_LATEST)

    assert rp.scope_key(older) != rp.scope_key(newer), \
        "the two ordinary-request measurements share a scope, so one of " \
        "them silently replaces the other"
    for payload, path in ((older, ORDINARY_REQUEST_2026_10_20),
                          (newer, ORDINARY_REQUEST_LATEST)):
        reconciliation = payload["provenance"].get("reconciliation") or {}
        assert reconciliation.get("denominator"), \
            f"{path.name} states no denominator"
        assert reconciliation.get("what_this_file_measures"), \
            f"{path.name} states no scope"
        assert reconciliation.get("result"), \
            f"{path.name} states no result"
    assert "editorial-request-acceptance-2026-10-20.json" in \
        newer["provenance"]["reconciliation"][
            "the_older_measurement_of_the_same_measurement"]
    assert "editorial-request-acceptance.json" in \
        older["provenance"]["reconciliation"][
            "the_newer_measurement_of_the_same_measurement"]


def test_the_ordinary_request_reports_keep_their_own_denominators() -> None:
    """A report counts what it ran, over the tasks it ran."""
    for path in (ORDINARY_REQUEST_2026_10_20, ORDINARY_REQUEST_LATEST):
        payload = _load(path)
        assert payload["task_count"] == 10, path.name
        assert payload["passed"] + payload["failed"] == payload["task_count"], \
            path.name


def test_the_e2e_workflow_assessment_is_present_and_says_it_is_not_live() -> None:
    """The assessment PR #2952 replaced in place is back, and says what it is."""
    payload = _load(E2E_ASSESSMENT)
    assert payload["schema"] == "ren-editorial-e2e-workflow-assessment/1"
    assert payload["provenance"]["evidence_kind"] == "implementation", \
        "a repository assessment is not a live qualification"
    assert "no live" in payload["provenance"]["fixture"]
    assert payload["provenance"]["recovered_from"]["commit"], \
        "the assessment does not say which revision it was recovered from"


def test_the_registry_names_the_report_each_row_came_from() -> None:
    """A qualification is traceable to the run that earned it.

    The registry keeps the trail ON THE ROW. Every routing row names the
    report its evidence came from, and every retained row keeps the
    evidence it was retained with. The registry's own `imported` list is
    not the trail any more: it named the import BEFORE the one that
    produced the row, so it was history rather than the row's own story,
    and the rows now carry their evidence themselves.
    """
    registry = _load(REGISTRY)
    rows = [(pair, row) for pair, row in (registry.get("records") or {}).items()]
    rows += [(row.get("operation_backend") or "?", row.get("record") or {})
             for row in (registry.get("retained_historical_evidence") or [])]
    assert rows, "the registry holds no qualified row"
    for pair, row in rows:
        evidence = row.get("evidence")
        assert evidence, f"{pair} names no report it came from"
        assert ".json" in evidence, (
            f"{pair} names no report file in its evidence: {evidence!r}")


def test_the_committed_ordinary_request_reports_carry_the_scope_their_writer_produces() -> None:
    """A re-run of the same measurement lands on the same report."""
    from tests.live.editorial_acceptance import QUALIFICATION_FIXTURE, TASKS

    task_set = ",".join(task.id for task in TASKS)
    for path, state in ((ORDINARY_REQUEST_LATEST, "seeded"),
                        (ORDINARY_REQUEST_2026_10_20, "unavailable")):
        scope = _load(path)["provenance"]["scope"]
        assert scope["measurement"] == "ordinary_request_acceptance", path.name
        assert scope["evaluation_level"] == 2, path.name
        assert scope["task_set"] == task_set, path.name
        assert scope["fixture"] == f"{QUALIFICATION_FIXTURE} ({state})", \
            path.name


def test_the_committed_blind_report_carries_the_scope_its_writer_produces() -> None:
    """The blind run is the orchestration measurement, and says so."""
    from tests.live.editorial_acceptance import (COMPOUND_REQUESTS,
                                                  QUALIFICATION_FIXTURE, TASKS)

    scope = _load(REPORTS / "editorial-acceptance-blind.json")["provenance"]
    assert scope["evidence_kind"] == "orchestration"
    assert scope["scope"]["measurement"] == "blind_editorial_requests"
    assert scope["scope"]["evaluation_level"] == 3
    assert scope["scope"]["task_set"] == ",".join(
        task.id for task in TASKS + COMPOUND_REQUESTS)
    assert scope["scope"]["fixture"] == QUALIFICATION_FIXTURE


def test_the_committed_brief_report_carries_the_scope_its_writer_produces() -> None:
    """The brief acceptance report is scoped by the brief SET's digest."""
    from library.tools.editorial_acceptance import briefs

    the_set = briefs.load_brief_set()
    scope = _load(REPORTS / "editorial-brief-acceptance.json")["provenance"]
    assert scope["evidence_kind"] == "editorial-acceptance"
    assert scope["scope"] == {
        "measurement": "editorial_brief_acceptance",
        "evaluation_level": 4,
        "task_set": the_set["sha256"],
        "fixture": f"brief set sha256 {the_set['sha256']}",
    }
