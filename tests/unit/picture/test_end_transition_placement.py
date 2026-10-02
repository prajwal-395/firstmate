"""Rung 7 end slot (K1, TR3.1): a transition addressed to the end builds.

TR3.1's "1s dip to black out of the final shot" had no slot: the plan
could only address cuts between blocks, the compile failed it ("does
not sit at the end of any V1 clip" - since finding 32, a recorded
downgrade), and resolve-axi refuses end transitions although the raw
API places one. The plan addresses the end with `cut_point_position:
"end"`; the compile routes an end-placed fade to the tail-only Fusion
build and an end-placed dissolve to the end-placed native build, and
the applicators place exactly that - judged by what they return.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _compile_with(project, plan_transitions):
    from unittest.mock import patch

    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, sfx_file = project
    outputs = copy.deepcopy(outputs)
    outputs["plan_transitions"] = {"transition_spec": plan_transitions}
    layout.pipeline_data_path.write_text(
        json.dumps({"step_outputs": outputs,
                    "project_folder": str(project_dir)}),
        encoding="utf-8")
    with patch.object(step, "load_sfx_catalog", return_value=[
            {"sfx_id": "whoosh.wav", "path": sfx_file,
             "duration_seconds": 0.5, "transient_offset_sec": 0.0}]):
        return step.compile_manifest(str(layout.output_root))


@pytest.fixture
def project(tmp_path):
    """Two abutting V1 clips (0-2.285, 2.285-5.418), under tmp_path only."""
    import tests.scenarios.test_compile_manifest_without_the_decoration as base
    from library.tools import music_audit_trail as audit
    from library.tools.project_layout import ProjectLayout

    media = tmp_path / "media"
    media.mkdir()
    names = {}
    for name in ("a_roll.mov", "b_roll.mov", "bed.wav", "whoosh.wav",
                 "sub_seg_000.mov"):
        path = media / name
        path.write_bytes(b"\x00" * 64)
        names[name] = str(path)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    layout = ProjectLayout(str(project_dir))
    layout.ensure()
    outputs = base._step_outputs(
        names["a_roll.mov"], names["b_roll.mov"], names["bed.wav"],
        names["whoosh.wav"], names["sub_seg_000.mov"])
    audit.write_audit_trail(
        str(project_dir), outputs["music_selection"]["music_selection"])
    return project_dir, layout, outputs, names["whoosh.wav"]


def test_end_fade_compiles_tail_only_on_the_last_clip(project):
    """TR3.1's dip: fade_to_black at the end is one tail row, no head."""
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_099", "transition_type": "fade_to_black",
         "cut_point_timeline": 5.418, "duration_frames": 30,
         "duration_source": "stated_frames", "at_end": True}])
    (row,) = manifest["fusion_effects"]["transitions"]
    assert row["type"] == "fade_to_black"
    assert row["after_clip"] == 1
    assert row["at_end"] is True
    assert row["duration_frames"] == 30
    assert manifest["transitions_downgraded"] == []


def test_end_entry_needing_two_pictures_downgrades(project):
    """Stale state carrying zoom_blur at the end: recorded, run builds."""
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_099", "transition_type": "zoom_blur",
         "cut_point_timeline": 5.418, "duration_frames": 8,
         "at_end": True}])
    assert manifest["fusion_effects"]["transitions"] == []
    (downgraded,) = manifest["transitions_downgraded"]
    assert downgraded["transition_id"] == "trans_099"
    assert "only fade_to_black draws its tail half" in downgraded["reason"]


class _FakeItem:
    def __init__(self, name, start, end):
        self._name = name
        self._start = start
        self._end = end
        self.payloads = []

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def AddTransition(self, payload):
        self.payloads.append(dict(payload))
        placed = _FakeItem(payload["type"], self._end - 10, self._end)
        return placed


def test_the_native_applicator_places_the_end_on_the_last_item():
    """The end op lands on the last item at its end - never an incoming."""
    from library.tools import native_ops_apply as apply

    first = _FakeItem("clip_a", 0, 150)
    last = _FakeItem("clip_b", 150, 300)
    report = apply.apply_native_transitions(
        None, [first, last],
        [{"transition_id": "trans_099", "resolve_name": "Cross Dissolve",
          "category": "simple", "after_clip": 1, "at_end": True,
          "duration_frames": 30}],
        fps=30.0)
    assert report["failed"] == []
    (row,) = report["applied"]
    assert row["position"] == "end"
    assert first.payloads == []
    (payload,) = last.payloads
    assert payload["position"] == "end"
    assert payload["duration"] == 30
