"""Why step 4.03's visual-effects plan is the length it is.

`{"visual_effects": []}` was the whole of what the step said about an
empty plan, and it meant two opposite things.  On project 001's run of
record it meant the planner had read the piece and decided it wanted
stillness - it wrote the reasoning before answering and held the answer
across three `semantically empty` rejections
(`docs/run-001-reasoning/plan_vfx.md`).  It means exactly the same bytes
when the planner asked for four effects and the post-bridge dropped every
one of them for naming a block that is not on the spine, an effect the
toolkit has not got, or an intensity outside `subtle|moderate|strong`.

The drops were printed to stderr, so they reached
`pipeline_output/logs/run_*.log` and nothing else.  A line forty minutes
into an unattended run is read by nobody - the same reading AGENTS.md §13
gives the bookend card that vanished into a log line - so an edit could
ship with no effects that nobody decided to leave out, and the output
could not tell a reviewer which had happened.

This module is the whole vocabulary for saying which.  It states a
BASIS, it does not judge one: an empty plan is still a legitimate answer
(`may_be_empty: true` on the step's `vfx_creative`), and nothing here
rejects, pads or completes a plan.

    python3 -m library.tools.vfx_plan_basis          # the vocabulary

What this module deliberately does NOT do
-----------------------------------------
It does not refuse the step when every entry is dropped.  AGENTS.md §10.5
refuses an unplayable SFX plan by name in step 4.04, and §13 refuses an
invented bookend, so the precedent for refusing here exists and is
recorded in `THE_REFUSAL_QUESTION` - but which of the drop reasons
should stop a run is a decision about how the pipeline behaves, and it is
the captain's, not this module's.  Recording is what makes the question
askable from the output rather than from a log.


Rules relocated from AGENTS.md 10.2
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.2 keeps the headline
and points here.

**An empty VFX plan says WHY it is empty, and step 4.03 CAN produce a non-empty one.**
One enumeration, `library/tools/vfx_plan_basis.py`. [why - `{"visual_effects": []}` read the same whether the planner chose stillness or named four effects the post-bridge discarded, plus the four hypotheses and which one it was](docs/RULE_EVIDENCE.md#the-vfx-plan-that-was-always-empty)
- `enhancement_spec.planning_basis` carries `basis`, `proposed`, `resolved` and one `dropped` record per casualty with its reason. **`no_effects_planned` and `every_entry_dropped` are spelled differently on purpose**: the first is a decision, the second is the absence of one.
- `DROP_REASONS` is the whole of what a drop can be for and a reason outside it is refused by name, so a new drop branch has to say what it is before it can go quiet.
- **`no_effects_planned` and `every_entry_dropped` are spelled differently on purpose**: the first is a decision, the second is the absence of one.
- **Recording is not gating.** An empty plan is accepted. Whether a dropped entry should REFUSE the step is the captain's call (`THE_REFUSAL_QUESTION`). true` on `vfx_creative` still holds and an empty plan is still accepted;
- **The step is not broken and the vocabulary is not missing.** `tests/test_vfx_reaches_the_manifest.py` runs a named toolkit effect end to end to a drawn node.
- `tests/test_vfx_plan_basis.py`.
"""

from dataclasses import dataclass, field


# ── The three bases a plan's length can rest on ───────────────────────
#
# `no_effects_planned` and `every_entry_dropped` are spelled differently
# on purpose, and neither is spelled `empty`: the first is a decision and
# the second is the absence of one, which is the distinction the whole
# module exists to carry.  Same shape as `cutaway_window.BASES`, where
# `single_span` is recorded apart from `moment_match` so a reviewer can
# tell a choice from a clip that offered none.
PLAN_BASES = {
    "planned": (
        "the planner named at least one effect and at least one of them "
        "resolved.  `dropped` is empty when every entry survived and "
        "names the casualties when some did not"
    ),
    "no_effects_planned": (
        "the planner named no effects at all.  This is the deliberate "
        "answer for a piece that wants stillness, which the step's "
        "handoff offers in as many words and `may_be_empty: true` "
        "accepts.  Nothing was dropped, because nothing was proposed"
    ),
    "every_entry_dropped": (
        "the planner named effects and NONE of them resolved.  The plan "
        "is empty and nobody decided it should be: read `dropped` for "
        "what each entry asked for and why it could not be built.  Never "
        "read this as a decision for stillness"
    ),
}


