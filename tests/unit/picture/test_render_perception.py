"""Perceptual verification of a DELIVERED file, measured against the plan.

Section 6 of the capability punch list asks Ren to tell a structurally
valid timeline from a technically compliant, visually coherent and
editorially acceptable final output.  These tests are about the SECOND
half of that: the readings
`library/tools/render_perception.py` takes of the rendered file, and the
one question `library/tools/qa_findings.py` answers about all of them -
is this finding a MEASURABLE technical failure or a subjective
aesthetic concern?

Every fixture here is SYNTHETIC and built under `tmp_path`: a moving
square on a flat field, encoded by ffmpeg, with the defect under test
put in deliberately.  Nothing reaches a real project, and no captain's
media is touched.

The measurements are real - ffmpeg decodes the fixture and the numbers
come off the pixels - so a threshold that stops being true fails here
rather than in a render nobody was watching.

Every reading here is REPORT-ONLY, and the tests say so: a reading that
finds a defect says so in its SEVERITY and its value, and still passes.
The test that proves the difference is the one that promotes the gate
boolean and watches the same measurement fail.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from library.tools import qa_findings as qa
from library.tools import render_perception as rp

#: The fixture geometry.  Small enough to encode in a moment, big
#: enough that a changed square is a visible fraction of the frame.
WIDTH, HEIGHT, FPS = 160, 120, 25

#: One second of frames, and the number of frames a section holds.
SECOND = FPS

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are not available on PATH",
)


# ── Fixtures: synthetic media with one defect each ───────────────────


def _moving_frames(count: int, start: int = 0, direction: int = 1,
                   level: int = 0):
    """A bright square that walks across a flat field and turns around.

    The square's position is a pure function of the frame index, so a
    reader can say exactly which source frames a delivered frame came
    from - which is what makes "the join is visible" a statement about
    the join and not about the encoder.  `level` lifts the field itself,
    which is what makes two shots genuinely different pictures.

    The square TURNS around at the edges rather than wrapping, so the
    only discontinuity in a fixture is the one a test put there.  A
    square that wrapped would jump from one edge to the other every few
    seconds, and the reading would have to choose between that and the
    defect it was looking for.
    """
    span = WIDTH - 40
    frames = []
    for index in range(count):
        frame = np.full((HEIGHT, WIDTH), level, dtype=np.uint8)
        walked = (start + direction * index * 3) % (2 * span)
        x = walked if walked <= span else 2 * span - walked
        frame[40:80, x:x + 40] = 220
        frames.append(frame)
    return frames


def _encode(frames, path: Path) -> Path:
    """Encode grayscale frames as a small H.264 file."""
    raw = path.with_suffix(".raw")
    with open(raw, "wb") as handle:
        handle.write(np.stack(frames).tobytes())
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", str(raw),
         "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, encoding="utf-8", errors="replace", check=False)
    raw.unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr[-2000:]
    assert path.exists() and path.stat().st_size > 0
    return path


def _encode_with_audio(frames, path: Path, first_gain: float,
                       second_gain: float) -> Path:
    """The same picture, with a deliberate LEVEL STEP at the midpoint."""
    silent = _encode(frames, path.with_suffix(".silent.mp4"))
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(silent),
         "-f", "lavfi", "-i",
         f"sine=frequency=440:sample_rate=44100:duration=2,volume={first_gain}",
         "-f", "lavfi", "-i",
         f"sine=frequency=440:sample_rate=44100:duration=2,volume={second_gain}",
         "-filter_complex", "[1:a][2:a]concat=n=2:v=0:a=1[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
         "-threads", "1", "-shortest", str(path)],
        capture_output=True, encoding="utf-8", errors="replace", check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return path


def _overlay(path: Path, bar_height: int) -> Path:
    """A caption overlay: one bar of ink on a transparent field."""
    frame = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
    frame[60:60 + bar_height, 20:140, :3] = 255
    frame[60:60 + bar_height, 20:140, 3] = 255
    raw = path.with_suffix(".raw")
    with open(raw, "wb") as handle:
        handle.write(frame.tobytes() * 4)
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
         "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", str(raw),
         "-c:v", "qtrle", "-threads", "1", str(path)],
        capture_output=True, encoding="utf-8", errors="replace", check=False)
    raw.unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr[-2000:]
    return path


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """Every fixture the readings are measured against."""
    root = tmp_path_factory.mktemp("perception")
    base = _moving_frames(4 * SECOND)

    # A different picture: the field is lifted, so the join between the
    # two shots is a real cut and not a change of direction.
    other = _moving_frames(2 * SECOND, start=0, direction=-1, level=120)

    return {
        # A real cut at two seconds: one shot becomes another.
        "cut": _encode(base[:2 * SECOND] + other, root / "cut.mp4"),
        # One shot cut in two, contiguously: the join is invisible.
        "split_ok": _encode(base[:2 * SECOND] + base[2 * SECOND:4 * SECOND],
                             root / "split_ok.mp4"),
        # The same shot cut in two, with a second skipped: the join
        # shows, because the picture does not continue.
        "split_broken": _encode(base[:2 * SECOND] + base[3 * SECOND:5 * SECOND],
                                root / "split_broken.mp4"),
        # One picture, one level step at two seconds.
        "levels": _encode_with_audio(base[:4 * SECOND],
                                     root / "levels.mp4",
                                     first_gain=0.5, second_gain=0.02),
        # One picture with a single white frame in the middle.
        "flash": _encode(
            [np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)]
            + base[1:2 * SECOND]
            + [np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)]
            + base[2 * SECOND:4 * SECOND],
            root / "flash.mp4"),
        # Three caption overlays: two alike and one drawn larger.
        "caption_small": _overlay(root / "small.mov", 20),
        "caption_large": _overlay(root / "large.mov", 40),
    }


def _manifest(seam_at: float = 2.0, sections=None, words=None,
              frame_rate: float = FPS):
    """A manifest declaring one seam, and the sections and words given."""
    manifest = {
        "project": {"frame_rate": frame_rate, "resolution": [WIDTH, HEIGHT]},
        "tracks": {"V1": {"clips": [
            {"source_file": "/footage/a.mov", "source_in": 0.0,
             "source_out": seam_at, "timeline_in": 0.0,
             "timeline_out": seam_at, "label": "a"},
            {"source_file": "/footage/b.mov", "source_in": 0.0,
             "source_out": 2.0, "timeline_in": seam_at,
             "timeline_out": seam_at + 2.0, "label": "b"},
        ]}},
        "_spine_blocks": [],
    }
    if sections is not None:
        manifest["_spine_blocks"] = sections
    if words is not None:
        manifest["_spine_blocks"] = words
    return manifest


def _section(start, end, behaviour="background", kind="speech"):
    """A spine block, as the manifest carries it."""
    return {"type": kind, "clip_id": "c", "source_start": 0.0,
            "source_end": end - start, "timeline_start": start,
            "timeline_end": end, "word_timestamps": [],
            "alignment_method": None, "music_behavior": behaviour}


def _plan_section(start, end, behaviour="background"):
    """The same stretch, as the readings take it."""
    return rp.Section(start=start, end=end, label="section",
                      quiet=(behaviour == "silent"))


def _split_manifest():
    """A manifest whose two clips are ONE shot cut in two."""
    manifest = _manifest(seam_at=2.0)
    manifest["tracks"]["V1"]["clips"][0]["source_file"] = "/footage/a.mov"
    manifest["tracks"]["V1"]["clips"][1]["source_file"] = "/footage/a.mov"
    manifest["tracks"]["V1"]["clips"][1]["source_in"] = 2.0
    return manifest


# ── QA-009: a cut the plan never wrote ───────────────────────────────


def test_a_cut_the_plan_never_declared_is_named(media):
    """A change of picture with no seam behind it is a cut nobody wrote.

    The manifest declares one seam, at a place the render does not cut,
    and the render cuts somewhere else.  The reading names the cut the
    plan never wrote and says where it is.
    """
    manifest = _manifest(seam_at=3.5)
    result = rp.measure_cut_motivation(str(media["cut"]),
                                       rp.declared_seams(manifest))
    assert result.severity == "warning", result.detail
    assert result.value, "the cut was measured and not named"
    assert result.value[0]["time"] == pytest.approx(2.0, abs=0.2)
    assert "match no declared seam" in result.detail


def test_a_cut_the_plan_declared_is_not_reported(media):
    """The same render, with the seam the plan wrote, reads clean."""
    manifest = _manifest(seam_at=2.0)
    result = rp.measure_cut_motivation(str(media["cut"]),
                                       rp.declared_seams(manifest))
    assert result.severity == "info", result.detail
    assert not result.value


def test_a_plan_that_declared_nothing_says_it_was_not_measured(media):
    """No seam declared is NOT the same claim as a render with no cuts.

    The reading must say it could not measure, and must not report a
    clean result that a reader would take as coverage.
    """
    result = rp.measure_cut_motivation(str(media["cut"]), [])
    assert result.value is None
    assert "NOT MEASURED" in result.detail
    assert result.severity == "warning", result.detail


# ── QA-010: a seam that lands inside a spoken word ───────────────────


def test_a_seam_inside_a_word_is_named(media):
    """A cut while the word is being spoken damages the word.

    The word's timing comes from the spine and is in SOURCE seconds;
    the reading has to move it into TIMELINE seconds before it can say
    anything about the deliverable.
    """
    block = _section(0.0, 4.0)
    block["word_timestamps"] = [
        {"word": "hello", "source_start": 1.9, "source_end": 2.4},
        {"word": "world", "source_start": 3.0, "source_end": 3.4},
    ]
    manifest = _manifest(words=[block])
    words = rp.declared_words(manifest)
    assert words, "the plan's words were not read"
    result = rp.measure_dialogue_integrity(str(media["cut"]), words)
    assert result.severity == "warning", result.detail
    assert result.value[0]["word"] == "hello"
    assert "'hello'" in result.detail


def test_a_seam_between_words_damages_nothing(media):
    """A cut that lands in the gap between two words is a clean cut."""
    block = _section(0.0, 4.0)
    block["word_timestamps"] = [
        {"word": "hello", "source_start": 0.5, "source_end": 0.9},
        {"word": "world", "source_start": 3.0, "source_end": 3.4},
    ]
    words = rp.declared_words(_manifest(words=[block]))
    result = rp.measure_dialogue_integrity(str(media["cut"]), words)
    assert result.severity == "info", result.detail


def test_a_render_with_no_plan_words_is_not_measured(media):
    """No word timing declared is a measurement that could not be taken."""
    result = rp.measure_dialogue_integrity(str(media["cut"]), [])
    assert result.value is None
    assert "NOT MEASURED" in result.detail


# ── QA-011: a join that should be invisible, and a flash ─────────────


def test_a_contiguous_split_leaves_no_trace(media):
    """One shot cut in two, contiguously, joins without a seam."""
    seams = rp.declared_seams(_split_manifest())
    assert [seam.kind for seam in seams] == [rp.SEAM_SPLIT]
    result = rp.measure_visual_continuity(str(media["split_ok"]), seams, FPS)
    assert result.severity == "info", result.detail


def test_a_split_that_skips_the_footage_shows_its_join(media):
    """A declared split the deliverable does not honour is visible.

    The plan says the two clips are one shot cut in two, so the join
    must leave no trace.  The delivered file skips a second of the
    footage, so the picture does not continue and the join SHOWS.

    The join is judged against the shot's own motion, so a shot that
    moves a lot does not report itself as broken.
    """
    seams = rp.declared_seams(_split_manifest())
    result = rp.measure_visual_continuity(str(media["split_broken"]), seams,
                                          FPS)
    assert result.severity == "warning", result.detail
    split = result.value["splits"][0]
    assert split["visible"], split
    assert split["jump"] > rp.CONTINUITY_MIN_JUMP


def test_a_plan_with_no_split_says_it_was_not_measured(media):
    """Nothing to be continuous against is not the same as continuity."""
    result = rp.measure_visual_continuity(str(media["split_ok"]), [], FPS)
    assert result.value is None
    assert "NOT MEASURED" in result.detail


def test_a_flash_frame_is_named(media):
    """One white frame in the middle of a shot is a defect."""
    result = rp.measure_flash_frames(str(media["flash"]), FPS)
    assert result.severity == "warning", result.detail
    assert result.value["flashes"], "the flash was not located"


def test_a_clean_picture_has_no_flash(media):
    """The same reading on a picture with no flash in it is clean."""
    result = rp.measure_flash_frames(str(media["cut"]), FPS)
    assert result.severity == "info", result.detail


# ── QA-012: two sections delivered at very different levels ──────────


def test_a_level_step_between_two_sections_is_named(media):
    """The level STEPS between one section and the next.

    The whole master's loudness is `lufs`'s business; this asks whether
    the mix steps, which is what the ear hears as a jump.
    """
    sections = [_plan_section(0.0, 2.0), _plan_section(2.0, 4.0)]
    result = rp.measure_section_levels(str(media["levels"]), sections)
    assert result.severity == "warning", result.detail
    step = result.value["steps"][0]
    assert abs(step["delta_db"]) > rp.SECTION_LEVEL_JUMP_DB
    assert step["over_tolerance"]


def test_a_section_the_plan_marked_silent_is_exempt(media):
    """A level step the plan made is the plan's, not the mix's.

    A section the spine marked `silent` is a chosen hole in the bed, and
    the level at its edge is not a mixing mistake.
    """
    sections = [_plan_section(0.0, 2.0),
                _plan_section(2.0, 4.0, behaviour="silent")]
    result = rp.measure_section_levels(str(media["levels"]), sections)
    assert result.severity == "info", result.detail


def test_two_sections_at_the_same_level_are_consistent(media):
    """The same render judged as one section is one section."""
    sections = [_plan_section(0.0, 4.0)]
    result = rp.measure_section_levels(str(media["levels"]), sections)
    assert result.severity == "info", result.detail
    assert not result.value["steps"]


# ── QA-013: caption cards drawn unlike one another ───────────────────


def test_a_caption_drawn_at_another_size_is_named(media):
    """A reel whose captions change size halfway reads as two reels."""
    cards = [
        rp.CaptionCard(overlay_path=str(media["caption_small"]),
                       start=0.0, end=1.0, label="one"),
        rp.CaptionCard(overlay_path=str(media["caption_small"]),
                       start=1.0, end=2.0, label="two"),
        rp.CaptionCard(overlay_path=str(media["caption_large"]),
                       start=2.0, end=3.0, label="three"),
    ]
    result = rp.measure_caption_consistency(cards)
    assert result.severity == "warning", result.detail
    assert result.value["segments"][2]["height_ratio"] > 1.5


def test_captions_drawn_alike_are_consistent(media):
    """The same cards at one size read as one reel."""
    cards = [
        rp.CaptionCard(overlay_path=str(media["caption_small"]),
                       start=0.0, end=1.0, label="one"),
        rp.CaptionCard(overlay_path=str(media["caption_small"]),
                       start=1.0, end=2.0, label="two"),
    ]
    result = rp.measure_caption_consistency(cards)
    assert result.severity == "info", result.detail


# ── QA-014: a piece of the plan that is not in the deliverable ───────


def test_a_declared_cut_the_render_does_not_show_is_named(media):
    """The plan's structure must be in the deliverable.

    The render here has NO cut at all, so the seam the plan declared at
    two seconds is not in it.
    """
    manifest = _manifest(seam_at=2.0)
    seams = rp.declared_seams(manifest)
    result = rp.measure_narrative_coverage(seams, [], [])
    assert result.severity == "warning", result.detail
    assert result.value["missing_cuts"]
    assert result.value["missing_cuts"][0]["time"] == pytest.approx(2.0)


def test_a_deliverable_that_ends_before_the_plan_does_is_named(media):
    """A deliverable shorter than the plan is missing the end of it."""
    manifest = _manifest(seam_at=2.0)
    manifest["_spine_blocks"] = [_section(0.0, 4.0)]
    result = rp.measure_narrative_coverage(
        rp.declared_seams(manifest), rp.declared_sections(manifest), [],
        render_duration=2.5)
    assert result.severity == "warning", result.detail
    assert result.value["short"]["planned_end"] == pytest.approx(4.0)
    assert result.value["short"]["render_duration"] == pytest.approx(2.5)


def test_a_deliverable_that_covers_the_plan_is_covered(media):
    """The same plan, delivered whole, is covered."""
    manifest = _manifest(seam_at=2.0)
    manifest["_spine_blocks"] = [_section(0.0, 4.0)]
    cuts = rp.detect_cut_points(str(media["cut"]))
    result = rp.measure_narrative_coverage(
        rp.declared_seams(manifest), rp.declared_sections(manifest), cuts,
        render_duration=4.0)
    assert result.severity == "info", result.detail


# ── QA-015: the delivered pace is not the pace the plan wrote ────────


def test_a_seam_delivered_somewhere_else_is_a_pacing_drift(media):
    """A seam that lands a second away is a pace nobody chose.

    The bound is a FRACTION of the section the seam closes, so a
    half-second matters in a short section and means nothing in a long
    one.
    """
    manifest = _manifest(seam_at=2.0)
    manifest["_spine_blocks"] = [_section(0.0, 4.0)]
    seams = rp.declared_seams(manifest)
    drifted = [rp.CutPoint(time=2.8, score=0.9)]
    result = rp.measure_brief_pacing(
        seams, rp.declared_sections(manifest), drifted)
    assert result.severity == "warning", result.detail
    assert result.value[0]["drift_seconds"] == pytest.approx(0.8, abs=0.01)


def test_a_seam_delivered_where_the_plan_put_it_is_the_plans_pace(media):
    """The same seam, delivered on time, is the pace the plan wrote."""
    manifest = _manifest(seam_at=2.0)
    manifest["_spine_blocks"] = [_section(0.0, 4.0)]
    seams = rp.declared_seams(manifest)
    on_time = [rp.CutPoint(time=2.02, score=0.9)]
    result = rp.measure_brief_pacing(
        seams, rp.declared_sections(manifest), on_time)
    assert result.severity == "info", result.detail


# ── The composed capability, and the reader it lands in ──────────────


def test_every_reading_is_produced_and_named(media):
    """The composed call produces all of them, and each has a reader.

    A metric with no reader is a measurement nobody reads, which is the
    defect this lane exists to close.
    """
    manifest = _manifest(seam_at=2.0)
    manifest["_spine_blocks"] = [_section(0.0, 2.0), _section(2.0, 4.0)]
    plan = rp.perception_plan(manifest)
    results = rp.verify_render_perception(str(media["cut"]),
                                          assembly_manifest=manifest,
                                          render_duration=4.0)
    assert {result.metric for result in results} == set(rp.METRICS)
    for result in results:
        assert result.metric in qa.FINDING_READERS, result.metric
        assert result.detail, result.metric


def test_a_plan_that_declared_nothing_is_not_a_clean_render(media):
    """No plan means every reading says it was NOT MEASURED.

    A report that says "clean" for a check that never ran is coverage
    that does not exist.
    """
    results = rp.verify_render_perception(str(media["cut"]),
                                          assembly_manifest={})
    needs_a_plan = [r for r in results if r.metric != "flash_frames"]
    assert needs_a_plan, "the composed call produced nothing to judge"
    for result in needs_a_plan:
        assert "NOT MEASURED" in result.detail, (
            f"{result.metric} reported a clean result for a check that "
            f"never ran: {result.detail}")
    # The one reading that needs no plan is the flash: a white frame in
    # the middle of a shot is a defect whoever asked for the video.
    flash = next(r for r in results if r.metric == "flash_frames")
    assert flash.value is not None, flash.detail


def test_promoting_the_gate_is_the_only_thing_between_a_report_and_a_failure(
        media):
    """The same measurement, with its boolean flipped, fails.

    This is what makes a report-only reading honest: the number is the
    same either way, and what changes is whether a gate may act on it.
    """
    manifest = _manifest(seam_at=3.5)
    seams = rp.declared_seams(manifest)
    reported = rp.measure_cut_motivation(str(media["cut"]), seams)
    gated = rp.measure_cut_motivation(str(media["cut"]), seams,
                                      gate=True)
    assert reported.passed, "a report-only reading must not fail"
    assert reported.severity == "warning"
    assert not gated.passed, "a promoted reading must fail on the same defect"
    assert gated.severity == "error"
    assert gated.value == reported.value


def test_a_measurable_finding_and_a_subjective_one_are_told_apart():
    """QA-016: a gate may act on a measurement and not on a judgement.

    `broll_correspondence` asks a model whether a cutaway illustrates
    the speech it covers - a judgement about taste, which no gate may
    act on because nobody can reproduce it.  Everything else in the
    reader table is a number against a stated bound.
    """
    rows = [
        {"metric": "lufs", "passed": False, "severity": "error",
         "detail": "too quiet"},
        {"metric": "broll_correspondence", "passed": False,
         "severity": "warning", "detail": "does not illustrate"},
        {"metric": "a_metric_nobody_claims", "passed": False,
         "severity": "error", "detail": "no reader"},
    ]
    read = qa.read_qa_report(rows)
    kinds = {f.metric: f.kind for f in read.findings}
    assert kinds["lufs"] == qa.DETERMINISTIC
    assert kinds["broll_correspondence"] == qa.SUBJECTIVE
    assert kinds["a_metric_nobody_claims"] == qa.UNCLASSIFIED
    assert read.kind_counts() == {qa.DETERMINISTIC: 1, qa.SUBJECTIVE: 1,
                                  qa.UNCLASSIFIED: 1}


def test_a_perceptual_reading_is_read_as_advisory_and_not_as_a_failure():
    """A report-only reading is an ADVISORY in the reader, not a failure.

    It arrives as a passing row carrying a non-`info` severity, and the
    reader reads that as a note rather than as something a gate may act
    on.  That is the whole difference between a measurement and a
    failure.
    """
    row = {"metric": "cut_motivation", "passed": True, "severity": "warning",
           "detail": "1 cut(s) match no declared seam"}
    finding = qa.read_qa_report([row]).findings[0]
    assert finding.verdict == qa.ADVISORY
    assert not finding.blocks
    assert finding.kind == qa.DETERMINISTIC


def test_the_summary_says_how_many_measurements_and_how_many_judgements():
    """The run summary reports the kind of every finding, not only the
    verdict - a reader has to be able to tell them apart at a glance."""
    rows = [
        {"metric": "lufs", "passed": False, "severity": "error",
         "detail": "too quiet"},
        {"metric": "broll_correspondence", "passed": False,
         "severity": "warning", "detail": "does not illustrate"},
    ]
    printed = "\n".join(qa.summary_lines(qa.read_qa_report(rows)))
    assert "1 measurable" in printed
    assert "1 subjective" in printed
