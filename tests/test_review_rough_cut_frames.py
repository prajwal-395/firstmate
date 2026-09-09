"""The rough-cut review sees the cut it is reviewing.

Step 3.03 judged the assembled cut from prose alone: its context carried
`project_folder` and raw file paths, so a model with file tools COULD
reach the footage, but nothing routed a picture in and nothing recorded
whether one was seen. A backend without file tools had to guess. That is
the optional-sight shape - the same one `window_frames.py` already
answers for step 3.02, which hands the model one strip per candidate
window.

This step's windows are PLACED, not candidate: every A-roll video
segment and every B-roll overlay the cut actually plays. The
deterministic half draws one strip per placed window and the prompt
carries the map, so sight is routed and recorded rather than optional.

These tests FOLLOW the reference the way `tests/test_window_frames.py`
does: they parse the directory and filenames out of the string the model
reads, open what comes back, and require a real picture of the placed
window. A test that only checked the text would pass on a map pointing
at nothing.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import window_frames as wf

REPO = Path(__file__).resolve().parents[1]
STEP = (REPO / "library" / "steps" / "step_3_03_review_rough_cut"
        / "step.py")

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")

OUTPUT_KEY = "roughcut_window_frames"


def _fixture_clip(path: Path, seconds: float = 20.0) -> Path:
    """A clip whose picture CHANGES, so a strip can show that it does."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size=640x360:rate=30:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, encoding="utf-8",
    )
    return path


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "pipeline_output").mkdir(parents=True)
    (root / "project.yaml").write_text("name: fixture\n", encoding="utf-8")
    return root


def _bridge_inputs(root: Path, clip: Path) -> dict:
    return {
        "project_folder": str(root),
        "a_roll_assignments": [{
            "spine_block_position": 1,
            "block_type": "speech",
            "timeline_start": 0.0,
            "timeline_end": 4.0,
            "video_segments": [{
                "clip_id": "clip_001",
                "source_file": str(clip),
                "video_in": 5.0,
                "video_out": 9.0,
                "frame_rate": 30.0,
            }],
        }],
        "b_roll_assignments": [{
            "spine_block_position": 1,
            "clip_id": "clip_001",
            "source_file": str(clip),
            "video_in": 12.0,
            "video_out": 14.5,
            "timeline_start": 1.0,
            "timeline_end": 3.5,
        }],
        "b_roll_interjections": [{
            "assigned_clip": {
                "clip_id": "clip_001",
                "source_file": str(clip),
                "video_in": 1.0,
                "video_out": 2.382,
            },
            "timeline_start": 4.0,
            "timeline_end": 5.382,
        }],
    }


def _run_step(data: dict) -> dict:
    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, str(STEP)],
        input=json.dumps(data),
        capture_output=True, encoding="utf-8", cwd=str(REPO), env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["rough_cut_review"]["passed"], (
        f"the fixture cut must pass the mechanical gates, or the frames "
        f"are tested against a cut the pipeline would refuse: "
        f"{out['rough_cut_review'].get('rejection_reasons')}")
    return out


def _placed_windows(data: dict) -> set:
    """Every (clip_id, video_in) the cut plays, derived from the inputs."""
    out = set()
    for ar in data.get("a_roll_assignments", []):
        for seg in ar.get("video_segments", []):
            out.add((seg["clip_id"], round(float(seg["video_in"]), 3)))
    for br in data.get("b_roll_assignments", []):
        out.add((br["clip_id"], round(float(br["video_in"]), 3)))
    for interj in data.get("b_roll_interjections", []):
        clip = interj.get("assigned_clip", {})
        out.add((clip["clip_id"], round(float(clip["video_in"]), 3)))
    return out


# ── The bridge routes sight ──────────────────────────────────────────

