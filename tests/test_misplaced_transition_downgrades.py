"""Finding 32: a transition 4.02 accepts that no V1 cut can carry ships as
the hard cut the boundary already is, recorded in `transitions_downgraded`
with its reason - never failing the whole compile (AGENTS.md 10.5).
History: `docs/evidence/transition_own_track.md`.
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


def test_a_transition_no_v1_cut_carries_downgrades_instead_of_failing(
        project):
    """The B6 shape on the native path (a dissolve mid-clip, no V1 clip
    ends at 1.0s), the same on the Fusion path, and a transition on the
    last V1 clip (5.418s), which has no incoming clip for its head half."""
    cases = [
        ("cross_dissolve", 1.0, 0, "native_transitions", "V1"),
        ("crash_zoom", 1.0, 0, None, None),
        ("cross_dissolve", 5.418, 1, "native_transitions", "incoming"),
    ]
    for ttype, at, after, placed_key, reason in cases:
        manifest = _compile_with(project, plan_transitions=[
            {"transition_id": "trans_002", "transition_type": ttype,
             "cut_point_timeline": at, "duration": 0.4,
             "after_clip": after}])
        if placed_key:
            assert manifest[placed_key] == []
        else:
            assert manifest["fusion_effects"]["transitions"] == []
        (downgraded,) = manifest["transitions_downgraded"]
        assert downgraded["transition_id"] == "trans_002"
        assert downgraded["shipped_type"] == "hard_cut"
        if reason:
            assert reason in downgraded["reason"], (ttype, at)
