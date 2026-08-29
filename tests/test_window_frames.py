"""A real frame of the window the step will actually receive.

These tests FOLLOW the reference rather than asserting its shape, the
way `tests/test_brief_reference.py` does: they parse the directory and
the filenames out of the string the model reads, open what comes back,
and require it to be a real picture of the right number of frames.  A
test that only checked the text would pass on a map pointing at nothing.
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
from library.tools.cutaway_window import candidate_windows, choose_window
from library.tools.project_layout import AREAS, Area

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


# ── The sampling rule ────────────────────────────────────────────────

def test_a_strip_shows_both_ends_of_the_window():
    times = wf.sample_times(6.1, 10.286, 30.0)
    assert times[0] == 6.1
    # The last frame the viewer sees is one frame BEFORE video_out.
    assert times[-1] == pytest.approx(10.286 - 1 / 30.0, abs=0.002)


def test_nothing_of_the_window_passes_unseen_beyond_the_resolution():
    for video_in, video_out in [(0.0, 1.382), (0.0, 4.186), (12.5, 15.0)]:
        times = wf.sample_times(video_in, video_out, 30.0)
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert max(gaps) <= wf.SECONDS_UNSEEN_BETWEEN_SAMPLES + 1e-6


def test_a_long_window_is_bounded_and_the_bound_is_the_frame_count():
    times = wf.sample_times(0.0, 60.0, 30.0)
    assert len(times) == wf.MAX_FRAMES_PER_STRIP


def test_a_degenerate_window_still_yields_a_time():
    assert wf.sample_times(4.0, 4.0, 30.0) == [4.0]


# ── The harness ──────────────────────────────────────────────────────

def test_every_harness_the_pipeline_offers_has_a_recorded_reach():
    """The enumeration is COMPLETE against the runner's own choices."""
    from library.processes.edit_video import run_pipeline

    source = Path(run_pipeline.__file__).read_text(encoding="utf-8")
    line = next(l for l in source.splitlines() if '"--full-auto"' in l)
    offered = {c.strip(' "\'') for c in
               line.split("choices=[")[1].split("]")[0].split(",")}
    assert offered == set(wf.HARNESS_SHOWS_FRAMES)


def test_an_unestablished_harness_raises_rather_than_being_assumed():
    with pytest.raises(wf.UnknownHarness):
        wf.harness_shows_frames("some-new-backend")


def test_a_harness_that_cannot_be_shown_a_picture_gets_the_prose_instead():
    block = wf.build_block("/frames", [
        {"clip_id": "clip_001", "video_in": 0.0, "strip_end": 2.5,
         "frames": 4, "file": "clip_001__0000.000.jpg"},
    ])
    inputs = {"broll_window_frames": block, "broll_candidates_toon": "[1]{x}\n"}

    kept, withheld = wf.withhold_for_harness(inputs, "agy")
    assert withheld == []
    assert kept["broll_window_frames"] == block

    dropped, withheld = wf.withhold_for_harness(inputs, "api")
    assert withheld == ["broll_window_frames"]
    assert "FRAMES:" not in dropped["broll_window_frames"]
    assert "clip_001__0000.000.jpg" not in dropped["broll_window_frames"]
    # The absence is STATED, never silent: a reconstructed context must
    # not read as a run where no frames were drawn.
    assert "cannot be shown a picture" in dropped["broll_window_frames"]
    # And the prose path is untouched - it is the fallback.
    assert dropped["broll_candidates_toon"] == inputs["broll_candidates_toon"]
    assert inputs["broll_window_frames"] == block      # not mutated


def test_the_runner_withholds_before_it_serialises_the_context():
    """The withhold has to happen on the way IN, or the path still ships."""
    from library.processes.edit_video import run_pipeline

    source = Path(run_pipeline.__file__).read_text(encoding="utf-8")
    body = source.split("def present_llm_step")[1]
    withhold = body.index("withhold_for_harness")
    serialise = body.index("json_to_toon")
    assert withhold < serialise


# ── The anchors are the windows the selector really returns ──────────

def _clip_analysis():
    return {"blocks": [
        {"start": 0.0, "end": 6.0, "label": "kitchen",
         "visual": "a kettle boiling on a hob"},
        {"start": 6.0, "end": 14.0, "label": "street",
         "visual": "walking past a mural"},
        {"start": 14.0, "end": 20.0, "label": "car",
         "visual": "a dashboard tilting up to the sky"},
    ]}


def test_every_window_the_selector_can_return_has_an_anchor():
    analysis, index, duration = _clip_analysis(), {}, 20.0
    slots = [1.382, 2.5, 4.186]
    anchors = {a["video_in"] for a in
               wf.window_anchors(analysis, index, duration, slots)}
    for target in slots:
        for row in candidate_windows("", analysis, index, duration, target):
            assert row["video_in"] in anchors


def test_the_anchor_is_the_window_the_current_selector_chooses():
    """The frame is of THIS window, not of some other moment."""
    analysis, index, duration = _clip_analysis(), {}, 20.0
    slots = [1.382, 2.5, 4.186]
    anchors = {a["video_in"]: a for a in
               wf.window_anchors(analysis, index, duration, slots)}
    choice = choose_window("a dashboard tilting up to the sky",
                           analysis, index, duration, 2.5)
    assert choice.basis == "moment_match"
    assert choice.video_in in anchors
    anchor = anchors[choice.video_in]
    # The strip runs at least as far as this window does.
    assert anchor["strip_end"] >= choice.video_out - 1e-6


