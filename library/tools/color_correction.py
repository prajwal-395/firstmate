"""The grade the COLOURIST decides, and how it composes with a declared look.

A project that names no brand template still gets a grade, because a
colourist - the model at step 5.01 - looks at the measured clips and
decides one (captain, 2026-09-03).  The decision is per clip: a colourist
may bring two bright shots down, leave a third, and lift a dark one only
part of the way because its darkness is the content.

Why this is not `exposure_reference` wearing a new hat
------------------------------------------------------
`exposure_reference` stays exactly what it is: a `LookElement` a brand
TEMPLATE declares, one per-series scalar.  The correction is its own
field, `color_correction` (:data:`FIELD`), on three counts:

1. **It is per clip, and it is not one target.**  One scalar would make
   "normalise everything to the mean" the only expressible answer.
2. **It is a JUDGEMENT, not a declaration**, and writing it into the
   template slot would make `series_look.exposure_reference` mean two
   things depending on who wrote it.
3. **A template that declares one must keep winning.**  Keeping the two
   fields apart is what makes that precedence expressible and testable.

A project that declares a template gets that template's look with the
correction composed underneath it.

How the two compose, exactly
----------------------------
An ASC CDL is ``out = (in * slope + offset) ** power`` per channel, then a
saturation term.  `compose_cdl` applies, in this order:

    1. the correction's exposure gain ``g = 2 ** exposure_stops`` and its
       per-channel ``slope`` multiplier ``c``      (a pre-scale)
    2. the declared look's ``slope`` ``s`` and ``offset`` ``o``
    3. the correction's ``offset`` shift ``d``     (same linear stage)
    4. the declared look's ``power`` ``p``
    5. the correction's ``power`` ``q``
    6. the two saturations

    out = ( in * (g * c * s) + (o + d) ) ** (p * q)
    saturation = look_saturation * correction_saturation

Every step is exact: ``(x ** p) ** q == x ** (p * q)``, a pre-scale folds
into the slope, a same-stage offset adds, and the luma-preserving
saturation composes multiplicatively.  That is why the order is fixed.
Gamma is offered (it is exact under (4)/(5)); `CORRECTION_TERMS` is the
vocabulary and `WITHHELD_TERMS` records what a correction may not say and
where it lives instead.

What is refused, what is dropped, and what is recorded
------------------------------------------------------
* A malformed VALUE - a slope that is not three numbers, a level that is
  not a number - RAISES `ColorCorrectionRefused`, so
  `library/tools/post_bridge_retry.py` carries it back to the model.
* An entry that names no clip in the cut, no correction term, or no
  reason is DROPPED with the reason recorded (`Dropped`).  Completing it
  would mean this file choosing a number.  `DROP_REASONS` is the whole of
  what a drop can be for; a reason outside it is refused by name.
* **`planning_basis` says which ABSENCE an ungraded run is**, recorded as
  the spec's `correction_basis` (`basis_record`).  Four readings
  (`BASIS_READINGS`): ``corrected``, ``judged_no_correction_needed`` (a
  decision), ``no_correction_decision`` (nobody decided), and
  ``every_entry_dropped`` (the absence of a decision, not a decision to
  do nothing).
* `check_assessment` holds the colourist's checkable prose claims (a
  universal scope word, one term, one number) against what shipped, and
  REPORTS a mismatch - never refuses.

There are no bounds.  How far a correction may travel is the colourist's,
the same way `series_look` bounds no declared slope (AGENTS.md 10.5).

`tests/unit/picture/test_color_grade.py`, `tests/unit/picture/test_grade_delivery.py`,
`tests/unit/picture/test_color_grade.py`.

The nine measured clips graded with the identity CDL, and the ruling that
gave the decision to a colourist: docs/evidence/color_correction.md.


Rules relocated from AGENTS.md 12
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 12
keeps the headline and points here.

**A project that names no template still gets a GRADE, because a colourist decides one.**
One enumeration, `library/tools/color_correction.py`. [why - the nine measured clips and the identity CDL](docs/RULE_EVIDENCE.md#the-step-that-measured-nine-clips-and-graded-none) Step 5.01 measured 001's nine clips across a 2.7x luma spread - clip_011 at 145.495, clip_017 at 53.116 - and wrote the identity CDL on all nine, because normalisation was reachable only through an `exposure_reference` only a template declares. Captain, 2026-09-03: *"we need to still let the LLM understand it should try to add some color grading if it thinks it is needed rather than saying no completely bc of a lack of brand template."*
- **The correction is its own field, NOT `exposure_reference` reused.** That slot is a per-SERIES scalar a template DECLARES; a correction is per-clip, is a JUDGEMENT, and a template that declares one must keep winning. Writing a model's answer into a template slot would make the key mean two things depending on who wrote it.
- **The two compose EXACTLY, in a stated serial order**: `out = (in * (2**exposure_stops * slope * look_slope) + (look_offset + offset)) ** (look_power * power)`, saturations multiplied. Every step is exact - `(x**p)**q == x**(p*q)`, a pre-scale folds into slope, a same-stage offset adds - which is why the order is fixed. **A declared look with no correction is byte-for-byte `look.cdl()`.**
- **No bound and no default.** How far a correction may travel is the colourist's, the same way `series_look` bounds no declared slope. A malformed VALUE RAISES so `post_bridge_retry` carries it back to the model; an entry naming no clip, no term or no `why` is DROPPED with the reason (`DROP_REASONS`, refused if outside).
- **`correction_basis` says which absence an ungraded run is.** FOUR readings, spelled differently on purpose: `corrected`, `judged_no_correction_needed` (a decision), `no_correction_decision` (nobody looked), `every_entry_dropped`. The old output could not tell the second from the third - an identity CDL read the same either way.
- **`WITHHELD_TERMS` records what a correction may NOT say** and where it lives instead: `temperature` (no CDL term; say it as slope and offset), `contrast` (Fusion's, not the CDL's), `curve` (no reader anywhere).
- 5.01 is now HYBRID: `bridge.py` measures and builds `clip_exposure` + `cut_adjacency` (the pairs a viewer sees, in stops), `handoff.md` asks a colourist, `post_bridge.py` composes. `tests/unit/picture/test_color_grade.py`, `tests/unit/picture/test_color_grade.py`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

RGB = Tuple[float, float, float]

#: The key the model writes its per-clip corrections under.  One
#: spelling, here, because a key name spelled twice is this repository's
#: dominant bug class (AGENTS.md 10.1).
FIELD = "color_correction"

#: The key carrying the colourist's account of the cut as a whole - what
#: they saw across the clips and why the corrections are what they are.
#: Separate from the per-clip `why` because a grade is a decision about a
#: SEQUENCE and the per-clip sentences cannot carry that.
ASSESSMENT_FIELD = "grade_assessment"


class ColorCorrectionRefused(ValueError):
    """A correction that cannot be delivered as written.

    Raised rather than dropped and rather than completed.  A raised
    violation reaches the model that caused it through
    `library/tools/post_bridge_retry.py`; a completed one is this file
    choosing a strength.
    """


@dataclass(frozen=True)
class CorrectionTerm:
    """One move a colourist may make, and what it does to the CDL.

    Attributes:
        key: What the answer writes.
        scalar: True when the value is one number rather than an (r,g,b).
        neutral: The value that changes nothing.  Recorded so the
            composition can say what an absent term means without a
            second statement of it.
        composes: How it meets a declared look's own term, in one word.
        why_here: What a colourist uses it for.
    """

    key: str
    scalar: bool
    neutral: Any
    composes: str
    why_here: str


#: Everything a per-clip correction may carry.  A key outside this table
#: is REFUSED: a misspelt term that silently changes nothing is the exact
#: failure this repository keeps hitting, and a grade is the one place
#: where "not applied" and "applied faintly" look alike in a report.
CORRECTION_TERMS: Tuple[CorrectionTerm, ...] = (
    CorrectionTerm(
        key="exposure_stops",
        scalar=True,
        neutral=0.0,
        composes="multiplies the slope equally on all three channels",
        why_here=(
            "How much brighter or darker this clip should sit, in stops - "
            "the unit exposure is actually discussed in. A stop is a "
            "doubling, so +1.0 is twice the light and -0.5 is about a "
            "third less. It moves level and leaves the hue balance alone "
            "because it multiplies the three channels equally."
        ),
    ),
    CorrectionTerm(
        key="slope",
        scalar=False,
        neutral=(1.0, 1.0, 1.0),
        composes="multiplies the declared look's slope, per channel",
        why_here=(
            "Gain, per channel. Scaling moves the bright end most, so "
            "this is where a cast in the highlights is corrected - a shot "
            "shot under sodium light against one shot in daylight."
        ),
    ),
    CorrectionTerm(
        key="offset",
        scalar=False,
        neutral=(0.0, 0.0, 0.0),
        composes="adds to the declared look's offset, per channel",
        why_here=(
            "Lift, per channel. Adding moves the dark end most, so this "
            "is the black floor and the shadow tint - lifting a crushed "
            "shadow, or taking a blue cast out of one."
        ),
    ),
    CorrectionTerm(
        key="power",
        scalar=False,
        neutral=(1.0, 1.0, 1.0),
        composes="multiplies the declared look's power, per channel",
        why_here=(
            "Gamma, per channel. Warps the middle without moving either "
            "end, which is how a face is brought up in a shot whose "
            "highlights are already where they should be."
        ),
    ),
    CorrectionTerm(
        key="saturation",
        scalar=True,
        neutral=1.0,
        composes="multiplies the declared look's saturation",
        why_here=(
            "One global multiplier. 1.0 changes nothing; below 1.0 pulls "
            "colour out, above 1.0 pushes it."
        ),
    ),
)

TERMS_BY_KEY: Dict[str, CorrectionTerm] = {t.key: t for t in CORRECTION_TERMS}

#: The key that is not a term: the colourist's reason for this clip.  Not
#: optional - a correction with no reason is a number nobody can review,
#: and reviewing the grade is what the captain does next.
WHY_KEY = "why"

#: The other non-term key: which clip this is about.
CLIP_KEY = "clip_id"

#: Terms this deliberately does NOT offer, with the reason.  A colourist
#: asking for one of these is asking for something the renderer has no
#: term for, and inventing a mapping would be the engine choosing what
#: the word means.
WITHHELD_TERMS = {
    "temperature": (
        "A single kelvin-ish warm/cool number has no CDL term. Delivering "
        "it means this file deciding how many points of red per point of "
        "blue a 'warmer' shot gets, which is a strength nobody chose. Say "
        "it as `slope` and `offset` triples, which is what a CDL carries "
        "and what a colourist would set anyway."
    ),
    "contrast": (
        "A CDL cannot pivot around mid grey, which is why a declared "
        "look delivers contrast through Fusion "
        "(`series_look.LOOK_ELEMENTS`). A per-clip pivot contrast would "
        "need a per-clip Fusion comp for the correction's sake alone; "
        "whether a correction may draw a comp is a real question and it "
        "is not this change's to answer."
    ),
    "curve": (
        "There is no curve node on the CDL route and none on the Fusion "
        "route this repository builds. A term with no reader draws "
        "nothing and says nothing (AGENTS.md 10.2)."
    ),
}

# ── What an ungraded run IS ──────────────────────────────────────────

CORRECTED = "corrected"
JUDGED_NO_CORRECTION_NEEDED = "judged_no_correction_needed"
NO_CORRECTION_DECISION = "no_correction_decision"
EVERY_ENTRY_DROPPED = "every_entry_dropped"

BASIS_READINGS = (
    CORRECTED,
    JUDGED_NO_CORRECTION_NEEDED,
    NO_CORRECTION_DECISION,
    EVERY_ENTRY_DROPPED,
)

BASIS_MEANING = {
    CORRECTED:
        "the colourist named corrections and at least one reached the CDL.",
    JUDGED_NO_CORRECTION_NEEDED:
        "the colourist read the measurements and decided this footage "
        "needs no correction. A DECISION, and not the same fact as "
        "nobody having looked.",
    NO_CORRECTION_DECISION:
        "no correction decision was recorded at all - the step did not "
        "reach a colourist, or the answer carried no correction field. "
        "The absence of a decision, spelled differently from a decision "
        "to leave the footage alone.",
    EVERY_ENTRY_DROPPED:
        "the colourist named corrections and none survived. Each drop is "
        "recorded with its reason; this is the absence of a decision, not "
        "a decision to do nothing.",
}

#: The whole of what a drop can be for.  A reason outside this table is
#: refused by name, so a new drop branch has to say what it is before it
#: can go quiet.  Same shape as `vfx_plan_basis.DROP_REASONS`.
DROP_REASONS = {
    "no_clip_named":
        "the entry names no clip_id, so there is nothing to apply it to.",
    "clip_not_in_the_cut":
        "the entry names a clip that no V1 or V2 placement uses, so "
        "nothing on the timeline would carry it.",
    "no_correction_terms":
        "the entry names no correction term, or names only neutral ones. "
        "Leaving a clip alone is said by not naming it; an entry that "
        "changes nothing is an instruction nobody can act on.",
    "no_reason_given":
        "the entry names no `why`. A grade value with no reason beside it "
        "is a number nobody can review, and reviewing the grade is the "
        "next thing that happens to it.",
    "duplicate_clip":
        "a second entry names a clip an earlier entry already corrected. "
        "Merging two corrections for one clip means this engine deciding "
        "which one the colourist meant.",
}


@dataclass
class Correction:
    """One clip's correction, as read off the answer."""

    clip_id: str
    why: str
    exposure_stops: Optional[float] = None
    slope: Optional[RGB] = None
    offset: Optional[RGB] = None
    power: Optional[RGB] = None
    saturation: Optional[float] = None
    #: The term keys the answer really wrote, in declaration order.
    declared: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def exposure_gain(self) -> float:
        """`2 ** exposure_stops`, or 1.0. The definition of a stop."""
        if self.exposure_stops is None:
            return 1.0
        return 2.0 ** self.exposure_stops


