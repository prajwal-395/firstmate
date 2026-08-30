"""The frame under the playhead goes with the question, and can be read.

Three guarantees, in the order they cost something when they break:

1. **The call runs where it can open the still.**  The CLI will not read
   a file outside its working directory, and the panel's call never set
   one, so it inherited whatever Resolve was launched with.  The failure
   that comes out the other side is the model saying *"I need permission
   to read the screenshot file"* - which reads as an unhelpful model, not
   as a permissions bug, so nobody would go looking for it.
   :func:`test_a_call_that_does_not_reach_the_file_is_refused` is that
   wall, and it fails against the old call shape.
2. **A grab that cannot happen degrades to a text-only question with a
   stated reason.**  A playhead over a gap and a timeline with nothing
   open are ordinary, and none of them is worth failing a question over.
3. **Nothing is written under the captain's project.**  `marker_capture`
   writes stills into `<project>/marker_feedback/stills/` by design -
   that is `Kind.CAPTURED`, a frame the captain deliberately kept.  A
   question's frame is remade by asking again, so it is panel scratch.

The grab itself is not tested here and must not be: `marker_capture` is
the one grabber and `tests/test_marker_capture_against_resolve.py` drives
it against a real Resolve.  What is tested here is everything around it,
which is why it lives in `library/tools/panel/` and needs no application.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from library.tools.panel import frame_attach

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = REPO_ROOT / "resolve_scripts" / "VEP Pipeline Panel.py"


def _frame(tmp_path, name="ask_frame.20260830T120000000000Z.png"):
    directory = Path(frame_attach.frames_dir(str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return path


# ── 1. The wall, shown closed ───────────────────────────────────────

def test_a_call_that_does_not_reach_the_file_is_refused(tmp_path):
    """The old call shape, judged.

    `["claude", "-p", "--model", m]` with no cwd is what shipped, and it
    is exactly the call the scout measured coming back asking for
    permission. The predicate has to say NO to it, or it cannot say
    anything useful about the fixed one.
    """
    still = _frame(tmp_path)
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()

    assert not frame_attach.reaches_the_file(
        str(still), cwd=str(elsewhere), argv=["claude", "-p", "--model", "m"])
    # And the true "no cwd at all" case the panel used to make.
    assert not frame_attach.reaches_the_file(
        str(still), cwd="", argv=["claude", "-p", "--model", "m"])


def test_the_call_site_reaches_the_still(tmp_path):
    """The fix, judged by the same predicate."""
    still = _frame(tmp_path)
    site = frame_attach.call_site(str(still), str(tmp_path))
    assert frame_attach.reaches_the_file(
        str(still), site.cwd, ["claude", "-p"] + list(site.extra_argv))


def test_the_guarantee_is_the_behaviour_and_not_the_mechanism(tmp_path):
    """`--add-dir` closes the same wall, so the predicate accepts it.

    The report proved both routes work and the choice between them is
    ours; pinning `cwd` specifically would make a later swap read as a
    regression when the captain's experience would be identical.
    """
    still = _frame(tmp_path)
    for argv in (["claude", "-p", "--add-dir", str(still.parent)],
                 ["claude", "-p", "--add-dir=%s" % still.parent]):
        assert frame_attach.reaches_the_file(str(still), cwd="/", argv=argv)


def test_a_question_with_no_frame_reaches_nothing_and_is_fine(tmp_path):
    """A text-only question has no file to open, so no cwd can be wrong.

    The predicate must not answer False here, or the degraded path would
    look like the failure it is meant to be the alternative to.
    """
    assert frame_attach.reaches_the_file("", cwd="", argv=[])


def test_the_call_site_names_a_directory_even_with_no_frame(tmp_path):
    """An inherited cwd is what made the failure unreproducible, so the
    panel names one whether or not a picture is going."""
    site = frame_attach.call_site("", str(tmp_path))
    assert site.cwd == frame_attach.frames_dir(str(tmp_path))
    assert os.path.isdir(site.cwd)


def test_a_sibling_directory_is_not_reached(tmp_path):
    """`/a/frames` must not satisfy a file in `/a/frames-old` - a prefix
    match without the separator would say it does."""
    still = _frame(tmp_path)
    assert not frame_attach.reaches_the_file(
        str(still), cwd=str(still.parent) + "-old")


# ── 2. The entry point really passes it ─────────────────────────────

def test_the_entry_point_hands_the_call_a_call_site():
    """The predicate is only worth anything if the shipped call uses it.

    Read off the source, because the widget layer cannot be driven
    without Resolve - the same reason `test_panel_boundary.py` reads the
    worker guard off the source.
    """
    source = ENTRY_POINT.read_text(encoding="utf-8")
    assert "frame_attach.call_site" in source, (
        "the panel decides no call site, so the call inherits Resolve's cwd")
    tree = ast.parse(source)
    ask = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "ask_model")
    assert "call_site" in [a.arg for a in ask.args.args], (
        "ask_model takes no call site, so nothing can set its cwd")
    run = next(n for n in ast.walk(ask)
               if isinstance(n, ast.Call)
               and getattr(n.func, "attr", "") == "run")
    assert "cwd" in [k.arg for k in run.keywords], (
        "subprocess.run is called without cwd=, which is the whole defect")


def test_the_panel_writes_no_second_frame_grabber():
    """`marker_capture` is the one grabber (it is measured against a real
    render and it puts the gallery back). A `GrabStill` in the panel
    would be a second one, unmeasured, with no gallery restore."""
    source = ENTRY_POINT.read_text(encoding="utf-8")
    assert "marker_capture.grab_still" in source
    assert "ExportStills" not in source
    assert source.count("GrabStill") == 0


# ── 3. A grab that failed still asks the question ───────────────────

def test_a_failed_grab_states_its_reason_in_the_prompt():
    frame = frame_attach.Frame(reason="the playhead is over a gap")
    block = frame_attach.attach_to_block("MEASUREMENTS", frame)
    assert "No frame was attached" in block
    assert "the playhead is over a gap" in block
    assert "MEASUREMENTS" in block, "the question must still carry its context"
    assert "say plainly" in block, "the answer has to admit it saw no picture"


def test_a_reason_that_already_ends_in_a_stop_is_not_doubled():
    """`marker_capture`'s refusals are whole sentences."""
    frame = frame_attach.Frame(reason="Resolve declined to grab a still.")
    assert ".." not in frame_attach.prompt_lines(frame)[2]


