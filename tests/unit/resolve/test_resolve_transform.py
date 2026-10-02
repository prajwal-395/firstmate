"""Resolve's shared Pan/Tilt law matches the rendered measurements.

Calibration history is in `docs/evidence/pan_tilt_units.md`.
"""
import pytest
from library.tools.reel_framing import delivered_picture
from library.tools.resolve_transform import (
    MEASURED_OVERLAY_CASES,
    MEASURED_PICTURE_CASES,
    NATIVE_BASE_SCALE,
    ResolveTransformError,
    drawn_centre,
    drawn_origin,
    fit_base_scale,
    pan_tilt_for_centre,
    shift_px,
)
from library.tools.tight_box import (
    canvas_offset,
    canvas_screen_origin,
    placement_for_box,
)
from library.tools.resolve_transform import (
    FALLBACK_DRAW_GAIN,
    units_for_shift,
)
import numpy as np
from PIL import Image
from library.tools import draw_gain_probe as probe
from library.tools import row_shift


# ── 1. The law is DERIVED from the measurements, not asserted ────────

def test_the_gain_solved_from_every_measured_overlay_case_is_one():
    """`G` solved per case, never assumed. Any case that needs a gain
    other than 1 fails here, and 2.0 fails on all fourteen that move."""
    solved = []
    for clip_w, clip_h, frame_w, frame_h, pan, tilt, x0, y0 in \
            MEASURED_OVERLAY_CASES:
        if pan:
            shift = (x0 + clip_w / 2.0) - frame_w / 2.0
            solved.append(shift / (pan * (clip_w / frame_w)))
        if tilt:
            shift = frame_h / 2.0 - (y0 + clip_h / 2.0)
            solved.append(shift / (tilt * (clip_h / frame_h)))
    assert len(solved) == 12, "every case that actually moves is solved"
    for gain in solved:
        assert gain == pytest.approx(1.0, abs=0.005)


def test_the_law_reproduces_every_measured_overlay_case():
    # The 2026-09-11 calibration, pinned as history at explicit
    # gain 1.0: the renderer drew that gain then. Today's gain is
    # pinned separately (test_draw_gain_measured).
    for clip_w, clip_h, frame_w, frame_h, pan, tilt, x0, y0 in \
            MEASURED_OVERLAY_CASES:
        got = drawn_origin(clip_w, clip_h, frame_w, frame_h, pan, tilt,
                           NATIVE_BASE_SCALE, None, None, 1.0)
        assert got == pytest.approx((x0, y0), abs=1.0), (
            f"{clip_w}x{clip_h} at pan {pan} tilt {tilt}")


def test_the_law_reproduces_every_measured_picture_case():
    """Including that the user ZOOM does not enter the shift.

    Cases at zoom 1.0 and 2.307 give the same pixels per unit; a model
    that multiplied by zoom would miss the 2.307 rows by 130%.

    Pinned as history at explicit gain 1.0, like the overlay cases.
    """
    for src_w, src_h, frame_w, frame_h, _zoom, pan, tilt, cx, cy in \
            MEASURED_PICTURE_CASES:
        base = fit_base_scale(src_w, src_h, frame_w, frame_h)
        got = drawn_centre(src_w, src_h, frame_w, frame_h, pan, tilt,
                           base, 1.0)
        assert got == pytest.approx((cx, cy), abs=0.5), (
            f"pan {pan} tilt {tilt} at zoom {_zoom}")


def test_a_zero_dimension_raises_rather_than_dividing():
    with pytest.raises(ResolveTransformError):
        shift_px(100.0, 0, 1920)
    with pytest.raises(ResolveTransformError):
        fit_base_scale(3840, 2160, 1080, 0)


# ── 2. The two paths agree ───────────────────────────────────────────

