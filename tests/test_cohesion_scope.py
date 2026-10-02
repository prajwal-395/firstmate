"""A cohesion finding is applicable where it runs, or it is an observation.

Every ACTIONABLE pair is really applied by `apply_cohesion_adjustments`,
every OWNED_UPSTREAM pair is really refused with its own sentence, and an
undeclared pair raises. History: docs/evidence/creative_cohesion.md.
"""
import pytest

from library.steps.step_5_03_creative_cohesion.step import review_creative_cohesion
from library.steps.step_5_04_compile_manifest.step import apply_cohesion_adjustments
from library.tools import cohesion_scope
from library.tools.cohesion_scope import (
    ACTIONABLE_AT_COHESION,
    OWNED_UPSTREAM,
    UnknownCohesionFinding,
)


def test_every_declared_pair_is_applied_or_refused_as_declared():
    """Drive the real applier for every pair in both lists. A pair
    declared actionable that the compiler has no branch for, or an upstream
    entry the compiler would in fact apply (a stale refusal), fails here -
    and a pair in both lists cannot pass both halves."""
    for target, field in sorted(ACTIONABLE_AT_COHESION):
        record = apply_cohesion_adjustments(
            [{"transition_type": "defocus", "duration_frames": 15}],
            {"adjustments": [{"target_step": target, "field": field,
                              "suggested_value": 10, "target_index": 0}]})
        assert record["not_applied"] == [], (
            f"{target}.{field} is declared actionable at 5.03, and "
            f"apply_cohesion_adjustments refused it")
        assert len(record["applied"]) == 1
    for target, field in sorted(OWNED_UPSTREAM):
        record = apply_cohesion_adjustments(
            [{"transition_type": "defocus", "duration_frames": 15}],
            {"adjustments": [{"target_step": target, "field": field,
                              "suggested_value": "whatever",
                              "target_index": 0}]})
        assert record["applied"] == [], (target, field)
        assert record["not_applied"][0]["reason"] == OWNED_UPSTREAM[
            (target, field)].reason


def test_an_undeclared_pair_raises_rather_than_defaulting():
    with pytest.raises(UnknownCohesionFinding):
        cohesion_scope.owner_of("something_new", "whatever")
    with pytest.raises(UnknownCohesionFinding):
        cohesion_scope.split([{"target_step": "something_new",
                               "field": "whatever"}])


def test_the_compiler_still_refuses_an_undeclared_pair_out_loud():
    """The compiler is the last line, so it never raises on one - a
    cohesion_review from an older run must be refused, not crash the
    compile."""
    record = apply_cohesion_adjustments([], {"adjustments": [{
        "target_step": "something_new", "field": "whatever",
        "suggested_value": 1}]})
    assert record["applied"] == []
    assert record["not_applied"][0]["reason"]


# ── The step's own output obeys the split ────────────────────────────

SPINE_60S = {"structure": [
    {"block_type": "speech", "position": 0,
     "timeline_start": 0.0, "timeline_end": 60.0}]}


def _review_with_every_finding():
    """One input that fires every finding the step can still make.

    The sequence opens on a passage the model ranked third and its
    strongest is last - two ORDERINGS disagreeing, which is the whole of
    what the engagement observation reads since the closed role
    vocabulary and the 0-100 composite were withdrawn (captain,
    2026-09-02).
    """
    return review_creative_cohesion({
        "creative_direction": {"target_energy": "high"},
        "transition_spec": [
            {"transition_type": "defocus", "duration_frames": 30}],
        "sfx_spec": [],
        "audio_spine": SPINE_60S,
        "speech_sequence": {
            "body_sequence": [
                {"clip_id": "clip_002", "source_start": 1.0,
                 "source_end": 3.0,
                 "engagement": {"rank": 3, "basis": "a tease"}},
                {"clip_id": "clip_016", "source_start": 48.065,
                 "source_end": 52.0,
                 "engagement": {"rank": 1,
                                "basis": "the floor of the piece"}}],
        },
    })


def test_the_step_is_a_pure_observer():
    """5.03 proposes nothing ACTIONABLE, at any energy - not just at 001's.

    Issue #272 asked whether the actionable set was too narrow for a
    moderate edit. The answer this pins is stronger: every proposal the
    step can make is routed to `OWNED_UPSTREAM`, so `adjustments` is
    empty for every input, and the step changes nothing where it runs.

    Read off the SOURCE rather than by running fixtures, because "no
    input produces one" is not something a fixture can establish. Adding
    a proposal that targets an ACTIONABLE pair fails here, which is the
    signal that #272 has been answered differently and that
    cohesion_scope's docstring and AGENTS.md 10.5 need updating with it.
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "library" / "steps"
           / "step_5_03_creative_cohesion" / "step.py").read_text(encoding="utf-8")
    targets = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "proposals"):
            continue
        (payload,) = node.args
        assert isinstance(payload, ast.Dict), (
            "a proposal built somewhere other than a literal - this test "
            "can no longer read what it targets")
        fields = {k.value: v for k, v in zip(payload.keys, payload.values)
                  if isinstance(k, ast.Constant)}
        targets.append((fields["target_step"].value, fields["field"].value))

    assert targets, "the step proposes nothing at all; it used to observe"
    actionable = [t for t in targets if t in ACTIONABLE_AT_COHESION]
    assert actionable == [], (
        f"step 5.03 proposes {actionable}, which the compiler applies. "
        f"It has been a pure observer since the four energy thresholds "
        f"were removed - update library/tools/cohesion_scope.py, "
        f"AGENTS.md 10.5 and issue #272 along with this.")
    for target in targets:
        assert target in OWNED_UPSTREAM, target


def test_the_findings_it_cannot_apply_are_still_reported():
    """Rescoping must not be a way to go quiet: the upstream-owned
    finding is still in `warnings` and in `observations`."""
    review = _review_with_every_finding()
    owners = {o["state_key"]: o for o in review["observations"]}
    # `sfx_spec` was the second entry, produced by the ">= 10 SFX per
    # minute for a high energy edit" floor. The floor is gone; the
    # OWNED_UPSTREAM entry stays, because it is the guard.
    assert set(owners) == {"speech_sequence"}
    for observation in owners.values():
        assert observation["finding"] in review["warnings"]
        assert observation["owner_step"]
        assert observation["reason"]
        assert observation["how_to_act"]
