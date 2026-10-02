# `library.tools.logo_relight` - the history behind its contract

This is the module docstring of `library/tools/logo_relight.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
logo_relight.py - the end-logo animation, lit rather than thickened.

WHAT IS STILL IN FORCE HERE, AND WHAT IS NOT
--------------------------------------------
The captain approved this relight on 2026-09-17 from a side-by-side
("Promote the relit one", core light "A little, as built"), watched it
inside a reel LATER THE SAME DAY, and asked for a different ending. The
newer ruling supersedes that approval and is not a contradiction of it -
a side-by-side shows you a light, a reel shows you an ending.

- **The PHYSICS below stand.** The ink/halo split, the navy base not
  emitting, the four fitted falloff octaves, additive compositing, the
  two temperatures, ``CORE_LIGHT`` at 0.05, the 16-bit pipe and the
  dither are all still what light does here, and
  :mod:`library.tools.logo_bulb` imports every one of them.
- **The ENVELOPE below is SUPERSEDED**, along with transparency as the
  ground and the delivered tail that stops at 0.164 alpha.
  :func:`intensity_envelope` surges on growth and then HOLDS, and a hold
  is exactly what the captain rejected. Do not restore it on the
  strength of the survey: the asset that ships is the one
  ``logo_bulb`` renders - dark ground, arrival, ONE flash at completion,
  fade to nothing.

The captain, 2026-09-17, on the logo animation that closes every reel:

    *"and also the end logo animation, can you redo it so that it
    actually looks like the logo glows and isn't just a thick blob"*

That is a judgement about how it LOOKS, and the measurements agree with
him. What was delivered (``logo_reveal.mov``, 30fps, and the 23.976
conform beside it) carries its glow BAKED IN, and the bake has four
properties that read as mass:

===========================================================  ===========
measured on the peak frame of ``logo_reveal_23976.mov``      value
===========================================================  ===========
authored halo reaches, at most                               21 px
mean halo alpha 0-2 px out from the ink                      98 / 255
luminance spread across the 13,146 px of orange ink          sd 1.4
halo pixels cast by the DARK navy screw base                 6,947
===========================================================  ===========

A 21 px collar around a mark 465 px wide is not light in the frame - it
is a second, softer stroke drawn behind the first, and at 98/255 where it
meets the ink it is dense enough to read as stroke weight. The ink inside
it is a flat fill: 13,146 pixels spanning 180.3 to 182.1 in luminance,
a filament with no temperature anywhere. And 6,947 of the halo pixels
belong to the navy base, which is the tell - a dark object cannot emit,
so a halo around it says the effect was a blur of the ARTWORK rather than
light from a source.

What this module does about it
------------------------------
It re-lights the delivered mark. It does not re-author it: the mark's
shape, its colours, its choreography, the duration, the frame rate and
the 1080x1920 frame all come through untouched, frame for frame, because
every one of those is something the captain fixed. The only thing
replaced is the light.

1. **Separate the authored ink from the authored halo.**  Alpha is
   bimodal on every frame of both files - a dense population at the
   frame's own maximum and a sparse one below 0.55 of it, with an empty
   trough between (:data:`INK_LO`, :data:`INK_HI` sit in that trough).
   The threshold is RELATIVE to each frame's own alpha maximum, because
   the reveal ramps from 1/255 and the tail fades to 46/255, and a fixed
   cut would erase the mark at both ends. The halo below the trough is
   discarded; the ink above it is carried through unchanged.

2. **Decide what emits.**  Only the orange filament, never the navy base
   (:data:`EMIT_LO`, :data:`EMIT_HI` sit in the empty gap between the
   mark's two luminance populations, 0.19 and 0.71). The base then
   RECEIVES light instead of casting it, which is what puts it in the
   picture as an object rather than a second glowing shape.

3. **Fall off over four octaves, not one.**  :data:`OCTAVES` - sigma 3,
   12, 48 and 190 px - sum to the shape of camera glare rather than the
   shape of a blur: measured on the delivered mark, the relit light is
   129/255 in the 0-2 px band and still 2.7/255 at 256 px, falling by a
   similar factor across every band in between. The delivered collar is
   109/255 at the ink and ZERO by 32 px. One blur can be the hot rim or
   the far wash and is always neither: tight enough to be hot at the rim
   and it has nothing left at 64 px, wide enough to reach and the rim
   and the far field are the same brightness.

4. **Add, never cover.**  The light is composited ADDITIVELY, over the
   mark and over everything under it. That is the difference the word
   "blob" is pointing at: an over-blend occludes, so more of it is more
   material, while an add only ever brightens, so more of it is more
   light. What lands ON the mark is attenuated by the ink's own alpha
   (:data:`CORE_LIGHT`), because a full-strength add clips the C to
   white and the mark's colour is the captain's - so the mark's
   anti-aliased EDGE takes most of it, which is where a lit object is
   brightest anyway.

5. **Run hot at the source and coloured at the edge.**
   :data:`NEAR_COLOUR` is the brand orange taken up its own value ramp
   until it is nearly white; :data:`FAR_COLOUR` is the brand orange
   itself. Light mixes between them on its own intensity. Nothing here
   invents a hue: both ends are the mark's colour, at two temperatures.

6. **Peak and settle, and go out when the mark does.**  A filament
   reaching temperature overshoots and relaxes; a constant multiplier is
   the thing that reads as paint. The envelope is DRIVEN by the
   animation's own measured emission - the frames where new filament
   appears surge (:data:`SURGE_GAIN`) and the surge decays once the mark
   stops growing (:data:`SURGE_RELEASE`) - so it is keyed to this
   animation rather than hand-placed on a timeline, and it re-derives
   itself for any other mark handed to it. It is then scaled by how lit
   the mark is, so the relit tail lands where the delivered tail lands
   and the captain's ending is his.

What is NOT decided here
------------------------
This module has no opinion about where the asset is placed, how long it
runs, what rate it conforms to or what the mark looks like. It reads one
RGBA sequence and writes another of the same length, rate and geometry,
and :func:`relight_file` refuses a source it cannot carry through
unchanged (:class:`SourceNotCarried`).

The artwork stays project data (AGENTS.md 14): nothing of the mark ships
in this repository, and the engine never edits a client's asset in place
- :func:`relight_file` writes a new file and leaves the source alone.

Why the numbers are here and not in ``decided_value``
-----------------------------------------------------
``decided_value`` decides a creative value per RUN, for a step that has a
model, a prompt and a project to read a stated preference from. This is
none of those: it is an asset render run by hand, off no pipeline state,
and wiring a registry slot to it would be machinery around a value that
has exactly one reader. The values below are therefore DECLARED, in one
place, each with the measurement that placed it, and a project that wants
a different light passes a different :class:`LightProfile` rather than
editing a constant.

    python3 -m library.tools.logo_relight --source <in.mov> --out <out.mov>
    python3 -m library.tools.logo_relight --source <in.mov> --contact-sheet <out.png>
    python3 -m library.tools.logo_relight --source <in.mov> --compare <relit.mov> <cmp.mp4>

``tests/test_logo_relight.py``.
```
