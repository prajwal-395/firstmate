from unittest.mock import patch, MagicMock
from library.tools.render_qa import (
    measure_lufs,
    detect_black_frames,
    detect_freeze_frames,
    verify_resolution,
    verify_framerate,
    verify_duration
)
import json
import sys
from pathlib import Path
from library.steps.step_6_01_render import step as render_step
from library.steps.step_5_04_compile_manifest import step as compile_step
import os
import subprocess
import pytest


@patch('subprocess.run')
def test_measure_lufs_fails_off_target_and_judges_the_declared_one(mock_run):
    mock_run.return_value = MagicMock(stderr='{\n"input_i": "-20.0",\n"input_tp": "-1.5"\n}', returncode=0)
    res = measure_lufs("dummy.mp4")
    assert not res.passed
    assert res.severity == "error"
    # An explicit -16 LUFS / -1 dBTP request is judged as declared.
    mock_run.return_value = MagicMock(
        stderr='{\n"input_i": "-16.0",\n"input_tp": "-1.5"\n}',
        returncode=0)
    result = measure_lufs(
        "dummy.mp4", target_lufs=-16.0, true_peak_ceiling=-1.0)
    assert result.passed
    assert result.threshold == {
        "target_lufs": -16.0, "tolerance": 1.0,
        "true_peak_ceiling": -1.0}


@patch('subprocess.run')
def test_black_and_freeze_frames_are_parsed_and_fail(mock_run):
    mock_run.return_value = MagicMock(stderr='[blackdetect @ 0x123] black_start:1.5 black_end:2.5 black_duration:1.0', returncode=0)
    res = detect_black_frames("dummy.mp4")
    assert not res.passed
    assert len(res.value) == 1
    assert res.value[0]["duration"] == 1.0
    mock_run.return_value = MagicMock(stderr=(
        "lavfi.freezedetect.freeze_start: 3.0\n"
        "lavfi.freezedetect.freeze_duration: 2.0\n"
        "lavfi.freezedetect.freeze_end: 5.0"), returncode=0)
    res = detect_freeze_frames("dummy.mp4")
    assert not res.passed
    assert len(res.value) == 1
    assert (res.value[0]["start"], res.value[0]["duration"],
            res.value[0]["end"]) == (3.0, 2.0, 5.0)


@patch('subprocess.run')
def test_resolution_framerate_and_duration_are_read_off_the_file(mock_run):
    mock_run.return_value = MagicMock(stdout='{"streams": [{"width": 1920, "height": 1080}]}', returncode=0)
    assert not verify_resolution("dummy.mp4", 1080, 1920).passed
    mock_run.return_value = MagicMock(stdout='{"streams": [{"r_frame_rate": "30000/1000"}]}', returncode=0)
    res = verify_framerate("dummy.mp4", expected_fps=30)
    assert res.passed
    assert res.value == 30.0
    mock_run.return_value = MagicMock(stdout='{"format": {"duration": "30.0"}}', returncode=0)
    assert verify_duration("dummy.mp4", expected_seconds=30.0).passed


def test_bar_rows_fully_masked_run_letterbox():
    from library.tools.render_qa import _bar_rows
    import numpy as np
    
    # 10 rows. 
    # rows 0,1,2: bar (mean 0)
    # rows 3,4,5: unreadable (masked by overlay)
    # rows 6,7,8,9: picture (mean 50)
    
    row_mean = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 50.0, 50.0, 50.0, 50.0])
    row_std = np.zeros(10)
    readable = np.array([True, True, True, False, False, False, True, True, True, True])
    
    # Walk starts at 0.
    # 0,1,2 are bar.
    # 3,4,5 are unreadable.
    # 6 breaks (picture).
    # Since previous is not None (it saw 0,1,2), the unreadable run is bounded by bar on outer side.
    # It should return i=6, treating the unreadable rows as bar.
    assert _bar_rows(row_mean, row_std, readable=readable) == 6


