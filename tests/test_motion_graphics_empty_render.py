"""An overlay that draws nothing is not rendered.

Project 001 declares `default_brand`, which declares neither
`effect.motion_accents` nor `effect.motion_progress_bar`, and
`creative_direction` has no `title`, `subtitle`, `series_name` or
`episode_label` in its schema at all. Every resolved prop was therefore
`{title: "", subtitle: "", showUpperThird: true, showProgress: false,
showAccents: false}` - which the MotionGraphics composition draws as an
empty frame.

Measured by the pipeline audit: sampling all eight
`motion_graphics_segments/*.mov` at 2 Hz, `max(alpha > 10)` is **0** on
every frame of every file. The step still ran Remotion eight times, wrote
53.8 MB of ProRes 4444, and the renderer still placed eight clips on V4,
so `render.json` reported `V4: 8` - which reads as motion graphics
delivered.

The capability is untouched: a template that DOES declare accents, a
progress bar, or a creative direction that supplies a title still renders
every segment. Only the fully transparent render stops.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
STEP_DIR = REPO / "library" / "steps" / "step_4_06_render_motion_graphics"
for _p in (str(REPO), str(STEP_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_motion_props import generate_motion_props, props_draw_ink

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


def _template(name):
    with open(REPO / "library" / "templates" / f"{name}.yaml",
              encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── The predicate ─────────────────────────────────────────────────────

class TestPropsDrawInk:
    """Each of the three elements the composition can draw, alone."""

    BLANK = {"title": "", "subtitle": "", "showUpperThird": True,
             "showProgress": False, "showAccents": False}

    def test_the_shipped_001_props_draw_nothing(self):
        assert not props_draw_ink(dict(self.BLANK))

    def test_an_upper_third_with_no_text_draws_nothing(self):
        """`showUpperThird` alone is not ink - the element IS the text."""
        assert not props_draw_ink({**self.BLANK, "showUpperThird": True})
        assert not props_draw_ink({**self.BLANK, "title": "   ",
                                   "subtitle": "\n"})

    def test_a_title_is_ink(self):
        assert props_draw_ink({**self.BLANK, "title": "Episode One"})

    def test_a_subtitle_is_ink(self):
        assert props_draw_ink({**self.BLANK, "subtitle": "Part 2"})

    def test_text_behind_a_hidden_upper_third_is_not_ink(self):
        """The composition does not render the div at all."""
        assert not props_draw_ink({**self.BLANK, "showUpperThird": False,
                                   "title": "Episode One"})

    def test_accents_are_ink(self):
        assert props_draw_ink({**self.BLANK, "showAccents": True})

    def test_the_progress_bar_is_ink(self):
        assert props_draw_ink({**self.BLANK, "showProgress": True})


def test_default_brand_resolves_to_props_that_draw_nothing():
    """The concrete 001 configuration, end to end through the generator."""
    tmpl = _template("default_brand")
    got = generate_motion_props({}, {}, SPINE, brand_style=tmpl.get("style"),
                                brand_effect=tmpl.get("effect"))
    assert got, "the generator still produces props - only the render stops"
    assert not any(props_draw_ink(p) for p in got)


def test_a_template_that_declares_accents_still_draws():
    """The capability must survive: this is what must NOT be skipped."""
    tmpl = _template("shortform_energetic")
    got = generate_motion_props({}, {}, SPINE, brand_style=tmpl.get("style"),
                                brand_effect=tmpl.get("effect"))
    assert got
    assert all(props_draw_ink(p) for p in got)


def test_a_creative_direction_with_a_title_draws_nothing():
    """The upper third has never carried a word, and this says why.

    This test used to assert the opposite, and it passed only because it
    handed the generator a `title` no model writes: step 2.01's schema is
    eight fields of prose and `title` is not among them
    (library/tools/creative_direction.DIRECTION_KEYS), so the read
    returned "" on every real run.

    On-screen copy is ARTWORK and belongs to the project (AGENTS.md
    section 14). Nothing declares the upper third's copy today, so
    nothing is drawn and the render is skipped - which is the honest
    state, not a regression.
    """
    got = generate_motion_props({}, {"title": "The Shop"}, SPINE)
    assert got, "the generator still produces props - only the render stops"
    assert not any(props_draw_ink(p) for p in got)
    assert all(p["title"] == "" and p["subtitle"] == "" for p in got)


def test_the_predicate_covers_everything_the_composition_draws():
    """`props_draw_ink` is only correct while it knows every element.

    It reads three flags, so the composition must draw exactly three
    things and each behind one of them. PR #153 moved all three onto the
    safe area without adding a fourth - which is why the predicate
    survived that rebase - but the next change might not, and an element
    drawn outside a flag makes this step skip a render that would have
    put something on screen.

    If this fails, add the new element's flag to `props_draw_ink` rather
    than relaxing the assertion.
    """
    src = (REPO / "remotion-subtitles" / "src" / "compositions"
           / "MotionGraphics" / "index.tsx").read_text(encoding="utf-8")
    body = src[src.index("<AbsoluteFill"):]

    guards = re.findall(r"\{(\w+) &&", body)
    assert set(guards) == {"showUpperThird", "showAccents", "showProgress"}, (
        f"MotionGraphics draws behind flags {sorted(set(guards))}; "
        f"props_draw_ink reads showUpperThird / showAccents / "
        f"showProgress. A flag it does not know about means a render "
        f"gets skipped that would have drawn something.")

    # An AbsoluteFill with a background paints every pixel, so "nothing
    # draws" would stop being true for any prop at all.
    opening = body[:body.index(">") + 1]
    assert "backgroundColor" not in opening, (
        "the root AbsoluteFill has a background - the overlay is no "
        "longer transparent by construction and props_draw_ink is wrong "
        "about every prop")


# ── The step: no Remotion process is started ──────────────────────────

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
        [sys.executable, str(STEP_DIR / "step.py")],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        env=env,
    )
    log = tmp_path / "npx.log"
    return proc, (log.read_text() if log.exists() else "")


def _payload(tmp_path: Path, **kw):
    project = tmp_path / "project"
    (project / "pipeline_output").mkdir(parents=True, exist_ok=True)
    return {
        "project_folder": str(project),
        "audio_spine": SPINE,
        "enhancement_spec": {},
        "creative_direction": kw.pop("creative_direction", {}),
        "brand_style": kw.pop("brand_style", {}) or {},
        "brand_effect": kw.pop("brand_effect", {}) or {},
        "project_fps": 30,
    }


def test_the_step_starts_no_render_when_nothing_would_draw(tmp_path):
    tmpl = _template("default_brand")
    proc, log = _run_step(tmp_path, _payload(
        tmp_path, brand_style=tmpl.get("style"),
        brand_effect=tmpl.get("effect")))

    assert proc.returncode == 0, proc.stderr
    assert log == "", (
        f"Remotion was invoked {len(log.splitlines())} time(s) for an "
        f"overlay in which no pixel is ever opaque:\n{log}")

    out = json.loads(proc.stdout)["motion_graphics_overlay"]
    assert out["segments"] == []
    assert out["declared"] is False
    # NOT `available: false` - check_output_is_real in run_pipeline reads
    # that anywhere in a step's output as a failed run, and a template
    # declaring no motion graphics is the normal case.
    assert "available" not in out
    assert "draw nothing" in out["reason"]


def test_the_step_still_renders_what_a_template_declares(tmp_path):
    tmpl = _template("shortform_energetic")
    proc, log = _run_step(tmp_path, _payload(
        tmp_path, brand_style=tmpl.get("style"),
        brand_effect=tmpl.get("effect")))

    assert proc.returncode == 0, proc.stderr
    assert len(log.splitlines()) == len(SPINE["structure"]), (
        "a template that declares accents must still render every "
        f"segment; npx log was:\n{log}")
    out = json.loads(proc.stdout)["motion_graphics_overlay"]
    assert out["available"] is True
    assert len(out["segments"]) == len(SPINE["structure"])


def test_no_motion_graphics_is_not_a_failed_step(tmp_path):
    """The output must survive run_pipeline's hollow-output gate."""
    sys.path.insert(0, str(REPO / "library" / "processes" / "edit_video"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_run_pipeline_for_test",
        REPO / "library" / "processes" / "edit_video" / "run_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tmpl = _template("default_brand")
    proc, _ = _run_step(tmp_path, _payload(
        tmp_path, brand_style=tmpl.get("style"),
        brand_effect=tmpl.get("effect")))
    output = json.loads(proc.stdout)

    assert module.check_output_is_real("render_motion_graphics", output) == []
