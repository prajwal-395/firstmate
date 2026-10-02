# `library.tools.transition_overlay` - the history behind its contract

This is the module docstring of `library/tools/transition_overlay.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
A transition carried by an ELEMENT laid over the cut, and why nothing is keyed.

The captain named "chroma key transitions" as a capability the pipeline
should have.  Read plainly that is the classic form: a graphic or a piece
of footage shot on green, keyed out, sweeping across frame to hide a cut.
In short form it is the wipes, sweeps and shape transitions that carry a
cut without a hard jump.

This module is the mechanism.  It carries no keyer, and the rest of this
docstring is why that is the right answer rather than a shortcut.

Nothing is keyed, because nothing needs keying
----------------------------------------------
Measured 2026-09-07, over every asset library and every project on this
machine:

- **There is no green-screen source to key.**  The only motion elements
  any project owns are ``transition_bumper.mov`` and ``logo_reveal.mov``
  in the Lucie brand assets, and both are already ProRes 4444
  (``yuva444p12le``) - an ALPHA CHANNEL, authored, 1080x1920.  Nobody
  keyed them and nobody would.  They were rendered from the project's own
  Remotion compositions, which is the route the engine already runs for
  every caption (step 4.05 emits an alpha artefact - see
  ``library/tools/overlay_carriage.py``) and every motion
  graphic (4.06).  **An element that is born with alpha never needs
  keying at all.**
- **A keyer cannot recover an authored alpha, even in its best case.**
  The bumper's own frame 22 was flattened onto perfectly uniform
  ``0x00B140`` - no spill, no lighting variation, no compression, which
  is a cleaner plate than any real shoot produces - and keyed back with
  ``ffmpeg chromakey`` across the whole similarity range.  Against the
  authored alpha (31,871 ink pixels of 2,073,600):

      similarity   background left opaque   holes punched in the element
      0.01              2,041,729                        0
      0.05                  1,054                   10,426
      0.10                      2                   20,980
      0.20                      0                   24,729
      0.30                      0                   30,501

  There is no setting that returns the element.  Every one of them either
  leaves the plate or eats the artwork, and the soft glow the design
  actually carries is destroyed at every setting.  ``docs/CHROMA_KEY_TRANSITIONS_MEASURED.md``
  has the pictures.
- **Choosing between those rows is taste with no producer here.**  A key
  colour, a similarity and a blend are three numbers nobody in this
  pipeline is asked for, and ``if similarity < 0.x`` is precisely the
  invented threshold AGENTS.md 10.5 and ``tests/contracts/test_no_creative_floors.py``
  exist to keep out.  An authored alpha needs none of them: the softness
  is drawn, not derived.

So the engine REQUIRES alpha and does not key.  An element whose picture
carries no alpha plane is refused by name, with that reasoning, rather
than silently composited as an opaque rectangle - see
:func:`measure_element` and :data:`ALPHA_IS_REQUIRED_NOT_KEYED`.

Why an OVERLAY is buildable where a wipe is not
-----------------------------------------------
``transition_vocabulary.WITHDRAWN`` withdrew ``cross_dissolve``, ``wipe``
and ``whip_pan`` for one reason, and it is still true: a per-clip Fusion
comp sees only its own clip, so nothing on that route can MIX the
outgoing and incoming pictures.

An element laid over the cut does not mix them.  It HIDES the cut - it is
an additive overlay on its own track, and it reads neither neighbour.
That is the whole reason this capability exists when a wipe does not, and
it is why the mechanism belongs here rather than in
``library/tools/fusion/``.

The same distinction is already drawn in
``motion_graphics_vocabulary.OUT_OF_VOCABULARY``, which sends
``shape_wipe_transition`` to ``transition_vocabulary`` and notes the
per-clip limitation.  This module is the answer that entry was waiting
for, and ``transition_vocabulary.OVERLAY_TYPES`` is where it is named, so
there is still one enumeration of transition types and not two.

An overlay is ADDITIVE: it changes no duration
----------------------------------------------
:data:`TIMING_IS_ADDITIVE` is the ruling and this is the reasoning.

A transition occupies time, and the question is whose.  Three answers
were available and two of them cost something the captain measures:

1. **Consume frames from the shots either side.**  The reel gets shorter,
   which is a change to a delivered quality.  Worse, a reel is its
   ``keep_ranges`` laid end to end (``reel_build.reel_time``), so eating
   frames at a seam moves every caption after it AND changes every later
   block's ``timeline_start`` - which is one of the five fields
   ``plan_provenance.footage_binding_hash`` digests.  Every caption on
   the reel would have to be re-planned and re-rendered to stay bound to
   its footage.
2. **Extend the reel.**  Picture and sound come off the same ranges, so
   inserting picture frames the audio does not have desynchronises
   everything downstream of the seam.
3. **Lay the element OVER the cut.**  The hard cut underneath stays on
   exactly the frame it was on.  Duration is unchanged, ``keep_ranges``
   is unchanged, every caption's timing and binding is unchanged, and the
   element hides the jump - which is what the gesture is for.

Three is what an editor does with a bumper, and it is the only one of the
three that costs nothing.  ``tests/unit/captions/test_transition_overlay.py`` asserts
the binding hash is byte-identical either side of adding overlays, so the
claim is checked rather than asserted.

What it DOES cost is stated rather than assumed: an element over the cut
draws over whatever else is on frame, captions included.
:func:`captions_covered` measures which caption cards it covers and for
how long, and the reels conformance verifier reports it.  Whether that is
wanted is the captain's; that it happened is a fact, and a fact with a
reader (AGENTS.md 10.1).

The engine ships no element
---------------------------
An element is ARTWORK - copy or a graphic the viewer reads - so it lives
with the project, not the engine (AGENTS.md 14, ``docs/ASSET_LIBRARY_PLAN.md``).
The declaration is the same two-mode shape ``content.bookends`` uses and
for the same recorded reason::

    # <project>/project.yaml
    effect:
      transition_overlay:
        element:
          asset: brand_assets/transition_bumper.mov   # already rendered
          # -- or --
          composition: TransitionBumper               # rendered by Remotion
          source: compositions/TransitionBumper/index.tsx
          duration_seconds: 1.5
        anchor: centre
        on_cuts: [closer]

The key is ``on_cuts`` and not ``on`` because YAML 1.1 - which PyYAML
implements - parses a bare ``on`` as the BOOLEAN True.  Written as
``on:`` the declaration parses to ``{True: ['closer']}``, the lookup
finds nothing, and the reader is told they declared no seams while
looking straight at the line where they did.  Found by writing the
documented syntax and running it.

Nothing here has a default.  ``anchor`` is REQUIRED and comes from
:data:`ANCHORS` - naming a set is not choosing from it - because where an
element sits relative to the cut is the gesture, and an engine-supplied
default would be taste arriving one level up.  ``duration_seconds`` is
required only in ``composition`` mode, where there is no file to measure;
in ``asset`` mode it is MEASURED off the element and a declared value that
disagrees with the measurement is refused rather than believed.

Which renderer owns a full-frame element
----------------------------------------
Another lane owns the full-screen architecture and had not landed a
``docs/`` ruling when this was written (2026-09-07: no such document, no
open PR).  **This module renders nothing**, which is what makes it
reconcilable with whatever that lane decides: it consumes an element BY
PATH and places it.  ``asset`` mode needs no renderer at all, and
``composition`` mode names the composition and its project-owned source
so it goes wherever bookends go - and until a renderer exists on the
reels side it REFUSES by name rather than placing nothing, because the
reels process is two nodes and neither renders (see
:func:`resolve_element`).  If the full-screen lane names Remotion,
nothing here changes; if it names something else, nothing here changes
either.  Ruling 1 holds: no second path is built beside it.

Reachability
------------
``element_overlay`` is reachable when a declared element MEASURES
drawable, and that is derived rather than declared -
:func:`element_is_reachable` runs the measurement.  A roster entry that
claims reachability it does not have is the defect class this project
spent a week removing, so nothing here claims it.

    python3 -m library.tools.transition_overlay --measure <element.mov>

``tests/unit/captions/test_transition_overlay.py``.
```

## The tests

Moved from the module docstring of `tests/unit/captions/test_transition_overlay.py`.
Every gate there is proved in BOTH directions. A gate that cannot fail is
worse than no gate because it reads as coverage (AGENTS.md 10.4), and this
area has a documented history of exactly that: project 001 rendered 53.8 MB of
motion-graphics ProRes in which `max(alpha)` was 0 on every frame and reported
them delivered. The alpha instrument is validated against three fixtures whose
answers are known before it is pointed at anything real - opaque, empty, and no
alpha plane at all. The empty and the no-alpha cases are DIFFERENT results:
ffmpeg exits non-zero having written no frames when the plane is absent, and a
reader that only looked at the maximum would call that "draws nothing" and give
the wrong diagnosis.