# --------------------------------------------------------------------------
# From test_render_asks_only_for_its_verdict.py
#
# 6.01 asks the model only for what the model authors.
#
# Finding 11, execution-frontier report 2026-09-24: step 6.01 asked the
# model for `render_watch_frames` and `visual_qa` - two bridge-owned
# optional fields, both measured by the deterministic build. The manifest
# declared no `llm_outputs`, so the runner asked for every declared
# output the step had not already produced: a call with nothing to ask.
#
# The manifest now declares `llm_outputs: [render_review]` - the one key
# the model writes, a narrative verdict on the export it just built -
# and `library/tools/render_review.py` reads it onto the run summary,
# so the verdict has a reader (the 6.02 validation remains the gate
# with teeth; reading is not gating).

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    llm_output_declarations,
)
from library.tools import render_review as rr  # noqa: E402


def _manifest():
    with open(REPO_ROOT / "library" / "steps" / "step_6_01_render"
              / "manifest.json", encoding="utf-8") as f:
        return json.load(f)


# ── The ask: only the model-owned key ──────────────────────────────

def test_the_model_is_asked_for_the_verdict_and_nothing_else():
    """Whatever the build already produced, the ask stays render_review.

    Before the fix the ask was the full outputs list minus what the
    build had in hand - on a default run, the two bridge-owned
    optional fields. Now the declared llm_outputs decide, and the
    bridge-owned keys are never asked for.
    """
    manifest = _manifest()
    for already_have in (
        set(),
        {"render_output"},
        {"render_output", "render_watch_frames", "visual_qa"},
    ):
        asked = llm_output_declarations(manifest, already_have)
        assert [o["name"] for o in asked] == ["render_review"], (
            f"already_have={already_have}: asked={[o['name'] for o in asked]}")
    from library.tools.output_contract import survey
    rows = survey()
    row = next(r for r in rows
               if r.node == "render" and r.name == "render_review")
    assert not row.unread, (
        "render_review has no reader: the summary stopped reading it")

    lines = rr.summary_lines({
        "overall": "concerns",
        "notes": ["V2 b-roll crowds the caption row at 37s"],
    })
    assert any("concerns" in line for line in lines)
    assert any("37s" in line for line in lines)


# --------------------------------------------------------------------------
# From test_render_transition_report.py
#
# A compiled transition miss stays visible in the build report.

def test_build_report_preserves_requested_frames_after_hard_cut_fallback():
    """TR3.1's 12-frame request must survive a fallback at frame 428."""
    transition = {
        "transition_id": "trans_001",
        "requested_type": "cross_dissolve",
        "transition_type": "cross_dissolve",
        "cut_point_frame": 428,
        "cut_point_timeline": 14.267,
        "duration_source": "stated_frames",
        "duration_frames": 12,
    }
    compile_step._downgrade_misplaced_transition(
        transition, "trans_001", "no V1 clip ends at frame 428")

    (item,) = render_step._transition_items_for_report(
        {"transitions": [transition]})

    assert item == {
        "transition_id": "trans_001",
        "requested_type": "cross_dissolve",
        "compiled_type": "hard_cut",
        "cut_point_frame": 428,
        "cut_point_timeline": 14.267,
        "duration_source": "stated_frames",
        "requested_duration_frames": 12,
        "requested_duration_seconds": None,
        "requested_duration_feel": None,
        "compiled_duration_frames": 0,
        "status": "downgraded",
        "reason": "no V1 clip ends at frame 428",
    }


def test_build_report_keeps_seconds_and_feel_in_their_own_units():
    """Seconds and feel words must not be rewritten as requested frames."""
    (seconds, feel) = render_step._transition_items_for_report({
        "transitions": [
            {
                "transition_id": "trans_seconds",
                "transition_type": "fade_to_black",
                "requested_type": "fade_to_black",
                "duration_source": "stated_seconds",
                "duration_seconds": 1.0,
                "duration_frames": 30,
            },
            {
                "transition_id": "trans_feel",
                "transition_type": "cross_dissolve",
                "requested_type": "cross_dissolve",
                "duration_source": "feel",
                "duration_feel": "quick",
                "duration_frames": 6,
            },
        ],
    })

    assert seconds["requested_duration_seconds"] == 1.0
    assert seconds["requested_duration_frames"] is None
    assert seconds["compiled_duration_frames"] == 30
    assert feel["requested_duration_feel"] == "quick"
    assert feel["requested_duration_frames"] is None
    assert feel["compiled_duration_frames"] == 6


