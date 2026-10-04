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
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
import re
import copy


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
    """A fake `npx` that records calls and writes static PNG sequences."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    renderer = tmp_path / "fake-remotion.py"
    renderer.write_text(
        "import json, shutil, sys\n"
        "from pathlib import Path\n"
        "from PIL import Image\n"
        "args = sys.argv[1:]\n"
        "out = Path(args[3])\n"
        "props_path = args[args.index('--props') + 1]\n"
        "props = json.loads(Path(props_path).read_text(encoding='utf-8'))\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "probe = out / '.probe.png'\n"
        "Image.new('RGBA', (int(props['width']), int(props['height'])),\n"
        "          (0, 0, 0, 0)).save(probe)\n"
        "for i in range(int(props['durationInFrames'])):\n"
        "    shutil.copyfile(probe, out / f'frame-{i}.png')\n"
        "probe.unlink()\n",
        encoding="utf-8")
    npx = bindir / "npx"
    npx.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{tmp_path / "npx.log"}"\n'
        f'exec "{sys.executable}" "{renderer}" "$@"\n'
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


# --------------------------------------------------------------------------
# From test_motion_graphics_overlay_modes.py
#
# The motion-graphics carrying option: full canvas or the drawn union.
#
# `render_one_segment` behind a stubbed `subprocess.run`, so what is
# pinned is the unit's own decisions - names, records, fallback - without
# paying for Remotion. The default path (tight, since 2026-09-10) is
# asserted first; explicit full still renders full canvas.

NEEDS_FFMPEG = shutil.which("ffmpeg") is None
FFMPEG_REASON = "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_06_render_motion_graphics.post_bridge import (  # noqa: E402
    render_one_segment,
)

SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def _el(element, anchor="middle_centre", duration=60):
    return {
        "element": element,
        "anchor": anchor,
        "row": 0,
        "runs": [{"text": "and so my very", "type_role": "supporting"}],
        "color": "#FFDD55",
        "entrance": "fade",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": duration,
        "timelineProgressStart": 0.0,
        "timelineProgressEnd": 0.5,
        "footprint": None,
        "emphasis": None,
    }


def _planned(elements):
    total = max(e["startFrame"] + e["durationFrames"] for e in elements)
    return {
        "index": 0,
        "timeline_start": 1.0,
        "timeline_end": 1.0 + total / 30.0,
        "total_frames": total,
        "element_count": len(elements),
        "elements": sorted({e["element"] for e in elements}),
        "props": {
            "elements": elements,
            "fps": 30,
            "width": 1080,
            "height": 1920,
            "safeArea": dict(SAFE),
            "durationInFrames": total,
        },
    }


class _StubRun:
    """Acts like a successful `npx remotion render`.

    Receives the whole argv (`post_bridge` calls `subprocess.run`
    directly - there is no renderer seam here), so the output path is
    argv[4] and the props path (which carries the canvas the step
    chose) is the value after `--props`.

    `draw` decides whether anything lands there, and `animated`
    decides whether its rendered pixels change over the declared span.
    """

    def __init__(self, draw=True, animated=True, alpha=255):
        self.calls = []
        self.draw = draw
        self.animated = animated
        self.alpha = alpha
        # Patching `post_bridge.subprocess.run` patches the module
        # object itself, so ffmpeg calls made anywhere - the pad, this
        # stub's own drawing - arrive here too. Only `npx` is stubbed.
        self.real_run = subprocess.run

    def __call__(self, *args, **kwargs):
        argv = args[0]
        if not argv or argv[0] != "npx":
            return self.real_run(*args, **kwargs)
        self.calls.append(argv)
        frames_dir = argv[4]
        props = json.load(open(argv[argv.index("--props") + 1],
                               encoding="utf-8"))
        _draw_clip(frames_dir, props["width"], props["height"],
                   props["durationInFrames"], draw=self.draw,
                   animated=self.animated, alpha=self.alpha)

        class Done:
            returncode = 0
            stderr = ""
        return Done()


def _draw_clip(frames_dir, width, height, frames, *, draw=True,
               animated=False, alpha=255):
    """Write transparent PNG frames with static or changing pixels."""
    from PIL import Image, ImageDraw

    os.makedirs(frames_dir, exist_ok=True)
    block_w, block_h = max(width // 2, 2), max(height // 2, 2)
    for index in range(max(frames, 1)):
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if draw:
            dx = index % 7 if animated else 0
            x0 = (width - block_w) // 2 + dx
            y0 = (height - block_h) // 2
            ImageDraw.Draw(image).rectangle(
                (x0, y0, x0 + block_w - 1, y0 + block_h - 1),
                fill=(255, 255, 255, alpha))
        image.save(os.path.join(frames_dir,
                                f"frame-{index + 1:06d}.png"))


def _render(monkeypatch, planned, out_dir, draw=True, animated=True,
            alpha=255, **kwargs):
    stub = _StubRun(draw=draw, animated=animated, alpha=alpha)
    monkeypatch.setattr(
        "library.steps.step_4_06_render_motion_graphics.post_bridge.subprocess.run",
        stub,
    )
    return render_one_segment(planned, out_dir, **kwargs), stub


def test_default_path_is_tight(tmp_path, monkeypatch):
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path))
    name = os.path.basename(out["overlay_path"])
    assert re.fullmatch(r"mg_noproject_[0-9a-f]{8}\.mov", name), name
    assert "_tight" not in name
    assert out["geometry"] == "tight"
    assert out["tight_box"] is not None
    assert os.path.isfile(out["overlay_path"])


def test_explicit_full_path_is_unchanged(tmp_path, monkeypatch):
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), overlay_geometry="full")
    name = os.path.basename(out["overlay_path"])
    assert re.fullmatch(r"mg_noproject_[0-9a-f]{8}\.mov", name), name
    assert "_tight" not in name
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert out["placement_label"] == "mg_000"
    assert os.path.isfile(out["overlay_path"])


def test_identical_rendered_pixels_are_kept_as_one_still(
        tmp_path, monkeypatch):
    from library.tools import hyperframes_render

    def must_not_encode(*_args, **_kwargs):
        pytest.fail("static pixels must not be encoded as a movie")

    monkeypatch.setattr(hyperframes_render, "encode_frames", must_not_encode)
    out, stub = _render(
        monkeypatch, _planned([_el("title_lockup", duration=24)]),
        str(tmp_path), animated=False, alpha=128)

    assert stub.calls
    assert out["media_type"] == "still"
    assert out["overlay_path"].endswith(".png")
    assert out["total_frames"] == 24
    assert os.path.isfile(out["overlay_path"])
    assert not list(tmp_path.glob("*.mov"))
    from PIL import Image
    import numpy as np
    with Image.open(out["overlay_path"]) as still:
        pixels = np.asarray(still.convert("RGBA"))
    center = pixels[pixels.shape[0] // 2, pixels.shape[1] // 2]
    assert tuple(center) == (128, 128, 128, 128)


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_tight_renders_the_union_and_places_it_by_transform(
        tmp_path, monkeypatch):
    """The union is what Remotion DRAWS; the placement is what carries
    it. The record keeps the union's size and its Pan/Tilt, and the
    artefact on disk IS the small canvas - Resolve transforms it into
    place and reads the transform back."""
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), overlay_geometry="tight")
    name = os.path.basename(out["overlay_path"])
    assert re.fullmatch(r"mg_noproject_[0-9a-f]{8}\.mov", name), name
    assert out["geometry"] == "tight"
    box = out["tight_box"]
    assert box["width"] < 1080 and box["height"] < 1920
    assert "origin" not in box
    placement = box["placement"]
    assert abs(placement["tilt"]) <= 3400
    stem = out["overlay_path"][:-len(".mov")]
    props_on_disk = json.load(open(stem + "_props.json", encoding="utf-8"))
    assert props_on_disk["width"] == box["width"]
    assert props_on_disk["height"] == box["height"]

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         out["overlay_path"]],
        capture_output=True, text=True, encoding="utf-8", check=True)
    assert probe.stdout.strip() == (
        f"{box['width']},{box['height']}"), (
        "the artefact Resolve transforms must BE the tight canvas, "
        "not the delivery frame")


def test_a_predicted_clamp_refusal_is_retried_from_pixels(
        tmp_path, monkeypatch):
    """The prediction is not the verdict: a graphic the predicted
    clamp gate refuses is rebound from its own rendered pixels, and a
    probe whose ink binds reaches the timeline as a verified tight
    crop rather than full canvas."""
    import library.tools.mg_tight_box as mg_tight_box
    from library.tools.tight_box import TightBoxMismatch

    def _refuse(*args, **kwargs):
        raise TightBoxMismatch("exceeds the rail")
    monkeypatch.setattr(mg_tight_box,
                        "tighten_motion_graphics_props_with_reason",
                        _refuse)
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), overlay_geometry="tight")
    assert out["geometry"] == "tight"
    assert out["tight_fallback"] == ""
    assert out["tight_box"] is not None
    assert out["overlay_path"].endswith("_tight.mov")
    assert out["tight_report"]["before"] == [1080, 1920]
    stem = out["overlay_path"][:-len(".mov")]
    sidecar = json.load(open(stem + "_tightness.json", encoding="utf-8"))
    assert sidecar["outcome"] == "tight"
    # The probe keeps its own account beside it: full canvas, with
    # the predicted refusal named - a reasoned full canvas, never a
    # silent one.
    probe_sidecar = json.load(
        open(stem[:-len("_tight")] + "_tightness.json",
             encoding="utf-8"))
    assert probe_sidecar["outcome"] == "full"
    assert probe_sidecar["reason"] == "placement_unholdable"
    assert probe_sidecar["element"] == "title_lockup"


def test_explicit_full_declares_itself_on_the_artefact(
        tmp_path, monkeypatch):
    """A project that declares full-canvas carrying never asks the
    tighten path - and the artefact still says why it is full canvas,
    so the build-time guard passes it by declaration, not by sight."""
    out, _ = _render(monkeypatch, _planned([_el("title_lockup")]),
                     str(tmp_path), overlay_geometry="full")
    assert out["geometry"] == "full"
    stem = out["overlay_path"][:-len(".mov")]
    sidecar = json.load(open(stem + "_tightness.json", encoding="utf-8"))
    assert sidecar["outcome"] == "full"
    assert sidecar["reason"] == "geometry_full_declared"
    from library.tools.mg_tight_box import check_motion_graphics_files
    errors, census = check_motion_graphics_files(
        [stem + "_props.json"], 1080, 1920)
    assert errors == []
    assert census["full_by_design"] == 1


def test_accents_stay_full_canvas_when_tight_is_asked(tmp_path, monkeypatch):
    out, _ = _render(monkeypatch, _planned([_el("frame_accents")]),
                     str(tmp_path), overlay_geometry="tight")
    assert re.fullmatch(r"mg_noproject_[0-9a-f]{8}\.mov",
                        os.path.basename(out["overlay_path"]))
    assert "_tight" not in out["overlay_path"]
    # The file IS full canvas, so the record says full: a structural
    # refusal that kept the requested "tight" is how full-canvas files
    # passed as tighten candidates. The refusal names itself, on the
    # record and on the artefact sidecar beside the props.
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert "frame_accents_span_by_design" in out["tight_fallback"]
    stem = out["overlay_path"][:-len(".mov")]
    sidecar = json.load(open(stem + "_tightness.json", encoding="utf-8"))
    assert sidecar["outcome"] == "full"
    assert sidecar["reason"] == "frame_accents_span_by_design"
    assert sidecar["element"] == "frame_accents"


# --------------------------------------------------------------------------
# From test_motion_graphics_reuse_digest.py
#
# Motion-graphics reuse is decided by pixels, never by placing.
#
# The reel lower-third path renders one speaker card per reel through
# `render_one_segment` with `reuse=True` - but the drawing digest hashed
# the whole props object, including the placement and provenance keys
# the plan carries for readers (`timeline_start`/`timeline_end`, the
# whole-piece `timelineProgress*` fractions, `timing_basis`,
# `subject`, `why`, `colorBasis`). Every reel's card therefore digested
# differently and re-rendered from scratch: measured on geo-podcast as
# 26 SpeakerTwo cards and 27 SpeakerOne cards decoding framemd5-identical while
# carrying 53 distinct digests.
#
# `render_one_segment` behind a stubbed `subprocess.run`, so what is
# pinned is the unit's own decisions without paying for Remotion.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_06_render_motion_graphics.post_bridge import (  # noqa: E402
    _mg_drawing_digest,
)
from library.tools.render_cache import (  # noqa: E402
    motion_segment_name,
)


def _lower_third(progress_end=0.0438, timeline_start=0.0):
    return {
        "element": "lower_third",
        "anchor": "bottom_left",
        "row": 0,
        "runs": [{"text": "SpeakerTwo Lucie", "type_role": "display"},
                 {"text": "CEO Lucie Content",
                  "type_role": "supporting"}],
        "color": "#FBF0B8",
        "colorBasis": "stated by the plan",
        "entrance": "draw",
        "exit": "fade",
        "timeline_start": timeline_start,
        "timeline_end": timeline_start + 3.5,
        "timing_basis": "declared",
        "subject": "",
        "timelineProgressStart": 0.0,
        "timelineProgressEnd": progress_end,
        "startFrame": 0,
        "durationFrames": 84,
        "asset": "",
        "footprint": None,
        "emphasis": None,
        "why": "first appearance of 'SpeakerTwo' in this reel, at 0.0s",
        "data": {"construction": "staged_rule",
                 "speaker": "SpeakerTwo"},
    }


def _props(element):
    return {
        "elements": [element],
        "fps": 23.976023976023978,
        "width": 514,
        "height": 480,
        "safeArea": {"top": 310, "right": 48,
                     "bottom": 48, "left": 48},
        "durationInFrames": 84,
    }


def test_reel_variants_share_a_digest():
    """Two reels' placings of one card digest identically.

    The progress fractions move with each reel's length and the
    bounds with each placing; neither is read by the composition
    for a `lower_third`, so neither may decide reuse.
    """
    first = _mg_drawing_digest(
        _props(_lower_third(progress_end=0.0438,
                            timeline_start=0.0)), "full", None)
    second = _mg_drawing_digest(
        _props(_lower_third(progress_end=0.0667,
                            timeline_start=19.06)), "full", None)
    assert first == second
    assert (motion_segment_name("", first)
            == motion_segment_name("", second))


def test_provenance_edits_share_a_digest():
    """The model's reasoning and the plan's provenance never re-render."""
    base = _lower_third()
    edited = copy.deepcopy(base)
    edited["why"] = "second appearance, later in the reel"
    edited["subject"] = "the guest"
    edited["timing_basis"] = "word_window:SpeakerTwo"
    edited["colorBasis"] = "pipeline.speaker_subtitle_styles['SpeakerTwo']"
    assert (_mg_drawing_digest(_props(base), "full", None)
            == _mg_drawing_digest(_props(edited), "full", None))


def test_progress_bar_progress_still_draws():
    """The one element that draws its fractions keeps them in the digest."""
    base = _lower_third()
    base["element"] = "progress_bar"
    moved = copy.deepcopy(base)
    moved["timelineProgressEnd"] = 0.9
    assert (_mg_drawing_digest(_props(base), "full", None)
            != _mg_drawing_digest(_props(moved), "full", None))


def _planned_2(element, timeline_start=0.0):
    total = element["startFrame"] + element["durationFrames"]
    return {
        "index": 0,
        "timeline_start": timeline_start,
        "timeline_end": timeline_start + total / 30.0,
        "total_frames": total,
        "element_count": 1,
        "elements": [element["element"]],
        "props": _props(element),
    }


# --------------------------------------------------------------------------
# From test_graphics_renderer_selection.py
#
# The graphics-engine seam: the project wins over the user, both lose to nothing.
#
# The defect this names: without a precedence test, a later edit can make
# the user's machine setting override the project's own declaration (or
# make an unset key read as a choice), and every run on that machine
# would draw with an engine nobody's video chose while reporting
# success. Remotion staying the default is the other half: nothing he
# sees changes unless something is selected.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import graphics_renderer as engines  # noqa: E402


def _project_with(tmp_path, value) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "project.yaml").write_text(
        f"name: Probe\nslug: probe\npipeline:\n"
        f"  graphics_renderer: {value}\n",
        encoding="utf-8")
    return str(tmp_path)


def test_the_project_wins_over_the_user_and_both_lose_to_nothing(
        tmp_path, monkeypatch):
    monkeypatch.delenv(engines.USER_SETTING_KEY, raising=False)
    assert engines.resolve_engine(str(tmp_path)) == "remotion"
    assert engines.resolve_engine() == "remotion"
    assert not engines.is_hyperframes(str(tmp_path))

    # The user's machine setting selects, case- and space-insensitively.
    monkeypatch.setenv(engines.USER_SETTING_KEY, "  HyperFrames  ")
    assert engines.resolve_engine(str(tmp_path)) == "hyperframes"
    assert engines.is_hyperframes(str(tmp_path))

    # The project's own declaration wins, in both directions ...
    for user, project in (("remotion", "hyperframes"),
                          ("hyperframes", "remotion")):
        monkeypatch.setenv(engines.USER_SETTING_KEY, user)
        folder = _project_with(tmp_path / project, project)
        assert engines.resolve_engine(folder) == project
    assert not engines.is_hyperframes(folder)

    # ... and a project that declares nothing falls back to the user.
    monkeypatch.setenv(engines.USER_SETTING_KEY, "hyperframes")
    (tmp_path / "project.yaml").write_text(
        "name: Probe\nslug: probe\npipeline:\n  brand_template: x\n",
        encoding="utf-8")
    assert engines.resolve_engine(str(tmp_path)) == "hyperframes"


def test_an_unknown_engine_refuses_from_either_source(tmp_path, monkeypatch):
    monkeypatch.setenv(engines.USER_SETTING_KEY, "flash")
    with pytest.raises(engines.UnknownGraphicsEngine):
        engines.resolve_engine()
    monkeypatch.delenv(engines.USER_SETTING_KEY, raising=False)
    folder = _project_with(tmp_path, "flash")
    with pytest.raises(engines.UnknownGraphicsEngine):
        engines.resolve_engine(folder)
