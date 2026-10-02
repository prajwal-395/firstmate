"""An empty motion-graphics layer says WHICH absence it is, and no template gates one.

**What this file used to hold.** Project 001 declared `default_brand`,
which declares neither `effect.motion_accents` nor
`effect.motion_progress_bar`, so every resolved prop was
`{title: "", showUpperThird: true, showProgress: false, showAccents:
false}` - eight ProRes 4444 renders in which `max(alpha)` is 0 on every
frame of every file, placed on V4, reported as `V4: 8`. These tests
stopped the transparent render.

**What replaced it.** The captain's ruling of 2026-09-02 is that the
gate was the bug rather than the render: *"the LLM was still meant to
plan these things and implement them properly, the brand template is
only a secondary"*. So a model plans the layer, a template refines it,
and an empty layer is now a decision somebody took rather than two
absent booleans.

The transparent render still cannot happen - `props_draw_ink` is now the
much shorter statement that a segment with no element draws nothing -
and the new claim these tests hold is the one the old design could not
make: **an empty layer states which absence it is.**
`no_elements_planned` is a plan of none; `every_entry_dropped` is a plan
whose entries all died, each named with its reason. Reading those two as
one another is what let 001 report a delivered layer.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[3]
STEP_DIR = REPO / "library" / "steps" / "step_4_06_render_motion_graphics"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import motion_graphics_plan as mgp  # noqa: E402

SPINE = {
    "structure": [
        {"block_type": "hook", "position": 0,
         "timeline_start": 0.0, "timeline_end": 3.0},
        {"block_type": "speech", "position": 1,
         "timeline_start": 3.0, "timeline_end": 8.0},
        {"block_type": "speech", "position": 2,
         "timeline_start": 8.0, "timeline_end": 14.0},
    ]
}

#: A plan a model could write for this spine, in a project with NO brand
#: template. Two elements at one moment, on two rows, and a third on its
#: own timescale straddling a block boundary.
A_PLAN = [
    {"element": "title_lockup", "start_seconds": 0.3,
     "duration_seconds": 2.2, "anchor": "top_left", "row": 0,
     "copy": {"display": "DAY 001", "supporting": "the first one"},
     "color": "#F5F5F0", "entrance": "slide", "exit": "fade",
     "why": "the viewer needs a name for what they are watching"},
    {"element": "frame_accents", "start_seconds": 0.3,
     "duration_seconds": 2.2, "anchor": "centre", "row": 1,
     "color": "#FF8A3D", "entrance": "draw", "exit": "fade",
     "why": "chrome, declared as chrome"},
    {"element": "progress_bar", "start_seconds": 2.4,
     "duration_seconds": 9.0, "anchor": "bottom_centre",
     "color": "#FF8A3D", "entrance": "fade", "exit": "cut",
     "why": "how far through the piece the viewer is"},
]


def _template(name):
    from tests.brand_fixtures import ALL_SYNTHETIC
    return dict(ALL_SYNTHETIC[name])


# ── The predicate ─────────────────────────────────────────────────────


# ── The step ──────────────────────────────────────────────────────────

def _stub_npx(tmp_path: Path) -> Path:
    """A fake `npx` that records every invocation instead of rendering.

    The assertion is about the SUBPROCESS: an empty render is expensive
    because Remotion runs, not because a file lands on disk.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    npx = bindir / "npx"
    npx.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{tmp_path / "npx.log"}"\n'
        "exit 0\n"
    )
    npx.chmod(0o755)
    return bindir


def _run_step(tmp_path: Path, payload: dict):
    bindir = _stub_npx(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(STEP_DIR / "post_bridge.py")],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        env=env,
    )
    log = tmp_path / "npx.log"
    return proc, (log.read_text() if log.exists() else "")


def _payload(tmp_path: Path, **kw):
    project = tmp_path / "project"
    (project / "pipeline_output").mkdir(parents=True, exist_ok=True)
    payload = {
        "project_folder": str(project),
        "audio_spine": SPINE,
        "brand_style": kw.pop("brand_style", {}) or {},
        "brand_effect": kw.pop("brand_effect", {}) or {},
        "project_fps": 30,
    }
    if "motion_graphics_plan" in kw:
        payload["motion_graphics_plan"] = kw.pop("motion_graphics_plan")
    return payload


def test_a_plan_renders_with_no_brand_template_at_all(tmp_path):
    """The claim the old design could not make.

    No `brand_style`, no `brand_effect`, no template named anywhere -
    and Remotion is invoked, because the model planned a layer and the
    template was never what decided.
    """
    proc, log = _run_step(tmp_path, _payload(
        tmp_path, motion_graphics_plan=A_PLAN))

    assert proc.returncode == 0, proc.stderr
    assert log.splitlines(), (
        "no Remotion invocation for a plan with three drawable elements "
        "and no brand template - the gate is back")

    out = json.loads(proc.stdout)["motion_graphics_overlay"]
    assert out["available"] is True
    assert out["planning_basis"]["basis"] == mgp.ELEMENTS_PLANNED
    assert out["planning_basis"]["resolved"] == 3
    assert not out["planning_basis"]["dropped"]


