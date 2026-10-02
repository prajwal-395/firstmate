# `library.tools.speaker_identity` - the history behind its contract

This is the module docstring of `library/tools/speaker_identity.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Who is speaking, named on screen the first time they appear.

The captain, on a BLUE marker at frame 42 of ``Reel 23 -
why-small-business-wins-on-ai``, 2026-09-12::

    this is a note that i want for all reels, not just this one, but can
    we get like a little label graphic that comes in on the first
    appearance of akshita and craig? ... this should not be words
    animated but like an actual motion graphic.

Four things in that are load-bearing and each one is a rule below.

**"for all reels, not just this one"** - so this is a capability of the
reel build, planned fresh on every reel from the reel's own words, and
never a graphic pinned onto one timeline.  A reel nobody has planned yet
gets one too, which is the same inheritance ``full_frame_elements`` has.

**"on the first appearance"** - once per speaker per reel, on the first
line that speaker really SAYS in that reel.  Derived from
``reel_quality_bar.played_speech``, which is the reel's lines after the
bad takes are cut and in the order the reel plays them - the same list
the caption pass and the conformance verifier read, so a reel's own
words cannot disagree about who spoke first.  Not from the master
timeline, where Craig speaks first in every reel whether the reel keeps
that line or not.

A declared speaker who APPEARS in the reel - the proposal's own cast
list, ``moment.speakers`` - but says no attributed line in it still
gets the card (captain's 2026-09-30 ruling: the card identifies the
person, not the sentence).  The card opens the reel: the first such
speaker at 0.0s and each further one where the previous card's hold
ends, in the proposal's speaker order.  The hold is the project's, the
order is the proposal's and 0.0s is the reel's own start, so no timing
is invented; the existing colour, outside-the-reel, truncation and
readability machinery applies unchanged.  Where no appearance is known
the old skips stand - ``no_lines_in_the_reel`` and
``no_declared_speaker_spoke`` - because a card with no anchor at all
would be taste (AGENTS.md 10.5).

**"not words animated but like an actual motion graphic"** - the
drawing is ``remotion-subtitles/src/compositions/MotionGraphics``'s
``lower_third`` arm, rebuilt as a staged construction (a rule that
draws, type uncovered by a wipe travelling with it, a second line
built beneath).  What this module owns is only WHEN it plays, WHO it
names and WHERE it sits; how it is drawn is the composition's.

**The names and titles are DATA.**  The four strings the captain typed
belong to ONE project and nothing in this file knows any of them - not
in a constant, not in a docstring, not as an example.  The engine serves
a daily channel AND client work (AGENTS.md 14), so a name in library
code is a defect rather than a shortcut.  They are declared at
``effect.speaker_lower_thirds`` in the project's own ``project.yaml``,
the same slot and the same precedence ``effect.timed_text_overlay`` and
``effect.full_frame_elements`` take, and for the same reason: copy the
viewer READS is artwork, and artwork belongs to the project.

**A project that declares none gets none.**  :func:`plan_for_reel`
returns an empty plan with a basis saying WHICH kind of nothing it is,
and the timeline it builds is the timeline it built before this module
existed.  Nothing here has a default name, a default title, a default
colour or a default hold.

Where the colour comes from
---------------------------
There are no house looks (AGENTS.md 12), so the one colour this graphic
draws in is never picked here.  Two declarations answer, in order:

1. the speaker's own ``colour`` under ``effect.speaker_lower_thirds``;
2. failing that, the accent this project ALREADY declares for that
   speaker's captions
   (``pipeline.speaker_subtitle_styles.<speaker>.accentColor``).

Both are the project's.  The second exists because a project that has
already said "Akshita is this pink" should not have to say it twice, and
because the lower third and the caption naming the same speaker
disagreeing about their colour is worse than either choice.  Where
neither answers, the entry is DROPPED with
:data:`NO_COLOUR_DECLARED` - not drawn in something the engine chose.

Everything else the plan states - the anchor, the hold, the entrance and
exit characters - is declared too, and a declaration missing one is
refused by name in :func:`declared_speakers`.  ``resolve_plan`` would
otherwise supply ``cut`` for an unreadable motion character, which is a
default reaching a frame through the back door.

Where it sits
-------------
:func:`placement_box` is the answer, and it is the safe area with its
BOTTOM RAISED so the graphic cannot land on the caption row.

The strictest safe area governs - one master render serves Reels,
TikTok and Shorts - and that is ``safe_area.resolve_safe_area``, read
rather than restated.  The caption row is where the PROJECT put it
(``subtitle_style.project_caption_row``), or the engine's own row where
it declared none, and the cards grow upward from it - so the box's
bottom is that row lifted by the height of the tallest caption card
this reel really rendered.  ``measured_caption_height`` is that
reading and it is a MEASUREMENT off the rendered cards, never a
prediction from the font size.

The graphic renders FULL CANVAS as its probe, and is then BOUND
tightly from its own pixels (`reel_build._bind_lower_third_tight`).
`mg_tight_box` predicts a motion-graphics union from the
composition's literals rather than measuring it, and on this project
it is wrong on every case measured so far - a tight box carries a
Pan/Tilt computed from that prediction, so these graphics never take
the predicted path. Instead the full-canvas render is measured
across every frame and the tight file is cropped out of it: no
re-layout, so the copy cannot re-wrap, and the placement is computed
from the measured union rather than asserted. :func:`render_findings`
then MEASURES the drawn ink back off the rendered file and refuses a
segment whose ink left the box, so the placement is checked against
pixels rather than asserted.

``tests/unit/audio/test_speaker_identity.py``.
```

## The staging record that was never renamed

Moved from `tests/unit/audio/test_speaker_identity.py::test_promotion_renames_the_staging_record_to_the_final_name`
(2026-10-02). Measured 2026-09-12 on the first beside build of Reel 01 in the
captain's project: two lower thirds placed on V7, F24 ERROR "2 item(s) on the
lower-third row (V7) and this reel has no recorded speaker lower-third plan at
all". The three sibling records (`explainer_plan`, `reel_semantic_visual`
twice) were renamed at promotion and this one was not.
