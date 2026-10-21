"""An overlay transform the placed item already carries is not written.

The defect this guards, measured on a reel build (2026-10-21): the
overlay placement wrote all 420 transform properties of a build - 105
overlays x {Pan, Tilt, ZoomX, ZoomY} - at ~0.17 s each, and 365 of
them were values the freshly placed item ALREADY carried (Pan 0, Tilt 0,
ZoomX 1, ZoomY 1 are what Resolve gives a new clip). A property already
at the wanted value is not changed by writing it, so the write is
skipped - and the read-back below still judges the value either way.

The skip is decided by a READ-BACK, never by an assumption: the value
is read off the handle before any write, and a value that was not read
is not compared at all.
"""

from types import SimpleNamespace

from library.tools import overlay_intent, overlay_placement


class _Item:
    """A placed clip that carries what Resolve gives a new one."""

    def __init__(self, **props):
        self.start = 10
        self.values = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0}
        self.values.update(props)
        self.writes = []

    def GetStart(self):
        return self.start

    def GetProperty(self, name=None):
        if name is None:
            return dict(self.values)
        return self.values.get(name)

    def SetProperty(self, prop, value):
        self.writes.append((prop, value))
        self.values[prop] = value
        return True


class _Timeline:
    def __init__(self, item):
        self.item = item

    def GetSetting(self, name):
        return {"timelineResolutionWidth": 1080,
                "timelineResolutionHeight": 1920}[name]

    def GetItemListInTrack(self, media_type, track):
        return [self.item]


def _ready(monkeypatch, placement):
    """The intent resolution returns the placement under test."""
    monkeypatch.setattr(overlay_intent, "resolve",
                        lambda *args, **kwargs: (placement, "computed"))
    monkeypatch.setattr(overlay_placement, "entry_unit_mismatch",
                        lambda *args: "")
    monkeypatch.setattr("library.tools.layout_model.current_proxy_units",
                        lambda _: "original")

    def set_property(placed, prop, value, **kwargs):
        placed.writes.append((prop, value))
        placed.values[prop] = value
        return True

    monkeypatch.setattr("library.tools.transform_write_log.set_property",
                        set_property)


def test_a_transform_the_placed_item_already_carries_is_not_written(
        monkeypatch):
    """The no-op write: the item already carries the value, and writing
    it again costs ~0.17 s and changes nothing."""
    placement = {"scaling": 1.0, "pan": 0.0, "tilt": 0.0,
                 "zoom_x": 1.0, "zoom_y": 1.0}
    _ready(monkeypatch, placement)
    item = _Item()
    timeline = _Timeline(item)

    result = overlay_placement.apply_placement_transform(
        timeline, 2, 10, placement, label="caption")

    assert result == ""
    # `Scaling` is a dictionary in Resolve: it is never compared, so it is
    # written. The four transform properties were not.
    assert item.writes == [("Scaling", 1.0)]
    assert ("Pan", 0.0) not in item.writes
    assert ("Tilt", 0.0) not in item.writes
    assert ("ZoomX", 1.0) not in item.writes
    assert ("ZoomY", 1.0) not in item.writes


def test_a_transform_the_item_does_not_carry_is_written(monkeypatch):
    """The skip must never cost a write the placement needs."""
    placement = {"scaling": 1.0, "pan": 0.0, "tilt": -1720.0,
                 "zoom_x": 1.0, "zoom_y": 1.0}
    _ready(monkeypatch, placement)
    item = _Item()
    timeline = _Timeline(item)

    result = overlay_placement.apply_placement_transform(
        timeline, 2, 10, placement, label="caption")

    assert result == ""
    assert ("Tilt", -1720.0) in item.writes
    assert ("Pan", 0.0) not in item.writes
    assert ("ZoomX", 1.0) not in item.writes


def test_a_skip_never_hides_a_value_the_item_does_not_hold(monkeypatch):
    """The read-back judges the value either way: a skipped property that
    the item does not really hold is still a refused placement."""
    placement = {"scaling": 1.0, "pan": 0.0, "tilt": 0.0,
                 "zoom_x": 1.0, "zoom_y": 1.0}
    _ready(monkeypatch, placement)
    item = _Item()
    timeline = _Timeline(item)

    def read_back(placed, prop):
        # The item LIED about the value it carries: the whole-item read
        # says Pan 0, the single read says 7.
        return 7.0 if prop == "Pan" else placed.values.get(prop)

    monkeypatch.setattr(overlay_placement, "_read_back", read_back)
    result = overlay_placement.apply_placement_transform(
        timeline, 2, 10, placement, label="caption")

    assert "Pan=0" in result
    assert "Resolve holds 7" in result


def test_the_declared_zoom_rides_after_the_per_axis_zoom(monkeypatch):
    """The declared zoom overrides the per-axis one, so it is written even
    when the item already carries the declared value."""
    placement = {"scaling": 1.0, "pan": 0.0, "tilt": 0.0,
                 "zoom_x": 2.0, "zoom_y": 2.0, "zoom": 1.0}
    _ready(monkeypatch, placement)
    item = _Item()
    timeline = _Timeline(item)

    overlay_placement.apply_placement_transform(
        timeline, 2, 10, placement, label="caption")

    assert item.values["ZoomX"] == 1.0
    assert item.writes == [("Scaling", 1.0), ("ZoomX", 2.0), ("ZoomY", 2.0),
                           ("ZoomX", 1.0), ("ZoomY", 1.0)]


def test_an_item_that_cannot_be_read_is_written_everything(monkeypatch):
    """An unreadable item is not an item that already carries anything."""
    placement = {"scaling": 1.0, "pan": 0.0, "tilt": 0.0,
                 "zoom_x": 1.0, "zoom_y": 1.0}
    _ready(monkeypatch, placement)
    item = _Item()
    timeline = _Timeline(item)

    def unreadable(name=None):
        raise RuntimeError("the item does not serve properties")

    item.GetProperty = unreadable
    result = overlay_placement.apply_placement_transform(
        timeline, 2, 10, placement, label="caption")

    assert result == ""
    assert item.writes == [("Scaling", 1.0), ("Pan", 0.0), ("Tilt", 0.0),
                           ("ZoomX", 1.0), ("ZoomY", 1.0)]


def test_the_skip_is_not_taken_when_a_value_is_not_a_number(monkeypatch):
    """`Scaling` is a dictionary in Resolve: it is not compared, so it is
    always written."""
    placement = {"scaling": 1.0, "pan": 0.0, "tilt": 0.0}
    _ready(monkeypatch, placement)
    item = _Item(Scaling={"X": 1.0, "Y": 1.0})
    timeline = _Timeline(item)

    overlay_placement.apply_placement_transform(
        timeline, 2, 10, placement, label="caption")

    assert ("Scaling", 1.0) in item.writes
    assert ("Pan", 0.0) not in item.writes


def test_a_placement_keeps_its_read_back_judgement(monkeypatch):
    """The held value the intent check reads is the value the item
    carries - skipped or written."""
    placement = {"scaling": 1.0, "pan": 0.0, "tilt": -1720.0,
                 "zoom_x": 1.0, "zoom_y": 1.0}
    _ready(monkeypatch, placement)
    item = _Item()
    timeline = _Timeline(item)

    overlay_placement.apply_placement_transform(
        timeline, 2, 10, placement, label="caption", draw_intent=SimpleNamespace())

    assert item.values["Tilt"] == -1720.0
    assert item.values["ZoomX"] == 1.0
