"""LE-09: `ren now <p> [--json]` - state plus next actions, in one call.

The cost this removes (plan section 2): learning "what now" costs 4 or
more calls today (status + progress + `ls llm_requests` + reading the
proposals JSON), or a dozen direct reads of `pipeline_data.json`. One
`ren now --json` answers it.

Every project here is built under `tmp_path`; no captain project,
Resolve timeline or broker is touched.
"""

import json

import pytest

from library.tools.project_work_state import (
    main,
    project_launch_pack,
    project_now_state,
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    # Firstmate's full-suite run executes inside a Claude Code session,
    # so host/agent markers are cleared: the fixtures address projects
    # by absolute path and must read identically there.
    for marker in ("PIPELINE_PROJECTS_ROOT", "PIPELINE_SFX_LIBRARY",
                   "PIPELINE_MUSIC_LIBRARY", "REN_OUTPUT"):
        monkeypatch.delenv(marker, raising=False)


def _write_project(root, name="Show", slug="show"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.yaml").write_text(
        f'name: "{name}"\nslug: {slug}\n', encoding="utf-8")
    return root


def _write_request(project, stem, step_id=None, kind="llm_step"):
    requests = project / "pipeline_output" / "llm_requests"
    requests.mkdir(parents=True, exist_ok=True)
    (requests / f"{stem}.json").write_text(json.dumps({
        "step_id": step_id or stem, "kind": kind, "prompt": "decide",
        "constraints": "", "context": "", "expected_schema": "{}",
        "project_folder": str(project), "timestamp": "2026-10-12T00:00:00",
    }), encoding="utf-8")


def _write_proposal(project, approval="proposed", number=2, slug="beta"):
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "reel_proposals_v2.json").write_text(json.dumps({
        "format": "reel_proposal/1", "derived_from": {}, "instruction": "",
        "moment_count": 1, "moments": [{
            "number": number, "slug": slug, "reason": "why",
            "timeline_start": 10.0, "timeline_end": 15.0,
            "approval": approval,
        }],
    }), encoding="utf-8")