@dataclass
class Dropped:
    """One entry that did not survive, and why."""

    reason: str
    detail: str
    entry: Dict[str, Any]


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ColorCorrectionRefused(
            f"{where} must be a number, got {value!r}.")
    if not math.isfinite(float(value)):
        raise ColorCorrectionRefused(
            f"{where} must be a finite number, got {value!r}.")
    return float(value)


def _triple(value: Any, where: str) -> RGB:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ColorCorrectionRefused(
            f"{where} must be three numbers (r, g, b), got {value!r}.")
    values = list(value)
    if len(values) != 3:
        raise ColorCorrectionRefused(
            f"{where} must be three numbers (r, g, b), got {len(values)}. "
            f"A per-channel term with a channel missing cannot be "
            f"completed without this engine choosing the missing one.")
    return (
        _number(values[0], f"{where}[r]"),
        _number(values[1], f"{where}[g]"),
        _number(values[2], f"{where}[b]"),
    )


def _is_neutral(term: CorrectionTerm, value: Any) -> bool:
    if term.scalar:
        return abs(float(value) - float(term.neutral)) < 1e-9
    return all(abs(a - b) < 1e-9 for a, b in zip(value, term.neutral))


def read_corrections(answer: Any, clips_in_the_cut: Sequence[str]
                     ) -> Tuple[List[Correction], List[Dropped]]:
    """Read the colourist's per-clip corrections off the answer.

    Args:
        answer: whatever the model wrote under `color_correction`.
        clips_in_the_cut: the clip ids that really carry a placement, so
            an entry naming something else is dropped rather than written
            into a spec no renderer will join.

    Returns:
        `(corrections, dropped)`.  Both are records; neither is a
        judgement about whether the grade is any good.

    Raises:
        ColorCorrectionRefused: a value that is not the shape the CDL
            takes.  Carried back to the model by `post_bridge_retry`.
    """
    if answer is None:
        return [], []
    if isinstance(answer, Mapping):
        # A model that wrapped the list in its own key.  Accepted because
        # the alternative is failing a run over a container, and the
        # entries inside are validated exactly the same way.
        answer = answer.get(FIELD, answer.get("clips", []))
    if not isinstance(answer, Sequence) or isinstance(answer, (str, bytes)):
        raise ColorCorrectionRefused(
            f"`{FIELD}` must be a list of per-clip corrections, got "
            f"{type(answer).__name__}.")

    known = set(clips_in_the_cut)
    corrections: List[Correction] = []
    dropped: List[Dropped] = []
    seen: set = set()

    for raw in answer:
        if not isinstance(raw, Mapping):
            raise ColorCorrectionRefused(
                f"every entry in `{FIELD}` must be an object carrying "
                f"{CLIP_KEY}, at least one correction term and {WHY_KEY}; "
                f"got {raw!r}.")
        entry = dict(raw)

        unknown = sorted(
            set(entry) - set(TERMS_BY_KEY) - {CLIP_KEY, WHY_KEY})
        if unknown:
            withheld = [k for k in unknown if k in WITHHELD_TERMS]
            detail = "".join(
                f"\n  {k}: {WITHHELD_TERMS[k]}" for k in withheld)
            raise ColorCorrectionRefused(
                f"a `{FIELD}` entry carries {unknown}, which nothing "
                f"reads. A term the renderer never applies changes no "
                f"pixel and reports no failure. Known terms: "
                f"{', '.join(TERMS_BY_KEY)}.{detail}")

        clip_id = str(entry.get(CLIP_KEY) or "").strip()
        if not clip_id:
            dropped.append(Dropped("no_clip_named",
                                   DROP_REASONS["no_clip_named"], entry))
            continue
        if known and clip_id not in known:
            dropped.append(Dropped(
                "clip_not_in_the_cut",
                f"{clip_id} is not one of the {len(known)} clip(s) the cut "
                f"places. " + DROP_REASONS["clip_not_in_the_cut"], entry))
            continue
        if clip_id in seen:
            dropped.append(Dropped(
                "duplicate_clip",
                f"{clip_id} was already corrected by an earlier entry. "
                + DROP_REASONS["duplicate_clip"], entry))
            continue

        values: Dict[str, Any] = {}
        declared: List[str] = []
        for term in CORRECTION_TERMS:
            if term.key not in entry or entry[term.key] is None:
                continue
            where = f"`{FIELD}[{clip_id}].{term.key}`"
            value = (_number(entry[term.key], where) if term.scalar
                     else _triple(entry[term.key], where))
            if _is_neutral(term, value):
                # Neutral is not a move. Recorded as not declared rather
                # than carried, so `declared` says what really changes.
                continue
            values[term.key] = value
            declared.append(term.key)

        if not declared:
            dropped.append(Dropped("no_correction_terms",
                                   DROP_REASONS["no_correction_terms"],
                                   entry))
            continue

        why = str(entry.get(WHY_KEY) or "").strip()
        if not why:
            dropped.append(Dropped("no_reason_given",
                                   DROP_REASONS["no_reason_given"], entry))
            continue

        seen.add(clip_id)
        corrections.append(Correction(
            clip_id=clip_id, why=why, declared=tuple(declared), **values))

    _assert_drop_reasons_are_known(dropped)
    return corrections, dropped


