"""Something is shown the PICTURE of the built output.

The measured state on 2026-09-13: the `reels` process is two structural
nodes, `reel_quality_bar` judges from the transcript, and
`step_6_02_validate_output`'s handoff opened *"You are watching the
RENDERED video ... Would I post this?"* over three inputs none of which
was a frame.  Nothing in this pipeline had ever seen the picture.

These tests FOLLOW THE REFERENCE the way `tests/test_window_frames.py`
does rather than asserting the text's shape: they parse the directory
and the filenames out of the string the model reads, open what comes
back, and require a real picture of the right number of frames.  A test
that only checked the text would pass on a map pointing at nothing -
which is the exact defect this module exists to end.

THE INPUT THAT BREAKS THE ABSENCE HALF: a video file with no decodable
video stream (`test_a_watch_that_drew_nothing_refuses`).  Every strip
fails, `draw_watch_strips` returns no rows, and `assert_watched` raises.
Delete that raise and a watch that saw nothing reports as a watch that
found nothing.

A test builds its project under `tmp_path`, or it skips.  It never falls
back to a real one.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import render_watch as rwatch
from library.tools import window_frames as wf

REPO_ROOT = Path(__file__).resolve().parents[1]
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


# ── Fixtures ─────────────────────────────────────────────────────────

def _rendered_file(path: Path, seconds: float = 9.0) -> Path:
    """A file whose picture CHANGES, standing in for a rendered reel.

    1080x1920 is the reel frame; `testsrc` moves, so a strip can show
    that the picture changes across one span.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size=270x480:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(path)],
        check=True, capture_output=True, encoding="utf-8",
    )
    return path


def _probe_size(path: Path) -> tuple:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         str(path)],
        capture_output=True, encoding="utf-8", check=True,
    ).stdout.strip().split(",")
    return int(out[0]), int(out[1])


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "pipeline_output").mkdir(parents=True)
    (root / "project.yaml").write_text(
        "name: fixture\nslug: fixture\n", encoding="utf-8")
    return root


# ── The sampling law is one law ──────────────────────────────────────

def test_the_watch_samples_by_the_same_rule_a_window_does():
    """Not a second number: a finished cut and a candidate window are
    told apart at the same resolution, stated in one place."""
    assert rwatch.STRIP_SECONDS == (
        wf.MAX_FRAMES_PER_STRIP * wf.SECONDS_UNSEEN_BETWEEN_SAMPLES)


def test_the_strips_cover_the_whole_file_and_nothing_is_shortlisted():
    spans = rwatch.strip_spans(35.2)
    assert spans[0][0] == 0.0
    assert spans[-1][1] == pytest.approx(35.2)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert start == pytest.approx(end)


def test_a_file_with_no_duration_yields_no_span():
    assert rwatch.strip_spans(0) == []
    assert rwatch.strip_spans(None) == []


# ── The watcher is shown a real image ────────────────────────────────

@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_block_carries_an_image_a_reader_can_open(tmp_path):
    """The done-check: a real picture of the RENDER, reachable from the
    text the model reads, by following it the way the model would."""
    video = _rendered_file(tmp_path / "Reel 03.mp4", seconds=9.0)
    drawn = rwatch.draw_watch_strips(str(video), str(tmp_path / "frames"))
    rwatch.assert_watched(drawn, "the fixture render")
    block = rwatch.build_watch_block(
        drawn["directory"], drawn["rows"], drawn["missing"],
        subject="Reel 03", duration=drawn["duration"])

    directory = next(line.split("FRAMES:")[1].strip()
                     for line in block.splitlines() if "FRAMES:" in line)
    assert Path(directory).is_dir()

    names = [line.split("\t")[-1] for line in block.splitlines()
             if line.startswith("0.0\t") or line.startswith("8.0\t")]
    assert names, block
    for name in names:
        strip = Path(directory) / name
        assert strip.is_file() and strip.stat().st_size > 2000
        width, height = _probe_size(strip)
        assert height == wf.STRIP_FRAME_SHORT_SIDE
        # One tile per sampled instant: a single frame cannot show that
        # something appears or jumps inside one continuous shot.
        assert width > wf.STRIP_FRAME_SHORT_SIDE


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_strip_is_drawn_by_the_existing_extractor(tmp_path):
    """`window_frames` draws it - there is not a second extractor."""
    video = _rendered_file(tmp_path / "r.mp4", seconds=4.0)
    calls = []
    real = wf.draw_strip

    def spy(source, times, out_path):
        calls.append((source, tuple(times)))
        return real(source, times, out_path)

    wf.draw_strip = spy
    try:
        drawn = rwatch.draw_watch_strips(str(video),
                                         str(tmp_path / "frames"))
    finally:
        wf.draw_strip = real
    assert calls and drawn["rows"]
    assert calls[0][0] == str(video)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_name_carries_what_was_drawn(tmp_path):
    """A strip drawn under an older rule is a different file.

    `window_frames.strip_filename`'s own cache key, reused: on project
    001, 83 of 94 rows declared 5 frames for a strip that had 6 because
    the name recorded only where the window started."""
    a = rwatch.strip_filename("render", 0.0, [0.0, 1.0, 2.0])
    b = rwatch.strip_filename("render", 0.0, [0.0, 0.5, 1.0, 1.5, 2.0])
    assert a != b
    assert "3f" in a and "5f" in b


