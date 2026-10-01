# `library.tools.marker_capture` - the history behind its contract

This is the module docstring of `library/tools/marker_capture.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Capture the frame the captain is looking at, onto the marker there.

§15 gave the captain a way to talk to the pipeline: type a note onto a
Resolve timeline marker, and `marker_feedback.py` collects it.  It worked
immediately and immediately showed its one gap - their first note landed
on a frame with FOUR clips stacked under it, and the reader could only
list all four.  A picture of the frame removes the guess.

This module is the writing half.  It reads the playhead, grabs the still
Resolve is showing, exports it as
a PNG into `<project>/marker_feedback/stills/`, and writes a
`marker_payload` record into the marker's `customData` - creating the
marker if there is none there, and updating it in place if there is, in
both cases without touching a character of what the captain typed.

Nothing in the pipeline writes into the
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
  `verify_treatment`, `window_frames`,
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
```
