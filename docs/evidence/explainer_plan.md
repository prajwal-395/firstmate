# `library.tools.explainer_plan` - the history behind its contract

This is the module docstring of `library/tools/explainer_plan.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
An animated explainer, and the one field that would make it automatic.

The captain named four capabilities; this is the fourth.  Their words:

    "there is a lot we can potentially do with it in terms of creating
     motion graphics and maybe even whole full screen animations and
     chroma key transitions and animated explainer videos which are all
     capabilities not flushed out for the pipeline."

The phrase covers three different things, and building the wrong one
wastes the work, so §1 settles which one this is before anything else.


1. What an animated explainer is HERE
=====================================
Three readings were on the table:

(a) **A reel that is ENTIRELY animation**, no talking heads, narrated
    over the hosts' audio.
(b) **A sustained animated SEQUENCE inside an otherwise normal reel**,
    explaining a concept the speech introduces.
(c) **A diagram that BUILDS across several beats** rather than one card
    appearing.

**(b) and (c) are one thing and this module is it.**  (c) describes the
internal structure - stages revealed one at a time - and (b) describes
where it sits - over picture that keeps playing, while the speech goes
on.  Neither is a capability without the other: a build whose stages are
not timed to the words is a card that animates for its own sake, and a
sequence over speech that arrives whole is a card.  So:

    **An animated explainer is a STAGED graphic whose stages are
    revealed at the reel seconds where the speech says them.**

The vocabulary already agrees, and it was written before this module.
`motion_graphics_vocabulary`'s `list_build` says its refusal in the same
words from the other side: *"Where the items are not timed to the words.
A set that appears all at once is a quote_card."*  What makes an
explainer an explainer is the anchoring, and the anchoring is what
nothing had.

**(a) is the same mechanism with a taste input the engine cannot
supply, and that is why it is not built.**  Mechanically an all-animation
reel is this, with the span equal to the whole reel and the graphic
opaque - one declaration, no new code.  What it needs and nobody has is
sixty seconds of authored artwork: a ground, a palette, a motion
language and a drawing for every beat.  AGENTS.md 14 puts artwork in the
project and AGENTS.md 10.5 forbids the engine stating taste, so the
engine can carry an all-animation reel and cannot author one.  That is a
statement about who draws it, not about whether it is reachable; a
project that owns the artwork declares a full-frame element for the span
(`docs/FULL_FRAME_ELEMENTS.md`) and gets it.


2. What an explainer needs, and what the pipeline produces
==========================================================
An explainer needs three things: a CLAIM, its PARTS, and an ORDER to
reveal them in.  Measured against what the pipeline really writes down,
2026-09-07:

=========================  ==========  ==============================
Needed                     Produced?   Where
=========================  ==========  ==============================
the claim                  **yes**     `reel_judgement.readings[].claim`,
                                       with `claim_quote` grounded in the
                                       reel's own words
its parts, enumerated      **no**      the claim is ONE SENTENCE of
                                       prose.  Nothing splits it.
when each part is said     **derived** the judge is already handed
                                       `lines` with `at` in REEL
                                       seconds; a part carrying its own
                                       quote is anchored by SEARCH
=========================  ==========  ==============================

So the gap is **one field**, and it is smaller than it looks.  Row three
is not a second gap: it is arithmetic over row two.  Give a part the
words where it is said and the reel second falls out of a search over
`lines`, which is the discipline AGENTS.md 6 already applies to a
passage (*"A passage is anchored by SEARCH, never by the occurrence
nearest the hint"*) and the discipline `reel_quality_bar` already
applies to `claim_quote` and every `assumes_known[].quote`.

That field is `claim_parts`, added to step 3.05's ask -
`reel_quality_bar.READING_FIELDS` - as an OPTIONAL, `contains`-grounded,
ordered list.  Optional for the same reason `takeaway_quote` is: *"this
claim has no parts"* is a real answer and the common one.  A claim that
is one indivisible statement gets no explainer, and that is correct.

**What was NOT enough, and why the cheap version was refused.**  Three
things already on disk look like parts and are not:

- `assumes_known` is a grounded, ordered list with quotes - and it is
  what the reel does NOT explain.  Drawing it would caption the reel's
  own gaps.
- the reel's SENTENCES are ordered and timed.  Staging them is refused
  by the vocabulary itself: *"For items the speech does not actually
  enumerate.  A list imposes a structure, and imposing one the speaker
  did not use misreads them."*
- `claim` is one sentence.  Splitting prose on "and" / a semicolon is an
  engine inventing a structure, which is the whole of AGENTS.md 10.5.


3. The route, which is not a third one
======================================
Two lanes settled the architecture this rides on and neither is
re-derived here.

`docs/FULL_FRAME_ELEMENTS.md` settled the renderer boundary: **picture
that has to be CREATED is Remotion's; a treatment of picture that EXISTS
is Fusion's.**  An explainer is created picture, so it is Remotion's.

`docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` settled the shape of an
element laid over a reel: **additive, on its own video track, timing
untouched either side**, with the plan RECORDED by the build and graded
against what was recorded rather than re-derived.

Between them there is nothing left for this module to invent about
rendering, and it invents nothing:

    reel_judgement.readings[].claim_parts   the model's grounded parts
      -> explainer_plan.anchor_stages       part -> reel second, BY SEARCH
      -> explainer_plan.plan_entries        stages -> plan entries
      -> motion_graphics_plan.resolve_plan  the EXISTING resolver
      -> motion_graphics_plan.plan_segments the EXISTING clusterer
      -> motion_graphics.render_segment     step 4.06's OWN renderer
      -> reel_build.build_reel_timeline     placed on the explainer track
      -> reel_conformance_verifier          F21, failing both ways

Every arrow after the second is code that already existed.  This module
owns the first two and the enumeration around them.

**No new element draws.**  The elements an explainer is made of are
`motion_graphics_vocabulary`'s own, and the three an explainer can be
built from - `list_build`, `comparison_bars`, `step_counter` - are
already `reachable_now` and already drawn by `MotionGraphics/index.tsx`.
A fourth entry that drew nothing would be the defect class this project
has spent a week removing.  What was missing was never the drawing.

**What WAS missing, twice over.**  The reels process is two nodes,
`build_reels` and `verify_reels`, and neither had any route to the
motion-graphics layer at all: `motion_graphics` appears nowhere in
`reel_build.py`, so no reel has ever carried a graphic of any kind.  And
`motion_graphics_plan` times an entry from `start_seconds` the MODEL
states freely, with nothing anchoring it to a word.  So even on the
master, a build timed to the speech was not expressible.


4. What this module refuses
===========================
A gate that cannot fail is worse than no gate (AGENTS.md 10.4), so the
refusals are the substance and they are all reachable from a real
declaration:

- a stage whose quote is not in the reel's own words - `UNGROUNDED`
- two stages the reel says in a different order than the plan lists -
  `OUT_OF_ORDER`.  An explainer that reveals part three before part one
  misreads the speech it is drawn over.
- a stage anchored past the end of the reel - `OFF_THE_END`
- a declared element the roster does not mark drawable - `NOT_DRAWABLE`,
  by name, never dropped quietly
- an explainer with no stages left - `NOTHING_TO_DRAW` (AGENTS.md 10.2:
  an overlay that draws nothing is not rendered)
- a malformed declaration - raised by name rather than dropped

And it states no taste.  No colour, no typeface, no element, no anchor,
no duration, no hold, no stage count and no default motion character:
every one of them is the declaration's, and a declaration missing one
REFUSES rather than being completed from a constant.


5. Where an explainer sits, and the measurement that was owed
=============================================================
`docs/FULL_FRAME_ELEMENTS.md` §7 recorded owed work and named its owner:

    "What is missing is a picture-area enumeration ... That is the owed
     work, and it belongs to whoever owns the overlay layer, not here."

`timed_text_overlay` records the same absence from the other side, and
works around it by making every moment state its own `y`.  An explainer
is an overlay and this is that enumeration: :func:`picture_bands`.

The measurement it makes is exact.  A reel delivers its source fitted
into the frame (`reel_framing.delivered_picture`), and on the field-test
project that is 1080x607 of a 1080x1920 delivery - **31.6% of the
frame**, the rest black.  The platform's keep-clear insets are
`safe_area`.  Intersect them and a frame has THREE bands an overlay can
sit in, not one:

    above    inside the safe area, above the picture   no picture under it
    over     inside the safe area, on the picture      picture under it
    below    inside the safe area, below the picture   no picture under it

**It measures and reports; it does not choose.**  Which band an
explainer sits in is the declaration's, and an explainer over the
picture is not refused - a graphic over the shot is a legitimate gesture
and refusing it would be a gate that fails correct output (AGENTS.md
10.4).  What the run says is what the declaration bought: how much
picture it covers and how many caption seconds it draws over, the same
report `transition_overlay.captions_covered` makes for the same reason.

This is also the answer to the question the lane opened with - whether
an explainer competes with the speech underneath it.  On this project it
need not: there are 536 rows of dead frame above the picture strip and
336 below it, inside the safe area, carrying nothing.

`tests/unit/captions/test_explainer_plan.py`.
```
