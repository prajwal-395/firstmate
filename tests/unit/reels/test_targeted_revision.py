"""QA-009: a targeted quality-driven revision, and when to stop making one.

The defect this names
---------------------
The delivered-file checks NAME their failures - the audio spec
(QA-004), the caption spec (QA-003), the picture spec (QA-002) and the
editorial review (QA-006) each say which property failed, over which
interval, with the number they measured.  A name is not an action: a
file whose captions collide with a lower third was MEASURED, and the
engine could do nothing about it but print it.  The next thing a
quality loop does - take the file back and fix THAT - had no path, so
the only way to fix a delivered file was to rebuild it, which throws
away every part of the work that was already accepted.

What each test proves
---------------------
* a loudness failure becomes ONE narrow `audio.gain` on the audio it
  implicates, with the dB read off the measurement, and every other
  row and marker declared unchanged;
* a caption collision becomes ONE narrow `overlay.position` on the
  GRAPHIC - the caption's words and timing are the accepted part of
  the work and are left alone;
* a failure with no narrow revision is REPORTED by name, and one that
  needs material the timeline does not carry is reported as needing a
  source - the engine does not choose what goes there;
* a SUBJECTIVE failure is never revised by the engine;
* a second attempt at a revision that did not improve is refused;
* the verification answers the corrected issue and the previously
  satisfied constraints SEPARATELY, so a revision that fixes the
  caption and breaks the loudness is a regression, not a fix;
* and, on a seeded scratch file, each seeded defect is fixed by its
  narrow revision and NOTHING ELSE MOVES.

Every render here is SYNTHETIC and lives under `tmp_path`: the check
needs a file with captions and audio on it, and the captain's media is
read-only.
"""
from __future__ import annotations

import math
import os
import subprocess

import pytest

from library.tools import caption_ocr
from library.tools.deliverable_qc import run_deliverable_qc
from library.tools.delivered_caption_spec import CaptionIntent
from library.tools.delivery_spec import resolve_delivery_spec
from library.tools.targeted_revision import (
    ACCEPT,
    ALREADY_ATTEMPTED,
    AMBIGUOUS,
    ASK_CREATOR,
    FIXED,
    NEEDS_SOURCE,
    NOT_IMPROVING,
    PLANNED,
    REGRESSED,
    STOP,
    SUBJECTIVE,
    QualityCheck,
    RevisionContext,
    RevisionOutcome,
    TimelineItem,
    objective_checks,
    plan_revision,
    verify_revision,
)

WIDTH, HEIGHT = 540, 960
FPS = 15
FRAMES = 90
CAPTION_FIRST, CAPTION_LAST = 15, 40
CAPTION_TEXT = "this is the caption text"
LOWER_THIRD = "LOWER THIRD LABEL"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

#: Where the plan says the lower third goes, in delivered-frame
#: pixels.  The seed draws it there, and the caption spec MEASURES it
#: there; the plan's box is what the revision is measured against.
LOWER_THIRD_BOX = (40.0, 788.0, 324.0, 812.0)


