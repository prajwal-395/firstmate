"""A run stops where it was told to, and says where it will not.

`--review` gated after EVERY step and there was no way to say "stop
after the rough cut and nowhere else".  The gate machinery itself is
unchanged - `review_gate.py` still writes the snapshot, still takes
approve/reject/revise and still merges a revision back.  This is the
selector it never had.

Two properties are worth holding hardest:

* `--review` still means what it always meant, so nothing that used it
  regresses;
* a breakpoint armed at a step this run will not reach is REPORTED and
  not refused, because a pause that never happens strands nothing - but
  a pause the captain asked for that silently never happens is exactly
  the trap AGENTS.md section 3 exists to stop.
"""

from __future__ import annotations

import pytest

from library.tools import breakpoints, operations, run_scope
from library.tools.breakpoints import EVERY_STEP, BreakpointError


@pytest.fixture(scope="module")
def steps():
    return {n["id"] for n in run_scope.load_dag()["nodes"]}


def _resolve(steps, **kw):
    return breakpoints.resolve(known_steps=steps, **kw)


# ── Nothing armed ────────────────────────────────────────────────────

def test_a_plain_run_stops_nowhere(steps):
    gates = _resolve(steps)
    assert not gates.any_armed
    assert not any(gates.armed_at(s) for s in steps)
    assert "none" in gates.describe()[0]


# ── --review is now the every-step case, and still works ─────────────

def test_review_arms_every_step(steps):
    gates = _resolve(steps, review_all=True)
    assert gates.every_step
    assert all(gates.armed_at(s) for s in steps)
    assert gates.armed_at("a step that does not exist"), (
        "every_step means wherever the run goes")




# ── Per step ─────────────────────────────────────────────────────────

def test_one_step_is_armed_and_nothing_else(steps):
    gates = _resolve(steps, break_at=("review_rough_cut",))
    assert gates.armed_at("review_rough_cut")
    assert not gates.armed_at("catalog")
    assert not gates.every_step






def test_no_break_star_disarms_the_lot(steps):
    """"Run my project's profile but do not stop anywhere" - the one
    thing that could not be said before at all."""
    gates = _resolve(steps, profile_breakpoints=(EVERY_STEP, "catalog"),
                     review_all=True, break_at=("scan",),
                     no_break_at=(EVERY_STEP,))
    assert not gates.any_armed
    assert not gates.every_step
    assert "disarmed every breakpoint" in gates.basis[0]


# ── What is refused ──────────────────────────────────────────────────

def test_an_unknown_step_is_refused_by_name(steps):
    with pytest.raises(BreakpointError) as exc:
        _resolve(steps, break_at=("rough_cut",))
    assert "rough_cut" in str(exc.value)
    assert "Known steps" in str(exc.value)






# ── An unreachable breakpoint is reported, never silent ──────────────



def test_an_unreachable_breakpoint_does_not_refuse_the_run(steps):
    """A breakpoint strands no consumer, so refusing would make
    `--profile podcast --only catalog` impossible for no gain."""
    gates = _resolve(steps, break_at=("render",))
    assert gates.any_armed




# ── The record a later reader gets ───────────────────────────────────



# ── The two CLIs cannot drift ────────────────────────────────────────

def test_both_clis_register_the_same_flags():
    import argparse

    wrapper, runner = argparse.ArgumentParser(), argparse.ArgumentParser()
    breakpoints.add_breakpoint_arguments(wrapper)
    breakpoints.add_breakpoint_arguments(runner)
    for parser in (wrapper, runner):
        args = parser.parse_args(["--break", "a", "--no-break", "b"])
        assert args.break_at == ["a"]
        assert args.no_break_at == ["b"]




def test_the_resume_command_drops_a_rerun_that_already_happened():
    """Found by driving the loop on 001. `--rerun` clears a ledger entry,
    so carrying it into the resume clears the entry the pause just wrote,
    re-runs the step, re-arms its breakpoint and stops in the same place
    - a loop the captain cannot get out of by following the instruction
    the pipeline printed."""
    argv = ["run_pipeline.py", "--project", "/p",
            "--rerun", "scan", "--rerun", "catalog", "--break", "scan"]
    command = breakpoints.resume_command(argv, "python3")
    assert "--rerun" not in command
    assert "scan" in command, "the breakpoint that armed the pause survives"
    assert "--break scan" in command
    assert command.endswith("--resume")
    # `--rerun=scan` is the same request spelled with an equals sign.
    assert "--rerun" not in breakpoints.resume_command(
        ["run.py", "--rerun=scan", "--break", "scan"], "python3")


# ── A breakpoint may name an OPERATION, at a region ─────────────────


OPS = set(operations.names())
ONE = sorted(OPS)[0]


def test_a_breakpoint_may_be_an_operation_address():
    gates = breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                       break_at=(f"{ONE}@45.0-72.0",))
    assert gates.armed_at(f"{ONE}@45.0-72.0")
    assert not gates.armed_at("render")




def test_a_region_breakpoint_does_not_arm_the_whole_operation():
    """The other direction must NOT hold, or a region breakpoint is a
    whole-operation one wearing a disguise."""
    gates = breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                       break_at=(f"{ONE}@45.0-72.0",))
    assert not gates.armed_at(ONE)
    assert not gates.armed_at(f"{ONE}@0.0-1.0")




def test_an_unknown_operation_is_refused_by_name():
    with pytest.raises(BreakpointError) as exc:
        breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                   break_at=("nope.jog@1.0-2.0",))
    assert "nope.jog" in str(exc.value)
    assert ONE in str(exc.value), "the known operations are listed"


def test_a_malformed_region_is_refused_by_the_one_parser():
    """`region.parse` owns what an interval is; this does not re-parse
    it, so a bad span is refused in that module's own words."""
    with pytest.raises(BreakpointError) as exc:
        breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                   break_at=(f"{ONE}@notaspan",))
    assert "notaspan" in str(exc.value)




