"""Render 6.01 wires the frames into its own review call.

Captain's ruling on vep-llm-context-audit-decision-render-qa-llm-role:
"Wire the frames in" - the same route step 6.02 took. Step 6.01's model
call could ask for visual verdicts with `visual_qa` empty, because the
grabs run only behind `PIPELINE_PERCEPTUAL_QA` (off by default) while
the handoff unconditionally instructed the model to use the table.

So the deterministic half now draws render-watch strips off its own
export behind that same flag and emits them as `render_watch_frames` -
6.02's key, 6.02's module, 6.02's confess-when-absent handoff language -
and the `VISUAL_QA_INSTRUCTIONS` marker is always replaced: with the
table's description when the grabs ran, with the record of their
absence when they did not.

Nothing here renders, opens Resolve, or needs ffmpeg: the draw is
stubbed and only the wiring is exercised. A test builds its project
under `tmp_path`, or it skips. It never falls back to a real one.
"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# step.py does `from resolve_build_timeline import build_timeline` - a
# sibling import served by tests/conftest.py, which owns every non-root
# sys.path entry so collection order cannot change what it binds to.
from library.steps.step_6_01_render import step as render_step
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

def test_addition_describes_the_table_when_present():
    """Grabs ran: the review is told what the table is for."""
    text = render_step._visual_qa_prompt_addition(
        [{"type": "frame_grab_result"}])
    assert "visual_qa" in text
    assert "Use these findings" in text


def test_addition_records_the_absence_when_missing():
    """Flag off: the review must not be instructed to use an absent table."""
    for missing in (None, [], {}):
        text = render_step._visual_qa_prompt_addition(missing)
        assert "no `visual_qa` table" in text
        assert FLAG in text
        assert "Use these findings" not in text


# ── The contract around the wiring ───────────────────────────────────

def test_manifest_declares_both_frame_keys():
    """`validate_step_output` warns on undeclared extras: both keys the
    payload can carry - the new watch block and the already-emitted
    `visual_qa` table - must be declared, and neither may be required,
    because a default run carries neither."""
    manifest = json.loads(
        Path(STEP_DIR, "manifest.json").read_text(encoding="utf-8"))
    outputs = {o["name"]: o
               for o in manifest["interface"]["outputs"]}
    for key in ("render_watch_frames", "visual_qa"):
        assert key in outputs, f"manifest declares no {key}"
        assert outputs[key].get("required") is False, \
            f"{key} must be optional: a default run carries none"


def test_handoff_confesses_an_unwatched_render():
    """The 6.02 rule, in 6.01's words: an absent block must read as an
    unwatched render, never as a watched one that passed."""
    handoff = Path(STEP_DIR, "handoff.md").read_text(encoding="utf-8")
    assert "render_watch_frames" in handoff
    assert "NOTHING HAS WATCHED THIS RENDER" in handoff
    # The marker step.py replaces must still be there to be replaced.
    assert "<!-- VISUAL_QA_INSTRUCTIONS -->" in handoff
