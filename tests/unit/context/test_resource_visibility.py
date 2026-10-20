"""Defect named: a project's disk and analysis cost had no per-project view.

`ren storage status` reported the machine: the registered artifacts of
every project merged into one by-kind table, the legacy entries as one
machine-wide summary, and nothing at all about what a project's ANALYSIS
consumed. The performance ledger held the answer - the wall, the CPU,
the peak memory and the bytes each capability wrote - and no reader
aggregated it.

A captain asking "what is this project costing me" got a machine total
and had to subtract the other projects by hand. Worse, a measurement
that was never taken read as a zero, and a zero reads as "nothing was
written", which is a different fact.

This file names the gap and pins the report: each project's own bytes,
its own legacy entries, and its own analysis consumption, with an
absent measurement reported as absent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from library.tools import artifact_lifecycle, storage_lifecycle


def _project(tmp_path: Path, monkeypatch, slug: str = "show") -> Path:
    """A project under the registry root, with a scratch directory."""
    root = tmp_path / slug
    (root / "pipeline_output" / "scratch").mkdir(parents=True)
    (root / "project.yaml").write_text(
        f'name: "Show"\nslug: {slug}\nproject_format_version: 1\n',
        encoding="utf-8")
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    return root


def _register(project: Path, name: str, *, size: int = 100) -> Path:
    target = project / "pipeline_output" / "scratch" / name
    target.write_bytes(b"x" * size)
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id=f"artifact-{name}", kind="generated_render",
        project_id=project.name, run_id="run", job_id="job",
        path=str(target), created_at=datetime.now(UTC).isoformat(),
        identity=artifact_lifecycle.FileIdentity.capture(target),
        retention_class="temporary", regenerable=True,
        regeneration_method="rebuild")
    artifact_lifecycle.register_artifact(str(project), record)
    artifact_lifecycle.transition_artifact(
        str(project), record.artifact_id, artifact_lifecycle.COMPLETED,
        reason="fixture completed")
    artifact_lifecycle.transition_artifact(
        str(project), record.artifact_id, artifact_lifecycle.DISPOSABLE,
        reason="fixture is disposable")
    return target


def _ledger(project: Path, rows: list[dict]) -> None:
    logs = project / "pipeline_output" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "perf_ledger.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_each_project_reports_its_own_registered_bytes(tmp_path, monkeypatch):
    """Named defect: every project merged into one machine total."""
    first = _project(tmp_path, monkeypatch, "one")
    second = _project(tmp_path, monkeypatch, "two")
    _register(first, "a.bin", size=100)
    _register(second, "b.bin", size=250)

    report = storage_lifecycle.status([str(first), str(second)])

    by_project = report["by_project"]
    assert set(by_project) == {str(first.resolve()), str(second.resolve())}
    assert by_project[str(first.resolve())]["logical_bytes"] == 100
    assert by_project[str(second.resolve())]["logical_bytes"] == 250
    assert by_project[str(first.resolve())]["artifacts"] == 1
    assert by_project[str(second.resolve())]["artifacts"] == 1
    # The machine total is still the sum of the projects.
    assert report["logical_bytes"] == 350


def test_a_projects_legacy_entries_are_charged_to_that_project(tmp_path,
                                                               monkeypatch):
    """The unregistered files are most of the disk, so they must be split."""
    first = _project(tmp_path, monkeypatch, "one")
    second = _project(tmp_path, monkeypatch, "two")
    (first / "pipeline_output" / "scratch" / "debris.bin").write_bytes(b"y" * 64)
    (second / "pipeline_output" / "scratch" / "debris.bin").write_bytes(b"y" * 32)

    report = storage_lifecycle.status([str(first), str(second)])

    by_project = report["by_project"]
    assert by_project[str(first.resolve())]["legacy"]["logical_bytes"] >= 64
    assert by_project[str(second.resolve())]["legacy"]["logical_bytes"] >= 32
    assert (by_project[str(first.resolve())]["legacy"]["logical_bytes"]
            > by_project[str(second.resolve())]["legacy"]["logical_bytes"])


def test_a_projects_analysis_consumption_is_reported(tmp_path, monkeypatch):
    """The ledger held the answer and nothing read it back."""
    project = _project(tmp_path, monkeypatch)
    _ledger(project, [
        {"kind": "capability", "capability": "semantic_analysis",
         "wall_s": 1.5, "cpu_s": 1.2, "self_peak_rss_mb": 800.0,
         "bytes_written": 4096},
        {"kind": "capability", "capability": "semantic_analysis",
         "wall_s": 0.5, "cpu_s": 0.4, "self_peak_rss_mb": 900.0},
        {"kind": "span", "layer": "reel_lifecycle", "phase": "model_access",
         "wall_s": 2.0, "cpu_s": 1.6, "bytes_written": 2048,
         "bytes_read": 1024},
    ])

    report = storage_lifecycle.status([str(project)])

    analysis = report["by_project"][str(project.resolve())]["analysis"]
    assert analysis["ledger_present"] is True
    totals = analysis["totals"]
    assert totals["wall_s"] == 2.0
    assert totals["cpu_s"] == 1.6
    assert totals["peak_rss_mb"] == 900.0
    assert totals["capability_bytes_written"] == 4096
    assert totals["capability_bytes_measured"] is True
    assert analysis["by_capability"]["semantic_analysis"]["calls"] == 2
    assert analysis["by_phase"]["model_access"]["bytes_read"] == 1024


def test_a_measurement_that_was_never_taken_is_reported_as_absent(tmp_path,
                                                                   monkeypatch):
    """A zero reads as "nothing was written", and that is a different fact."""
    project = _project(tmp_path, monkeypatch)
    _ledger(project, [
        {"kind": "capability", "capability": "semantic_analysis",
         "wall_s": 1.5, "cpu_s": 1.2, "self_peak_rss_mb": 800.0},
    ])

    totals = storage_lifecycle.analysis_resources(str(project))["totals"]

    assert totals["capability_bytes_measured"] is False
    assert totals["phase_bytes_measured"] is False


def test_a_project_with_no_ledger_says_so(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)

    analysis = storage_lifecycle.analysis_resources(str(project))

    assert analysis["ledger_present"] is False
    assert analysis["rows"] == 0
    assert analysis["totals"]["capability_bytes_measured"] is False
