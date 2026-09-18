"""A build files where it imports; the organiser has nothing to repair.

The captain's field-test project held timelines in motion-graphics bins
and caption clips beside them, plus duplicate pool entries for the same
file - because `CreateEmptyTimeline` and `ImportMedia` land in whatever
bin is CURRENT, and because re-importing a path already pooled makes a
second item rather than returning the existing one. The build now
decides every destination up front through `resolve_bin_layout` (the
one owner of bin paths) and looks every path up before importing it, so
a timeline lands in the reels bin and a subtitle clip in the subtitles
bin because the code cannot put them anywhere else - not because a
cleanup pass moved them afterwards.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from library.tools import resolve_bin_layout as bins
from library.tools import resolve_organization as org
from library.tools.project_layout import AREAS, Area


class _FakeClip:
    def __init__(self, file_path, name="clip", uid="uid"):
        self._path = file_path
        self._name = name
        self._uid = uid

    def GetClipProperty(self, key):
        if key == "File Path":
            return self._path
        return ""

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid


class _FakeFolder:
    def __init__(self, name):
        self._name = name
        self._subs = []
        self._clips = []

    def GetName(self):
        return self._name

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _FakePool:
    """A pool that files like Resolve: imports land in CURRENT."""

    def __init__(self):
        self.root = _FakeFolder("Master")
        self._current = self.root
        self.imports = []
        self.created_bins = []
        self.timelines = []
        self._uid = 0

    def GetRootFolder(self):
        return self.root

    def GetCurrentFolder(self):
        return self._current

    def SetCurrentFolder(self, folder):
        self._current = folder
        return True

    def AddSubFolder(self, parent, name):
        folder = _FakeFolder(name)
        parent._subs.append(folder)
        self.created_bins.append(name)
        return folder

    def ImportMedia(self, paths):
        self.imports.append((self._current.GetName(), list(paths)))
        items = []
        for path in paths:
            self._uid += 1
            item = _FakeClip(path, name=os.path.basename(path),
                             uid=f"uid-{self._uid}")
            self._current._clips.append(item)
            items.append(item)
        return items

    def CreateEmptyTimeline(self, name):
        self.timelines.append((self._current.GetName(), name))
        return _FakeClip("", name=name, uid=f"timeline-{name}")


def _area_path(root, area, *names):
    return os.path.join(str(root), AREAS[area].relpath, *names)


def test_a_rebuild_imports_nothing_for_a_path_already_in_the_pool():
    """The done-check's first half: count pool items for a known path
    before and after. A second build of the same reel adds no second
    entry, because the lookup runs before the import."""
    from library.tools.reel_build import import_pool_item

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _area_path(tmp, Area.SUBTITLE_SEGMENTS, "sub_a.mov")
        pool = _FakePool()
        first = import_pool_item(pool, path, str(tmp))
        assert first is not None
        before = len(pool.imports)
        second = import_pool_item(pool, path, str(tmp))
        assert second is first
        assert pool.imports == pool.imports[:before]
        assert len(pool.imports) == before == 1


def test_a_new_subtitle_import_lands_in_the_subtitles_bin():
    """The destination is decided by the file, never inherited from
    the current folder - even when current is a motion-graphics bin."""
    from library.tools.reel_build import import_pool_item

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pool = _FakePool()
        mg = _FakeFolder(bins.MOTION_GRAPHICS_BIN)
        pool.root._subs.append(mg)
        pool.SetCurrentFolder(mg)
        path = _area_path(tmp, Area.SUBTITLE_SEGMENTS, "sub_a.mov")
        found = import_pool_item(pool, path, str(tmp))
        assert found is not None
        folder, _ = pool.imports[0]
        assert folder == bins.SUBTITLES_BIN
        assert pool.GetCurrentFolder() is mg


def test_a_motion_graphics_import_lands_in_the_motion_graphics_bin():
    from library.tools.reel_build import import_pool_item

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pool = _FakePool()
        path = _area_path(
            tmp, Area.MOTION_GRAPHICS_SEGMENTS, "motion_graphics", "mg.mov")
        found = import_pool_item(pool, path, str(tmp))
        assert found is not None
        folder, _ = pool.imports[0]
        assert folder == bins.MOTION_GRAPHICS_BIN


def test_an_import_never_forks_a_bin_that_already_exists():
    """`AddSubFolder` makes a second same-named bin rather than
    refusing, so the build looks the name up first - one import, no
    new bin."""
    from library.tools.reel_build import import_pool_item

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pool = _FakePool()
        pool.root._subs.append(_FakeFolder(bins.SUBTITLES_BIN))
        path = _area_path(tmp, Area.SUBTITLE_SEGMENTS, "sub_a.mov")
        import_pool_item(pool, path, str(tmp))
        assert pool.created_bins == []


def test_a_pooled_frame_sequence_is_reused_not_reimported():
    """A sequence reports one bracketed File Path, so no single frame
    path matches it - the old first-frame lookup missed every time and
    each rebuild imported the captions beside themselves. The lookup
    is by directory now."""
    from library.tools.reel_build import import_pool_sequence

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        frame_dir = os.path.join(
            str(tmp), AREAS[Area.SUBTITLE_SEGMENTS].relpath, "seg_00")
        pool = _FakePool()
        sub = _FakeFolder(bins.SUBTITLES_BIN)
        pool.root._subs.append(sub)
        sub._clips.append(
            _FakeClip(os.path.join(frame_dir, "frame-[000-059].png")))
        frames = [os.path.join(frame_dir, f"frame-{i:03d}.png")
                  for i in range(60)]
        found = import_pool_sequence(pool, frames, frame_dir, str(tmp))
        assert found is not None
        assert pool.imports == []


def test_a_new_frame_sequence_imports_once_into_the_subtitles_bin():
    from library.tools.reel_build import import_pool_sequence

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        frame_dir = os.path.join(
            str(tmp), AREAS[Area.SUBTITLE_SEGMENTS].relpath, "seg_00")
        pool = _FakePool()
        frames = [os.path.join(frame_dir, f"frame-{i:03d}.png")
                  for i in range(60)]
        found = import_pool_sequence(pool, frames, frame_dir, str(tmp))
        assert found is not None
        assert len(pool.imports) == 1
        folder, arrived = pool.imports[0]
        assert folder == bins.SUBTITLES_BIN
        assert arrived == frames


def test_a_reel_timeline_is_created_in_the_reels_bin():
    """`CreateEmptyTimeline` inherits the current folder, so the build
    sets it first - a reel created while a motion-graphics bin is
    current still lands in the reels bin, and current is restored."""
    from library.tools.reel_build import create_reel_timeline

    pool = _FakePool()
    mg = _FakeFolder(bins.MOTION_GRAPHICS_BIN)
    pool.root._subs.append(mg)
    pool.SetCurrentFolder(mg)
    create_reel_timeline(pool, "Reel 09 - slug")
    assert pool.timelines == [(bins.REELS_BIN, "Reel 09 - slug")]
    assert pool.GetCurrentFolder() is mg


def test_the_import_bin_is_the_bin_the_organiser_keeps():
    """Import-time and organise-time read the same fact through the
    same function, so filing afterwards moves nothing the build
    placed: the code cannot put them anywhere else, and the cleanup
    pass proves it by finding no moves."""
    from library.tools.reel_build import import_dest_bin

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for area, leaf in ((Area.SUBTITLE_SEGMENTS, bins.SUBTITLES_BIN),
                           (Area.MOTION_GRAPHICS_SEGMENTS,
                            bins.MOTION_GRAPHICS_BIN)):
            path = _area_path(tmp, area, "x.mov")
            assert import_dest_bin(path, str(tmp)) == (leaf,)
            artefacts = [
                org.Artefact(item_id="t-master", name="master", kind="timeline",
                             file_path="", placed_by=(),
                             folder_path=(bins.REELS_BIN,)),
                org.Artefact(item_id="t-reel", name="Reel 01 - live",
                             kind="timeline", file_path="", placed_by=(),
                             folder_path=(bins.REELS_BIN,
                                          bins.REEL_STATE_BINS[org.CURRENT])),
                org.Artefact(item_id="c-x", name="x.mov", kind="clip",
                             file_path=path,
                             placed_by=("Reel 01 - live",),
                             folder_path=import_dest_bin(path, str(tmp))
                             + ("Reel 01 - live",)),
            ]
            plan = org.plan_organization(
                artefacts=artefacts, project_root=str(tmp),
                master_timeline_name="master",
                current_reels=["Reel 01 - live"], archived_plan_names=[])
            assert plan.moves == []


def test_no_reel_build_call_site_invents_a_bin_name():
    """The one-owner rule extended to the reel builder: every bin path
    it touches comes from a `resolve_bin_layout` attribute, never a
    string literal - the same discipline `test_bins_one_owner` holds
    `resolve_build_timeline` to."""
    source = Path(
        "library/tools/reel_build.py").read_text(encoding="utf-8")
    names = [bins.MASTER_BIN, bins.REELS_BIN, bins.REELS_ARCHIVE_BIN,
             bins.REELS_PROOF_BIN, bins.SUBTITLES_BIN,
             bins.MOTION_GRAPHICS_BIN, bins.SOURCE_BIN, bins.UNPLACED_BIN,
             *bins.REEL_STATE_BINS.values(), "02 - Music", "03 - Assets",
             "08 - Exports"]
    pattern = "|".join(re.escape(f'"{n}"') for n in names) + "|" + "|".join(
        re.escape(f"'{n}'") for n in names)
    match = re.search(pattern, source)
    assert match is None, (
        f"reel_build invents a bin {match.group(0)!r} instead of asking "
        f"resolve_bin_layout")
