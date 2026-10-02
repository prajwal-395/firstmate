# `library.tools.motion_graphics_vocabulary` - the history behind its contract

This is the module docstring of `library/tools/motion_graphics_vocabulary.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The motion-graphics elements this pipeline may plan, and the axes each one is declared on.

**The problem this closes.** The captain, 2026-08-29, on the capability:
the pipeline has *"access to remotion to make literally any kind of
motion design and animation desired."*  That is true of the toolchain and
false at the moment of decision.  A step asked to pick from an undefined
set picks nothing, or invents inconsistently: the creative audit of round
2 counted **9 of 28 output decisions the model was never asked**, and an
undefined roster is exactly that failure - a decision nobody offered.
"Anything is possible" is not a menu.

**What was here before.** An implicit roster of three, and nobody wrote
it down.  ``remotion-subtitles/src/compositions/MotionGraphics/index.tsx``
draws a two-line upper third, four corner brackets and a progress bar,
each behind its own boolean; ``generate_motion_props`` turns two brand
flags into those booleans and reads ``creative_direction`` for a title
that its schema has no field for.  Project 001 rendered eight segments,
53.8 MB of ProRes, in which ``max(alpha)`` is 0 on every frame - motion
graphics reported as delivered, drawing nothing.  Three elements chosen
by whoever wrote the component is not a vocabulary, and the fact that the
render path is broken is not a reason to keep it at three.

**So this table is written from what an editor needs, not from what the
composition can draw today.**  Every entry carries
:attr:`MotionElement.reachable` saying whether the current renderer can
put it on a frame, and one of the nineteen cannot.  The reachability
flag is a fact reported about each entry; it is not a filter on
membership.  A roster written around today's renderer would bake a
defect into the vocabulary permanently.

An axis, not a value
--------------------
Every entry defines the DIMENSIONS a declaration must fill and fixes none
of them.  There is no colour here, no duration, no size, no easing
strength and no intensity, and none of those has a default or a bound
either - the captain, 2026-08-28: *"i want no hardcoded values. there are
no house glow looks, there are no settled house grain or anything"*, and
PR #310 emptied ``series_look.py`` on that ruling.  A roster that said
"the stat callout holds for 1.2 s in the accent colour" would put the
same defect back one level up, in the one place it is hardest to see.

:data:`AXES` is the vocabulary of dimensions; an entry names the axes it
is declared on.  An axis with enumerated positions names the positions
(``fade``, ``slide``, ``scale``, ``mask``, ``cut`` is a set of motion
characters, not a chosen one).  An axis without them is continuous and
its magnitude belongs to whoever declares it.
:func:`assert_no_settled_values` is the runnable statement of that rule
and ``tests/contracts/test_motion_graphics_vocabulary.py`` parses this file's own
source to enforce it.

Series-neutral, because the purpose is two purposes
---------------------------------------------------
Set 2026-08-25: this engine serves a daily channel **and** client work,
so anything keyed to one identity is a defect rather than a shortcut
(AGENTS.md section 14).  Nothing here names a channel, a host, a show, a
palette or a typeface.  ``channel_bug`` is the closest an entry comes to
identity and it takes a project-supplied ASSET; the engine ships no
artwork and states none.  The captain's own standard - clean, vibrant and
deliberate, not distressed; saturated and bold, no washed-out neutrals -
is a standard for the DECLARATIONS a project or a template writes, and it
is deliberately not encoded here, because encoding it would make every
client's video look like the captain's.

Two neighbouring decisions this roster does NOT take
----------------------------------------------------
Both remain the captain's, and the roster is written so that either
answer works:

1. **What produces the copy a motion graphic shows.**  Entries declare
   :attr:`MotionElement.copy` - whether the element needs a text payload
   at all - and never where that text comes from.  A model writing it, a
   project declaring it, a transcript supplying it and a template
   carrying it all satisfy the same entry.  :data:`COPY_SOURCE_IS_UNSET`
   records that this file must never grow a producer.
2. **Whether the model authors each component or fills a props schema.**
   An entry names a KIND, its axes, its inputs and its refusals.  Under
   the props answer the axes are the schema; under the authoring answer
   they are the brief the authored component must honour, and the
   refusals are what review checks it against.  Nothing here says which.

If a future change to this table would force either answer, that is the
signal to stop and escalate rather than to decide it by implication.

Where the boundary runs
-----------------------
:data:`OUT_OF_VOCABULARY` records the things that look like motion
graphics and belong to another enumeration - captions, transitions,
picture treatment, bookend artwork - each with the module that owns it.
An element is in this roster when it is an ADDITIVE OVERLAY that carries
MEANING the picture and the captions do not already carry.  That is the
line: a treatment of the picture is VFX, a treatment of the spoken word
is a caption, a change between two shots is a transition, and a full
frame of artwork is a bookend.

Reading it
----------
    python3 -m library.tools.motion_graphics_vocabulary            # the roster
    python3 -m library.tools.motion_graphics_vocabulary --check    # the rules, as a gate

:func:`roster_rows` and :data:`ROSTER_LEGEND` are the prompt-side route -
the same shape ``sfx_library.load_sfx_catalog`` and
``music_measurement.MEASUREMENT_LEGEND`` take, so a planning step's
bridge can put the whole roster in front of the model as a table without
this module knowing anything about prompts.  Nothing is shortlisted:
nineteen entries fit, and whatever selects a shortlist becomes the chooser
(AGENTS.md section 10.5).
```

## The tests

Moved from `tests/contracts/test_motion_graphics_vocabulary.py`. The roster answers an
open captain decision delegated on 2026-08-29: *"which motion-graphics elements
belong in our vocabulary"*. The failure it closes is the one the round-2
creative audit counted nine times in twenty-eight decisions - a decision the
model was never offered - and the failure it must not introduce is the one
PR #310 spent an audit removing: a value reaching a frame from a table in this
repository rather than from somebody's declaration. The engine serves a daily
channel and client work (2026-08-25), so a channel's name, a host's name or one
project's slug in the vocabulary is a defect. When written, eleven of fifteen
entries could not be drawn; if that ever inverts silently, the vocabulary has
been trimmed to fit a defect.

A textual scan of the module's table source for value shapes (hex colours,
px/ms/Hz literals, with a `CITED_VALUES` allow-list for the withdrawn cyan
`#00D4FF`, the withdrawn `60px` corner accent and the `5 Hz` face sampling
rate) was retired in the 2026-10 suite halving as a source-text pin; the
structural half (no field can hold a magnitude) and `assert_no_settled_values`
remain.