# ── The absence is DETECTED ──────────────────────────────────────────

@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_watch_that_drew_nothing_refuses(tmp_path):
    """THE INPUT THAT BREAKS IT: a file with no decodable video stream.

    Every strip fails to draw, so nothing saw the picture. A watch that
    saw nothing must not read as a watch that found nothing - that is
    the same defect as a handoff claiming to watch a table.
    """
    audio_only = tmp_path / "no_picture.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=5", "-c:a", "aac",
         str(audio_only)],
        check=True, capture_output=True, encoding="utf-8")

    drawn = rwatch.draw_watch_strips(str(audio_only),
                                     str(tmp_path / "frames"))
    assert drawn["rows"] == []
    assert drawn["missing"], "an undrawable span must be NAMED"
    with pytest.raises(rwatch.NothingWasWatched) as refused:
        rwatch.assert_watched(drawn, "the audio-only fixture")
    assert "NO STRIP WAS DRAWN" in str(refused.value)


def test_a_file_that_is_not_there_is_a_refusal_not_a_pass(tmp_path):
    drawn = rwatch.draw_watch_strips(str(tmp_path / "absent.mp4"),
                                     str(tmp_path / "frames"))
    with pytest.raises(rwatch.NothingWasWatched):
        rwatch.assert_watched(drawn, "a file that is not there")


def test_a_span_that_could_not_be_drawn_is_named_in_the_block():
    block = rwatch.build_watch_block(
        "/frames", [{"span_start": 0.0, "span_end": 8.0, "frames": 8,
                     "file": "a.jpg"}],
        missing=["8.000-12.400s"])
    assert "8.000-12.400s" in block
    assert "NO STRIP" in block


def test_the_record_separates_nobody_looked_from_nothing_was_wrong():
    seen = rwatch.watch_record("/v.mp4", {"rows": [{}], "directory": "/f",
                                          "missing": [], "duration": 8.0},
                               "Reel 03", answer={"issues": []})
    unseen = rwatch.watch_record("/v.mp4", {"rows": [], "directory": "/f",
                                            "missing": ["0-8s"],
                                            "duration": 8.0}, "Reel 03")
    assert seen["watched"] is True and seen["answer"] == {"issues": []}
    assert unseen["watched"] is False and unseen["answer"] is None
    # It REPORTS. Both records say so on their face.
    assert seen["gates"] is False and unseen["gates"] is False


def test_an_answer_cannot_be_filed_onto_a_watch_that_saw_nothing(tmp_path):
    path = tmp_path / "w.json"
    rwatch.write_record(str(path), rwatch.watch_record(
        "/v.mp4", {"rows": [], "directory": "/f", "missing": [],
                   "duration": 0.0}, "Reel 03"))
    with pytest.raises(rwatch.NothingWasWatched):
        rwatch.record_answer(str(path), {"issues": []})


# ── A harness with no eyes SAYS nothing watched ──────────────────────

def test_a_harness_that_cannot_be_shown_a_picture_says_nothing_watched():
    """The withheld line must not read as "frames were withheld".

    This step spent its whole life claiming to watch. A generic
    withholding notice would let it claim it again.
    """
    block = rwatch.build_watch_block(
        "/frames", [{"span_start": 0.0, "span_end": 8.0, "frames": 8,
                     "file": "a.jpg"}])
    inputs = {"render_watch_frames": block, "deterministic_validation": "{}"}

    kept, withheld = wf.withhold_for_harness(inputs, "agent")
    assert withheld == [] and kept["render_watch_frames"] == block

    dropped, withheld = wf.withhold_for_harness(inputs, "api")
    assert withheld == ["render_watch_frames"]
    assert "FRAMES:" not in dropped["render_watch_frames"]
    assert "NOTHING HAS WATCHED THIS RENDER" in dropped[
        "render_watch_frames"]
    assert inputs["render_watch_frames"] == block      # not mutated