# ── The synthetic file ───────────────────────────────────────────────


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def _frame(index: int, *, caption: str = "", lower_third: str = "",
           lower_third_dy: int = 0):
    """One frame: a moving background, an optional caption, a lower third."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WIDTH, HEIGHT), (30, 34, 40))
    draw = ImageDraw.Draw(image)
    for i in range(8):
        x = (index * 5 + i * 60) % WIDTH
        draw.rectangle([x, 120 + i * 90, x + 90, 190 + i * 90],
                       fill=(70 + i * 5, 60, 90))
    if lower_third:
        draw.text((45, 784 + lower_third_dy), lower_third, font=_font(26),
                  fill=(255, 220, 0), stroke_width=2, stroke_fill=(0, 0, 0))
    if caption:
        draw.text((45, 800), caption, font=_font(34), fill=(255, 255, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0))
    return image


def _render(path: str, *, lower_third_dy: int = 0, db: float = 0.0):
    """A synthetic delivered file, and the frames it was drawn from.

    The frames are returned so a test can compare the PICTURE the seed
    drew, which is the only way to say "nothing else changed" exactly:
    a re-encode of a changed frame is not bit-identical anywhere, so
    the file's pixels are not the evidence - the seed's are.
    """
    import numpy as np

    frames = []
    raw = path + ".rgb"
    with open(raw, "wb") as handle:
        for index in range(FRAMES):
            caption = CAPTION_TEXT if CAPTION_FIRST <= index <= CAPTION_LAST else ""
            lower = LOWER_THIRD if CAPTION_FIRST <= index <= CAPTION_LAST else ""
            image = _frame(index, caption=caption, lower_third=lower,
                           lower_third_dy=lower_third_dy)
            frames.append(image)
            handle.write(image.tobytes())
    command = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS),
               "-i", raw]
    seconds = FRAMES / FPS
    samples = np.arange(int(seconds * 48000)) / 48000.0
    tone = 0.12 * (10 ** (db / 20.0)) * np.sin(2 * math.pi * 440 * samples)
    pcm = (tone * 32767).astype("<i2")
    interleaved = np.repeat(pcm, 2)
    wav = path + ".pcm"
    interleaved.tofile(wav)
    command += ["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", wav]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
                "-c:a", "pcm_s16le", "-shortest", path]
    subprocess.run(command, check=True, capture_output=True)
    os.remove(raw)
    os.remove(wav)
    return path, frames


def _intent() -> CaptionIntent:
    """The plan's intent for the one caption the seed carries."""
    return CaptionIntent(
        label="caption-1", text=CAPTION_TEXT,
        start=CAPTION_FIRST / FPS, end=(CAPTION_LAST + 1) / FPS)


def _timeline(*, lower_third_dy: int = 0) -> RevisionContext:
    """The timeline the plan built, as the planner sees it.

    Four rows: the picture, the lower third, the caption card and the
    dialogue.  The lower third's box is the plan's own placement, and
    the caption card carries no box of its own - the caption's box is
    MEASURED on the delivered file, by the caption spec.
    """
    return RevisionContext(
        project="qa009-scratch", timeline="reel-01",
        items=(
            TimelineItem("picture-1", "video", 1, 0, FRAMES, "picture",
                         "video"),
            TimelineItem("lower-third-1", "video", 2, CAPTION_FIRST,
                         CAPTION_LAST + 1, "lower third", "graphic",
                         box=(LOWER_THIRD_BOX[0], LOWER_THIRD_BOX[1] + lower_third_dy,
                              LOWER_THIRD_BOX[2], LOWER_THIRD_BOX[3] + lower_third_dy)),
            TimelineItem("caption-1", "video", 3, CAPTION_FIRST,
                         CAPTION_LAST + 1, "caption card", "caption"),
            TimelineItem("dialogue-1", "audio", 1, 0, FRAMES, "dialogue",
                         "audio"),
        ),
        fps=FPS, first_frame=0, markers=(7,))


needs_ocr = pytest.mark.skipif(
    not caption_ocr.helper_available()[0],
    reason="the delivered frames cannot be read without Apple Vision OCR")


# ── The narrow revisions, one at a time ──────────────────────────────


def _loudness_check(measured: float = -19.15) -> QualityCheck:
    """A loudness failure, as the audio spec reports it."""
    return QualityCheck(
        metric="loudness", property="integrated_loudness", passed=False,
        detail="integrated -19.15 LUFS is 5.15 dB off the -14.0 target",
        source="library.tools.deliverable_qc:verify_audio_spec (QA-004)",
        measured={"input_i": measured, "input_tp": -18.42,
                  "target_lufs": -14.0, "target_tolerance": 1.0,
                  "target_true_peak_ceiling": -1.0})


def _collision_check() -> QualityCheck:
    """A caption collision, as the caption spec reports it."""
    return QualityCheck(
        metric="caption", property="caption_collision", passed=False,
        detail="the caption collides with 'LOWER THIRD LABEL'",
        source=("library.tools.delivered_caption_spec:"
                "verify_delivered_captions (QA-003)"),
        label="caption-1",
        start=CAPTION_FIRST / FPS, end=(CAPTION_LAST + 1) / FPS,
        measured={"caption_box": [44.0, 808.0, 404.0, 836.0],
                  "collision_boxes": [[40.0, 788.0, 324.0, 812.0]],
                  "collision_frames": [16, 20, 24],
                  "frame": [0, 0, WIDTH, HEIGHT],
                  "intended_first_frame": CAPTION_FIRST,
                  "intended_last_frame": CAPTION_LAST})


