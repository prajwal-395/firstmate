"""One runner start, one run id, in all three stores (Lane A1, closes R1).

`pipeline_run.json`, `provenance/runs.jsonl` and the perf ledger each
named the run its own way, so "what did this run cost, what did it
change" could not be joined. The runner now mints one id
(`library/tools/run_id.py`), exports it as `REN_RUN_ID`, and all three
stores record that same value.

Fixture-only: nothing here runs a step, renders, transcribes or opens a
real project. Each test replays what the runner does at start - mint,
export, record - against a scratch project.
"""
from __future__ import annotations

import json
import os

from library.tools import perf_ledger, provenance, run_control
from library.tools import run_id as run_identity
from library.tools.project_layout import ProjectLayout


def _scratch(tmp_path):
    ProjectLayout(tmp_path).ensure()
    return str(tmp_path)


# ── The lane's contract ──────────────────────────────────────────────

def test_one_runner_start_yields_one_id_in_all_three_stores(
        tmp_path, monkeypatch):
    """The test this lane exists for: mint once, and `pipeline_run.json`,
    the provenance runs and the perf rows all carry that one value."""
    project = _scratch(tmp_path)

    # What the runner does at start.
    rid = run_identity.mint_run_id()
    assert run_identity.is_unified_run_id(rid)
    monkeypatch.setenv(run_identity.ENV_VAR, rid)

    record = run_control.begin_run_status(
        project, "full run", ["scan"], run_id=rid)
    assert record["run_id"] == rid

    ledger = provenance.ProvenanceLedger(tmp_path)
    ledger.start_run(rid, mode="full run")
    ledger.end_run(rid, "success", ["scan"])

    cap = perf_ledger.begin(tmp_path, "scan", rid, node="scan")
    assert cap is not None
    cap.end()

    # All three stores, one id.
    assert run_control.read_run_status(project)["run_id"] == rid
    assert [r.run_id for r in ledger.runs()] == [rid]
    rows = perf_ledger.read_rows(tmp_path)
    assert rows, "the capability row was never written"
    assert {r["run_id"] for r in rows} == {rid}
    assert os.environ[run_identity.ENV_VAR] == rid


def test_run_history_carries_each_predecessors_run_id(tmp_path):
    """The status file replaces itself every run, so a predecessor's id
    survives only on its history entry - which is what joins a restart
    back to the run it follows."""
    project = _scratch(tmp_path)
    first = run_identity.mint_run_id()
    run_control.begin_run_status(project, "full run", ["scan"], run_id=first)
    run_control.write_run_status(
        project, status="success", finished_at="2026-10-10T10:01:00")

    second = run_identity.mint_run_id()
    record = run_control.begin_run_status(
        project, "full run", ["scan"], run_id=second)

    assert record["run_id"] == second
    assert record["run_history"][-1]["run_id"] == first


def test_begin_run_status_falls_back_to_the_exported_run_id(
        tmp_path, monkeypatch):
    """The runner exports before it begins, so a begin that was not
    handed the id still records the run it belongs to."""
    project = _scratch(tmp_path)
    rid = run_identity.mint_run_id()
    monkeypatch.setenv(run_identity.ENV_VAR, rid)

    record = run_control.begin_run_status(project, "full run", ["scan"])

    assert record["run_id"] == rid


def test_perf_begin_falls_back_to_the_exported_run_id(
        tmp_path, monkeypatch):
    """A front door running under a minted run records that run's id
    without threading it through every call."""
    _scratch(tmp_path)
    rid = run_identity.mint_run_id()
    monkeypatch.setenv(run_identity.ENV_VAR, rid)

    cap = perf_ledger.begin(tmp_path, "scan", "", node="scan")

    assert cap is not None
    assert cap.row["run_id"] == rid
    cap.end()
    assert perf_ledger.read_rows(tmp_path)[-1]["run_id"] == rid


# ── The mint ─────────────────────────────────────────────────────────

def test_minted_ids_are_utc_prefixed_unique_and_ordered():
    """`run-<UTC>-<rand6>`: UTC, never local; random, never pid; the
    timestamp prefix keeps ids sorting chronologically."""
    ids = {run_identity.mint_run_id() for _ in range(200)}
    assert len(ids) == 200, "two mints in one second shared an id"
    assert all(run_identity.is_unified_run_id(i) for i in ids)
    # The timestamp prefix is what orders runs across time.
    assert (run_identity.mint_run_id(now=1000)
            < run_identity.mint_run_id(now=2000))

    pinned = run_identity.mint_run_id(now=0)
    assert pinned.startswith("run-19700101T000000Z-"), pinned


def test_new_provenance_ids_come_from_the_one_scheme():
    """`provenance.new_run_id` is the same mint under the old name, so
    every provenance row converges without touching its readers."""
    assert run_identity.is_unified_run_id(provenance.new_run_id())


# ── Old rows still read ──────────────────────────────────────────────

def test_legacy_provenance_rows_still_read(tmp_path):
    """Runs recorded under `YYYYMMDDTHHMMSS-pid` are still runs: the
    scheme changed for new rows, not for the ones already on disk."""
    _scratch(tmp_path)
    ledger = provenance.ProvenanceLedger(tmp_path)
    ledger.start_run("20260829T123000-1", mode="full run")
    ledger.end_run("20260829T123000-1", "failed", ["mesh_spine"])

    runs = ledger.runs()

    assert [r.run_id for r in runs] == ["20260829T123000-1"]
    assert runs[0].status == "failed"
    assert run_identity.is_legacy_run_id(runs[0].run_id)
    assert run_identity.is_run_id(runs[0].run_id)
    assert not run_identity.is_unified_run_id(runs[0].run_id)
    assert not run_identity.is_run_id("not-a-run-id")


def test_a_status_file_from_before_the_unification_still_begins(
        tmp_path, monkeypatch):
    """No `run_id` on disk and none exported: the next run begins with
    an empty id rather than refusing, and its history says the
    predecessor had none rather than inventing one."""
    monkeypatch.delenv(run_identity.ENV_VAR, raising=False)
    project = _scratch(tmp_path)
    (tmp_path / run_control.RUN_STATUS_FILE).write_text(
        json.dumps({"status": "success", "finished_at": "2026-08-01T10:00:00",
                    "mode": "full run", "steps_to_run": ["scan"]}),
        encoding="utf-8")

    record = run_control.begin_run_status(project, "full run", ["scan"])

    assert record["run_id"] == ""
    assert record["run_history"][-1]["run_id"] == ""


def test_legacy_perf_rows_still_profile(tmp_path):
    """Perf rows keyed by a pre-unification id still attribute to it -
    the key changed shape, not meaning."""
    _scratch(tmp_path)
    cap = perf_ledger.begin(tmp_path, "scan", "20260829T123000-1",
                             node="scan")
    assert cap is not None
    cap.end()

    rows = perf_ledger.read_rows(tmp_path)

    assert perf_ledger.run_ids(rows) == ["20260829T123000-1"]
    assert perf_ledger.profile(rows, "20260829T123000-1")["run_id"] == \
        "20260829T123000-1"
