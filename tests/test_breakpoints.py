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


def test_a_profile_may_say_every_step_as_data(steps):
    """`--review` and `*` are one thing said two ways, so a profile can
    express exactly what the flag expresses."""
    from_data = _resolve(steps, profile_breakpoints=(EVERY_STEP,),
                         profile_name="all")
    from_flag = _resolve(steps, review_all=True)
    assert from_data.every_step is from_flag.every_step is True
    assert from_data.steps == from_flag.steps == ()
    assert any("all" in line for line in from_data.basis)


# ── Per step ─────────────────────────────────────────────────────────

def test_one_step_is_armed_and_nothing_else(steps):
    gates = _resolve(steps, break_at=("review_rough_cut",))
    assert gates.armed_at("review_rough_cut")
    assert not gates.armed_at("catalog")
    assert not gates.every_step


def test_a_profile_arms_and_the_flag_adds_to_it(steps):
    gates = _resolve(steps, profile_breakpoints=("review_rough_cut",),
                     break_at=("catalog",), profile_name="podcast")
    assert gates.steps == ("review_rough_cut", "catalog")
    assert any("podcast" in line for line in gates.basis)
    assert any("--break" in line for line in gates.basis)


def test_no_break_disarms_one_the_profile_armed(steps):
    gates = _resolve(steps, profile_breakpoints=("review_rough_cut", "catalog"),
                     no_break_at=("review_rough_cut",), profile_name="podcast")
    assert gates.steps == ("catalog",)
    assert not gates.armed_at("review_rough_cut")


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


def test_an_unknown_step_in_no_break_is_refused_by_name(steps):
    with pytest.raises(BreakpointError) as exc:
        _resolve(steps, no_break_at=("nope",))
    assert "--no-break" in str(exc.value)


def test_the_same_step_armed_and_disarmed_is_refused(steps):
    with pytest.raises(BreakpointError) as exc:
        _resolve(steps, break_at=("catalog",), no_break_at=("catalog",))
    assert "Say it once" in str(exc.value)


# ── An unreachable breakpoint is reported, never silent ──────────────

def test_a_breakpoint_the_run_will_not_reach_is_named(steps):
    gates = _resolve(steps, break_at=("render",))
    running = ["scan", "catalog"]
    assert gates.unreachable(running) == ("render",)
    lines = gates.describe(running)
    assert any("will NOT stop there" in line for line in lines)
    assert any("render" in line for line in lines)


def test_an_unreachable_breakpoint_does_not_refuse_the_run(steps):
    """A breakpoint strands no consumer, so refusing would make
    `--profile podcast --only catalog` impossible for no gain."""
    gates = _resolve(steps, break_at=("render",))
    assert gates.any_armed


def test_every_step_is_never_unreachable(steps):
    gates = _resolve(steps, review_all=True)
    assert gates.unreachable(["scan"]) == ()


# ── The record a later reader gets ───────────────────────────────────

def test_the_record_says_where_it_stops_and_where_it_cannot(steps):
    gates = _resolve(steps, break_at=("catalog", "render"))
    record = gates.as_record(["scan", "catalog"])
    assert record["steps"] == ["catalog", "render"]
    assert record["unreachable"] == ["render"]
    assert record["every_step"] is False
    assert record["basis"]


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


def test_the_resume_command_is_this_runs_own_argv_plus_resume():
    """A breakpoint is armed per RUN, so a resume without the flags that
    armed it sails past the next one. The pause prints the command."""
    argv = ["run_pipeline.py", "--project", "/p", "--profile", "podcast"]
    command = breakpoints.resume_command(argv, "python3")
    assert command.endswith("--resume")
    assert "--profile podcast" in command
    assert command.count("--resume") == 1
    # Idempotent: resuming a resumed run does not double the flag.
    assert breakpoints.resume_command(argv + ["--resume"], "python3") == command


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


def test_a_bare_operation_arms_every_region_of_itself():
    """The same "a wildcard is said as data" idea EVERY_STEP already is,
    one level narrower."""
    gates = breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                       break_at=(ONE,))
    assert gates.armed_at(ONE)
    assert gates.armed_at(f"{ONE}@45.0-72.0")
    assert gates.armed_at(f"{ONE}@0.0-1.0")


def test_a_region_breakpoint_does_not_arm_the_whole_operation():
    """The other direction must NOT hold, or a region breakpoint is a
    whole-operation one wearing a disguise."""
    gates = breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                       break_at=(f"{ONE}@45.0-72.0",))
    assert not gates.armed_at(ONE)
    assert not gates.armed_at(f"{ONE}@0.0-1.0")


def test_a_step_id_is_never_read_as_an_operation():
    """A DAG node never contains the separator, so nothing guesses."""
    gates = breakpoints.resolve(known_steps={"render", "scan"}, known_operations=OPS,
                       break_at=("render",))
    assert gates.armed_at("render")
    assert not gates.armed_at("scan")


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


def test_the_refusal_names_both_namespaces():
    with pytest.raises(BreakpointError) as exc:
        breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                   break_at=("mystery",))
    message = str(exc.value)
    assert "Known steps:" in message
    assert "Known operations:" in message
    assert "<start>-<end>" in message


def test_operation_addresses_compose_with_everything_else():
    """Nothing about the algebra changes: profiles, --no-break, the
    unreachable warning and resume_command all still work on them."""
    gates = breakpoints.resolve(known_steps={"render"}, known_operations=OPS,
                       profile_breakpoints=(f"{ONE}@0.0-1.0",),
                       break_at=(f"{ONE}@45.0-72.0",),
                       no_break_at=(f"{ONE}@0.0-1.0",),
                       profile_name="podcast")
    assert gates.armed_at(f"{ONE}@45.0-72.0")
    assert not gates.armed_at(f"{ONE}@0.0-1.0"), "--no-break disarmed it"
    assert f"{ONE}@45.0-72.0" in gates.unreachable(["render"])
    assert "--resume" in breakpoints.resume_command(
        ["run_pipeline.py", "--break", f"{ONE}@45.0-72.0"], "python3")