def test_the_runner_withholds_the_watch_frames_too():
    """One list, so the runner keeps exactly one place that knows a
    harness has no eyes."""
    assert "render_watch_frames" in wf.FRAME_INPUTS


# ── What it asks, and what it refuses to be asked ────────────────────

def test_the_questions_are_checkable_and_the_boundary_is_written_down():
    assert rwatch.WATCH_QUESTIONS
    assert rwatch.NOT_ANSWERABLE_FROM_STILLS
    for key, question in rwatch.WATCH_QUESTIONS.items():
        assert question.endswith("?"), key
    # "Would I post this?" is the captain's bar and is not checkable by
    # a step. It must not be one of the questions.
    joined = " ".join(rwatch.WATCH_QUESTIONS.values()).lower()
    assert "would i post" not in joined


def test_no_question_states_a_threshold_or_a_preferred_answer():
    """AGENTS.md 10.5: the block hands over capability, never taste."""
    import re
    for key, question in rwatch.WATCH_QUESTIONS.items():
        assert not re.search(r"\d+\s*(px|%|pixels|percent)", question), key
        assert "should be" not in question.lower(), key


def test_the_block_states_its_own_resolution_and_its_own_limits():
    block = rwatch.build_watch_block(
        "/frames", [{"span_start": 0.0, "span_end": 8.0, "frames": 8,
                     "file": "a.jpg"}])
    assert f"{wf.SECONDS_UNSEEN_BETWEEN_SAMPLES:g} s" in block
    for key in rwatch.NOT_ANSWERABLE_FROM_STILLS:
        assert key in block
    for key in rwatch.WATCH_QUESTIONS:
        assert key in block


# ── Nothing here renders ─────────────────────────────────────────────

def test_the_watch_path_cannot_start_a_render():
    """The captain's standing ruling: a render is the expensive thing
    they must ask for. Watching reads a file they already asked for.

    THE INPUT THAT BREAKS THIS is any reference from this module or the
    verb to `reel_deliver`, `deliver_reel` or `render_timeline`.
    """
    import inspect
    import manage_project

    source = Path(rwatch.__file__).read_text(encoding="utf-8")
    body = source.split("# ── Watching a delivered reel")[1]
    assert "deliver_reel(" not in body
    assert "render_timeline" not in source
    assert "import reel_deliver" not in source

    verb = inspect.getsource(manage_project.cmd_watch_reel)
    assert "render_timeline" not in verb
    assert "reel_deliver" not in verb


def test_the_reels_process_is_still_two_structural_nodes():
    """This capability is a VERB, not a node: a node would watch on
    every build, and the frames only exist after a render."""
    from library.tools import processes

    assert processes.execution_order(processes.REELS) == [
        "build_reels", "verify_reels"]


def test_watch_reel_is_a_registered_verb():
    import manage_project

    assert "watch-reel" in manage_project.ALL_COMMANDS
    assert manage_project.cmd_watch_reel.__doc__


def test_an_undelivered_reel_is_refused_with_the_verb_that_delivers_it(
        tmp_path):
    """Watching requires a render, and the refusal says so plainly
    rather than designing around it."""
    root = _project(tmp_path)
    with pytest.raises(rwatch.NotDelivered) as refused:
        rwatch.delivered_reel(str(root), 3)
    assert "deliver-reel" in str(refused.value)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_delivered_reel_is_found_from_the_deliver_record(tmp_path):
    """Off the sidecar `deliver_reel` wrote, never by matching a
    filename: a .mp4 in exports/ could be anything."""
    root = _project(tmp_path)
    exports = root / "exports"
    exports.mkdir()
    video = _rendered_file(exports / "Reel 03 - fixture.mp4", seconds=4.0)
    (exports / "Reel 03 - fixture.deliver.json").write_text(json.dumps({
        "reel": 3, "timeline_name": "Reel 03 - fixture",
        "render": {"output_path": str(video)}, "delivered": True,
    }), encoding="utf-8")

    row = rwatch.delivered_reel(str(root), 3)
    assert row["video_path"] == str(video)

    frames_dir, record_path = rwatch.watch_paths(str(root), str(video))
    watch = rwatch.watch_video(str(video), frames_dir, record_path,
                               "Reel 03")
    assert watch["record"]["watched"] is True
    assert Path(record_path).is_file()
    assert Path(watch["record"]["block_path"]).is_file()
    # And the strips the record points at are real pictures.
    directory = Path(watch["record"]["frames_directory"])
    strips = sorted(directory.glob("*.jpg"))
    assert strips
    for strip in strips:
        assert _probe_size(strip)[1] == wf.STRIP_FRAME_SHORT_SIDE


