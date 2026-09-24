"""Reel selection reaches a MODEL, and says which craft it is.

The defect these tests were written against, measured 2026-09-04: reel
selection was `reel_proposal.py` plus `reel_exchange.py`, which between
them had no model call, no handoff and no craft role, and was absent from
`undetermined.DECLARING_STEPS`. A crewmate chose the sixteen
conversations in its own turn and committed a validator around the
result.

The root cause is the part worth keeping: **every guard in this engine
aims at the ENGINE inventing taste and none at a WORKER supplying it**,
so a judgement that arrives already made passes every gate. These tests
are the one that looks the other way - they ask whether a model is
actually reached, not whether the engine holds a constant.

An earlier version of this file used `xfail` for the unbuilt fix.
`tests/test_no_unfailable_tests.py` refused it, correctly: an xfail for
work nobody has started cannot fail. Section 15 of
`docs/FIELD_TEST_PODCAST_FINDINGS.md` carries the specification.
"""

from __future__ import annotations

import pathlib

from library.tools import craft_role
from library.tools.undetermined import DECLARING_STEPS

REPO = pathlib.Path(__file__).resolve().parents[1]
FINDINGS = REPO / "docs/FIELD_TEST_PODCAST_FINDINGS.md"


def _findings_text() -> str:
    """Whitespace-normalised: the document is hard-wrapped and a phrase
    that matters can straddle a line break."""
    return " ".join(FINDINGS.read_text().split())


# ── The step reaches a model ─────────────────────────────────────────

def test_reel_selection_is_a_declared_model_step():
    """The question that would have caught this on the first batch:
    which model chose, and where is its handoff?"""
    assert "select_reels" in DECLARING_STEPS


def test_it_is_told_which_craft_it_is():
    assert craft_role.declares("select_reels")
    assert craft_role.role_for("select_reels").discipline == "short-form editor"


def test_the_role_hands_over_authority_and_no_taste():
    """craft_role's own contract: capability and authority, never a
    count, a strength or a direction."""
    role = craft_role.role_for("select_reels")
    block = craft_role.prompt_block("select_reels")
    assert role.decides and role.defers
    assert craft_role.NEUTRALITY_LINE in block
    lowered = block.lower()
    for forbidden in ("pick 16", "choose 16", "at least 20", "aim for"):
        assert forbidden not in lowered, f"the role states taste: {forbidden!r}"


# ── Measurements are context, not gates ──────────────────────────────

def test_a_closing_pitch_is_never_a_reason_to_withhold_a_candidate():
    from library.tools.reel_exchange import Turn, exchange_windows

    turns = [Turn("Craig", 0.0, 20.0, "so why does that matter to a business"),
             Turn("Akshita", 21.0, 40.0, "because AI cannot describe you"),
             Turn("Craig", 41.0, 55.0,
                  "go check out the lucy visibility score on our website")]
    window = exchange_windows(turns, "Craig", "Akshita")[0]
    assert not any("pitch" in c for c in window.concerns)
    assert window.measurements()["closes_on_pitch"] is True


def test_a_long_story_is_offered_with_its_length_not_truncated():
    from library.tools.reel_exchange import Turn, exchange_windows

    turns = [Turn("Craig", 0.0, 10.0, "what is the story here"),
             Turn("Akshita", 11.0, 100.0, "a long answer that lands late")]
    windows = exchange_windows(turns, "Craig", "Akshita")
    assert windows, "a 100s exchange must be offered, not withheld"
    assert windows[0].measurements()["within_length_guidance"] is False


# ── The understanding outlives the worker that had it ────────────────


# ── The step can actually RUN ────────────────────────────────────────
#
# Caught in review, and it is the same shape as the defect it was built
# to fix: step_3_04_select_reels was registered in DECLARING_STEPS, given
# a craft role, and asserted in its own docs to reach a model - while the
# directory held nothing but manifest.json. No handoff means no System
# Context and no Task Prompt, so the role had nothing to prepend to and
# nothing told the model what it was doing.

STEP_DIR = REPO / "library/steps/step_3_04_select_reels"