def _write_state(project, **fields):
    state = {"failed_steps": [], "step_errors": {},
             "preflight_completed": {}, "edit_completed": {}}
    state.update(fields)
    (project / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")


def test_now_returns_ask_reel_and_failed_step_with_commands(tmp_path):
    project = _write_project(tmp_path / "show")
    _write_request(project, "plan_sfx")
    _write_proposal(project)
    _write_state(project, failed_steps=["music_analysis"],
                 step_errors={"music_analysis": "librosa missing"},
                 preflight_completed={"scan": {}})

    state = project_now_state(str(project))

    assert [ask["id"] for ask in state["asks"]["pending"]] == ["plan_sfx"]
    assert state["reels"]["by_approval"] == {"proposed": 1}
    assert state["reels"]["proposed"] == [2]
    assert [failed["step"] for failed in state["pipeline"]["failed"]] == [
        "music_analysis"]
    assert [action["id"] for action in state["next_actions"]] == [
        "answer-plan_sfx", "rerun-music_analysis", "approve-reel-2"]
    assert state["next_actions"][0]["command"] == (
        f"ren asks {project}")
    assert state["next_actions"][1]["command"] == (
        f"ren edit {project} --rerun music_analysis")
    assert state["next_actions"][2]["command"] == (
        f"ren approve {project} 2")
    for action in state["next_actions"]:
        assert action["command"].split()[0] in {"ren", "python3"}
        assert set(action) >= {"id", "why", "command", "effects"}


def test_now_on_raw_project_suggests_full_auto_detach(tmp_path):
    project = _write_project(tmp_path / "raw")
    (project / "raw").mkdir()

    state = project_now_state(str(project))

    assert state["pipeline"]["completed"] == []
    assert state["asks"]["pending"] == []
    assert [action["id"] for action in state["next_actions"]] == [
        "run-next-stage"]
    assert state["next_actions"][0]["command"] == (
        f"ren edit {project} --full-auto agent --detach")


def test_now_text_prints_summary_plus_numbered_actions(tmp_path, capsys):
    project = _write_project(tmp_path / "show")
    _write_request(project, "plan_sfx")
    _write_proposal(project)
    _write_state(project, failed_steps=["music_analysis"],
                 step_errors={"music_analysis": "boom"})

    assert main(["now", str(project)]) == 0
    lines = capsys.readouterr().out.splitlines()

    state = project_now_state(str(project))
    assert lines[0] == state["summary"]
    assert len(lines) == 1 + len(state["next_actions"])
    for number, line in enumerate(lines[1:], start=1):
        assert line.startswith(f"{number}. [")


def test_now_json_gives_the_whole_structure(tmp_path, capsys):
    project = _write_project(tmp_path / "show")
    _write_request(project, "plan_sfx")

    assert main(["now", str(project), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert set(payload) >= {"project", "pipeline", "run", "asks", "reels",
                            "notes", "config_findings", "next_actions",
                            "summary"}
    assert payload["run"] == {"status": None, "current_step": None,
                              "last_completed_step": None, "pid": None,
                              "pid_alive": False, "hold": None,
                              "lock_wait": None, "updated_at": None}
    assert [ask["id"] for ask in payload["asks"]["pending"]] == ["plan_sfx"]


def test_now_answered_ask_leaves_the_queue(tmp_path):
    project = _write_project(tmp_path / "show")
    _write_request(project, "plan_sfx")
    responses = project / "pipeline_output" / "llm_responses"
    responses.mkdir(parents=True)
    (responses / "plan_sfx.json").write_text(json.dumps({"answer": 1}),
                                             encoding="utf-8")

    state = project_now_state(str(project))

    assert state["asks"]["pending"] == []
    assert all(not action["id"].startswith("answer-")
               for action in state["next_actions"])


def test_now_held_run_offers_resume_first(tmp_path):
    project = _write_project(tmp_path / "show")
    (project / "pipeline_run.json").write_text(json.dumps({
        "status": "held", "current_step": "plan_sfx",
        "steps_to_run": ["plan_sfx"],
    }), encoding="utf-8")
    (project / "pipeline.hold").write_text(json.dumps({
        "requested_at": "2026-10-12T00:00:00", "requested_by": "captain",
        "reason": "looking at it",
    }), encoding="utf-8")

    state = project_now_state(str(project))

    assert state["run"]["status"] == "held"
    assert state["run"]["current_step"] == "plan_sfx"
    assert state["run"]["pid_alive"] is False
    assert state["run"]["hold"]["reason"] == "looking at it"
    assert state["next_actions"][0]["id"] == "resume-run"
    assert state["next_actions"][0]["command"] == f"ren resume {project}"


def test_now_offers_building_approved_reels_before_the_next_stage(tmp_path):
    """After "keep both", `ren now` pointed at the edit pipeline's next
    stage (`ren edit --full-auto agent --detach`) - not at building the
    reels the person had just approved."""
    project = _write_project(tmp_path / "show")
    _write_proposal(project, approval="approved")
    _write_state(project, preflight_completed={"scan": {}})

    state = project_now_state(str(project))

    ids = [action["id"] for action in state["next_actions"]]
    assert ids[0] == "build-reels", ids
    assert "run-next-stage" in ids
    assert state["next_actions"][0]["command"] == f"ren build {project}"
    assert "Next: ren build" in state["summary"]


def test_now_reports_lock_wait_from_the_run_record(tmp_path):
    project = _write_project(tmp_path / "show")
    (project / "pipeline_run.json").write_text(json.dumps({
        "status": "running", "current_step": "render",
        "heavy_work_wait": {"message": "heavy-work: waiting",
                            "holders": [{"owner": "job", "pid": 1}]},
    }), encoding="utf-8")

    state = project_now_state(str(project))

    assert state["run"]["lock_wait"]["holders"] == [{"owner": "job",
                                                     "pid": 1}]
    assert "heavy-work lock" in state["summary"]


def test_now_bad_config_reports_finding_and_still_answers(tmp_path):
    project = tmp_path / "bad"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: Bad\nslug: badproj\nproject_format_version: 2\n"
        "pipeline:\n"
        "  deliver_preset: {format: mp4, codec: H264, render_preset: X}\n",
        encoding="utf-8")

    state = project_now_state(str(project))

    assert state["config_findings"] != []
    assert state["next_actions"][0]["id"] == "fix-config"
    assert state["next_actions"][0]["command"] == f"ren check {project}"


def test_now_names_the_task_and_the_act_it_waits_on(tmp_path):
    """The defect: `ren now` - the ONE call a fresh session is told to
    make - never mentioned the TASK. The project had an assignment with
    work on it, and the ranked actions pointed at the pipeline instead,
    so a session with no conversation context could not tell what had
    already been proposed, applied, verified or delivered."""
    project = _write_project(tmp_path / "show")
    from library.tools import assignment_lifecycle as lifecycle

    record = lifecycle.create(
        str(project), "assignment-1", kind="reels",
        reels={"moment-1": {"number": 1, "name": "Reel 01 - pricing",
                            "status": "built"}})
    record["state"] = "review"
    lifecycle.save(str(project), record)

    state = project_now_state(str(project))

    [task] = state["tasks"]
    assert task["assignment_id"] == "assignment-1"
    assert "task assignment-1" in state["summary"]
    assert "0 proposed" in state["summary"]
    ids = [action["id"] for action in state["next_actions"]]
    assert "resume-task-assignment-1" in ids
    action = next(row for row in state["next_actions"]
                  if row["id"] == "resume-task-assignment-1")
    assert action["command"] == f"ren sign-off {project} 'Reel 01 - pricing'"
    assert action["effects"]


def test_now_verb_is_registered_in_both_front_doors():
    from pathlib import Path

    from ren.commands import VERBS

    repo = Path(__file__).resolve().parents[3]
    assert any(verb.name == "now" and verb.module_argv == (
        "library.tools.project_work_state", "now") for verb in VERBS)
    native = (repo / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "now"' in native


def test_launch_pack_contains_now_state_and_invalidates_fingerprint(tmp_path):
    project = _write_project(tmp_path / "show")
    _write_request(project, "plan_sfx")
    _write_proposal(project)
    _write_state(project, preflight_completed={"scan": {}})
    (project / "pipeline_run.json").write_text(json.dumps({
        "status": "running", "current_step": "plan_sfx",
    }), encoding="utf-8")

    pack = project_launch_pack(str(project), captured_at="2026-10-15T12:00:00Z")
    same = project_launch_pack(str(project), captured_at="2026-10-15T12:00:00Z")
    assert pack == same
    assert pack["schema"] == "ren-launch-pack/1"
    assert pack["project"] == {"slug": "show", "name": "Show",
                                "path": str(project)}
    assert pack["state"] == project_now_state(str(project))
    assert pack["state"]["run"]["status"] == "running"
    assert [ask["id"] for ask in pack["state"]["asks"]["pending"]] == [
        "plan_sfx"]
    assert pack["state"]["reels"]["total"] == 1

    (project / "pipeline_run.json").write_text(json.dumps({
        "status": "running", "current_step": "render",
    }), encoding="utf-8")
    changed = project_launch_pack(str(project), captured_at="2026-10-15T12:00:00Z")
    assert changed["snapshot"]["fingerprint"] != pack["snapshot"]["fingerprint"]


@pytest.mark.parametrize("request_count", [1, 50, 200])
def test_launch_pack_stays_bounded_at_realistic_project_sizes(
        tmp_path, request_count):
    from library.tools.project_work_state import LAUNCH_PACK_MAX_BYTES

    project = _write_project(tmp_path / f"show-{request_count}")
    (project / "project.yaml").write_text(
        'name: "Show"\nslug: show\n'
        'pipeline:\n  creative_brief: "Keep the guest perspective central"\n'
        'source:\n  language: en\n  shape: speech\n', encoding="utf-8")
    for index in range(request_count):
        _write_request(project, f"step_{index:04d}")

    pack = project_launch_pack(str(project), captured_at="2026-10-15T12:00:00Z")
    encoded = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))

    assert len(encoded.encode("utf-8")) <= LAUNCH_PACK_MAX_BYTES
    assert len(pack["state"]["asks"]["pending"]) == request_count
    assert {row["decision"] for row in pack["state"]["settled_decisions"]} >= {
        "creative brief attached", "source shape"}
    assert any(row["value"] == "Keep the guest perspective central"
               for row in pack["state"]["settled_decisions"])


def test_launch_pack_refuses_missing_project_with_structured_refusal():
    from library.tools.ren_refusal import RenRefusal

    with pytest.raises(RenRefusal) as error:
        project_launch_pack("does-not-exist")
    assert error.value.code == "REN_LAUNCH_PACK_PROJECT_INVALID"
    assert error.value.to_dict()["fix"]


def test_launch_pack_is_registered_for_cli_and_mcp():
    from pathlib import Path

    from ren.commands import VERBS

    repo = Path(__file__).resolve().parents[3]
    verb = next(verb for verb in VERBS if verb.name == "launch-pack")
    assert verb.read_only and verb.json
    assert verb.module_argv == ("library.tools.project_work_state", "launch-pack")
    assert verb.input_schema["required"] == ["project"]
    native = (repo / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "launch-pack"' in native
