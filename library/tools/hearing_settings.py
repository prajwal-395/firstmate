"""Which dials the hearing pass really has, and which numbers are not dials.

The captain, 2026-09-16, on `library/tools/reel_hearing.py` one day
after it landed:

    *"ok see if you can refine it and configure it a bit more to push
     what we can do but i think overall this is still a pretty big
     upgrade"*

So this module answers "configure WHAT", by sorting every number the
pass carries into two kinds that must not be confused.

Two kinds, and only one of them is a dial
-----------------------------------------
**PINNED** - a property of how the two transcribers disagree. It was
MEASURED on this project's own audio before it was written down, and the
only honest way to move it is to measure again. `DRIFT_NOISE_FLOOR_
SECONDS` is one: 0.25s sits a little over one standard deviation above
the 94.1ms mean absolute disagreement measured over 6,983 words. Raising
it does not make a reel better - it makes the instrument blind, and a
check tuned until it stops reporting is the gate that cannot fail turned
inside out (AGENTS.md 10.4).

**CHOSEN** - a judgement about what is worth reporting, which really is
a project's or a run's. `caption_coverage_floor` is one: how much of a
word must have a card under it before the viewer is served is a
question about a series' caption style, and a karaoke-heavy reel and a
talking head can answer it differently without either being wrong.

A PINNED value may still be MOVED - and the record says so
----------------------------------------------------------
Refusing to let anyone move a measured value would make the pass
unusable the first time it meets footage it was not measured on. So a
pinned value is overridable, and every hearing that moved one records it
in `moved_pinned` and says so in its own summary. A record that moved a
pinned dial is not comparable to one that did not, and it must never be
readable as though it were. That is the same shape as `--override <req>`
(AGENTS.md section 3): proceed, and be RECORDED.

Why `decided_value` does not bind here, stated rather than assumed
-----------------------------------------------------------------
`library/tools/decided_value.py` carries the captain's standing ruling
that a number deciding a creative outcome belongs to a decision and not
to code. It does not reach these values, for two independent reasons:

1. **None of them decides a creative outcome.** They decide what gets
   REPORTED about a measurement already taken. No transition, mood,
   effect, level, energy word or sound is chosen anywhere in this pass;
   a different `caption_coverage_floor` changes which words are listed
   in a report, and changes not one frame and not one decibel of what
   ships. `creative_floors` draws exactly this line, and these fall on
   the mechanical side of it.
2. **The ladder structurally cannot run here.** `decide` requires a
   slot's deciding step to reach a model, declare the slot in its
   manifest's top-level `decides`, and carry the question in its prompt
   on the same run. The hearing pass is not a DAG step, calls no model
   and has no manifest. Registering a slot it could never fill would be
   a declaration that cannot be true, which is the thing
   `requirements.py` exists to refuse.

What is recorded instead is the smaller true thing: WHICH READING
answered each dial - the measurement, the project, or this run - which
is `decided_value`'s discipline without its ladder.

Where a project declares its own
--------------------------------
`pipeline.reel_hearing` in the project's `project.yaml`, read through
`brand_registry.project_pipeline_block` like every other per-project
declaration. A project that declares none gets the measured values and
the record says `pinned` for every dial. A malformed declaration RAISES
rather than being dropped: a caption bar the captain believes he set and
that silently did not apply is worse than no declaration at all.

Turning a check OFF is not the same as it having nothing to say
---------------------------------------------------------------
`checks_declined` names the metrics this producer is asked NOT to make.
A check a project turned off lands in `hearing.skipped` with that as its
reason - it is never simply absent - exactly
as the coverage check already does on a reel with no captions. A report
that is simply missing a row reads as a clean one, and that is the
defect this whole pass exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from library.tools import reel_hearing

# `reel_hearing` defines these three and this module decides what may
# move them.  Imported rather than re-typed: a bound spelled twice is
# this repository's dominant bug class (AGENTS.md 10.1), and a default
# that drifts from the constant the pass actually documents would make
# every `basis` below a lie.  The import is one-way - `reel_hearing`
# reaches this module from inside `hear`, never at import time.

PINNED = "pinned"
"""The measured value answered: nothing asked for anything else."""

PROJECT = "project"
"""The project declared it in its own `project.yaml`."""

RUN = "run"
"""This invocation asked for it, on the command line."""

READINGS = (PINNED, PROJECT, RUN)
"""Every way a dial's value can have been arrived at. Complete."""

MEASURED = "measured"
"""A property of the instruments. Moving it needs a new measurement."""

CHOSEN = "chosen"
"""A judgement about what is worth reporting. Genuinely a dial."""

KINDS = (MEASURED, CHOSEN)

BLOCK = "reel_hearing"
"""The key under `pipeline:` a project declares its own values in."""


class MalformedHearingDeclaration(ValueError):
    """A `pipeline.reel_hearing` block that cannot be acted on."""


