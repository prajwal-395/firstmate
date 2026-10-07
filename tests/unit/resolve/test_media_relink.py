"""resolve-axi relink: re-point moved media, verified by re-read.

Covers the broker-eligible relink verb `library.tools.resolve_axi.cmd_relink`
against fakes - no Resolve, no real project (AGENTS.md 8). The live half
is proved in Ren Qualification through the broker's
`resolve.qualification_session` (see tests/qualification/test_media_relink_live.py):
this file proves the discipline - dry-run plans without writing, apply
re-points and verifies by reading File Path back, refusals claim nothing.
"""

import contextlib

import pytest

from library.tools import resolve_axi
from library.tools.resolve_axi import cmd_relink


class _Clip:
    def __init__(self, name, file_path, replace_ok=True):
        self._name = name
        self._file = file_path
        self._replace_ok = replace_ok
        self.replace_seen = []

    def GetName(self):
        return self._name

    def GetClipProperty(self, key=None):
        if key is None:
            return {"Type": "Video", "File Path": self._file}
        if key == "File Path":
            return self._file
        return ""

    def ReplaceClip(self, path):
        self.replace_seen.append(path)
        if not self._replace_ok:
            return False
        self._file = path
        return True


class _Folder:
    def __init__(self, name, clips=None, subs=None):
        self._name = name
        self._clips = list(clips or [])
        self._subs = list(subs or [])

    def GetName(self):
        return self._name

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _Pool:
    def __init__(self, root):
        self._root = root

    def GetRootFolder(self):
        return self._root

    def GetCurrentFolder(self):
        return self._root


class _Project:
    def __init__(self, name, pool):
        self._name = name
        self._pool = pool

    def GetName(self):
        return self._name

    def GetMediaPool(self):
        return self._pool


class _Manager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class _Resolve:
    def __init__(self, project):
        self._project = project

    def GetProjectManager(self):
        return _Manager(self._project)


class _Timeline:
    def __init__(self, name="Reel 29 - salvage"):
        self._name = name

    def GetName(self):
        return self._name


def _ns(**kwargs):
    return type("Args", (), kwargs)()


@pytest.fixture()
def env(monkeypatch, tmp_path):
    old = tmp_path / "old-a.mov"
    new = tmp_path / "new-a.mov"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    clip = _Clip("a.mov", str(old))
    other = _Clip("b.mov", "/footage/b.mov")
    root = _Folder("root", clips=[clip, other])
    project = _Project("Ren Qualification", _Pool(root))
    resolve = _Resolve(project)
    monkeypatch.setattr(resolve_axi, "_connect", lambda: resolve)
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    import library.tools.marker_feedback as feedback
    monkeypatch.setattr(feedback, "current_timeline",
                        lambda resolve=None: (_Timeline(), None))
    return {"old": str(old), "new": str(new), "clip": clip,
            "other": other, "project": project}


def test_dry_run_plans_without_writing(env, capsys):
    assert cmd_relink(_ns(project="Ren Qualification",
                          old_path=env["old"], new_path=env["new"],
                          clip="", timeout=None, apply=False)) == 0
    out = capsys.readouterr().out
    assert "relink_plan" in out
    assert "a.mov" in out
    assert env["clip"].replace_seen == []


def test_apply_repoints_and_verifies_by_reread(env, capsys):
    assert cmd_relink(_ns(project="Ren Qualification",
                          old_path=env["old"], new_path=env["new"],
                          clip="", timeout=5.0, apply=True)) == 0
    out = capsys.readouterr().out
    assert "relinked" in out
    assert "verified" in out
    assert env["clip"].replace_seen == [env["new"]]
    assert env["clip"].GetClipProperty("File Path") == env["new"]


def test_missing_target_refuses_before_resolve(env, capsys, monkeypatch):
    seen = {"connected": False}

    def boom():
        seen["connected"] = True
        raise AssertionError("must not connect")

    monkeypatch.setattr(resolve_axi, "_connect", boom)
    assert cmd_relink(_ns(project="Ren Qualification",
                          old_path=env["old"],
                          new_path="/nope/missing.mov",
                          clip="", timeout=None, apply=True)) == 1
    assert "not on disk" in capsys.readouterr().out
    assert seen["connected"] is False


def test_no_match_reports_scan_count(env, capsys):
    assert cmd_relink(_ns(project="Ren Qualification",
                          old_path="/nope/nowhere.mov",
                          new_path=env["new"],
                          clip="", timeout=None, apply=False)) == 1
    out = capsys.readouterr().out
    assert "nothing to relink" in out
    assert "2 scanned" in out


def test_failed_replace_is_reported(env, capsys):
    env["clip"]._replace_ok = False
    assert cmd_relink(_ns(project="Ren Qualification",
                          old_path=env["old"], new_path=env["new"],
                          clip="", timeout=None, apply=True)) == 1
    out = capsys.readouterr().out
    assert "failed" in out
    assert "ReplaceClip returned false" in out


def test_clip_filter_selects_one(env, capsys, tmp_path):
    second = _Clip("a.mov", env["old"])
    env["project"]._pool._root._clips.append(second)
    assert cmd_relink(_ns(project="Ren Qualification",
                          old_path=env["old"], new_path=env["new"],
                          clip="a.mov", timeout=None, apply=False)) == 0
    out = capsys.readouterr().out
    assert "matched" in out
