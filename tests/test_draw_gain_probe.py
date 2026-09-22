"""The build-time draw-gain probe, without Resolve in it.

Pure helpers are tested directly (plate determinism, bar-extent
measurement, the red-marker native-size proof, gain derivation
including the zero case, fallback record shaping). The orchestration
- create, import, place, set, still twice, measure, delete, restore -
runs against duck-typed fakes in the shape of the scripting proxies,
with the still capture stubbed to draw the plate shifted by a KNOWN
gain: the record must come back with that gain from pixels the test
drew itself. Failure paths (a refused grab, a blank still, an
unreadable entry playhead) must fall back loud, never raise and never
leave the fakes changed.
"""

import numpy as np
import pytest
from PIL import Image

from library.tools import draw_gain_probe as probe
from library.tools.resolve_transform import FALLBACK_DRAW_GAIN


def _plate_array():
    import tempfile, os

    path = os.path.join(tempfile.mkdtemp(), "plate.png")
    probe.build_plate(path)
    return np.asarray(Image.open(path).convert("RGB"))


def test_the_plate_is_deterministic():
    import tempfile, os

    d = tempfile.mkdtemp()
    a = probe.build_plate(os.path.join(d, "a.png"))
    b = probe.build_plate(os.path.join(d, "b.png"))
    assert (np.asarray(Image.open(a)) == np.asarray(Image.open(b))).all()


def test_bar_extent_finds_the_shifted_bars():
    plate = _plate_array()
    # Paste the whole plate shifted down 800px.
    canvas = np.zeros((1920, 1080, 3), dtype=np.uint8)
    canvas[800:800 + 1120, :, :] = plate[:1120, :, :]
    first, last = probe.bar_extent(canvas)
    assert (first, last) == (800 + 283, 800 + 403)


def test_bar_extent_on_blank_is_empty():
    assert probe.bar_extent(np.zeros((1920, 1080, 3),
                                     dtype=np.uint8)) == (0, -1)


def test_gain_derivation_is_the_law_inverted():
    # Full-frame plate: 800px down from Tilt -400 is gain 2.
    assert probe.derive_gain(800.0, -400.0, 1920, 1920) == pytest.approx(
        2.0)
    assert probe.derive_gain(400.0, -400.0, 1920, 1920) == pytest.approx(
        1.0)
    with pytest.raises(ValueError):
        probe.derive_gain(0.0, 0.0, 1920, 1920)


def test_the_red_marker_proves_native_size():
    plate = _plate_array()
    first, last = probe.red_marker_extent(plate)
    file_h = probe.PROBE_MARKER_LAST - probe.PROBE_MARKER_FIRST + 1
    assert last - first + 1 == pytest.approx(
        file_h, abs=probe.NATIVE_SIZE_TOLERANCE_PX)
    blank = np.zeros((1920, 1080, 3), dtype=np.uint8)
    assert probe.red_marker_extent(blank) == (0, -1)


def test_fallback_record_is_loud_and_shaped():
    record = probe._fallback_record(["a reason"])
    assert record["source"] == "fallback"
    assert record["gain"] == FALLBACK_DRAW_GAIN
    assert record["disagrees_with_fallback"] is False
    assert record["warnings"] == ["a reason"]


# ── Orchestration against fakes ───────────────────────────────────

SIM_GAIN = 2.0
FRAME_WH = (1080, 1920)


@pytest.fixture(autouse=True)
def _sole_writer():
    """The probe runs inside its own exclusive hold in production;
    the fakes here have no instance to contend for, so the cursor
    establishments go through unguarded-with-a-reason rather than
    queueing behind a live captain for nothing."""
    from library.tools.resolve_lock import assume_sole_writer
    with assume_sole_writer(
            "test: fake project has no instance to contend for"):
        yield


class _Clip:
    def __init__(self, duration=120):
        self._duration = duration
        self.props = {"Tilt": 0.0, "Pan": 0.0}

    def GetDuration(self):
        return self._duration

    def SetProperty(self, name, value):
        self.props[name] = value
        return True

    def GetProperty(self, name):
        return self.props[name]


class _Timeline:
    def __init__(self, name, project):
        self._name = name
        self._project = project
        self.clip = None
        self.key = 0
        self.settings = {}

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return f"uid-{self._name}-{id(self)}"

    def SetSetting(self, key, value):
        self.settings[key] = value
        return True

    def GetSetting(self, key):
        return {"timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920",
                "timelineFrameRate": "23.976",
                "timelinePlaybackFrameRate": "23.976"}.get(key)

    def GetStartTimecode(self):
        return "00:00:00:00"

    def GetCurrentTimecode(self):
        total = self.key
        return f"00:00:{total // 24:02d}:{total % 24:02d}"

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return self.clip.GetDuration() if self.clip else 0

    def SetCurrentTimecode(self, tc):
        h, m, s, f = [int(x) for x in tc.replace(";", ":").split(":")]
        self.key = ((h * 60 + m) * 60 + s) * 24 + f
        return True