def test_render_output_payload_does_not_drop_transition_items():
    """The step's declared state output carries its build report through."""
    transition_items = [{
        "transition_id": "trans_001",
        "requested_type": "cross_dissolve",
        "compiled_type": "hard_cut",
        "cut_point_frame": 428,
        "cut_point_timeline": 14.267,
        "duration_source": "frames",
        "requested_duration_frames": 12,
        "requested_duration_seconds": None,
        "requested_duration_feel": None,
        "compiled_duration_frames": 0,
        "status": "downgraded",
        "reason": "no V1 clip ends at frame 428",
    }]
    payload = render_step._render_output_payload(
        {"transition_items": transition_items}, {})

    assert payload["transition_items"] == transition_items


# --------------------------------------------------------------------------
# From test_render_watch_in_render.py
#
# Render 6.01 wires the frames into its own review call.
#
# History: docs/evidence/resolve_test_history.md#test_render_watch_in_render.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# step.py does `from resolve_build_timeline import build_timeline` - a
# sibling import served by tests/conftest.py, which owns every non-root
# sys.path entry so collection order cannot change what it binds to.
from library.tools import render_watch

STEP_DIR = os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_6_01_render")
FLAG = "PIPELINE_PERCEPTUAL_QA"


def _fixture_rows():
    return [
        {"span_start": 0.0, "span_end": 8.0, "frames": 8,
         "file": "render__watch_0.000_8f1.0x.png"},
        {"span_start": 8.0, "span_end": 16.0, "frames": 8,
         "file": "render__watch_8.000_8f1.0x.png"},
    ]


def _stub_watch(monkeypatch, tmp_path, rows):
    """Stand in for the instrument: paths and strips without ffmpeg."""
    frames_dir = str(tmp_path / "watch")
    drawn = {"directory": frames_dir, "rows": list(rows),
             "missing": [], "duration": 16.0, "fps": 30.0}
    monkeypatch.setattr(
        render_watch, "watch_paths",
        lambda project_folder, video_path: (
            frames_dir, str(tmp_path / "export.watch.json")))
    monkeypatch.setattr(
        render_watch, "draw_watch_strips",
        lambda video_path, directory, **kwargs: drawn)
    return drawn


def _export_file(tmp_path):
    path = tmp_path / "export.mp4"
    path.write_bytes(b"\x00")
    return str(path)


# ── The flag gates the draw ──────────────────────────────────────────

def test_flag_off_draws_nothing(monkeypatch, tmp_path):
    """Default runs attach no frames: the draw must not even be attempted."""
    monkeypatch.delenv(FLAG, raising=False)

    def _boom(*args, **kwargs):
        raise AssertionError("draw attempted with the flag off")

    monkeypatch.setattr(render_watch, "draw_watch_strips", _boom)
    assert render_step._render_watch_block(
        _export_file(tmp_path), str(tmp_path)) == ""


def test_flag_on_draws_the_export(monkeypatch, tmp_path):
    """With the flag set the block maps the export's own strips."""
    monkeypatch.setenv(FLAG, "1")
    drawn = _stub_watch(monkeypatch, tmp_path, _fixture_rows())

    block = render_step._render_watch_block(
        _export_file(tmp_path), str(tmp_path))

    assert drawn["directory"] in block
    for row in drawn["rows"]:
        assert row["file"] in block


def test_draw_failure_is_said_not_fatal(monkeypatch, tmp_path, capsys):
    """A watch that breaks must not break a render: "" plus a warning."""
    monkeypatch.setenv(FLAG, "1")

    def _fail(*args, **kwargs):
        raise RuntimeError("ffmpeg gone")

    monkeypatch.setattr(
        render_watch, "watch_paths",
        lambda project_folder, video_path: ("d", "r"))
    monkeypatch.setattr(render_watch, "draw_watch_strips", _fail)

    assert render_step._render_watch_block(
        _export_file(tmp_path), str(tmp_path)) == ""
    assert "Render watch unavailable" in capsys.readouterr().err


