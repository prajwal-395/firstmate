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
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
STEP_DIR = REPO / "library" / "steps" / "step_4_06_render_motion_graphics"
for _p in (str(REPO), str(STEP_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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
    with open(REPO / "library" / "templates" / f"{name}.yaml",
              encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── The predicate ─────────────────────────────────────────────────────

def test_props_with_no_element_draw_nothing_and_props_with_one_draw():
    assert not mgp.props_draw_ink({"elements": []})
    assert not mgp.props_draw_ink({})
    assert mgp.props_draw_ink({"elements": [{"element": "progress_bar"}]})


def test_the_overlay_is_still_transparent_by_construction():
    """`props_draw_ink` is only true because nothing else paints a pixel.

    An `AbsoluteFill` with a background would paint every one of them,
    and then "this segment draws nothing" stops being true for any
    props at all.
    """
    composition = (REPO / "remotion-subtitles" / "src" / "compositions"
                   / "MotionGraphics" / "index.tsx").read_text(encoding="utf-8")
    body = composition[composition.index("<AbsoluteFill"):]
    opening = body[:body.index(">") + 1]
    assert "backgroundColor" not in opening, (
        "the root AbsoluteFill has a background - the overlay is no "
        "longer transparent by construction and props_draw_ink is wrong "
        "about every prop")


def test_the_predicate_covers_everything_the_composition_draws():
    """Every arm of the composition's element switch is a roster key that
    `resolve_plan` can actually produce, so a segment reaching the
    renderer with an element in it really does draw."""
    composition = (REPO / "remotion-subtitles" / "src" / "compositions"
                   / "MotionGraphics" / "index.tsx").read_text(encoding="utf-8")
    drawn = {key for key in mgp.DRAWABLE
             if f'element.element === "{key}"' in composition}
    assert drawn == set(mgp.DRAWABLE), (
        f"the composition draws {sorted(drawn)} and the planner offers "
        f"{sorted(mgp.DRAWABLE)}. An element the planner resolves and the "
        f"renderer has no arm for is a transparent render again.")


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


def test_a_template_that_declares_nothing_no_longer_empties_the_layer(tmp_path):
    """`default_brand` declares neither motion flag. It used to be the
    whole decision; now it is not a decision at all."""
    tmpl = _template("default_brand")
    proc, log = _run_step(tmp_path, _payload(
        tmp_path, motion_graphics_plan=A_PLAN,
        brand_style=tmpl.get("style"), brand_effect=tmpl.get("effect")))
    assert proc.returncode == 0, proc.stderr
    assert log.splitlines()
    out = json.loads(proc.stdout)["motion_graphics_overlay"]
    assert out["available"] is True


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
            {"element": "channel_bug", "start_seconds": 1.0,
             "duration_seconds": 2.0, "anchor": "centre",
             "copy": {"display": "42%"}, "color": "#fff"},
            {"element": "title_lockup", "start_seconds": 1.0,
             "duration_seconds": 2.0, "anchor": "centre",
             "copy": {"display": "no colour anywhere"}},
        ]))
    assert proc.returncode == 0, proc.stderr
    assert log == ""
    basis = json.loads(proc.stdout)["motion_graphics_overlay"]["planning_basis"]
    assert basis["basis"] == mgp.EVERY_ENTRY_DROPPED
    reasons = {row["reason"] for row in basis["dropped"]}
    assert reasons == {"renderer_cannot_draw_it_yet", "no_colour_to_draw_it_in"}
    for row in basis["dropped"]:
        assert row["what_the_reason_means"]


def test_a_run_that_asked_for_no_plan_is_a_third_reading(tmp_path):
    """No `motion_graphics_plan` key at all - a `--step` invocation of
    the post-bridge alone, say. Not a plan of none."""
    proc, _ = _run_step(tmp_path, _payload(tmp_path))
    assert proc.returncode == 0, proc.stderr
    basis = json.loads(proc.stdout)["motion_graphics_overlay"]["planning_basis"]
    assert basis["basis"] == mgp.NOT_PLANNED


def test_no_motion_graphics_is_not_a_failed_step(tmp_path):
    """The output must survive run_pipeline's hollow-output gate."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_run_pipeline_for_test",
        REPO / "library" / "processes" / "edit_video" / "run_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    proc, _ = _run_step(tmp_path, _payload(tmp_path, motion_graphics_plan=[]))
    output = json.loads(proc.stdout)

    assert module.check_output_is_real("render_motion_graphics", output) == []
