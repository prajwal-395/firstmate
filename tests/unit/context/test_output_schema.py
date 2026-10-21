"""The prompt points at the request's schema instead of repeating it.

The defect (scout `scout-opt-planning-cost`, C3): a request carried the
schema TWICE - the prose block injected into the prompt repeated every
field's description, and the request's own `expected_schema` carried the
same list as JSON.  24,666 bytes of prose across the planning band's
fifteen model-calling steps, about 8,700 tokens.

The JSON is the authority: it is what the answer is validated against,
and `ren asks` prints it in the request's stable prefix, before the task
instructions.  So the prompt names the fields and points at the schema,
and the descriptions travel once.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import llm_handshake, model_task  # noqa: E402
from library.tools.project_layout import Area, layout_for  # noqa: E402

NODE_ID = "schema_step"

DESCRIPTION = ("A long description of the field, of the kind a manifest "
                "carries: " + "and more words about it. " * 40)


def _outputs() -> list:
    return [{"name": "plan", "type": "list", "required": True,
             "description": DESCRIPTION}]


def test_the_pointer_is_much_cheaper_than_the_prose():
    pointer = model_task.output_schema_pointer(_outputs())
    prose = model_task.generate_output_schema_text(_outputs())

    assert len(pointer) < len(prose) / 4, (
        "the pointer is no cheaper than the prose it replaced")
    assert "`plan`" in pointer
    assert "expected_schema" in pointer


def test_the_request_carries_the_description_once(tmp_path, monkeypatch):
    """The whole point: the description is in the schema, not twice."""
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()
    handoff = tmp_path / "handoff.md"
    handoff.write_text("# Step\n\nChoose something.\n", encoding="utf-8")
    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = model_task._agent_sleep

    def _sleep(seconds):
        (responses / f"{NODE_ID}.json").write_text(
            json.dumps({"plan": ["a plan"]}), encoding="utf-8")
        return original_sleep(0)

    monkeypatch.setattr(model_task, "_agent_sleep", _sleep)

    task = model_task.ModelTask(
        key=NODE_ID,
        handoff_path=str(handoff),
        outputs=_outputs(),
        llm_outputs=_outputs(),
        context_fields=None,
        reads_brief=False,
        skills=None,
    )
    model_task.run_model_task(
        task, {"project_folder": str(project)}, full_auto="agent",
        llm_timeout=5)

    request = llm_handshake.read_request(
        layout.read_dir(Area.LLM_REQUESTS) / f"{NODE_ID}.json")
    prompt = request["prompt"]
    schema = request["expected_schema"]

    assert "plan" in prompt, "the prompt must name the field it wants"
    assert DESCRIPTION not in prompt, (
        "the description travelled in the prompt AND in the schema")
    assert DESCRIPTION in schema, (
        "the description has to reach the model somewhere")
    assert "expected_schema" in prompt