def test_a_loudness_failure_becomes_one_narrow_gain():
    """The failure is a NUMBER, so the edit is a number read off it."""
    revision = plan_revision(_loudness_check(), _timeline())

    assert revision.status == PLANNED, revision.reason
    operations = revision.request.operations
    assert len(operations) == 1, operations
    operation = operations[0]
    assert operation.op == "audio.gain"
    assert operation.params["db"] == pytest.approx(5.15)
    assert operation.target.unique_id == "dialogue-1"


def test_a_loudness_revision_declares_everything_else_unchanged():
    """The previously accepted portions are named, not hoped for."""
    revision = plan_revision(_loudness_check(), _timeline())

    requirements = {row["kind"]: row for row in revision.preservation}
    assert "row_unchanged" in requirements
    unchanged = [row for row in revision.preservation
                 if row["kind"] == "row_unchanged"]
    rows = {(row["track_type"], row["track_index"]) for row in unchanged}
    assert rows == {("video", 1), ("video", 2), ("video", 3)}
    assert {"kind": "marker_preserved", "frame": 7} in [
        dict(row) for row in revision.preservation]


def test_a_caption_collision_moves_the_graphic_and_leaves_the_caption():
    """The caption's words and timing are the accepted part of the work."""
    revision = plan_revision(_collision_check(), _timeline())

    assert revision.status == PLANNED, revision.reason
    operation = revision.request.operations[0]
    assert operation.op == "overlay.position"
    assert operation.target.unique_id == "lower-third-1"
    assert operation.params["dy"] < 0, "the lower third moves UP, clear of it"
    # The caption's own row is declared unchanged, so its words and its
    # timing cannot be traded for the fix.
    assert {"kind": "row_unchanged", "track_type": "video",
            "track_index": 3} in [dict(row) for row in revision.preservation]
    # And so is the dialogue, which has nothing to do with a caption.
    assert {"kind": "row_unchanged", "track_type": "audio",
            "track_index": 1} in [dict(row) for row in revision.preservation]


def test_a_collision_with_two_candidates_is_ambiguous_not_picked():
    """A tie is a decision, and the engine does not make it."""
    context = RevisionContext(
        project="qa009-scratch", timeline="reel-01",
        items=(
            TimelineItem("lower-third-1", "video", 2, 0, FRAMES, "lower third",
                         "graphic", box=LOWER_THIRD_BOX),
            TimelineItem("watermark-1", "video", 3, 0, FRAMES, "watermark",
                         "graphic", box=LOWER_THIRD_BOX),
            TimelineItem("dialogue-1", "audio", 1, 0, FRAMES, "dialogue",
                         "audio"),
        ),
        fps=FPS, first_frame=0)

    revision = plan_revision(_collision_check(), context)

    assert revision.status == AMBIGUOUS, revision.reason
    assert revision.request is None
    assert revision.next_action == ASK_CREATOR


def test_a_black_run_needs_a_source_and_is_not_guessed_at():
    """Covering a gap needs material; choosing it is not the engine's."""
    check = QualityCheck(
        metric="black_frames", property="undeclared_black", passed=False,
        detail="0.709s of black at 47.172-47.881s",
        source="library.tools.render_qa:detect_black_frames",
        start=47.172, end=47.881,
        measured={"where": [{"start": 47.172, "end": 47.881}]})

    revision = plan_revision(check, _timeline())

    assert revision.status == NEEDS_SOURCE, revision.reason
    assert revision.request is None
    assert "47.172" in revision.reason


def test_a_subjective_failure_is_never_revised_by_the_engine():
    """Nobody can reproduce a judgement, so no edit is planned from one."""
    check = QualityCheck(
        metric="broll_correspondence", property="caption_words",
        passed=False, detail="the cutaway does not illustrate the speech",
        kind=SUBJECTIVE)

    revision = plan_revision(check, _timeline())

    assert revision.status == "subjective"
    assert revision.request is None
    assert revision.next_action == ASK_CREATOR


