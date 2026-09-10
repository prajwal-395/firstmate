"""Declared overlay positions, honoured exactly or refused loudly.

The Reel 09 numbers are the fixtures: 22 captions the captain pinned
to one tilt, four motion graphics pinned by segment id. What is
asserted is the MECHANISM - segment beats kind beats computed, and
anything malformed refuses - with his values as the data, so a
regression that drops or silently ignores a pin fails here rather
than on his timeline.
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.overlay_intent import (  # noqa: E402
    CAPTION_KIND,
    OverlayIntentError,
    load_intent,
    parse_intent,
    resolve,
)
from library.tools.overlay_placement import (  # noqa: E402
    place_overlay_segment,
)

#: The Reel 09 pin set, as the captain's corrections read: one kind
#: default for all 22 captions, one position per motion graphic.
REEL_09_INTENT = {
    "version": 1,
    "targets": {
        "caption": {"pan": 0.0, "tilt": -1700.0, "scaling": 1},
        "mg_geo-podcast_19fe552d": {"pan": -1153.0, "tilt": 241.0,
                                    "scaling": 1},
        "mg_geo-podcast_0c3a697f": {"pan": 679.0, "tilt": 0.0,
                                    "scaling": 1},
        "mg_geo-podcast_7a07c11c": {"pan": -710.0, "tilt": 0.0,
                                    "scaling": 1},
        "mg_geo-podcast_5e1c3efb": {"pan": 0.0, "tilt": 0.0,
                                    "scaling": 1},
    },
}

COMPUTED_CAPTION = {"scaling": 1, "pan": 0.0, "tilt": -1744.0}


def test_kind_default_pins_every_caption_alike():
    intent = parse_intent(REEL_09_INTENT)
    for segment_id in ("sub_akshita_x_1491914-1493721_6bdaa694",
                       "sub_craig_y_1421571-1426656_8f6f0b4c",
                       "sub_anything_unseen_before"):
        placement, provenance = resolve(CAPTION_KIND, segment_id,
                                        COMPUTED_CAPTION, intent)
        assert provenance == "declared"
        assert placement == {"pan": 0.0, "tilt": -1700.0, "scaling": 1}


def test_segment_pin_beats_kind_default():
    intent = parse_intent({
        "version": 1,
        "targets": {
            "caption": {"pan": 0.0, "tilt": -1700.0, "scaling": 1},
            "sub_special": {"pan": 5.0, "tilt": -1600.0, "scaling": 1},
        },
    })
    placement, provenance = resolve(
        CAPTION_KIND, "sub_special", COMPUTED_CAPTION, intent)
    assert provenance == "declared"
    assert placement["tilt"] == -1600.0


def test_motion_graphics_have_no_kind_fallback():
    intent = parse_intent(REEL_09_INTENT)
    placement, provenance = resolve(
        "semantic visual", "mg_geo-podcast_unseen",
        {"scaling": 1, "pan": 99.0, "tilt": 9.0}, intent)
    assert provenance == "computed"
    assert placement == {"scaling": 1, "pan": 99.0, "tilt": 9.0}


def test_unpinned_keeps_computed():
    placement, provenance = resolve(
        CAPTION_KIND, "sub_x", COMPUTED_CAPTION, {})
    assert provenance == "computed"
    assert placement == COMPUTED_CAPTION
    assert placement is not COMPUTED_CAPTION


def test_nothing_computed_nothing_declared_is_no_transform():
    placement, provenance = resolve("caption", "sub_x", None, {})
    assert (placement, provenance) == (None, "computed")


def test_partial_pin_is_refused():
    with pytest.raises(OverlayIntentError):
        parse_intent({"version": 1,
                      "targets": {"caption": {"tilt": -1700.0}}})


def test_non_numeric_pin_is_refused():
    with pytest.raises(OverlayIntentError):
        parse_intent({"version": 1, "targets": {
            "caption": {"pan": 0.0, "tilt": "low", "scaling": 1}}})


def test_wrong_version_is_refused():
    with pytest.raises(OverlayIntentError):
        parse_intent({"version": 2, "targets": {}})


def test_non_object_is_refused():
    with pytest.raises(OverlayIntentError):
        parse_intent([])


def test_missing_project_file_is_no_intent(tmp_path):
    assert load_intent(str(tmp_path)) == {}


def test_missing_explicit_file_is_refused(tmp_path):
    with pytest.raises(OverlayIntentError):
        load_intent(intent_file=str(tmp_path / "absent.json"))


def test_malformed_file_is_refused(tmp_path):
    path = tmp_path / "overlay_intent.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(OverlayIntentError):
        load_intent(intent_file=str(path))


def test_explicit_file_loads(tmp_path):
    path = tmp_path / "overlay_intent.json"
    path.write_text(json.dumps(REEL_09_INTENT), encoding="utf-8")
    intent = load_intent(intent_file=str(path))
    placement, provenance = resolve(
        CAPTION_KIND, "sub_x", COMPUTED_CAPTION, intent)
    assert (placement["tilt"], provenance) == (-1700.0, "declared")


class _Item:
    """A timeline item fake: records SetProperty, serves GetStart."""

    def __init__(self, start):
        self._start = start
        self.set_calls = {}

    def GetStart(self):
        return self._start

    def SetProperty(self, prop, value):
        self.set_calls[prop] = value
        return True

    def GetProperty(self, prop=None):
        if prop is None:
            return dict(self.set_calls)
        return self.set_calls[prop]


class _Pool:
    def __init__(self, result=True):
        self._result = result

    def AppendToTimeline(self, specs):
        return self._result


class _Timeline:
    def __init__(self, items):
        self._items = items

    def GetItemListInTrack(self, kind, index):
        assert kind == "video"
        return self._items


def test_declared_intent_wins_over_computed_on_the_timeline():
    """Reel 09 captions: computed -1744.0, declared -1700.0 - the
    placed item carries the declared position, end to end through
    the production placer."""
    item = _Item(10)
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement=dict(COMPUTED_CAPTION),
        label="seg", kind="caption", segment_id="sub_x",
        intent=parse_intent(REEL_09_INTENT))
    assert ok and note == ""
    assert item.set_calls == {"Scaling": 1, "Pan": 0.0, "Tilt": -1700.0}


def test_no_intent_keeps_computed_on_the_timeline():
    item = _Item(10)
    ok, note = place_overlay_segment(
        _Pool(result=["placed"]), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=9,
        placement=dict(COMPUTED_CAPTION),
        label="seg", kind="caption", segment_id="sub_x",
        intent=None)
    assert ok and note == ""
    assert item.set_calls["Tilt"] == -1744.0
