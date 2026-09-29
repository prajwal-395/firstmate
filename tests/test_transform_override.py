"""The captain's hand move survives the rebuild that would throw it away.

The captain, 2026-09-10: Akshita's clip moved by hand in Resolve to
Pan -35 from the pipeline's 14 - *"i want you to really investigate
if any of these changes actually persist and are saved"*. And earlier,
still standing: *"if i ask to remove a piece of the video and replace
it with something else, and then ask you to rebuild the timeline,
those changes should persist"*.

The store, the reader and the CTA redraw all exist
(`library/tools/captain_edits.py`, PR #857); what was missing was the
WRITE side and a placement-shaped entry. A `transform_override` is
that entry, in the SAME store - the anchor is the same stable thing
every other kind anchors to (the spoken words), only the payload
differs (a number held, not a range redrawn). The "x" the captain
moved is the API property `Pan` (the Inspector's Position X;
`PositionX` is not an API property - measured live on Reel 09).

Fail-before: `validate_edits` knows no `transform_override` kind and
`captain_edits` has no `match_transform_overrides` - every test here
errors on the kind or the attribute, not on an assertion.
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
    return {"kind": "transform_override", "anchor_phrase": anchor,
            "property": prop, "value": value, "reason": reason}


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

    def GetProperty(self, prop):
        return self.held.get(prop)

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


# ── 1. The override validates, number-anchored ───────────────────────

def test_a_transform_override_validates():
    assert len(captain_edits.validate_edits([_override()])) == 1


def test_an_unknown_property_is_refused():
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.validate_edits([_override(prop="PositionX")])
    assert "PositionX" in str(exc.value)


def test_a_non_numeric_value_is_refused():
    edit = _override()
    edit["value"] = "left a bit"
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.validate_edits([edit])
    assert "number" in str(exc.value).lower()


def test_a_non_positive_zoom_is_refused():
    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.validate_edits([_override(prop="ZoomX", value=0)])


def test_a_pan_past_what_resolve_holds_is_refused():
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.validate_edits([_override(value=5000)])
    assert "3840" in str(exc.value)


def test_a_frame_field_is_refused_like_every_other_kind():
    edit = _override()
    edit["timeline_start"] = 5.38
    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.validate_edits([edit])


def test_a_reasonless_override_is_refused():
    edit = _override(reason="  ")
    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.validate_edits([edit])


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


def test_an_override_matching_no_span_reports_stale():
    spans = [_span((10.0, 14.0))]
    matched, stale = captain_edits.match_transform_overrides(
        spans, _tx(), [_override(anchor="zebras on mars")])
    assert matched == []
    assert len(stale) == 1 and "STALE" in stale[0]["reason"]


def test_a_stale_override_says_which_kind_of_stale_it_is():
    """Measured 2026-09-12 on `geo-podcast`: ten recorded overrides,
    every one of them matched against every reel, so a Reel 09 build
    prints EIGHT stale lines for decisions that belong to Reels 01,
    13, 26 and 28 and are working perfectly. A captain's value whose
    words were reworded away prints the ninth, in the same sentence.
    The two are opposite: one is routine, the other is the hand-set
    value gone for every future build of every reel. This is the
    separation."""
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
    """A rebuild whose anchor no longer exists plays the engine's own
    aim over the captain's number. It must SAY the number is lost -
    not print the sentence it prints for the eight overrides that
    simply belong to other reels."""
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
    """The counterweight, and the reason this is not just a louder
    print: the routine case must NOT reach the lost channel, or the
    new line is eight-ninths noise and gets ignored like the old one."""
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
    """The placement-survives-rebuild case: the item sits at the
    punch-in aim (Pan 14) and the recorded override holds -35 after
    the build pass, judged by return AND read-back."""
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
    wide two-shot speaks this override's anchor. Its stored 2.307 zoom
    was from an older aim; the old four-property transform left 168.8px
    of the current TV window uncovered. The current manually selected
    Craig aim is calculated from the opening shot's measured x/y, with
    this build's gain=1.
    Exercise the build's transform-override stage without Resolve.
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

    # Values read from the current TV 4k look and the failed build's
    # draw-gain probe. These are the delivered screen-window pixels.
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
    corrected_items = fresh_items()
    applied = reel_build.apply_transform_overrides(
        reel, _TrackPlan(), {"1": 1}, placements,
        _Timeline(corrected_items), transcript, str(project), 1080, 1920,
        screen_window=window, draw_gain=1.0)
    assert applied == 4
    assert corrected_items[0].GetProperty("ZoomX") == pytest.approx(2.1386)
    assert corrected_items[0].GetProperty("ZoomY") == pytest.approx(2.1386)
    assert corrected_items[0].GetProperty("Pan") == pytest.approx(39.263)
    assert corrected_items[0].GetProperty("Tilt") == pytest.approx(-696.041)
    assert corrected_items[1].sets == []
    picture = reel_framing.delivered_picture(
        3840, 2160, 1080, 1920,
        {key: corrected_items[0].GetProperty(key)
         for key in ("ZoomX", "ZoomY", "Pan", "Tilt")},
        draw_gain=1.0)
    assert reel_look.uncovered_window_edges(picture, window) == []
    assert "recorded ZoomX holds 2.1386" in capsys.readouterr().err


# ── 4. The write side: record, refuse, supersede ────────────────────

def test_record_creates_the_store_where_none_was_ever_written(tmp_path):
    project = _project(tmp_path)
    assert not (project / "external").exists()
    edit, action = captain_edits.record_edit(
        str(project), _override(), "captain, 2026-09-10")
    assert action == "recorded"
    assert captain_edits.load_edits(str(project)) == [edit]


def test_record_refuses_an_exact_duplicate(tmp_path):
    project = _project(tmp_path)
    captain_edits.record_edit(str(project), _override())
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.record_edit(str(project), _override())
    assert "already in force" in str(exc.value)


def test_a_re_ruling_supersedes_in_place(tmp_path):
    project = _project(tmp_path)
    captain_edits.record_edit(str(project), _override(value=-35.0))
    edit, action = captain_edits.record_edit(
        str(project), _override(value=-40.0))
    assert action == "superseded"
    assert captain_edits.load_edits(str(project)) == [edit]


def test_a_different_property_is_a_different_edit(tmp_path):
    project = _project(tmp_path)
    captain_edits.record_edit(str(project), _override(prop="Pan"))
    captain_edits.record_edit(str(project), _override(prop="Tilt",
                                                       value=1.5))
    assert len(captain_edits.load_edits(str(project))) == 2


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


def test_describe_names_the_hold_in_plain_language(capsys):
    lines = captain_edits.describe_edits([_override()])
    assert "Pan" in lines[0] and "-35.0" in lines[0]


def test_a_recorded_override_survives_the_read_and_the_rebuild(tmp_path):
    """The whole loop through the dormant store: record (the write
    side) -> load (the reader the build uses) -> match -> apply over
    a 14 aim. What the captain settled is what the rebuild holds -
    and a second rebuild holds it again rather than drifting."""
    from library.tools import reel_build
    project = _project(tmp_path)
    captain_edits.record_edit(str(project), _override(), "captain, test")
    spans = [_span((10.0, 14.0)), _span((20.0, 24.0), speaker="Craig")]
    for _ in range(2):
        edits = captain_edits.load_edits(str(project))
        matched, stale = captain_edits.match_transform_overrides(
            spans, _tx(), edits)
        assert stale == [] and len(matched) == 1
        item = _Item(pan=14.0)
        applied = reel_build.apply_transform_overrides(
            "Reel 09", _TrackPlan(), {"1": 1}, spans,
            _Timeline([item]), _tx(), str(project), 1080, 1920)
        assert applied == 1
        assert item.GetProperty("Pan") == pytest.approx(-35.0)


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
        "--reason", "captain: moved by hand"]) == 1
    out, _ = capsys.readouterr()
    assert "no readable reel proposal" in out
    assert captain_edits.load_edits(str(project)) == []


# ── 6. Per-reel scope: one reel's Pan on a shared shot ────────────────
#
# A shot four reels share speaks one anchor on all four; the captain's
# Pan for ONE of them is the same words with a `reel` scope. No scope
# holds everywhere, exactly as before.

def _scoped(anchor="explains the number", prop="Pan", value=-35.0,
            reel="Reel 01 - the-cta",
            reason="captain: reel 01 sits her left"):
    return {"kind": "transform_override", "anchor_phrase": anchor,
            "property": prop, "value": value, "reel": reel,
            "reason": reason}




def test_an_empty_reel_scope_is_refused():
    bad = _scoped()
    bad["reel"] = "  "
    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.validate_edits([bad])


def test_a_scoped_override_holds_on_its_reel_only():
    spans = [_span((10.0, 14.0))]
    matched, stale = captain_edits.match_transform_overrides(
        spans, _tx(), [_scoped()], reel_name="Reel 01 - the-cta")
    assert len(matched) == 1 and stale == []
    matched, stale = captain_edits.match_transform_overrides(
        spans, _tx(), [_scoped()], reel_name="Reel 02 - something-else")
    assert matched == [] and len(stale) == 1
    assert stale[0]["scope"] == "reel"
    assert "Reel 01 - the-cta" in stale[0]["reason"]


def test_a_staging_suffix_is_the_same_reel():
    spans = [_span((10.0, 14.0))]
    matched, stale = captain_edits.match_transform_overrides(
        spans, _tx(), [_scoped()],
        reel_name="Reel 01 - the-cta (scratch 7) (rebuild staging)")
    assert len(matched) == 1 and stale == []


def test_an_unscoped_override_still_holds_everywhere():
    """The failing input the scope exists to end: the same anchor
    with no `reel` matches on both reels' builds - which is why one
    reel's Pan was inexpressible before the scope."""
    spans = [_span((10.0, 14.0))]
    for reel in ("Reel 01 - the-cta", "Reel 02 - something-else"):
        matched, stale = captain_edits.match_transform_overrides(
            spans, _tx(), [_override()], reel_name=reel)
        assert len(matched) == 1 and stale == []