def test_a_second_attempt_at_a_non_improving_revision_is_refused():
    """Retrying the same edit is not a revision; it is the same edit."""
    check = _loudness_check()
    history = (RevisionOutcome(outcome=NOT_IMPROVING, target_key=check.key),)

    revision = plan_revision(check, _timeline(), history=history)

    assert revision.status == ALREADY_ATTEMPTED, revision.reason
    assert revision.request is None


def test_a_revision_is_scoped_to_the_failing_interval():
    """The request's affected frames are the item's, and no more."""
    check = QualityCheck(
        metric="loudness", property="integrated_loudness", passed=False,
        detail="the window is too quiet",
        measured={"input_i": -19.15, "target_lufs": -14.0},
        start=2.0, end=4.0)
    context = RevisionContext(
        project="qa009-scratch", timeline="reel-01",
        items=(
            TimelineItem("dialogue-1", "audio", 1, 0, 60, "dialogue", "audio"),
            TimelineItem("dialogue-2", "audio", 1, 60, 120, "dialogue", "audio"),
        ),
        fps=FPS, first_frame=0)

    revision = plan_revision(check, context)

    assert revision.status == PLANNED, revision.reason
    assert [op.target.unique_id for op in revision.request.operations] == [
        "dialogue-1"]
    assert revision.scope == (30, 60)


# ── The read-back: the write changed what it was allowed to change ───


def _snapshot(*, lower_third_position=(0.0, 0.0), volume=0.0) -> dict:
    """A timeline snapshot, in the shape the invariant reads."""
    return {
        "tracks": [
            {"type": "video", "index": 1, "clips": [
                {"unique_id": "picture-1", "record_in": 0, "record_out": FRAMES,
                 "source_file": "picture.mov", "source_in_frame": 0,
                 "source_out_frame": FRAMES,
                 "transform": {"ZoomX": 1.0}}]},
            {"type": "video", "index": 2, "clips": [
                {"unique_id": "lower-third-1", "record_in": CAPTION_FIRST,
                 "record_out": CAPTION_LAST + 1,
                 "source_file": "lower-third.png", "source_in_frame": 0,
                 "source_out_frame": 1,
                 "fusion": {"position": list(lower_third_position)}}]},
            {"type": "video", "index": 3, "clips": [
                {"unique_id": "caption-1", "record_in": CAPTION_FIRST,
                 "record_out": CAPTION_LAST + 1,
                 "source_file": "caption.mov", "source_in_frame": 0,
                 "source_out_frame": 1,
                 "transform": {"ZoomX": 1.0}}]},
            {"type": "audio", "index": 1, "clips": [
                {"unique_id": "dialogue-1", "record_in": 0, "record_out": FRAMES,
                 "source_file": "dialogue.wav", "source_in_frame": 0,
                 "source_out_frame": FRAMES,
                 "transform": {"Volume": volume}}]},
        ],
        "markers": {"timeline": [{"frame": 7}]},
    }


def test_the_write_changed_only_what_it_was_allowed_to_change():
    """The read-back judges the write: nothing else moved."""
    from library.tools import preservation_invariant

    revision = plan_revision(_collision_check(), _timeline())
    operations = [op.to_algebra_op() for op in revision.request.operations]
    before = _snapshot()
    after = _snapshot(lower_third_position=(0.0, -6.0))

    scope = preservation_invariant.scope_for(
        operations, affected_spans=[revision.scope])
    delta = preservation_invariant.invariant(
        scope=scope, before=before, operations=operations, after=after)

    assert delta.status == "preserved", delta.as_dict()
    assert delta.unrelated == ()


def test_the_declared_preservation_is_judged_and_passes():
    """A requirement nobody can judge is a promise nobody is keeping."""
    from library.tools import preservation_invariant

    revision = plan_revision(_collision_check(), _timeline())
    before = _snapshot()
    after = _snapshot(lower_third_position=(0.0, -6.0))

    judged = preservation_invariant.judge_requirements(
        revision.preservation, before=before, after=after)

    assert judged, revision.preservation
    assert {row["status"] for row in judged} == {"preserved"}, judged


