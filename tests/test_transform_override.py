"""The captain's hand-set Pan/Tilt survives the rebuild.

Invariant: a recorded `transform_override` holds the captain's number
on the span speaking its anchor, on every rebuild, on the reels its
scope names - and says LOST when its words are gone. The incident
history behind this contract lives in
`docs/evidence/transform_override.md`; this module pins only the
behaviour.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools import captain_edits
from library.tools.project_layout import ProjectLayout
from library.tools.resolve_transform import FALLBACK_DRAW_GAIN


# ── Fixtures ─────────────────────────────────────────────────────────

def _tx():
    """Two spans of timed speech: Akshita explains, Craig asks."""
    def seg(speaker, text, start, words):
        out = []
        cursor = start
        for token in words:
            out.append({"word": token, "start": round(cursor, 3),
                        "end": round(cursor + 0.3, 3), "timed": True})
            cursor += 0.4
        return {"speaker": speaker, "text": text,
                "timeline_start": start,
                "timeline_end": round(cursor, 3), "words": out}

    return {"segments": [
        seg("Akshita", "akshita explains the number clearly", 10.0,
            ["akshita", "explains", "the", "number", "clearly"]),
        seg("Craig", "craig asks where the rest comes from", 20.0,
            ["craig", "asks", "where", "the", "rest", "comes", "from"]),
    ]}


def _override(anchor="explains the number", prop="Pan", value=-35.0,
              reason="captain: akshita sits left of the frame edge"):
    edit = {"kind": "transform_override", "anchor_phrase": anchor,
            "property": prop, "value": value, "reason": reason}
    if prop in ("Pan", "Tilt"):
        edit["recorded_draw_gain"] = FALLBACK_DRAW_GAIN
    return edit


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _write_edits_file(project, edits):
    directory = project / "external"
    directory.mkdir(exist_ok=True)
    (directory / "captain_edits.json").write_text(
        json.dumps({"key": "captain_edits",
                    "source": "captain, 2026-09-10", "value": edits}),
        encoding="utf-8")


def _write_transcript(project, transcript):
    path = (project / "pipeline_output" / "scratch"
            / "timeline_transcript" / "transcript.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(transcript), encoding="utf-8")


def _span(master, speaker="Akshita"):
    clip = type("Clip", (), {})()
    clip.track_index = 1
    clip.track_type = "video"
    clip.source_file = "LC4932.MXF"
    clip.speaker = speaker
    return {"clip": clip, "source_in": 0.0, "source_out": 4.0,
            "record": 0.0, "snapped_record": 0, "track_index": 1,
            "speaker": speaker, "master": master}


class _PoolItem:
    def GetClipProperty(self, name):
        return "1920x1080" if name == "Resolution" else None


class _Item:
    """A Resolve timeline item, holding an Edit-page transform."""

    def __init__(self, pan=14.0):
        self.held = {"Pan": pan, "Tilt": 0.25,
                     "ZoomX": 2.307, "ZoomY": 2.307}
        self.sets = []

    def GetName(self):
        return "LC4932.MXF"

    def GetProperty(self, prop=None):
        return dict(self.held) if prop is None else self.held.get(prop)

    def SetProperty(self, prop, value):
        self.sets.append((prop, value))
        self.held[prop] = value
        return True

    def GetMediaPoolItem(self):
        return _PoolItem()


class _Row:
    def __init__(self, index):
        self.index = index


class _TrackPlan:
    def aroll_rows(self):
        return [_Row(1)]


class _Timeline:
    def __init__(self, items):
        self.items = items

    def GetItemListInTrack(self, kind, index):
        assert kind == "video" and index == 1
        return list(self.items)

    def GetName(self):
        return "Reel 24 - why-ai-trusts-youtube (staging)"

    def GetSetting(self, key):
        return {"timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920"}.get(key, "")


class _CurrentTimeline:
    def GetName(self):
        return "Podcast (field test)"

    def GetSetting(self, key):
        return {"timelineResolutionWidth": "3840",
                "timelineResolutionHeight": "2160"}.get(key, "")


class _ScaledReadItem(_Item):
    """Model Resolve's per-axis scaling on a non-current timeline read."""

    def __init__(self, pan=0.0, tilt=0.0, zoom=1.0):
        super().__init__(pan)
        self.held = {"Pan": pan, "Tilt": tilt,
                     "ZoomX": zoom, "ZoomY": zoom}

    def GetProperty(self, prop=None):
        scale = {"Pan": 3840 / 1080, "Tilt": 2160 / 1920}
        raw = {key: (value * scale.get(key, 1.0)
                     if value is not None else None)
               for key, value in self.held.items()}
        return raw if prop is None else raw.get(prop)

    def SetProperty(self, prop, value):
        self.sets.append((prop, value))
        self.held[prop] = value
        return True