def _assert_drop_reasons_are_known(dropped: Sequence[Dropped]) -> None:
    unknown = sorted({d.reason for d in dropped} - set(DROP_REASONS))
    if unknown:
        raise RuntimeError(
            f"library/tools/color_correction.py dropped an entry for "
            f"{unknown}, which is not in DROP_REASONS. A new drop branch "
            f"says what it is before it can go quiet.")


def compose_cdl(look, correction: Optional[Correction],
                neutral_cdl: Mapping[str, float]) -> Dict[str, float]:
    """The one CDL the renderer sees, for one clip.

    The declared look and the colourist's correction, flattened in the
    serial order this module's docstring states.  Every step of that
    flattening is exact, so the returned CDL is what a Resolve node tree
    of the two would produce and not an approximation of it.

    Args:
        look: a `series_look.DeclaredLook`, or None where the project's
            template declares no look.
        correction: this clip's correction, or None where the colourist
            left it alone.
        neutral_cdl: `series_look.NEUTRAL_CDL`, passed in rather than
            imported so this module holds no CDL values of its own.
    """
    gain = correction.exposure_gain if correction else 1.0
    c_slope = (correction.slope if correction and correction.slope
               else (1.0, 1.0, 1.0))
    c_offset = (correction.offset if correction and correction.offset
                else (0.0, 0.0, 0.0))
    c_power = (correction.power if correction and correction.power
               else (1.0, 1.0, 1.0))
    c_sat = (correction.saturation
             if correction and correction.saturation is not None else 1.0)

    if look is not None and look.has_cdl:
        l_slope, l_offset = look.slope, look.offset
        l_power, l_sat = look.power, look.saturation
    else:
        l_slope, l_offset = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
        l_power, l_sat = (1.0, 1.0, 1.0), 1.0

    values = dict(neutral_cdl)
    for index, channel in enumerate("rgb"):
        values[f"slope_{channel}"] = round(
            gain * c_slope[index] * l_slope[index], 4)
        values[f"offset_{channel}"] = round(
            l_offset[index] + c_offset[index], 4)
        values[f"power_{channel}"] = round(
            l_power[index] * c_power[index], 4)
    values["saturation"] = round(float(l_sat) * float(c_sat), 4)
    return values


