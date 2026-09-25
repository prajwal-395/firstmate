"""Finding 32: a transition 4.02 accepts can refuse the whole compile.

On the scout's B6 run (PA1.2) a slow cross dissolve at the cut out of a
transition slot (b-roll on V2, nothing on V1) passed 4.02 and then failed
`compile_manifest` with "Transition trans_002 at 15.0s does not sit at
the end of any V1 clip" - the whole run FAILED, rather than the one
entry being dropped with its reason (AGENTS.md 10.5's own rule for plan
entries: a plan entry that names no effect, no sound or no level is
DROPPED with the reason, never failed).

The fix: the compile ships the hard cut the boundary already is and
records the one entry in `transitions_downgraded` with its reason. The
run builds; the miss is said, not silent.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
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
    import tests.test_compile_manifest_without_the_decoration as base
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


def test_a_native_transition_off_v1_downgrades_instead_of_failing(project):
    """The B6 shape on the native path: a dissolve mid-clip (no V1 clip
    ends at 1.0s) must not fail the run."""
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_002", "transition_type": "cross_dissolve",
         "cut_point_timeline": 1.0, "duration": 0.4, "after_clip": 0}])
    assert manifest["native_transitions"] == []
    (downgraded,) = manifest["transitions_downgraded"]
    assert downgraded["transition_id"] == "trans_002"
    assert downgraded["shipped_type"] == "hard_cut"
    assert "V1" in downgraded["reason"]


def test_a_drawn_transition_off_v1_downgrades_instead_of_failing(project):
    """Same shape on the Fusion path."""
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_002", "transition_type": "crash_zoom",
         "cut_point_timeline": 1.0, "duration": 0.4, "after_clip": 0}])
    assert manifest["fusion_effects"]["transitions"] == []
    (downgraded,) = manifest["transitions_downgraded"]
    assert downgraded["transition_id"] == "trans_002"
    assert downgraded["shipped_type"] == "hard_cut"


def test_a_transition_on_the_last_clip_downgrades_instead_of_failing(project):
    """A transition at the end of the last V1 clip (5.418s) has no
    incoming clip for its head half: same downgrade, same record."""
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_003", "transition_type": "cross_dissolve",
         "cut_point_timeline": 5.418, "duration": 0.4, "after_clip": 1}])
    assert manifest["native_transitions"] == []
    (downgraded,) = manifest["transitions_downgraded"]
    assert downgraded["transition_id"] == "trans_003"
    assert downgraded["shipped_type"] == "hard_cut"
    assert "incoming" in downgraded["reason"]


def test_a_well_placed_transition_still_ships(project):
    """The downgrade must not swallow the working path: a dissolve on
    the real cut at 2.285s still reaches the build."""
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_001", "transition_type": "cross_dissolve",
         "cut_point_timeline": 2.285, "duration": 0.4, "after_clip": 0}])
    assert len(manifest["native_transitions"]) == 1
    assert manifest["transitions_downgraded"] == []