# ── Step 6.02 no longer claims eyes it does not have ─────────────────

def test_the_handoff_does_not_claim_to_be_watching_unconditionally():
    """A gate whose prose claims eyes it does not have reads as
    coverage (AGENTS.md 10.4)."""
    handoff = (REPO_ROOT / "library" / "steps"
               / "step_6_02_validate_output" / "handoff.md").read_text(
        encoding="utf-8")
    assert "You are watching the RENDERED video" not in handoff
    # It names the picture it is given, and what it means when it is not.
    assert "render_watch_frames" in handoff
    assert "NOTHING HAS WATCHED THIS RENDER" in handoff


def test_the_manifest_declares_the_frames_it_is_shown():
    manifest = json.loads((
        REPO_ROOT / "library" / "steps" / "step_6_02_validate_output"
        / "manifest.json").read_text(encoding="utf-8"))
    names = [o["name"] for o in manifest["interface"]["outputs"]]
    assert "render_watch_frames" in names


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_validate_bridge_hands_the_step_an_openable_picture(tmp_path):
    """End to end through the real bridge: the key the prompt reads
    carries strips a reader can open."""
    root = _project(tmp_path)
    video = _rendered_file(root / "render.mp4", seconds=9.0)

    bridge = (REPO_ROOT / "library" / "steps"
              / "step_6_02_validate_output" / "bridge.py")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run(
        [sys.executable, str(bridge)],
        input=json.dumps({
            "rendered_output": {"output_path": str(video)},
            "assembly_manifest": {"project": {
                "frame_rate": 30, "resolution": [270, 480],
                "duration_seconds": 9.0}},
            "project_folder": str(root),
        }),
        capture_output=True, encoding="utf-8", cwd=str(REPO_ROOT), env=env,
        timeout=900, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    block = out["render_watch_frames"]
    assert out["deterministic_validation"]["watched"] is True

    directory = next(line.split("FRAMES:")[1].strip()
                     for line in block.splitlines() if "FRAMES:" in line)
    assert Path(directory).is_dir()
    names = [line.split("\t")[-1] for line in block.splitlines()
             if line.count("\t") == 3 and line.endswith(".jpg")]
    assert names, block
    for name in names:
        strip = Path(directory) / name
        assert strip.is_file() and strip.stat().st_size > 2000
        assert _probe_size(strip)[1] == wf.STRIP_FRAME_SHORT_SIDE


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_validate_bridge_says_when_nothing_watched(tmp_path):
    """THE INPUT THAT BREAKS IT, through the real step: a render with no
    decodable picture. The verdict must SAY nothing watched it."""
    root = _project(tmp_path)
    video = root / "render.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=30", "-c:a", "aac",
         "-b:a", "320k", str(video)],
        check=True, capture_output=True, encoding="utf-8")
    # `validate_output` refuses a file under 100 KB before anything else,
    # so the fixture is padded past that bound - the point of this test
    # is the WATCH half, not the size check.
    assert video.stat().st_size > 100_000

    bridge = (REPO_ROOT / "library" / "steps"
              / "step_6_02_validate_output" / "bridge.py")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run(
        [sys.executable, str(bridge)],
        input=json.dumps({
            "rendered_output": {"output_path": str(video)},
            "assembly_manifest": {"project": {
                "frame_rate": 30, "resolution": [270, 480],
                "duration_seconds": 30.0}},
            "project_folder": str(root),
        }),
        capture_output=True, encoding="utf-8", cwd=str(REPO_ROOT), env=env,
        timeout=900, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "render_watch_frames" not in out
    verdict = out["deterministic_validation"]
    assert verdict["watched"] is False
    assert any("NOTHING SAW THIS PICTURE" in issue
               for issue in verdict.get("all_issues", [])), verdict


# ── The accounting is written down ───────────────────────────────────

def test_the_recorded_defects_are_accounted_for_honestly():
    """Which of the 15 recorded interventions this would have caught,
    and which it would not - including that the number is 5."""
    doc = REPO_ROOT / "docs" / "WATCHING_THE_BUILT_REEL.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "WOULD NOT" in text
    # Every question the module asks is accounted for in the doc, and
    # every boundary is too - a doc that listed only the wins would be
    # the coverage-that-is-not story again.
    for key in rwatch.WATCH_QUESTIONS:
        assert key in text, key
    for key in rwatch.NOT_ANSWERABLE_FROM_STILLS:
        assert key in text, key
