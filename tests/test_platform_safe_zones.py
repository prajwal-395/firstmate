"""The safe-zone guides draw the table, and a guide can never render.

Each test names the defect it catches:

* a checked-in overlay PNG that no longer draws
  `platform_safe_zones.PLATFORMS` - the guide an editor trusts would
  mark the wrong pixels;
* a post header drawn with a hook nobody wrote (AGENTS.md 10.5);
* a guide item Resolve reads back ENABLED - the guide would render into
  the export (measured: the ROW disable returned True and rendered).
"""

import json
import os

import numpy as np
import pytest
from PIL import Image

from library.tools import platform_safe_zones as psz


@pytest.mark.parametrize("name", psz.OVERLAY_NAMES)
def test_checked_in_overlay_draws_exactly_the_table(name):
    path = psz.overlay_path(name)
    assert os.path.isfile(path), f"regenerate: {path}"
    with Image.open(path) as image:
        assert image.size == psz.REFERENCE_SIZE
        alpha = np.asarray(image.convert("RGBA"))[:, :, 3] > 0
    expected = psz.covered_mask(name, *psz.REFERENCE_SIZE)
    # Every covered pixel is washed, and nothing outside the bands is
    # touched - the lines and legend are drawn inside the bands.
    assert np.array_equal(alpha, expected)


def test_intrusions_names_the_band_a_box_sits_in():
    # A box in the top 100 rows sits in every platform's top bar.
    hits = psz.intrusions((300, 20, 700, 100))
    assert {h["platform"] for h in hits} == set(psz.PLATFORMS)
    assert all(h["band"] == "top" for h in hits)
    # The combined safe box clears every zone.
    assert psz.intrusions((130, 300, 770, 830)) == []


def _project(tmp_path, avatar):
    (tmp_path / "project.yaml").write_text(
        "effect:\n  post_header:\n"
        f"    avatar: {avatar}\n    name: Acme\n    handle: acme\n"
        "    verified: true\n    top: 0.02\n    name_size: 30\n"
        "    handle_size: 26\n    hook_size: 30\n", encoding="utf-8")
    return str(tmp_path)


def test_a_reel_with_no_hook_gets_no_header(tmp_path):
    from library.tools import reel_post_header as rph

    avatar = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(avatar)
    folder = _project(tmp_path, avatar)

    def never(*_a, **_k):
        raise AssertionError("rendered a header nobody wrote a hook for")

    plan = rph.plan_for_reel("Reel 01", 1, [(0, 48)], 24.0, 1080, 1920,
                             folder, render=never)
    assert plan.basis == rph.NO_HOOK_WRITTEN
    assert plan.segments == []


def test_an_empty_hook_is_refused_not_drawn(tmp_path):
    from library.tools import reel_post_header as rph

    avatar = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(avatar)
    folder = _project(tmp_path, avatar)
    (tmp_path / "external").mkdir()
    (tmp_path / "external" / rph.HOOKS_FILE).write_text(
        json.dumps({"hooks": {"1": {"hook": "  "}}}), encoding="utf-8")
    with pytest.raises(rph.PostHeaderError):
        rph.hook_for(folder, 1)


class _Item:
    """A placed item whose switch-off claims success and changes nothing."""

    def SetClipEnabled(self, value):
        return True

    def GetClipEnabled(self):
        return True


class _Timeline:
    """Enough of Resolve's Timeline to place a guide, with a stuck enable."""

    def __init__(self):
        self.tracks = ["A-Roll"]
        self.enabled = {}

    def GetName(self):
        return "Reel 01"

    def GetStartFrame(self):
        return 86400

    def GetEndFrame(self):
        return 86400 + 47

    def GetTrackCount(self, _kind):
        return len(self.tracks)

    def GetTrackName(self, _kind, index):
        return self.tracks[index - 1]

    def SetTrackName(self, _kind, index, name):
        self.tracks[index - 1] = name
        return True

    def AddTrack(self, _kind):
        self.tracks.append("Video")
        return True

    def DeleteTrack(self, _kind, index):
        del self.tracks[index - 1]
        return True

    def GetItemListInTrack(self, _kind, index):
        return [_Item()] if self.tracks[index - 1] == "Safe Zones" else []

    def SetTrackEnable(self, _kind, index, value):
        return True  # claims success, changes nothing

    def GetIsTrackEnabled(self, _kind, index):
        return True


class _Pool:
    def ImportMedia(self, paths):
        return ["item"]

    def AppendToTimeline(self, infos):
        return ["placed"]


class _Project:
    def GetMediaPool(self):
        return _Pool()


def test_a_guide_item_that_reads_back_enabled_is_refused(tmp_path, monkeypatch):
    from library.tools import safe_zone_guide as szg

    monkeypatch.setattr(szg, "guide_movie", lambda *a, **k: "guide.mov")
    timeline = _Timeline()
    with pytest.raises(szg.SafeZoneGuideError, match="ENABLED"):
        szg.place_on_timeline(_Project(), timeline, str(tmp_path),
                              psz.COMBINED, 24.0)
    # ...and the row that would have rendered is gone again.
    assert timeline.tracks == ["A-Roll"]


def _item(start, frames, name="post_header_0123456789", row="Post Header"):
    from library.tools.reel_conformance_verifier import TimelineItem

    return TimelineItem(track_type="video", track_index=9,
                        start_frame=start, end_frame=start + frames,
                        duration_frames=frames, source_start_frame=0,
                        source_end_frame=frames, source_file=name + ".mov",
                        speaker=None, name=name + ".mov", track_name=row)


def test_f25_catches_a_header_placed_somewhere_the_build_did_not_record():
    from library.tools.reel_conformance_verifier import check_post_header

    record = {"segments": [{"timeline_start": 0.0, "total_frames": 480}]}
    assert check_post_header("Reel 01", [_item(0, 480)], [], record,
                             24.0) == []
    # Placed short: the header would vanish partway through the reel.
    assert check_post_header("Reel 01", [_item(0, 120)], [], record, 24.0)
    # Placed with no record at all: out of band.
    assert check_post_header("Reel 01", [_item(0, 480)], [], None, 24.0)
    # A non-guide file on the disabled guide row never renders.
    assert check_post_header(
        "Reel 01", [], [_item(0, 480, name="tv_frame_x", row="Safe Zones")],
        None, 24.0)
