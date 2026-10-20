from __future__ import annotations

import pytest

from library.tools.resolved import drt_recovery_probe, jobs


def test_prepare_is_exclusive_qualification_only_and_closed(monkeypatch,
                                                             tmp_path):
    archive = tmp_path / "recovery.drt"
    archive.write_bytes(b"fixture")
    monkeypatch.setattr(
        "library.tools.qualification_project.drt_export_probe_project_folder",
        lambda: tmp_path)

    shape = jobs.prepare(drt_recovery_probe.KIND,
                         {"project": jobs.QUALIFICATION_PROJECT})

    assert shape["mode"] == "exclusive"
    assert shape["qualification"] is True
    assert shape["project"] == jobs.QUALIFICATION_PROJECT
    # qualification_only by design: the probe requires Ren Qualification
    # open and manages its own scratch-project lifecycle with a recorded
    # origin, so it must NOT join the borrow-and-restore set - the
    # worker's non-borrowing open check enforces the entry project.
    assert drt_recovery_probe.KIND not in jobs.BORROWING_QUALIFICATION_KINDS
    assert drt_recovery_probe.KIND in jobs.RUNNING_CANCELLATION_KINDS

    with pytest.raises(jobs.JobRefused, match="unknown field"):
        drt_recovery_probe.prepare({"project": jobs.QUALIFICATION_PROJECT,
                                    "archive_path": str(archive),
                                    "resolve_script": "unsafe"})


def test_archive_path_must_be_inside_qualification_store(monkeypatch, tmp_path):
    store = tmp_path / "qualification"
    store.mkdir()
    archive = tmp_path / "outside.drt"
    archive.write_bytes(b"fixture")
    monkeypatch.setattr(
        "library.tools.qualification_project.drt_export_probe_project_folder",
        lambda: store)

    with pytest.raises(jobs.JobRefused, match="only from"):
        drt_recovery_probe.prepare({"project": jobs.QUALIFICATION_PROJECT,
                                    "archive_path": str(archive)})
