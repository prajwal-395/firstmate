"""The comp carry: a trim that re-places a clip keeps its treatment.

A trim on a built reel re-places the clip it trims, and a re-placed
item is a NEW object - so the clip's Fusion composition is dropped by
the very edit that was supposed to keep the reel's look.  Every picture
clip on a built reel carries one, so this refusal met every trim the
engine could receive:

    the item carries Fusion compositions that this replacement path
    cannot carry

The composition is RE-DERIVED from the reel's own recorded manifest,
which says what each clip's treatment IS, and the write is proved by
reading the composition back.

Nothing here reaches a real project or a real Resolve.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import comp_carry  # noqa: E402
from library.tools import edit_state_preservation as esp  # noqa: E402


class _PoolItem:
    def __init__(self, path="/lab/a.mov", fps=24.0):
        self._path = path
        self._fps = fps

    def GetClipProperty(self, key):
        return {"File Path": self._path, "FPS": self._fps}.get(key)

    def GetUniqueId(self):
        return "pool-1"

    def GetMediaId(self):
        return "media-1"


class _Graph:
    def GetNumNodes(self):
        return 1


class _Item:
    """A timeline item that reads like the fake Resolve the tests use."""

    def __init__(self, *, path="/lab/a.mov", fps=24.0, source_in=100,
                 source_out=160, comps=1, uid="item-1"):
        self._pool = _PoolItem(path, fps)
        self._source_in = source_in
        self._source_out = source_out
        self._comps = comps
        self._uid = uid

    def GetMediaPoolItem(self):
        return self._pool

    def GetSourceStartFrame(self):
        return self._source_in

    def GetSourceEndFrame(self):
        return self._source_out

    def GetFusionCompCount(self):
        return self._comps

    def GetFusionCompNameList(self):
        return ["Composition 1"] if self._comps else []

    def GetUniqueId(self):
        return self._uid

    def GetProperty(self):
        return {"Pan": 0.0, "Tilt": 0.0}

    def GetNodeGraph(self):
        return _Graph()

    def GetFlagList(self):
        return []

    def GetClipEnabled(self):
        return True

    def GetClipColor(self):
        return ""

    def GetMarkers(self):
        return {}

    def GetLinkedItems(self):
        return []

    def GetTrackTypeAndIndex(self):
        return "video", 1

    def GetStart(self):
        return 0

    def GetEnd(self):
        return 100

    def CopyGrades(self, items):
        return True


def _manifest(*, source_file="/lab/a.mov", source_in=100 / 24.0,
              source_out=160 / 24.0, label="reel_picture_00",
              effects=None):
    return {
        "tracks": {"V1": {"clips": [{
            "label": label,
            "source_file": source_file,
            "source_in": source_in,
            "source_out": source_out,
        }]}},
        "fusion_effects": {"per_clip": {label: (
            effects if effects is not None
            else {"vignette": True, "grade_contrast": 0.12})}},
    }


def test_the_carry_names_the_clip_the_record_describes():
    """The reel's record says which treatment this clip carries."""
    items = [_Item()]

    plan = comp_carry.plan_carry(
        project_folder="/lab/project", timeline_name="Reel 13",
        manifest=_manifest(), row="V1", item_index=0, items=items,
        source_file="/lab/a.mov", source_in_frame=100,
        source_out_frame=140, played_frames=41, source_fps=24.0)

    assert plan.label == "reel_picture_00"
    assert plan.effects["vignette"] is True
    assert plan.source_in == pytest.approx(100 / 24.0)
    assert plan.source_out == pytest.approx(140 / 24.0)


def test_the_carry_writes_a_manifest_for_that_clip_only():
    """The reel's own record is not overwritten by a one-clip manifest."""
    manifest = _manifest()

    plan = comp_carry.plan_carry(
        project_folder="/lab/project", timeline_name="Reel 13",
        manifest=manifest, row="V1", item_index=0, items=[_Item()],
        source_file="/lab/a.mov", source_in_frame=100,
        source_out_frame=140, played_frames=41, source_fps=24.0)

    one = plan.one_clip_manifest()

    assert list(one["tracks"]) == ["V1"]
    assert len(one["tracks"]["V1"]["clips"]) == 1
    assert one["fusion_effects"]["per_clip"]["reel_picture_00"]
    # The reel's own record is untouched by the carry.
    assert len(manifest["tracks"]["V1"]["clips"]) == 1


def test_a_record_that_does_not_describe_this_clip_refuses_the_carry():
    """A manifest from another cut is a refusal, not a guess.

    The comp pass maps a clip spec to an item by source path AND
    window, so a manifest that does not describe this item would write
    treatments onto the wrong clips.
    """
    items = [_Item(source_in=900, source_out=960)]

    with pytest.raises(comp_carry.CompCarryRefused) as refused:
        comp_carry.plan_carry(
            project_folder="/lab/project", timeline_name="Reel 13",
            manifest=_manifest(), row="V1", item_index=0, items=items,
            source_file="/lab/a.mov", source_in_frame=900,
            source_out_frame=940, played_frames=41, source_fps=24.0)

    assert "does not describe" in str(refused.value)


def test_a_reel_with_no_record_refuses_the_carry():
    """Nothing to re-derive from is a refusal with a next step."""
    with pytest.raises(comp_carry.CompCarryRefused) as refused:
        comp_carry.plan_carry(
            project_folder="/lab/project", timeline_name="Reel 13",
            manifest=None, row="V1", item_index=0, items=[_Item()],
            source_file="/lab/a.mov", source_in_frame=100,
            source_out_frame=140, played_frames=41, source_fps=24.0)

    assert "not on disk" in str(refused.value)


def test_a_record_that_says_nothing_about_this_clip_refuses_the_carry():
    """A clip the record does not describe gets no composition."""
    manifest = _manifest()
    manifest["fusion_effects"]["per_clip"] = {}

    with pytest.raises(comp_carry.CompCarryRefused) as refused:
        comp_carry.plan_carry(
            project_folder="/lab/project", timeline_name="Reel 13",
            manifest=manifest, row="V1", item_index=0, items=[_Item()],
            source_file="/lab/a.mov", source_in_frame=100,
            source_out_frame=140, played_frames=41, source_fps=24.0)

    assert "nothing about" in str(refused.value)


def test_the_capture_refuses_a_comp_unless_the_caller_re_derives_it():
    """The refusal stays, unless the caller says it will re-derive."""
    item = _Item(comps=2)

    with pytest.raises(esp.StatePreservationError) as refused:
        esp.capture(item, picture=True)

    assert "Fusion compositions" in str(refused.value)

    state = esp.capture(item, picture=True, comps_rederived=True)

    assert state["comp_count"] == 2
    assert state["comps_rederived"] is True