def test_both_paths_are_the_law_and_not_a_second_copy_of_it():
    """Each call site's answer IS `resolve_transform`'s answer.

    Driven against the law called directly, so re-deriving the
    arithmetic inside either module - the way both wrong models came
    to exist - fails here rather than at the next rebuild.
    """
    frame_w, frame_h = 1080, 1920
    # The overlay path, at native scale.
    for canvas_w, canvas_h, pan, tilt in ((840, 480, 0.0, -1740.0),
                                          (296, 480, 1167.568, 0.0),
                                          (724, 480, 0.0, 5184.0),
                                          (772, 540, 0.7, 2197.333)):
        placement = {"scaling": 1, "pan": pan, "tilt": tilt}
        assert canvas_screen_origin(canvas_w, canvas_h, placement,
                                    frame_w, frame_h) == \
            drawn_origin(canvas_w, canvas_h, frame_w, frame_h, pan, tilt,
                         NATIVE_BASE_SCALE)
        assert placement_for_box(canvas_w, canvas_h, 540.0, 1176.0,
                                 frame_w, frame_h) == {
            "scaling": 1,
            "pan": pan_tilt_for_centre(canvas_w, canvas_h, frame_w,
                                       frame_h, 540.0, 1176.0)[0],
            "tilt": pan_tilt_for_centre(canvas_w, canvas_h, frame_w,
                                        frame_h, 540.0, 1176.0)[1]}
    # The picture path, at the fit.
    for src_w, src_h, pan, tilt in ((3840, 2160, 46.341, 0.25),
                                    (3840, 2160, -12.0, 0.0),
                                    (1920, 1080, 100.0, -100.0)):
        base = fit_base_scale(src_w, src_h, frame_w, frame_h)
        picture = delivered_picture(src_w, src_h, frame_w, frame_h,
                                    {"ZoomX": 1.0, "ZoomY": 1.0,
                                     "Pan": pan, "Tilt": tilt})
        cx, cy = drawn_centre(src_w, src_h, frame_w, frame_h, pan, tilt,
                              base)
        assert (picture.left + picture.right) / 2.0 == pytest.approx(
            cx, abs=1.0)
        assert (picture.top + picture.bottom) / 2.0 == pytest.approx(
            cy, abs=1.0)


def test_the_box_file_reader_and_the_placer_share_one_origin():
    """`canvas_offset` is `drawn_origin` rounded, and nothing else.

    Pinned as history at explicit gain 1.0: measured on an exported
    still of Reel 26 when the renderer drew that gain.
    """
    from library.tools.tight_box import TightBox

    box = TightBox(width=920, height=480, props={},
                   placement={"scaling": 1, "pan": 0.0, "tilt": 2592.0},
                   union_w=920.0, union_h=480.0,
                   full_width=1080, full_height=1920, gain=1.0)
    assert canvas_offset(box) == (80, 72), (
        "measured on an exported still of Reel 26: a 920x480 graphic "
        "stored at Tilt 2592 is found at frame rows 72..552")
    ox, oy = drawn_origin(920, 480, 1080, 1920, 0.0, 2592.0,
                          NATIVE_BASE_SCALE, None, None, 1.0)
    assert canvas_offset(box) == (round(ox), round(oy))


# --------------------------------------------------------------------------
# From test_draw_gain_measured.py
#
# The fallback draw gain matches rendered-pixel measurements.
#
# Calibration evidence is in `docs/evidence/pan_tilt_units.md`.

def _solved_gain(shift_px_measured, units, clip_dim, frame_dim,
                 base=1.0):
    """The gain a (shift, units) pair needs under the law's geometry."""
    return shift_px_measured / (units * (clip_dim / frame_dim) * base)


def test_the_measured_caption_points_solve_gain_two():
    # (stored Tilt, rendered canvas centre y) on 904x480 in 1080x1920.
    for tilt, centre_y in ((-888.0, 1404.0), (-917.0, 1418.5)):
        shift = centre_y - 960.0
        assert _solved_gain(shift, -tilt, 480, 1920) == pytest.approx(
            2.0, abs=0.01)


def test_the_declared_row_converts_to_the_captains_hand_value():
    """The ground truth the rebuild gate checks: caption_row 0.8451 is
    delivery row 1623, canvas centre y 1418.5 on a 904x480 canvas, and
    the conversion must store Tilt -917 - his hand value, exactly -
    not the -1834 the unmeasured law produces."""
    assert units_for_shift(960.0 - 1418.5, 480, 1920) == pytest.approx(
        -917.0, abs=0.5)
    assert drawn_centre(904, 480, 1080, 1920, 0.0, -917.0) == \
        pytest.approx((540.0, 1418.5), abs=0.5)


