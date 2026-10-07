"""Static overlay videos freeze into stills; animated ones are left alone."""

import shutil
import subprocess

import pytest

from library.tools import still_overlays as stills

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="needs ffmpeg to make media")


def _render(path, source, seconds=2):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source,
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(path)], check=True, capture_output=True, encoding="utf-8")
    return str(path)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("overlays")
    return {
        "static": _render(root / "card.mov",
                          "color=c=0x2050c0:s=64x64:r=24:d=2"),
        "animated": _render(root / "sweep.mov",
                            "testsrc=size=64x64:rate=24:duration=2"),
    }


def test_static_video_proves_static(media):
    assert stills.is_static_video(media["static"], 2.0) is True


def test_animated_video_is_left_alone(media):
    assert stills.is_static_video(media["animated"], 2.0) is False


def test_unreadable_media_refuses(tmp_path):
    with pytest.raises(stills.StillOverlayError):
        stills.is_static_video(str(tmp_path / "missing.mov"), 2.0)


def _row(name, start, end, path, markers=None, props=None):
    return {"name": name, "record": [start, end], "src_off": 0,
            "mpi_path": path, "enabled": True, "props": props or {},
            "markers": markers or {}}


def test_clean_swap_passes():
    before = {"V7:Post Header": [
        _row("card.mov", 0, 100, "/media/card.mov")]}
    after = {"V7:Post Header": [
        _row("card_still.png", 0, 100, "/media/card_still.png")]}
    swaps = [{"row": "V7:Post Header", "record": [0, 100],
              "extends": False, "want_end": 100,
              "still_path": "/media/card_still.png"}]
    assert stills.judge_replacement(before, after, swaps) == []


def test_moved_non_target_item_refuses():
    before = {"V7:Post Header": [
        _row("card.mov", 0, 100, "/media/card.mov")],
        "V1:Akshita": [_row("shot.mov", 0, 100, "/media/shot.mov")]}
    after = {"V7:Post Header": [
        _row("card_still.png", 0, 100, "/media/card_still.png")],
        "V1:Akshita": [_row("shot.mov", 0, 99, "/media/shot.mov")]}
    swaps = [{"row": "V7:Post Header", "record": [0, 100],
              "extends": False, "want_end": 100,
              "still_path": "/media/card_still.png"}]
    problems = stills.judge_replacement(before, after, swaps)
    assert len(problems) == 1 and "V1:Akshita" in problems[0]


class _FakePoolItem:
    def __init__(self, path=None):
        self._path = path

    def GetClipProperty(self, key):
        assert key in ("Type", "File Path")
        return "Video" if key == "Type" else self._path


class _FakeItem:
    def __init__(self, start, end):
        self._start, self._end = start, end

    def GetName(self):
        return "card.mov"

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetLeftOffset(self):
        return 0

    def GetClipEnabled(self):
        return True

    def GetFusionCompCount(self):
        return 0

    def GetProperty(self):
        return {}

    def GetMarkers(self):
        return {}

    def GetUniqueId(self):
        return "uid-1"


def test_planned_swap_judges_clean_after_keying(media):
    """Reel 01 refused a correct replacement: `plan_swaps` emits the
    bare row name while the judge matches inventory keys, so every
    swapped item read as "moved without a swap". The call site must
    stamp swaps with the inventory key before judging."""
    triples = [(_FakeItem(0, 48), _FakePoolItem(), media["static"])]
    swaps, skipped, refused = stills.plan_swaps(
        triples, "Post Header", 7, 0, 2000, 24.0)
    assert refused == [] and len(swaps) == 1
    key = stills.inventory_row_key("video", 7, "Post Header")
    before = {key: [_row("card.mov", 0, 48, media["static"])]}
    after = {key: [_row("card_still.png", 0, 2000,
                        "/media/card_still.png")]}
    swaps[0]["still_path"] = "/media/card_still.png"
    problems = stills.judge_replacement(before, after, swaps)
    assert len(problems) == 1 and "moved without a swap" in problems[0]
    stills.key_swaps_for_judge(swaps, "video", 7, "Post Header")
    assert swaps[0]["row"] == key
    assert stills.judge_replacement(before, after, swaps) == []


class _FakeTimeline:
    def __init__(self, rows):
        self._rows = rows

    def GetTrackCount(self, kind):
        return len(self._rows.get(kind, []))

    def GetTrackName(self, kind, index):
        return self._rows[kind][index - 1][0]

    def GetItemListInTrack(self, kind, index):
        return self._rows[kind][index - 1][1]

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return 2000

    def GetMarkers(self):
        return {}


def test_snapshot_keys_come_from_inventory_row_key():
    """The snapshot and the stamped swaps must spell row keys one way -
    `inventory_row_key` is that spelling, used on both sides."""
    timeline = _FakeTimeline({"video": [("Post Header", [])],
                              "audio": []})
    snapshot = stills.inventory_snapshot(timeline)
    assert list(snapshot["rows"]) == [
        stills.inventory_row_key("video", 1, "Post Header")]


class _FakePlacedItem:
    def __init__(self, start, path):
        self._start = start
        self._pool = _FakePoolItem(path)

    def GetStart(self):
        return self._start

    def GetMediaPoolItem(self):
        return self._pool


def test_same_file_twice_resolves_by_record_start():
    """Reel 07 carries one tv_frame across three Frame items: a
    path-only lookup reads its siblings back and refuses a correct
    placement ("row holds 3 still(s)"). The record start is part of
    the identity."""
    row = [_FakePlacedItem(0, "/media/tv_still.png"),
           _FakePlacedItem(553, "/media/tv_still.png"),
           _FakePlacedItem(974, "/media/tv_still.png")]
    found = stills._items_with_path(row, "/media/tv_still.png", 974)
    assert len(found) == 1 and found[0].GetStart() == 974
    assert stills._items_with_path(row, "/media/other.png", 974) == []


def test_lost_note_refuses():
    note = {"color": "Blue", "name": "feedback", "note": "words"}
    before = {"V7:Post Header": [
        _row("card.mov", 0, 100, "/media/card.mov",
             markers={"5": note})]}
    after = {"V7:Post Header": [
        _row("card_still.png", 0, 100, "/media/card_still.png")]}
    swaps = [{"row": "V7:Post Header", "record": [0, 100],
              "extends": False, "want_end": 100,
              "still_path": "/media/card_still.png"}]
    problems = stills.judge_replacement(before, after, swaps)
    assert len(problems) == 1 and "feedback" in problems[0]
