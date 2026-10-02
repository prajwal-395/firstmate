"""Finding 17: a retimed talking shot without its dialogue loses sync.

Scout RT2.1 (B2): SetSpeed 50% verified (GetSpeed re-reads) while the
picture played source 25-61 in the same 72 record frames and audio1
still played 25-97 at full speed - half the line's picture gone, lip
sync broken, build green.

The fix: the linked dialogue rides the same write. In the build's
native applicator every dialogue-row audio item sharing the video
item's record span is retimed to the same Percentage and judged by its
own re-read; an audio refusal fails the op (restoring the video item)
rather than shipping the pair split. The resolve-axi `edit speed`
verb selects the same way (same span, same source file - the bed
shares spans, never sources) and names what it will touch in the dry
run.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FPS = 30.0


class _FakePool:
    def __init__(self, filename):
        self._filename = filename

    def GetClipProperty(self, key):
        assert key == "File Name"
        return self._filename


class _FakeItem:
    def __init__(self, name, start, duration, pool="hook.mov",
                 speed=100.0, refusing=False):
        self._name = name
        self._start = start
        self._duration = duration
        self._pool = _FakePool(pool)
        self._speed = float(speed)
        self._refusing = refusing
        self.writes = []

    def GetName(self): return self._name
    def GetStart(self): return self._start
    def GetEnd(self): return self._start + self._duration
    def GetDuration(self): return self._duration
    def GetSourceStartFrame(self): return 0
    def GetSourceEndFrame(self): return self._duration
    def GetUniqueId(self): return f"uid-{self._name}"
    def GetMediaPoolItem(self): return self._pool

    def GetSpeed(self):
        return {"Percentage": self._speed}

    def SetSpeed(self, opts):
        self.writes.append(dict(opts))
        if self._refusing:
            return False
        self._speed = float(opts["Percentage"])
        return True


class _FakeTimeline:
    def __init__(self, video=(), audio=()):
        self._video = list(video)
        self._audio = list(audio)

    def GetTrackCount(self, track_type):
        if track_type == "audio":
            return 1 if self._audio else 0
        return 1

    def GetItemListInTrack(self, track_type, index):
        if track_type == "video":
            return list(self._video) if index == 1 else []
        return list(self._audio) if index == 1 else []

    def GetSetting(self, key):
        assert key == "timelineFrameRate"
        return FPS

    def GetName(self): return "fake"


def _speed_op(op_id="speed_001", start=0.0, end=2.4, percent=50.0):
    return {"op_id": op_id, "effect_type": "speed_ramp",
            "timeline_start": start, "timeline_end": end,
            "segments": [{"percent": percent, "timeline_start": start,
                          "timeline_end": end}]}


# ── the build applicator ─────────────────────────────────────────

def test_retime_carries_same_span_dialogue_and_never_the_bed():
    """The B2 shape: 50% on the picture retimes the same-span dialogue
    to 50% too, each judged by its own re-read. Span match alone is not
    linkage: the bed plays the same span on a music row and stays at
    100% (track scoping is the caller's: dialogue row 1 only)."""
    from library.tools import native_ops_apply as apply
    video = _FakeItem("hook", 0, 72)
    dialogue = _FakeItem("hook-audio", 0, 72)
    bed = _FakeItem("bed", 0, 72, pool="infected.wav")

    class _TwoRow(_FakeTimeline):
        def GetItemListInTrack(self, track_type, index):
            if track_type == "audio":
                return {1: [dialogue], 2: [bed]}.get(index, [])
            return super().GetItemListInTrack(track_type, index)

    report = apply.apply_native_speed_ops(
        _TwoRow(video=[video]), [_speed_op()], fps=FPS, dialogue_tracks=[1])
    assert len(report["applied"]) == 1
    row = report["applied"][0]
    assert dialogue.GetSpeed()["Percentage"] == 50.0
    assert row["audio"][0]["item"] == "hook-audio"
    assert row["audio"][0]["percent"] == 50.0
    assert bed.GetSpeed()["Percentage"] == 100.0
    assert report["failed"] == []


def test_video_only_clip_reports_no_linked_audio():
    """B-roll/video_only has no dialogue: the op applies with the
    audio half said plainly, not silently."""
    from library.tools import native_ops_apply as apply
    video = _FakeItem("broll", 0, 72, pool="street.mov")
    timeline = _FakeTimeline(video=[video], audio=[])
    report = apply.apply_native_speed_ops(
        timeline, [_speed_op()], fps=FPS, dialogue_tracks=[1])
    assert len(report["applied"]) == 1
    assert "no linked dialogue audio" in report["applied"][0]["audio"]
    assert report["failed"] == []


def test_audio_refusal_fails_the_op_and_restores_the_video():
    """An audio item that will not retime fails the op BY NAME - and
    the video item goes back to its pre-op speed rather than leaving
    the pair split across two speeds."""
    from library.tools import native_ops_apply as apply
    video = _FakeItem("hook", 0, 72)
    dialogue = _FakeItem("hook-audio", 0, 72, refusing=True)
    timeline = _FakeTimeline(video=[video], audio=[dialogue])
    report = apply.apply_native_speed_ops(
        timeline, [_speed_op()], fps=FPS, dialogue_tracks=[1])
    assert report["applied"] == []
    (failed,) = report["failed"]
    assert failed["op_id"] == "speed_001"
    assert "hook-audio" in failed["what"]
    assert video.GetSpeed()["Percentage"] == 100.0


def test_freeze_carries_linked_audio():
    """A freeze is a retime to 0.0 for the pair, judged the same way."""
    from library.tools import native_ops_apply as apply
    video = _FakeItem("hook", 0, 72)
    dialogue = _FakeItem("hook-audio", 0, 72)
    timeline = _FakeTimeline(video=[video], audio=[dialogue])
    report = apply.apply_native_speed_ops(
        timeline, [{"op_id": "speed_001", "effect_type": "freeze_frame",
                    "timeline_start": 0.0, "timeline_end": 2.4}],
        fps=FPS, dialogue_tracks=[1])
    assert len(report["applied"]) == 1
    assert dialogue.GetSpeed()["Percentage"] == 0.0


# ── the resolve-axi verb selection ───────────────────────────────

def _span_of(item):
    from library.tools import resolve_axi as axi
    return axi._item_span(item)


def test_verb_links_same_source_audio_and_refuses_to_guess():
    """Same span + same file is linked; same span + another file is
    named and left alone; no audio selects nothing; a same-span item
    whose source will not read refuses rather than guesses - the bed
    could be hiding behind the blank."""
    from library.tools import resolve_axi as axi
    video = _FakeItem("hook", 0, 72, pool="hook.mov")
    dialogue = _FakeItem("hook-audio", 0, 72, pool="hook.mov")
    bed = _FakeItem("bed", 0, 72, pool="infected.wav")
    linked, skipped, refused, unchecked = axi._linked_audio_for_speed(
        _FakeTimeline(video=[video], audio=[dialogue, bed]), video,
        _span_of(video))
    assert [a.GetName() for a in linked] == ["hook-audio"]
    assert skipped == ["bed"]
    assert refused == "" and unchecked == ""

    assert axi._linked_audio_for_speed(
        _FakeTimeline(video=[video], audio=[]), video,
        _span_of(video)) == ([], [], "", "")

    class _NoSource(_FakeItem):
        def GetMediaPoolItem(self): return None

    linked, skipped, refused, unchecked = axi._linked_audio_for_speed(
        _FakeTimeline(video=[video], audio=[_NoSource("ghost", 0, 72)]),
        video, _span_of(video))
    assert linked == [] and skipped == []
    assert "ghost" in refused and unchecked == ""


def test_ledger_retime_sets_picture_and_dialogue_and_refuses_a_reshape():
    """Punch list 10: a ledger retime reaches the timeline as a speed on
    the placed picture AND its dialogue, each re-read; a retimed piece a
    later placement pass reshaped refuses rather than play the wrong
    source."""
    from types import SimpleNamespace

    import pytest

    from library.tools.reel_build import (
        ReelBuildError, _apply_ledger_retimes, placements)
    from library.tools.reel_clock import rated_range

    ranges = [rated_range(0.0, 10.0, [(2.0, 4.2, 1.1)])]
    video = SimpleNamespace(timeline_start=0.0, timeline_end=10.0,
                            source_in=0.0, track_index=1, speaker="A",
                            track_type="video", source_file="/hook.mov")
    audio = SimpleNamespace(**{**vars(video), "track_type": "audio"})
    placed = placements(ranges, [video, audio], FPS)
    retimed = [p for p in placed if "rate" in p]
    assert len(retimed) == 2
    start, frames = retimed[0]["snapped_record"], retimed[0]["record_frames"]
    assert (start, frames) == (60, 60)  # 66 master frames at 110%

    picture = _FakeItem("pic", start, frames)
    speech = _FakeItem("speech", start, frames)
    applied = _apply_ledger_retimes(
        _FakeTimeline(video=[picture], audio=[speech]), placed, FPS,
        [1], [1], "Reel 01")
    assert len(applied) == 1
    assert picture.GetSpeed()["Percentage"] == pytest.approx(110.0)
    assert speech.GetSpeed()["Percentage"] == pytest.approx(110.0)
    assert picture.writes[0]["RippleTimeline"] is False

    reshaped = [dict(p) for p in placed]
    for p in reshaped:
        if "rate" in p and p["clip"] is video:
            p["source_in"] += 0.5
    with pytest.raises(ReelBuildError, match="reshaped that piece"):
        _apply_ledger_retimes(
            _FakeTimeline(video=[_FakeItem("pic", start, frames)],
                          audio=[_FakeItem("speech", start, frames)]),
            reshaped, FPS, [1], [1], "Reel 01")