def test_the_step_carries_a_handoff_and_a_bridge():
    """A step that cannot execute is not a step."""
    for name in ("manifest.json", "handoff.md", "bridge.py", "post_bridge.py"):
        assert (STEP_DIR / name).is_file(), f"select_reels has no {name}"


def test_the_handoff_states_the_captains_definition_of_a_reel():
    handoff = " ".join((STEP_DIR / "handoff.md").read_text().split())
    assert ("atomic segment of conversation that provides value and then "
            "closes with a small call to action") in handoff


def test_the_handoff_names_the_measurements_as_context_not_scores():
    handoff = " ".join((STEP_DIR / "handoff.md").read_text().split())
    assert "These are measurements, not scores" in handoff
    assert "nothing has been ranked or filtered for you" in handoff


def test_the_bridge_publishes_the_tables_the_handoff_names():
    """A handoff naming a table the bridge does not build is a prompt
    describing something that never arrives."""
    import json

    from library.steps.step_3_04_select_reels.bridge import build_context

    manifest = json.loads((STEP_DIR / "manifest.json").read_text())
    # The manifest's TOP LEVEL, which is where the projection reads it.
    # This assertion used to name `interface`, which is where the
    # declaration sat and where nothing read it - so it passed while the
    # projection never ran. See tests/test_context_fields_binds.py.
    assert "reel_candidates" in " ".join(manifest["context_fields"])

    context = build_context({"timeline_transcript": {"segments": []}})
    for table in ("turns", "reel_candidates"):
        assert table in context, f"the handoff names {table!r}; the bridge must build it"


def test_a_transcript_with_one_speaker_says_so_rather_than_guessing():
    from library.steps.step_3_04_select_reels.bridge import build_context

    context = build_context({"timeline_transcript": {"segments": [{
        "speaker": "Akshita", "text": "one two three four five",
        "timeline_start": 0.0, "timeline_end": 5.0,
        "resolve_item_id": "u", "source_file": "/m/a.MXF",
        "source_start": 0.0, "source_end": 5.0}]}})
    assert context["reel_candidates"] == []
    assert context["undetermined"], "a reel is a conversation - say so"


def test_the_post_bridge_leaves_every_moment_proposed():
    from library.tools.reel_proposal import Approval, read_proposal  # noqa
    from library.steps.step_3_04_select_reels.post_bridge import resolve

    seg1 = {"speaker": "Craig", "text": "a line about the topic here",
            "timeline_start": 10.0, "timeline_end": 30.0,
            "resolve_item_id": "u", "source_file": "/m/a.MXF",
            "source_start": 10.0, "source_end": 30.0}
    seg2 = {"speaker": "Akshita", "text": "and here is the response",
            "timeline_start": 30.5, "timeline_end": 55.0,
            "resolve_item_id": "u2", "source_file": "/m/b.MXF",
            "source_start": 30.5, "source_end": 55.0}
    out = resolve({"moments": [{"start": 12.0, "end": 55.0, "slug": "x",
                                "reason": "because it lands"}]},
                  {"timeline_transcript": {"segments": [seg1, seg2],
                                           "derived_from": {"duration_seconds": 600.0}}})
    moments = out["reel_selection"]["moments"]
    assert moments and all(m["approval"] == "proposed" for m in moments)


# ── The format, as the captain defined it ────────────────────────────
#
# These pin the parts of the definition that were ANSWERED rather than
# inferred. Each one was missing at some point and cost a review round:
# the opening was never mentioned to the model at all, the transcript
# being garbled was read as a bad reel rather than a bad passage, and
# both the CTA and overlap rules had been written as prohibitions the
# captain does not hold.


def _handoff() -> str:
    return " ".join((STEP_DIR / "handoff.md").read_text().split()).lower()


def test_the_handoff_judges_overlap_on_meaning_not_seconds():
    """"they can as long as its not like the exact same video". A
    seconds threshold is the mechanical proxy that was tested against his
    verdicts and failed to predict them."""
    handoff = _handoff()
    assert "say different things" in handoff
    assert "no seconds threshold" in handoff
