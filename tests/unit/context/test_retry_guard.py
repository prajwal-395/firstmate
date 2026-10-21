"""PERF-011: the refusal that already happened is remembered, once.

A lane that refuses a step and is re-run pays for the same decision
twice. The memo in `library/tools/retry_guard.py` is what stops the
second payment: a refusal is recorded against the identity of the state
it was decided on, and the next attempt on that identity is refused
immediately, with the cause named.

The defects these tests name:

  * a refusal that is NOT recorded is paid for again on the next run;
  * a refusal recorded against a CHANGED state refuses a step that
    would now succeed;
  * a refusal with no cause or no fix is recorded anyway, and the next
    attempt cannot say why it refused;
  * a memo that outlives its cause refuses a step that now works.
"""
from __future__ import annotations

from library.tools import retry_guard
from library.tools.ren_refusal import RenRefusal


def _refusal(code: str = "REN_TEST_REFUSED") -> RenRefusal:
    return RenRefusal(
        what="the plan names an overlay that is not on disk",
        why="the overlay segment was planned and never rendered",
        fix="render the overlay, then re-run the step",
        code=code, blocker="missing_media")


def test_a_refusal_is_remembered_against_the_identity_it_was_decided_on():
    state = {}

    assert retry_guard.record(state, "compile_manifest", "key-1",
                              _refusal(), seconds=12.5)

    memo = retry_guard.repeated(state, "compile_manifest", "key-1")
    assert memo is not None
    assert memo["code"] == "REN_TEST_REFUSED"
    assert memo["identity"] == "key-1"
    assert memo["seconds"] == 12.5
    assert "overlay" in memo["what"]


def test_the_same_refusal_on_another_state_is_not_the_same_refusal():
    state = {}
    retry_guard.record(state, "compile_manifest", "key-1", _refusal())

    assert retry_guard.repeated(state, "compile_manifest", "key-2") is None


def test_a_refusal_without_a_witness_is_not_recorded():
    state = {}

    assert not retry_guard.record(state, "compile_manifest", "",
                                  _refusal())
    assert retry_guard.entry(state, "compile_manifest") is None


def test_a_refusal_without_a_fix_is_not_recorded():
    state = {}
    broken = {"code": "REN_TEST_REFUSED", "what": "something", "why": "why"}

    assert not retry_guard.record(state, "compile_manifest", "key-1", broken)
    assert retry_guard.entry(state, "compile_manifest") is None


def test_the_repeat_names_the_cause_and_the_way_past_it():
    state = {}
    retry_guard.record(state, "compile_manifest", "key-1", _refusal(),
                       seconds=12.5)
    memo = retry_guard.repeated(state, "compile_manifest", "key-1")

    refusal = retry_guard.refusal_for(
        "compile_manifest", memo,
        rerun="ren edit demo --rerun compile_manifest")

    assert refusal.code == "REN_REPEATED_REFUSAL"
    # what, why and fix: the refusal shape the boundary renders.
    assert "overlay" in refusal.what
    assert "render the overlay" in refusal.fix
    assert "--rerun compile_manifest" in refusal.fix
    # The cost it did not pay again.
    assert "12.5s" in refusal.why


def test_a_memo_that_outlived_its_cause_stops_refusing():
    state = {}
    retry_guard.record(state, "compile_manifest", "key-1", _refusal())

    retry_guard.forget(state, "compile_manifest")

    assert retry_guard.repeated(state, "compile_manifest", "key-1") is None


def test_a_record_in_another_format_is_a_miss_not_a_match():
    state = {"retry_guard": {"compile_manifest": {
        "format": "retry_guard/0", "identity": "key-1",
        "code": "REN_TEST_REFUSED", "what": "w", "why": "y", "fix": "f"}}}

    assert retry_guard.repeated(state, "compile_manifest", "key-1") is None


def test_the_memo_keeps_the_most_recent_refusals():
    state = {}
    for index in range(retry_guard.MAX_ENTRIES + 5):
        retry_guard.record(state, f"step-{index}", "key-1", _refusal())

    memo = state[retry_guard.STATE_KEY]
    assert len(memo) == retry_guard.MAX_ENTRIES
    assert "step-0" not in memo
    assert f"step-{retry_guard.MAX_ENTRIES + 4}" in memo