class _HDPoolItem(_PoolItem):
    def GetClipProperty(self, name):
        return "1920x1080" if name == "Resolution" else None


# ── 1. The override validates, number-anchored ───────────────────────

def _refusal_case(name):
    """One invalid override per refusal guard (see `validate_edits`).

    Each row kills exactly its guard's mutant and no other
    (mutation probe 2026-10-02: six guards, six 1:1 kills) - the table
    is the consolidation of six one-assert tests, not a weaker one.
    """
    if name == "unknown-property":
        return _override(prop="PositionX"), "PositionX"
    if name == "non-numeric":
        edit = _override()
        edit["value"] = "left a bit"
        return edit, "number"
    if name == "nan":
        return _override(value=float("nan")), "number"
    if name == "non-positive-zoom":
        return _override(prop="ZoomX", value=0), "zoom"
    if name == "past-the-rail":
        return _override(value=5000), "3840"
    if name == "frame-field":
        edit = _override()
        edit["timeline_start"] = 5.38
        return edit, "timeline_start"
    assert name == "reasonless"
    return _override(reason="  "), "reason"


@pytest.mark.parametrize("name", [
    "unknown-property",
    "non-numeric",
    "nan",
    "non-positive-zoom",
    "past-the-rail",
    "frame-field",
    "reasonless",
])
def test_a_refused_override_names_what_refused_it(name):
    edit, fragment = _refusal_case(name)
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.validate_edits([edit])
    assert fragment in str(exc.value).lower() or fragment in str(exc.value)


# ── 2. Matching: words to placed spans ──────────────────────────────

def test_an_override_matches_the_span_speaking_its_anchor():
    spans = [_span((10.0, 14.0)), _span((20.0, 24.0), speaker="Craig")]
    matched, stale = captain_edits.match_transform_overrides(
        spans, _tx(), [_override()])
    assert stale == []
    assert len(matched) == 1
    assert matched[0]["span_index"] == 0
    assert matched[0]["property"] == "Pan"
    assert matched[0]["value"] == pytest.approx(-35.0)


def test_a_stale_override_says_which_kind_of_stale_it_is():
    """Routine (another reel's words) versus LOST (words gone everywhere);
    history in `docs/evidence/transform_override.md`."""
    spans = [_span((20.0, 24.0), speaker="Craig")]
    matched, stale = captain_edits.match_transform_overrides(
        spans, _tx(), [_override(anchor="explains the number"),
                       _override(anchor="zebras on mars")])
    assert matched == []
    by_anchor = {record["anchor_phrase"]: record for record in stale}
    elsewhere = by_anchor["explains the number"]
    assert elsewhere["scope"] == "reel"
    assert "not on this reel" in elsewhere["reason"]
    lost = by_anchor["zebras on mars"]
    assert lost["scope"] == "transcript"
    assert "LOST" in lost["reason"]
    assert captain_edits.lost_overrides(stale) == [lost]


def test_a_rebuild_that_would_overwrite_a_lost_override_says_so(
        tmp_path, capsys):
    """The LOST case must reach the lost channel (counterweight below)."""
    from library.tools import reel_build
    project = _project(tmp_path)
    _write_edits_file(project, [_override(anchor="zebras on mars",
                                          value=-35.0)])
    item = _Item(pan=14.0)
    applied = reel_build.apply_transform_overrides(
        "Reel 09", _TrackPlan(), {"1": 1}, [_span((10.0, 14.0))],
        _Timeline([item]), _tx(), str(project), 1080, 1920)
    assert applied == 0
    assert item.GetProperty("Pan") == pytest.approx(14.0)
    said = capsys.readouterr().err
    assert "IS LOST" in said and "Pan=-35" in said


def test_an_override_for_another_reel_is_not_called_lost(
        tmp_path, capsys):
    """The routine case must NOT reach the lost channel."""
    from library.tools import reel_build
    project = _project(tmp_path)
    _write_edits_file(project, [_override(anchor="explains the number")])
    reel_build.apply_transform_overrides(
        "Reel 09", _TrackPlan(), {"1": 1},
        [_span((20.0, 24.0), speaker="Craig")],
        _Timeline([_Item(pan=14.0)]), _tx(), str(project), 1080, 1920)
    said = capsys.readouterr().err
    assert "not on this reel" in said
    assert "IS LOST" not in said


