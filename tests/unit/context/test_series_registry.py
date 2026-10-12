"""T15: the captain's planning tree is the brief.

A fixture series tree under `tmp_path`, in the real tree's shape - a
`series portfolio '26 planning/` folder (the apostrophe is the real one)
holding a series folder with its creative-direction document, overview
and `episode_database.md` - registered in a per-machine registry that
`$REN_SERIES_REGISTRY` points into `tmp_path`.  Then `ren new n12
--series s1 --episode 12`, run as the CLI runs it, must produce a project
whose attached brief points at the series document, whose episode row
reaches step 2.01's prompt, whose delivery spec is 1080x1920 with a 60 s
maximum, and whose briefing interview asks nothing the row answers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import brief_attachment, briefing_chat
from library.tools import series_registry as sr
from library.tools.delivery_spec import (
    duration_refusal,
    resolve_delivery_spec_for_project,
)

SERIES_DOC = """\
# Series 1: "Night Shift" - Branding & Creative Direction

## The Vibe: "A Warm Room in a Quiet World"

{deep}

## Editing Vibe

Slow cuts. Every episode ends with the 2:16 closing ritual.
"""

EPISODE_DB = """\
# Series 1: "Night Shift" - Episode Database

## Pipeline Status Key

| Status | Meaning |
|---|---|
| 💡 Idea | Raw idea, not yet developed |
| 📝 Scripted | Full script or outline ready |
| 🎬 Filmed | Raw footage captured |
| ✂️ Edited | Final cut complete |
| ✅ Posted | Published to all platforms |

---

## Original Ideas (28 episodes)

| # | Title | Hook / Summary | Status |
|---|---|---|---|
| 11 | **Romanticize Life** | The life you are waiting for is already here. | 📝 Scripted |
| 12 | **Body Doubling Through Filmmaking** | ROW_HOOK_9f3a: the camera is an accountability partner. | 🎬 Filmed |
| 13 | **Finding a Louder Voice** | Fears lose power when spoken aloud. | 💡 Idea |

## Curated Ideas (44 episodes)

| # | Title | Status | Personal Story Anchor (to be filled) |
|---|---|---|---|
| 29 | Art of Letting Go | 💡 Idea | |

## What to Film

| # | Item | Clip Example |
|---|---|---|
| 1 | Coffee | a pour-over at 2 AM |
"""

DEEP_SENTENCE = "UNIQUE_SERIES_DEEP_SENTENCE_41b7"


def _tree(tmp_path: Path) -> Path:
    series = tmp_path / "PLAN" / "series portfolio '26 planning" / "1_night_shift"
    series.mkdir(parents=True)
    deep = " ".join([DEEP_SENTENCE] + ["Warm amber light, a clock at 2:15."] * 30)
    (series / "branding_creative_direction.md").write_text(
        SERIES_DOC.format(deep=deep), encoding="utf-8")
    (series / "overview.md").write_text("# Overview\n", encoding="utf-8")
    (series / "episode_database.md").write_text(EPISODE_DB, encoding="utf-8")
    return series


def _env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    # Hermetic: the registry, the user config and the projects root all
    # live under tmp_path, and no host marker decides a backend.
    for marker in ("REN_HOST", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
                   "OPENCODE", "CODEX_SANDBOX"):
        env.pop(marker, None)
    env["REN_SERIES_REGISTRY"] = str(tmp_path / "config" / "series.yaml")
    env["REN_CONFIG"] = str(tmp_path / "config" / "config.env")
    env["PIPELINE_PROJECTS_ROOT"] = str(tmp_path / "projects")
    return env


def _run(args, env) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], cwd=str(REPO_ROOT), env=env,
        capture_output=True, encoding="utf-8", timeout=120, check=False)


@pytest.fixture
def registered(tmp_path, monkeypatch):
    series = _tree(tmp_path)
    env = _env(tmp_path)
    for key in ("REN_SERIES_REGISTRY", "REN_CONFIG"):
        monkeypatch.setenv(key, env[key])
    added = _run(["-m", "library.tools.series_registry", "add", "s1",
                  "--dir", str(series), "--name", "Night Shift",
                  "--format", "vertical_1080x1920", "--max-duration", "60"],
                 env)
    assert added.returncode == 0, added.stderr
    return series, env


def _new(tmp_path, env, slug, episode) -> Path:
    made = _run(["manage_project.py", "new", slug, "--series", "s1",
                 "--episode", str(episode), "--non-interactive"], env)
    assert made.returncode == 0, made.stdout + made.stderr
    return tmp_path / "projects" / slug


def _creative_direction_context(project: Path) -> str:
    """What step 2.01's model is handed, as the agent harness files it."""
    from library.processes.edit_video import run_pipeline
    from library.tools import model_task

    state = run_pipeline.load_pipeline_state(str(project))
    manifest = json.loads((REPO_ROOT / "library" / "steps"
                           / "step_2_01_creative_direction"
                           / "manifest.json").read_text(encoding="utf-8"))
    inputs = run_pipeline.gather_step_inputs(
        "creative_direction", {"edges": []}, state, manifest,
        step_type="llm_only")
    prompt = project / "handoff.md"
    prompt.write_text("Do the work.\n", encoding="utf-8")
    with patch.object(model_task, "_agent_sleep"), patch.object(
            model_task, "_agent_clock", side_effect=[0, 0, 10, 10, 10, 10]), \
            pytest.raises(run_pipeline.LLMError, match="Timeout"):
        run_pipeline.present_llm_step(
            str(prompt), inputs, "creative_direction", manifest=manifest,
            full_auto="agent", llm_timeout=1)
    request = json.loads((project / "pipeline_output" / "llm_requests"
                          / "creative_direction.json").read_text(
                              encoding="utf-8"))
    return request["context"] if isinstance(request["context"], str) \
        else json.dumps(request["context"], ensure_ascii=False)


