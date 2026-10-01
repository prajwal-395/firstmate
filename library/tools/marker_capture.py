"""Capture the frame the captain is looking at, onto the marker there.

This is the writing half of §15 (`marker_feedback.py` reads).  It reads the
playhead, grabs the still Resolve is showing, exports it as a PNG into
`<project>/marker_feedback/stills/`, and writes a `marker_payload` record
into the marker's `customData` - creating the marker if there is none
there, and updating it in place if there is, in both cases without
touching a character of what the captain typed.

Nothing in the pipeline writes into the
captain's application support folder as a side effect of anything.

── The current contract ────────────────────────────────────────────

Every Resolve call is judged by what it RETURNED, never by `hasattr` (§5).
The Resolve behaviours each rule rests on were measured on Resolve Studio
21.0; the measurements are in docs/evidence/marker_capture.md.

* `Timeline.GrabStill()` returns the GRADED, CONFORMED timeline frame -
  the same picture a Deliver render of that frame produces - and the page
  it is taken on does not change the pixels, so the button never changes
  the captain's page.
* `SetCurrentTimecode` moves nothing off the Edit page: a script that
  positions the playhead must `OpenPage("edit")` first and read the
  playhead back.
* A measurement run holds the display awake (`caffeinate -d`): asleep,
  stills come back with upper tracks missing.  The first grab after a
  timeline switch is not evidence - grab, discard, grab again, and judge
  the keeper by counting every ink the artefact file holds at that media
  frame, never one brand colour.
* `ExportStills` writes a `.drx` beside every PNG and suffixes the name
  itself, so the export goes to a scratch directory, the PNG is moved to
  its real name, and the scratch directory is removed.
* A CAPTURE THAT DID NOT HAPPEN RAISES BY NAME.  `grab_still` raises
  `StillCaptureError` (a `CaptureError`) when `GrabStill` declines,
  `ExportStills` returns False, no PNG is on disk, or the PNG is empty.
  A True return is not evidence; the file on disk is.  A caller never
  receives a value it can average from a capture that failed.
* `GrabStill` leaves the still in the current album and Resolve saves the
  project, so the button deletes it again and reports the before/after
  album count, and it never runs unattended on a project a rebuild is
  using.
* A marker key is relative to the timeline's start:
  `frames(current) - frames(GetStartTimecode())`, with the timeline's own
  rate and drop-frame rule.  `SetCurrentTimecode` accepts and reads back
  timecodes the timeline does not have, so `read_playhead` bounds the
  position against `GetStartFrame()`/`GetEndFrame()` and refuses outside
  it - on the POSITION, never on the picture: a black frame inside the
  timeline is a legitimate thing to ask about (§10.2).
* `UpdateMarkerCustomData` replaces only the payload and takes only the
  marker's START frame; `AddMarker` refuses an occupied frame.  So an
  existing marker is ALWAYS updated in place and never replaced.
* An exported still is a fixed size whatever it shows, so §5's "under 2KB
  is a broken frame" rule does not transfer to this route.

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
  `verify_treatment`, `window_frames`,
  step 5.01 `grade._extract_frame`): cheap seeks, no Resolve, but the
  SOURCE frame - ungraded, unconformed, no comps or captions.  Every
  one of these accepts a capture only when the file exists AND is
  non-empty; a zero-byte file reads as a failure, never as a picture.


Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 15
keeps the headline and points here.

Playhead on the moment, capture the frame, and the
frame plus whatever the captain typed is captured. `library/tools/marker_capture.py` is the whole
of it.
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