def test_the_default_gain_is_the_fallback_gain():
    assert FALLBACK_DRAW_GAIN == pytest.approx(2.0)
    assert units_for_shift(960.0 - 1418.5, 480, 1920) == pytest.approx(
        units_for_shift(960.0 - 1418.5, 480, 1920, 1.0,
                        FALLBACK_DRAW_GAIN))
    assert shift_px(-917.0, 480, 1920) == pytest.approx(
        shift_px(-917.0, 480, 1920, 1.0, FALLBACK_DRAW_GAIN))


def test_the_inverse_round_trips_at_the_measured_gain():
    for value in (-1836.0, -917.0, -888.0, 0.0, 583.78):
        for clip_dim, frame_dim, base in ((480, 1920, 1.0),
                                          (296, 1080, 1.0),
                                          (2160, 1920, 0.28125)):
            back = units_for_shift(
                shift_px(value, clip_dim, frame_dim, base),
                clip_dim, frame_dim, base)
            assert back == pytest.approx(value, abs=1e-9)


# --------------------------------------------------------------------------
# From test_draw_gain_probe.py
#
# The build-time draw-gain probe, without Resolve in it.
#
# Pure helpers are tested directly (plate determinism, bar-extent
# measurement, the red-marker native-size proof, gain derivation
# including the zero case, fallback record shaping). The orchestration
# - create, import, place, set, still twice, measure, delete, restore -
# runs against duck-typed fakes in the shape of the scripting proxies,
# with the still capture stubbed to draw the plate shifted by a KNOWN
# gain: the record must come back with that gain from pixels the test
# drew itself. Failure paths (a refused grab, a blank still, an
# unreadable entry playhead) must fall back loud, never raise and never
# leave the fakes changed.

def _plate_array():
    import tempfile, os

    path = os.path.join(tempfile.mkdtemp(), "plate.png")
    probe.build_plate(path)
    return np.asarray(Image.open(path).convert("RGB"))


@pytest.mark.usefixtures("_sole_writer")
def test_gain_derivation_is_the_law_inverted():
    # Full-frame plate: 800px down from Tilt -400 is gain 2.
    assert probe.derive_gain(800.0, -400.0, 1920, 1920) == pytest.approx(
        2.0)
    assert probe.derive_gain(400.0, -400.0, 1920, 1920) == pytest.approx(
        1.0)
    with pytest.raises(ValueError):
        probe.derive_gain(0.0, 0.0, 1920, 1920)


# ── Orchestration against fakes ───────────────────────────────────

SIM_GAIN = 2.0
FRAME_WH = (1080, 1920)


@pytest.fixture
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


@pytest.mark.usefixtures("_sole_writer")
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


@pytest.mark.usefixtures("_sole_writer")
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


# --------------------------------------------------------------------------
# From test_row_shift.py
#
# One pixel move is a DIFFERENT Tilt on a picture clip than on an overlay.
#
# 2026-09-25: moving the picture, its frame and the captions of 30 reels by
# pixel counts. A Tilt unit moves a clip by a fraction of its OWN drawn
# size, so treating a unit as a pixel - or the picture's law as the
# overlay's - would move each row by a different wrong amount.

def _tracks():
    return [
        {"type": "video", "index": 1, "name": "Craig", "clips": [
            {"name": "A.MXF", "source_file": "pic",
             "transform": {"Tilt": -0.79}}]},
        {"type": "video", "index": 4, "name": "Subtitles", "clips": [
            {"name": "sub.mov", "source_file": "cap",
             "transform": {"Tilt": -1836.0}}]},
        {"type": "video", "index": 6, "name": "Motion Graphics", "clips": [
            {"name": "logo_bulb.mov", "source_file": "card",
             "transform": {"Tilt": 0.0}}]},
    ]


