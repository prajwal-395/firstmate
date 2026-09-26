"""Which cuts can carry a DRAWN transition, and which cannot.

A drawn transition is not a thing that sits between two blocks.  It is a
tail on the OUTGOING **V1** clip and a head on the V1 clip after it, drawn
by ``apply_fusion_comps`` per clip (AGENTS.md 5, "Transitions go through
Fusion").  ``compile_manifest`` therefore looks the outgoing clip up by
its tail::

    after_clip = _v1_index_ending_at(v1_clips, cut_time)
    if after_clip is None:
        raise ValueError(f"Transition ... does not sit at the end of any V1 clip")
    if after_clip + 1 >= len(v1_clips):
        raise ValueError(f"Transition ... sits at the end of the last V1 clip")

So a cut can carry a drawn transition only where a V1 clip ends, and only
when another V1 clip follows.

And the step that plans them is TOLD which cuts those are - it does not
work it out, and it does not offer a cut that cannot carry one.  (Moved
from AGENTS.md 5 verbatim, where the rule keeps its index row.)

**Which spine blocks reach V1 is the whole of the rule.**
:func:`block_reaches_v1` is the one statement of it, and
``compile_manifest`` builds its V1 track from the same predicate so the
two cannot drift.  A bookend card plays on V1, and so does a speech,
hook or picture block.  Everything else - ``transition_slot`` above all, which is
what the spine leaves for step 3.02 to fill, and the music-led ``music``
block beside it - is covered by B-roll, and
**every B-roll placement goes on V2** (AGENTS.md 5, "Tracks").  No V1
clip ends where a transition_slot block ends, so **a cut whose OUTGOING
block is a transition slot cannot carry a drawn transition.**

Why this module exists
----------------------
On 001's run of record, five of fifteen cuts were unbuildable this way
and the transitions step was told nothing about it.  It planned a defocus
on one of them and ``compile_manifest`` failed the run with
``Transition trans_009 at 38.801s does not sit at the end of any V1
clip``.  Worse than the wasted round trip: on that spine every unbuildable
cut is beat-aligned, and the handoff tells the model to *"prefer placing
major creative transitions on cuts with a nearby beat"* - so following the
stated criterion steered straight at the cuts that cannot carry the
effect.  ``cuts_toon`` already carried each cut's ``type``; nothing said
it decided anything.

The other half of the same mechanism
------------------------------------
``after_clip + 1`` is an index into the V1 LIST, and a cutaway is not in
it.  So on a ``speech-to-transition_slot`` cut the tail draws on the
outgoing speech clip and the head draws on the speech clip that RESUMES
after the cutaway - the effect brackets the cutaway rather than drawing
through the cut.  It is buildable, and it is not the same gesture, so
``carry_basis`` says which of the two it is.  This was found by one of the
answerers in the A/B below, reading only the buildability column: the
first thing an informed model did was find the next thing it had not been
told.

This module does not filter, rank or choose.  It states a fact per cut and
step 4.02's bridge puts it in the table as two more columns.  Choosing
where a transition goes stays with the model (AGENTS.md 10.5).

A hazard this does NOT model
----------------------------
``_v1_index_ending_at`` matches within 0.25 s, and step 4.02's post-bridge
may move a cut back to a beat-coincident word end up to
``MAX_WORD_END_BACKTRACK`` (0.35 s) before the block boundary.  A cut
backtracked by more than 0.25 s would fail compilation even where this
module says ``yes``.  On 001 every speech block's last word ends exactly
at its ``timeline_end`` (measured 2026-08-28, 11 of 11 speech blocks, gap
0.000 s), so the two bounds have never disagreed there.  It is a separate
defect in the post-bridge, not a property of the menu, and it is recorded
here rather than papered over with a narrower answer.


Rules relocated from AGENTS.md 5
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 5
keeps the headline and points here.

**A DRAWN transition can only sit where a V1 clip ends, and the step that plans them is TOLD which cuts those are.**
One enumeration, `library/tools/transition_carriers.py`.
- `block_reaches_v1` is the single statement of V1 membership - a bookend card, a `speech`, `hook` or `picture` block cut from footage (a voiceover-sourced speech block puts nothing on V1: its words play from the audio file and B-roll covers its picture) - and `compile_manifest` builds its V1 track from that same predicate, so the two cannot drift.
- Every B-roll placement goes on V2, so a cut whose OUTGOING block is a `transition_slot` has no V1 clip ending on it and `compile_manifest` refuses the transition by name. A cut whose outgoing clip is the LAST thing on V1 is refused too: the effect is a tail AND a head.
- `cut_carriers` reads that off the spine before the run, and step 4.02's bridge puts it in `cuts_toon` as `can_carry_drawn_transition` / `carry_basis`. **Both columns are DEFINED in step 4.02's `handoff.md`**, under "Context data available". They travelled beside the table as a `CUTS_LEGEND` dict only while that file was under the captain's freeze; the freeze was lifted 2026-09-09 and the definition moved into the prose, because one living in two places is worse than either.
- **The table is never filtered or re-ranked.** Every cut is still offered; the model is told the truth and still chooses (section 10.5).
- `tests/test_transition_carriers.py`.
"""