class _Pool:
    def __init__(self, project):
        self._project = project
        self.deleted_timelines = []
        self.deleted_clips = []

    def CreateEmptyTimeline(self, name):
        tl = _Timeline(name, self._project)
        self._project.timelines.append(tl)
        return tl

    def AppendToTimeline(self, items):
        tl = self._project.current
        tl.clip = _Clip()
        return [tl.clip]

    def DeleteTimelines(self, timelines):
        for tl in timelines:
            if tl in self._project.timelines:
                self._project.timelines.remove(tl)
                self.deleted_timelines.append(tl.GetName())
        return True

    def DeleteClips(self, items):
        self.deleted_clips.extend(items)
        return True


class _Storage:
    def AddItemListToMediaPool(self, paths):
        return [object() for _ in paths]


class _Project:
    def __init__(self):
        self.timelines = [_Timeline("entry", self)]
        self.current = self.timelines[0]
        self._pool = _Pool(self)

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, tl):
        self.current = tl
        return True

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1]

    def GetMediaPool(self):
        return self._pool


class _Resolve:
    def __init__(self, project):
        self._project = project
        self.pages = []

    def GetMediaStorage(self):
        return _Storage()

    def OpenPage(self, page):
        self.pages.append(page)
        return True


def _make_grab(plate_path):
    """A gallery-still stub drawing the full plate at SIM_GAIN."""
    plate = np.asarray(Image.open(plate_path).convert("RGB"))

    def grab(timeline, project, dest):
        tilt = timeline.clip.GetProperty("Tilt")
        shift = int(round(-tilt * SIM_GAIN))
        canvas = np.zeros((1920, 1080, 3), dtype=np.uint8)
        canvas[shift:shift + 1920, :, :] = plate[
            :1920 - shift, :, :] if shift >= 0 else plate[
            -shift:, :, :]
        from library.tools.marker_capture import StillResult
        Image.fromarray(canvas).save(dest)
        return StillResult(path=dest, gallery_album="a",
                           stills_before=0, stills_after=0,
                           exported_names=[], discarded=[],
                           surface="gallery_still")

    return grab


def _make_grab_proxy(plate_holder):
    def grab(timeline, project, dest):
        # The production grab needs a path it can mkdir beside:
        # a str destination is the bug that bit the first live run.
        assert hasattr(dest, "parent"), type(dest)
        return _make_grab(plate_holder["path"])(timeline, project,
                                                dest)

    return grab


def test_measured_gain_comes_back_from_rendered_pixels(monkeypatch,
                                                      tmp_path):
    import library.tools.draw_gain_probe as probe_mod

    project = _Project()
    resolve = _Resolve(project)
    plate_holder = {}

    real_build = probe_mod.build_plate

    def spy_build(path, width=1080, height=1920):
        out = real_build(path, width, height)
        plate_holder["path"] = out
        return out

    monkeypatch.setattr(probe_mod, "build_plate", spy_build)
    import library.tools.marker_capture as mc_mod
    monkeypatch.setattr(mc_mod, "grab_still",
                        _make_grab_proxy(plate_holder))
    record = probe_mod.calibrate(resolve, project, FRAME_WH,
                                 workdir=str(tmp_path))
    assert record["source"] == "measured"
    assert record["gain"] == pytest.approx(SIM_GAIN, abs=0.05)
    assert record["disagrees_with_fallback"] == (
        abs(SIM_GAIN - FALLBACK_DRAW_GAIN) > 0.05)
    # Nothing left behind, entry put back.
    assert probe_mod.PROBE_TIMELINE_NAME not in [
        tl.GetName() for tl in project.timelines]
    assert project.GetMediaPool().deleted_clips != []
    assert project.GetCurrentTimeline().GetName() == "entry"


def test_a_refused_grab_falls_back_loud(monkeypatch, tmp_path):
    import library.tools.draw_gain_probe as probe_mod

    project = _Project()
    resolve = _Resolve(project)

    def boom(timeline, project, dest):
        raise RuntimeError("gallery declined")

    import library.tools.marker_capture as mc_mod
    monkeypatch.setattr(mc_mod, "grab_still", boom)
    record = probe_mod.calibrate(resolve, project, FRAME_WH,
                                 workdir=str(tmp_path))
    assert record["source"] == "fallback"
    assert record["gain"] == FALLBACK_DRAW_GAIN
    assert record["warnings"], "a silent fallback is the defect"
    assert project.GetCurrentTimeline().GetName() == "entry"
    assert probe_mod.PROBE_TIMELINE_NAME not in [
        tl.GetName() for tl in project.timelines]


def test_a_blank_still_falls_back_loud(monkeypatch, tmp_path):
    import library.tools.draw_gain_probe as probe_mod

    project = _Project()
    resolve = _Resolve(project)

    def blank(timeline, project, dest):
        Image.new("RGB", (1080, 1920), (0, 0, 0)).save(dest)
        from library.tools.marker_capture import StillResult
        return StillResult(path=dest, gallery_album="a",
                           stills_before=0, stills_after=0,
                           exported_names=[], discarded=[],
                           surface="gallery_still")

    import library.tools.marker_capture as mc_mod
    monkeypatch.setattr(mc_mod, "grab_still", blank)
    record = probe_mod.calibrate(resolve, project, FRAME_WH,
                                 workdir=str(tmp_path))
    assert record["source"] == "fallback"
    assert any("bars span" in w for w in record["warnings"])