SIZES = {"pic": (3840, 2160), "cap": (904, 480), "card": (1080, 1920)}


def test_each_row_moves_the_stated_pixels_on_its_own_law():
    spec = row_shift.shift_spec(
        _tracks(), 1, {"Craig": 220, "Subtitles": -26,
                       "Motion Graphics": 220},
        frame=(1080, 1920), draw_gain=1.0, skip_prefixes=("logo_",),
        size_of=SIZES.__getitem__)
    by_row = {e["row"]: e["properties"]["Tilt"] for e in spec["edits"]}
    assert "V6" not in by_row  # the end card is skipped
    fit = fit_base_scale(3840, 2160, 1080, 1920)
    picture_px = shift_px(-0.79 - by_row["V1"], 2160, 1920, fit, 1.0)
    caption_px = shift_px(by_row["V4"] + 1836.0, 480, 1920, 1.0, 1.0)
    assert round(picture_px) == 220   # down: Tilt decreased
    assert round(caption_px) == 26     # up: Tilt increased


def test_a_row_the_reel_does_not_carry_is_refused():
    with pytest.raises(row_shift.RowShiftError):
        row_shift.shift_spec(_tracks(), 1, {"Frame": 220}, frame=(1080, 1920),
                             draw_gain=1.0, size_of=SIZES.__getitem__)


def test_scaling_the_picture_keeps_what_it_frames_and_what_rides_on_it():
    # 2026-09-25: shrinking the picture so no phone crops its edges. A
    # punched-in shot is panned to frame the speaker; scaling its zoom
    # but not its pan would slide the speaker off the window's centre,
    # and a title riding on the picture must move with it, not shrink.
    fit = fit_base_scale(3840, 2160, 1080, 1920)
    tracks = [
        {"type": "video", "index": 1, "name": "Craig", "clips": [
            {"name": "A.MXF", "source_file": "pic",
             "transform": {"Pan": 120.0, "Tilt": -0.79,
                           "ZoomX": 2.3, "ZoomY": 2.3}}]},
        {"type": "video", "index": 2, "name": "Frame", "clips": [
            {"name": "tv.mov", "source_file": "card",
             "transform": {"Pan": 0.0, "Tilt": -220.0,
                           "ZoomX": 1.0, "ZoomY": 1.0}}]},
        {"type": "video", "index": 6, "name": "Motion Graphics", "clips": [
            {"name": "title.mov", "source_file": "cap",
             "transform": {"Pan": -400.0, "Tilt": -1500.0,
                           "ZoomX": 1.0, "ZoomY": 1.0}}]},
    ]
    anchor = (540.0, 960.0 + 220)
    spec = row_shift.scale_spec(
        tracks, 1, [row_shift.PICTURE], 0.8,
        move_rows=["Motion Graphics", "Explainer"], anchor=anchor,
        frame=(1080, 1920), draw_gain=1.0, size_of=SIZES.__getitem__)
    assert spec["absent"] == ["Explainer"]
    props = {e["row"]: e["properties"] for e in spec["edits"]}

    def centre(transform, size, base):
        dx = shift_px(abs(transform["Pan"]), size[0], 1080, base, 1.0)
        dy = shift_px(abs(transform["Tilt"]), size[1], 1920, base, 1.0)
        return (540 + (dx if transform["Pan"] >= 0 else -dx),
                960 + (-dy if transform["Tilt"] >= 0 else dy))

    # The frame is not zoomed: a smaller TV is drawn inside its overlay.
    assert "V2" not in props
    for row, size, base, old in (
            ("V1", SIZES["pic"], fit, tracks[0]["clips"][0]["transform"]),
            ("V6", SIZES["cap"], 1.0, tracks[2]["clips"][0]["transform"])):
        before, after = centre(old, size, base), centre(props[row], size,
                                                        base)
        for b, a, c in zip(before, after, anchor):
            assert a - c == pytest.approx(0.8 * (b - c), abs=0.01)
    assert props["V1"]["ZoomX"] == pytest.approx(2.3 * 0.8)
    assert "ZoomX" not in props["V6"]  # the title keeps its size
