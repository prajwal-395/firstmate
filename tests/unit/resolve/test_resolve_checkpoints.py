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
    def __init__(self, names=(), folders=None):
        self.folders = folders or {"": {"projects": list(names), "folders": {}}}
        self.current = ""
        self.imported = []

    def GetCurrentFolder(self):
        return self.current.rsplit("/", 1)[-1]

    def GotoRootFolder(self):
        self.current = ""
        return True

    def GetFolderListInCurrentFolder(self):
        return list(self.folders[self.current]["folders"])

    def OpenFolder(self, name):
        if name not in self.folders[self.current]["folders"]:
            return False
        self.current = f"{self.current}/{name}".strip("/")
        return True

    def ExportProject(self, name, path):
        with open(path, "wb") as stream:
            stream.write(b"synthetic project")
        return True

    def GetProjectListInCurrentFolder(self):
        return self.folders[self.current]["projects"]

    def ImportProject(self, path, name):
        self.imported.append((path, name))
        self.folders[self.current]["projects"].append(name)
        return True


def nested_manager():
    folders = {
        "": {"projects": ["Other"], "folders": {"Lucie Content": {}}},
        "Lucie Content": {"projects": [], "folders": {"Social Media": {}}},
        "Lucie Content/Social Media": {"projects": [], "folders": {"Podcast": {}}},
        "Lucie Content/Social Media/Podcast": {
            "projects": ["Demo"], "folders": {}}
    }
    return Manager(folders=folders)


class Resolve:
    EXPORT_DRT = "DRT"


def test_project_checkpoint_is_idempotent_and_hashes_only_verified_export(tmp_path):
    manager, project = Manager(["Demo"]), Project()
    first = checkpoints.project_checkpoint(manager, project, str(tmp_path), "run-1")
    second = checkpoints.project_checkpoint(manager, project, str(tmp_path), "run-1")

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    with open(first["path"], "rb") as stream:
        data = stream.read()
    assert first["sha256"] == hashlib.sha256(data).hexdigest()
    ledger = json.loads((tmp_path / "pipeline_output/review/resolve_checkpoints.json").read_text())
    assert len(ledger["checkpoints"]) == 1


def test_project_checkpoint_finds_nested_folder_and_restores_manager_folder(tmp_path):
    manager, project = nested_manager(), Project()
    manager.current = "Lucie Content/Social Media/Podcast"
    seen = []
    original = manager.ExportProject

    def export(name, path):
        seen.append(manager.current)
        return original(name, path)

    manager.ExportProject = export
    checkpoints.project_checkpoint(manager, project, str(tmp_path), "nested")

    assert seen == ["Lucie Content/Social Media/Podcast"]
    assert manager.current == "Lucie Content/Social Media/Podcast"


def test_restore_imports_into_source_folder_and_restores_manager_folder(tmp_path):
    manager, project = nested_manager(), Project()
    checkpoints.project_checkpoint(manager, project, str(tmp_path), "nested")
    manager.current = "Lucie Content/Social Media/Podcast"
    restored = checkpoints.restore_project(manager, str(tmp_path), "nested", "Demo copy")

    assert restored["restored"] is True
    assert manager.imported[0][1] == "Demo copy"
    assert manager.folders["Lucie Content/Social Media/Podcast"]["projects"] == ["Demo", "Demo copy"]
    assert manager.current == "Lucie Content/Social Media/Podcast"


def test_project_checkpoint_refuses_duplicate_name_across_folders(tmp_path):
    manager = nested_manager()
    manager.folders["Other folder"] = {"projects": ["Demo"], "folders": {}}
    manager.folders[""]["folders"]["Other folder"] = {}

    with pytest.raises(RuntimeError, match="ambiguous lookup"):
        checkpoints.project_checkpoint(manager, Project(), str(tmp_path), "ambiguous")
    assert manager.GetCurrentFolder() == ""


@pytest.mark.parametrize("result, bytes_written", [(False, b"data"), (True, b"")])
def test_project_checkpoint_refuses_failed_or_empty_export(tmp_path, monkeypatch,
                                                          result, bytes_written):
    manager, project = Manager(["Demo"]), Project()

    def export(_name, path):
        with open(path, "wb") as stream:
            stream.write(bytes_written)
        return result

    monkeypatch.setattr(manager, "ExportProject", export)
    with pytest.raises(RuntimeError):
        checkpoints.project_checkpoint(manager, project, str(tmp_path), "run-1")
    assert not (tmp_path / "pipeline_output/review/resolve_checkpoints.json").exists()


def test_restore_refuses_existing_project_name_without_import(tmp_path):
    manager, project = Manager(["Demo", "Taken"]), Project()
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


def test_timeline_checkpoint_export_loss_does_not_claim_a_checkpoint(
        tmp_path, monkeypatch):
    """A Resolve disconnect during export leaves an explicit partial state.

    The duplicated timeline is evidence of partial completion. Ren must
    refuse a retry that could create another duplicate, and the ledger must
    never claim that the missing archive is recoverable.
    """
    from library.tools.versions import store

    project = Project()
    source = Timeline("Cut", project)
    project.timelines.append(source)

    def resolve_disappears(*_args, **_kwargs):
        return {"archived": False, "reason": "Resolve connection disappeared"}

    monkeypatch.setattr(store, "export_timeline_recovery_archive",
                        resolve_disappears)
    with pytest.raises(RuntimeError, match="Resolve connection disappeared"):
        checkpoints.timeline_checkpoint(
            project, source, str(tmp_path), "disconnect-during-export", Resolve())

    ledger = tmp_path / "pipeline_output/review/resolve_checkpoints.json"
    assert not ledger.exists()
    assert project.GetTimelineCount() == 2
    with pytest.raises(FileExistsError, match="without a ledger entry"):
        checkpoints.timeline_checkpoint(
            project, source, str(tmp_path), "disconnect-during-export", Resolve())
