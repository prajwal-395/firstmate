"""Cutting an approved reel, bad takes removed, both speakers in sync.

The captain: "remove the bad takes out so that the timelines of the reels
are the finished cut". A retake is easy to see and hard to prove, and
every loose rule tried against the sixteen approved reels removed REAL
content - so the tests that matter are the ones pinning what must NOT be
cut.
"""

from __future__ import annotations

import pytest

from library.tools.reel_build import (
    DURATION_RATIO,
    ReelBuildError,
    keep_ranges,
    place_overlay_segments,
    placements,
    redundant_takes,
    suspected_takes,
)
from library.tools.timeline_ingest import TimelineClip


def _seg(speaker, text, start, end, uid="u"):
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": "/m/a.MXF",
            "source_start": start, "source_end": end, "resolve_item_id": uid}


def _tx(*segments):
    return {"segments": list(segments)}


AUDIT_1 = ("So last week we ran an audit on a client and their SEO team had "
           "stuffed all their keywords with H1 tags")
AUDIT_2 = ("So we ran an audit last week on a client where an SEO team "
           "stuffed all the H1 tags with keywords")


# ── What IS cut ──────────────────────────────────────────────────────

def test_a_reworded_retake_is_cut_and_the_later_take_kept():
    """A retake exists because the first was flubbed - reel 02's first
    says "stuffed all their keywords with H1 tags", which is backwards."""
    tx = _tx(_seg("Akshita", AUDIT_1, 10.0, 15.0),
             _seg("Akshita", AUDIT_2, 18.0, 22.5, "u2"))
    cuts = redundant_takes(0.0, 60.0, tx)
    assert len(cuts) == 1
    assert cuts[0].dropped_start == 10.0
    assert cuts[0].kept_start == 18.0


# ── What must NOT be cut ─────────────────────────────────────────────

