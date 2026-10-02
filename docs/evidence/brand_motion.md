# `library.tools.brand_motion` - the history behind its contract

This is the module docstring of `library/tools/brand_motion.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Brand motion in a Remotion render: the slot, not the choice.

The gap this closes: no Remotion composition reads a video file, so the
captain's real brand motion - ``logo_reveal.mov`` (3.0s) and
``transition_bumper.mov`` (1.5s), both 1080x1920 ProRes 4444 with alpha at
30fps - could be REFERENCED (``transition_overlay`` asset mode,
``content.bookends`` asset mode) but never COMPOSITED into a Remotion
render. This module is the taste-free half of that gap: measure the file,
stage something the renderer can actually read, and build the props. What
it does NOT do is pick a frame-rate conform on the captain's behalf -
motion is their brand language, and the three strategies are named in
:data:`CONFORM_STRATEGIES` with their costs, never chosen here.

Why a mezzanine exists at all
-----------------------------
Measured 2026-09-08 in the same Chrome build Remotion renders with: a
``<video>`` pointed at the bumper's ProRes 4444 ``.mov`` reports
``videoWidth`` 0, ``videoHeight`` 0 and ``canPlayType('video/quicktime')``
``""`` - the container parses (duration reads, the audio clock even
seeks) but there is NO decodable video track, so an ``<OffthreadVideo>``
on the raw file renders nothing, silently. The same page pointed at a
VP9/``yuva420p`` WebM transcode reads 1080x1920 and paints the sparse
artwork with its alpha intact. ``docs/BRAND_MOTION_MEASURED.md`` has the
procedure and the numbers.

So the slot stages a same-rate VP9 WebM mezzanine of the source and the
composition plays THAT. Same rate is what keeps the transcode mechanical:
no frame is created or dropped, every source frame survives 1:1, and the
only change is the codec - which is forced, because VP9-in-WebM is the
only browser-decodable format that carries alpha. The cross-rate question
(30fps asset on a 24000/1001 timeline) is untouched by the transcode and
is answered by the declared conform strategy, below.

The three conform strategies, named and not chosen
--------------------------------------------------
:data:`CONFORM_STRATEGIES` is one enumeration, and :func:`require_conform`
refuses a declaration that names none of them - or names the one that is
not built - by name, with the costs. No default: any default here would
be the engine deciding what the captain's motion feels like.

``native_sample``
    Play the mezzanine in wall-clock time at the composition rate, which
    is what Remotion's ``<OffthreadVideo>`` does: each composition frame
    seeks the source timestamp. Nothing is blended and no authored pixel
    changes. What it costs is stated exactly: sampling 30fps at
    24000/1001 drops every 5th source frame (9 of the bumper's 45, 18 of
    the logo's 90 - a regular stutter-step, max jump 2 source frames).
    Wall-clock duration is exact; cadence is not.

``blended_conform``
    Pre-conform the mezzanine to the composition rate with frame blending
    (``ffmpeg`` ``minterpolate``/``framerate``). No frame is skipped and
    the cadence is smooth - and every output frame is a synthesis that
    softens the authored glow and ghosts fast motion. Which blender and
    how much blend IS the look, so this strategy is named, costed, and
    NOT BUILT: declaring it raises :class:`ConformNotBuilt` carrying the
    parameters nobody has chosen. That refusal is the taste question,
    stated as code.

``resolve_native``
    No Remotion render at all: place the original file on the Resolve
    timeline at its own rate through ``transition_overlay`` asset mode,
    whose placer already does the per-source-fps arithmetic
    (``reel_build``: 36 timeline frames of the bumper are 45 of its own).
    This is the route that already works today, and naming it here is
    what makes the enumeration complete rather than a menu of one.

The fixed length is not a fourth question
-----------------------------------------
Both files have FIXED lengths and the fear was that concatenating one
re-times a reel. It does not, because the slot never places anything:

* as an OVERLAY over a cut the element is additive
  (``transition_overlay.TIMING_IS_ADDITIVE``): the reel keeps its length,
  its keep ranges and every caption binding. The 1.5s bumper covers; it
  does not insert.
* as a CARD at the head or tail it concatenates (``content.bookends`` via
  ``mesh_spine``): the reel absorbs the 3.0s logo by declaration and the
  cursor shifts by exactly that. That is the declared shape, not drift.

Trimming the asset to fit is refused wherever this module is asked:
:func:`brand_motion_props` renders the WHOLE file
(``durationInFrames`` is the measured seconds at the composition rate)
and there is no trim parameter to disagree about - the same refusal
``OverlayDoesNotFit`` and the duration-mismatch check make on the
Resolve route. A reel that needs a shorter sting needs a shorter asset,
which is an authoring decision, not a render flag.

What reaches the composition
----------------------------
``BrandMotion`` (``remotion-subtitles/src/compositions/BrandMotion``) is
an ENGINE composition in the ``channel_bug`` shape: the engine draws and
the project supplies. Props carry the staged ``brand/<file>`` path, the
frame geometry, the measured duration in frames, and ``muted`` - which is
REQUIRED with no default, because both real assets carry an audio stream
and whether brand sound plays is a choice nobody made on the engine's
behalf. Geometry must match the delivery frame exactly: ``contain`` vs
``cover`` on a mismatch is framing taste, so a mismatch is refused
(:class:`GeometryMismatch`) rather than fitted.

    python3 -m library.tools.brand_motion --measure <file.mov>

``tests/unit/captions/test_brand_motion.py``.
```
