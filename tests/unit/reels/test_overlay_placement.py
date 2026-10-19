from __future__ import annotations

from types import SimpleNamespace

from library.tools import overlay_intent, overlay_placement


class _Item:
    def __init__(self):
        self.start = 10
        self.values = {}

    def GetStart(self):
        return self.start

    def GetProperty(self, name):
        return self.values.get(name)


class _Timeline:
    def __init__(self, item):
        self.item = item

    def GetSetting(self, name):
        return {"timelineResolutionWidth": 1080,
                "timelineResolutionHeight": 1920}[name]

    def GetItemListInTrack(self, media_type, track):
        assert (media_type, track) == ("video", 2)
        return [self.item]


def test_absolute_box_lowers_even_when_full_canvas_has_no_hint(monkeypatch):
    item = _Item()
    timeline = _Timeline(item)
    applied = []

    class _Model:
        def __init__(self, *args):
            assert args == (1080, 1920, "original", "original")

        def place_absolute_box(self, canvas, box, *, anchor, draw_gain,
                               rotation=0.0, crop=(0, 0, 0, 0)):
            assert canvas == (728, 480)
            assert box == (0.0, 0.0, 1080.0, 1920.0)
            assert anchor == (0.5, 0.5)
            return SimpleNamespace(
                scaling=1.0, pan=0.0, tilt=0.0,
                zoom_x=1080 / 728, zoom_y=1920 / 480,
                rotation=0.0, anchor_x=0.5, anchor_y=0.5,
                crop=(0.0, 0.0, 0.0, 0.0))

    monkeypatch.setattr(overlay_intent, "resolve",
                        lambda *args, **kwargs: (None, "computed"))
    monkeypatch.setattr(overlay_placement, "entry_unit_mismatch",
                        lambda *args: "")
    monkeypatch.setattr("library.tools.layout_model.LayoutModel", _Model)
    monkeypatch.setattr("library.tools.layout_model.current_proxy_units",
                        lambda _: "original")

    def set_property(placed, prop, value, **kwargs):
        placed.values[prop] = value
        applied.append((prop, value))
        return True

    monkeypatch.setattr("library.tools.transform_write_log.set_property",
                        set_property)
    result = overlay_placement.apply_placement_transform(
        timeline, 2, 10, None, label="post header",
        canvas=(728, 480), frame=(1080, 1920),
        absolute_box=(0, 0, 1080, 1920))

    assert result == ""
    assert dict(applied)["ZoomX"] == 1080 / 728
    assert dict(applied)["ZoomY"] == 4.0


def test_unqualified_crop_geometry_refuses_native_transform_write(monkeypatch):
    from library.tools.layout_model import GeometryDescriptor

    item = _Item()
    timeline = _Timeline(item)
    monkeypatch.setattr(overlay_intent, "resolve",
                        lambda *args, **kwargs: (None, "computed"))
    monkeypatch.setattr(overlay_placement, "entry_unit_mismatch",
                        lambda *args: "")
    monkeypatch.setattr("library.tools.layout_model.current_proxy_units",
                        lambda _: "original")
    geometry = GeometryDescriptor(
        frame=(1080, 1920), box=(10, 20, 310, 220),
        canvas=(300, 200), crop=(.1, 0, 0, 0)).to_dict()

    result = overlay_placement.apply_placement_transform(
        timeline, 2, 10, None, label="cropped graphic",
        canvas=(300, 200), frame=(1080, 1920),
        absolute_box=(10, 20, 310, 220), geometry_descriptor=geometry,
        resolve_project=SimpleNamespace())

    assert "source crop" in result
    assert "not qualified" in result
    assert not item.values
