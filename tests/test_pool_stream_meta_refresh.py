"""A pool item whose cached stream metadata disagrees with its file is
refreshed at the build's safe rebinding point, not reused.

THE INPUT THAT BREAKS THIS: Reel 26, 2026-09-13 - a pool item caching
`866x480`/ProRes for a file that is `904x480`/qtrle on disk. The
artefact was rewritten in place under a stable path (a re-render under
an unchanged drawing digest, then `transcode_in_place`'s ProRes-to-qtrle
carriage), while Resolve caches dimensions and codec at import where
the scripting API cannot refresh them. Reusing that item renders a
ProRes decode of qtrle bytes at the wrong dimensions: media offline on
exactly the frames the clip covers.

The fix reuses `deliver-reel`'s comparison (`pool_stream_meta`, the one
its preflight refuses on) at `import_pool_item`'s lookup hit: a
disagreeing hit is left pooled and a fresh item is imported beside it,
so the staging timeline binds the fresh item and promotion carries it.
Deletion is never the repair - the approved timeline still plays the
stale item, and promotion retires that timeline to Archive rather than
deleting it (`orphan_removal.assert_removable` refuses placed items for
exactly this reason).

A test builds its project under `tmp_path`, or it skips. It never falls
back to a real one. The disk probe is stubbed: no ffmpeg, no Resolve.
"""
from __future__ import annotations

import os
from unittest.mock import patch

from library.tools import pool_stream_meta


# ── The comparison ──────────────────────────────────────────────

def test_agreement_is_no_disagreement():
    """Identical signatures compare clean, codec leg included."""
    pool = {"width": 904, "height": 480, "codec": "qtrle"}
    disk = {"width": 904, "height": 480, "codec": "qtrle"}
    assert pool_stream_meta.stream_disagreement(pool, disk) is None


def test_reel_26_input_fires_the_resolution_leg():
    """Pool `866x480` against disk `904x480` is stale, whatever else."""
    pool = {"width": 866, "height": 480, "codec": None}
    disk = {"width": 904, "height": 480, "codec": "qtrle"}
    found = pool_stream_meta.stream_disagreement(pool, disk)
    assert found is not None
    assert found["mismatches"] == ["resolution"]
    assert found["pool_resolution"] == "866x480"
    assert found["disk_resolution"] == "904x480"
    assert found["codec_compared"] is False


def test_codec_only_rewrite_fires_the_codec_leg():
    """Same dimensions, transcoded underneath: resolution alone waves it
    through, so the codec leg must fire. This is the transcode
    migration with no re-render - `transcode_in_place` keeps the path
    and the pixels and turns only the codec over."""
    pool = {"width": 904, "height": 480, "codec": "prores"}
    disk = {"width": 904, "height": 480, "codec": "qtrle"}
    found = pool_stream_meta.stream_disagreement(pool, disk)
    assert found is not None
    assert found["mismatches"] == ["codec"]


def test_an_unreadable_side_is_skipped_never_flagged():
    """A missing file is not a stale item, and neither is a pool item
    with no parseable Resolution. Both read as incomparable."""
    pool = {"width": 866, "height": 480, "codec": None}
    assert pool_stream_meta.stream_disagreement(
        pool, {"width": None, "height": None, "codec": None}) is None
    assert pool_stream_meta.stream_disagreement(
        {"width": None, "height": None, "codec": None},
        {"width": 904, "height": 480, "codec": "qtrle"}) is None


def test_pool_codec_is_read_by_substring_never_by_assumed_key():
    """No caller in this tree has ever read a codec property, so the
    exact key is unverified against live Resolve and must not be
    assumed. Whatever key carries `codec` in its name answers."""

    class _Item:
        def GetClipProperty(self, key=None):
            if key is None:
                return {"Clip Name": "c", "File Path": "/f",
                        "Resolution": "904x480", "Video Codec": "ProRes 4444"}
            return {"Clip Name": "c", "File Path": "/f",
                    "Resolution": "904x480"}.get(key, "")

    stream = pool_stream_meta.pool_stream(_Item())
    assert (stream["width"], stream["height"]) == (904, 480)
    assert stream["codec"] == "prores 4444"


def test_pool_without_a_codec_key_compares_on_resolution_alone():
    """A pool that exposes no codec key says so in the record rather
    than passing off a half check as a full one."""

    class _Item:
        def GetClipProperty(self, key=None):
            if key is None:
                return {"Type": "Video"}  # no codec key at all
            return "866x480" if key == "Resolution" else ""

    stream = pool_stream_meta.pool_stream(_Item())
    assert (stream["width"], stream["height"]) == (866, 480)
    assert stream["codec"] is None
    found = pool_stream_meta.stream_disagreement(
        stream, {"width": 904, "height": 480, "codec": "qtrle"})
    assert found["mismatches"] == ["resolution"]
    assert found["codec_compared"] is False


