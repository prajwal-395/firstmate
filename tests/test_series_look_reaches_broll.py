"""The house look reaches B-roll, not only A-roll.

`compile_manifest` merges `fusion_look` - pivot contrast, glow, grain and
a shaped vignette - onto every V1 **and V2** clip, because a CDL has no
term for any of the four and a Fusion comp is their only route to the
picture. The Fusion pass read `tracks['V1']` alone, so on project 001
eleven clips carried a merged look and eight got a comp: the three
cutaways played at a different contrast, with no grain and no vignette,
beside the A-roll they were cut into. Commit 85634d5 added the detection
that names the dropped labels and deliberately left the gap open.

Making the look CONSISTENT is right under any answer to the open
`vep-house-look-grain-never-chosen` decision, so nothing here asserts a
grain, vignette or glow VALUE - only that whatever value is chosen
arrives on both tracks.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_6_01_render.build_verification import (
    detect_unreachable_fusion_effects,
)
from library.tools.execution.fusion_tracks import (
    FUSION_COMP_TRACKS,
    TRANSITION_TRACK,
    fusion_comp_tracks,
    reachable_effect_labels,
)

# A look declared WHOLE: an armed element with no strength is refused by
# `comp_builder.UndeclaredEffectStrength`, which is the same rule
# `series_look` applies at the template. `vignette_intensity` is a name
# the renderer never reads and is kept here on purpose - this file also
# pins that an unreadable name is REPORTED as dropped, not drawn.
LOOK = {"glow_gain": 1.4, "glow_threshold": 0.75, "glow_size": 3.5,
        "film_grain": 0.02, "film_grain_power": 0.2, "film_grain_size": 1.5,
        "vignette_intensity": 0.35}

MANIFEST = {
    "tracks": {
        "V1": {"clips": [
            {"label": "a_roll_0", "source_file": "/tmp/a0.mov",
             "source_in": 0.0, "source_out": 2.0},
            {"label": "a_roll_1", "source_file": "/tmp/a1.mov",
             "source_in": 0.0, "source_out": 2.0},
        ]},
        "V2": {"clips": [
            {"label": "broll_1", "source_file": "/tmp/b1.mov",
             "source_in": 0.0, "source_out": 1.5},
        ]},
    },
    "fusion_effects": {
        "per_clip": {
            "a_roll_0": dict(LOOK),
            "a_roll_1": dict(LOOK),
            "broll_1": dict(LOOK),
        },
        "transitions": [],
    },
}


# ── The enumeration ───────────────────────────────────────────────────

def test_every_looked_clip_is_reachable():
    per_clip = MANIFEST["fusion_effects"]["per_clip"]
    assert set(per_clip) <= reachable_effect_labels(MANIFEST)


def test_a_placed_broll_label_is_no_longer_a_drop():
    """The exact 001 shape: three cutaways carrying the merged look."""
    dropped = detect_unreachable_fusion_effects(
        MANIFEST["fusion_effects"]["per_clip"],
        {1: {"a_roll_0", "a_roll_1"}, 2: {"broll_1"}},
    )
    assert dropped == []


def test_an_unplaced_label_is_still_a_drop():
    """The detection must not become vacuous - a gate that cannot fire is
    worse than no gate."""
    dropped = detect_unreachable_fusion_effects(
        {"broll_9": dict(LOOK)}, {1: {"a_roll_0"}, 2: {"broll_1"}})
    assert [d["label"] for d in dropped] == ["broll_9"]
    assert "glow_gain" in dropped[0]["detail"]


# ── The pass itself, driven against a fake Resolve ────────────────────

class _FakeMediaPoolItem:
    def __init__(self, path, frames=120, fps="30.0", resolution="1920x1080"):
        self._props = {
            "File Path": path, "Frames": str(frames),
            "FPS": fps, "Resolution": resolution,
        }

    def GetClipProperty(self, key=None):
        return self._props if key is None else self._props.get(key, "")


class _FakeComp:
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
        class _Dummy:
            def Delete(self):
                return True
        return _Dummy()

    def FindTool(self, _name):
        return None


class _FakeTimelineItem:
    def __init__(self, path, start=0, end=60):
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
        # Read it NOW: the pass deletes its temp comp directory on the
        # way out, so the file is gone by the time the test looks.
        with open(path, encoding="utf-8") as f:
            self.imported.append(f.read())
        return _FakeComp()

    def GetFusionCompByName(self, _name):
        return _FakeComp()


class _FakeTimeline:
    def __init__(self, items_by_track):
        self.items_by_track = items_by_track

    def GetSetting(self, _key):
        return "30"

    def GetItemListInTrack(self, _kind, index):
        return self.items_by_track.get(index, [])


class _FakeResolve:
    def __init__(self, timeline):
        self._timeline = timeline
        self.pages = []

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self

    def GetCurrentTimeline(self):
        return self._timeline

    def OpenPage(self, name):
        self.pages.append(name)
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
        1: [_FakeTimelineItem("/tmp/a0.mov", 0, 60),
            _FakeTimelineItem("/tmp/a1.mov", 60, 120)],
        2: [_FakeTimelineItem("/tmp/b1.mov", 20, 65)],
    })


def test_a_broll_clip_gets_a_fusion_comp(fusion_module, monkeypatch, tmp_path):
    """The defect, driven through the real pass: eleven merged entries
    used to produce eight comps."""
    timeline = _timeline()
    monkeypatch.setattr(fusion_module, "dvr", _FakeDvr(timeline))

    assert fusion_module.apply_fusion_comps(
        json.loads(json.dumps(MANIFEST)), str(tmp_path)) is True

    v1 = timeline.items_by_track[1]
    v2 = timeline.items_by_track[2]
    assert all(item.imported for item in v1), "A-roll regressed"
    assert all(item.imported for item in v2), (
        "the cutaway carries no Fusion comp, so it plays at a different "
        "contrast with no grain and no vignette beside the A-roll")


def test_the_broll_comp_draws_the_same_look(fusion_module, monkeypatch,
                                            tmp_path):
    """Not just 'a comp' - the same nodes the A-roll got.

    No value is asserted, only that the same parameters draw the same
    node types on both tracks: the grain, vignette and glow numbers are
    the captain's.
    """
    timeline = _timeline()
    monkeypatch.setattr(fusion_module, "dvr", _FakeDvr(timeline))
    fusion_module.apply_fusion_comps(
        json.loads(json.dumps(MANIFEST)), str(tmp_path))

    def nodes(item):
        text = item.imported[0]
        return {name for name in
                ("SoftGlow", "FilmGrain", "EllipseMask", "Merge")
                if name in text}

    a_nodes = nodes(timeline.items_by_track[1][0])
    b_nodes = nodes(timeline.items_by_track[2][0])
    assert a_nodes, "the A-roll comp drew none of the house-look nodes"
    assert b_nodes == a_nodes


def test_transitions_are_not_replayed_onto_broll(fusion_module, monkeypatch,
                                                 tmp_path):
    """A transition's `after_clip` is a V1 index. Applied to V2 it would
    draw a flash at an unrelated cut."""
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["fusion_effects"]["transitions"] = [
        {"type": "flash", "after_clip": 0, "duration_frames": 12}
    ]
    timeline = _timeline()
    monkeypatch.setattr(fusion_module, "dvr", _FakeDvr(timeline))
    fusion_module.apply_fusion_comps(manifest, str(tmp_path))

    broll_comp = timeline.items_by_track[2][0].imported[0]
    a_roll_comp = timeline.items_by_track[1][0].imported[0]
    assert "Brightness" in a_roll_comp, "the flash did not reach the A-roll"
    assert "Brightness" not in broll_comp