def test_an_override_matching_two_spans_names_both():
    spans = [_span((10.0, 14.0)), _span((10.0, 14.0))]
    matched, stale = captain_edits.match_transform_overrides(
        spans, _tx(), [_override()])
    assert stale == []
    assert sorted(m["span_index"] for m in matched) == [0, 1]


# ── 3. The rebuild holds the recorded value, not the aim ────────────

def test_a_rebuild_reproduces_minus_35_rather_than_14(tmp_path):
    """The placement-survives-rebuild case, by return AND read-back."""
    from library.tools import reel_build
    project = _project(tmp_path)
    _write_edits_file(project, [_override()])
    item = _Item(pan=14.0)
    timeline = _Timeline([item])
    applied = reel_build.apply_transform_overrides(
        "Reel 09", _TrackPlan(), {"1": 1}, [_span((10.0, 14.0))],
        timeline, _tx(), str(project), 1080, 1920)
    assert applied == 1
    assert item.sets == [("Pan", -35.0)]
    assert item.GetProperty("Pan") == pytest.approx(-35.0)


def test_a_gain_four_capture_rebases_when_the_next_build_is_gain_one(
        tmp_path):
    from library.tools import reel_build

    project = _project(tmp_path)
    edit = _override(value=-8.75)
    edit["recorded_draw_gain"] = 4.0
    _write_edits_file(project, [edit])
    item = _Item(pan=14.0)
    applied = reel_build.apply_transform_overrides(
        "Reel 09", _TrackPlan(), {"1": 1}, [_span((10.0, 14.0))],
        _Timeline([item]), _tx(), str(project), 1080, 1920,
        draw_gain=1.0)
    assert applied == 1
    assert item.GetProperty("Pan") == pytest.approx(-35.0)
    assert item.sets == [("Pan", -35.0)]


def test_a_refused_setproperty_refuses_the_build(tmp_path, capsys):
    from library.tools import reel_build
    project = _project(tmp_path)
    _write_edits_file(project, [_override()])

    class _Refusing(_Item):
        def SetProperty(self, prop, value):
            return False

    timeline = _Timeline([_Refusing(pan=14.0)])
    with pytest.raises(reel_build.ReelBuildError) as exc:
        reel_build.apply_transform_overrides(
            "Reel 09", _TrackPlan(), {"1": 1}, [_span((10.0, 14.0))],
            timeline, _tx(), str(project), 1080, 1920)
    assert "refused" in str(exc.value).lower()


def test_an_unreadable_store_refuses_rather_than_building_past(tmp_path):
    from library.tools import reel_build
    project = _project(tmp_path)
    _write_edits_file(project, [{"kind": "transform_override"}])
    with pytest.raises(reel_build.ReelBuildError) as exc:
        reel_build.apply_transform_overrides(
            "Reel 09", _TrackPlan(), {"1": 1}, [_span((10.0, 14.0))],
            _Timeline([_Item()]), _tx(), str(project), 1080, 1920)
    assert "cannot be read" in str(exc.value)


def test_no_recorded_override_costs_nothing(tmp_path):
    from library.tools import reel_build
    project = _project(tmp_path)
    _write_edits_file(project, [{
        "kind": "caption_fix", "anchor_phrase": "the number",
        "replacement": "the figure", "reason": "captain: say figure"}])
    item = _Item(pan=14.0)
    applied = reel_build.apply_transform_overrides(
        "Reel 09", _TrackPlan(), {"1": 1}, [_span((10.0, 14.0))],
        _Timeline([item]), _tx(), str(project), 1080, 1920)
    assert applied == 0
    assert item.sets == [] and item.GetProperty("Pan") == pytest.approx(
        14.0)


def test_a_multi_property_aim_proves_the_window_once_all_hold(tmp_path):
    """A whole-shot aim is proved whole, not property by property.

    A multi-property aim (zoom plus pan plus tilt for one refused
    shot) arrives as one record per property. Proving the screen
    window after the first one fails on the three not yet set -
    measured on Reel 24, 2026-09-18, where Pan held while ZoomX was
    still 1 and the build refused before ZoomX was ever set. Each
    property is still judged by its own read-back as it lands; the
    window is proved once, on the merged hold.
    """
    from library.tools import reel_build
    project = _project(tmp_path)
    _write_edits_file(project, [
        _override(prop="ZoomX", value=2.4),
        _override(prop="ZoomY", value=2.4),
        _override(prop="Pan", value=0.0),
        _override(prop="Tilt", value=0.0),
    ])
    item = _Item()
    item.held = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0}
    applied = reel_build.apply_transform_overrides(
        "Reel 09", _TrackPlan(), {"1": 1}, [_span((10.0, 14.0))],
        _Timeline([item]), _tx(), str(project), 1080, 1920,
        screen_window=(18.0, 259.5, 1061.0, 1661.0))
    assert applied == 4
    assert item.GetProperty("ZoomX") == pytest.approx(2.4)
    assert item.GetProperty("ZoomY") == pytest.approx(2.4)
    assert [prop for prop, _ in item.sets] == [
        "ZoomX", "ZoomY", "Pan", "Tilt"]


