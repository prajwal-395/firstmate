import hashlib
import json

import pytest

from library.tools.versions import checkpoints


class Project:
    def GetName(self):
        return "Demo"

    def __init__(self):
        self.timelines = []

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]


class Timeline:
    def __init__(self, name, project):
        self.name, self.project = name, project

    def GetName(self):
        return self.name

    def DuplicateTimeline(self, name):
        duplicate = Timeline(name, self.project)
        self.project.timelines.append(duplicate)
        return duplicate

    def Export(self, path, flag):
        assert flag == "DRT"
        with open(path, "wb") as stream:
            stream.write(b"synthetic timeline")
        return True


class Manager:
    def __init__(self, names=()):
        self.names = list(names)
        self.imported = []

    def ExportProject(self, name, path):
        with open(path, "wb") as stream:
            stream.write(b"synthetic project")
        return True

    def GetProjectListInCurrentFolder(self):
        return self.names

    def ImportProject(self, path, name):
        self.imported.append((path, name))
        self.names.append(name)
        return True


class Resolve:
    EXPORT_DRT = "DRT"


def test_project_checkpoint_is_idempotent_and_hashes_only_verified_export(tmp_path):
    manager, project = Manager(), Project()
    first = checkpoints.project_checkpoint(manager, project, str(tmp_path), "run-1")
    second = checkpoints.project_checkpoint(manager, project, str(tmp_path), "run-1")

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    data = open(first["path"], "rb").read()
    assert first["sha256"] == hashlib.sha256(data).hexdigest()
    ledger = json.loads((tmp_path / "pipeline_output/review/resolve_checkpoints.json").read_text())
    assert len(ledger["checkpoints"]) == 1


@pytest.mark.parametrize("result, bytes_written", [(False, b"data"), (True, b"")])
def test_project_checkpoint_refuses_failed_or_empty_export(tmp_path, monkeypatch,
                                                          result, bytes_written):
    manager, project = Manager(), Project()

    def export(_name, path):
        with open(path, "wb") as stream:
            stream.write(bytes_written)
        return result

    monkeypatch.setattr(manager, "ExportProject", export)
    with pytest.raises(RuntimeError):
        checkpoints.project_checkpoint(manager, project, str(tmp_path), "run-1")
    assert not (tmp_path / "pipeline_output/review/resolve_checkpoints.json").exists()


def test_restore_refuses_existing_project_name_without_import(tmp_path):
    manager, project = Manager(["Taken"]), Project()
    entry = checkpoints.project_checkpoint(manager, project, str(tmp_path), "run-1")
    with pytest.raises(FileExistsError):
        checkpoints.restore_project(manager, str(tmp_path), "run-1", "Taken")
    assert manager.imported == []
    assert entry["path"]


def test_restore_imports_only_to_new_name(tmp_path):
    manager, project = Manager(["Demo"]), Project()
    checkpoints.project_checkpoint(manager, project, str(tmp_path), "run-1")
    restored = checkpoints.restore_project(manager, str(tmp_path), "run-1", "Demo copy")
    assert restored["project"] == "Demo copy"
    assert manager.imported[0][1] == "Demo copy"


def test_timeline_checkpoint_name_is_stable_and_idempotent(tmp_path, monkeypatch):
    from library.tools.versions import store

    def archive(_resolve, _timeline, timeline_name, project_folder, **_kwargs):
        path = (tmp_path / "pipeline_output/review/recovery_archives"
                / f"{timeline_name}.drt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic timeline")
        return {"archived": True, "path": str(path)}

    monkeypatch.setattr(store, "export_timeline_recovery_archive", archive)
    project = Project()
    source = Timeline("Cut", project)
    project.timelines.append(source)
    first = checkpoints.timeline_checkpoint(project, source, str(tmp_path), "run-1", Resolve())
    second = checkpoints.timeline_checkpoint(project, source, str(tmp_path), "run-1", Resolve())

    assert first["timeline"] == second["timeline"]
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert project.GetTimelineCount() == 2
