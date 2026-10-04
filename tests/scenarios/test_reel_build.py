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
import json
from library.tools.reel_build import (
    build_reel_timeline,
    resolve_reel_program_channels,
)
from library.tools.resolve_transform import FALLBACK_DRAW_GAIN
from library.tools.timeline_conformance import (
    verify_timeline,
)
from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeTimeline,
    make_project,
)


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
    tx = _tx(_seg("SpeakerOne", AUDIT_1, 10.0, 15.0),
             _seg("SpeakerOne", AUDIT_2, 18.0, 22.5, "u2"))
    cuts = redundant_takes(0.0, 60.0, tx)
    assert len(cuts) == 1
    assert cuts[0].dropped_start == 10.0
    assert cuts[0].kept_start == 18.0


# ── What must NOT be cut ─────────────────────────────────────────────

def test_what_must_not_be_cut_is_not_cut():
    """Three rows the cut rule must leave alone. An answer echoes the
    question's words - cutting on vocabulary alone deletes the question."""
    tx = _tx(_seg("SpeakerTwo", "what kind of content works best on AI platforms",
                  10.0, 15.0),
             _seg("SpeakerOne", "the best content is content that answers "
                             "specific questions on AI platforms", 16.0, 21.0, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []

    # A fragment is never kept over a full line: reel 06 would have
    # dropped 4.3s to keep a 0.5s fragment.
    tx = _tx(_seg("SpeakerOne", "it is going to start hallucinating because it is "
                             "confused about what you actually do", 10.0, 14.3),
             _seg("SpeakerOne", "confused about what you actually do hallucinating",
                  15.0, 15.5, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []
    # DURATION_RATIO is what refuses this, and it is the guard the module
    # docstring actually claims the protection for: 4.3s against 0.5s is
    # a ratio of 8.6. `MIN_TAKE_SECONDS` was removed 2026-09-05 and this
    # case is unaffected by that, which is the point of asserting the
    # surviving guard here rather than the removed one.
    assert 4.3 / 0.5 > DURATION_RATIO

    # Everything the cut rule is unsure of becomes a MARKER.
    tx = _tx(_seg("SpeakerOne", "make sure you are writing about that", 10.0, 13.0),
             _seg("SpeakerOne", "make sure you are writing about why you are "
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
    cut = Cut(20.0, 25.0, "d", 26.0, 31.0, "k", "SpeakerOne", 0.9, 0.8)
    ranges = keep_ranges(0.0, 60.0, [cut])
    clips = [_clip(1, "SpeakerOne", 0.0, 60.0), _clip(2, "SpeakerTwo", 0.0, 60.0)]
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
# Covered by `tests/scenarios/test_reel_build.py` (real
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

    SpeakerTwo's closer at 468.06s is EARLIER on the master than this reel's
    body at 600-660s, which is the case that cannot be expressed by
    subtracting from one window - `keep_ranges` only ever removes.
    """
    from library.tools.reel_build import placements, reel_ranges

    fps = 24000 / 1001
    body = _clip(1, "SpeakerOne", 600.0, 660.0, src_in=600.0)
    closer = _clip(2, "SpeakerTwo", 460.0, 480.0, src_in=460.0)

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
    tx = _tx(_seg("SpeakerOne", line, 10.0, 10.9),
             _seg("SpeakerOne", line, 11.0, 12.0, "u2"))

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
    seg = _seg("SpeakerOne", f"{line} {line} {line}", 301.2, 341.3)
    
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


def _tight_placer(segments, track=7, placed=None, project_folder="", **kwargs):
    placed = placed if placed is not None else [_PlacedItem(218)]
    timeline = _TrackTimeline(placed)
    project = _FakeProject(timeline)
    pool = _FakePool()
    from library.tools.transform_write_log import write_scope

    with write_scope(project="reel-build-test",
                     timeline_name="fake reel",
                     timeline_id="fake-reel-id",
                     run_id="reel-build-test-run"):
        place_overlay_segments(
            pool, project, timeline, "fake reel", 24000 / 1001,
            segments, track, kind="semantic visual", check="F22",
            project_folder=project_folder, **kwargs)
    return pool, placed


def test_a_tight_segment_is_placed_through_its_box_and_read_back(
        capsys, tmp_path):
    """A tight graphic rides the Scaling/Pan/Tilt its box computed:
    the placer SETS them on the placed item, then reads them back."""
    _, placed = _tight_placer([_tight_segment()],
                              project_folder=str(tmp_path))
    assert placed[0].set_calls == {
        "Scaling": 1, "Pan": 140.0, "Tilt": -1720.0}

    # THE READ-BACK: a placed overlay holding something other than its
    # box placement is REPORTED by name, not failed - the clip IS on the
    # timeline, and failing would trade a misplaced graphic for a missing one.
    moved = [_PlacedItem(218, frozen={"Tilt": -3840.0})]
    _tight_placer([_tight_segment()], placed=moved,
                  project_folder=str(tmp_path))
    err = capsys.readouterr().err
    assert "semantic visual" in err
    assert "Tilt" in err and "-3840" in err


# --------------------------------------------------------------------------
# From test_reel_build_sop_conformance.py
#
# The reel builder obeys the timeline SOP, and the verifier reads it back.
#
# Defects covered here (fake Resolve, no live connection):
#   1. two speakers collapsed onto one video row with mixed audio rows -
#      a-roll gets one video row per angle and speech one audio row per
#      angle, named from the master's own rows;
#   2. the MXF program stream never explicitly selected - exactly one
#      recorded program stream per source reaches the timeline, and a
#      stray is deleted on the spot and recorded;
#   3. nothing linked - picture links to its speech, and a caption whose
#      span falls inside a speech span joins that group in ONE call
#      (linking is exclusive, not additive);
#   4. unnamed and empty rows - every row is named from the plan, and a
#      row whose placements all fail is deleted, never kept blank.
#
# `library/tools/timeline_conformance.py` reads a built timeline back
# against the plan the build recorded. It is deterministic and it can
# fail, so it is a real gate.

def _media_properties(path):
    low_resolution_sources = ("LCATL0013", "LC4932", "reel_freeze_")
    resolution = (
        "1920x1080"
        if any(name in path for name in low_resolution_sources)
        else "3840x2160"
    )
    return {"Resolution": resolution, "FPS": "23.976"}


def _pool_item(path):
    item = FakeMediaPoolItem(path.rsplit("/", 1)[-1])
    item.SetClipProperty("File Path", path)
    item.SetClipProperty("FPS", "23.976")
    item.SetClipProperty("Resolution", _media_properties(path)["Resolution"])
    return item


class FakeMoment:
    timeline_name = "Reel 99 - sop-proof"
    timeline_start = 0.0
    timeline_end = 20.0
    number = 99
    call_to_action = None


def _clip_2(
    track_type, index, track_name, speaker, source, tl_start, tl_end, src_in=100.0
):
    return TimelineClip(
        resolve_item_id=f"{track_name}-{tl_start}",
        track_type=track_type,
        track_index=index,
        track_name=track_name,
        speaker=speaker,
        source_file=source,
        source_in=src_in,
        source_out=src_in + (tl_end - tl_start),
        source_in_frame=int(src_in * 24),
        source_out_frame=int(src_in * 24) + 1,
        source_frames=100000,
        timeline_start=tl_start,
        timeline_end=tl_end,
        name="clip",
    )


def _master_clips():
    return [
        _clip_2("video", 1, "SpeakerOne", "SpeakerOne", "/m/speakerone.MXF", 0.0, 10.0),
        _clip_2("video", 2, "SpeakerTwo", "SpeakerTwo", "/m/speakertwo.MXF", 10.0, 20.0),
        _clip_2("audio", 1, "SpeakerOne CH1", "SpeakerOne", "/m/speakerone.MXF", 0.0, 10.0),
        _clip_2("audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "/m/speakertwo.MXF", 10.0, 20.0),
    ]


def _world(audio_channels=(1,), fail_paths=()):
    timeline = FakeTimeline()
    paths = ["/m/speakerone.MXF", "/m/speakertwo.MXF", "/m/cap.mov", "/m/sem.mov"]
    project = make_project(width=1080, height=1920, frame_rate=23.976)
    pool = project.GetMediaPool()
    pool.next_timeline = timeline
    pool.audio_channels = tuple(audio_channels)
    pool.import_failures = set(fail_paths)
    pool.media_properties = {path: _media_properties(path) for path in paths}
    pool.ImportMedia([path for path in paths if path not in pool.import_failures])
    return timeline, pool, project


def _transcript():
    return {"segments": []}


def _caption(start, end, name="cap"):
    return {
        "overlay_path": "/m/cap.mov",
        "timeline_start": start,
        "timeline_end": end,
        "source_in_frame": 0,
        "segment_id": name,
    }


def _semantic(start, total, name="sem"):
    return {
        "overlay_path": "/m/sem.mov",
        "timeline_start": start,
        "total_frames": total,
    }


def _build(
    timeline,
    pool,
    project,
    clips,
    captions=(),
    semantic=(),
    look=None,
    program_channels=None,
    edit_ledger_rows=None,
    draw_gain=FALLBACK_DRAW_GAIN,
):
    return build_reel_timeline(
        project,
        FakeMoment(),
        clips,
        list(captions),
        23.976,
        1080,
        1920,
        "/tmp/no-such-project",
        _transcript(),
        look=look,
        semantic_segments=list(semantic) or None,
        master_timeline=None,
        program_channels=program_channels,
        edit_ledger_rows=edit_ledger_rows,
        draw_gain=draw_gain,
    )


def test_fresh_reel_resolution_is_set_before_it_becomes_current(monkeypatch):
    """Model the Fusion render-lock race against the Resolve double.

    Resolve hung when these writes landed after the new timeline had
    become current. Refuse that ordering here so the real reel builder
    proves it sizes the timeline before the guarded cursor move.
    """
    timeline, pool, project = _world()
    keys = ("useCustomSettings", "timelineResolutionWidth",
            "timelineResolutionHeight")
    writes = []
    original_set_setting = FakeTimeline.SetSetting

    def refuse_if_current(target, key, value):
        if key in keys:
            assert not target._is_current, (
                f"{key} was set after {target.GetName()} became current")
            writes.append((key, value))
        return original_set_setting(target, key, value)

    monkeypatch.setattr(FakeTimeline, "SetSetting", refuse_if_current)

    _build(timeline, pool, project, _master_clips(),
           program_channels={"1": 1, "2": 1})

    assert writes == [("useCustomSettings", "1"),
                      ("timelineResolutionWidth", "1080"),
                      ("timelineResolutionHeight", "1920")]
    assert project.GetCurrentTimeline() is timeline


def test_a_timeline_setting_timeout_refuses_the_build_by_name(monkeypatch):
    from library.tools import resolve_deadline

    timeline, pool, project = _world()

    def timeout(*_args, **_kwargs):
        raise resolve_deadline.ResolveCallTimeout(
            "timeline SetSetting timelineResolutionWidth did not return")

    monkeypatch.setattr(resolve_deadline, "apply_timeline_resolution",
                        timeout)

    with pytest.raises(ReelBuildError,
                       match="Reel 99 - sop-proof: timeline SetSetting"):
        _build(timeline, pool, project, _master_clips(),
               program_channels={"1": 1, "2": 1})

    assert project.GetCurrentTimeline() is None


def test_reel24_build_uses_one_units_conversion_for_punches_and_override(
    tmp_path, monkeypatch
):
    """The real timeline builder proves Reel 24 transforms offline.

    The opening two-shot has no automatic face aim, so its word-anchored
    transform override is the only thing that can cover the TV window.
    Five later shots use the normal punch-in path. The real
    ``build_reel_timeline`` runs against fake Resolve objects; no Resolve
    connection, project, media or rendered asset is used.
    """
    from types import SimpleNamespace

    from library.tools import reel_look, tv_frame
    from library.tools.project_layout import ProjectLayout

    reel = "Reel 24 - why-ai-trusts-youtube"
    source = "/media/LCATL0013.MXF"
    window = (56.106, 530.6365, 1022.967, 1829.827)
    monkeypatch.setattr(
        tv_frame, "screen_window_rect", lambda *_args, **_kwargs: window
    )
    monkeypatch.setattr(
        reel_look, "frame_overlay_segments", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        reel_look, "frame_properties", lambda *_args, **_kwargs: {"ZoomX": 1.0}
    )
    monkeypatch.setattr(
        "library.tools.reel_post_header.plan_for_reel",
        lambda *_args, **_kwargs: SimpleNamespace(segments=[], as_dict=dict),
    )

    subject = SimpleNamespace(
        center_x=0.518, center_y=0.325, others=0, detected=12, samples=12
    )
    monkeypatch.setattr(
        "library.tools.reel_build._recorded_first_measure",
        lambda _project: (
            lambda _source, source_in, _source_out: (
                None if source_in < 3944.0 else subject
            )
        ),
    )

    def _segment(text, start):
        words = []
        cursor = start
        for token in text.split():
            words.append(
                {"word": token, "start": cursor, "end": cursor + 0.2, "timed": True}
            )
            cursor += 0.25
        return {
            "speaker": "SpeakerTwo",
            "text": text,
            "timeline_start": start,
            "timeline_end": start + 4.0,
            "words": words,
        }

    phrases = [
        "why do ai platforms love video content",
        "later shot two has a different sentence",
        "later shot three carries its own sentence",
        "later shot four ends with another sentence",
        "later shot five carries a different thought",
        "later shot six finishes the thought",
    ]
    transcript = {
        "segments": [_segment(text, index * 4.0) for index, text in enumerate(phrases)]
    }
    master_clips = []
    source_files = [
        source,
        "/media/LC4932.MXF",
        "/media/LC4932.MXF",
        "/media/LC4932.MXF",
        "/media/reel_freeze_1b8ac2919c.mov",
        source,
    ]
    for index, source_file in enumerate(source_files):
        start, end = index * 4.0, (index + 1) * 4.0
        source_in = 3943.372 + index * 4.0
        master_clips.extend(
            [
                _clip_2(
                    "video",
                    1,
                    "SpeakerTwo",
                    "SpeakerTwo",
                    source_file,
                    start,
                    end,
                    src_in=source_in,
                ),
                _clip_2(
                    "audio",
                    1,
                    "SpeakerTwo CH1",
                    "SpeakerTwo",
                    source_file,
                    start,
                    end,
                    src_in=source_in,
                ),
            ]
        )
    moment = SimpleNamespace(
        number=24,
        slug="why-ai-trusts-youtube",
        timeline_name=reel + " (rebuild staging)",
        timeline_start=0.0,
        timeline_end=24.0,
        call_to_action=None,
    )

    def _build(gain, suffix):
        project_folder = tmp_path / suffix
        project_folder.mkdir()
        ProjectLayout(str(project_folder)).ensure()
        edit_path = (project_folder / "external" / "declarations" /
                     "captain_edits.json")
        edit_path.parent.mkdir(parents=True, exist_ok=True)
        edits = [
            {
                "kind": "transform_override",
                "anchor_phrase": phrases[0],
                "property": prop,
                "value": value,
                "reason": "offline Reel 24 regression",
                "reel": reel,
            }
            for prop, value in (
                ("Pan", 39.263),
                ("Tilt", -696.041),
                ("ZoomX", 2.1386),
                ("ZoomY", 2.1386),
            )
        ]
        edit_path.write_text(
            json.dumps({"key": "captain_edits", "source": "test", "value": edits}),
            encoding="utf-8",
        )

        timeline = FakeTimeline(frame_rate="23.976")
        project = make_project(width=1080, height=1920, frame_rate=23.976)
        pool = project.GetMediaPool()
        pool.next_timeline = timeline
        pool.media_properties = {
            path: _media_properties(path) for path in set(source_files)
        }
        pool.ImportMedia(sorted(set(source_files)))
        return (
            build_reel_timeline(
                project,
                moment,
                master_clips,
                [],
                23.976,
                1080,
                1920,
                str(project_folder),
                transcript,
                look={
                    "asset": "frame.png",
                    "punch_in": 2.3,
                    "scale": 2.1386 / 2.3,
                    "power": {},
                    "origin": "offline regression",
                },
                program_channels={"1": 1},
                ranges=[(0.0, 24.0)],
                edit_ledger_rows=[],
                draw_gain=gain,
            ),
            timeline,
        )

    built = {}
    for gain in (1.0, 4.0):
        record, timeline = _build(gain, f"reel24-legacy-override-gain-{gain:g}")
        picture_items = timeline.GetItemListInTrack("video", 1)
        assert len(picture_items) == 6
        built[gain] = [dict(item.GetProperty()) for item in picture_items]
        assert record["motion_coverage"] == []

    # The same legacy 1.0-reference values cover the opening at both
    # measured gains. The five automatic punch-ins also keep their
    # pixel aims: only the raw Resolve Pan/Tilt values scale by 1/gain.
    assert built[1.0][0]["Pan"] == pytest.approx(39.263)
    assert built[1.0][0]["Tilt"] == pytest.approx(-696.041)
    assert built[4.0][0]["Pan"] == pytest.approx(39.263 / 4)
    assert built[4.0][0]["Tilt"] == pytest.approx(-696.041 / 4)
    for one, four in zip(built[1.0], built[4.0]):
        assert four["Pan"] == pytest.approx(one["Pan"] / 4, abs=0.001)
        assert four["Tilt"] == pytest.approx(one["Tilt"] / 4, abs=0.001)
        assert four["ZoomX"] == pytest.approx(one["ZoomX"])


# ── Angles come from the master's own picture rows ──


def test_an_unresolvable_input_refuses_before_creating():
    timeline, pool, project = _world()
    with pytest.raises(ReelBuildError, match="no recorded program stream"):
        _build(timeline, pool, project, _master_clips())
    assert project.GetTimelineCount() == 0, (
        "the refusal must fire before a timeline exists"
    )
    assert pool.next_timeline is timeline

    # A grade row whose declared asset vanished refuses before creation
    # too, rather than leaving a half-built reel without that look.
    timeline, pool, project = _world()
    grade = {
        "op": "grade",
        "anchor": {"kind": "reel"},
        "params": {
            "drx": "missing.drx",
            "provenance": {
                "source": "Resolve export",
                "authorised_by": "captain",
                "licence": "captain's own asset",
            },
        },
        "stated_by": "requester",
        "reason": "apply the look",
    }

    with pytest.raises(
        ReelBuildError, match="grade cannot be resolved before the timeline"
    ):
        _build(
            timeline,
            pool,
            project,
            _master_clips(),
            program_channels={"1": 1, "2": 1},
            edit_ledger_rows=[grade],
        )
    assert project.GetTimelineCount() == 0
    assert pool.next_timeline is timeline


def test_caption_import_failure_refuses_instead_of_dropping_the_card(monkeypatch):
    """A rendered caption with no pool item must stop the reel build.

    Reel 17's staging build rendered the cards, but Resolve returned no
    media-pool item for most of them. The caption loop logged the failed
    import and continued, leaving those planned cards absent until F14
    found them on the timeline. This drives the actual timeline builder
    against the stub Resolve objects above and pins the refusal at the
    import that failed.
    """
    import library.tools.reel_placed_assets as placed_assets

    caption_path = "/m/new-caption.mov"
    timeline, pool, project = _world(fail_paths=(caption_path,))
    monkeypatch.setattr(placed_assets, "assert_placeable", lambda *_: None)

    with pytest.raises(ReelBuildError, match="would not import.*new-caption"):
        _build(
            timeline,
            pool,
            project,
            _master_clips(),
            captions=[
                {
                    "overlay_path": caption_path,
                    "timeline_start": 0.0,
                    "timeline_end": 1.0,
                    "source_in_frame": 0,
                    "segment_id": "sub_speakerone_source-clip_0-1000_abcdef12",
                }
            ],
            program_channels={"1": 1, "2": 1},
        )


def test_caption_placement_failure_refuses_instead_of_dropping_the_card(monkeypatch):
    import library.tools.reel_placed_assets as placed_assets
    from library.tools import overlay_placement, reel_build

    caption_path = "/m/caption.mov"
    timeline, pool, project = _world()
    monkeypatch.setattr(placed_assets, "assert_placeable", lambda *_: None)
    monkeypatch.setattr(
        reel_build,
        "import_pool_item",
        lambda _pool, path, *_args, **_kwargs: _pool_item(path),
    )
    monkeypatch.setattr(
        overlay_placement,
        "place_overlay_segment",
        lambda *_args, **_kwargs: (False, "stub placement refusal"),
    )

    with pytest.raises(ReelBuildError, match="stub placement refusal"):
        _build(
            timeline,
            pool,
            project,
            _master_clips(),
            captions=[
                {
                    "overlay_path": caption_path,
                    "timeline_start": 0.0,
                    "timeline_end": 1.0,
                    "source_in_frame": 0,
                    "segment_id": "sub_speakerone_source-clip_0-1000_abcdef12",
                }
            ],
            program_channels={"1": 1, "2": 1},
        )


# ── The three defects ──


def test_two_angles_get_two_picture_rows_and_two_named_speech_rows():
    """Defect 3: each speaker their own video AND audio row, named from
    the master - never "Video 1" / "Audio 2"."""
    timeline, pool, project = _world()
    _build(timeline, pool, project, _master_clips(), program_channels={"1": 1, "2": 1})
    assert timeline.GetTrackCount("video") == 2
    assert timeline.GetTrackCount("audio") == 2
    assert timeline.GetTrackName("video", 1) == "SpeakerOne"
    assert timeline.GetTrackName("video", 2) == "SpeakerTwo"
    assert timeline.GetTrackName("audio", 1) == "SpeakerOne CH1"
    assert timeline.GetTrackName("audio", 2) == "SpeakerTwo CH1"


def test_captain_override_window_check_uses_the_builds_measured_draw_gain(monkeypatch):
    """The override recheck must use the same 1.0 gain as the placed aim.

    Geo Podcast measured 1.0 while the machine fallback is 2.0. Using
    that fallback only for the captain's Pan=-8 recheck doubles the
    predicted vertical shift and falsely refuses a picture that covers
    the TV window at the measured gain.
    """
    from library.tools import reel_build

    received = {}

    def capture_override_recheck(*args, **kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(
        reel_build, "apply_transform_overrides", capture_override_recheck
    )
    timeline, pool, project = _world()

    record = _build(
        timeline,
        pool,
        project,
        _master_clips(),
        program_channels={"1": 1, "2": 1},
        draw_gain=1.0,
    )

    assert received["draw_gain"] == pytest.approx(1.0)
    v1 = timeline.GetItemListInTrack("video", 1)
    a1 = timeline.GetItemListInTrack("audio", 1)
    v2 = timeline.GetItemListInTrack("video", 2)
    a2 = timeline.GetItemListInTrack("audio", 2)
    assert len(v1) == len(a1) == 1 and len(v2) == len(a2) == 1
    assert "speakerone" in v1[0].GetName() and "speakertwo" in v2[0].GetName()
    assert "speakerone" in a1[0].GetName() and "speakertwo" in a2[0].GetName()
    assert record["track_plan"]["material"]["angles"][0]["label"] == "SpeakerOne"


def test_declared_angle_plan_limits_picture_rows_but_keeps_all_speech():
    """An E5 camera plan must drive picture placement independently of
    speech placement, or the reel keeps copying every master camera row."""
    timeline, pool, project = _world()
    clips = [
        _clip_2("video", 1, "SpeakerOne", "SpeakerOne", "/m/speakerone.MXF", 0.0, 20.0),
        _clip_2("video", 2, "SpeakerTwo", "SpeakerTwo", "/m/speakertwo.MXF", 0.0, 20.0),
        _clip_2("audio", 1, "SpeakerOne CH1", "SpeakerOne", "/m/speakerone.MXF", 0.0, 20.0),
        _clip_2("audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "/m/speakertwo.MXF", 0.0, 20.0),
    ]
    row = {
        "op": "angle_plan",
        "anchor": {"kind": "reel"},
        "reel": FakeMoment.timeline_name,
        "params": {"camera": "SpeakerOne", "min_shot_seconds": 3, "lead_frames": 0},
        "stated_by": "requester",
        "reason": "stay on the host",
    }
    record = _build(
        timeline,
        pool,
        project,
        clips,
        program_channels={"1": 1, "2": 1},
        edit_ledger_rows=[row],
    )
    assert timeline.GetTrackCount("video") == 1
    assert timeline.GetTrackCount("audio") == 2
    assert timeline.GetTrackName("video", 1) == "SpeakerOne"
    assert timeline.GetTrackName("audio", 1) == "SpeakerOne CH1"
    assert timeline.GetTrackName("audio", 2) == "SpeakerTwo CH1"
    assert record["angle_plan"]["declared"] is True
    assert record["angle_plan"]["picture_placements"] == 1


def test_non_program_streams_are_deleted_on_the_spot_and_recorded():
    """Defect 1: the MXF's four streams reach placement, and only the
    recorded program stream stays - the rest are deleted and said.

    The spill is the live shape, not the return value's: the append
    returns the program item while a non-program copy lands on the
    next audio row. Enforcement reads the rows back, so the copy is
    found whatever the call admitted to."""
    timeline, pool, project = _world(audio_channels=(1, 2, 3, 4))
    record = _build(
        timeline, pool, project, _master_clips(), program_channels={"1": 1, "2": 1}
    )
    enforcement = record["stream_enforcement"]
    assert enforcement["checked"] == 8, "four streams over two placements"
    assert len(enforcement["deleted"]) == 6
    assert {d["row"] for d in enforcement["deleted"]} == {1, 2}, (
        "strays land on both rows and both are swept"
    )
    assert {tuple(sorted(d["placed_channels"])) for d in enforcement["deleted"]} == {
        (2,),
        (3,),
        (4,),
    }
    for index in (1, 2):
        items = timeline.GetItemListInTrack("audio", index)
        assert len(items) == 1
        import json as _json

        mapping = _json.loads(items[0].GetSourceAudioChannelMapping())
        assert mapping["track_mapping"]["1"]["channel_idx"] == [1], (
            "no stray stream survives on a speech row"
        )


def test_picture_links_to_speech_in_one_call_per_pair():
    """Defect 2: picture and speech travel together - one link call per
    pair, read back."""
    timeline, pool, project = _world()
    record = _build(
        timeline, pool, project, _master_clips(), program_channels={"1": 1, "2": 1}
    )
    pair_calls = [c for c in timeline.link_calls if len(c[0]) == 2 and c[1]]
    assert len(pair_calls) == 2
    assert len(record["link_groups"]) == 2
    for row in (1, 2):
        for item in timeline.GetItemListInTrack(
            "video", row
        ) + timeline.GetItemListInTrack("audio", row):
            assert item.GetLinkedItems(), (
                f"every a-roll item is linked, found {item.GetName()} alone"
            )


def test_seven_frame_audio_lead_links_same_angle_a_roll():
    """Reel 11's source-edge offset still links picture and speech.

    SpeakerTwo's speech starts seven frames before his picture item. The
    placement entry point must join the overlapping items from the
    same angle despite their different start frames.
    """
    from library.tools.timeline_layout import TrackPlan, TrackSpec

    timeline, pool, project = _world()
    clips = _master_clips()
    clips[-1] = _clip_2("audio", 2, "SpeakerTwo CH1", "SpeakerTwo", "/m/speakertwo.MXF", 9.7, 20.0)
    record = _build(timeline, pool, project, clips, program_channels={"1": 1, "2": 1})
    raw = record["track_plan"]
    plan = TrackPlan(
        video_tracks=[TrackSpec(**row) for row in raw["video_tracks"]],
        audio_tracks=[TrackSpec(**row) for row in raw["audio_tracks"]],
        material=raw.get("material", {}),
    )

    picture = timeline.GetItemListInTrack("video", 2)[0]
    speech = timeline.GetItemListInTrack("audio", 2)[0]
    assert picture.GetStart() == 240
    assert speech.GetStart() == 233
    assert picture.GetLinkedItems() == [speech]
    assert speech.GetLinkedItems() == [picture]
    report = verify_timeline(timeline, plan=plan)
    assert report["passed"]
    assert "aroll_linked" in report["checks_run"]
    assert not [v for v in report["violations"] if v["check"] == "aroll_unlinked"]


def test_caption_inside_speech_joins_one_three_group():
    """Defect 2 (captions): picture, speech and caption link in a single
    call - a later pair-call would break the group."""
    timeline, pool, project = _world()
    record = _build(
        timeline,
        pool,
        project,
        _master_clips(),
        captions=[_caption(2.0, 4.0)],
        program_channels={"1": 1, "2": 1},
    )
    assert timeline.GetTrackName("video", 3) == "Subtitles"
    triples = [c for c in timeline.link_calls if len(c[0]) == 3 and c[1]]
    assert len(triples) == 1, (
        f"expected one three-group link call, saw {timeline.link_calls}"
    )
    assert len(record["caption_links"]) == 1
    cap = timeline.GetItemListInTrack("video", 3)[0]
    assert len(cap.GetLinkedItems()) == 2, (
        "the group holds: nothing re-linked afterwards to break it"
    )


def test_sparse_overlay_rows_pack_with_no_empty_row_left():
    """Defect 4 as the verifier sees it: a semantic row with no
    transitions or explainer above it packs onto V4 - no blank V4/V5
    kept, and every surviving row named."""
    timeline, pool, project = _world()
    record = _build(
        timeline,
        pool,
        project,
        _master_clips(),
        captions=[_caption(2.0, 4.0)],
        semantic=[_semantic(0.0, 48)],
        program_channels={"1": 1, "2": 1},
    )
    assert timeline.GetTrackCount("video") == 4
    assert timeline.GetTrackName("video", 4) == "Semantic"
    for index in range(1, 5):
        assert timeline.GetItemListInTrack("video", index), (
            f"V{index} is empty and must have been deleted"
        )
    assert record["deleted_empty_tracks"] == [], (
        "packing means no empty row is ever created, so none is deleted"
    )


# ── The look keeps one picture row per speaker ──


def test_program_channels_prefer_the_catalog_then_the_master():
    """Known unknown, answered: the reel path reaches the recorded
    program stream through the catalog first and the live master's own
    speech rows second - and refuses when neither names one."""
    clips = [_clip_2("audio", 1, "SpeakerOne CH1", "SpeakerOne", "/m/a.MXF", 0.0, 10.0)]
    angles = [{"key": "1", "label": "SpeakerOne", "track_index": 1}]
    assert resolve_reel_program_channels(angles, clips, "", explicit={"1": 3}) == {
        "1": 3
    }
    with pytest.raises(ReelBuildError, match="no recorded program stream"):
        resolve_reel_program_channels(angles, clips, "/tmp/no-such-project")