def test_nothing_is_shortlisted_or_ranked():
    analysis, index, duration = _clip_analysis(), {}, 20.0
    rows = wf.window_anchors(analysis, index, duration, [2.5])
    spans = candidate_windows("", analysis, index, duration, 2.5)
    assert len(rows) == len({s["video_in"] for s in spans})


# ── The picture itself ───────────────────────────────────────────────

def _fixture_clip(path: Path, seconds: float = 20.0) -> Path:
    """A clip whose picture CHANGES, so a strip can show that it does."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size=640x360:rate=30:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, encoding="utf-8",
    )
    return path


def _probe_size(path: Path) -> tuple:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, encoding="utf-8", check=True,
    ).stdout.strip().split(",")
    return int(out[0]), int(out[1])


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_strip_is_a_real_picture_of_the_whole_window(tmp_path):
    clip = _fixture_clip(tmp_path / "clip.mp4")
    out = tmp_path / "strip.jpg"
    times = wf.sample_times(5.0, 9.186, 30.0)
    assert wf.draw_strip(str(clip), times, str(out))
    width, height = _probe_size(out)
    assert height == wf.STRIP_FRAME_SHORT_SIDE
    # One tile per sampled instant, side by side. A single frame would
    # not show a shot that changes inside its own window.
    assert width == pytest.approx(640 * len(times), rel=0.02)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_strip_already_on_disk_is_reused(tmp_path):
    clip = _fixture_clip(tmp_path / "clip.mp4", seconds=6.0)
    out = tmp_path / "strip.jpg"
    times = wf.sample_times(1.0, 3.5, 30.0)
    assert wf.draw_strip(str(clip), times, str(out))
    stamp = out.stat().st_mtime_ns
    assert wf.draw_strip(str(clip), times, str(out))
    assert out.stat().st_mtime_ns == stamp


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_window_that_cannot_be_drawn_is_reported_not_dropped(tmp_path):
    assert not wf.draw_strip(str(tmp_path / "nothing.mp4"), [0.0],
                             str(tmp_path / "s.jpg"))
    block = wf.build_block("/frames", [], missing=["clip_009@3.000s"])
    assert "clip_009@3.000s" in block


# ── The built context ────────────────────────────────────────────────

def _project(tmp_path: Path, clip: Path) -> Path:
    root = tmp_path / "proj"
    (root / "pipeline_output").mkdir(parents=True)
    (root / "project.yaml").write_text("name: fixture\n", encoding="utf-8")
    return root


def _bridge_inputs(root: Path, clip: Path) -> dict:
    return {
        "project_folder": str(root),
        "a_roll_assignments": [{"clip_id": "clip_002"}],
        "clip_catalog": [{
            "clip_id": "clip_001", "filename": clip.name,
            "source_file": str(clip), "duration_seconds": 20.0,
            "frame_rate": 30.0,
        }],
        "semantic_analysis_documents": [dict(
            _clip_analysis(), clip_id="clip_001", file_stem=clip.stem,
            analysis={"scene": "a kitchen, then a street, then a car"},
        )],
        "temporal_event_indices": [{"clip_id": "clip_001"}],
        "timed_spine": {"structure": [
            {"position": 1, "block_type": "transition_slot",
             "timeline_start": 0.0, "timeline_end": 2.5},
            {"position": 2, "block_type": "speech",
             "timeline_start": 2.5, "timeline_end": 9.0},
        ]},
    }


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_built_context_carries_an_image_a_reader_can_open(tmp_path):
    """The done-check: a real image part, reachable from the context."""
    clip = _fixture_clip(tmp_path / "clip_001.mp4")
    root = _project(tmp_path, clip)

    bridge = (Path(__file__).resolve().parents[1] / "library" / "steps"
              / "step_3_02_select_broll" / "bridge.py")
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(repo))
    proc = subprocess.run(
        [sys.executable, str(bridge)],
        input=json.dumps(_bridge_inputs(root, clip)),
        capture_output=True, encoding="utf-8", cwd=str(repo), env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    block = out["broll_window_frames"]

    # Follow the reference the same way the model would.
    directory = next(l.split("FRAMES:")[1].strip()
                     for l in block.splitlines() if "FRAMES:" in l)
    assert Path(directory).is_dir()
    assert Path(directory).name == "window_frames"

    names = [l.split("\t")[-1] for l in block.splitlines()
             if l.startswith("clip_001\t")]
    assert names
    for name in names:
        strip = Path(directory) / name
        assert strip.is_file() and strip.stat().st_size > 2000
        width, height = _probe_size(strip)
        assert height == wf.STRIP_FRAME_SHORT_SIDE
        assert width >= 640

    # And the window each strip is of is one the selector really returns.
    anchors = {a["video_in"] for a in wf.window_anchors(
        _clip_analysis(), {}, 20.0, [2.5])}
    for line in block.splitlines():
        if line.startswith("clip_001\t"):
            assert float(line.split("\t")[1]) in anchors


# ── Where the frames live ────────────────────────────────────────────

def test_the_frames_are_owned_by_the_step_that_draws_them():
    spec = AREAS[Area.WINDOW_FRAMES]
    assert spec.step == "select_broll"
    assert spec.relpath.endswith("3_02_select_broll/window_frames")