def test_no_rows_is_no_watch(monkeypatch, tmp_path):
    """A draw that produced no strip is absence, not a clean verdict."""
    monkeypatch.setenv(FLAG, "1")
    _stub_watch(monkeypatch, tmp_path, [])
    assert render_step._render_watch_block(
        _export_file(tmp_path), str(tmp_path)) == ""


def test_no_project_folder_draws_nothing(monkeypatch, tmp_path, capsys):
    """Without a project there is nowhere layout-clean to put strips."""
    monkeypatch.setenv(FLAG, "1")
    _stub_watch(monkeypatch, tmp_path, _fixture_rows())
    assert render_step._render_watch_block(
        _export_file(tmp_path), "") == ""
    assert "No project_folder" in capsys.readouterr().err


# ── The marker is always replaced ────────────────────────────────────

def test_addition_records_the_absence_when_missing():
    """Flag off: the review must not be instructed to use an absent table."""
    for missing in (None, [], {}):
        text = render_step._visual_qa_prompt_addition(missing)
        assert "no `visual_qa` table" in text
        assert FLAG in text
        assert "Use these findings" not in text


# --------------------------------------------------------------------------
# From test_renderer_tooling_imports.py
#
# The renderer's verification layer must load when it is run as a SCRIPT.
#
# History: docs/evidence/resolve_test_history.md#test_renderer_tooling_imports.

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
RENDER_DIR = os.path.join(REPO, "library", "steps", "step_6_01_render")

# Every name the renderer binds in those import groups, and what dies with
# it if the import falls through to the None branch.
REQUIRED_TOOLING = [
    ("apply_super_scale", "neural Super Scale directives"),
    ("apply_stabilization", "neural stabilisation directives"),
    # No fairlight rows: the per-item stub (`get_preset` /
    # `apply_fairlight_preset`) is removed - it returned True having
    # applied nothing - and the renderer no longer imports that module.
    ("verify_clip_placement", "the clip placement QA station"),
    ("verify_transitions", "the transition QA station"),
    ("verify_color_grades", "the colour grade QA station"),
    ("verify_audio", "the audio QA station"),
    ("verify_fusion_comps", "the Fusion comp QA station"),
    ("run_full_timeline_qa", "the final timeline QA sweep"),
    ("plan_qa_checks", "the visual QA router"),
]

_PROBE = r"""
import json, os, sys, importlib.util

# Reproduce script invocation exactly: sys.path[0] is the SCRIPT's own
# directory. Nothing else from the repo is on the path.
render_dir = sys.argv[1]
sys.path.insert(0, render_dir)

# DaVinci Resolve is not running under CI, and the module imports it
# lazily inside functions, so nothing needs stubbing for the import.
spec = importlib.util.spec_from_file_location(
    "rbt_probe", os.path.join(render_dir, "resolve_build_timeline.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print(json.dumps({
    "bound": {n: getattr(mod, n, None) is not None for n in json.loads(sys.argv[2])},
    "errors": getattr(mod, "_TOOLING_IMPORT_ERRORS", None),
    "records_errors": hasattr(mod, "_TOOLING_IMPORT_ERRORS"),
}))
"""


def _probe():
    """Import the renderer the way the pipeline does, in a clean process."""
    names = [n for n, _ in REQUIRED_TOOLING]
    env = dict(os.environ)
    # A PYTHONPATH carrying the repo root would mask the bug.
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, RENDER_DIR, json.dumps(names)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=os.path.dirname(REPO),  # not the repo root, so '' cannot help
        env=env, timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"Importing resolve_build_timeline as a script failed outright:\n"
            f"{proc.stderr[-3000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_every_verification_tool_binds_in_a_scripted_run():
    result = _probe()
    missing = [
        f"{name} ({what})"
        for name, what in REQUIRED_TOOLING
        if not result["bound"].get(name)
    ]
    assert not missing, (
        "These renderer tools are None when the module is run as a script, "
        "so they silently do nothing in production:\n  - "
        + "\n  - ".join(missing)
        + f"\nRecorded import errors: {result['errors']}"
    )
