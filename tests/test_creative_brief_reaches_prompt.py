"""The captain's creative brief has to arrive in the prompt, not just exist.

Asserted by FOLLOWING the reference the model is handed and requiring the
declared brief's words at the end of it. History: docs/evidence/brief_reference.md
("The brief that never reached a prompt").
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    PROCESS_LEVEL_INPUTS,
    gather_step_inputs,
    load_pipeline_state,
    present_llm_step,
)

STEPS_ROOT = REPO_ROOT / "library" / "steps"

BRIEF_TEXT = (
    "# Through the 4th Wall\n\n"
    "Minimal cuts. Let moments breathe.\n"
    "SENTINEL_BRIEF_MARKER_9f3a\n"
)


def _manifest(step_dir: Path) -> dict:
    return json.loads((step_dir / "manifest.json").read_text())


def _declared_inputs(step_dir: Path) -> set:
    iface = _manifest(step_dir).get("interface", {})
    return {i.get("name") for i in iface.get("inputs", []) if isinstance(i, dict)}


def _steps_documenting_the_brief():
    """Every step whose handoff.md tells the model to read a brief."""
    found = []
    for handoff in sorted(STEPS_ROOT.glob("*/handoff.md")):
        if "creative_brief" in handoff.read_text():
            found.append(handoff.parent)
    return found


def test_every_step_that_documents_the_brief_declares_it():
    """Documenting it is not asking for it: the runner injects a
    process-level input only into steps whose manifest declares it."""
    documenting = _steps_documenting_the_brief()
    assert documenting, "no handoff names the brief; the scan is broken"
    silent = [d.name for d in documenting
              if "creative_brief" not in _declared_inputs(d)]
    assert not silent, (
        f"handoff.md tells the LLM to read the creative brief but the "
        f"manifest does not declare the input: {silent}")


# An edgeless DAG: these tests are about the process-level input
# mechanism, not about data_mapping routing, and a real node would demand
# its whole upstream be present in state first.
EDGELESS_DAG = {"nodes": [], "edges": []}


def _gather(node_id, state, manifest):
    return gather_step_inputs(node_id, EDGELESS_DAG, state, manifest=manifest)


def _brief_the_model_can_reach(inputs) -> str:
    """Everything the model can get to, following what it was handed.

    The reference itself plus, when it names one, the file at the end of
    the path - opened here exactly as a shell-capable harness would open
    it. A reference naming an unreachable or relative path fails here.
    """
    from library.tools.brief_reference import reference_path
    text = inputs["creative_brief"]
    path = reference_path(text)
    if not path:
        return text
    assert Path(path).is_absolute(), (
        f"the reference hands the model {path!r}, which is relative: it "
        f"runs from wherever the harness put it, not from the project")
    return text + Path(path).read_text(encoding="utf-8")


DECLARING_MANIFEST = {
    "interface": {"inputs": [{"name": "creative_brief", "required": False}]}
}
SILENT_MANIFEST = {"interface": {"inputs": [{"name": "clip_catalog"}]}}


def test_a_relative_brief_resolves_against_the_project(tmp_path):
    (tmp_path / "brief.md").write_text(BRIEF_TEXT, encoding="utf-8")
    state = {"project_folder": str(tmp_path), "creative_brief": "brief.md"}

    inputs = _gather("plan_vfx", state, DECLARING_MANIFEST)

    assert BRIEF_TEXT in _brief_the_model_can_reach(inputs)


def test_a_missing_brief_raises_rather_than_passing_the_path(tmp_path):
    """The regression that made the old code look like it worked."""
    state = {
        "project_folder": str(tmp_path),
        "creative_brief": str(tmp_path / "does_not_exist.md"),
    }

    with pytest.raises(RuntimeError, match="cannot be read"):
        _gather("plan_vfx", state, DECLARING_MANIFEST)


def test_a_project_declaring_no_brief_gets_none(tmp_path):
    """A project without a brief must still run."""
    inputs = _gather("plan_vfx", {"project_folder": str(tmp_path)},
                     DECLARING_MANIFEST)
    assert not inputs.get("creative_brief")

    project = _project_declaring(tmp_path, 'name: "T"\nslug: "t"\n')

    state = load_pipeline_state(str(project))

    assert not state.get("creative_brief")


def _answer_when_asked(project: Path, node_id: str, answer: dict):
    """Stand in for the agent on the other end of the agent file handshake.

    The response cannot simply be pre-placed: `present_llm_step` deletes
    any existing response file before it writes the request, precisely so
    a stale answer cannot be mistaken for a fresh one.
    """
    req = project / "pipeline_output" / "llm_requests" / f"{node_id}.json"
    res = project / "pipeline_output" / "llm_responses" / f"{node_id}.json"

    def run():
        deadline = time.time() + 25
        while time.time() < deadline:
            if req.exists():
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps(answer), encoding="utf-8")
                return
            time.sleep(0.05)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_context_field_projection_does_not_drop_the_brief(tmp_path):
    """`context_fields` deletes every key it does not name.

    The brief is not a context field - it is restored around the
    projection - so a step declaring narrow `context_fields` must still
    get it. This is the mechanism that would silently undo the fix.
    """
    project = tmp_path / "project"
    project.mkdir()
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Plan the sound effects.\n", encoding="utf-8")

    _answer_when_asked(project, "plan_sfx",
                       {"sfx_plan": [{"timeline_in": 0.0,
                                      "timeline_out": 0.5}]})

    present_llm_step(
        str(prompt_path),
        {"project_folder": str(project), "creative_brief": BRIEF_TEXT,
         "dropped_key": "should not survive", "timed_spine": {}},
        "plan_sfx",
        manifest={"interface": {"inputs": [{"name": "creative_brief"}],
                                "outputs": [{"name": "sfx_plan"}]},
                  "context_fields": ["timed_spine"]},
        full_auto="agent", llm_timeout=30,
    )

    request = json.loads(
        (project / "pipeline_output" / "llm_requests" / "plan_sfx.json").read_text()
    )
    assert "SENTINEL_BRIEF_MARKER_9f3a" in request["context"]
    assert "should not survive" not in request["context"]


# ── The declaration half ──────────────────────────────────────────────
#
# Everything above starts from `state["creative_brief"]` already being
# set. What sets it is `load_pipeline_state`, off the project's own
# `project.yaml`, and nothing tested that - which is how a channel that
# works end to end can still deliver nothing: no project points at a
# document.


def _project_declaring(tmp_path, declaration: str) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(declaration, encoding="utf-8")
    return project


def test_a_brief_in_a_read_only_planning_tree_reaches_the_request(tmp_path, request):
    """The whole path, from the declaration to the file the model is given.

    This is the shape a real project uses: the planning tree lives
    outside the project and is never copied in, so the declaration is an
    absolute path into somebody else's directory. Read-only here, because
    a run must not need to write to the captain's planning tree.
    """
    planning = tmp_path / "planning" / "1_through_the_4th_wall"
    planning.mkdir(parents=True)
    brief = planning / "branding_creative_direction.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    brief.chmod(0o444)
    planning.chmod(0o555)
    request.addfinalizer(lambda: planning.chmod(0o755))

    project = _project_declaring(
        tmp_path, f'name: "T"\nslug: "t"\ncreative_brief: "{brief}"\n')

    state = load_pipeline_state(str(project))
    inputs = gather_step_inputs(
        "plan_vfx", EDGELESS_DAG, state, manifest=DECLARING_MANIFEST)
    assert BRIEF_TEXT in _brief_the_model_can_reach(inputs)

    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Do the creative work.\n", encoding="utf-8")
    _answer_when_asked(project, "plan_vfx",
                       {"vfx_plan": [{"clip_id": "clip_001",
                                      "effect": "punch_in"}]})
    present_llm_step(
        str(prompt_path), {**inputs, "project_folder": str(project)},
        "plan_vfx",
        manifest={"interface": {"inputs": [{"name": "creative_brief"}],
                                "outputs": [{"name": "vfx_plan"}]},
                  "context_fields": ["timed_spine"]},
        full_auto="agent", llm_timeout=30)

    request = json.loads(
        (project / "pipeline_output" / "llm_requests"
         / "plan_vfx.json").read_text())
    assert "SENTINEL_BRIEF_MARKER_9f3a" in request["context"], (
        "the declaration reached state and the words did not reach the "
        "request")
