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


def test_checked_in_overlay_draws_exactly_the_table():
    for name in psz.OVERLAY_NAMES:
        path = psz.overlay_path(name)
        assert os.path.isfile(path), f"regenerate: {path}"
        with Image.open(path) as image:
            assert image.size == psz.REFERENCE_SIZE
            alpha = np.asarray(image.convert("RGBA"))[:, :, 3] > 0
        expected = psz.covered_mask(name, *psz.REFERENCE_SIZE)
        # Every covered pixel is washed, and nothing outside the bands is
        # touched - the lines and legend are drawn inside the bands.
        assert np.array_equal(alpha, expected)


def test_intrusions_names_the_zone_a_box_sits_in():
    # A box across the top 100 rows sits under every app's status bar,
    # and under TikTok's tabs where a short Android status bar lifts them.
    hits = psz.intrusions((300, 20, 700, 100))
    assert {h["platform"] for h in hits} == set(psz.PLATFORMS)
    assert {h["band"] for h in hits} == {"camera", "status-time", "tabs"}
    # The combined safe box clears every zone.
    assert psz.intrusions((130, 300, 770, 830)) == []
    # The captain, 2026-09-25: the Shorts search icon and menu were drawn
    # as a band across the whole top, marking everything left of them
    # unsafe. Each is its own box, so the row beside them is clear...
    assert psz.intrusions((100, 180, 780, 240), "youtube_shorts") == []
    # ...and the icon itself is still covered.
    hits = psz.intrusions((780, 180, 900, 240), "youtube_shorts")
    assert [h["band"] for h in hits] == ["search"]


def test_the_model_lays_the_screenshot_out_where_it_was_measured():
    # Laid out on the phone the screenshots came from, every element
    # lands exactly where the screenshot's own cover mapping puts it. A
    # pin read off the wrong edge, or an inset applied twice, moves a
    # box here before it moves one on a phone nobody has captured.
    for key in sorted(psz.CAPTURES):
        capture = psz.CAPTURES[key]
        phone = psz.DEVICE_BY_NAME[psz.MEASURED_DEVICE]
        s = capture.region / 1920
        ox = (1080 * s - psz.SCREENSHOT[0]) / 2
        laid = {z.name: z.rect for z in psz.zones_on(key, phone)}
        for element in capture.elements:
            x0, y0, x1, y1 = element.box
            want = (max(0, round((x0 - psz.PAD + ox) / s)),
                    max(0, round((y0 - psz.PAD) / s)),
                    min(1080, round((x1 + psz.PAD + ox) / s)),
                    min(1920, round((y1 + psz.PAD) / s)))
            got = laid[element.name]
            assert all(abs(a - b) <= 1 for a, b in zip(got, want)), (
                element.name, got, want)


def _project(tmp_path, avatar):
    (tmp_path / "project.yaml").write_text(
        "effect:\n  post_header:\n"
        f"    avatar: {avatar}\n    name: Acme\n    handle: acme\n"
        "    verified: true\n    top: 0.02\n    name_size: 30\n"
        "    handle_size: 26\n    hook_size: 30\n", encoding="utf-8")
    return str(tmp_path)


def test_a_reel_with_no_or_an_empty_hook_gets_no_header(tmp_path):
    from library.tools import reel_post_header as rph

    avatar = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(avatar)
    folder = _project(tmp_path, avatar)

    def never(*_a, **_k):
        raise AssertionError("rendered a header nobody wrote a hook for")

    plan = rph.plan_for_reel("Reel 01", 1, [(0, 48)], 24.0, 1080, 1920,
                             folder, render=never, draw_gain=1.0)
    assert plan.basis == rph.NO_HOOK_WRITTEN
    assert plan.segments == []

    # An empty hook is refused, not drawn.
    (tmp_path / "external").mkdir()
    (tmp_path / "external" / rph.HOOKS_FILE).write_text(
        json.dumps({"hooks": {"1": {"hook": "  "}}}), encoding="utf-8")
    with pytest.raises(rph.PostHeaderError):
        rph.hook_for(folder, 1)


def test_the_header_is_a_tight_canvas_placed_where_it_laid_out(tmp_path):
    # The captain, 2026-09-25: the header was placed FULL FRAME, so
    # moving it meant re-rendering. It is cut to its ink and placed by
    # Pan/Tilt - and that Pan/Tilt must draw the canvas exactly where
    # the full-frame layout put the ink, or the header jumps on the
    # swap.
    from library.tools import reel_post_header as rph
    from library.tools.tight_box import MIN_CANVAS_HEIGHT, ink_screen_box

    still = tmp_path / "post_header_x.png"
    image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    image.paste((255, 255, 255, 255), (130, 275, 951, 461))
    image.save(still)
    tight, canvas = rph.tight_still(str(still), (1080, 1920))
    with Image.open(tight) as cut:
        size = cut.size
    assert size == (canvas[2] - canvas[0], canvas[3] - canvas[1])
    assert size[0] < 1080 and size[1] >= MIN_CANVAS_HEIGHT
    assert size[0] % 2 == 0 and size[1] % 2 == 0
    for draw_gain in (1.0, 2.0):
        placement = rph.placement_for(canvas, (1080, 1920), draw_gain)
        ink = rph.ink_box(tight)
        drawn = ink_screen_box(size[0], size[1], placement, ink, 1080, 1920,
                               draw_gain=draw_gain)
        assert all(abs(a - b) < 0.5 for a, b in zip(drawn, (130, 275, 951, 461)))


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
