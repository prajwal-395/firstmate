"""A project declares a creative task the pipeline invokes, instead of adding a step.

Raised by the captain 2026-09-04 on seeing select_reels land as step 3.4:
a reel picker is project-shaped work - a single-video edit has no reels -
and step-hood was firstmate's over-correction of the real complaint, which
was that the judgement circumvented the pipeline (no model was ever
reached). Step-hood is currently what forces an actual invocation with a
recorded prompt, and what makes the three guards reach it - craft_role's
role plumbing, test_no_creative_floors' derived roster, and
direction_contradiction's coverage. A brief that nothing invokes is a
document, and a document a worker reads and then acts on in-turn is
exactly the failure diagnosed in findings section 15.

So a project may declare a named creative task carrying a role and a
handoff in `pipeline.creative_tasks`, and the pipeline invokes it through
the SAME forcing function steps go through - `present_llm_step` itself,
not a second mechanism. The three guards reconcile against the declared
task rather than a step id:

* the role is PREPENDED to the handoff by the shared renderer;
* the floors gate reads the task's prompt (and a task whose role or
  handoff demands a count is refused at declaration);
* a task that takes the direction with declared evidence gets the
  contradiction field; every invoked task gets the undetermined field.

Acceptance, stated up front in the record: a project-declared creative
task must reach a model with its role attached, and the floors gate must
read its prompt.

Everything here builds its project under tmp_path. No test reaches a
real project.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools import creative_tasks  # noqa: E402
from library.tools import creative_floors  # noqa: E402
from library.tools import craft_role  # noqa: E402
from library.tools import undetermined  # noqa: E402


HANDOFF_BODY = "# Pick reels\n\nSENTINEL_TASK_HANDOFF_BODY\n"

ROLE = {
    "discipline": "short-form editor",
    "addressed_as": "You are the short-form editor on this episode.",
    "reads_with": ["A short is a complete small thing, not an excerpt."],
    "decides": ["Which stretches are worth cutting as shorts."],
    "defers": ["Whether a chosen short is built. That is the captain's."],
}

OUTPUTS = [
    {"name": "reel_selection", "type": "object",
     "description": "The chosen stretches."},
]


def _write_project(tmp_path, tasks, handoff_text=HANDOFF_BODY):
    import yaml

    project = tmp_path / "proj"
    project.mkdir()
    (project / "task_handoff.md").write_text(handoff_text, encoding="utf-8")
    (project / "project.yaml").write_text(
        yaml.safe_dump({
            "name": "Task Test",
            "slug": "task-test",
            "pipeline": {"creative_tasks": tasks},
        }),
        encoding="utf-8",
    )
    return project


def _task_entry(name="reel_pick", **overrides):
    entry = {
        "name": name,
        "role": dict(ROLE),
        "handoff": "task_handoff.md",
        "inputs": ["timeline_transcript"],
        "outputs": [dict(o) for o in OUTPUTS],
    }
    entry.update(overrides)
    return entry


def _canned_answer(**extra):
    answer = {"reel_selection": {"moments": [], "considered": []},
              "could_not_determine": []}
    answer.update(extra)
    return answer


def _run_task(monkeypatch, project, name, context, answer):
    """Invoke a declared task through the agent backend with a stubbed wait.

    Mirrors tests/test_craft_role.py: the request file the answering
    agent reads is the artifact asserted on.
    """
    from library.processes.edit_video import run_pipeline
    from library.tools.project_layout import Area, layout_for

    layout = layout_for(str(project))
    layout.ensure()
    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = run_pipeline.time.sleep

    def _sleep(seconds):
        (responses / f"{creative_tasks.task_key(name)}.json").write_text(
            json.dumps(answer))
        return original_sleep(0)

    monkeypatch.setattr(run_pipeline.time, "sleep", _sleep)
    undetermined.reset()
    return creative_tasks.present_creative_task(
        str(project), name, context, full_auto="agent", llm_timeout=5)


# ── Acceptance: reaches a model with its role attached ─────────────────

def test_a_declared_task_reaches_a_model_with_its_role_attached(
        tmp_path, monkeypatch):
    project = _write_project(tmp_path, [_task_entry()])
    context = {"timeline_transcript": {"lines": []}}

    result = _run_task(monkeypatch, project, "reel_pick", context,
                       _canned_answer())

    assert result["reel_selection"] == {"moments": [], "considered": []}

    from library.tools.project_layout import Area, layout_for
    prompt = json.loads(
        (layout_for(str(project)).read_dir(Area.LLM_REQUESTS)
         / f"{creative_tasks.task_key('reel_pick')}.json"
         ).read_text(encoding="utf-8"))["prompt"]

    # The role the PROJECT declared, rendered by the shared renderer -
    # expected values derived from the expressions the code uses, not
    # copied literals.
    task = creative_tasks.tasks_for_project(str(project))["reel_pick"]
    expected_block = craft_role.render_block(task.role)
    assert expected_block in prompt
    assert ROLE["addressed_as"] in prompt
    # PREPENDED: the frame comes before the document it frames.
    assert prompt.index(ROLE["addressed_as"]) < prompt.index(
        "SENTINEL_TASK_HANDOFF_BODY")

    # The undetermined forcing function reached it: the answer the stub
    # gave is recorded as a real reading, not a non-answer.
    assert undetermined.collected()
    assert undetermined.collected()[-1].reading == undetermined.NOTHING_MISSING


def test_the_floors_gate_reads_the_task_prompt(tmp_path):
    project = _write_project(tmp_path, [_task_entry()])
    task = creative_tasks.tasks_for_project(str(project))["reel_pick"]

    prompt = creative_tasks.build_prompt(task)

    # The gate is the same enumeration steps are read for - not a copy
    # of its phrases.
    assert creative_floors.find_floors(prompt) == []


# ── The declaration is refused when it would break a guard ─────────────

def test_a_task_whose_handoff_demands_a_count_is_refused(tmp_path):
    project = _write_project(
        tmp_path, [_task_entry()],
        handoff_text="# Pick reels\n\nYou must plan at least 3 reels.\n")
    with pytest.raises(creative_tasks.CreativeTaskError, match="floor"):
        creative_tasks.tasks_for_project(str(project))


def test_a_task_whose_role_demands_a_count_is_refused(tmp_path):
    role = dict(ROLE, decides=["Plan at least 3 shorts."])
    project = _write_project(tmp_path, [_task_entry(role=role)])
    with pytest.raises(creative_tasks.CreativeTaskError, match="floor"):
        creative_tasks.tasks_for_project(str(project))


def test_a_task_shadowing_a_step_id_is_refused(tmp_path):
    project = _write_project(tmp_path, [_task_entry(name="select_broll")])
    with pytest.raises(creative_tasks.CreativeTaskError, match="step"):
        creative_tasks.tasks_for_project(str(project))


def test_a_task_with_nothing_to_ask_is_refused(tmp_path):
    project = _write_project(tmp_path, [_task_entry(outputs=[])])
    with pytest.raises(creative_tasks.CreativeTaskError, match="nothing to ask"):
        creative_tasks.tasks_for_project(str(project))


def test_a_task_with_an_incomplete_role_is_refused(tmp_path):
    role = dict(ROLE, defers=[])
    project = _write_project(tmp_path, [_task_entry(role=role)])
    with pytest.raises(creative_tasks.CreativeTaskError, match="role"):
        creative_tasks.tasks_for_project(str(project))


def test_a_task_naming_a_missing_handoff_is_refused(tmp_path):
    project = _write_project(
        tmp_path, [_task_entry(handoff="not_there.md")])
    with pytest.raises(creative_tasks.CreativeTaskError, match="handoff"):
        creative_tasks.tasks_for_project(str(project))


# ── The third guard reconciles against the declaration ─────────────────

def test_a_task_with_direction_and_evidence_gets_the_flag_field(
        tmp_path, monkeypatch):
    entry = _task_entry(inputs=["creative_direction", "reel_candidates"],
                        evidence={"reel_candidates": "turn counts per stretch"})
    project = _write_project(tmp_path, [entry])
    context = {"creative_direction": {"mood": "bright"}, "reel_candidates": []}

    _run_task(monkeypatch, project, "reel_pick", context,
              _canned_answer(contradicts_direction=[]))

    from library.tools.project_layout import Area, layout_for
    request = json.loads(
        (layout_for(str(project)).read_dir(Area.LLM_REQUESTS)
         / f"{creative_tasks.task_key('reel_pick')}.json"
         ).read_text(encoding="utf-8"))
    schema_names = [o["name"] for o in json.loads(request["expected_schema"])]
    from library.tools.direction_contradiction import FIELD
    assert FIELD in schema_names


def test_a_task_with_no_evidence_gets_no_flag_field(
        tmp_path, monkeypatch):
    project = _write_project(tmp_path, [_task_entry()])
    _run_task(monkeypatch, project, "reel_pick",
              {"timeline_transcript": {}}, _canned_answer())

    from library.tools.project_layout import Area, layout_for
    request = json.loads(
        (layout_for(str(project)).read_dir(Area.LLM_REQUESTS)
         / f"{creative_tasks.task_key('reel_pick')}.json"
         ).read_text(encoding="utf-8"))
    schema_names = [o["name"] for o in json.loads(request["expected_schema"])]
    from library.tools.direction_contradiction import FIELD
    assert FIELD not in schema_names


# ── The bench mirrors the task's prompt contributions ──────────────────

def test_the_replay_bench_mirrors_the_task_role():
    source = (REPO / "library/tools/replay_bench/reconstruct.py").read_text(
        encoding="utf-8")
    assert "creative_tasks" in source


# ── The declaration round-trips through the project config ─────────────

def test_creative_tasks_round_trip_through_project_config(tmp_path):
    from library.schemas.project_config import (
        load_project_config, project_config_to_dict,
    )
    project = _write_project(tmp_path, [_task_entry()])

    config = load_project_config(project / "project.yaml")
    assert len(config.pipeline.creative_tasks) == 1
    assert config.pipeline.creative_tasks[0]["name"] == "reel_pick"

    assert (project_config_to_dict(config)["pipeline"]["creative_tasks"]
            == config.pipeline.creative_tasks)