def test_disk_stream_of_a_missing_file_is_all_none(tmp_path):
    """The probe degrades to incomparable, honestly - no ffmpeg output
    is mistaken for a measurement."""
    stream = pool_stream_meta.disk_stream(str(tmp_path / "gone.mov"))
    assert stream == {"width": None, "height": None, "codec": None}


# ── The build refresh ───────────────────────────────────────────

class _Clip:
    """A pooled overlay item with readable stream metadata."""

    def __init__(self, path, resolution="904x480", codec=None):
        self._path = path
        self._resolution = resolution
        self._codec = codec

    def GetClipProperty(self, key=None):
        table = {"File Path": self._path,
                 "Clip Name": os.path.basename(self._path),
                 "Resolution": self._resolution}
        if key is None:
            if self._codec is not None:
                table = dict(table, Codec=self._codec)
            return dict(table)
        return table.get(key, "")

    def GetName(self):
        return os.path.basename(self._path)


class _Folder:
    def __init__(self):
        self._clips = []
        self._subs = []

    def GetName(self):
        return "bin"

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _Pool:
    """A pool that imports fresh-metadata items and never deletes."""

    def __init__(self, fresh_resolution="904x480", fresh_codec="qtrle"):
        self.root = _Folder()
        self.deletes = []
        self.imports = []
        self._fresh = (fresh_resolution, fresh_codec)

    def GetRootFolder(self):
        return self.root

    def ImportMedia(self, paths):
        self.imports.append(list(paths))
        items = [_Clip(p, self._fresh[0], self._fresh[1]) for p in paths]
        self.root._clips.extend(items)
        return items

    def DeleteClips(self, items):
        self.deletes.append(list(items))
        return True


def _overlay(tmp_path, name="sub_craig_162243-166986_e135147f.mov"):
    path = str(tmp_path / name)
    with open(path, "wb") as handle:
        handle.write(b"\x00")
    return path


def _disk_as(width, height, codec):
    return patch.object(pool_stream_meta, "disk_stream",
                        return_value={"width": width, "height": height,
                                      "codec": codec})


def test_stale_hit_imports_fresh_and_never_deletes(tmp_path):
    """THE breaking input, end to end at the import: pooled `866x480`
    against a disk stub of `904x480`/qtrle. The build must place the
    fresh item, must not touch DeleteClips, and must leave the stale
    item pooled for the archive that still plays it."""
    from library.tools.reel_build import import_pool_item

    path = _overlay(tmp_path)
    pool = _Pool()
    stale = _Clip(path, resolution="866x480", codec="prores")
    pool.root._clips.append(stale)
    with _disk_as(904, 480, "qtrle"):
        placed = import_pool_item(pool, path)
    assert placed is not stale
    assert pool.imports == [[path]]
    assert pool.deletes == []
    assert stale in pool.root._clips


def test_fresh_hit_reuses_with_no_import(tmp_path):
    """Agreement reuses as before: no second item, no probe-driven
    churn. This is the 2026-09-09 duplication lesson, held."""
    from library.tools.reel_build import import_pool_item

    path = _overlay(tmp_path)
    pool = _Pool()
    item = _Clip(path, resolution="904x480", codec="qtrle")
    pool.root._clips.append(item)
    with _disk_as(904, 480, "qtrle"):
        assert import_pool_item(pool, path) is item
    assert pool.imports == []
    assert pool.deletes == []


def test_second_hit_prefers_the_fresh_item_without_importing(tmp_path):
    """After one refresh the path holds two items. The next build must
    bind the fresh one WITHOUT importing a third - otherwise every
    rebuild grows the pool by the whole overlay set again."""
    from library.tools.reel_build import import_pool_item

    path = _overlay(tmp_path)
    pool = _Pool()
    stale = _Clip(path, resolution="866x480", codec="prores")
    fresh = _Clip(path, resolution="904x480", codec="qtrle")
    pool.root._clips.extend([stale, fresh])
    with _disk_as(904, 480, "qtrle"):
        assert import_pool_item(pool, path) is fresh
    assert pool.imports == []
    assert pool.deletes == []


def test_codec_only_staleness_refreshes(tmp_path):
    """Same dimensions, codec turned over underneath: the resolution
    leg cannot see it, the codec leg must."""
    from library.tools.reel_build import import_pool_item

    path = _overlay(tmp_path)
    pool = _Pool()
    stale = _Clip(path, resolution="904x480", codec="prores")
    pool.root._clips.append(stale)
    with _disk_as(904, 480, "qtrle"):
        placed = import_pool_item(pool, path)
    assert placed is not stale
    assert pool.imports == [[path]]
    assert pool.deletes == []
