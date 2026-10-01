# `library.tools.logo_bulb` - the history behind its contract

This is the module docstring of `library/tools/logo_bulb.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
logo_bulb.py - the closing animation: navy, arrival, one flash, black.

The captain, 2026-09-17, on a blue ``feedback`` marker placed on
``logo_reveal_23976.mov`` in Reel 13, after watching the rebuilt reels:

    *"this ending animation is not what i had in mind. i wanted to have
    like a darker color like the lucie dark blue or just like black, and
    then for the logo to be animated in and when it is animated in
    completely it glows in a quick soft flash like a light bulb since
    that is what the logo is made to resemble, and then the animation of
    the logo just fades out to nothing"*

    *"and when this animtation is fixed, it is something that applies to
    all of the reels we have already built and will be building"*

Shown the result on the Lucie navy and on black and asked which ships,
he chose neither and answered a third way:

    *"niether really, just scrap the old versions, i like the glow of
    the new version but i want it applied to the Lucie Navy and then
    fade to black to end out the animation at the end"*

That is FIVE beats, in order, and this module is those five beats:

===  ===================================================================
1    a DARK GROUND - a deliberate dark field, not transparency over
     whatever the reel happens to end on. On Lucie it is the navy, and
     it is now a beat with a beginning and an END
2    the mark ARRIVES - the delivered animation's own draw-on, untouched
3    ONE flash AT COMPLETION - a bulb switching on, not a hold
4    mark and light FADE OUT TO NOTHING
5    the GROUND GOES WITH THEM - the field travels to black on the same
     slope, so the animation ends on a frame that is black and holds
     nothing
===  ===================================================================

Beat 5 is the second ruling, and it is one gesture rather than two:
:func:`picture_fade` is the curve, and the ground, the mark and the
light all ride it. There is no separate ground timing to tune, and no
second slope to get wrong.

And the base has to stay a base
-------------------------------
He attached a picture with that ruling: the bulb on a warm amber field
with its dark navy SCREW BASE clearly visible - not the brand lockup,
which is an orange C with rays on transparency and no base at all. On
the navy ground put to him the base was 2.4 dE off the field, which is
the just-noticeable difference, so the only object left was the glowing
filament and a bulb with no base stops reading as a bulb - against his
own stated reason for wanting a flash, *"like a light bulb since that
is what the logo is made to resemble"*.

**The amber field and the pale mark in that picture are NOT read as a
colour instruction.** He said Lucie Navy in words and words beat a
picture. What is read from it is that the base must stay readable, and
:data:`SEPARATION_FLOOR` is that requirement as a number:
:data:`FIELD_LIFT` lifts the declared ground's own VALUE behind the
lockup until the base clears it. The mark's colour and the declared
navy are both untouched - a lift is not a different blue, and it is
exactly zero on a black ground, where a navy base already reads.

What this supersedes, and why that is not a contradiction
---------------------------------------------------------
Earlier the same day the captain was asked, in a survey against a
side-by-side over a dark ground, whether to promote the relit logo
(:mod:`library.tools.logo_relight`, PR 1191). He answered *"Promote the
relit one"*, with core light *"A little, as built"*. He then saw it
inside a reel and asked for something different.

**The newer ruling stands, and it supersedes that approval.** It is a
revision on better information - a side-by-side shows you a light, a reel
shows you an ending - and no later lane should restore the promoted
relight's envelope on the strength of the survey. What it supersedes is
narrow and worth stating exactly:

- **superseded**: ``logo_relight.intensity_envelope`` - surge on growth,
  release, then HOLD at :data:`~library.tools.logo_relight.GLOW_GAIN`
  for as long as the mark is up. The captain saw a sustained glow and
  asked for a flash.
- **superseded**: transparency as the ground. The delivered asset
  carries alpha and is composited over the reel's last frames; he asked
  for a dark field.
- **superseded**: the delivered tail, which stops at 0.164 alpha rather
  than reaching zero. He asked for "fades out to nothing", and the
  delivered animation never gets there.
- **KEPT, untouched**: every measurement in ``logo_relight`` about how
  light behaves - the ink/halo split, the navy base not emitting, the
  four fitted falloff octaves, additive compositing, the two
  temperatures, the 16-bit pipe and the dither. That work was right and
  this module imports it rather than restating it.
- **KEPT, and not re-opened**: ``CORE_LIGHT`` at 0.05. He accepted a
  little light landing on the mark itself, as built.
- **KEPT**: the 1080x1920 frame, the 23.976 conform (PR 1181), the
  mark's shape, its choreography, its colour and the 72-frame length.
  Every reel already built has a 3.003s slot for this asset, so the new
  one is a straight swap.

The envelope, and the tension in "quick soft flash"
---------------------------------------------------
"Quick" and "soft" pull against each other: a quick flash wants a short
attack, and a short attack is what makes a hard edge. Splitting the
difference gives a medium flash that is neither. **A fast rise with a
slower release reads as both** - that is what a filament actually does,
and it is what this module renders:

- the rise is :data:`ATTACK_SECONDS` long and it is a smoothstep, so it
  leaves zero and reaches the peak with zero slope at both ends. Quick,
  with no edge at either end of the rise.
- the peak lands ON the completion frame, measured
  (:func:`completion_index`), never keyframed.
- the release is exponential, reaching a tenth in
  :data:`RELEASE_SECONDS` - 2.4x the rise. That is what a hot thing
  cooling does, and it is the half that carries "soft".

Total event: 0.17s up, 0.40s down, inside a 3.003s animation.

Between flashes the light does not go to zero - it sits at
:data:`BASE_LIGHT`, a fifth of the level the captain rejected as a hold.
A filament being drawn on carries some light; at zero the mark would be
flat paint for two of its three seconds. The flash is
:data:`FLASH_LIGHT`, six times the base, and that ratio is the event.

The ground
----------
**The ground is a parameter, never a colour this engine states.**
AGENTS.md 14: the engine is series-neutral and ships no artwork and no
brand colour. The default is BLACK, which is not taste - it is the
absence of a declared ground (AGENTS.md 10.5), and the captain named it
as his own second choice.

The project's ground comes from the project's brand template, by
:func:`ground_from_brand_template`, which reads
``content.bookends.end_card.props.bgColor`` - the project's own navy.
That is the right key rather than a near one: it is the ground the
project's own end card sits on, and this is the other bookend of the
same brand. The template's ``style.color_palette`` carries the same
value as one of its entries. The artwork's own screw base sat a few
steps from the declared ground on the asset this was solved against;
it is a colour inside the mark, not a field to put the mark on.

The fade
--------
**This module CARRIES the delivered fade to zero. It does not author
one.** It carries the GROUND on the same curve (beat 5). The delivered animation's tail is a linear ramp that stops at
0.164 of full alpha and then cuts; :func:`fade_scale` re-maps that ramp
so the same slope reaches exactly zero, which is the captain's own
pacing finished rather than a curve this module chose. A source whose
tail does not actually descend is REFUSED
(:data:`TAIL_CEILING`, :class:`SourceNotClosed`) rather than being given
an ending the engine made up.

 Looking at it
 -------------
 The last version of this asset was judged from an isolated side-by-side
 and then rejected on sight in a reel. So this module renders both, and
 :func:`over_tail` is the one that matters: the real closing frames of a
 real reel, then this animation where it will sit.

      python3 -m library.tools.logo_bulb --source <in.mov> --out <out.mov>
          [--ground-from-template <project>/brand.json]
     python3 -m library.tools.logo_bulb --source <in.mov> \
         --contact-sheet <sheet.png>
     python3 -m library.tools.logo_bulb --source <in.mov> \
         --in-reel <reel.mp4> <out.mp4> --keep 40.5 43.42

 ``tests/test_logo_bulb.py``.

 The two-line variant
 --------------------
  The captain, 2026-09-21, on the animation above: keep it exactly as it
  is, and make a second version carrying two lines of type at the bottom -
  a sentence and a URL - so both versions are at his disposal. LENGTH
  stays 72 frames and LINES stays two (his ruling, same minute).

 Everything listed as unchanged in the spec above is unchanged: the two
 lines are the ONLY difference, and an empty :class:`ClosingText`
 renders today's animation pixel for pixel (pinned in the tests, not
 claimed here).

  Where the lines come from: :func:`lines_from_brand_template` reads
  ``content.closing_lockup`` - lines plus the colour they are set in -
  from the same brand template the ground already comes from. It is a new
  key, not ``end_card.props``: that headline and tagline belong to the
  end-card composition and are stale relative to the project's current
  lockup, so reading them here would silently couple two surfaces. A
  template declaring no ``closing_lockup`` gets no type, which is the
  "including none" half of the rule that the engine renders whatever
  text it is handed.

 When the type arrives, and why it needs no timing of its own: after
 the mark lands there are ten frames before the fade starts, and two
 lines cannot be introduced in that gap and read. So the type is part
 of the ground - up with the cut on frame 0, holding through the
 flash, and leaving on beat 5's own fade (:func:`picture_fade`), the
 same numbers the mark and the field ride. There is no second arrival
 to tune and no second slope to get wrong, for the same reason beat 5
 is one gesture.

  What is measured, as numbers: :func:`text_contrast_report` reads the
  type's separation from the ground at its worst full-presence frame
  against :data:`TEXT_CONTRAST_FLOOR`, and :func:`describe` reports how
  long the type stands at full legibility against
  :data:`READ_TIME_FLOOR_SECONDS` and how long the full lockup - mark
  complete AND type full - holds. Both are REPORTED, not gated, the
  same way :func:`separation_report` reports against
  :data:`SEPARATION_FLOOR`: the captain judges the render, and the
  numbers are what he judges it with.

  The animated variant
  --------------------
  The captain, 2026-09-24: the two bottom lines should animate in and
  out rather than sit static. Same 72 frames, same mark, same flash,
  same navy, same fade to black, same colours, font and copy - the
  lines' entrance and exit are the ONLY difference, and a
  ``closing_lockup`` declaring no ``motion`` renders the static
  version pixel for pixel, so both stay reproducible from one module.

  The motion is declared data on the lockup
  (:class:`ClosingTextMotion`, read by
  :func:`motion_from_declaration`): which gesture (``style``), each
  line's entrance window, the shared exit window, and how far a line
  travels below its seat arriving and leaving. The engine interprets
  it - per-frame presence and travel (:func:`text_motion_state`),
  drawn through the same rasterizer as the static layer - and refuses
  a half-declared or out-of-range motion rather than completing or
  clipping one. Full legibility (:func:`text_full_frames`) is the
  envelope's own numbers then, not beat 5's.

  The safe-zone placement
  ----------------------
  The captain, 2026-09-26: keep one line above the logo and put the URL
  below it. The rendered two-bottom-line lockup intrudes on the project
  policy's action rails, side crops and bottom caption region during its
  motion. The same copy, Montserrat Bold face and weight, colour and
  `rise` timing stay; the sentence takes the top seat and the URL the
  lower seat. The sentence is 50px so its rendered width clears the
  device-agnostic side crop under the all-platform policy.
  
```