from library.tools.bookends import block_bookend
from library.tools.footage_identity import is_audio_id
from library.tools.spine_contract import PICTURE_BLOCK_TYPES, SPEECH_BLOCK_TYPES

# Why a cut cannot carry a drawn transition, or why it can.  One string
# per outcome, stated as a fact about where the picture is placed.  These
# reach a prompt, so they say what IS, never what to do about it.
CARRIES = "yes"
NO_V1_CLIP_ENDS_HERE = "no"

# The basis names WHERE THE TWO HALVES LAND, because a drawn transition
# is a tail and a head and they can land on different pictures.  One
# short phrase per row: the mechanism behind it is said once in step
# 4.02's handoff.md rather than fifteen times in the table.
BASIS_DRAWS_THROUGH = (
    "outgoing {outgoing} on V1, incoming {incoming} on V1: draws through "
    "this cut")
BASIS_HEAD_AFTER_THE_CUTAWAY = (
    "outgoing {outgoing} on V1, incoming {incoming} on V2: the tail draws "
    "here, the head on the next V1 clip after the cutaway")
BASIS_OUTGOING_ON_V2 = "outgoing {outgoing} on V2: no V1 clip ends here"
BASIS_NOTHING_FOLLOWS = "outgoing {outgoing} on V1 but it ends the V1 track"
#: Rung 7 (TR3.1): the end of the piece. Nothing follows, so only a
#: tail-only fade and an end-placed native dissolve can sit there -
#: step 4.02 plans those with `cut_point_position: "end"`, and any
#: other type at the end drops with its reason.
BASIS_END_OF_PIECE = (
    "outgoing {outgoing} on V1, nothing follows: the end of the piece - "
    "a tail-only fade or an end-placed native dissolve only")
BASIS_END_NOT_V1 = (
    "outgoing {outgoing} ends the piece off V1: no V1 clip ends here")

# The bound a cut must sit within to count as a track cut. The same
# bound `compile_manifest._v1_index_ending_at` matches V1 clip tails
# within, so plan-time buildability and compile-time placement cannot
# disagree about where a cut is (finding 16: a native transition "into"
# V2 b-roll landed on the V1 clip underneath instead of the b-roll
# edge it was planned into).
CUT_TOLERANCE_SECONDS = 0.25


def v2_pair_at(v2_spans: list, cut_time: float,
               tolerance: float = CUT_TOLERANCE_SECONDS):
    """The (outgoing, incoming) V2 pair abutting `cut_time`, or None.

    `v2_spans` is [(timeline_start, timeline_end), ...] - b-roll
    assignments and interjections, the clips that play on V2. Returns
    indices into the TIMELINE order (sorted by start), because that is
    the order the build places and the applicator reads back. A
    native Resolve transition is a tail on the outgoing item and a
    head on the incoming one, so a lone V2 clip with no neighbour on
    the track carries nothing - exactly the V1 rule one track over.
    """
    if cut_time is None:
        return None
    try:
        cut = float(cut_time)
    except (TypeError, ValueError):
        return None
    order = sorted(range(len(v2_spans)),
                   key=lambda i: (float(v2_spans[i][0]), float(v2_spans[i][1])))
    for pos, i in enumerate(order):
        try:
            end = float(v2_spans[i][1])
        except (TypeError, ValueError, IndexError):
            continue
        if abs(end - cut) <= tolerance and pos + 1 < len(order):
            try:
                nxt_start = float(v2_spans[order[pos + 1]][0])
            except (TypeError, ValueError, IndexError):
                continue
            if abs(nxt_start - cut) <= tolerance:
                return (pos, pos + 1)
    return None


