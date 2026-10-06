"""The recorded timecode is read back for multicam sync.

The defect this names: the catalog records `timecode_start` (the raw
tag the camera wrote) and nothing ever reads it, so an n-gram offset
contradicting what the cameras themselves recorded goes out reported
as clean - and a stranger's timecode-synced multicam has no timecode
path at all. `conversation_clock` now cross-checks each member's
n-gram offset against the recorded tags and reports the agreement,
without moving the clock: timecode runs continuous across takes on
one camera, so promoting it over content would sync different scenes.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import conversation_clock as cc


def _clip(timecode, fps=25.0, vfr=False):
    return {"clip_id": "clip", "timecode_start": timecode,
            "frame_rate": fps, "is_vfr": vfr}


def test_agreeing_recorded_timecode_is_reported():
    """Tags ten seconds apart with the n-gram offset at minus ten:
    the cross-check agrees and names the implied offset."""
    clip = _clip("01:00:10:00")
    reference = _clip("01:00:00:00")

    check = cc.timecode_cross_check(clip, reference, -10.0)

    assert check["offset_seconds"] == -10.0
    assert check["agrees_with_ngram_offset"] is True
    assert check["approximate"] is False


def test_contradicting_recorded_timecode_is_reported():
    """The n-gram offset says minus ten while the cameras' own tags
    imply minus three hundred: the contradiction is reported with the
    difference, not averaged away and not silent."""
    clip = _clip("01:05:00:00")
    reference = _clip("01:00:00:00")

    check = cc.timecode_cross_check(clip, reference, -10.0)

    assert check["offset_seconds"] == -300.0
    assert check["agrees_with_ngram_offset"] is False
    assert check["difference_from_ngram_offset_seconds"] == -290.0


def test_missing_or_garbage_timecode_is_absent():
    """No tag, a tag nothing parses, or an unusable rate: no reading,
    never a default offset of zero that would read as synced."""
    reference = _clip("01:00:00:00")

    assert cc.timecode_cross_check(
        _clip(None), reference, -10.0) is None
    assert cc.timecode_cross_check(
        _clip("not-a-timecode"), reference, -10.0) is None
    assert cc.timecode_cross_check(
        _clip("01:00:10:00", fps=0), reference, -10.0) is None
    assert cc.timecode_offset_seconds(_clip(None), reference) is None


def test_vfr_timecode_is_flagged_approximate():
    """Under a variable rate the frame field has no fixed meaning: the
    number is still read at nominal, but flagged so a caller knows not
    to trust it to the frame."""
    clip = _clip("01:00:10:00", vfr=True)
    reference = _clip("01:00:00:00")

    implied = cc.timecode_offset_seconds(reference, clip)
    check = cc.timecode_cross_check(clip, reference, -10.0)

    assert implied["offset_seconds"] == -10.0
    assert implied["approximate"] is True
    assert check["agrees_with_ngram_offset"] is True
    assert check["approximate"] is True
