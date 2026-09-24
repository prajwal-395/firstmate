"""The brand's constraints must reach the steps that plan the look.

`TemplateLoader.get_brand_constraints` dispatched on the step's manifest
id (`step_4_03_plan_vfx`).  Its only caller, `present_llm_step`, passes
the DAG node id (`plan_vfx`).  No branch could ever match, so the
constraints string was empty for every step, every template and every
project for the whole life of the code, and `plan_transitions` chose
transitions with no knowledge of which ones its brand permits.

Nothing caught it because the one existing test called the function with
the identifier the function wanted rather than the one its caller sends.
Every assertion here therefore starts from a fact of the pipeline - the
node ids in `dag.json`, the synthetic project copies - rather than from
a string written in this file.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import present_llm_step
from library.tools import processes
from library.tools.project_layout import STEP_BY_ID, node_id_for
from library.tools.template_loader import BRAND_CONSTRAINT_STEPS, TemplateLoader
from tests.brand_fixtures import ALL_SYNTHETIC, write_brand_json, write_templates_dir

DAG = json.loads((REPO / "library/processes/edit_video/dag.json").read_text())
DAG_NODE_IDS = {n["id"] for n in DAG["nodes"]}

ALL_NODE_IDS = set(processes.node_owners())
# Every process's nodes, not just edit_video's. `BRAND_CONSTRAINT_STEPS`
# is checked against `DAG_NODE_IDS`: `present_llm_step` is edit_video's
# runner and brand constraints reach an LLM step of that pipeline. But
# `STEP_BY_ID` is the WHOLE repository's step table, so "wired" there
# means some process declares a node.


def _dag_node_id(step_dirname: str) -> str:
    """The DAG's id for a step, read off the DAG rather than assumed."""
    for node in DAG["nodes"]:
        if Path(node["step_ref"]).name == step_dirname:
            return node["id"]
    raise AssertionError(f"no DAG node runs {step_dirname}")




@pytest.mark.parametrize("step_dirname", [
    "step_2_01_creative_direction",
    "step_4_02_plan_transitions",
    "step_4_03_plan_vfx",
])
def test_the_identifier_the_runner_passes_gets_a_real_answer(tmp_path, step_dirname):
    """Ask the way the runner asks, and a brand must answer.

    This is the regression proper.  `_dag_node_id` reads the identifier
    out of `dag.json`, so renaming a node without teaching the template
    loader about it fails here instead of silently emptying the channel.
    """
    node_id = _dag_node_id(step_dirname)
    templates = write_templates_dir(tmp_path / "templates")
    loader = TemplateLoader(str(tmp_path), templates)
    constraints = loader.get_brand_constraints("synthetic_default", node_id)
    assert constraints.strip(), (
        f"synthetic_default contributes nothing to '{node_id}'. The runner "
        f"passes exactly this identifier."
    )
    assert "Brand Constraints:" in constraints






def test_a_step_with_no_brand_slot_gets_the_empty_string(tmp_path):
    """Only three steps have a slot; the rest must stay unaffected."""
    templates = write_templates_dir(tmp_path / "templates")
    loader = TemplateLoader(str(tmp_path), templates)
    for node_id in ("mesh_spine", "select_broll", "plan_sfx", "render"):
        assert loader.get_brand_constraints("synthetic_default", node_id) == ""


def test_the_project_copies_on_file_really_differ_from_one_another(tmp_path):
    """A channel that carries the same thing for every brand is not a channel."""
    templates = write_templates_dir(tmp_path / "templates")
    loader = TemplateLoader(str(tmp_path), templates)
    answers = {name: loader.get_brand_constraints(name, "plan_transitions")
               for name in sorted(ALL_SYNTHETIC)}
    answers = {k: v for k, v in answers.items() if v}
    assert len(answers) >= 2, f"only {list(answers)} say anything about transitions"
    assert len(set(answers.values())) > 1, (
        "every project copy permits the identical transition vocabulary, "
        "which would make the constraint pointless"
    )


def _answer_when_asked(project: Path, node_id: str, answer: dict):
    """Stand in for the agent on the other end of the agent file handshake."""
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


@pytest.mark.heavy
def test_the_brand_reaches_the_text_handed_to_the_model(tmp_path):
    """End to end, through the runner, in the mode this pipeline runs in.

    `agent` writes the request to a file and an agent answers it, so the
    request file IS the prompt.  It used to carry `prompt` alone while
    the constraints were concatenated only into the API path's
    `full_prompt` - which meant that even a working
    `get_brand_constraints` would have reached nobody here.
    """
    project = tmp_path / "project"
    project.mkdir()
    # The project SELECTS a brand and carries its copy.  It used to
    # declare none and still get a fallback file's constraints, which is
    # the thing that changed: a template-less project now contributes no
    # brand text at all, so a test that asserts the brand reaches the
    # prompt has to carry one.
    (project / "project.yaml").write_text(
        "name: t\nslug: t\npipeline:\n  brand_template: synthetic_default\n",
        encoding="utf-8")
    write_brand_json(project, "synthetic_default")
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Plan the transitions.\n", encoding="utf-8")

    node_id = _dag_node_id("step_4_02_plan_transitions")
    _answer_when_asked(project, node_id,
                       {"transition_creative": [{"cut_point_position": 1,
                                                 "transition_type": "hard_cut"}]})

    present_llm_step(
        str(prompt_path),
        {"project_folder": str(project), "timed_spine": {"structure": []}},
        node_id,
        manifest={"interface": {"outputs": [{"name": "transition_creative"}]},
                  "context_fields": ["timed_spine"]},
        full_auto="agent", llm_timeout=30,
    )

    request = json.loads(
        (project / "pipeline_output" / "llm_requests" / f"{node_id}.json").read_text()
    )
    expected = TemplateLoader(str(project)).get_brand_constraints(
        "synthetic_default", node_id)
    assert expected.strip()
    assert request["constraints"] == expected, (
        "the request file does not record what the brand contributed"
    )
    assert expected.strip() in request["prompt"], (
        "the brand constraints are missing from the prompt the agent is given"
    )
    # The vocabulary itself, not just the header.
    assert "hard_cut" in request["prompt"]


@pytest.mark.heavy
def test_a_project_that_selected_no_brand_contributes_no_brand_text(tmp_path):
    """The other half, and the point of the change.

    A project declaring no `pipeline.brand_template` used to be handed
    `default_brand.yaml`'s constraints: step 2.01 was told the series runs
    at "high" energy and step 4.03 was told to plan VFX at intensity 0.5.
    On project 001 the model overruled the energy in writing and called
    those values "a default nobody chose for this project".  Absence now
    declares nothing - see ABSENT_SLOT_READINGS in
    library/tools/brand_registry.py.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: t\nslug: t\n", encoding="utf-8")
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Plan the transitions.\n", encoding="utf-8")

    node_id = _dag_node_id("step_4_02_plan_transitions")
    _answer_when_asked(project, node_id,
                       {"transition_creative": [{"cut_point_position": 1,
                                                 "transition_type": "hard_cut"}]})

    present_llm_step(
        str(prompt_path),
        {"project_folder": str(project), "timed_spine": {"structure": []}},
        node_id,
        manifest={"interface": {"outputs": [{"name": "transition_creative"}]},
                  "context_fields": ["timed_spine"]},
        full_auto="agent", llm_timeout=30,
    )

    request = json.loads(
        (project / "pipeline_output" / "llm_requests" / f"{node_id}.json").read_text()
    )
    assert request["constraints"] == ""
    assert "Brand Constraints" not in request["prompt"]
    assert "Transition Duration MS" not in request["prompt"]