def block_reaches_v1(block: dict) -> bool:
    """True when this spine block puts a clip on the V1 video track.

    The one statement of V1 membership.  ``compile_manifest`` builds its
    V1 track from this, and :func:`cut_carriers` reads the same rule off
    the spine before the run, so a step planning transitions and the step
    compiling them cannot disagree about where the picture is.

    A speech or hook block sourced from a catalogued voiceover file
    (its `clip_id` is an `audio_001` id) puts NOTHING on V1: its words
    play from the audio file on A1 and B-roll covers its picture, so
    for V1 membership it reads like the music block beside it.
    """
    if not isinstance(block, dict):
        return False
    if block_bookend(block):
        return True
    if block.get("block_type") not in (
            SPEECH_BLOCK_TYPES + PICTURE_BLOCK_TYPES):
        return False
    if is_audio_id(block.get("clip_id")):
        return False
    return True


def cut_carriers(structure: list) -> list[dict]:
    """One row per cut in the spine, saying whether it can carry a drawn
    transition.

    A cut is the boundary between block ``i-1`` and block ``i``, keyed -
    as ``cuts_toon`` and the transition plan both key it - by the
    INCOMING block's ``position``.

    Returns, per cut::

        {"position": ..., "cut_time": float,
         "can_carry": bool, "verdict": "yes"|"no", "basis": str}
    """
    blocks = list(structure or [])
    reaches = [block_reaches_v1(b) for b in blocks]

    rows = []
    for i in range(1, len(blocks)):
        prev_block, curr_block = blocks[i - 1], blocks[i]
        prev_type = prev_block.get("block_type", "unknown")
        curr_type = curr_block.get("block_type", "unknown")

        if not reaches[i - 1]:
            can_carry = False
            basis = BASIS_OUTGOING_ON_V2.format(outgoing=prev_type)
        elif not any(reaches[i:]):
            # The outgoing clip is the last thing on V1, so there is
            # nothing to draw the head half of the effect onto.
            can_carry = False
            basis = BASIS_NOTHING_FOLLOWS.format(outgoing=prev_type)
        elif reaches[i]:
            can_carry = True
            basis = BASIS_DRAWS_THROUGH.format(
                outgoing=prev_type, incoming=curr_type)
        else:
            # Buildable, but the head half lands on the V1 clip AFTER the
            # cutaway rather than on the picture the viewer sees next -
            # `after_clip + 1` is an index into the V1 list, and the
            # cutaway is not in it. The effect brackets the cutaway.
            can_carry = True
            basis = BASIS_HEAD_AFTER_THE_CUTAWAY.format(
                outgoing=prev_type, incoming=curr_type)

        rows.append({
            "position": curr_block.get("position", i),
            "cut_time": curr_block.get("timeline_start", 0.0),
            "can_carry": can_carry,
            "verdict": CARRIES if can_carry else NO_V1_CLIP_ENDS_HERE,
            "basis": basis,
        })
    if blocks:
        # The end of the piece, addressed as `cut_point_position:
        # "end"`. It carries no drawn transition needing two pictures -
        # only the tail-only / end-placed kinds step 4.02 allows there -
        # so it reads `yes` exactly when the last block puts a V1 clip
        # on the timeline to fade out of.
        last, last_type = blocks[-1], blocks[-1].get("block_type",
                                                     "unknown")
        end_time = last.get("timeline_end",
                            last.get("timeline_start", 0.0))
        if reaches[-1]:
            rows.append({
                "position": "end",
                "cut_time": end_time,
                "can_carry": True,
                "verdict": CARRIES,
                "basis": BASIS_END_OF_PIECE.format(outgoing=last_type),
            })
        else:
            rows.append({
                "position": "end",
                "cut_time": end_time,
                "can_carry": False,
                "verdict": NO_V1_CLIP_ENDS_HERE,
                "basis": BASIS_END_NOT_V1.format(outgoing=last_type),
            })
    return rows