@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_step_draws_a_strip_per_placed_window(tmp_path):
    clip = _fixture_clip(tmp_path / "clip_001.mp4")
    root = _project(tmp_path)
    data = _bridge_inputs(root, clip)

    out = _run_step(data)
    assert OUTPUT_KEY in out, (
        f"the step drew no {OUTPUT_KEY}: the review still decides "
        f"from prose alone")
    assert out[OUTPUT_KEY]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_every_placed_window_is_shown_and_none_is_invented(tmp_path):
    clip = _fixture_clip(tmp_path / "clip_001.mp4")
    root = _project(tmp_path)
    data = _bridge_inputs(root, clip)

    block = _run_step(data)[OUTPUT_KEY]
    rows = [l for l in block.splitlines() if l.startswith("clip_001\t")]
    assert rows, "no row names the placed clip"
    # Columns are clip_id, position, timeline_start, video_in,
    # video_out, frames, file - the same order REVIEW_HEADERS declares.
    shown = {(l.split("\t")[0], round(float(l.split("\t")[3]), 3))
             for l in rows}
    assert shown == _placed_windows(data), (
        f"shown={sorted(shown)} placed={sorted(_placed_windows(data))}: "
        f"a placed window with no strip is unseen sight, and a strip "
        f"of no placed window is sight of another cut")


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_built_context_carries_images_a_reader_can_open(tmp_path):
    """The done-check: real image parts, reachable from the context."""
    clip = _fixture_clip(tmp_path / "clip_001.mp4")
    root = _project(tmp_path)
    data = _bridge_inputs(root, clip)

    block = _run_step(data)[OUTPUT_KEY]
    directory = next(l.split("FRAMES:")[1].strip()
                     for l in block.splitlines() if "FRAMES:" in l)
    assert Path(directory).is_dir()

    names = [l.split("\t")[-1] for l in block.splitlines()
             if l.startswith("clip_001\t")]
    assert names
    for name in names:
        strip = Path(directory) / name
        assert strip.is_file() and strip.stat().st_size > 2000
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(strip)],
            capture_output=True, encoding="utf-8", check=True,
        ).stdout.strip().split(",")
        width, height = int(probe[0]), int(probe[1])
        assert height == wf.STRIP_FRAME_SHORT_SIDE
        assert width >= 640


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_strip_shows_the_placed_window_not_a_sample_of_the_clip(tmp_path):
    """The frame count comes from the PLACED range, by the same rule."""
    clip = _fixture_clip(tmp_path / "clip_001.mp4")
    root = _project(tmp_path)
    data = _bridge_inputs(root, clip)

    block = _run_step(data)[OUTPUT_KEY]
    for line in block.splitlines():
        if not line.startswith("clip_001\t"):
            continue
        cells = line.split("\t")
        video_in, video_out, frames = (
            float(cells[3]), float(cells[4]), int(cells[5]))
        # Derived from the expression the code uses, never a literal.
        assert frames == len(wf.sample_times(video_in, video_out, 30.0))


# ── The harness contract ─────────────────────────────────────────────

def test_a_harness_that_cannot_be_shown_a_picture_is_told_so():
    block = ("FRAMES: /frames\n"
             "clip_001\t1\t0.0\t5.000\t9.000\t5\t"
             "clip_001__0005.000__5fabc123.jpg\n")
    inputs = {OUTPUT_KEY: block}

    kept, withheld = wf.withhold_for_harness(inputs, "agent")
    assert withheld == []
    assert kept[OUTPUT_KEY] == block

    dropped, withheld = wf.withhold_for_harness(inputs, "api")
    assert withheld == [OUTPUT_KEY]
    assert "cannot be shown a picture" in dropped[OUTPUT_KEY]
    assert "clip_001__0005.000" not in dropped[OUTPUT_KEY]


# ── The declarations ─────────────────────────────────────────────────

def test_the_step_declares_the_frames_it_shows():
    """A declared output needs the output declaration AND the prompt
    routing: without the first the runner rejects the step's own
    answer, without the second the frames never reach the model."""
    manifest = json.loads(
        (REPO / "library" / "steps" / "step_3_03_review_rough_cut"
         / "manifest.json").read_text(encoding="utf-8"))
    outputs = [o["name"] for o in manifest["interface"]["outputs"]]
    assert OUTPUT_KEY in outputs
    assert OUTPUT_KEY in manifest["context_fields"]
    # The step emits a key it also declares as an output, so what the
    # MODEL owes is declared separately (AGENTS.md 10.1) - and it must
    # not include the frames, or a run that drew none asks the model
    # to invent them.
    llm_names = [o["name"]
                 for o in manifest["interface"]["llm_outputs"]]
    assert OUTPUT_KEY not in llm_names
    assert "rough_cut_review" in llm_names


def test_the_frames_are_owned_by_the_step_that_draws_them():
    from library.tools.project_layout import AREAS, Area
    spec = AREAS[Area.ROUGHCUT_FRAMES]
    assert spec.step == "review_rough_cut"
    assert spec.relpath.endswith("3_03_review_rough_cut/window_frames")
