"""F-09: the pipeline's own events file learnings, not just the CLI.

A rejection that is not manually filed is forgotten at run end: the
next run re-attempts the same step with no memory of why it was
refused. The three events - a gate rejection, a post-bridge
exhaustion, a signed-off reel - each file a learning through
`learned_context`, best-effort, so a learning write never fails the
answer being recorded.
"""
import sys
from pathlib import Path

from library.tools import learned_context

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def test_a_gate_rejection_files_a_correction_the_refused_step_reads(tmp_path):
    """The captain's reason becomes the learning's statement, and the
    next run of the refused step reads it in its prompt - the memory
    the run used to lack."""
    learning = learned_context.record_gate_rejection(
        str(tmp_path), "plan_transitions",
        "No crash zooms on eating shots.")
    assert learning is not None
    assert learning["kind"] == learned_context.CORRECTION
    assert learning["statement"] == "No crash zooms on eating shots."
    assert learning["read_by"] == ["plan_transitions"]
    assert learning["source"]["gate"] == "plan_transitions"
    rendered = learned_context.render_for_prompt(
        str(tmp_path), "plan_transitions")
    assert "No crash zooms on eating shots." in rendered
    # A step that is not the reader does not see it.
    assert learned_context.render_for_prompt(
        str(tmp_path), "music_selection") == ""


def test_a_gate_rejection_in_the_directory_vocabulary_routes_to_its_node(
        tmp_path):
    """A gate id spelled the step's own way (`step_3_04_select_reels`)
    routes to the node that step is - the reader check accepts one
    vocabulary, so the routing translates before it asks."""
    learning = learned_context.record_gate_rejection(
        str(tmp_path), "step_3_04_select_reels",
        "the plan invents a timecode nobody spoke")
    assert learning is not None
    assert learning["read_by"] == ["select_reels"]
    assert "invents a timecode" in learned_context.render_for_prompt(
        str(tmp_path), "select_reels")


def test_an_operation_address_gate_rejection_routes_to_the_owning_step(
        tmp_path):
    """A gate answered at an operation address
    (`subtitles.render@45.0-72.0`) is read by the step that runs that
    operation, reached through the operations registry."""
    learning = learned_context.record_gate_rejection(
        str(tmp_path), "subtitles.render@45.0-72.0",
        "captions too aggressive on the reveal")
    assert learning is not None
    assert learning["read_by"] == ["render_subtitles"]
    assert "too aggressive" in learned_context.render_for_prompt(
        str(tmp_path), "render_subtitles")


def test_an_unroutable_gate_rejection_files_nothing(tmp_path):
    """A gate id that names no known step and no registered operation
    determines no reader, and a learning with no reader is still
    refused (learned_context.py:242-249) - the event path routes, it
    never invents a reader."""
    assert learned_context.record_gate_rejection(
        str(tmp_path), "bogus@1.0-2.0", "rejected") is None
    assert learned_context._load(str(tmp_path)) == []


def test_a_post_bridge_exhaustion_files_a_mistake_fix_the_step_reads(tmp_path):
    """What the pipeline's own contract refused lands as a mistake_fix
    routed to the step that made the mistake, violation in the
    statement - `render_for_prompt` carries the statement, not the
    detail."""
    learning = learned_context.record_post_bridge_exhaustion(
        str(tmp_path), "mesh_spine", "block 4 has no clip_id", attempts=3)
    assert learning is not None
    assert learning["kind"] == learned_context.MISTAKE_FIX
    assert "mesh_spine" in learning["statement"]
    assert "block 4 has no clip_id" in learning["statement"]
    assert learning["read_by"] == ["mesh_spine"]
    rendered = learned_context.render_for_prompt(str(tmp_path), "mesh_spine")
    assert "block 4 has no clip_id" in rendered
    assert learned_context.render_for_prompt(
        str(tmp_path), "plan_transitions") == ""


def test_a_signed_off_reel_files_a_settled_decision_every_step_reads(tmp_path):
    """A sign-off names what was approved, and every planning step
    reads it - the same route a settled series identity takes."""
    learning = learned_context.record_reel_signoff(
        str(tmp_path), "Reel 09", note="ships", by="captain", round_number=3)
    assert learning is not None
    assert learning["kind"] == learned_context.SETTLED_DECISION
    assert "Reel 09" in learning["statement"]
    assert "ships" in learning["statement"]
    assert learning["read_by"] == ["*"]
    assert "Reel 09" in learned_context.render_for_prompt(
        str(tmp_path), "plan_transitions")
    assert "Reel 09" in learned_context.render_for_prompt(
        str(tmp_path), "music_selection")


def test_filing_is_one_atomic_write(tmp_path, monkeypatch):
    """A filing publishes its complete JSON with one atomic replacement."""
    project = str(tmp_path)
    first = learned_context.record_gate_rejection(
        project, "plan_transitions", "warm-up.")
    replacements = []
    replace = learned_context.os.replace

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        replace(source, destination)

    monkeypatch.setattr(learned_context.os, "replace", record_replace)
    learning = learned_context.record_gate_rejection(
        project, "plan_transitions", "No crash zooms.")
    assert learning is not None
    assert len(replacements) == 1
    source, destination = replacements[0]
    assert source.parent == destination.parent
    assert destination == Path(project) / "learned_context" / "learnings.json"
    assert not source.exists()
    assert learned_context._load(project) == [first, learning]


def test_filing_never_fails_the_run(tmp_path):
    """An empty project folder cannot hold a store: the filing returns
    None and says so, never raises - a learning write that fails the
    run turns a recorded rejection into a crashed one."""
    assert learned_context.record_gate_rejection(
        "", "plan_transitions", "no store, no failure") is None
    assert learned_context.record_post_bridge_exhaustion(
        "", "mesh_spine", "violation") is None
    assert learned_context.record_reel_signoff(
        "", "Reel 09", note="ships") is None


def test_a_rejected_gate_files_the_learning_through_review_gate(tmp_path):
    """The F-02 hook: `save_gate_feedback` with a rejection files the
    correction learning beside the history entry."""
    from library.tools import review_gate
    review_gate.save_gate_feedback(
        str(tmp_path), "plan_transitions", "rejected",
        feedback="No crash zooms on eating shots.")
    learned = learned_context.active_for_step(str(tmp_path), "plan_transitions")
    assert [l["kind"] for l in learned] == ["correction"]
    assert learned[0]["statement"] == "No crash zooms on eating shots."


def test_file_exhaustion_files_the_learning(tmp_path):
    """The post-bridge bound files the mistake_fix learning beside the
    history entry."""
    from library.tools import post_bridge_retry
    post_bridge_retry.file_exhaustion(
        str(tmp_path), "mesh_spine", "block 4 has no clip_id")
    learned = learned_context.active_for_step(str(tmp_path), "mesh_spine")
    assert [l["kind"] for l in learned] == ["mistake_fix"]
    assert "block 4 has no clip_id" in learned[0]["statement"]


def test_sign_off_files_the_learning(tmp_path):
    """The approval event: signing off a built reel files the
    settled_decision learning beside the sign-off record."""
    from library.tools import reel_signoff
    reel_signoff.sign_off(str(tmp_path), "Reel 09", note="ships")
    learned = learned_context.active_for_step(str(tmp_path), "*")
    assert [l["kind"] for l in learned] == ["settled_decision"]
    assert "Reel 09" in learned[0]["statement"]
    assert "ships" in learned[0]["statement"]