def test_what_must_not_be_cut_is_not_cut():
    """Three rows the cut rule must leave alone. An answer echoes the
    question's words - cutting on vocabulary alone deletes the question."""
    tx = _tx(_seg("Craig", "what kind of content works best on AI platforms",
                  10.0, 15.0),
             _seg("Akshita", "the best content is content that answers "
                             "specific questions on AI platforms", 16.0, 21.0, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []

    # A fragment is never kept over a full line: reel 06 would have
    # dropped 4.3s to keep a 0.5s fragment.
    tx = _tx(_seg("Akshita", "it is going to start hallucinating because it is "
                             "confused about what you actually do", 10.0, 14.3),
             _seg("Akshita", "confused about what you actually do hallucinating",
                  15.0, 15.5, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []
    # DURATION_RATIO is what refuses this, and it is the guard the module
    # docstring actually claims the protection for: 4.3s against 0.5s is
    # a ratio of 8.6. `MIN_TAKE_SECONDS` was removed 2026-09-05 and this
    # case is unaffected by that, which is the point of asserting the
    # surviving guard here rather than the removed one.
    assert 4.3 / 0.5 > DURATION_RATIO

    # Everything the cut rule is unsure of becomes a MARKER.
    tx = _tx(_seg("Akshita", "make sure you are writing about that", 10.0, 13.0),
             _seg("Akshita", "make sure you are writing about why you are "
                             "better than a competitor today", 14.0, 19.0, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []
    assert suspected_takes(0.0, 60.0, tx), "a near miss must still be reported"


# ── Keep ranges and sync ─────────────────────────────────────────────

def _clip(track, speaker, tl_start, tl_end, src_in=100.0):
    return TimelineClip(
        resolve_item_id=f"{speaker}-{tl_start}", track_type="video",
        track_index=track, track_name=speaker, speaker=speaker,
        source_file="/m/a.MXF", source_in=src_in,
        source_out=src_in + (tl_end - tl_start),
        source_in_frame=int(src_in * 24), source_out_frame=int(src_in * 24) + 1,
        source_frames=100000, timeline_start=tl_start, timeline_end=tl_end,
        name="clip")


def test_both_tracks_shift_by_the_same_amount():
    """This is what stops a cut sliding one speaker against the other."""
    from library.tools.reel_build import Cut
    cut = Cut(20.0, 25.0, "d", 26.0, 31.0, "k", "Akshita", 0.9, 0.8)
    ranges = keep_ranges(0.0, 60.0, [cut])
    clips = [_clip(1, "Akshita", 0.0, 60.0), _clip(2, "Craig", 0.0, 60.0)]
    spots = placements(ranges, clips, 23.976)
    by_track = {}
    for spot in spots:
        by_track.setdefault(spot["track_index"], []).append(round(spot["record"], 3))
    assert by_track[1] == by_track[2], "the two tracks must land identically"


# ── The resolution that would otherwise be silently wrong ────────────

def test_the_reel_resolution_is_explicit_and_declared(tmp_path):
    """The reel timeline is sized EXPLICITLY to the project's DECLARED delivery
    format (vertical when nothing is declared), never a constant.
    History: docs/evidence/reel_build.md.
    """
    from library.tools.reel_build import reel_resolution

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "project.yaml").write_text("name: plain\n", encoding="utf-8")
    assert reel_resolution(str(plain)) == (1080, 1920)

    wide = tmp_path / "wide"
    wide.mkdir()
    (wide / "project.yaml").write_text(
        "name: wide\npipeline:\n  delivery_format: horizontal_1920x1080\n",
        encoding="utf-8")
    assert reel_resolution(str(wide)) == (1920, 1080)

    square = tmp_path / "square"
    square.mkdir()
    (square / "project.yaml").write_text(
        "name: square\npipeline:\n  delivery_format: square_1080x1080\n",
        encoding="utf-8")
    assert reel_resolution(str(square)) == (1080, 1080)


# ── A reel must carry BOTH speakers' audio ───────────────────────────
# Covered by `tests/test_reel_build_sop_conformance.py` (real
# `build_reel_timeline` against fake Resolve); history in
# docs/evidence/reel_build.md.


# ── The closing CTA, from anywhere in the episode ────────────────────
#
# The captain's format closes every reel on a genuinely spoken call to
# action, and this episode says about six of them in nineteen minutes.
# While a moment was ONE contiguous master window those two requirements
# could not both be met and the batch came out at three reels.  A moment
# now carries a second range - its closer - which `reel_ranges` appends
# LAST and `placements` lays down at the running offset like any other.
#
# Nothing is copied or synthesised to let six CTAs close sixteen reels:
# the same real clip is placed again, which is an ordinary editing move.


def _moment(start, end, cta=None, number=1, slug="topic"):
    from library.tools.reel_proposal import CallToAction, ReelMoment
    return ReelMoment(
        number=number, slug=slug, reason="a complete exchange",
        timeline_start=start, timeline_end=end,
        call_to_action=(CallToAction(timeline_start=cta[0],
                                     timeline_end=cta[1])
                        if cta else None))


def test_a_distant_cta_clip_lands_last_on_the_reel():
    """The done-check: build the placements and prove the CTA is the final
    clip, at the record frame the body's length puts it at.

    Craig's closer at 468.06s is EARLIER on the master than this reel's
    body at 600-660s, which is the case that cannot be expressed by
    subtracting from one window - `keep_ranges` only ever removes.
    """
    from library.tools.reel_build import placements, reel_ranges

    fps = 24000 / 1001
    body = _clip(1, "Akshita", 600.0, 660.0, src_in=600.0)
    closer = _clip(2, "Craig", 460.0, 480.0, src_in=460.0)

    ranges = reel_ranges(_moment(600.0, 660.0, cta=(468.0, 476.0)), _tx())
    spots = placements(ranges, [body, closer], fps)

    assert len(spots) == 2, "the body clip and the closer, once each"
    assert spots[0]["clip"] is body
    assert spots[-1]["clip"] is closer, "the CTA plays LAST on the reel"

    # The closer starts exactly where the body ends: no hole, no overlap.
    body_frames = (int(round(660.0 * fps)) - int(round(600.0 * fps)))
    assert spots[0]["snapped_record"] == 0
    assert spots[-1]["snapped_record"] == body_frames

    # And it plays the master seconds the plan named, not the body's.
    assert spots[-1]["source_in"] == pytest.approx(468.0, abs=1 / fps)
    assert spots[-1]["source_out"] == pytest.approx(476.0, abs=1 / fps)

    # The reel is exactly body + closer long, with nothing between them.
    reel_frames = sum(int(round(b * fps)) - int(round(a * fps))
                      for a, b in ranges)
    last_len = (int(round(spots[-1]["source_out"] * fps))
                - int(round(spots[-1]["source_in"] * fps)))
    assert spots[-1]["snapped_record"] + last_len == reel_frames


def test_a_closer_must_present_two_real_numbers():
    """A bare MagicMock answers every attribute with a truthy mock, and
    `float()` of one is 1.0 - so a stand-in that never mentioned a CTA
    read as closing on the single second 1.00-1.00, and every reel built
    through one raised "the closer runs 1.00-1.00s, under a frame"."""
    from unittest.mock import MagicMock
    from library.tools.reel_build import cta_range, reel_ranges
    stand_in = MagicMock()
    stand_in.timeline_start, stand_in.timeline_end = 10.0, 40.0
    assert cta_range(stand_in) is None
    assert reel_ranges(stand_in, _tx()) == [(10.0, 40.0)]


def test_an_unplayable_closer_is_refused_at_build_time():
    """`validate_proposal` refuses this when the proposal is WRITTEN, but
    the plan is a file the captain edits and `read_proposal` does not
    re-run validation. Without a refusal here the reel plays those
    seconds twice and `reel_time` maps them to the first copy only,
    leaving the second silently uncaptioned."""
    from library.tools.reel_build import ReelBuildError, reel_ranges
    with pytest.raises(ReelBuildError, match="play those seconds twice"):
        reel_ranges(_moment(600.0, 660.0, cta=(610.0, 620.0)), _tx())
    # A sub-frame closer is refused too, rather than dropped.
    with pytest.raises(ReelBuildError, match="under a frame"):
        reel_ranges(_moment(600.0, 660.0, cta=(468.0, 468.01)), _tx())


def test_a_finely_segmented_retake_is_cut():
    """Reel 03: a retake segmented into sub-second pieces is still scored and cut
    (the removed `MIN_TAKE_SECONDS` floor). History: docs/evidence/reel_build.md.
    """
    line = ("search didn't change the question changed and whoever AI "
            "understands best gets the answer")
    tx = _tx(_seg("Akshita", line, 10.0, 10.9),
             _seg("Akshita", line, 11.0, 12.0, "u2"))

    cuts = redundant_takes(0.0, 60.0, tx)

    assert len(cuts) == 1, cuts
    # The LATER take is kept - a retake exists because the first was
    # flubbed - so the cut removes the first.
    assert cuts[0].dropped_start == 10.0
    assert cuts[0].kept_start == 11.0


def test_intra_turn_repetition_is_cut():
    """Reel 03's span 301.2-341.3 with its three takes.
    
    The uncovered case is a single speaker turn that contains its own repetition,
    where the transcriber did not split it into two segments and no second window exists.
    """
    line = "search didn't change the question changed whoever AI understands best gets the answer"
    # Three takes inside a single segment
    seg = _seg("Akshita", f"{line} {line} {line}", 301.2, 341.3)
    
    words_list = line.split()
    words = []
    t = 301.2
    for _ in range(3):
        for w in words_list:
            words.append({"word": w, "start": t, "end": t + 0.5})
            t += 0.5
        t += 5.0  # gap
    seg["words"] = words
    
    tx = _tx(seg)
    cuts = redundant_takes(300.0, 350.0, tx)
    
    # It should cut at least something, meaning the intra-turn repetition is found
    assert len(cuts) > 0
    assert cuts[0].dropped_start == 301.2


# ── Overlay placement is video-only and judged ────────────────────────

class _FakeItem:
    def __init__(self, uid="pool-item-1"):
        self._uid = uid


class _FakePool:
    """Records what the placer asked Resolve to do."""

    def __init__(self, append_result=None):
        self.appended = []
        self.append_result = (
            append_result if append_result is not None else [{"placed": True}])
        self._root = self._Folder("Master")
        self._current = self._root
        self.imported_into = []

    class _Folder:
        def __init__(self, name):
            self._name = name
            self.subs = []

        def GetName(self):
            return self._name

        def GetClipList(self):
            return []

        def GetSubFolderList(self):
            return list(self.subs)

    class _EmptyFolder:
        """A pool with nothing in it yet.

        `place_overlay_segments` asks the pool for the file BEFORE
        importing it (`reel_build.pool_item_for`), because re-importing
        what is already there is how the field-test project's unplaced
        bin reached 1,210 items. An empty pool means every segment here
        still takes the import path these tests are about.
        """

        def __init__(self):
            self._subs = []
            self._name = "Master"

        def GetName(self):
            return self._name

        def GetClipList(self):
            return []

        def GetSubFolderList(self):
            return list(self._subs)

    def GetRootFolder(self):
        return self._root

    def GetCurrentFolder(self):
        return self._current

    def SetCurrentFolder(self, folder):
        self._current = folder
        return True

    def AddSubFolder(self, parent, name):
        folder = self._Folder(name)
        parent.subs.append(folder)
        self._current = folder
        return folder

    def ImportMedia(self, paths):
        self.imported_into.append(self._current)
        return [_FakeItem()]

    def AppendToTimeline(self, clips):
        self.appended.extend(clips)
        return self.append_result


class _FakeTimeline:
    def GetUniqueId(self):
        return "timeline-1"

    def GetName(self):
        return "fake reel"

    def GetSetting(self, key):
        return {"timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920"}.get(key, "")


class _FakeProject:
    def __init__(self, timeline):
        self._timeline = timeline
        self.set_calls = 0

    def SetCurrentTimeline(self, timeline):
        self.set_calls += 1

    def GetCurrentTimeline(self):
        return self._timeline


def _segment(path="/renders/vox_test_00.mov", start=9.092, frames=60):
    return {"overlay_path": path, "timeline_start": start,
            "timeline_end": start + frames / 23.976, "total_frames": frames}


def _placer(pool=None, segments=None, track=6):
    timeline = _FakeTimeline()
    project = _FakeProject(timeline)
    pool = pool if pool is not None else _FakePool()
    place_overlay_segments(
        pool, project, timeline, "fake reel", 24000 / 1001,
        segments if segments is not None else [_segment()],
        track, kind="semantic visual", check="F22")
    return pool, project


def test_overlay_append_is_video_only_on_the_named_track():
    """R09's first vox build placed nothing on V6: the append carried
    the overlay's silent audio stream because no mediaType was passed.
    Every other video append in reel_build passes mediaType 1, and so
    must this one - on the track index the caller named, at the reel
    frame the plan computed."""
    pool, _ = _placer()
    assert len(pool.appended) == 1
    clip = pool.appended[0]
    assert clip["mediaType"] == 1
    assert clip["trackIndex"] == 6
    assert clip["startFrame"] == 0
    assert clip["endFrame"] == 60
    assert clip["recordFrame"] == 218  # round(9.092 * 24000/1001)


def test_a_refused_append_or_import_is_raised_not_skipped():
    """AppendToTimeline returns nothing on a refusal instead of raising,
    and the old loop carried on - so the record claimed two visuals and
    the timeline carried none until F22 refused the build. A refusal
    raises here, where the cause still points at the append."""
    pool = _FakePool(append_result=[])
    with pytest.raises(ReelBuildError, match="would not place"):
        _placer(pool=pool)

    # Same shape one call earlier: an overlay file Resolve will not
    # import is a refused build, not a reel that quietly loses a visual.
    class _NoImport(_FakePool):
        def ImportMedia(self, paths):
            return []

    with pytest.raises(ReelBuildError, match="would not import"):
        _placer(pool=_NoImport())


def test_an_overlay_import_lands_in_its_declared_bin_not_in_current():
    """An overlay import lands in its declared bin (`03 - Assets`), not wherever
    CURRENT is. History: docs/evidence/reel_build.md.
    """
    from library.tools import resolve_bin_layout as bins

    pool, _ = _placer()
    assert len(pool.imported_into) == 1
    landed = pool.imported_into[0]
    assert landed.GetName() == bins.ASSETS_BIN
    tops = {f.GetName(): f for f in pool.GetRootFolder().GetSubFolderList()}
    assert list(tops) == [bins.ASSETS_BIN]


# ── The explainer render reads the project's geometry ────────────────

def test_explainer_render_forwards_the_project_folder(tmp_path, monkeypatch):
    """The semantic-visual half forwards `project_folder` so a project
    declaring `motion_graphics_overlay_geometry: tight` renders tight
    boxes; the explainer half drove the same operation without it, so
    `render_one_segment` resolved the geometry against nothing and
    every explainer rendered full canvas beside a tight master."""
    from types import SimpleNamespace

    import library.tools.explainer_plan as ex
    import library.tools.motion_graphics_plan as mg
    import library.tools.operations as operations
    import library.tools.reel_quality_bar as quality_bar
    from library.tools.reel_build import reel_explainer_segments

    captured = {}

    class _Render:
        def run(self, planned, out_dir, **kwargs):
            captured.update(kwargs)
            return {"overlay_path": "/renders/exp_test_00.mov",
                    "timeline_start": 0.0, "timeline_end": 1.0,
                    "total_frames": 24, "elements": ["title_lockup"]}

    monkeypatch.setattr(
        ex, "resolve_declaration", lambda *a, **k: {"kind": "explainer"})
    monkeypatch.setattr(
        ex, "author_explainer",
        lambda *a, **k: SimpleNamespace(
            entries=[{"element": "title_lockup"}], anchored=None,
            band=None, basis="planned"))
    monkeypatch.setattr(
        quality_bar, "played_speech", lambda *a, **k: [])
    monkeypatch.setattr(
        mg, "resolve_plan",
        lambda *a, **k: SimpleNamespace(
            moments=[{"element": "title_lockup"}], proposed=1, dropped=[]))
    monkeypatch.setattr(
        mg, "plan_segments",
        lambda *a, **k: [{"index": 0, "props": {}}])
    monkeypatch.setattr(operations, "get", lambda name: _Render())
    monkeypatch.setattr(ex, "measure_render", lambda *a, **k: None)

    moment = SimpleNamespace(number=9, timeline_name="Reel 09")
    segments, _plan = reel_explainer_segments(
        moment, {"segments": []}, [(0.0, 10.0)], str(tmp_path),
        fps=24000 / 1001, width=1080, height=1920,
        judgement=None, brand_effect={}, timeline_name="Reel 09")

    assert len(segments) == 1
    assert captured.get("project_folder") == str(tmp_path)


# ── Tight motion graphics land positioned, not centred ───────────────

class _PlacedItem:
    """A timeline item whose transform can be read back.

    A tight overlay carries Scaling/Pan/Tilt from its box; a fake
    that reports something else is what proves the mismatch is
    reported (`library/tools/overlay_placement.py`).
    """

    def __init__(self, start, held=None, frozen=None):
        self._start = start
        self._held = dict(held or {})
        # Props Resolve silently clamps: `SetProperty` returns True
        # but the held value does not move - the captain's -3840.
        self._frozen = dict(frozen or {})
        self._held.update(self._frozen)
        self.set_calls = {}

    def GetStart(self):
        return self._start

    def GetProperty(self, prop=None):
        return (dict(self._held) if prop is None
                else self._held.get(prop))

    def SetProperty(self, prop, value):
        self.set_calls[prop] = value
        if prop not in self._frozen:
            self._held[prop] = value
        return True


class _TrackTimeline(_FakeTimeline):
    def __init__(self, items):
        self._items = items

    def GetItemListInTrack(self, kind, index):
        assert kind == "video"
        return self._items


def _tight_segment(path="/renders/vox_test_00_tight.mov"):
    seg = _segment(path=path)
    seg["geometry"] = "tight"
    seg["tight_box"] = {
        "width": 800, "height": 400,
        "placement": {"scaling": 1, "pan": 140.0, "tilt": -1720.0},
    }
    return seg


def _tight_placer(segments, track=7, placed=None, **kwargs):
    placed = placed if placed is not None else [_PlacedItem(218)]
    timeline = _TrackTimeline(placed)
    project = _FakeProject(timeline)
    pool = _FakePool()
    place_overlay_segments(
        pool, project, timeline, "fake reel", 24000 / 1001,
        segments, track, kind="semantic visual", check="F22",
        project_folder="/proj", **kwargs)
    return pool, placed


def test_a_tight_segment_is_placed_through_its_box_and_read_back(capsys):
    """A tight graphic rides the Scaling/Pan/Tilt its box computed:
    the placer SETS them on the placed item, then reads them back."""
    _, placed = _tight_placer([_tight_segment()])
    assert placed[0].set_calls == {
        "Scaling": 1, "Pan": 140.0, "Tilt": -1720.0}

    # THE READ-BACK: a placed overlay holding something other than its
    # box placement is REPORTED by name, not failed - the clip IS on the
    # timeline, and failing would trade a misplaced graphic for a missing one.
    moved = [_PlacedItem(218, frozen={"Tilt": -3840.0})]
    _tight_placer([_tight_segment()], placed=moved)
    err = capsys.readouterr().err
    assert "semantic visual" in err
    assert "Tilt" in err and "-3840" in err


