"""A pool item whose cached stream metadata disagrees with its file is
refreshed at the build's safe rebinding point, not reused.

History: docs/evidence/resolve_test_history.md#test_pool_stream_meta_refresh.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from unittest.mock import patch

import pytest

from library.tools import pool_stream_meta


# ── The comparison ──────────────────────────────────────────────

def test_stream_disagreement_fires_each_leg_and_never_on_ignorance():
    """Reel 26: pool `866x480` against disk `904x480` is stale, whatever
    else. Same dimensions transcoded underneath (`transcode_in_place`
    keeps the path and pixels, turns only the codec over) fires the
    codec leg. A missing file or an unparseable pool Resolution is
    incomparable, never stale; and a codec name the table has not
    learned is NOT staleness - flagging it would read every camera
    format in the pool as stale (AGENTS.md 10.4)."""
    found = pool_stream_meta.stream_disagreement(
        {"width": 866, "height": 480, "codec": None},
        {"width": 904, "height": 480, "codec": "qtrle"})
    assert found["mismatches"] == ["resolution"]
    assert found["pool_resolution"] == "866x480"
    assert found["disk_resolution"] == "904x480"
    assert found["codec_compared"] is False

    found = pool_stream_meta.stream_disagreement(
        {"width": 904, "height": 480, "codec": "apple prores 4444"},
        {"width": 904, "height": 480, "codec": "qtrle"})
    assert found["mismatches"] == ["codec"]
    assert found["codec_compared"] is True

    unknown = {"width": None, "height": None, "codec": None}
    assert pool_stream_meta.stream_disagreement(
        {"width": 866, "height": 480, "codec": None}, unknown) is None
    assert pool_stream_meta.stream_disagreement(
        unknown, {"width": 904, "height": 480, "codec": "qtrle"}) is None

    assert pool_stream_meta.codecs_agree("xavc high l5.1", "h264") is None
    pool = {"width": 3840, "height": 2160, "codec": "xavc high l5.1"}
    disk = {"width": 3840, "height": 2160, "codec": "h264"}
    assert pool_stream_meta.stream_disagreement(pool, disk) is None
    found = pool_stream_meta.stream_disagreement(
        dict(pool, width=1920, height=1080), disk)
    assert found["mismatches"] == ["resolution"]
    assert found["codec_compared"] is False


def test_pool_codec_is_the_video_one_never_the_first_codec_key():
    """THE INPUT THAT BROKE THIS: Resolve 21.1's property dict, in its
    real order. Three keys match `codec` and `Audio Codec` comes
    first, so a plain substring match returned `Linear PCM` for every
    overlay artefact in the project on 2026-09-13 and compared an
    audio codec against a video one. `Codec Bitrate` is not a codec
    either."""

    class _Item:
        def GetClipProperty(self, key=None):
            if key is None:
                return {"Audio Codec": "Linear PCM", "Camera Format": "",
                        "Codec Bitrate": "", "Format": "QuickTime",
                        "Resolution": "904x480",
                        "Video Codec": "Apple ProRes 4444"}
            return {"Resolution": "904x480"}.get(key, "")

    stream = pool_stream_meta.pool_stream(_Item())
    assert (stream["width"], stream["height"]) == (904, 480)
    assert stream["codec"] == "apple prores 4444"


def test_disk_stream_reads_a_real_file_by_name_not_by_position(tmp_path):
    """THE DEFECT THIS CLOSES, against a real ffprobe.

    `-show_entries` selects fields and does NOT order them: ffprobe
    prints its own stream order, so `stream=width,height,codec_name`
    emits `qtrle,904,480`. The positional parse landed in #1089 read
    `qtrle` as the width, raised `ValueError`, and returned all-None -
    so every comparison read `comparable: False` and the staleness
    check could not fire while 13 stale items sat in the pool.

    A synthesised qtrle file at a NON-SQUARE size, so a transposed
    width and height cannot pass either."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("needs ffmpeg and ffprobe - CI installs both "
                    "(AGENTS.md 9, 'What CI actually checks')")
    made = tmp_path / "overlay.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=black:s=904x480:d=0.2:r=30",
         "-c:v", "qtrle", str(made)],
        check=True, capture_output=True, encoding="utf-8")
    assert pool_stream_meta.disk_stream(str(made)) == {
        "width": 904, "height": 480, "codec": "qtrle"}


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
                table = dict(table, **{"Audio Codec": "Linear PCM",
                                       "Video Codec": self._codec})
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
    stale = _Clip(path, resolution="866x480", codec="Apple ProRes 4444")
    pool.root._clips.append(stale)
    with _disk_as(904, 480, "qtrle"):
        placed = import_pool_item(pool, path)
    assert placed is not stale
    assert pool.imports == [[path]]
    assert pool.deletes == []
    assert stale in pool.root._clips


def test_a_fresh_hit_reuses_without_importing(tmp_path):
    """Agreement reuses as before: no second item, no probe-driven
    churn. This is the 2026-09-09 duplication lesson, held."""
    from library.tools.reel_build import import_pool_item

    path = _overlay(tmp_path)
    pool = _Pool()
    item = _Clip(path, resolution="904x480", codec="Animation")
    pool.root._clips.append(item)
    with _disk_as(904, 480, "qtrle"):
        assert import_pool_item(pool, path) is item
    assert pool.imports == []
    assert pool.deletes == []

    # After one refresh the path holds two items: the next build binds
    # the fresh one WITHOUT importing a third - otherwise every rebuild
    # grows the pool by the whole overlay set again.
    pool = _Pool()
    stale = _Clip(path, resolution="866x480", codec="Apple ProRes 4444")
    fresh = _Clip(path, resolution="904x480", codec="Animation")
    pool.root._clips.extend([stale, fresh])
    with _disk_as(904, 480, "qtrle"):
        assert import_pool_item(pool, path) is fresh
    assert pool.imports == []
    assert pool.deletes == []
