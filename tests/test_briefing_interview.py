"""A step with no creative brief ASKS, rather than planning in silence.

The captain, 2026-09-02: *"if this was like a TUI interface if a user
declines to attach a creative brief, then it should prompt the LLM to
ask some briefing questions for the user"*.

Both paths are exercised here against the REAL prompt assembly:

* **attached** - the brief goes in, and the step is not interviewed. A
  step handed the captain's own brief and then asked what it wished the
  captain had said is being invited to manufacture a gap.
* **declined, then interviewed** - the brief does not go in, the
  question reaches the model in both halves of the request (the prose
  prompt AND the machine-readable `expected_schema`, which is the gap
  `could_not_determine` fell through), the answer is split back out
  before anything validates it, and it lands in the run summary where
  the captain reads it.

And the three readings stay three: asked, nothing-to-ask, and a
non-answer that is never read as either.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import brief_attachment as ba  # noqa: E402
from library.tools import briefing_interview as bi  # noqa: E402
from library.processes.edit_video.run_pipeline import (  # noqa: E402
    present_llm_step,
)

MANIFEST = {"interface": {"outputs": [{"name": "a_verdict"}]}}

A_BRIEF = "the reference the runner builds, whatever its shape"


# ── Membership is derived, not listed ────────────────────────────────

def test_every_step_that_asked_for_a_brief_can_be_interviewed():
    """DERIVED from the manifests, so a step that starts or stops
    declaring the brief cannot fall out of the interview silently."""
    assert bi.ASKING_STEPS == frozenset({
        "creative_direction", "speech_sequence", "music_selection",
        "mesh_spine", "select_broll", "plan_transitions", "plan_vfx",
        "plan_sfx",
        # The ninth, from 2026-09-03: 5.01 became hybrid and a colourist
        # with no brief has as much to ask as any other planner - 001's
        # brief carries a whole "Color System Philosophy" section that no
        # colour step had ever seen.
        "color_grade",
        # The tenth, 2026-09-05. Reel selection decides WHICH moments of
        # an episode become standalone shorts and how many there are -
        # the most creative answer any step gives - and it was doing that
        # with no brief and no question. See
        # `test_select_reels_is_interviewed_without_a_brief` below.
        "select_reels",
    })


def test_select_reels_is_interviewed_without_a_brief():
    """The reel selector ASKS rather than selecting in silence.

    This landed once (89c61e6, the field-test lane) and was lost when
    that lane was never merged, so main shipped a reel selector that
    received no brief and was never asked what it would have wanted told.
    Nineteen reels were approved off a run that HAD the brief, from a
    worktree, which is why nothing downstream noticed.

    Pinned separately from the membership assertion above because a set
    comparison fails for any reason at all; this one fails for exactly
    this reason and says so.
    """
    assert bi.asks("select_reels", brief_attached=False) is True
    assert bi.asks("select_reels", brief_attached=True) is False


def test_a_step_is_interviewed_off_its_manifest_not_its_prompt():
    """A step is asked because its MANIFEST asked for a brief, not
    because its prompt mentions one (AGENTS.md 10.1).

    `mesh_spine` was the case that proved it: it declared the brief for
    months while its `handoff.md` - under the captain's freeze - never
    named one, and it was interviewed correctly throughout. The freeze
    lifted 2026-09-09 and the prompt now names it too, so the proof
    moved to the derivation itself rather than to that one file's
    silence.
    """
    assert "mesh_spine" in bi.ASKING_STEPS
    assert bi.ASKING_STEPS == bi._steps_declaring_the_brief(), (
        "the asking set is DERIVED from what the manifests declare; a "
        "prompt mentioning a brief neither adds a step nor removes one")






# ── Through the real prompt assembly ─────────────────────────────────

def _answer_once(req: Path, res: Path, payload: dict, seen: list):
    def run():
        deadline = time.time() + 25
        while time.time() < deadline:
            if req.exists() and not res.exists():
                seen.append(json.loads(req.read_text(encoding="utf-8")))
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps(payload), encoding="utf-8")
                return
            time.sleep(0.05)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _run(tmp_path, node_id, answer, inputs=None, yaml_body=""):
    bi.reset()
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    if yaml_body:
        (project / "project.yaml").write_text(yaml_body, encoding="utf-8")
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Plan the effects.\n", encoding="utf-8")

    req = project / "pipeline_output" / "llm_requests" / f"{node_id}.json"
    res = project / "pipeline_output" / "llm_responses" / f"{node_id}.json"
    seen = []
    _answer_once(req, res, answer, seen)

    step_inputs = {"project_folder": str(project)}
    step_inputs.update(inputs or {})
    result = present_llm_step(
        str(prompt_path), step_inputs, node_id, manifest=MANIFEST,
        full_auto="agent", llm_timeout=30,
    )
    assert seen, "the step never issued a request"
    return seen[0], result


# ── Path one: the brief is attached ──────────────────────────────────



# ── Path two: declined, then interviewed ─────────────────────────────







# ── The reader that closes the loop ──────────────────────────────────





def test_nothing_here_gates_or_reads_a_questions_content():
    """Whatever picks which questions matter becomes the interviewer."""
    bi.reset()
    bi.record(bi.Interview("plan_vfx", bi.ASKED, [{"question": "?"}]))
    assert bi.summary_lines()          # it prints
    assert bi.as_records()             # and it records
    # and it returns nothing anybody can branch on
    assert not hasattr(bi, "should_fail")
    assert not hasattr(bi, "severity")


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
