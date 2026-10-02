# `library.tools.full_frame_element` - the history behind its contract

This is the module docstring of `library/tools/full_frame_element.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Full-frame elements: the whole frame, for a bounded stretch, in place of picture.

What a full-frame element IS
----------------------------
Everything the overlay layer draws is ADDITIVE: a caption card, a lower
third, a counter, a timed text moment.  It sits over picture and the
picture keeps playing underneath.  ``motion_graphics_vocabulary`` owns
that layer and states the boundary in its own words - *"a full frame of
artwork is a bookend"* - and files ``intro_card`` and ``end_card`` under
:data:`motion_graphics_vocabulary.OUT_OF_VOCABULARY` for exactly this
reason.

This module is the other side of that boundary, for the REELS process.
A full-frame element:

- occupies the WHOLE delivery frame, so there is nothing to composite over;
- holds for a BOUNDED stretch of the reel's own time;
- **replaces picture rather than overlaying it** - it is a picture item on
  the reel's own picture track, not a layer above one.

Why REPLACE and not overlay at full opacity
-------------------------------------------
This was the open question and it is settled here with two measurements,
not a preference.

**1. The reels path cannot silence what plays under an overlay.**
An overlay at full opacity hides the picture and leaves the SOUND.  The
footage under it goes on talking, so the viewer watches a card while a
half-sentence plays.  The only repair is an audio level, and AGENTS.md 5
records the probe's verdict verbatim: *"The scripting API cannot set an
audio level, and that is a COMPLETE enumeration."*
(``library/steps/step_6_01_render/probe_resolve_capabilities.py``).  So
overlay is not a worse answer here, it is an unimplementable one - the
defect would be permanent and unfixable from the reels path.  A card that
occupies its own stretch of reel time cannot run over speech, because
there is no speech at those seconds to run over.

**2. An overlay is invisible to every check this product has.**
``reel_conformance_verifier`` counts picture items on V1 and V2 only
(``check_item_count``), grades delivered framing on V1 and V2 only
(``check_delivered_framing``), and takes the UNION of every picture track
for holes (``check_picture_holes``) - so an opaque card on a track above
V2 adds coverage where coverage already existed and changes no count and
no framing verdict.  It would ship a capability that nothing can see,
which is the defect class this repository has spent a week removing
(AGENTS.md 10.4).  On V1 the card is inside all three, and each of them
had to be taught what it is - see :mod:`library.tools.reel_conformance_verifier`
and ``docs/FULL_FRAME_ELEMENTS.md``.

The third reading - that this is already how the master does it - is the
confirmation rather than the argument.  ``bookends`` turns a declared card
into a V1 spine block *"which is what puts it inside the coverage
assertion and the manifest duration"*.  Reels had no equivalent because
they have no ``mesh_spine``; this is that missing half.

Why Remotion and not Fusion
---------------------------
Also settled with evidence rather than preference, and written up in
``docs/FULL_FRAME_ELEMENTS.md``.  In short: every Fusion route in this
repository is ``TimelineItem.ImportFusionComp(path)``, which attaches a
comp to a clip that already exists - a full-frame element has no carrier
clip to attach to; ``library/tools/fusion/`` emits no text node of any
kind, so it cannot draw type at all; and AGENTS.md 5 forbids creating a
timeline and calling ``ImportFusionComp`` in one process, which is exactly
what ``reel_build.build_reel_timeline`` does.  Remotion already renders
1080x1920 to a FILE, and a file is a media pool item, which is a timeline
clip - the carrier this needs.

A declaration, and no taste
---------------------------
The engine draws what a declaration states and states nothing itself.  No
colour, no typeface, no duration, no motion character and no copy has a
default here, for the reason ``series_look.py`` was emptied (AGENTS.md
10.5).  What a declaration looks like, in a project's own ``project.yaml``
(the PROJECT wins, the same precedence and the same reason as
``timed_text_overlay.resolve_declaration``: copy the viewer reads is
ARTWORK and artwork belongs to the project - AGENTS.md 14)::

    effect:
      full_frame_elements:
        - element: full_frame_card
          placement: head            # head | tail
          duration_seconds: 2.0
          background: "#101014"
          entrance: blur             # a motion character; absent means `cut`
          exit: fade
          font_family: Montserrat
          runs:
            - bind: opening_line     # or: text: "..."
              type_role: display
              colour: "#FFFFFF"

Copy is DECLARED or BOUND, never written here
---------------------------------------------
A run either carries literal ``text`` - the project's own words - or
``bind``s to a fact the pipeline already produces
(:data:`COPY_BINDINGS`).  A binding is a QUOTATION, not an invention: the
opening line is the words the reel itself plays, read back through
``reel_opening.opening_words``.  This module names no producer of copy
beyond that enumeration, and a binding that resolves to nothing REFUSES
rather than drawing an empty run - the same rule
``motion_graphics_plan`` applies to an entry with no readable parameter.

Step 3.04's handoff says titling a reel is *"not yours"* to the model that
chooses it, so nothing in the reels path may author a title.  That is why
there is a binding table and not a title generator.

How a declaration reaches the picture, in order
-----------------------------------------------
1. :func:`resolve_declaration` reads the project's ``project.yaml`` (or
   the brand template's ``effect`` slot) and :func:`declared_elements`
   normalises it, raising on anything malformed.
2. :func:`plan_reel_cards` turns each declaration into planned picture -
   a CARD per declaration, or one SEGMENT per declared window inside a
   keep range for a ``full_frame_span`` - the props, the reel seconds
   it occupies, and the file it will be rendered to - using facts
   measured off the reel itself (:class:`ReelFacts`).
3. :func:`render_reel_cards` renders them all in ONE batch through the
   shared Remotion renderer (``library/tools/remotion_batch.py``), OPAQUE:
   a full-frame element is the picture, so it carries its own ground
   rather than relying on black showing through an alpha channel that
   nothing is beneath.
4. ``reel_build.build_reel_timeline`` places each card on the declared
   card row (`effect.card_row_role`, resolved to a track-plan role row
   - never the first a-roll row by position), and
   ``reel_build.lead_seconds`` shifts every other placement and every
   caption by the head cards' total, so one clock moves together.
5. ``reel_conformance_verifier`` grades the card as the picture item it
   is: inside F1, counted by F4, and exempt from F12 for a stated reason
   rather than by omission.

``tests/unit/captions/test_full_frame_element.py``.
```