def test_the_scope_wins_where_both_would_hold():
    """A scoped narrowing and the general decision coexist: on the
    scoped reel only the narrowing holds that property on that span;
    on every other reel the general one still does."""
    edits = [_override(value=-20.0, reason="captain: everywhere"),
             _scoped(value=-35.0)]
    spans = [_span((10.0, 14.0))]
    matched, _ = captain_edits.match_transform_overrides(
        spans, _tx(), edits, reel_name="Reel 01 - the-cta")
    assert [m["value"] for m in matched] == [-35.0]
    matched, _ = captain_edits.match_transform_overrides(
        spans, _tx(), edits, reel_name="Reel 02 - something-else")
    assert [m["value"] for m in matched] == [-20.0]




def test_two_reels_rulings_are_two_edits(tmp_path):
    """Reel 02's Pan for the shared shot must not supersede Reel
    01's: different scopes are different decisions, and both stay in
    force."""
    project = _project(tmp_path)
    captain_edits.record_edit(str(project), _scoped(), "captain, test")
    other = _scoped(value=-12.0, reel="Reel 02 - something-else",
                    reason="captain: reel 02 sits her right")
    edit, action = captain_edits.record_edit(
        str(project), other, "captain, test")
    assert action == "recorded"
    assert len(captain_edits.load_edits(str(project))) == 2