@dataclass(frozen=True)
class Dial:
    """One value, and whether it is a dial at all."""

    name: str
    kind: str
    default: Any
    what: str
    """One sentence: what this value does."""
    basis: str
    """MEASURED: the measurement. CHOSEN: why it is a judgement."""

    def cast(self, value: Any, where: str) -> Any:
        """The declared value, in this dial's own type, or a refusal.

        A whole-number dial refuses a fraction rather than truncating
        it: `drift_run_min_words: 2.7` silently becoming 2 is a
        declaration the captain wrote and the engine did not obey.
        """
        wanted = type(self.default)
        if isinstance(value, bool):
            raise MalformedHearingDeclaration(
                f"{where}: {self.name} must be "
                f"{wanted.__name__}, got a boolean")
        try:
            cast = wanted(value)
        except (TypeError, ValueError) as bad:
            raise MalformedHearingDeclaration(
                f"{where}: {self.name} must be "
                f"{wanted.__name__}, got {value!r}") from bad
        if wanted is int and float(value) != float(cast):
            raise MalformedHearingDeclaration(
                f"{where}: {self.name} counts words, so it must be a "
                f"whole number; {value!r} is not one.")
        if cast <= 0:
            raise MalformedHearingDeclaration(
                f"{where}: {self.name} is {cast!r}. A hearing dial at or "
                f"below zero cannot report anything - a floor of zero "
                f"makes every word a finding and a run of zero words "
                f"makes every finding a run.")
        return cast


DIALS: Tuple[Dial, ...] = (
    Dial(
        "drift_noise_floor_seconds", MEASURED,
        reel_hearing.DRIFT_NOISE_FLOOR_SECONDS,
        "Below this, a drift between the plan and the render is the two "
        "transcribers disagreeing rather than anything an edit did.",
        "Measured 2026-09-16 over 6,983 words of this project's own "
        "audio: the on-device transcriber's word starts sit 94.1ms mean "
        "absolute from WhisperX's forced-alignment boundaries with a "
        "134.0ms standard deviation. A quarter second is a little over "
        "one standard deviation above that. Re-measure before moving it; "
        "a project cannot know this number about its own footage without "
        "running the comparison that produced it."),
    Dial(
        "drift_run_min_words", CHOSEN,
        reel_hearing.DRIFT_RUN_MIN_WORDS,
        "How many consecutive words must drift the same way past the "
        "floor before the run is reported.",
        "The FLOOR is measured; how much evidence is worth a reader's "
        "attention is not. A reel cut into two-second passages can have "
        "a whole passage sit inside three words, and a forty-minute "
        "talking head can afford to wait for more before it speaks up. "
        "Reel 26's run is nine words long and is found at any setting "
        "this dial can take."),
    Dial(
        "caption_coverage_floor", CHOSEN,
        reel_hearing.CAPTION_HALF_COVERED,
        "How much of a spoken word must have a caption card on screen "
        "before the word counts as captioned.",
        "A readability judgement about a series' caption style, not a "
        "property of any instrument. Zero coverage - no card at all "
        "while the word is spoken - is counted separately and is NOT "
        "governed by this dial, because a card that never arrives is a "
        "different defect from one that arrives late."),
)

BY_NAME: Dict[str, Dial] = {dial.name: dial for dial in DIALS}


def assert_dials_are_well_formed() -> None:
    """Every dial names its kind and says what its value rests on.

    A dial with no basis is a constant with better paperwork - the exact
    thing this module was written to stop - so it is refused at import
    rather than shipped.
    """
    for dial in DIALS:
        if dial.kind not in KINDS:
            raise MalformedHearingDeclaration(
                f"{dial.name} declares kind {dial.kind!r}, which is not "
                f"one of {KINDS}")
        if not dial.what.strip() or not dial.basis.strip():
            raise MalformedHearingDeclaration(
                f"{dial.name} declares no {'what' if not dial.what.strip() else 'basis'}. "
                f"A MEASURED dial must name its measurement and a CHOSEN "
                f"one must say why it is a judgement.")


assert_dials_are_well_formed()


@dataclass(frozen=True)
class HearingSettings:
    """The values one hearing ran with, and what answered each."""

    drift_noise_floor_seconds: float = reel_hearing.DRIFT_NOISE_FLOOR_SECONDS
    drift_run_min_words: int = reel_hearing.DRIFT_RUN_MIN_WORDS
    caption_coverage_floor: float = reel_hearing.CAPTION_HALF_COVERED
    readings: Dict[str, str] = field(default_factory=dict)
    moved_pinned: List[Dict[str, Any]] = field(default_factory=list)
    """Every MEASURED dial this hearing did not use the measurement for."""
    checks_declined: Tuple[str, ...] = ()
    """Metrics a project or a run asked not to be made."""

    def runs(self, metric: str) -> bool:
        return metric not in self.checks_declined

    def as_dict(self) -> Dict[str, Any]:
        return {
            "drift_noise_floor_seconds": self.drift_noise_floor_seconds,
            "drift_run_min_words": self.drift_run_min_words,
            "caption_coverage_floor": self.caption_coverage_floor,
            "readings": dict(self.readings),
            "moved_pinned": list(self.moved_pinned),
            "checks_declined": list(self.checks_declined),
            "dials": [
                {"name": d.name, "kind": d.kind, "default": d.default,
                 "what": d.what, "basis": d.basis}
                for d in DIALS],
        }