# ── The verification: the issue AND the satisfied constraints ─────────


def test_a_fixed_issue_with_nothing_else_moved_is_fixed():
    """The corrected issue, on its own."""
    before = [_loudness_check(), QualityCheck(
        metric="channels", property="channel_presence", passed=True)]
    after = [QualityCheck(
        metric="loudness", property="integrated_loudness", passed=True),
        QualityCheck(
            metric="channels", property="channel_presence", passed=True)]
    revision = plan_revision(before[0], _timeline())

    outcome = verify_revision(before, after, revision)

    assert outcome.outcome == FIXED, outcome.detail
    assert outcome.next_action == ACCEPT


def test_a_failure_that_is_still_there_is_not_improving():
    """The signal to STOP, not to try the same edit again."""
    before = [_loudness_check()]
    after = [_loudness_check()]
    revision = plan_revision(before[0], _timeline())

    outcome = verify_revision(before, after, revision)

    assert outcome.outcome == NOT_IMPROVING, outcome.detail
    assert outcome.next_action == STOP


def test_a_revision_that_breaks_a_passing_check_is_a_regression():
    """Fixing the caption and breaking the loudness is not a fix."""
    before = [_collision_check(), QualityCheck(
        metric="channels", property="channel_presence", passed=True)]
    after = [QualityCheck(
        metric="caption", property="caption_collision", passed=True),
        QualityCheck(
            metric="channels", property="channel_presence", passed=False)]
    revision = plan_revision(before[0], _timeline())

    outcome = verify_revision(before, after, revision)

    assert outcome.outcome == REGRESSED, outcome.detail
    assert outcome.regressions == (("channels", "channel_presence", ""),)
    assert outcome.next_action == STOP


# ── The review surface: the narrow revision reaches the reader ───────


def _acceptance_cli():
    """The `ren accept` entry point, imported for its wiring."""
    import importlib

    return importlib.import_module(
        "library.tools.editorial_acceptance.__main__")


def test_the_review_surface_carries_a_narrow_revision_per_failure(
        seeded, seeded_verdict):
    """The failure the review names is the failure a revision is planned from."""
    cli = _acceptance_cli()

    revisions = cli._targeted_revisions(seeded_verdict, _timeline())

    statuses = {row["check"]["property"]: row["status"]
                for row in revisions}
    assert statuses == {"integrated_loudness": PLANNED,
                        "caption_collision": PLANNED}
    for row in revisions:
        assert row["request"], row
        assert row["next_action"] == "submit_through_axi_after_authorization"


def test_a_review_without_a_timeline_view_says_what_it_cannot_do(
        seeded_verdict):
    """No view, no item to name - and the engine says so."""
    cli = _acceptance_cli()

    revisions = cli._targeted_revisions(seeded_verdict, None)

    assert revisions, revisions
    assert {row["status"] for row in revisions} == {"no_timeline_view"}
    assert "no timeline view" in revisions[0]["reason"]


# ── The proof: a seeded defect, fixed by a narrow revision ───────────


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """The seeded file: a caption collision and a loudness failure."""
    root = tmp_path_factory.mktemp("qa009")
    path, frames = _render(str(root / "before.mkv"))
    return {"path": path, "frames": frames, "root": root}


@pytest.fixture(scope="module")
def seeded_verdict(seeded):
    """The seeded file, judged by the engine's own delivered-file checks."""
    return run_deliverable_qc(
        seeded["path"], resolve_delivery_spec("tiktok"), FRAMES / FPS,
        caption_intents=[_intent()], expected_width=WIDTH,
        expected_height=HEIGHT)


def test_the_seeded_file_fails_exactly_the_two_seeded_defects(seeded_verdict):
    """The denominators: what failed, and what did not."""
    checks = objective_checks(seeded_verdict)
    failed = [check for check in checks if not check.passed]

    assert {(check.metric, check.property) for check in failed} == {
        ("loudness", "integrated_loudness"),
        ("caption", "caption_collision"),
    }
    assert len(checks) == 14, [check.key for check in checks]
    assert sum(1 for check in checks if check.passed) == 12
    # Every check names its own thing: no two share an identity.
    assert len({check.key for check in checks}) == len(checks)


