"""One placer, tested without Resolve behind it.

`place_overlay_segment` branches on fakes: a placed clip with no
transform, a tight clip whose transform lands, an AppendToTimeline
that fails, and a Resolve that refuses a property. What is asserted
is the contract in the module docstring - placed-but-refused warns
rather than failing, and only a failed append is False.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.overlay_placement import (  # noqa: E402
    place_overlay_segment,
    sequence_frame_paths,
)


class _Item:
    def __init__(self, start, refuse=()):
        self._start = start
        self._refuse = set(refuse)
        self.set_calls = {}

    def GetStart(self):
        return self._start

    def SetProperty(self, prop, value):
        self.set_calls[prop] = value
        return None if prop in self._refuse else True


class _ClampingItem(_Item):
    """Resolve 21 past four times the timeline dimensions: setting
    beyond returns True and reads back the clamp. The captain's
    captions at Tilt -7680."""

    def __init__(self, start, pan_limit, tilt_limit):
        super().__init__(start)
        self._limits = {"Scaling": float("inf"),
                        "Pan": pan_limit, "Tilt": tilt_limit}
        self._held = {}

    def SetProperty(self, prop, value):
        self.set_calls[prop] = value
        limit = self._limits[prop]
        self._held[prop] = max(-limit, min(limit, value))
        return True

    def GetProperty(self, prop):
        return self._held[prop]


class _Pool:
    def __init__(self, result=True):
        self._result = result
        self.calls = []

    def AppendToTimeline(self, specs):
        self.calls.append(specs)
        return self._result


class _Timeline:
    def __init__(self, items):
        self._items = items

    def GetItemListInTrack(self, kind, index):
        assert kind == "video"
        return self._items


def test_full_canvas_places_with_no_transform():
    pool, item = _Pool(result=["placed"]), _Item(10)
    ok, note = place_overlay_segment(
        pool, _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement=None, label="seg")
    assert ok and note == ""
    assert item.set_calls == {}
    spec = pool.calls[0][0]
    assert spec["trackIndex"] == 3 and spec["recordFrame"] == 10
    assert spec["startFrame"] == 0 and spec["endFrame"] == 9
    assert spec["mediaType"] == 1


def test_tight_clip_gets_scaling_pan_tilt():
    item = _Item(10)
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement={"scaling": 1, "pan": 0.0, "tilt": -100.0},
        label="seg")
    assert ok and note == ""
    assert item.set_calls == {"Scaling": 1, "Pan": 0.0, "Tilt": -100.0}


def test_failed_append_is_the_only_false():
    ok, note = place_overlay_segment(
        _Pool(result=None), _Timeline([]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement={"scaling": 1, "pan": 0.0, "tilt": 0.0},
        label="seg")
    assert not ok
    assert "AppendToTimeline" in note


def test_refused_transform_warns_but_stays_placed():
    """A movable caption Resolve declined to move is misplaced, not
    missing: the build keeps it and says so."""
    item = _Item(10, refuse=("Tilt",))
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement={"scaling": 1, "pan": 0.0, "tilt": -100.0},
        label="seg")
    assert ok
    assert "Tilt" in note


def test_silent_clamp_is_a_refusal_not_a_success():
    """The captain's bug: Resolve returns True past the clamp and
    holds +-7680. The return alone reports the caption placed while
    it sits off-screen; the read-back refuses it instead. Stays
    placed - the clip IS on the timeline - and says the held value,
    so the build log names the clamp rather than a bare refusal."""
    item = _ClampingItem(10, pan_limit=4320, tilt_limit=7680)
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement={"scaling": 1, "pan": 0.0, "tilt": -7929.0},
        label="seg")
    assert ok
    assert "Tilt=-7929.0" in note
    assert "-7680" in note
    assert "clamp" in note


def test_held_values_pass_on_the_read_back():
    """A placement Resolve really holds is still a clean place -
    the read-back must not fail correct output."""
    item = _ClampingItem(10, pan_limit=4320, tilt_limit=7680)
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement={"scaling": 1, "pan": 100.0, "tilt": -100.0},
        label="seg")
    assert ok and note == ""


def test_clamped_pan_is_refused_too():
    """The captain's motion graphics on huge X: Pan overflows the
    same way Tilt does, and a fix watching Tilt alone is half a
    fix."""
    item = _ClampingItem(10, pan_limit=4320, tilt_limit=7680)
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement={"scaling": 1, "pan": 9188.0, "tilt": 0.0},
        label="seg")
    assert ok
    assert "Pan=9188.0" in note
    assert "4320" in note


def test_sequence_lists_pngs_in_order(tmp_path):
    d = tmp_path / "seg.frames"
    d.mkdir()
    for name in ("frame-02.png", "frame-00.png", "frame-01.png",
                 "notes.txt"):
        (d / name).write_bytes(b"x")
    assert sequence_frame_paths(str(d)) == [
        str(d / "frame-00.png"),
        str(d / "frame-01.png"),
        str(d / "frame-02.png"),
    ]


def test_sequence_missing_dir_is_empty():
    assert sequence_frame_paths("/no/such/dir") == []