def test_an_attached_frame_tells_the_model_three_things(tmp_path):
    """The path to open, that it is the DELIVERED picture, and that it is
    one instant against measurements that span the whole clip."""
    still = _frame(tmp_path)
    frame = frame_attach.Frame(path=str(still), timecode="00:00:13:04")
    block = frame_attach.attach_to_block("MEASUREMENTS", frame)
    assert str(still) in block
    assert "Read it" in block
    assert "GRADED, CONFORMED" in block
    assert "ONE INSTANT" in block
    assert "00:00:13:04" in block


def test_the_frame_goes_above_the_measurements():
    """It is the most concrete thing in the prompt and the model latches
    onto that (the ordering failure `clip_context` was built against), and
    it is out of reach of the budget, which cuts the TAIL."""
    frame = frame_attach.Frame(path="/tmp/f.png")
    block = frame_attach.attach_to_block("WHAT THE EDITOR IS LOOKING AT",
                                         frame)
    assert block.index(frame_attach.FRAME_HEADING) < block.index(
        "WHAT THE EDITOR IS LOOKING AT")


def test_the_description_says_which_outcome_it_is(tmp_path):
    got = frame_attach.Frame(path=str(_frame(tmp_path)), timecode="00:00:01:00",
                             seconds=1.4, size_bytes=6 << 20)
    assert "attached" in frame_attach.describe(got)
    assert "1.4s" in frame_attach.describe(got)
    missing = frame_attach.Frame(reason="no timeline")
    assert "no frame attached" in frame_attach.describe(missing)
    assert "no timeline" in frame_attach.describe(missing)


def test_the_environment_can_decline_the_frame():
    """A picture costs seconds per question, so there is a way to say no
    that does not add a control to a panel the captain has already seen."""
    assert frame_attach.disabled_by_environment({"VEP_PANEL_NO_FRAME": "1"})
    assert frame_attach.disabled_by_environment({"VEP_PANEL_NO_FRAME": "true"})
    assert not frame_attach.disabled_by_environment({})
    assert not frame_attach.disabled_by_environment({"VEP_PANEL_NO_FRAME": "0"})


# ── Where the still goes, and how many are kept ─────────────────────

def test_the_still_goes_to_panel_scratch_and_never_to_the_project(tmp_path):
    destination = frame_attach.still_destination(str(tmp_path))
    assert destination.startswith(frame_attach.frames_dir(str(tmp_path)))
    assert "marker_feedback" not in destination
    assert os.path.isdir(os.path.dirname(destination))


def test_two_asks_in_one_second_do_not_collide(tmp_path):
    """An answer names the picture it was given, so a second ask must not
    replace the file the first one's answer points at."""
    first = frame_attach.still_destination(str(tmp_path))
    second = frame_attach.still_destination(str(tmp_path))
    assert first != second


def test_only_the_newest_frames_are_kept(tmp_path):
    for index in range(6):
        _frame(tmp_path, "ask_frame.2026083%dT120000000000Z.png" % index)
    removed = frame_attach.prune_frames(str(tmp_path), keep=2)
    left = sorted(os.listdir(frame_attach.frames_dir(str(tmp_path))))
    assert len(removed) == 4
    assert left == ["ask_frame.20260834T120000000000Z.png",
                    "ask_frame.20260835T120000000000Z.png"]


def test_the_pruner_only_considers_files_it_named(tmp_path):
    """The same rule the pipeline_data backup pruner holds: a file a
    person dropped in beside them is not this function's to delete."""
    _frame(tmp_path)
    theirs = Path(frame_attach.frames_dir(str(tmp_path))) / "keep-me.png"
    theirs.write_bytes(b"x")
    frame_attach.prune_frames(str(tmp_path), keep=0)
    assert theirs.exists()


def test_pruning_an_absent_directory_is_not_an_error(tmp_path):
    assert frame_attach.prune_frames(str(tmp_path / "never-made")) == []