def test_reel24_override_matches_the_opening_same_named_item_and_covers_tv(
        tmp_path, capsys):
    """Reel 24 has two LCATL0013.MXF placements, but only the opening
    wide two-shot speaks this override's anchor. Its earlier stored
    2.307 zoom left 168.8px of the current TV window uncovered. With the
    2026-09-29 build's measured draw gain of 4.0, Tilt -696.041 moves the
    1920x1080 source too far down; -174.014 matches the other punch-ins
    and covers the window. The fake Resolve reads through a 3840x2160
    current timeline so the build check must restore the vertical
    timeline units before judging either value.
    """
    from library.tools import reel_build, reel_framing, reel_look

    project = _project(tmp_path)
    anchor = "why do ai platforms love video content"
    reel = "Reel 24 - why-ai-trusts-youtube"

    def transform_records(values, reason):
        return [{"kind": "transform_override",
                 "anchor_phrase": anchor,
                 "property": prop,
                 "value": value,
                 "reason": reason,
                 "reel": reel}
                for prop, value in values]

    old_records = transform_records(
        [("Pan", 20.679), ("Tilt", -0.395),
         ("ZoomX", 2.307), ("ZoomY", 2.307)],
        "firstmate 2026-09-18 batch 4 M24 (not the captain)")
    _write_edits_file(project, old_records)

    def segment(text, start):
        words = []
        cursor = start
        for token in text.split():
            words.append({"word": token, "start": round(cursor, 3),
                          "end": round(cursor + 0.3, 3), "timed": True})
            cursor += 0.4
        return {"speaker": "Craig", "text": text,
                "timeline_start": start,
                "timeline_end": round(cursor, 3), "words": words}

    transcript = {"segments": [
        segment("why do ai platforms love video content", 10.0),
        segment("later unrelated footage plays here", 20.0),
    ]}
    opening = _span((10.0, 14.0), speaker="Craig")
    opening["clip"].source_file = "/media/LCATL0013.MXF"
    opening["source_in"] = 3943.372
    opening["source_out"] = 3947.001
    next_shot = _span((20.0, 24.0), speaker="Craig")
    next_shot["clip"].source_file = "/media/LCATL0013.MXF"
    next_shot["snapped_record"] = 1
    placements = [opening, next_shot]

    class _FourKPoolItem(_PoolItem):
        def GetClipProperty(self, name):
            return "3840x2160" if name == "Resolution" else None

    class _LCATLItem(_Item):
        def GetName(self):
            return "LCATL0013.MXF"

        def GetMediaPoolItem(self):
            return _FourKPoolItem()

    def fresh_items():
        items = [_LCATLItem(pan=0.0), _LCATLItem(pan=0.0)]
        for item in items:
            item.held = {"Pan": 0.0, "Tilt": 0.0,
                         "ZoomX": 1.0, "ZoomY": 1.0}
        return items

    # The saved values are at the legacy reference gain 1.0. Both gain
    # readings must apply this same anchor to the same delivered pixels.
    window = (56.106, 530.6365, 1022.967, 1829.827)
    old_items = fresh_items()
    with pytest.raises(reel_build.ReelBuildError) as exc:
        reel_build.apply_transform_overrides(
            reel, _TrackPlan(), {"1": 1}, placements,
            _Timeline(old_items), transcript, str(project), 1080, 1920,
            screen_window=window, draw_gain=1.0)
    assert "recorded Pan=20.679" in str(exc.value)
    assert "ZoomX=2.307" in str(exc.value)
    assert "bottom 168.8px" in str(exc.value)
    assert old_items[0].GetProperty("ZoomX") == pytest.approx(2.307)
    assert old_items[1].sets == []

    # Source measurement is center_x=.4828, center_y=.325. The recorded
    # frame still shows Craig speaking, so this manual aim resolves the
    # detector's `others=1` ambiguity without treating it as auto-punch.
    current_records = transform_records(
        [("Pan", 39.263), ("Tilt", -696.041),
         ("ZoomX", 2.1386), ("ZoomY", 2.1386)],
        "firstmate 2026-09-29 Reel 24: current TV-window aim for the "
        "opening LCATL0013.MXF shot, source 3943.372-3947.001")
    _write_edits_file(project, current_records)
    resolve_project = type("Project", (), {
        "GetCurrentTimeline": lambda self: _CurrentTimeline(),
    })()
    gain_four_items = [
        type("CrossReadLCATLItem", (_ScaledReadItem,), {
            "GetName": lambda self: "LCATL0013.MXF",
            "GetMediaPoolItem": lambda self: _HDPoolItem(),
        })(zoom=1.0),
        type("CrossReadLCATLItem", (_ScaledReadItem,), {
            "GetName": lambda self: "LCATL0013.MXF",
            "GetMediaPoolItem": lambda self: _HDPoolItem(),
        })(zoom=1.0),
    ]
    applied = reel_build.apply_transform_overrides(
        reel, _TrackPlan(), {"1": 1}, placements,
        _Timeline(gain_four_items), transcript, str(project), 1080, 1920,
        screen_window=window, draw_gain=4.0,
        resolve_project=resolve_project)
    assert applied == 4
    assert gain_four_items[0].held["ZoomX"] == pytest.approx(2.1386)
    assert gain_four_items[0].held["ZoomY"] == pytest.approx(2.1386)
    assert gain_four_items[0].held["Pan"] == pytest.approx(39.263 / 4)
    assert gain_four_items[0].held["Tilt"] == pytest.approx(-696.041 / 4)
    assert gain_four_items[1].sets == []
    picture = reel_framing.delivered_picture(
        1920, 1080, 1080, 1920, gain_four_items[0].held,
        draw_gain=4.0)
    assert reel_look.uncovered_window_edges(picture, window) == []
    assert "recorded ZoomX holds 2.1386" in capsys.readouterr().err


