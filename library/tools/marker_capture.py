"""Capture the frame the captain is looking at, onto the marker there.

§15 gave the captain a way to talk to the pipeline: type a note onto a
Resolve timeline marker, and `marker_feedback.py` collects it.  It worked
immediately and immediately showed its one gap - their first note landed
on a frame with FOUR clips stacked under it, and the reader could only
list all four.  A picture of the frame removes the guess.

This module is the writing half.  From the captain's side it is one click
in Workspace > Scripts: playhead on the moment, click, done.  Underneath
it reads the playhead, grabs the still Resolve is showing, exports it as
a PNG into `<project>/marker_feedback/stills/`, and writes a
`marker_payload` record into the marker's `customData` - creating the
marker if there is none there, and updating it in place if there is, in
both cases without touching a character of what the captain typed.

The entry point Resolve calls is `resolve_scripts/Capture Frame for
Firstmate.py`; `scripts/install_resolve_scripts.sh` puts it where
Workspace > Scripts can see it.  Nothing in the pipeline writes into the
captain's application support folder as a side effect of anything.

── What the API actually did ───────────────────────────────────────────

Measured against DaVinci Resolve Studio 21.0.0b.28 on macOS, 2026-08-28,
on throwaway timelines (`fm_capture_probe_*`) built and deleted by the
probe.  `hasattr` is useless on Resolve's proxies (§5), so every call
below is judged by what it returned.

`Timeline.GrabStill()` RETURNS THE GRADED, CONFORMED TIMELINE FRAME, not
the source frame - this is the finding the button's worth rests on, and
it was measured rather than assumed.  On a 1080x1920 timeline carrying a
1920x1080 source clip, one frame was taken three ways:

    exported still, no grade      1080x1920  meanSat 17.3  picture rows 656..1263
    exported still, graded        1080x1920  meanSat  0.0  picture rows 420..1499
    Deliver render, same frame    1080x1920  meanSat  0.0  picture rows 420..1499
    raw source frame via ffmpeg   1920x1080  meanSat 56.0  full frame

The grade was a CDL with `Saturation 0` and a red-ward offset; the
conform was `ZoomX`/`ZoomY` 1.7778 plus `Pan` 120.  The still tracks BOTH:
the letterbox band moves with the zoom, and the colour goes with the CDL.
Against the Deliver render of the same timeline frame the still is a mean
absolute difference of 1.61/255 (p99 7, max 20) - h.264 quantisation, the
two are the same picture.  Against the ungraded still it is 63.35/255.
Resolve's own log calls it a "Full version snapshot".

THE PAGE DOES NOT MATTER.  Grabs taken on the Edit page and on the Color
page at the same frame exported byte-identical PNGs (md5
e70be9dda51a2a0e1986c619c1f8b5fb both).  So the button never changes the
captain's page, which would be a visible disturbance for nothing.

THE PAGE MATTERS FOR THE PLAYHEAD.  `SetCurrentTimecode` returns False
and moves nothing unless Resolve is on the Edit page (measured
2026-09-17: False on the Fusion page with `GetCurrentTimecode` reading
None straight after, True on Edit).  A script that positions the
playhead must `OpenPage("edit")` first and read the playhead back -
`read_playhead` asserting the absolute frame is that read-back.

THE DISPLAY MUST BE AWAKE.  With the display asleep `screencapture`
returns a pure-black frame, and viewer stills come back missing
upper-track overlays while lower tracks read correctly (measured
2026-09-17: captions pixel-perfect, motion graphics absent, on the same
frames that verify exactly with the display held awake).  Hold it with
`caffeinate -d` for the whole measurement run, and re-grab rather than
trust a still taken while it may have slept.

A STILL'S FIRST GRAB IS NOT EVIDENCE.  The first `GrabStill` after a
timeline switch can return before the upper tracks have composited
(measured 2026-09-17: first grab blank, second grab full, same frame,
ten seconds apart).  Position, grab, discard, grab again, and judge the
keeper by counting the expected ink - never by the grab succeeding.

MASK EVERY INK, NOT ONE BRAND COLOUR.  A motion-graphics title carries
time-varying emphasis colours: base pink `(255,184,212)`, emphasis
yellow `(255,200,87)`, list-build grey `(170,187,204)` - all three
measured off the same file at different media frames.  A pink-only mask
reports a drawing title absent.  Sample the artefact file at the played
media frame first, then mask the still for every colour the file holds.


`GalleryStillAlbum.ExportStills` WRITES A `.drx` BESIDE EVERY PNG, unasked
- `<prefix>_1.1.1.png` and `<prefix>_1.1.1.drx`, a PowerGrade sidecar.
A capture sidecar is not a staged grade - it carries no recorded
provenance, so `color.power_grade_drx` would refuse it (AGENTS.md 11,
library/tools/color_page_grade.py). The export goes to a scratch
directory, the PNG is moved to its real name, and the whole scratch
directory is removed.  The `_1.1.1` suffix is Resolve's own and is not
a name a caller can choose, which is the other reason for the scratch
directory.

`ExportStills` RETURNS True.  It also returned True in the run where the
file was wanted, so the return value is not evidence: the PNG is checked
on disk, and a True with no file is reported as a failure.  A False
return is a failure even when a file IS on disk - see "WHEN THE ROUTE
FAILS" below.

`Timeline.GrabStill()` LEAVES THE STILL IN THE GALLERY, in the current
still album, and it PERSISTS - Resolve saves the project immediately
after ("Start saving project" in its log, 17 ms).  Repeated clicking
would leave the captain a gallery full of stills to clean up.
`GalleryStillAlbum.DeleteStills([still])` returns True and the album goes
back to the count it had; the button always does this, and reports the
before/after count so a leak cannot go quiet.

── WHEN THE ROUTE FAILS ────────────────────────────────────────────

On 2026-09-10 a lane reported the captain's `.drx` grade moving
599,583 pixels, 28.9% of the frame.  A later lane reproduced the real
number as 486 pixels, 0.023% - and found the cause: the
`GrabStill` + `ExportStills` capture route RETURNS False AND WRITES NO
FILE on this build (Resolve Studio 21.0.0b), reproduced live.  All three
numbers in the first report clustered around 600,000 px because they
were a constant - the picture area - not a measurement.  The captain
was told something that never happened.

What is known: the failure shape is `ExportStills` returning False
with nothing on disk, on Resolve Studio 21.0.0b - where the 2026-08-28
probe above, on 21.0.0b.28, got True and a file.  What is NOT yet
isolated: whether the difference is the build, the project state, the
page, or the still album.  A rebuild was running in the captain's
project when this was written, so no live re-probe was attempted here
(switching timelines would disturb it); the isolation matrix is open
work, not a silent assumption.

The rule this produced: A CAPTURE THAT DID NOT HAPPEN RAISES BY NAME.
`grab_still` raises `StillCaptureError` - a `CaptureError`, so every
existing `except CaptureError` still catches it - on all three failure
shapes: `GrabStill` declining (False/None), `ExportStills` returning
False, no PNG on disk, and a zero-byte PNG.  A caller must never
receive a value it can average from a capture that failed.

── WHICH CAPTURE ROUTES ARE TRUSTWORTHY ────────────────────────────

Three routes capture a frame in this repo.  Choose by what you need,
and know what each one proves:

* `grab_still` here (gallery `GrabStill` + `ExportStills`): the ONLY
  route that returns the GRADED, CONFORMED timeline frame - the picture
  the captain is looking at.  Untrusted until it returns: it raises
  `StillCaptureError` unless the PNG is on disk and non-empty.  It
  writes into the captain's gallery (put back afterwards) and saves
  the project, so it never runs unattended on a project a rebuild is
  using.
* `segment_renderer.render_single_frame` (Deliver-page render plus an
  ffmpeg PNG extraction): the trustworthy fallback.  It renders through
  the Deliver page rather than the gallery, returns None (never a
  path) when the render, the extraction, or the non-empty check fails,
  and `visual_qa_router.execute_frame_grab` turns that None into a
  FAILED check with the reason - never a passing measurement.
* ffmpeg straight off a source or render file (`ask_the_footage`,
  `verify_treatment`, `thumbnail_extractor`, `window_frames`,
  step 5.01 `grade._extract_frame`): cheap seeks, no Resolve, but the
  SOURCE frame - ungraded, unconformed, no comps or captions.  Every
  one of these accepts a capture only when the file exists AND is
  non-empty; a zero-byte file reads as a failure, never as a picture.

MARKERS AND `customData`, measured:
`Timeline.AddMarker(frame, colour, name, note, duration, customData)`
carries customData in at creation.  `UpdateMarkerCustomData(frame, data)`
REPLACES the string, leaves `name`, `note`, `colour` and `duration`
untouched, and takes only the marker's START frame - an interior frame of
a marker with duration > 1 is refused.  `GetMarkerCustomData(frame)`
reads it back; a 4 KB payload with apostrophes and an en-dash round-tripped
byte for byte.  `AddMarker` on a frame that already carries a marker
returns False (§15), so an existing marker is ALWAYS updated in place and
never replaced - which is also the only way the captain's typed text
survives.

`Timeline.GetCurrentTimecode()` is the playhead.  A marker key is
relative to the timeline's start (§15), so the key is
`frames(current) - frames(GetStartTimecode())`, computed with the
timeline's own rate and drop-frame rule.

`SetCurrentTimecode` RETURNS True FOR A TIMECODE THE TIMELINE DOES NOT
HAVE, and the playhead then READS BACK THERE - `00:01:00:00` set and read
on a 120-frame timeline, and `00:00:08:00` on a 179-frame one.  (It was
also seen clamping to the last frame on a timeline that had been sitting
open, so neither behaviour can be relied on.)  `GrabStill` at such a
position returns a perfectly good GalleryStill and exports an entirely
BLACK 1080x1920 PNG, and `AddMarker` bounds-checks nothing (§15), so the
click would produce a black frame on a marker nobody can see.
`Timeline.GetEndFrame()` is in the same absolute space as
`GetStartFrame()` and `GetEndFrame() - GetStartFrame()` equals the placed
clips' own `GetDuration()`, so `read_playhead` computes the bound and
refuses rather than trusting any return value.  It refuses on the
POSITION and never on the picture: a black frame inside the timeline is a
legitimate thing to ask about, and this pipeline plans declared black
beats (§10.2).

NOTE FOR ANY SIZE HEURISTIC: an exported still is a FIXED SIZE whatever
is in it.  All four PNGs from one 1080x1920 timeline came out at exactly
6,232,792 bytes, the black one included, so §5's "under 2KB is a broken
frame" rule does not transfer to this route.


Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 15
keeps the headline and points here.

Playhead on the moment, one click in **Workspace > Scripts > Capture Frame for Firstmate**, and the
frame plus whatever the captain typed is captured. `library/tools/marker_capture.py` is the whole
of it; `resolve_scripts/` is the entry point Resolve calls.
- **The repository is the source of truth.** The installer stamps the checkout's path. **Nothing else may write into the application support folder.**
- **`GrabStill` returns the GRADED, CONFORMED frame** regardless of the active Resolve page.
- **The still goes to `<project>/marker_feedback/stills/`** (`Kind.CAPTURED`). A timeline whose footage sits under no project is REFUSED.
- **The gallery is put back.** `GrabStill` leaves the still in the current album and Resolve saves
  it; the button deletes it again and reports the before/after count.
- **A marker that is already there is UPDATED, never replaced** - `AddMarker` refuses an occupied
  frame anyway, and the captain's `name` and `note` are what must survive. The playhead inside a
  marker with a duration resolves to that marker's start frame.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools import (  # noqa: E402
    marker_payload,
    resolve_surfaces,
    timeline_decisions,
)
from library.tools.resolve_lock import under_lease
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402

WRITER = "capture_frame"
WRITER_VERSION = 1

STILLS_SUBDIR = "stills"
"""Inside `Area.MARKER_FEEDBACK`, beside the pull files.  CAPTURED, not
OUTPUT: a frame the captain chose is no more reproducible than the note
they typed on it, and both die with the timeline a re-render deletes."""

STILL_FORMAT = "png"

CAPTURED_SURFACE = resolve_surfaces.GALLERY_STILL
"""WHICH SURFACE THIS ROUTE CAPTURES.  `GrabStill` returns the graded,
conformed timeline frame regardless of the open page, which is one of the
two surfaces a grade may be measured on (AGENTS.md 5,
`library/tools/resolve_surfaces.py`).  Named here so `grab_still` can
assert it rather than every caller having to know it."""

CREATED_MARKER_NAME = "Frame captured"
"""`AddMarker` refuses an empty name (§15), so a created marker must carry
one.  It says what happened and nothing else; the captain types over it."""

CREATED_MARKER_COLOR = "Blue"
"""The colour Resolve's own Add Marker dialog defaults to, so a marker
this button makes is indistinguishable from one the captain made.  §15
forbids a colour VOCABULARY - reading meaning out of colour - not passing
the argument `AddMarker` demands."""

PROJECT_MARKER_FILE = "project.yaml"
"""What makes a directory a pipeline project.  The button finds the
project by walking up from the footage on the timeline, so the captain is
never asked to name a folder."""


class CaptureError(RuntimeError):
    """The capture could not be completed, with a reason to show."""


class StillCaptureError(CaptureError):
    """The still was not captured: the instrument failed, not the scene.

    Raised - never a return value - on all three failure shapes of the
    `GrabStill` + `ExportStills` route: `GrabStill` declining, `ExportStills`
    returning False, and no (or empty) file on disk.  A subclass of
    `CaptureError`, so every existing `except CaptureError` still catches
    it; the distinct name is what lets a test - and a caller that must
    tell "no picture" from "bad position" - pin the capture failure
    itself.  See "WHEN THE ROUTE FAILS" in the module docstring for the
    2026-09-10 false finding this exists because of.
    """


# ── The playhead ────────────────────────────────────────────────────


def timeline_fps(timeline) -> float:
    for key in ("timelineFrameRate", "timelinePlaybackFrameRate"):
        raw = timeline.GetSetting(key)
        try:
            fps = float(raw)
        except (TypeError, ValueError):
            continue
        if fps > 0:
            return fps
    raise CaptureError(
        f"Timeline {timeline.GetName()!r} reports no frame rate, so a "
        f"timecode cannot be turned into a frame."
    )


_TC = re.compile(r"^(\d+):(\d+):(\d+)([:;])(\d+)$")


def timecode_to_frames(timecode: str, fps: float) -> int:
    """Frames since 00:00:00:00 for a Resolve timecode.

    A `;` before the frames field is Resolve's drop-frame marker and the
    arithmetic differs; guessing from the rate alone would be wrong for a
    29.97 timeline running non-drop, which Resolve allows.
    """
    match = _TC.match((timecode or "").strip())
    if not match:
        raise CaptureError(f"Resolve returned an unreadable timecode: {timecode!r}")
    hours, minutes, seconds, sep, frames = (
        int(match.group(1)), int(match.group(2)), int(match.group(3)),
        match.group(4), int(match.group(5)),
    )
    nominal = int(round(fps))
    total = ((hours * 3600 + minutes * 60 + seconds) * nominal) + frames
    if sep == ";":
        dropped = int(round(fps * 0.066666))  # 2 at 29.97, 4 at 59.94
        elapsed_minutes = hours * 60 + minutes
        total -= dropped * (elapsed_minutes - elapsed_minutes // 10)
    return total


@dataclass
class Playhead:
    """Where the captain is looking, in all three frame spaces at once."""

    timecode: str
    marker_key: int
    """Relative to `GetStartFrame()` - the space `AddMarker` takes."""

    absolute_frame: int
    """`GetStartFrame() + marker_key` - the space `read_notes` reports."""

    fps: float
    start_frame: int
    start_timecode: str


def read_playhead(timeline) -> Playhead:
    """Where the playhead is, refusing a position off the timeline.

    The bounds check is not fussiness.  A playhead past the last frame
    grabs a still that is entirely BLACK - measured - and puts a marker
    somewhere the captain cannot see it, since `AddMarker` bounds-checks
    nothing (§15).  Judging the picture instead of the position would be
    wrong: a black frame INSIDE the timeline is a legitimate thing to ask
    about, and a declared black beat is a thing this pipeline plans.
    """
    fps = timeline_fps(timeline)
    current = timeline.GetCurrentTimecode()
    start_tc = timeline.GetStartTimecode()
    start_frame = int(timeline.GetStartFrame())
    key = timecode_to_frames(current, fps) - timecode_to_frames(start_tc, fps)
    if key < 0:
        raise CaptureError(
            f"The playhead reads {current} but the timeline starts at "
            f"{start_tc}; that is before the first frame."
        )
    last_key = int(timeline.GetEndFrame()) - start_frame - 1
    if last_key >= 0 and key > last_key:
        raise CaptureError(
            f"The playhead reads {current}, which is past the end of "
            f"{timeline.GetName()!r} (its last frame is {last_key} frames "
            f"in). Resolve grabs a black still there. Put the playhead on "
            f"the moment you want and click again."
        )
    return Playhead(
        timecode=current, marker_key=key, absolute_frame=start_frame + key,
        fps=fps, start_frame=start_frame, start_timecode=start_tc,
    )


# ── Which project this timeline belongs to ──────────────────────────


def _project_root_above(path: str):
    try:
        p = Path(path).resolve()
    except Exception:
        return None
    for parent in p.parents:
        if (parent / PROJECT_MARKER_FILE).is_file():
            return parent
    return None


def project_folder_from_timeline(timeline):
    """The pipeline project this timeline's footage comes out of, or None.

    Measured, not configured: every clip's own file is walked upward to
    the nearest directory carrying `project.yaml`, and the answer is the
    one the most clips agree on.  That is what lets the button be one
    click - the captain never names a folder, and a timeline built from
    another project's footage cannot silently write into this one.
    """
    votes: dict = {}
    order: list = []
    for track_type in ("video", "audio"):
        count = timeline.GetTrackCount(track_type) or 0
        for index in range(1, count + 1):
            for item in timeline.GetItemListInTrack(track_type, index) or []:
                pool_item = item.GetMediaPoolItem()
                if not pool_item:
                    continue
                path = pool_item.GetClipProperty("File Path") or ""
                if not path:
                    continue
                root = _project_root_above(path)
                if root is None:
                    continue
                key = str(root)
                if key not in votes:
                    votes[key] = 0
                    order.append(key)
                votes[key] += 1
    if not votes:
        return None
    # Most clips wins; ties break on the first clip found, so the answer
    # does not move between two runs over the same timeline.
    return Path(max(order, key=lambda k: (votes[k], -order.index(k))))


# ── The still ───────────────────────────────────────────────────────


@dataclass
class StillResult:
    path: Path
    gallery_album: str
    stills_before: int
    stills_after: int
    exported_names: list = field(default_factory=list)
    discarded: list = field(default_factory=list)
    surface: str = ""
    """WHICH RESOLVE SURFACE THIS IMAGE IS - `CAPTURED_SURFACE`.  It is
    recorded rather than assumed because the neighbouring surfaces -
    either page's viewer - are NOT the delivered picture, and a lane that
    mixes them up grades against a preview."""


def _current_album(project):
    gallery = project.GetGallery()
    if not gallery:
        raise CaptureError("Resolve returned no Gallery for this project.")
    album = gallery.GetCurrentStillAlbum()
    if not album:
        raise CaptureError("Resolve returned no current still album.")
    return gallery, album


def grab_still(timeline, project, destination: Path) -> StillResult:
    """Export the frame at the playhead to `destination`, leaving no trace.

    The gallery is put back exactly as it was found: the grabbed still is
    deleted again, and the `.drx` sidecar `ExportStills` writes unasked
    goes with the scratch directory.
    """
    gallery, album = _current_album(project)
    album_name = gallery.GetAlbumName(album) or ""
    before = len(album.GetStills() or [])

    still = timeline.GrabStill()
    if not still:
        raise StillCaptureError(
            "Resolve declined to grab a still at the playhead (it returned "
            f"{still!r}). It grabs "
            "from the current video clip, so the playhead has to be over "
            "one."
        )
    scratch = Path(tempfile.mkdtemp(prefix="vep_still_"))
    try:
        exported = album.ExportStills([still], str(scratch), "frame", STILL_FORMAT)
        if not exported:
            # The return is authoritative on failure even though it is
            # not evidence of success: on 2026-09-10 this route returned
            # False and wrote nothing (Resolve Studio 21.0.0b), and the
            # lane that trusted the disk alone reported the picture area
            # as a measurement.  A False return raises even if a file
            # happens to be there - it is not this call's file.
            raise StillCaptureError(
                f"ExportStills returned {exported!r}: the still was not "
                "exported, so there is no picture to measure."
            )
        names = sorted(p.name for p in scratch.iterdir())
        pngs = sorted(scratch.glob(f"*.{STILL_FORMAT}"))
        if not pngs:
            # ExportStills returned something; the disk is the authority.
            raise StillCaptureError(
                f"ExportStills returned {exported!r} but wrote no "
                f"{STILL_FORMAT} into {scratch}: {names or 'nothing at all'}."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pngs[0]), str(destination))
        discarded = [n for n in names if n != pngs[0].name]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        # Judged on the COUNT, not on the return (§5): the count is the
        # thing the captain would have to tidy. The return is only used
        # to decide whether another album is worth asking.
        if not album.DeleteStills([still]):
            for other in gallery.GetGalleryStillAlbums() or []:
                if other is not album and other.DeleteStills([still]):
                    break
        after = len(album.GetStills() or [])

    if destination.stat().st_size <= 0:
        raise StillCaptureError(f"The exported still {destination} is empty.")
    result = StillResult(
        path=destination, gallery_album=album_name,
        stills_before=before, stills_after=after,
        exported_names=names, discarded=discarded,
        surface=CAPTURED_SURFACE,
    )
    # The surface is asserted, not assumed: if this route is ever pointed
    # at a viewer instead of the gallery, the capture fails here rather
    # than handing a lane a preview to grade against.
    resolve_surfaces.assert_measurable(result.surface)
    return result


def unused_path(path: Path) -> Path:
    """`path`, or the next free `name-2.png` beside it.

    Never overwrite.  The stamp in a still's name has one-second
    resolution, so two clicks on the same frame inside one second would
    otherwise land on the same file and the first capture would be gone -
    the same collision `marker_feedback.pull` guards its record files
    against, and the same reason: nothing here is reproducible.
    """
    if not path.exists():
        return path
    serial = 1
    while True:
        serial += 1
        candidate = path.with_name(f"{path.stem}-{serial}{path.suffix}")
        if not candidate.exists():
            return candidate


def still_filename(timeline_name: str, playhead: Playhead) -> str:
    safe = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in (timeline_name or "")
    ) or "timeline"
    stamp = marker_payload.stamp()
    return f"{safe}.f{playhead.absolute_frame:06d}.{stamp}.{STILL_FORMAT}"


# ── The marker ──────────────────────────────────────────────────────


def marker_spanning(timeline, key: int):
    """(start_key, marker) for the marker covering `key`, or (None, None).

    A marker with a duration covers a RANGE, and `UpdateMarkerCustomData`
    only accepts its start frame, so the playhead landing anywhere inside
    one has to resolve to that start rather than making a second marker
    on top of the captain's.
    """
    markers = timeline.GetMarkers() or {}
    best = (None, None)
    for raw_key, marker in markers.items():
        start = int(raw_key)
        span = max(1, int(marker.get("duration") or 1))
        if start <= key < start + span:
            # The nearest enclosing start wins if two overlap.
            if best[0] is None or start > best[0]:
                best = (start, marker)
    return best


@dataclass
class CaptureResult:
    project_folder: Path
    timeline_name: str
    playhead: Playhead
    still: StillResult
    still_relpath: str
    marker_key: int
    marker_created: bool
    marker_name: str
    marker_note: str
    record_id: str
    envelope_id: str
    custom_data: str
    gallery_note: str = ""
    decisions: list = field(default_factory=list)
    """The decision behind everything playing at this frame, stamped onto
    the marker beside the still.  Empty when the project has no decision
    ledger, or when nothing is placed there - never a guess."""

    def summary(self) -> str:
        what = "created a marker" if self.marker_created else "updated the marker"
        typed = " / ".join(
            part for part in (self.marker_name, self.marker_note) if part
        ) or "(nothing typed yet)"
        decided = "\n".join(f"        {d}" for d in self.decisions)
        return (
            f"{self.playhead.timecode}  frame {self.playhead.absolute_frame}\n"
            f"{what} on {self.timeline_name}\n"
            f"typed:  {typed}\n"
            f"still:  {self.still.path}\n"
            f"marker: {self.record_id}"
            + (f"\nplaying:\n{decided}" if self.decisions else "")
        )


@under_lease("capture a frame for firstmate", prefer=True)
def capture(timeline, project, project_folder=None) -> CaptureResult:
    """The whole button: still to disk, record onto the marker there.

    Takes the Resolve instance if it is free and goes ahead if it is
    not (`resolve_lock.prefer_lease`). The gallery and the playhead are
    global state, so this is a writer - but it is the CAPTAIN's writer,
    pressed by hand, and a button that queued behind a fifteen-minute
    reel build would be the workflow designed out rather than served.
    An agent holding the instance learns about the collision through
    its own cursor fence, which is what that fence is for.
    """
    playhead = read_playhead(timeline)
    folder = Path(project_folder) if project_folder else \
        project_folder_from_timeline(timeline)
    if folder is None:
        raise CaptureError(
            "Could not tell which pipeline project this timeline belongs "
            "to: no clip on it sits under a folder containing "
            f"{PROJECT_MARKER_FILE}. The still has nowhere durable to go, "
            "so nothing was written."
        )
    layout = ProjectLayout(folder)
    destination = unused_path(layout.write_path(
        Area.MARKER_FEEDBACK, STILLS_SUBDIR,
        still_filename(timeline.GetName(), playhead),
    ))
    still = grab_still(timeline, project, destination)
    relpath = str(destination.relative_to(layout.root))

    start_key, existing = marker_spanning(timeline, playhead.marker_key)
    envelope = marker_payload.parse(
        (existing or {}).get("customData") or ""
    )
    record = {
        "kind": marker_payload.KIND_STILL,
        "writer": WRITER,
        "writer_version": WRITER_VERSION,
        "id": marker_payload.new_id("still"),
        "at": marker_payload.utc_now(),
        "path": relpath,
        "path_absolute": str(destination),
        "media_type": f"image/{STILL_FORMAT}",
        "timeline": timeline.GetName(),
        "timeline_frame": playhead.absolute_frame,
        "marker_key": playhead.marker_key,
        "timecode": playhead.timecode,
        "fps": playhead.fps,
        "source": "resolve_grab_still",
        "carries": "graded_and_conformed",
    }
    marker_payload.merge_record(envelope, record)

    # WHAT DECIDED THE PICTURE, beside the picture. The still says what
    # the captain was looking at; this says which step's decision put it
    # there, so the note they are about to type reaches that step without
    # anything having to read their prose (AGENTS.md 15). Read off the
    # ledger step 6.01 wrote; a project with none is stamped with nothing
    # rather than with a guess.
    decisions = timeline_decisions.placements_at_frame(
        timeline_decisions.read_ledger(layout.root), playhead.marker_key)
    timeline_decisions.stamp_envelope(envelope, decisions, timeline.GetName())

    payload = marker_payload.dumps(envelope)

    if existing is None:
        landed = timeline.AddMarker(
            playhead.marker_key, CREATED_MARKER_COLOR, CREATED_MARKER_NAME,
            "", 1, payload,
        )
        if not landed:
            raise CaptureError(
                f"Resolve refused to add a marker at frame "
                f"{playhead.marker_key} (it returned False). The still is "
                f"on disk at {destination}, but nothing points at it."
            )
        key, name, note, created = playhead.marker_key, CREATED_MARKER_NAME, "", True
    else:
        landed = timeline.UpdateMarkerCustomData(int(start_key), payload)
        if not landed:
            raise CaptureError(
                f"Resolve refused to update the marker at frame "
                f"{start_key} (it returned False). The still is on disk at "
                f"{destination}, but nothing points at it."
            )
        key = int(start_key)
        name = existing.get("name") or ""
        note = existing.get("note") or ""
        created = False

    gallery_note = ""
    if still.stills_after != still.stills_before:
        gallery_note = (
            f"gallery album {still.gallery_album!r} went from "
            f"{still.stills_before} to {still.stills_after} stills - the "
            f"grabbed still was not removed"
        )
    return CaptureResult(
        project_folder=layout.root, timeline_name=timeline.GetName(),
        playhead=playhead, still=still, still_relpath=relpath,
        marker_key=key, marker_created=created, marker_name=name,
        marker_note=note, record_id=record["id"],
        envelope_id=envelope["id"], custom_data=payload,
        gallery_note=gallery_note,
        decisions=[
            f"{d.get('track')} {d.get('label')} <- {d.get('step')}"
            for d in decisions
        ],
    )