def project_declaration(project_folder: Optional[str]) -> Dict[str, Any]:
    """`pipeline.reel_hearing` off a project.yaml, or `{}`.

    An empty mapping and an absent block are the same thing here: no
    dial was declared. A block that is not a mapping, or that names a
    key nothing reads, RAISES - a declaration the engine silently drops
    is a preference the captain believes is in force and is not.
    """
    from library.tools.brand_registry import project_pipeline_block

    block = project_pipeline_block(project_folder)
    declared = block.get(BLOCK)
    if declared is None:
        return {}
    where = f"pipeline.{BLOCK} in {project_folder}/project.yaml"
    if not isinstance(declared, dict):
        raise MalformedHearingDeclaration(
            f"{where} must be a mapping, got "
            f"{type(declared).__name__}: {declared!r}")
    known = set(BY_NAME) | {"checks_declined"}
    unknown = sorted(set(declared) - known)
    if unknown:
        raise MalformedHearingDeclaration(
            f"{where} declares {unknown}, which nothing reads. It takes "
            f"{sorted(known)}.")
    return dict(declared)


def _declined(value: Any, where: str) -> Tuple[str, ...]:
    """The metrics a declaration asks not to be measured, checked."""
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise MalformedHearingDeclaration(
            f"{where}: checks_declined must be a list of metric names, "
            f"got {value!r}")
    names = tuple(str(name) for name in value)
    unknown = sorted(set(names) - set(reel_hearing.METRICS))
    if unknown:
        raise MalformedHearingDeclaration(
            f"{where}: checks_declined names {unknown}, which this pass "
            f"does not measure. It measures "
            f"{list(reel_hearing.METRICS)}.")
    return names


def resolve(project_folder: Optional[str] = None,
            overrides: Optional[Dict[str, Any]] = None,
            declined: Optional[Iterable[str]] = None) -> HearingSettings:
    """The values one hearing runs with, and what answered each.

    Precedence is run, then project, then the measurement. Every dial
    records WHICH of the three answered it, and a MEASURED dial that the
    measurement did not answer is additionally recorded in
    `moved_pinned` - see the module docstring on why moving one is
    allowed and why it can never be silent.
    """
    declared = project_declaration(project_folder)
    where = f"pipeline.{BLOCK} in {project_folder}/project.yaml"
    asked = {name: value for name, value in (overrides or {}).items()
             if value is not None}
    unknown = sorted(set(asked) - set(BY_NAME))
    if unknown:
        raise MalformedHearingDeclaration(
            f"this run asks for {unknown}, which is not a hearing dial. "
            f"The dials are {sorted(BY_NAME)}.")

    values: Dict[str, Any] = {}
    readings: Dict[str, str] = {}
    moved: List[Dict[str, Any]] = []
    for dial in DIALS:
        if dial.name in asked:
            values[dial.name] = dial.cast(asked[dial.name], "this run")
            readings[dial.name] = RUN
        elif dial.name in declared:
            values[dial.name] = dial.cast(declared[dial.name], where)
            readings[dial.name] = PROJECT
        else:
            values[dial.name] = dial.default
            readings[dial.name] = PINNED
        if dial.kind == MEASURED and readings[dial.name] != PINNED:
            moved.append({
                "dial": dial.name, "measured": dial.default,
                "used": values[dial.name], "asked_by": readings[dial.name],
                "measurement": dial.basis,
            })

    off = tuple(_declined(declined, "this run")) or _declined(
        declared.get("checks_declined"), where)
    return HearingSettings(readings=readings, moved_pinned=moved,
                           checks_declined=off, **values)


def warning_lines(settings: HearingSettings) -> List[str]:
    """What a reader must be told about how this hearing was configured.

    Nothing at all when every dial read the measurement and every check
    ran - the common case says nothing rather than adding a line to
    every report.
    """
    lines: List[str] = []
    for row in settings.moved_pinned:
        lines.append(
            f"  MOVED:    {row['dial']} is a MEASURED value and this "
            f"hearing used {row['used']} instead of {row['measured']} "
            f"(asked by the {row['asked_by']}). This record is not "
            f"comparable to one that used the measurement.")
    chosen = [name for name, reading in sorted(settings.readings.items())
              if reading != PINNED and name not in
              {row["dial"] for row in settings.moved_pinned}]
    if chosen:
        lines.append(
            "  dials:    "
            + ", ".join(f"{name}={getattr(settings, name)} "
                        f"({settings.readings[name]})" for name in chosen))
    return lines
