"""A step with no creative brief ASKS, rather than planning in silence.

The captain, 2026-09-02: a declined brief should prompt the LLM to ask
briefing questions. Membership is derived from the manifests
(`briefing_interview._steps_declaring_the_brief`); the collector keeps one
record per attempt and the final one per step is the reading.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import brief_attachment as ba  # noqa: E402
from library.tools import briefing_interview as bi  # noqa: E402


def test_select_reels_is_interviewed_without_a_brief():
    """The reel selector ASKS rather than selecting in silence.

    This landed once (89c61e6, the field-test lane) and was lost when
    that lane was never merged, so main shipped a reel selector that
    received no brief and was never asked what it would have wanted told.
    Nineteen reels were approved off a run that HAD the brief, from a
    worktree, which is why nothing downstream noticed.
    """
    assert bi.asks("select_reels", brief_attached=False) is True
    assert bi.asks("select_reels", brief_attached=True) is False


# ── The collector's two rules, shared with both siblings ─────────────

def test_one_record_per_attempt_numbered_and_final_by_step_is_the_reading():
    bi.reset()
    for i in range(3):
        bi.record(bi.Interview("mesh_spine", bi.ASKED,
                               [{"question": f"q{i}"}]))
    assert [i.attempt for i in bi.collected()] == [1, 2, 3]
    final = bi.final_by_step()
    assert len(final) == 1 and final[0].attempt == 3
    assert "3 attempts" in "\n".join(bi.summary_lines())


def test_a_step_this_run_did_not_reach_keeps_its_rows_and_is_marked():
    previous = [{"step_id": "plan_sfx", "reading": bi.ASKED, "entries": [],
                 "attempt": 1},
                {"step_id": "plan_vfx", "reading": bi.ASKED, "entries": [],
                 "attempt": 1}]
    current = [{"step_id": "plan_vfx", "reading": bi.NOTHING_TO_ASK,
                "entries": [], "attempt": 1}]
    merged = bi.merge_records(previous, current)
    by_step = {row["step_id"]: row for row in merged}
    assert by_step["plan_sfx"]["from_a_previous_run"] is True
    assert "from_a_previous_run" not in by_step["plan_vfx"]
    assert by_step["plan_vfx"]["reading"] == bi.NOTHING_TO_ASK


# ── The two modules agree about what "not attached" means ────────────

def test_every_not_attached_reading_leads_to_an_interview():
    for body in ({}, {"attach_creative_brief": False,
                      "creative_brief": "b.md"}):
        attachment = ba.read_declaration_from(body)
        assert attachment.interview
        assert bi.asks("plan_vfx", attachment.attached)
    attached = ba.read_declaration_from({"creative_brief": "b.md"})
    assert not attached.interview
    assert not bi.asks("plan_vfx", attached.attached)