@needs_ocr
def test_a_seeded_caption_collision_is_fixed_by_its_narrow_revision(
        seeded, seeded_verdict):
    """The collision goes; every other caption leg and the picture stay."""
    checks = objective_checks(seeded_verdict)
    collision = next(check for check in checks
                     if not check.passed and check.property == "caption_collision")

    revision = plan_revision(collision, _timeline())
    assert revision.status == PLANNED, revision.reason
    dy = round(revision.request.operations[0].params["dy"])

    fixed_path, fixed_frames = _render(
        str(seeded["root"] / "after-collision.mkv"), lower_third_dy=dy)
    after = run_deliverable_qc(
        fixed_path, resolve_delivery_spec("tiktok"), FRAMES / FPS,
        caption_intents=[_intent()], expected_width=WIDTH,
        expected_height=HEIGHT)

    after_checks = {check.key: check for check in objective_checks(after)}
    assert after_checks[("caption", "caption_collision", "caption-1")].passed
    # Every caption leg that passed before still passes.
    for check in checks:
        if check.passed and check.metric == "caption":
            assert after_checks[check.key].passed, check.key
    # And the picture is untouched.
    for check in checks:
        if check.passed and check.metric in ("black_frames", "framing",
                                             "resolution", "freeze_frames"):
            assert after_checks[check.key].passed, check.key

    # The loop's own verdict: the issue is fixed and nothing regressed.
    outcome = verify_revision(checks, objective_checks(after), revision)
    assert outcome.outcome == FIXED, outcome.detail

    # NOTHING ELSE MOVED: the seed's own frames outside the graphic's
    # old and new boxes are identical.
    moved = _union_box(LOWER_THIRD_BOX, dy)
    assert _unchanged_outside(seeded["frames"], fixed_frames, boxes=[moved])


@needs_ocr
def test_a_seeded_loudness_failure_is_fixed_by_its_narrow_revision(
        seeded, seeded_verdict):
    """The loudness reaches the target; the picture does not move."""
    checks = objective_checks(seeded_verdict)
    loudness = next(check for check in checks
                    if not check.passed and check.property == "integrated_loudness")

    revision = plan_revision(loudness, _timeline())
    assert revision.status == PLANNED, revision.reason
    db = revision.request.operations[0].params["db"]

    fixed_path, fixed_frames = _render(
        str(seeded["root"] / "after-audio.mkv"), db=db)
    after = run_deliverable_qc(
        fixed_path, resolve_delivery_spec("tiktok"), FRAMES / FPS,
        caption_intents=[_intent()], expected_width=WIDTH,
        expected_height=HEIGHT)

    after_checks = {check.key: check for check in objective_checks(after)}
    assert after_checks[("loudness", "integrated_loudness", "")].passed
    # The number, not just the verdict: the file reads the spec's target.
    measured = after_checks[("loudness", "integrated_loudness", "")].measured
    assert measured["input_i"] == pytest.approx(-14.0, abs=1.0)
    # Every other leg that passed before still passes.
    for check in checks:
        if check.passed:
            assert after_checks[check.key].passed, check.key

    # The loop's own verdict: the issue is fixed and nothing regressed.
    outcome = verify_revision(checks, objective_checks(after), revision)
    assert outcome.outcome == FIXED, outcome.detail

    # The picture is the same picture: the frames are identical.
    assert _unchanged_outside(seeded["frames"], fixed_frames)


def _unchanged_outside(before, after, boxes=()) -> bool:
    """Whether the two frame sets are identical outside the given boxes."""
    from PIL import ImageChops

    for one, two in zip(before, after):
        difference = ImageChops.difference(one, two)
        if difference.getbbox() is None:
            continue
        for box in boxes:
            difference.paste(0, (int(box[0]), int(box[1]),
                                 int(box[2]), int(box[3])))
        if difference.getbbox() is not None:
            return False
    return True


def _union_box(box, dy: int, pad: int = 8):
    """The area the graphic occupied before and after the move."""
    x0, y0, x1, y1 = box
    return (x0 - pad, min(y0, y0 + dy) - pad,
            x1 + pad, max(y1, y1 + dy) + pad)