def test_reel24_punch_ins_and_override_share_vertical_timeline_units(
        capsys, monkeypatch):
    """The other Reel 24 shots take the same values the F12 path grades.

    Each item is on the 1080x1920 staging timeline, while the fake
    Resolve project has a 3840x2160 master current. The readback scales
    Pan/Tilt by current/target dimensions; both the generated punch-in
    check and recorded override must restore them before calculating
    picture coverage.
    """
    from types import SimpleNamespace

    from library.tools import reel_build, reel_framing, reel_look

    monkeypatch.setattr(reel_look, "window_zoom_for",
                        lambda *_args: 2.1386)

    window = (56.106, 530.6365, 1022.967, 1829.827)
    target_timeline = _Timeline([])
    resolve_project = type("Project", (), {
        "GetCurrentTimeline": lambda self: _CurrentTimeline(),
    })()
    items = []
    places = []
    for index in range(5):
        item_type = type("CrossReadLCATLItem", (_ScaledReadItem,), {
            "GetName": lambda self: "LCATL0013.MXF",
            "GetMediaPoolItem": lambda self: _HDPoolItem(),
        })
        items.append(item_type(zoom=1.0))
        place = _span((index * 4.0, (index + 1) * 4.0), speaker="Craig")
        place["clip"].source_file = "/media/LCATL0013.MXF"
        places.append(place)

    subject = SimpleNamespace(center_x=0.518, center_y=0.325,
                              others=0, detected=12, samples=12)
    aimed = reel_build.aim_picture_row(
        "Reel 24 - why-ai-trusts-youtube",
        {"punch_in": 2.3, "scale": 2.1386 / 2.3},
        window, 1080, 1920, items, places,
        measure=lambda *_: subject,
        size_of=lambda _item: (1920, 1080),
        draw_gain=4.0, timeline=target_timeline,
        resolve_project=resolve_project)

    assert aimed == 5
    assert all(item.held["Tilt"] == pytest.approx(-174.014)
               for item in items)
    assert all(item.held["ZoomX"] == pytest.approx(2.1386)
               for item in items)
    pictures = [reel_framing.delivered_picture(
        1920, 1080, 1080, 1920, item.held, draw_gain=4.0)
        for item in items]
    assert all(reel_look.uncovered_window_edges(picture, window) == []
               for picture in pictures)
    assert capsys.readouterr().err.count("Tilt -174.014") == 5


# ── 4. The write side: record, refuse, supersede ────────────────────

