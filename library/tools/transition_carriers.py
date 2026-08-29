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

**Which spine blocks reach V1 is the whole of the rule.**
:func:`block_reaches_v1` is the one statement of it, and
``compile_manifest`` builds its V1 track from the same predicate so the
two cannot drift.  A bookend card plays on V1, and so does a speech or
hook block.  Everything else - ``transition_slot`` above all, which is
what the spine leaves for step 3.02 to fill - is covered by B-roll, and
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
"""

from library.tools.bookends import block_bookend
from library.tools.spine_contract import SPEECH_BLOCK_TYPES

# Why a cut cannot carry a drawn transition, or why it can.  One string
# per outcome, stated as a fact about where the picture is placed.  These
# reach a prompt, so they say what IS, never what to do about it.
CARRIES = "yes"
NO_V1_CLIP_ENDS_HERE = "no"

# The basis names WHERE THE TWO HALVES LAND, because a drawn transition
# is a tail and a head and they can land on different pictures.  One
# short phrase per row: the mechanism behind it is said once in
# CUTS_LEGEND rather than fifteen times in the table.
BASIS_DRAWS_THROUGH = (
    "outgoing {outgoing} on V1, incoming {incoming} on V1: draws through "
    "this cut")
BASIS_HEAD_AFTER_THE_CUTAWAY = (
    "outgoing {outgoing} on V1, incoming {incoming} on V2: the tail draws "
    "here, the head on the next V1 clip after the cutaway")
BASIS_OUTGOING_ON_V2 = "outgoing {outgoing} on V2: no V1 clip ends here"
BASIS_NOTHING_FOLLOWS = "outgoing {outgoing} on V1 but it ends the V1 track"

#: What the two derived columns of ``cuts_toon`` ARE.  Step 4.02's
#: ``handoff.md`` is under the captain's freeze and cannot name them, so
#: the definition travels as data beside the table - the same route
#: ``music_measurement.MEASUREMENT_LEGEND`` takes for step 2.04.  It says
#: what each column measures; it never says what to conclude.
CUTS_LEGEND = {
    "can_carry_drawn_transition":
        "Whether a DRAWN transition (fade_to_black, zoom_blur, defocus, "
        "flash) can be built at this cut. 'yes' or 'no'. A drawn "
        "transition is a tail on the outgoing V1 clip and a head on the "
        "next one, so it needs a V1 clip ending at the cut with another "
        "V1 clip after it. A cut reading 'no' fails compilation if a "
        "drawn transition is planned on it. The undrawn types "
        "(hard_cut, jump_cut, match_cut) place nothing and are "
        "unaffected: they are buildable at every cut.",
    "carry_basis":
        "Which track the blocks either side of this cut play on, and "
        "therefore where the effect's two halves land. A speech, hook or "
        "bookend block puts a clip on V1; every other block - a "
        "transition_slot above all - is covered by B-roll, and every "
        "B-roll placement goes on V2. 'draws through this cut' means the "
        "tail and the head land on the two pictures either side of it. "
        "'the head on the next V1 clip after the cutaway' means the "
        "incoming picture is B-roll on V2, so the effect brackets the "
        "cutaway instead of drawing through the cut - it is buildable, "
        "and it is not the same gesture. 'ends the V1 track' means there "
        "is no V1 clip left to draw the head half onto.",
}


def block_reaches_v1(block: dict) -> bool:
    """True when this spine block puts a clip on the V1 video track.

    The one statement of V1 membership.  ``compile_manifest`` builds its
    V1 track from this, and :func:`cut_carriers` reads the same rule off
    the spine before the run, so a step planning transitions and the step
    compiling them cannot disagree about where the picture is.
    """
    if not isinstance(block, dict):
        return False
    if block_bookend(block):
        return True
    return block.get("block_type") in SPEECH_BLOCK_TYPES


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
    return rows