def test_a_same_reel_reruling_supersedes_in_place(tmp_path):
    project = _project(tmp_path)
    captain_edits.record_edit(str(project), _scoped(), "captain, test")
    edit, action = captain_edits.record_edit(
        str(project), _scoped(value=-12.0,
                              reason="captain: further left"),
        "captain, test")
    assert action == "superseded"
    assert captain_edits.load_edits(str(project))[0]["value"] == -12.0


def test_a_scoped_hold_survives_two_rebuilds(tmp_path):
    """The whole loop twice: the scoped hold lands on its reel's
    build and stays off the other's, on every rebuild - by value on
    the placed item, not asserted."""
    from library.tools import reel_build
    project = _project(tmp_path)
    captain_edits.record_edit(str(project), _scoped(), "captain, test")
    spans = [_span((10.0, 14.0))]
    for _ in range(2):
        edits = captain_edits.load_edits(str(project))
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
    assert captain_edits.load_edits(str(project))[0]["reel"] == \
        "Reel 01 - the-cta"


def test_describe_names_the_scope(capsys):
    assert "Reel 01" in captain_edits.describe_edits([_scoped()])[0]
    assert "Reel" not in captain_edits.describe_edits([_override()])[0]


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
    """Reels 30 and 31, 2026-09-17: the live speaker moved to Pan -26
    while the freeze held the engine aim (-12.00, -2.24) - the held
    frame jumped against the live picture in front of it. A freeze
    speaks nothing, so its own master span is empty and no word anchor
    could name it; the value arrived only through the build-time copy
    from whatever played before it. The freeze placement now carries
    the tail span it was held from (`held_master`), and the match reads
    the anchor against that - the declaration names the hold directly,
    on top of the copy that already runs."""
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