# ── What a drop can be for ────────────────────────────────────────────
#
# One row per branch the post-bridge can take that discards an entry.  A
# reason outside this table is refused by name, so a new drop branch has
# to say what it is before it can go quiet.
DROP_REASONS = {
    "not_a_spine_block": (
        "the entry's target_block_position names no block on the timed "
        "spine, so there is no range to place the effect over"
    ),
    "duplicate_block": (
        "a second entry on a block another entry already covers.  One "
        "effect per block; a second is a duplicate, not a stacked effect"
    ),
    "no_effect_type": (
        "the entry names no effect_type.  Which effect a block gets is "
        "the decision this step exists to make and there is nothing to "
        "substitute"
    ),
    "unknown_effect_type": (
        "the effect_type is neither in the step's own toolkit nor a "
        "built-in Fusion clip effect, so no renderer would draw it"
    ),
    "withdrawn_alias": (
        "the effect_type is a spelling that was withdrawn because it "
        "chose something the planner had not stated - `slow_zoom` names "
        "no direction, and picking one would be the engine inventing "
        "taste (AGENTS.md §10.5)"
    ),
    "no_readable_parameters": (
        "the entry names a toolkit effect but its `params` carry none of "
        "the parameter names the renderer dispatches on, so the comp "
        "would be built without the effect in it and nothing would say "
        "so (AGENTS.md §10.2).  The names are enumerated in step 4.03's "
        "`TOOLKIT_PARAMETERS`; what they are set TO is the plan's "
        "decision and nothing is substituted"
    ),
    "no_clip_at_that_position": (
        "no clip on V1 or V2 covers the block the entry names, so there "
        "is no picture to draw the effect on.  An effect is a per-clip "
        "Fusion comp and the renderer builds them on both tracks, so a "
        "cutaway block carries one perfectly well - this is the case "
        "where the timeline shows nothing at all there.  Step 4.03's "
        "candidate table states it per block as `picture_track: none` "
        "(library/tools/vfx_carriers.py), so a plan reaching this reason "
        "asked for an effect the table already said could not be drawn"
    ),
    "generator_not_a_clip_effect": (
        "the effect_type is a generator preset, which produces pixels "
        "from nothing and has no image input.  It cannot modify the "
        "picture and is routed to the overlay track instead - so this is "
        "a drop from the CLIP-EFFECT path, not from the plan"
    ),
    "no_stated_reason": (
        "the entry asks for drift motion on a static hold but states no "
        "per-shot reason.  Motion applies only where the plan states why "
        "that shot wants it (captain's ruling 2026-09-08: a shot with no "
        "reason gets no motion), so an entry with a missing or blank "
        "`rationale` gets no motion rather than a default one"
    ),
    "ken_burns_without_direction": (
        "the entry asks for `ken_burns` but its params state no direction: "
        "`zoom_end` above `zoom_start` is a push in, below is a pull out, "
        "and equal (or missing) names neither.  The direction is read off "
        "the values the plan chose, never defaulted - answering \"in\" on "
        "the planner's behalf would be the engine inventing taste"
    ),
}


# ── What the reader is told each key is ───────────────────────────────
#
# A legend says what a key IS.  It never says what to conclude from it.
# Same route `music_measurement.MEASUREMENT_LEGEND` and
# `transition_carriers.CUTS_LEGEND` take, and for the same reason: step
# 4.03's `handoff.md` is frozen, so a column definition travels as DATA.
BASIS_LEGEND = {
    "basis": "which of PLAN_BASES the length of this plan rests on",
    "proposed": "how many entries the planner named",
    "resolved": "how many of them reached `visual_effects`",
    "dropped": (
        "one record per entry that did not, each carrying the block it "
        "named, the effect it asked for, the reason code and the "
        "post-bridge's own sentence"
    ),
}


# ── The question this module records rather than answers ──────────────
THE_REFUSAL_QUESTION = (
    "Whether a dropped VFX entry should REFUSE step 4.03 the way an "
    "unplayable sound refuses step 4.04 (AGENTS.md §10.5) and an invented "
    "bookend refuses step 2.05 (§13).  The argument for is that a plan "
    "written around an effect ships without it and nobody decided that.  "
    "The argument against is that most of the reasons are the "
    "planner's own typo, and failing a forty-minute run on one is a "
    "harder outcome than recording it.  Not settled here."
)