def planning_basis(decided: bool, corrections: Sequence[Correction],
                   dropped: Sequence[Dropped]) -> str:
    """Which of the four an ungraded clip list IS.

    Args:
        decided: did a colourist answer this step at all?  False is the
            only thing that produces `no_correction_decision`, and it is
            a fact about the RUN, never inferred from an empty list.
    """
    if not decided:
        return NO_CORRECTION_DECISION
    if corrections:
        return CORRECTED
    if dropped:
        return EVERY_ENTRY_DROPPED
    return JUDGED_NO_CORRECTION_NEEDED


def basis_record(basis: str, corrections: Sequence[Correction],
                 dropped: Sequence[Dropped], assessment: str = "",
                 clips_in_the_cut: Sequence[str] = ()) -> dict:
    """The whole account of what the colourist did, for the spec.

    `proposed` and `resolved` are counted separately for the same reason
    `vfx_plan_basis` counts them: an empty result reads identically
    whether nothing was proposed or everything was discarded.

    `assessment_mismatches` is D11's guard: the assessment's checkable
    claims held against the corrections that shipped
    (`check_assessment`). Empty when nothing checkable disagreed - which
    covers both "the claim holds" and "the prose made no checkable
    claim", and the function says which prose it cannot read.
    """
    if basis not in BASIS_READINGS:
        raise RuntimeError(
            f"library/tools/color_correction.py cannot record the basis "
            f"{basis!r}; known: {', '.join(BASIS_READINGS)}.")
    return {
        "basis": basis,
        "means": BASIS_MEANING[basis],
        "proposed": len(corrections) + len(dropped),
        "resolved": len(corrections),
        "assessment": assessment,
        "assessment_mismatches": check_assessment(
            assessment, corrections, clips_in_the_cut),
        "dropped": [
            {"reason": d.reason, "detail": d.detail, "entry": d.entry}
            for d in dropped
        ],
    }


