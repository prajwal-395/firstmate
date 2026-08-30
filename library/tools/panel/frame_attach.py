"""The frame under the playhead goes to the model with the question.

The panel's join (`clip_context`) fixed the first half of the captain's
steer: it stopped handing the model a filename and started handing it
what the pipeline MEASURED about the clip.  This is the other half.  A
measurement is a reading of the whole clip; the captain is looking at one
frame, and *"why is this fully blurry"* is a question about that frame.

Nothing here grabs a picture.  `library/tools/marker_capture.grab_still`
is the ONE grabber - it returns the graded, conformed timeline frame
(measured at 0.36-0.38/255 against a Deliver render of the same
timecode), and it puts the gallery back exactly as it found it.  This
module decides the three things around it that have nothing to do with
Resolve, and can therefore be tested without it: WHERE the still goes,
WHERE the model call has to run to be able to read it, and WHAT the model
is told it is looking at.

── The wall, and which side of it we close ─────────────────────────────

**The CLI will not read a file outside its working directory.**  Measured
by the scout, 2026-08-30: the same image and the same call, run from a
different cwd, came back *"I need permission to read the screenshot file.
Could you grant access to ..."*.  That is the failure this module exists
to prevent, and it is nasty precisely because it does not look like a
bug - it looks like an unhelpful model, and the natural next move is to
reword the question.

Two routes close it: run the call with `cwd` inside the directory holding
the still, or pass the CLI an `--add-dir`.  **This picks `cwd`**, for
three reasons:

1. The panel's call has never set one, so it inherits whatever Resolve
   was launched with.  An unpredictable cwd is the root of the failure
   above; naming one fixes the class rather than one instance.
2. `cwd` is a property of a process we spawn.  `--add-dir` is a flag on a
   CLI whose interface is not ours, and a renamed flag would fail exactly
   as silently as no flag at all.
3. The directory is one this module owns and that holds nothing but
   frames the panel wrote, so widening the CLI's reach to it discloses
   nothing.  It is deliberately NOT the repository and NOT the captain's
   project.

:func:`reaches_the_file` is what the test asserts, so the guarantee pinned
is *"the call can read the still"* and not *"the call sets cwd"* - either
route satisfies it, and neither route missing does.

── Where the still goes ────────────────────────────────────────────────

The panel's own `~/.vep_panel`, never the project.  `marker_capture`
writes into `<project>/marker_feedback/stills/` by design, because a
frame the captain deliberately captured onto a marker is `Kind.CAPTURED`
- irreproducible, and destroyed by precisely the re-render that would
recreate everything else.  A frame attached to a question is the
opposite: asking again remakes it.  So it is panel scratch, it is pruned,
and it never writes a byte under the captain's project tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

FRAMES_DIRNAME = "frames"
"""Inside the panel's `SCRATCH`.  Its own directory, because `cwd` is set
to it for the model call and the heartbeat log has no business being
inside the CLI's reach."""

FRAME_PREFIX = "ask_frame"
FRAME_SUFFIX = ".png"

KEEP_FRAMES = 10
"""How many stills to keep.  Not one overwritten file: an answer names
the picture it was given, and two asks a second apart would otherwise
leave the first answer pointing at the second question's frame.  Not
unbounded either - an exported still is a FIXED size whatever is in it
(`marker_capture`'s own measurement; 6,232,792 bytes off 001's
1080x1920 timeline, the all-black one included), so ten is ~62 MB and a
thousand asks is 6 GB of panel scratch."""

FRAME_HEADING = "THE FRAME THE EDITOR IS LOOKING AT"

NO_TIMELINE = "there is no timeline open in Resolve"
NO_PROJECT = "Resolve has no project open"
DISABLED = ("frame attachment is off for this panel "
            "(VEP_PANEL_NO_FRAME is set)")

DISABLE_ENV = "VEP_PANEL_NO_FRAME"


@dataclass
class Frame:
    """The still that went with a question, or the reason none did.

    One dataclass for both outcomes, because a caller that has to ask
    "did I get a frame or an error" in two places will one day forget in
    one of them.  `path` empty and `reason` set is the whole of "no
    frame", and :func:`prompt_lines` renders it as a stated absence
    rather than as silence.
    """

    path: str = ""
    reason: str = ""
    timecode: str = ""
    seconds: float = 0.0
    """How long the grab took, so the cost of the picture is reportable
    rather than felt."""

    size_bytes: int = 0

    @property
    def attached(self) -> bool:
        return bool(self.path)


def disabled_by_environment(environ=None) -> bool:
    """The escape hatch, as an environment variable rather than a widget.

    The captain has seen the five tabs and a new control is a change to
    what they were shown.  A frame costs seconds per question (measured
    in the PR), so there has to be a way to decline it; this is the way
    that adds nothing to the screen.
    """
    env = os.environ if environ is None else environ
    return str(env.get(DISABLE_ENV, "")).strip().lower() in {
        "1", "true", "yes", "on"}


# ── Where the still goes ────────────────────────────────────────────

def frames_dir(scratch: str) -> str:
    return os.path.join(scratch, FRAMES_DIRNAME)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def still_destination(scratch: str, stamp: Optional[str] = None) -> str:
    """A fresh path under the panel's scratch, made if it is not there.

    Microseconds in the stamp: two asks inside one second are ordinary
    and a collision would silently replace the picture an answer already
    on screen points at.
    """
    directory = frames_dir(scratch)
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory,
                        "%s.%s%s" % (FRAME_PREFIX, stamp or _stamp(),
                                     FRAME_SUFFIX))


