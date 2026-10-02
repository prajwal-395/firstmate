"""A project declares a creative task the pipeline invokes, instead of adding a step.

Acceptance: a project-declared creative task reaches a model through
`present_llm_step` with its role prepended, the floors gate reads its
prompt, and a declaration that would break a guard is refused. History:
docs/evidence/creative_tasks.md. Every project is built under tmp_path.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import creative_tasks  # noqa: E402
from library.tools import creative_floors  # noqa: E402
from library.tools import craft_role  # noqa: E402
from library.tools import undetermined  # noqa: E402
from library.tools import model_task  # noqa: E402


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

    Mirrors tests/contracts/test_craft_role.py: the request file the answering
    agent reads is the artifact asserted on.
    """
    from library.processes.edit_video import run_pipeline
    from library.tools.project_layout import Area, layout_for

    layout = layout_for(str(project))
    layout.ensure()
    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = model_task._agent_sleep

    def _sleep(seconds):
        (responses / f"{creative_tasks.task_key(name)}.json").write_text(
            json.dumps(answer))
        return original_sleep(0)

    monkeypatch.setattr(model_task, "_agent_sleep", _sleep)
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


# ── The declaration is refused when it would break a guard ─────────────

def test_a_declaration_that_would_break_a_guard_is_refused_by_name(tmp_path):
    """A handoff demanding a count (floor), a name shadowing a step id, a
    task with nothing to ask, and a missing handoff each refuse."""
    rows = [
        ("floor", dict(handoff_text=(
            "# Pick reels\n\nYou must plan at least 3 reels.\n")), {}),
        ("step", {}, dict(name="select_broll")),
        ("nothing to ask", {}, dict(outputs=[])),
        ("handoff", {}, dict(handoff="not_there.md")),
    ]
    for i, (match, project_kw, entry_kw) in enumerate(rows):
        case = tmp_path / f"case{i}"
        case.mkdir()
        project = _write_project(case, [_task_entry(**entry_kw)], **project_kw)
        with pytest.raises(creative_tasks.CreativeTaskError, match=match):
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
