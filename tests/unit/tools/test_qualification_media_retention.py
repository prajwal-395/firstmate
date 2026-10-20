"""Defect named: the qualification media store grew without bound.

`library/tools/qualification_project.py` copies a source video into the
machine's qualification media store, content-addressed, once per
distinct source. Nothing ever collected one: the media protection in
`library/tools/workspace_gc.py` keeps those files out of every other
rule, and the store is outside the project purge entirely. Every
qualification run against a new source left a whole video behind, for
ever.

The same is true of the scratch project's recovery export. Every
`Ren Scratch - ` project retired by `library/tools/resolve_scratch_projects.py`
left a `.drp` in the machine home, and nothing bounded them either.

A copy is freed by its OWN rule: the run that points at it decides. A
pinned run is a promise that the reproduction stays available, so the
pinned run and the media it points at are both retained.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import workspace_gc
from library.tools.qualification_project import recipe_key


def _media_home(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """The machine home and the qualification media store under it."""
    home = tmp_path / "vep"
    home.mkdir()
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(home))
    media_root = home / "qualification"
    monkeypatch.setenv("REN_QUALIFICATION_MEDIA", str(media_root))
    from library.tools.qualification_project import media_dir

    return home, media_dir()


def _copy(media_dir: Path, name: str) -> Path:
    target = media_dir / "real_footage" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"a whole source video")
    return target


def _run(media_dir: Path, stamp: str, *, copy: Path | None = None,
         pinned: bool = False, finished: bool = True) -> Path:
    """One workflow run directory, with the report a run really writes."""
    root = media_dir / "workflow-runs" / stamp
    root.mkdir(parents=True)
    report = {"schema": "ren-workflow-qualification.v1",
              "started_at": "2026-10-20T00:00:00+00:00", "passed": True}
    if finished:
        report["finished_at"] = "2026-10-20T00:10:00+00:00"
    if copy is not None:
        report["source_selection"] = {"qualification_copy": str(copy)}
    (root / "qualification-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    events = [{"type": "qualification_run_lifecycle",
               "fields": {"state": "completed", "pinned": pinned,
                          "run_id": stamp}}]
    log = root / "pipeline_output" / "logs"
    log.mkdir(parents=True)
    (log / "event_log.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8")
    return root


def test_a_qualification_copy_no_run_references_is_collectable(tmp_path,
                                                                monkeypatch):
    """Named defect: the media store had no bound at all."""
    home, media_dir = _media_home(tmp_path, monkeypatch)
    orphan = _copy(media_dir, "source-abc123def456.mp4")
    _run(media_dir, "20261020T000000000000Z", copy=None)

    candidates = workspace_gc._qualification_media_candidates(
        home, [media_dir / "workflow-runs" / "20261020T000000000000Z"])

    assert [Path(entry.path) for entry in candidates] == [orphan.resolve()]
    assert "no retained run references" in candidates[0].reason


def test_a_qualification_copy_a_retained_run_points_at_is_kept(tmp_path,
                                                               monkeypatch):
    home, media_dir = _media_home(tmp_path, monkeypatch)
    referenced = _copy(media_dir, "source-abc123def456.mp4")
    other = _copy(media_dir, "source-999999999999.mp4")
    run = _run(media_dir, "20261020T000000000000Z", copy=referenced)

    candidates = workspace_gc._qualification_media_candidates(home, [run])

    assert [Path(entry.path) for entry in candidates] == [other.resolve()]


def test_a_copy_a_pinned_run_points_at_is_kept(tmp_path, monkeypatch):
    """A pin is a promise that the reproduction stays available."""
    home, media_dir = _media_home(tmp_path, monkeypatch)
    referenced = _copy(media_dir, "source-abc123def456.mp4")
    pinned = _run(media_dir, "20261020T000000000000Z", copy=referenced,
                  pinned=True)

    assert workspace_gc._pinned_runs([pinned]) == [pinned]
    candidates = workspace_gc._qualification_media_candidates(home, [pinned])
    assert candidates == []


def test_the_media_store_outside_the_machine_home_is_refused(tmp_path,
                                                             monkeypatch):
    """A store outside the home is a different machine's problem."""
    home, media_dir = _media_home(tmp_path, monkeypatch)
    _copy(media_dir, "source-abc123def456.mp4")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("REN_QUALIFICATION_MEDIA", str(elsewhere))
    (elsewhere / recipe_key() / "real_footage").mkdir(parents=True)

    with pytest.raises(workspace_gc.WorkspaceGcRefused,
                       match="outside Ren's data home"):
        workspace_gc._qualification_media_candidates(home, [])


def test_the_scratch_project_export_is_bounded(tmp_path, monkeypatch):
    """Named defect: every retired scratch project left a .drp for ever."""
    from library.tools import resolve_scratch_projects

    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path))
    from library.tools.shared_environment import vep_home

    root = vep_home() / "resolve-scratch-backups"
    root.mkdir(parents=True)
    stamps = [f"2026102{i}T00000000000{i}Z" for i in range(8)]
    written = []
    for stamp in stamps:
        backup = root / f"Ren_Scratch_one_{stamp}.drp"
        backup.write_bytes(b"export")
        backup.with_suffix(".json").write_text("{}", encoding="utf-8")
        written.append(backup)

    pruned = resolve_scratch_projects._prune_scratch_backups(
        "Ren_Scratch_one", keep=3)

    remaining = sorted(path.name for path in root.glob("*.drp"))
    assert len(remaining) == 3
    assert remaining == sorted(path.name for path in written[-3:])
    assert len(pruned) == 10  # five exports and their five receipts


def test_the_scratch_project_export_pruner_leaves_a_foreign_file_alone(
        tmp_path, monkeypatch):
    """Only the shape this module writes is ever named."""
    from library.tools import resolve_scratch_projects

    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path))
    from library.tools.shared_environment import vep_home

    root = vep_home() / "resolve-scratch-backups"
    root.mkdir(parents=True)
    foreign = root / "Ren_Scratch_one_hand-placed.drp"
    foreign.write_bytes(b"not ours")
    ours = root / "Ren_Scratch_one_20261020T000000000000Z.drp"
    ours.write_bytes(b"ours")
    ours.with_suffix(".json").write_text("{}", encoding="utf-8")

    resolve_scratch_projects._prune_scratch_backups("Ren_Scratch_one", keep=1)

    assert foreign.read_bytes() == b"not ours"
    assert ours.exists()