def test_record_creates_the_store_where_none_was_ever_written(tmp_path):
    project = _project(tmp_path)
    assert not (project / "external").exists()
    edit, action = captain_edits.record_edit(
        str(project), _override(), "captain, 2026-09-10")
    assert action == "recorded"
    assert captain_edits.load_edits(str(project)) == [edit]


@pytest.mark.parametrize("first,second,action,count", [
    # An exact duplicate is refused; a re-ruling supersedes in place;
    # a different property - or a different reel scope - is a different
    # decision and both stay in force.
    ("same", "same", "refused", 1),
    ("pan-35", "pan-40", "superseded", 1),
    ("pan", "tilt", "recorded", 2),
    ("scoped-r1", "scoped-r2", "recorded", 2),
    ("scoped", "scoped-reruled", "superseded", 1),
])
def test_record_identity_is_decided_by_property_scope_and_value(
        tmp_path, first, second, action, count):
    edits = {
        "same": _override(),
        "pan-35": _override(value=-35.0),
        "pan-40": _override(value=-40.0),
        "pan": _override(prop="Pan"),
        "tilt": _override(prop="Tilt", value=1.5),
        "scoped": _scoped(),
        "scoped-r1": _scoped(),
        "scoped-reruled": _scoped(value=-12.0,
                                  reason="captain: further left"),
        "scoped-r2": _scoped(value=-12.0, reel="Reel 02 - something-else",
                             reason="captain: reel 02 sits her right"),
    }
    project = _project(tmp_path)
    captain_edits.record_edit(str(project), edits[first])
    if action == "refused":
        with pytest.raises(captain_edits.CaptainEditError) as exc:
            captain_edits.record_edit(str(project), edits[second])
        assert "already in force" in str(exc.value)
    else:
        edit, seen = captain_edits.record_edit(str(project), edits[second])
        assert seen == action
    stored = captain_edits.load_edits(str(project))
    assert len(stored) == count
    if action == "superseded":
        assert stored == [edit]
        assert stored[0]["value"] == edits[second]["value"]


def test_record_checks_the_anchor_against_measured_speech(tmp_path):
    project = _project(tmp_path)
    _write_transcript(project, _tx())
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.record_edit(
            str(project), _override(anchor="zebras on mars"))
    assert "spoken nowhere" in str(exc.value)
    edit, _ = captain_edits.record_edit(str(project), _override())
    assert edit["anchor_phrase"] == "explains the number"


def test_record_closer_refuses_a_typo_before_it_lands(tmp_path):
    project = _project(tmp_path)
    _write_transcript(project, _tx())
    typo = {"kind": "redraw_closer",
            "anchor_phrase": "explains the number",
            "from_phrase": "craig asks where the zebras",
            "reason": "captain: typo check"}
    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.record_edit(str(project), typo)
    assert captain_edits.load_edits(str(project)) == []


def test_the_anchor_resolves_to_reel_seconds(tmp_path):
    ranges = [(10.0, 14.0), (20.0, 24.0)]
    reel, master, master_end = captain_edits.anchor_reel_time(
        ranges, _tx(), "explains the number", lead_seconds=2.0)
    assert reel == pytest.approx(2.0 + 0.4)
    assert master == pytest.approx(10.4)
    assert master_end == pytest.approx(11.5)


def test_a_twice_spoken_anchor_names_both_occurrences(tmp_path):
    ranges = [(10.0, 14.0), (20.0, 24.0)]
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.anchor_reel_time(ranges, _tx(), "the")
    assert "2 times" in str(exc.value)


def test_the_external_check_covers_the_new_kind(tmp_path):
    from library.tools import external_inputs
    from library.tools.external_inputs import ExternalStateError
    project = _project(tmp_path)
    _write_edits_file(project, [_override(
        anchor="akshita explains the number")])
    state = {"step_outputs": {"speech_sequence": {"body_sequence": [
        {"text": "akshita explains the number clearly"}]}}}
    supplied = external_inputs.load(str(project), state)
    assert "1 edit(s)" in supplied["captain_edits"].checked
    _write_edits_file(project, [_override(anchor="zebras on mars")])
    with pytest.raises(ExternalStateError):
        external_inputs.load(str(project), state)


# ── 5. The CLI: the one route ────────────────────────────────────────

