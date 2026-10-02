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

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


# ── The sampling rule ────────────────────────────────────────────────

def test_a_strip_shows_both_ends_of_the_window():
    times = wf.sample_times(6.1, 10.286, 30.0)
    assert times[0] == 6.1
    # The last frame the viewer sees is one frame BEFORE video_out.
    assert times[-1] == pytest.approx(10.286 - 1 / 30.0, abs=0.002)
    # Nothing of the window passes unseen beyond the resolution.
    for video_in, video_out in [(0.0, 1.382), (0.0, 4.186), (12.5, 15.0)]:
        times = wf.sample_times(video_in, video_out, 30.0)
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert max(gaps) <= wf.SECONDS_UNSEEN_BETWEEN_SAMPLES + 1e-6


# ── The harness ──────────────────────────────────────────────────────


def test_a_harness_that_cannot_be_shown_a_picture_gets_the_prose_instead():
    block = wf.build_block("/frames", [
        {"clip_id": "clip_001", "video_in": 0.0, "strip_end": 2.5,
         "frames": 4, "file": "clip_001__0000.000.jpg"},
    ])
    inputs = {"broll_window_frames": block, "broll_candidates_toon": "[1]{x}\n"}

    kept, withheld = wf.withhold_for_harness(inputs, "agent")
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

    # A harness nobody established raises rather than being assumed.
    with pytest.raises(wf.UnknownHarness):
        wf.harness_shows_frames("some-new-backend")


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


def test_the_anchor_is_the_window_the_current_selector_chooses():
    """The frame is of THIS window, not of some other moment."""
    analysis, index, duration = _clip_analysis(), {}, 20.0
    slots = [1.382, 2.5, 4.186]
    anchors = {a["video_in"]: a for a in
               wf.window_anchors(analysis, index, duration, slots)}
    # Every window the selector can return has an anchor...
    for target in slots:
        for row in candidate_windows("", analysis, index, duration, target):
            assert row["video_in"] in anchors
    choice = choose_window("a dashboard tilting up to the sky",
                           analysis, index, duration, 2.5)
    assert choice.basis == "moment_match"
    assert choice.video_in in anchors
    anchor = anchors[choice.video_in]
    # The strip runs at least as far as this window does.
    assert anchor["strip_end"] >= choice.video_out - 1e-6


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


# ── The cache is keyed on what was DRAWN ──────────────────────────────

def test_the_name_carries_the_frame_count_and_the_sampling_rule():
    """`(clip_id, video_in)` is not enough to identify a strip.

    On project 001, 83 of 94 rows declared 5 frames for a strip that has
    6: the strips were drawn at 10:26 under an older sampling rule,
    `draw_strip` reused every one of them, and `frames: len(times)` was
    recomputed from current code at 12:36. Same class as #348/#350 - a
    cache invalidated by the footage and never by the code that wrote it.
    """
    a = wf.sample_times(0.0, 4.0, 30.0)
    b = wf.sample_times(0.0, 6.0, 30.0)
    assert len(a) != len(b)

    name_a = wf.strip_filename("clip_001", 0.0, a)
    name_b = wf.strip_filename("clip_001", 0.0, b)
    assert name_a != name_b
    # Still readable, and still labelled by the window it shows.
    assert name_a.startswith("clip_001__0000.000__")
    assert f"{len(a)}f" in name_a
    # Stable for the same drawing.
    assert name_a == wf.strip_filename("clip_001", 0.0, list(a))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_strip_drawn_under_an_older_rule_is_never_read_as_this_one(
        tmp_path, monkeypatch):
    """The whole defect, end to end, in one clip."""
    clip = _fixture_clip(tmp_path / "clip.mp4", seconds=6.0)

    # Yesterday's rule: a coarser sampling, so fewer frames.
    monkeypatch.setattr(wf, "SECONDS_UNSEEN_BETWEEN_SAMPLES", 2.0)
    old_times = wf.sample_times(0.0, 4.0, 30.0)
    old_name = wf.strip_filename("clip_004", 0.0, old_times)
    assert wf.draw_strip(str(clip), old_times, str(tmp_path / old_name))

    # Today's rule, same window, same clip.
    monkeypatch.setattr(wf, "SECONDS_UNSEEN_BETWEEN_SAMPLES", 1.0)
    new_times = wf.sample_times(0.0, 4.0, 30.0)
    new_name = wf.strip_filename("clip_004", 0.0, new_times)
    assert new_name != old_name
    assert wf.draw_strip(str(clip), new_times, str(tmp_path / new_name))

    # The file the table describes has the frames the table declares.
    width, height = _probe_size(tmp_path / new_name)
    assert width == pytest.approx(640 * len(new_times), rel=0.02)
    old_width, _ = _probe_size(tmp_path / old_name)
    assert old_width == pytest.approx(640 * len(old_times), rel=0.02)
    assert old_width != width