def prune_frames(scratch: str, keep: int = KEEP_FRAMES) -> List[str]:
    """Delete all but the newest `keep` stills, and say which went.

    Only files this module names are ever considered - the same rule the
    `pipeline_data.json` backup pruner holds (AGENTS.md section 8): a
    file dropped in beside them by a person is not this function's to
    delete.
    """
    directory = frames_dir(scratch)
    if not os.path.isdir(directory):
        return []
    ours = [name for name in os.listdir(directory)
            if name.startswith(FRAME_PREFIX + ".")
            and name.endswith(FRAME_SUFFIX)]
    removed: List[str] = []
    for name in sorted(ours, reverse=True)[max(0, keep):]:
        path = os.path.join(directory, name)
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            continue
    return removed


# ── Where the call has to run ───────────────────────────────────────

@dataclass
class CallSite:
    """Where the model call runs, and what it is told it may read."""

    cwd: str
    extra_argv: Tuple[str, ...] = ()


def call_site(image_path: str, scratch: str) -> CallSite:
    """The directory the call runs in so the CLI can read the still.

    With no still it still names the panel's frames directory rather than
    leaving the cwd inherited: a text-only question does not need it, and
    an inherited cwd that varies with how Resolve was launched is the
    thing that made the failure unreproducible in the first place.
    """
    directory = (os.path.dirname(os.path.abspath(image_path))
                 if image_path else frames_dir(scratch))
    os.makedirs(directory, exist_ok=True)
    return CallSite(cwd=directory)


def reaches_the_file(image_path: str, cwd: str,
                     argv: Sequence[str] = ()) -> bool:
    """Can a call made like this READ that file?

    The behaviour the test pins, deliberately not the mechanism: the
    file is under the working directory, or a `--add-dir` names a
    directory it is under.  Either closes the wall; neither present does
    not, whatever else the call gets right.
    """
    if not image_path:
        return True                     # nothing to read, nothing to reach
    target = os.path.abspath(image_path)
    allowed = [cwd] if cwd else []
    argv = list(argv)
    for index, token in enumerate(argv):
        if token == "--add-dir" and index + 1 < len(argv):
            allowed.append(argv[index + 1])
        elif token.startswith("--add-dir="):
            allowed.append(token.split("=", 1)[1])
    for directory in allowed:
        if not directory:
            continue
        root = os.path.abspath(directory)
        if target == root or target.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


# ── What the model is told it is looking at ─────────────────────────

def prompt_lines(frame: Frame) -> List[str]:
    """The frame's own section of the context block.

    Three things are said, and each was chosen against a way the answer
    goes wrong without it:

    * **Read it, at this absolute path.** The picture reaches the model
      because the prompt names a file it can open, so the path is an
      instruction and not a caption.
    * **It is the graded, conformed frame.** Otherwise a colour question
      is answered about the source file, which is a different picture -
      63.35/255 different, on `marker_capture`'s own measurement.
    * **It is ONE INSTANT and the prose is the WHOLE CLIP.** This is the
      one that matters. The measurements below describe a span; the
      picture is a moment inside it, so a disagreement between them is
      two spans and not a contradiction. Without the sentence the model
      has to guess which to believe, and the scout's step 2 evidence is
      that it describes a picture very literally once it has one.
    """
    if not frame.attached:
        return [
            FRAME_HEADING, "",
            "No frame was attached to this question: %s."
            % (frame.reason or "the grab did not produce one").rstrip("."),
            "Answer from the measurements below alone, and say plainly "
            "that you could not see the picture.",
            "",
        ]
    return [
        FRAME_HEADING, "",
        "A PNG of the frame at the playhead is on this machine at:",
        "  " + frame.path,
        "Read it. That file is the picture the editor has on screen"
        + (" at %s" % frame.timecode if frame.timecode else "")
        + " - the GRADED, CONFORMED timeline frame Resolve is showing, not "
          "the raw source file, so its colour and its framing are the "
          "delivered ones.",
        "",
        "It is ONE INSTANT. Everything below was measured over the WHOLE "
        "clip, so where the picture and the prose differ they are "
        "describing different spans rather than contradicting each other. "
        "Use the picture for what is on screen now and the measurements "
        "for what the pipeline knows, and say which one an answer came "
        "from.",
        "",
    ]


def attach_to_block(context_block: str, frame: Frame) -> str:
    """The frame's section, then the joined context.

    It goes FIRST, for the reason `clip_context.prompt_block` puts what
    the captain is looking at above what they last clicked: the model
    latches onto the most concrete thing in the prompt, and the frame is
    the most concrete thing there is. It also puts the picture out of
    reach of the budget, which cuts the TAIL.
    """
    return "\n".join(prompt_lines(frame)) + "\n" + (context_block or "")


def describe(frame: Frame) -> str:
    """One line for the panel to put on screen beside the answer."""
    if not frame.attached:
        return "no frame attached - %s" % (frame.reason or "the grab failed")
    size = ("%.1f MB" % (frame.size_bytes / float(1 << 20))
            if frame.size_bytes else "unknown size")
    return ("frame at %s attached (%s, grabbed in %.1fs)"
            % (frame.timecode or "the playhead", size, frame.seconds))
