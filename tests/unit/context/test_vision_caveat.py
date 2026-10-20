"""VI-026: a generated description reaches every reader as a description.

The vision document carries measured ranges AND another model's prose in
one object. The prose was named only to the steps that also inherited a
creative direction, so every other reader of the same document - the
planning steps, the compiler, the graph builder - was handed a model's
reading with nothing saying which half of the document it was looking
at.

These pin the caveat's reach: it is delivered by what a reader READS,
not by what else it holds, and it names the paths that are prose.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import direction_contradiction as dc  # noqa: E402
from library.tools import llm_handshake, model_task  # noqa: E402
from library.tools.project_layout import Area, layout_for  # noqa: E402

NODE_ID = "vision_reader"
CAVEAT_HEADING = "One of your inputs is a description, not an observation"


def _prompt_for(tmp_path, monkeypatch, inputs) -> str:
    """Run one model call and read the prompt the model was handed."""
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()

    handoff = tmp_path / "handoff.md"
    handoff.write_text("# Step\n\nSENTINEL_HANDOFF_BODY\n", encoding="utf-8")

    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = model_task._agent_sleep

    def _sleep(seconds):
        (responses / f"{NODE_ID}.json").write_text(
            json.dumps({"answer": "ok"}), encoding="utf-8")
        return original_sleep(0)

    monkeypatch.setattr(model_task, "_agent_sleep", _sleep)

    task = model_task.ModelTask(
        key=NODE_ID,
        handoff_path=str(handoff),
        outputs=({"name": "answer", "type": "string", "required": True,
                  "description": "your answer"},),
        llm_outputs=({"name": "answer", "type": "string", "required": True,
                      "description": "your answer"},),
        context_fields=None,
        reads_brief=False,
        skills=None,
    )
    model_task.run_model_task(
        task, dict(inputs, project_folder=str(project)),
        full_auto="agent", llm_timeout=5)
    return llm_handshake.read_request(
        layout.read_dir(Area.LLM_REQUESTS) / f"{NODE_ID}.json")["prompt"]


def test_the_caveat_names_the_paths_that_are_prose():
    block = dc.vision_caveat_block()
    assert CAVEAT_HEADING in block
    for path in dc.VISION_PROSE_PATHS:
        assert f"`{path}`" in block
    assert "never an observed fact" in block


def test_a_reader_is_told_by_what_it_reads():
    assert dc.holds_vision_document({"semantic_analysis_documents": []})
    assert dc.holds_vision_document({"semantic_analysis": {}})
    assert not dc.holds_vision_document({"semantic_index": {}})
    assert not dc.holds_vision_document({})


def test_a_step_that_reads_the_description_is_told_it_is_a_description(
        tmp_path, monkeypatch):
    prompt = _prompt_for(tmp_path, monkeypatch, {
        "semantic_analysis_documents": [
            {"analysis": {"scene": "a kitchen"}}]})
    assert CAVEAT_HEADING in prompt
    assert "`analysis.scene`" in prompt


def test_a_step_that_reads_no_description_is_not_told_about_one(
        tmp_path, monkeypatch):
    prompt = _prompt_for(tmp_path, monkeypatch, {"semantic_index": {}})
    assert CAVEAT_HEADING not in prompt


def test_the_prose_list_is_rendered_once():
    """One list, one renderer: the direction block and the standalone
    caveat are the same text, so a reader that holds a direction is not
    told the same thing twice."""
    block = dc.prompt_block_for({"semantic_analysis_documents": "prose"})
    assert dc.vision_caveat_block() in block
    assert dc.renders_vision_caveat({"semantic_analysis_documents": "prose"})
    assert not dc.renders_vision_caveat({"semantic_index": "measured"})