#: Scope words that make an assessment sentence a claim about the whole
#: cut rather than about a named clip. Closed vocabulary, in the shape
#: `direction_contradiction` keeps: a claim outside it is not checked,
#: and inventing a reading of one would be this module deciding what
#: the colourist meant (AGENTS.md 10.5).
UNIVERSAL_SCOPE_WORDS = (
    "every",
    "all",
    "each",
    "everywhere",
    "throughout",
    "across the board",
    "whole cut",
    "entire cut",
)

#: Bare words the colourist writes for terms whose key carries a suffix.
#: `exposure +0.5 stops` means `exposure_stops`; nothing else here needs
#: an alias, so nothing else gets one.
TERM_ALIASES = {
    "exposure": "exposure_stops",
}

_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)")
_CLIP_ID_RE = re.compile(r"clip_\d+", re.IGNORECASE)


def check_assessment(assessment: Any,
                     corrections: Sequence[Correction],
                     clips_in_the_cut: Sequence[str]) -> List[Dict[str, Any]]:
    """Hold the assessment's checkable claims against what shipped.

    D11: the colourist wrote "one global saturation of 1.10 on every
    placed clip" while two entries carried no `saturation` term and
    shipped at neutral. Both halves were stored, both were read by a
    human, and nothing compared them.

    A claim is checkable only when the prose carries all three of a
    universal scope word (`UNIVERSAL_SCOPE_WORDS`), exactly one
    correction term (`CORRECTION_TERMS`, plus `TERM_ALIASES`), and
    exactly one number. Anything else is DECLINED - returned as no
    mismatch - because pairing two numbers with two terms, or reading
    a per-clip sentence as a universal one, invents a reading this
    module must not invent. A term the answer left out reads as that
    term's neutral, which is the composition's own rule
    (`compose_cdl`), not a second statement of it; a clip with no
    entry at all ships every term neutral.

    This REPORTS, never refuses. A prose claim is a model judgement,
    and a gate that fails correct output is no coverage (AGENTS.md
    10.4, and `direction_contradiction`'s flag-never-act shape).

    Returns:
        One record per contradicted claim - `term`, `claimed`, the
        `scope` word that made it universal, the `clips` shipping
        something else with their effective values, and a `detail`
        sentence. Empty when nothing checkable disagreed.
    """
    if not assessment or not isinstance(assessment, str):
        return []
    if not list(clips_in_the_cut):
        return []
    lowered = assessment.lower()
    scope = next(
        (word for word in UNIVERSAL_SCOPE_WORDS
         if re.search(r"\b" + re.escape(word) + r"\b", lowered)),
        None)
    if scope is None:
        return []
    terms = [term.key for term in CORRECTION_TERMS if term.key in lowered]
    for alias, key in TERM_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", lowered) \
                and key not in terms:
            terms.append(key)
    if len(terms) != 1:
        return []
    term = TERMS_BY_KEY[terms[0]]
    if not term.scalar:
        # A triple claim ("slope 1.1, 1.0, 0.9 on every clip") is three
        # numbers the prose may punctuate any way it likes. Declined
        # rather than parsed.
        return []
    numbers = _NUMBER_RE.findall(_CLIP_ID_RE.sub(" ", assessment))
    if len(numbers) != 1:
        return []
    claimed = float(numbers[0])

    by_clip = {c.clip_id: c for c in corrections}
    offending: List[str] = []
    for clip_id in clips_in_the_cut:
        correction = by_clip.get(clip_id)
        value = getattr(correction, term.key, None) \
            if correction is not None else None
        effective = float(value) if value is not None else float(
            term.neutral)
        if abs(effective - claimed) >= 1e-9:
            offending.append(f"{clip_id} (ships {effective})")
    if not offending:
        return []
    return [{
        "term": term.key,
        "claimed": claimed,
        "scope": scope,
        "clips": [row.split(" ", 1)[0] for row in offending],
        "detail": (
            f"grade_assessment claims {term.key} {claimed} on {scope} "
            f"clip(s), but {len(offending)} of "
            f"{len(list(clips_in_the_cut))} ship something else: "
            f"{', '.join(offending)}. A term the answer leaves out "
            f"ships neutral ({term.neutral})."),
    }]