def test_cli_record_then_list(tmp_path, capsys):
    project = _project(tmp_path)
    assert captain_edits.main([
        str(project), "record-transform", "--anchor", "explains the number",
        "--property", "Pan", "--value", "-35",
        "--reason", "captain: akshita sits left"]) == 0
    out, _ = capsys.readouterr()
    assert "recorded" in out and "Pan" in out
    assert captain_edits.main([str(project), "list"]) == 0
    out, _ = capsys.readouterr()
    assert "Framing" in out and "-35.0" in out


def test_cli_record_without_reason_is_refused(tmp_path, capsys):
    project = _project(tmp_path)
    assert captain_edits.main([
        str(project), "record-transform", "--anchor", "explains the number",
        "--property", "Pan", "--value", "-35",
        "--reason", "  "]) == 1
    out, _ = capsys.readouterr()
    assert "REFUSED" in out


def test_cli_capture_without_timeline_is_refused(tmp_path, capsys):
    project = _project(tmp_path)
    assert captain_edits.main([
        str(project), "capture-transform", "--reel", "9",
        "--words", "explains the number",
        "--reason", "captain: moved by hand"]) == 1
    out, _ = capsys.readouterr()
    assert "EXACT" in out


def test_cli_capture_without_proposal_is_refused(tmp_path, capsys):
    project = _project(tmp_path)
    _write_transcript(project, _tx())
    assert captain_edits.main([
        str(project), "capture-transform", "--reel", "9",
        "--timeline", "Reel 09 - your-website-is-only-20-percent",
        "--words", "explains the number",
        "--draw-gain", "1",
        "--reason", "captain: moved by hand"]) == 1
    out, _ = capsys.readouterr()
    assert "no readable reel proposal" in out
    assert captain_edits.load_edits(str(project)) == []


# ── 6. Per-reel scope: one reel's Pan on a shared shot ────────────────
#
# A shot four reels share speaks one anchor on all four; the captain's
# Pan for ONE of them is the same words with a `reel` scope. No scope
# holds everywhere, exactly as before. History in
# `docs/evidence/transform_override.md`.

def _scoped(anchor="explains the number", prop="Pan", value=-35.0,
            reel="Reel 01 - the-cta",
            reason="captain: reel 01 sits her left"):
    edit = {"kind": "transform_override", "anchor_phrase": anchor,
            "property": prop, "value": value, "reel": reel,
            "reason": reason}
    if prop in ("Pan", "Tilt"):
        edit["recorded_draw_gain"] = FALLBACK_DRAW_GAIN
    return edit


def test_an_empty_reel_scope_is_refused():
    bad = _scoped()
    bad["reel"] = "  "
    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.validate_edits([bad])


@pytest.mark.parametrize("edits_key,reel,values,stale_scope", [
    # A scoped hold lands on its reel only (naming it in the stale
    # reason elsewhere); a staging suffix is the same reel; no scope
    # holds everywhere; where both would hold the scope wins.
    ("scoped", "Reel 01 - the-cta", [-35.0], None),
    ("scoped", "Reel 02 - something-else", [], "reel"),
    ("scoped", "Reel 01 - the-cta (scratch 7) (rebuild staging)",
     [-35.0], None),
    ("unscoped", "Reel 01 - the-cta", [-35.0], None),
    ("unscoped", "Reel 02 - something-else", [-35.0], None),
    ("both", "Reel 01 - the-cta", [-35.0], "reel"),
    ("both", "Reel 02 - something-else", [-20.0], "reel"),
])
def test_scope_decides_which_reel_a_hold_lands_on(
        edits_key, reel, values, stale_scope):
    edits = {
        "scoped": [_scoped()],
        "unscoped": [_override()],
        "both": [_override(value=-20.0, reason="captain: everywhere"),
                 _scoped(value=-35.0)],
    }[edits_key]
    spans = [_span((10.0, 14.0))]
    matched, stale = captain_edits.match_transform_overrides(
        spans, _tx(), edits, reel_name=reel)
    assert [m["value"] for m in matched] == values
    if stale_scope is None:
        assert stale == []
    else:
        assert len(stale) == 1 and stale[0]["scope"] == stale_scope
        if edits_key == "scoped":
            assert "Reel 01 - the-cta" in stale[0]["reason"]


# (record-identity rows above cover the scoped store cases too:
# different scopes are different decisions, a same-reel re-ruling
# supersedes - so the two per-scope store tests live in that table.)


