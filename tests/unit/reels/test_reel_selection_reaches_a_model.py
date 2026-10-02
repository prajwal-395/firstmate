"""Reel selection reaches a MODEL, and says which craft it is.

Every guard aims at the ENGINE inventing taste; these look the other way
and ask whether a model is actually reached. History:
docs/evidence/select_reels.md.
"""

from __future__ import annotations

import pathlib

from library.tools import craft_role
from library.tools.undetermined import DECLARING_STEPS

REPO = pathlib.Path(__file__).resolve().parents[3]


# ── The step reaches a model ─────────────────────────────────────────

def test_reel_selection_is_a_declared_model_step_with_a_neutral_role():
    """The question that would have caught this on the first batch:
    which model chose, and where is its handoff? The role hands over
    capability and authority, never a count, a strength or a direction."""
    assert "select_reels" in DECLARING_STEPS
    assert craft_role.declares("select_reels")
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


# ── The step can actually RUN ────────────────────────────────────────

STEP_DIR = REPO / "library/steps/step_3_04_select_reels"


def test_the_bridge_publishes_the_tables_the_handoff_names():
    """A handoff naming a table the bridge does not build is a prompt
    describing something that never arrives."""
    import json

    from library.steps.step_3_04_select_reels.bridge import build_context

    # a step that cannot execute is not a step: no handoff means no
    # System Context and nothing for the role to prepend to
    for name in ("manifest.json", "handoff.md", "bridge.py", "post_bridge.py"):
        assert (STEP_DIR / name).is_file(), f"select_reels has no {name}"
    manifest = json.loads((STEP_DIR / "manifest.json").read_text())
    # The manifest's TOP LEVEL, which is where the projection reads it.
    # This assertion used to name `interface`, which is where the
    # declaration sat and where nothing read it - so it passed while the
    # projection never ran. See tests/contracts/test_context_fields_binds.py.
    assert "reel_candidates" in " ".join(manifest["context_fields"])

    context = build_context({"timeline_transcript": {"segments": []}})
    for table in ("turns", "reel_candidates"):
        assert table in context, f"the handoff names {table!r}; the bridge must build it"


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