def describe_correction(correction: Optional[Correction]) -> str:
    """One sentence saying what was done to this clip, and why."""
    if correction is None:
        return ""
    parts = []
    if correction.exposure_stops is not None:
        parts.append(f"exposure {correction.exposure_stops:+.3f} stops "
                     f"(slope x{correction.exposure_gain:.4f})")
    for key in ("slope", "offset", "power"):
        value = getattr(correction, key)
        if value is not None:
            parts.append(f"{key} "
                         f"({value[0]:.4f}, {value[1]:.4f}, {value[2]:.4f})")
    if correction.saturation is not None:
        parts.append(f"saturation x{correction.saturation:.4f}")
    return f"Colourist: {'; '.join(parts)}. {correction.why}"


def term_legend() -> Dict[str, str]:
    """What each term IS, as DATA beside the context.

    It defines the vocabulary and how each term meets a declared look.
    It never says what to conclude - the `MEASUREMENT_LEGEND` route
    (AGENTS.md 10.5).
    """
    legend = {
        t.key: f"{t.why_here} Neutral is {t.neutral}; it {t.composes}."
        for t in CORRECTION_TERMS
    }
    legend[WHY_KEY] = (
        "why this clip needed what you gave it, in your own words. Not "
        "optional: an entry with no reason is dropped, because a grade "
        "value nobody can review is what the last run shipped nine of.")
    legend[CLIP_KEY] = (
        "the clip this corrects, exactly as the measurement table spells "
        "it. An entry naming a clip the cut does not place is dropped.")
    legend["how_it_composes"] = (
        "out = ( in * (2**exposure_stops * slope * look_slope) + "
        "(look_offset + offset) ) ** (look_power * power), with the two "
        "saturations multiplied. Exact for that order, which is why the "
        "order is fixed. Where no brand template declares a look, every "
        "look term is identity and your correction IS the grade.")
    legend["leaving_a_clip_alone"] = (
        "say it by not naming the clip. An entry whose terms are all "
        "neutral is dropped rather than written as a no-op, because a "
        "no-op CDL is what the last run wrote on all nine clips and "
        "Resolve drew no node for any of them.")
    legend["no_correction_at_all"] = (
        "an empty list is a legitimate answer and it is recorded as a "
        "DECISION you took (`judged_no_correction_needed`), distinct in "
        "the output from a run where no colourist was asked. Say what you "
        "saw in `" + ASSESSMENT_FIELD + "` either way.")
    return legend