def test_a_plan_of_none_starts_no_render_and_says_it_was_a_decision(tmp_path):
    proc, log = _run_step(tmp_path, _payload(
        tmp_path, motion_graphics_plan=[]))

    assert proc.returncode == 0, proc.stderr
    assert log == "", f"Remotion ran for an empty plan:\n{log}"

    out = json.loads(proc.stdout)["motion_graphics_overlay"]
    assert out["segments"] == []
    assert out["declared"] is False
    # NOT `available: false` - check_output_is_real in run_pipeline reads
    # that anywhere in a step's output as a failed run, and a plan of
    # none is a legitimate answer.
    assert "available" not in out
    assert out["planning_basis"]["basis"] == mgp.NO_ELEMENTS_PLANNED


def test_a_plan_whose_entries_all_died_is_a_different_absence(tmp_path):
    """`every_entry_dropped` is spelled differently from
    `no_elements_planned` on purpose, and the casualties are named."""
    proc, log = _run_step(tmp_path, _payload(
        tmp_path, motion_graphics_plan=[
            # `channel_bug` draws now (its component and its asset
            # resolution landed together), so the entry that dies here is
            # one naming a file the project does not have. Same shape of
            # refusal, still a real one.
            {"element": "channel_bug", "start_seconds": 1.0,
             "duration_seconds": 2.0, "anchor": "centre",
             "asset": "no_such_file.png", "color": "#fff"},
            {"element": "title_lockup", "start_seconds": 1.0,
             "duration_seconds": 2.0, "anchor": "centre",
             "copy": {"display": "no colour anywhere"}},
        ]))
    assert proc.returncode == 0, proc.stderr
    assert log == ""
    basis = json.loads(proc.stdout)["motion_graphics_overlay"]["planning_basis"]
    assert basis["basis"] == mgp.EVERY_ENTRY_DROPPED
    reasons = {row["reason"] for row in basis["dropped"]}
    assert reasons == {"asset_not_found_on_disk", "no_colour_to_draw_it_in"}
    for row in basis["dropped"]:
        assert row["what_the_reason_means"]


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)")


def _title_entry(layer=None):
    entry = {
        "element": "title_lockup", "anchor": "centre", "row": 0,
        "copy": {"display": "MORNING WALK"},
        "color": "#FFFFFF", "colour_role": "text",
        "entrance": "cut", "exit": "cut",
        "start_seconds": 0.0, "duration_seconds": 0.5,
        "why": "opening title for the walk",
    }
    if layer is not None:
        entry["layer"] = layer
    return entry


def _draw_mov(path, frames=15):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=white:s=64x64:d=1:r=30,format=rgba",
         "-frames:v", str(frames), "-c:v", "qtrle", str(path)],
        check=True)


def _render_shapes(monkeypatch, tmp_path, plan):
    """The step's output shapes with the renderer stubbed out.

    `render_one_segment` is the Remotion half (covered by its own
    tests); what is pinned here is what the step SAYS about each
    side of the layer - and that the hollow check reads it as a
    legitimate answer, not a failed run.
    """
    import library.steps.step_4_06_render_motion_graphics.post_bridge as pb
    from library.processes.edit_video.run_pipeline import (
        check_output_is_real)

    mov = tmp_path / "title.mov"
    _draw_mov(mov)
    canned = {"segment_id": "mg_000", "overlay_path": str(mov),
              "total_frames": 15,
              "timeline_start": 0.0, "timeline_end": 0.5}
    monkeypatch.setattr(
        pb, "render_one_segment", lambda *a, **k: dict(canned))
    monkeypatch.setattr(
        "library.tools.mg_tight_box.check_motion_graphics_files",
        lambda paths, w, h: ([], {"tight": 0, "full_by_design": 1,
                                  "full_with_reason": 0,
                                  "full_undeclared": 0}))
    payload = _payload(tmp_path, motion_graphics_plan=plan)
    out = pb.render_motion_graphics(payload)
    assert check_output_is_real("render_motion_graphics", out) == []
    return out


@needs_ffmpeg
def test_a_behind_only_plan_states_the_empty_above_side(tmp_path,
                                                        monkeypatch):
    out = _render_shapes(monkeypatch, tmp_path,
                         [_title_entry(layer="behind_subject")])
    behind = out["behind_subject_overlays"]
    assert behind["available"] is True
    assert len(behind["segments"]) == 1
    seg = behind["segments"][0]
    # The segment was sequenced for the compile precomposite: the
    # manifest names the first PNG frame, not the .mov.
    assert seg["overlay_path"].endswith(".png")
    assert seg["sequence"]["frame_count"] == 15
    assert os.path.isfile(seg["overlay_path"])
    above = out["motion_graphics_overlay"]
    assert above["available"] is False
    assert above["segments"] == []
    assert "behind_subject_overlays" in above["reason"]


@needs_ffmpeg
def test_an_above_only_plan_states_the_empty_behind_side(tmp_path,
                                                         monkeypatch):
    out = _render_shapes(monkeypatch, tmp_path, [_title_entry()])
    above = out["motion_graphics_overlay"]
    assert above["available"] is True
    assert len(above["segments"]) == 1
    behind = out["behind_subject_overlays"]
    assert behind["available"] is False
    assert behind["segments"] == []
    assert "behind_subject" in behind["reason"]


def test_a_plan_of_none_is_hollow_clean_on_both_sides(tmp_path):
    proc, _ = _run_step(tmp_path, _payload(
        tmp_path, motion_graphics_plan=[]))
    assert proc.returncode == 0, proc.stderr
    from library.processes.edit_video.run_pipeline import (
        check_output_is_real)
    out = json.loads(proc.stdout)
    assert check_output_is_real("render_motion_graphics", out) == []
