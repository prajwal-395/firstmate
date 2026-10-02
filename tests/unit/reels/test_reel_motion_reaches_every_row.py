"""A treatment planned for a clip on ANY picture row must reach that clip.

A two-angle reel built through `timeline_layout.plan_layout` draws every
planned drift, on both rows. Reel 09's history: docs/evidence/reel_look.md.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import reel_look
from library.tools.pipeline_skills import read_receipts
from library.tools.timeline_layout import plan_layout


class _Clip:
    def __init__(self, source_file, track_index, speaker):
        self.source_file = source_file
        self.track_type = "video"
        self.track_index = track_index
        self.timeline_start = 0.0
        self.source_in = 0.0
        self.speaker = speaker


def _placement(source_file, track_index, record_frame, speaker, fps=24.0):
    seconds = 5.0
    return {
        "clip": _Clip(source_file, track_index, speaker),
        "source_in": 0.0,
        "source_out": seconds,
        "record": record_frame / fps,
        "snapped_record": record_frame,
        "speaker": speaker,
    }


def _two_angle_plan():
    """The Reel 09 shape: Akshita on V1, Craig on V2, the set above."""
    return plan_layout({
        "angles": [
            {"key": "1", "label": "Akshita",
             "speech_name": "Akshita CH1", "program_channel": 1},
            {"key": "2", "label": "Craig",
             "speech_name": "Craig CH1", "program_channel": 1},
        ],
        "has_broll": False,
        "has_frame": True,
        "caption_spans": [],
        "has_transitions": False,
        "has_explainer": False,
        "has_semantic": False,
        "mg_spans": [],
        "has_generators": False,
        "timed_text_spans": [],
        "music_spans": [],
        "sfx_spans": [],
    })


def _angle_key(clip):
    return str(int(clip.track_index))


def _placements():
    # The Reel 09 arrangement: the outer shots ride Craig's row (V2),
    # the inner two Akshita's (V1).
    return [
        _placement("/tmp/cr0.mxf", 2, 0, "Craig"),
        _placement("/tmp/ak1.mxf", 1, 120, "Akshita"),
        _placement("/tmp/ak2.mxf", 1, 240, "Akshita"),
        _placement("/tmp/cr3.mxf", 2, 360, "Craig"),
    ]


def _motion(count=4):
    return [{
        "target_block_position": i,
        "effect_type": "slow_zoom_in",
        "params": {"zoom_start": 1.0, "zoom_mid": 1.02,
                   "zoom_end": 1.04},
    } for i in range(count)]


def _manifest():
    return reel_look.fusion_manifest(
        _placements(), {"power": {}}, _motion(), 24.0,
        track_plan=_two_angle_plan().serializable(),
        angle_key=_angle_key)


def test_manifest_groups_clips_by_the_plan_rows():
    manifest = _manifest()
    labels = {row: [c["label"] for c in spec["clips"]]
              for row, spec in manifest["tracks"].items()}
    assert labels == {
        "V1": [reel_look.clip_label(1), reel_look.clip_label(2)],
        "V2": [reel_look.clip_label(0), reel_look.clip_label(3)],
    }


# ── The pass itself, driven against a fake Resolve ────────────────────

class _FakeMediaPoolItem:
    def __init__(self, path, frames=600, fps="24", resolution="1080x1920"):
        self._props = {
            "File Path": path, "Frames": str(frames),
            "FPS": fps, "Resolution": resolution,
        }

    def GetClipProperty(self, key=None):
        return self._props if key is None else self._props.get(key, "")


class _FakeComp:
    def __init__(self):
        self.locked = False

    def Lock(self):
        self.locked = True

    def Unlock(self):
        self.locked = False

    def GetToolList(self):
        class _Tool:
            def __init__(self, regid):
                self._regid = regid

            def GetAttrs(self):
                return {"TOOLS_RegID": self._regid}

            def Delete(self):
                return True
        return {1: _Tool("MediaIn"), 2: _Tool("Merge"), 3: _Tool("MediaOut")}

    def AddTool(self, _name):
        assert self.locked, "Fusion node creation must hold comp.Lock()"
        class _Dummy:
            def Delete(self):
                return True
        return _Dummy()

    def FindTool(self, _name):
        return None


class _FakeTimelineItem:
    def __init__(self, path, start, end):
        self.mpi = _FakeMediaPoolItem(path)
        self.imported = []
        self._start, self._end = start, end

    def GetMediaPoolItem(self):
        return self.mpi

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetFusionCompNameList(self):
        return ["Composition 1"] if self.imported else []

    def DeleteFusionCompByName(self, _name):
        self.imported = []
        return True

    def ImportFusionComp(self, path):
        with open(path, encoding="utf-8") as f:
            self.imported.append(f.read())
        return _FakeComp()

    def GetFusionCompByName(self, _name):
        return _FakeComp()


class _FakeTimeline:
    def __init__(self, items_by_track):
        self.items_by_track = items_by_track

    def GetSetting(self, _key):
        return "24"

    def GetItemListInTrack(self, _kind, index):
        return self.items_by_track.get(index, [])


class _FakeResolve:
    def __init__(self, timeline):
        self._timeline = timeline

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self

    def GetCurrentTimeline(self):
        return self._timeline

    def OpenPage(self, _name):
        return True


class _FakeDvr:
    def __init__(self, timeline):
        self.timeline = timeline

    def scriptapp(self, _name):
        return _FakeResolve(self.timeline)


@pytest.fixture
def fusion_module():
    module = importlib.import_module(
        "library.tools.execution.apply_fusion_comps")
    return importlib.reload(module)


def _timeline():
    return _FakeTimeline({
        1: [_FakeTimelineItem("/tmp/ak1.mxf", 120, 240),
            _FakeTimelineItem("/tmp/ak2.mxf", 240, 360)],
        2: [_FakeTimelineItem("/tmp/cr0.mxf", 0, 120),
            _FakeTimelineItem("/tmp/cr3.mxf", 360, 480)],
    })


def test_drift_on_each_row_gets_a_comp_and_draws(fusion_module,
                                                 monkeypatch, tmp_path):
    """The Reel 09 receipt shape, closed: four planned, four checked."""
    timeline = _timeline()
    monkeypatch.setattr(fusion_module, "dvr", _FakeDvr(timeline))

    assert fusion_module.apply_fusion_comps(
        json.loads(json.dumps(_manifest())), str(tmp_path)) is True

    for row in (1, 2):
        for item in timeline.items_by_track[row]:
            assert item.imported, (
                f"{item.mpi.GetClipProperty('File Path')} got no comp - "
                f"its planned drift never reached the picture")

    result = read_receipts(str(tmp_path), "render")["verify_treatment"][
        "result"]
    assert result["clips_checked"] == len(result["rows"])
    drift_rows = [r for r in result["rows"] if "motion_over_time" in r]
    assert {r["label"] for r in drift_rows} == {
        reel_look.clip_label(i) for i in range(4)}
    assert all(r["motion_over_time"] for r in drift_rows), (
        "a planned drift that moves no frame is the shipped defect")
    assert not any(r["undone"] for r in drift_rows)
