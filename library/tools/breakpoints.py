"""One enumeration for WHERE a run stops so the captain can look at it.

The problem this exists to remove
---------------------------------
The review gate has worked since it was written - a snapshot of the
step's output, feedback carrying `approved` / `rejected` / `revised`,
`apply_feedback_to_output` merging a revision back before the run
continues, and `--resume` to carry on.  What it never had was a
SELECTOR.  `--review` is one boolean and it gates after EVERY step, so
"stop after the rough cut and nowhere else" could not be said at all,
and the only way to get one pause was to take twenty-six.

The captain, 2026-08-30: "what if i want to setup specific breakpoints
and such for a given run".

So a breakpoint is armed PER STEP, and `--review` becomes the special
case "every step" rather than the only case.  Nothing about the gate
machinery changes: `library/tools/review_gate.py` still writes the
snapshot, still takes the three actions and still applies a revision.
This decides which steps reach it.

A breakpoint is a request to PAUSE, and that is why it does not refuse
--------------------------------------------------------------------
`run_scope` REFUSES a selection that strands a consumer, because an
absent required input has no code path and the run would die inside
`gather_step_inputs`.  A breakpoint armed at a step this run does not
run is a different thing entirely: nothing downstream is missing, and
the only consequence is that a pause the captain asked for never
happens.

Never happening quietly is still unacceptable - "a step that exists and
silently never runs is the trap AGENTS.md section 3 exists to stop" -
so an unreachable breakpoint is NAMED, in the run header, before the run
starts, and recorded on the run's own account of itself.  It is reported
rather than refused because refusing would make `--profile podcast
--only catalog` impossible for no gain in safety.

What CAN refuse here
--------------------
A step id that is not in this pipeline, and a step both armed and
disarmed on the same command line.  Both are the captain saying
something that cannot be true, and both are refused by name - the same
shape `run_scope._reject_unknown` takes.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

**A breakpoint is armed PER STEP, and `--review` is the every-step case.**
One enumeration, `library/tools/breakpoints.py`. The gate machinery is unchanged - `review_gate.py` still writes the snapshot and still takes approve/reject/revise; this is the selector it never had.
- **An unreachable breakpoint is NAMED, never refused.** A breakpoint strands no consumer, so refusing would make `--profile podcast --only catalog` impossible for no gain; the header says "armed at X, which this run does not run - it will NOT stop there" before the run starts, and `pipeline_run.json` records it.
- An unknown step id, or a step both `--break` and `--no-break`, IS refused by name.
- **Arming a gate makes it `pending` and throws away the previous run's answer.** A gate that pauses is by definition unanswered.
- **The pause prints the command that answers it and the command that carries on**, and the resume command DROPS `--rerun` (`breakpoints._NOT_CARRIED`): carrying it would clear the ledger entry the pause just wrote and stop in the same place forever.
- `tests/test_run_profile.py`, `tests/test_breakpoints.py`, `tests/test_run_configuration_end_to_end.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple


class BreakpointError(ValueError):
    """A breakpoint request that cannot be honoured, refused by name."""


EVERY_STEP = "*"
"""The every-step case, said as data. `--review` is this word, and a
profile may carry it, so the two spellings mean one thing."""


# How a breakpoint came to be armed. Recorded per source rather than
# merged, because a run that stops somewhere the captain did not type on
# this command line has to be able to say where that came from.
FROM_PROFILE = "profile"
FROM_REVIEW_FLAG = "--review"
FROM_BREAK_FLAG = "--break"


@dataclass(frozen=True)
class Breakpoints:
    """Where this run stops, and why it thinks so."""

    steps: Tuple[str, ...] = ()
    """Step ids armed individually, in the order they were declared."""

    every_step: bool = False
    """`--review`, or `*` from a profile or `--break`."""

    basis: Tuple[str, ...] = ()
    """One sentence per source that contributed, for the run header."""

    def armed_at(self, step_id: str) -> bool:
        return self.every_step or step_id in self.steps

    @property
    def any_armed(self) -> bool:
        return self.every_step or bool(self.steps)

    def unreachable(self, steps_to_run: Iterable[str]) -> Tuple[str, ...]:
        """Armed steps this run will not reach, in the order declared.

        `every_step` is never unreachable: it means "wherever this run
        goes", and a run with no steps has nothing to say about it.
        """
        running = set(steps_to_run)
        return tuple(step for step in self.steps if step not in running)

    def describe(self,
                 steps_to_run: Optional[Iterable[str]] = None) -> List[str]:
        """The lines a run prints about where it will stop."""
        if not self.any_armed:
            return ["  Breakpoints: none - this run does not stop for review"]
        if self.every_step:
            lines = ["  Breakpoints: every step (review gates on)"]
        else:
            lines = [f"  Breakpoints: {', '.join(self.steps)}"]
        for sentence in self.basis:
            lines.append(f"    - {sentence}")
        if steps_to_run is not None:
            missing = self.unreachable(steps_to_run)
            if missing:
                lines.append(
                    f"    ! armed at {', '.join(missing)}, which this run "
                    f"does not run - it will NOT stop there")
        return lines

    def as_record(self,
                  steps_to_run: Optional[Iterable[str]] = None) -> dict:
        """What goes on `pipeline_run.json`, so a reader that never saw
        the command line can tell where the run intends to stop."""
        return {
            "every_step": self.every_step,
            "steps": list(self.steps),
            "basis": list(self.basis),
            "unreachable": list(self.unreachable(steps_to_run or ())),
        }


NO_BREAKPOINTS = Breakpoints()


def resolve(known_steps: Iterable[str],
            profile_breakpoints: Sequence[str] = (),
            review_all: bool = False,
            break_at: Sequence[str] = (),
            no_break_at: Sequence[str] = (),
            profile_name: str = "") -> Breakpoints:
    """Arm this run's breakpoints, or raise `BreakpointError`.

    Four sources, and they compose the way the run profile's selection
    does - the profile is the default and the command line outranks it:

    * a profile's `breakpoints:` list arms steps;
    * `--review` arms every step;
    * `--break <step>` arms one more (or `*` for every step);
    * `--no-break <step>` disarms one, and `--no-break '*'` disarms the
      lot - which is how "run my project's profile but do not stop
      anywhere" is said.
    """
    known: Set[str] = set(known_steps)
    profile_breakpoints = tuple(profile_breakpoints or ())
    break_at = tuple(break_at or ())
    no_break_at = tuple(no_break_at or ())

    _reject_unknown(known, break_at, "--break")
    _reject_unknown(known, no_break_at, "--no-break")

    contradicted = sorted(set(break_at) & set(no_break_at))
    if contradicted:
        raise BreakpointError(
            f"{', '.join(contradicted)} is both --break and --no-break. "
            f"Say it once."
        )

    disarm_all = EVERY_STEP in no_break_at
    disarmed = {step for step in no_break_at if step != EVERY_STEP}

    every_step = (review_all
                  or EVERY_STEP in profile_breakpoints
                  or EVERY_STEP in break_at)

    armed: List[str] = []
    basis: List[str] = []

    from_profile = [step for step in profile_breakpoints
                    if step != EVERY_STEP and step not in disarmed]
    if EVERY_STEP in profile_breakpoints and not disarm_all:
        basis.append(f"profile {profile_name or '(unnamed)'} arms every step")
    if from_profile and not disarm_all:
        armed += from_profile
        basis.append(f"profile {profile_name or '(unnamed)'} arms "
                     f"{', '.join(from_profile)}")

    if review_all and not disarm_all:
        basis.append("--review arms every step")

    from_flag = [step for step in break_at
                 if step != EVERY_STEP and step not in disarmed]
    if EVERY_STEP in break_at and not disarm_all:
        basis.append("--break '*' arms every step")
    if from_flag and not disarm_all:
        for step in from_flag:
            if step not in armed:
                armed.append(step)
        basis.append(f"--break arms {', '.join(from_flag)}")

    if disarm_all:
        return Breakpoints(
            steps=(), every_step=False,
            basis=(("--no-break '*' disarmed every breakpoint this run "
                    "would otherwise have had"),))

    if disarmed:
        basis.append(f"--no-break disarmed {', '.join(sorted(disarmed))}")

    return Breakpoints(steps=tuple(dict.fromkeys(armed)),
                       every_step=every_step,
                       basis=tuple(basis))


def _reject_unknown(known: Set[str], values: Sequence[str],
                    label: str) -> None:
    for value in values:
        if value == EVERY_STEP:
            continue
        if value not in known:
            raise BreakpointError(
                f"{label} {value!r} is not a step in this pipeline "
                f"(and is not {EVERY_STEP!r}, which means every step). "
                f"Known steps: {', '.join(sorted(known))}."
            )


# Flags that must NOT be carried into a resume, with the reason.
# A resume is the SAME run continuing, so anything that describes work
# already performed would perform it a second time.
_NOT_CARRIED = {
    # `--rerun` clears a ledger entry and deletes artifacts. Carrying it
    # into the resume would clear the entry the pause just wrote, re-run
    # the step, re-arm its breakpoint and stop in exactly the same place
    # - a loop the captain cannot get out of by following the printed
    # instruction. The re-run happened; it is not a standing request.
    "--rerun": True,
}


def resume_command(argv: Sequence[str],
                   executable: str = "python3") -> str:
    """The exact command that carries on from a gate, spelled out.

    A breakpoint is armed per RUN, so a run resumed without the flags
    that armed it sails straight past the next one.  This is THIS
    process's own argv with `--resume` added, MINUS the flags that
    describe work already done (`_NOT_CARRIED`), so it is right
    whichever CLI launched the run - and it is printed at the pause,
    which is the moment the captain is least able to work it out for
    themselves.
    """
    words: List[str] = []
    skip_next = False
    for word in argv:
        if skip_next:
            skip_next = False
            continue
        if word == "--resume":
            continue
        flag = word.split("=", 1)[0]
        if flag in _NOT_CARRIED:
            skip_next = "=" not in word
            continue
        words.append(word)
    return " ".join([executable, *words, "--resume"])


# ── The CLI half ─────────────────────────────────────────────────────

def add_breakpoint_arguments(parser) -> None:
    """`--break` and `--no-break`, registered from here by BOTH CLIs.

    Same reason `run_scope.add_scope_arguments` exists: a flag defined in
    two places is a flag that will eventually differ.
    """
    parser.add_argument(
        "--break", action="append", metavar="STEP", default=[],
        dest="break_at",
        help=f"Stop after this step for review, and nowhere else. "
             f"Repeatable. {EVERY_STEP!r} means every step, which is what "
             f"--review does. The pause uses the review gate: inspect the "
             f"snapshot, approve/reject/revise, then --resume.")
    parser.add_argument(
        "--no-break", action="append", metavar="STEP", default=[],
        dest="no_break_at",
        help=f"Do not stop after this step, even if the run profile arms "
             f"it. Repeatable. {EVERY_STEP!r} disarms every breakpoint "
             f"this run would otherwise have had.")
