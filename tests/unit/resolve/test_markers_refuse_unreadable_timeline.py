"""`resolve-axi markers` refuses a scratch timeline instead of crashing.

Finding 19, execution-frontier report 2026-09-24: `resolve-axi markers`
crashed on the scratch timeline. The listing read the timeline's start
frame, rate, markers and items unguarded, so a timeline that would not
report one of them failed deep inside with a bare TypeError instead of
a refusal. "No markers" and "I could not look" stay different answers.

No Resolve: scripted timeline fakes, and `cmd_markers` with its
connect/lease/project seams patched.
"""

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from library.tools import marker_feedback  # noqa: E402
from library.tools import resolve_axi  # noqa: E402
from library.tools.resolve_axi import cmd_markers  # noqa: E402


class _ScratchTimeline:
    """A fresh scratch timeline: empty, and already odd about it.

    `GetStartFrame` answers None (the call the listing used to make
    unconditionally), `GetSetting` raises (the call `_timeline_fps`
    used to make outside any guard).
    """

    def __init__(self, markers=None, start=None):
        self._markers = dict(markers or {})
        self._start = start

    def GetName(self):
        return "ren-exec-scratch-001"

    def GetMarkers(self):
        return dict(self._markers)

    def GetStartFrame(self):
        return self._start

    def GetSetting(self, key):
        raise RuntimeError("no settings on a scratch timeline")

    def GetTrackCount(self, track_type):
        return 0

    def GetItemListInTrack(self, track_type, index):
        return []


# ── read_notes: list what can be listed, refuse the rest ────────────

def test_an_empty_scratch_timeline_lists_cleanly():
    """No markers, no start frame, no rate: zero notes, no crash - and
    the start frame is never even asked for, because there is nothing
    to place with it."""
    timeline = _ScratchTimeline()
    marker_feedback.read_notes(timeline)


def test_markers_with_an_unreported_start_raise_a_named_refusal():
    """Timeline markers exist but the start frame - their origin -
    will not report: a named refusal, not a bare TypeError."""
    timeline = _ScratchTimeline(
        markers={10: {"color": "Green", "name": "note",
                      "note": "look", "duration": 1, "customData": ""}},
        start=None)
    with pytest.raises(marker_feedback.TimelineUnreadableError) as excinfo:
        marker_feedback.read_notes(timeline)
    message = str(excinfo.value)
    assert "ren-exec-scratch-001" in message
    assert "start frame" in message
    assert "TypeError" not in message


# ── cmd_markers: the refusal reaches the operator, not a traceback ──

def _args(**kwargs):
    base = {"project": "", "timeline": "ren-exec-scratch-001",
            "plane": "", "full": False}
    base.update(kwargs)
    return types.SimpleNamespace(**base)


def _patched_cmd(monkeypatch, timeline):
    monkeypatch.setattr(resolve_axi, "_connect", lambda: object())
    monkeypatch.setattr(
        resolve_axi, "_lease",
        lambda exclusive: _NullContext())
    monkeypatch.setattr(resolve_axi, "_project", lambda resolve, name: object())
    monkeypatch.setattr(
        resolve_axi, "_target_timeline",
        lambda project, name: (timeline, False, ""))


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_cmd_markers_lists_an_empty_scratch_timeline(monkeypatch, capsys):
    _patched_cmd(monkeypatch, _ScratchTimeline())
    assert cmd_markers(_args()) == 0
    assert "error:" not in capsys.readouterr().out


def test_cmd_markers_refuses_an_unreadable_timeline(monkeypatch, capsys):
    """The finding: exit 1 with an error line, never a traceback."""
    _patched_cmd(monkeypatch, _ScratchTimeline(
        markers={10: {"color": "Green", "name": "note",
                      "note": "look", "duration": 1, "customData": ""}},
        start=None))
    assert cmd_markers(_args()) == 1
    out = capsys.readouterr().out
    assert "error:" in out
    assert "ren-exec-scratch-001" in out
    assert "Traceback" not in out