def test_ren_new_series_episode_makes_the_planning_tree_the_brief(
        tmp_path, registered):
    series, env = registered
    project = _new(tmp_path, env, "n12", 12)
    doc = str(series / "branding_creative_direction.md")

    # The attached brief points at the series document, by reference:
    # nothing of the planning tree is copied into the project.
    attachment = brief_attachment.read_declaration(str(project))
    assert attachment.attached
    assert attachment.path == doc
    assert not (project / "brief.md").exists()
    pipeline = yaml.safe_load(
        (project / "project.yaml").read_text(encoding="utf-8"))["pipeline"]
    assert pipeline["episode"]["number"] == "12"
    assert pipeline["episode"]["database"] == str(series / "episode_database.md")
    assert pipeline["series"] == "Night Shift"

    # The episode row reaches step 2.01's prompt context, inside the
    # brief reference whose path is the series document.
    context = _creative_direction_context(project)
    assert f"FILE: {doc}" in context
    assert "ROW_HOOK_9f3a: the camera is an accountability partner." in context
    assert "episode 12" in context
    assert "line 20" in context
    assert DEEP_SENTENCE not in context, (
        "the series document must travel as a reference, not a copy")

    # The delivery spec: vertical 1080x1920, at most 60 s.
    spec = resolve_delivery_spec_for_project(str(project))
    assert (spec.width, spec.height) == (1080, 1920)
    assert spec.max_duration_seconds == 60.0
    assert duration_refusal(spec, 60.0) == ""
    assert "at most 60s" in duration_refusal(spec, 61.5)

    # The interview asks nothing the row answers, and says how many it
    # asked.
    state = briefing_chat.conduct_if_needed(
        str(project), {"project_folder": str(project),
                       "creative_brief": doc},
        "agent", llm_timeout=0, save=lambda p, s: None)
    assert state["briefing_interview_record"] == {
        "basis": "episode_row", "questions_asked": 0, "questions": []}
    assert not (project / "pipeline_output" / "llm_requests"
                / "briefing_interview.json").exists()


def test_an_empty_cell_is_the_only_question_asked(tmp_path, registered):
    _series, env = registered
    project = _new(tmp_path, env, "n29", 29)
    state = briefing_chat.conduct_if_needed(
        str(project), {"project_folder": str(project),
                       "creative_brief": "x"},
        "agent", llm_timeout=0, save=lambda p, s: None)
    record = state["briefing_interview_record"]
    assert record["questions_asked"] == 1
    assert "'Personal Story Anchor (to be filled)'" in record["questions"][0]
    request = json.loads((project / "pipeline_output" / "llm_requests"
                          / "briefing_interview.json").read_text(
                              encoding="utf-8"))
    assert [q["episode_column"] for q in request["questions"]] == [
        "Personal Story Anchor (to be filled)"]


def test_status_is_reported_never_written(tmp_path, registered):
    series, env = registered
    database = series / "episode_database.md"
    before = database.read_bytes()
    project = _new(tmp_path, env, "n12", 12)
    lines = sr.describe_for_run(str(project))
    joined = "\n".join(lines)
    assert "reads 🎬 Filmed" in joined
    assert "it would read ✂️ Edited - update it yourself" in joined
    record = sr.record_episode_for_run(str(project))
    assert record["recorded"] and record["line"] == 20
    assert database.read_bytes() == before, "Ren wrote the episode database"


def test_refusals_name_what_is_wrong(tmp_path, registered):
    _series, env = registered
    missing = _run(["manage_project.py", "new", "n99", "--series", "s1",
                    "--episode", "99", "--non-interactive"], env)
    assert missing.returncode != 0
    assert "episode '99' has no row" in missing.stdout + missing.stderr
    assert not (tmp_path / "projects" / "n99").exists()

    unknown = _run(["manage_project.py", "new", "x", "--series", "nope",
                    "--non-interactive"], env)
    assert unknown.returncode != 0
    assert "not in the series registry" in unknown.stdout + unknown.stderr


def test_only_a_table_led_by_the_episode_column_is_searched(tmp_path):
    series = _tree(tmp_path)
    row = sr.find_episode_row(str(series / "episode_database.md"), "13")
    assert row.title == "Finding a Louder Voice"
    # `What to Film` starts at 1 too, but its first column is also `#`:
    # episode 1 exists only there, and is found there, not invented.
    item = sr.find_episode_row(str(series / "episode_database.md"), "1")
    assert item.section == "What to Film"
    with pytest.raises(sr.SeriesRegistryError, match="first column is 'Day'"):
        sr.find_episode_row(str(series / "episode_database.md"), "12",
                            episode_column="Day")