def test_a_scoped_hold_survives_two_rebuilds(tmp_path):
    """The scoped hold lands on its reel and stays off the other's, on
    every rebuild - by value on the placed item, not asserted."""
    from library.tools import reel_build
    project = _project(tmp_path)
    captain_edits.record_edit(str(project), _scoped(), "captain, test")
    spans = [_span((10.0, 14.0))]
    for _ in range(2):
        assert len(captain_edits.load_edits(str(project))) == 1
        item = _Item(pan=14.0)
        applied = reel_build.apply_transform_overrides(
            "Reel 01 - the-cta", _TrackPlan(), {"1": 1}, spans,
            _Timeline([item]), _tx(), str(project), 1080, 1920)
        assert applied == 1
        assert item.GetProperty("Pan") == pytest.approx(-35.0)
        other = _Item(pan=14.0)
        applied = reel_build.apply_transform_overrides(
            "Reel 02 - something-else", _TrackPlan(), {"1": 1}, spans,
            _Timeline([other]), _tx(), str(project), 1080, 1920)
        assert applied == 0
        assert other.GetProperty("Pan") == pytest.approx(14.0)


def test_cli_record_transform_on_reel_stamps_the_scope(tmp_path,
                                                       capsys):
    project = _project(tmp_path)
    assert captain_edits.main([
        str(project), "record-transform", "--anchor", "explains the number",
        "--property", "Pan", "--value", "-35",
        "--on-reel", "Reel 01 - the-cta",
        "--reason", "captain: reel 01 sits her left"]) == 0
    out, _ = capsys.readouterr()
    assert "Reel 01 - the-cta" in out
    saved = captain_edits.load_edits(str(project))[0]
    assert saved["reel"] == "Reel 01 - the-cta"
    assert saved["recorded_draw_gain"] == pytest.approx(1.0)


def test_cli_record_transform_keeps_the_source_gain(tmp_path, capsys):
    project = _project(tmp_path)
    assert captain_edits.main([
        str(project), "record-transform", "--anchor", "explains the number",
        "--property", "Tilt", "--value", "-174.01", "--draw-gain", "4",
        "--reason", "captain: measured gain-four aim"]) == 0
    assert captain_edits.load_edits(str(project))[0][
        "recorded_draw_gain"] == pytest.approx(4.0)
    out, _ = capsys.readouterr()
    assert "draw gain 4" in out


def test_describe_names_the_hold_and_its_scope(capsys):
    assert "Reel 01" in captain_edits.describe_edits([_scoped()])[0]
    unscoped = captain_edits.describe_edits([_override()])[0]
    assert "Pan" in unscoped and "-35.0" in unscoped
    assert "Reel" not in unscoped


# ── 7. The hold takes the hand value of the shot it holds ────────────

def _freeze(held_master):
    clip = type("Clip", (), {})()
    clip.track_index = 1
    clip.track_type = "video"
    clip.source_file = "reel_freeze_test.mov"
    clip.speaker = "Akshita"
    return {"clip": clip, "source_in": 0.0, "source_out": 0.8,
            "record": 4.0, "snapped_record": 96, "track_index": 1,
            "speaker": "Akshita", "master": (0.0, 0.0),
            "held_master": held_master, "freeze": True}


def test_a_hand_declared_speaker_value_reaches_the_freeze_built_from_that_clip():
    """Reels 30/31 2026-09-17: the freeze held the engine aim while the
    live picture moved; history in `docs/evidence/transform_override.md`.
    The freeze carries its tail span (`held_master`) and the match reads
    the anchor against it."""
    shot = _span((10.0, 14.0))
    hold = _freeze((10.0, 14.0))
    matched, stale = captain_edits.match_transform_overrides(
        [shot, hold], _tx(), [_override()])
    assert stale == []
    by_span = {record["span_index"]: record for record in matched}
    assert set(by_span) == {0, 1}
    assert by_span[1]["property"] == "Pan"
    assert by_span[1]["value"] == pytest.approx(-35.0)


def test_a_freeze_holding_another_clips_frame_takes_nothing():
    hold = _freeze((20.0, 24.0))
    matched, _ = captain_edits.match_transform_overrides(
        [_span((10.0, 14.0)), hold], _tx(), [_override()])
    assert [record["span_index"] for record in matched] == [0]


def test_plan_freeze_carries_the_tail_span_the_hold_names():
    from library.tools import reel_ending
    tail = _span((10.0, 14.0))
    freeze = reel_ending.plan_freeze(
        [tail], {"tail_hold": "freeze", "tail_element": "tv_power_tail"},
        24.0)
    assert freeze.held_master == (10.0, 14.0)
    place = reel_ending.freeze_placement(freeze, 24.0)
    assert place["master"] == (0.0, 0.0)
    assert place["held_master"] == (10.0, 14.0)
    assert place["freeze"] is True