@dataclass
class DroppedEntry:
    """One plan entry that did not reach the picture, and why."""

    target_block_position: object
    effect_type: str
    reason: str
    detail: str = ""

    def __post_init__(self):
        if self.reason not in DROP_REASONS:
            raise ValueError(
                f"unknown VFX drop reason {self.reason!r}; the reasons are "
                f"{', '.join(sorted(DROP_REASONS))}"
            )

    def as_dict(self) -> dict:
        return {
            "target_block_position": self.target_block_position,
            "effect_type": self.effect_type,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class PlanBasis:
    """What the planner asked for, and what survived."""

    proposed: int
    resolved: int
    dropped: list = field(default_factory=list)

    @property
    def basis(self) -> str:
        return plan_basis(self.proposed, self.resolved)

    def as_dict(self) -> dict:
        return {
            "basis": self.basis,
            "proposed": self.proposed,
            "resolved": self.resolved,
            "dropped": [d.as_dict() for d in self.dropped],
        }


def plan_basis(proposed: int, resolved: int) -> str:
    """Which of `PLAN_BASES` a plan of this shape rests on.

    Raises on a shape that cannot happen, rather than picking the nearest
    basis: more resolved than proposed means the count is wrong, and a
    basis read off a wrong count is worse than no basis.
    """
    if proposed < 0 or resolved < 0:
        raise ValueError(
            f"proposed={proposed} resolved={resolved}: neither can be negative"
        )
    if resolved > proposed:
        raise ValueError(
            f"{resolved} effects resolved from {proposed} proposed: a plan "
            f"cannot grow.  The counts are wrong, not the basis"
        )
    if proposed == 0:
        return "no_effects_planned"
    if resolved == 0:
        return "every_entry_dropped"
    return "planned"


def amend_with_drops(basis_record, dropped: list, entries_seen: int) -> dict:
    """Fold drops made AFTER step 4.03 into that step's own basis record.

    `compile_manifest` is the second place an entry can fall out of the
    plan: the block it names may have no clip on V1 or V2 at all, which
    step 4.03 cannot see because the timeline does not exist yet.  The
    drop belongs in the SAME record the step's own drops go in, so a
    reviewer reads one account of why the layer is the length it is
    rather than two half-accounts in two places.

    `entries_seen` is how many entries reached the compiler.  It is used
    only when 4.03 wrote no record at all - a plan from before
    `planning_basis` existed - because `proposed` then has no other
    source, and a count read off the compiler is the honest one to state.

    Returns a fresh dict; the argument is never mutated.
    """
    record = dict(basis_record) if isinstance(basis_record, dict) else {}
    proposed = record.get("proposed")
    if not isinstance(proposed, int) or proposed < 0:
        proposed = entries_seen
    existing = list(record.get("dropped") or [])
    new_drops = [
        d.as_dict() if isinstance(d, DroppedEntry) else dict(d)
        for d in dropped
    ]
    resolved = record.get("resolved")
    if not isinstance(resolved, int) or resolved < 0:
        resolved = entries_seen
    resolved = max(0, resolved - len(new_drops))

    record["proposed"] = proposed
    record["resolved"] = resolved
    record["dropped"] = existing + new_drops
    record["basis"] = plan_basis(proposed, resolved)
    return record


def basis_summary(basis_record: dict) -> str:
    """One line, for the run log and for a reviewer.

    States the basis rather than a verdict: `no_effects_planned` reads as
    a decision and `every_entry_dropped` reads as the absence of one, and
    the sentence says which without calling either right.
    """
    basis = basis_record.get("basis")
    if basis not in PLAN_BASES:
        raise ValueError(
            f"unknown VFX plan basis {basis!r}; the bases are "
            f"{', '.join(sorted(PLAN_BASES))}"
        )
    proposed = basis_record.get("proposed", 0)
    resolved = basis_record.get("resolved", 0)
    dropped = basis_record.get("dropped", []) or []

    if basis == "no_effects_planned":
        return (
            "VFX plan: no_effects_planned - the planner named no effects. "
            "An empty plan is a legitimate answer and nothing was dropped."
        )
    if basis == "every_entry_dropped":
        reasons = ", ".join(
            sorted({str(d.get("reason", "?")) for d in dropped})
        )
        return (
            f"VFX plan: every_entry_dropped - the planner named {proposed} "
            f"effect(s) and none could be built ({reasons}). The plan is "
            f"empty and nobody decided it should be."
        )
    if dropped:
        reasons = ", ".join(
            sorted({str(d.get("reason", "?")) for d in dropped})
        )
        return (
            f"VFX plan: planned - {resolved} of {proposed} effect(s) "
            f"resolved; {len(dropped)} dropped ({reasons})."
        )
    return f"VFX plan: planned - {resolved} effect(s), none dropped."


def assert_vocabulary_is_well_formed() -> None:
    """Every basis and every reason says what it is, in a sentence.

    An entry that only names itself teaches a reader nothing, which is
    what the bare `[]` did.
    """
    for table, label in ((PLAN_BASES, "PLAN_BASES"),
                         (DROP_REASONS, "DROP_REASONS"),
                         (BASIS_LEGEND, "BASIS_LEGEND")):
        for key, reading in table.items():
            if not isinstance(reading, str) or len(reading.split()) < 4:
                raise ValueError(
                    f"{label}[{key!r}] must state what it is in a sentence"
                )


def _main() -> None:
    assert_vocabulary_is_well_formed()
    print("VFX plan bases")
    for name, reading in PLAN_BASES.items():
        print(f"\n  {name}\n      {reading}")
    print("\nDrop reasons")
    for name, reading in DROP_REASONS.items():
        print(f"\n  {name}\n      {reading}")
    print("\nLegend")
    for name, reading in BASIS_LEGEND.items():
        print(f"\n  {name}\n      {reading}")
    print(f"\nNot settled here\n      {THE_REFUSAL_QUESTION}")


if __name__ == "__main__":
    _main()
