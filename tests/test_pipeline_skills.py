"""Recallable skills: the catalogue the model is told about and must use.

`library/tools/pipeline_skills.py` owns what skills exist and how they
are rendered into a prompt; `library/skills/` holds one directory per
skill. These pin the four things that make the catalogue trustworthy:

- a skill named in a manifest that does not exist fails, and a
  declared skill that never reaches the prompt fails (both refusal
  directions, or the catalogue is 177 more tools nothing is told
  about);
- `verify_render` gates: it fails on real bad input and passes on real
  good input (a gate that cannot fail, or that fails correct output,
  is worse than no gate - AGENTS.md 10.4);
- `ask_the_footage` reports: its deterministic half carries the
  verdict while the model's opinion is recorded, never enforced;
- a step that skips a declared gating skill fails, read back from the
  receipt on disk rather than from the answer's claim - and a further
  skill joins without touching runner code.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools import pipeline_skills  # noqa: E402


# ── Fixture videos: real files, real ffmpeg ───────────────────────

def _build_video(path, width=1080, height=1920, duration=2.0,
                 black=False, fps=30):
    """A small render-like file. `black=True` makes 2s of digital black
    with silence - the canonical broken render."""
    if black:
        src = (f"color=c=black:s={width}x{height}:r={fps}:d={duration}")
        audio = "anullsrc=r=48000:cl=stereo"
        audio_filter = "anull"
    else:
        src = f"testsrc=s={width}x{height}:r={fps}:d={duration}"
        audio = f"sine=frequency=440:duration={duration}"
        # Mastered to the delivery target in one pass: a constant tone
        # takes a constant gain, so single-pass loudnorm lands it.
        audio_filter = "loudnorm=I=-14:TP=-1.5:LRA=11"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", src,
         "-f", "lavfi", "-i", audio,
         "-map", "0:v", "-map", "1:a",
         "-filter:a", audio_filter,
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
         "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, check=True)


@pytest.fixture()
def good_video(tmp_path):
    path = str(tmp_path / "good.mp4")
    _build_video(path)
    return path


@pytest.fixture()
def black_video(tmp_path):
    path = str(tmp_path / "black.mp4")
    _build_video(path, black=True)
    return path


# ── The catalogue is real directories with model-facing docs ──────

def test_every_catalogued_skill_has_a_directory_entry_and_docs():
    for name, skill in pipeline_skills.SKILLS.items():
        d = REPO / "library" / "skills" / name
        assert d.is_dir(), f"skill {name!r} has no library/skills/ directory"
        doc = (d / "SKILL.md").read_text(encoding="utf-8").lower()
        for word in ("when", "cost", "return"):
            assert word in doc, (
                f"{name}/SKILL.md never says {word} - it is written for "
                f"a model deciding whether to call, not for a human")
        entry = d / "skill.py"
        assert entry.exists(), f"skill {name!r} has no entry point"
        assert skill.module == f"library.skills.{name}"


def test_kinds_are_only_gate_and_report():
    for name, skill in pipeline_skills.SKILLS.items():
        assert skill.kind in (pipeline_skills.GATE,
                              pipeline_skills.REPORT), name


# ── Refusal direction one: a name nothing holds ───────────────────

def test_unknown_skill_name_is_refused():
    with pytest.raises(pipeline_skills.UnknownSkill, match="no_such_skill"):
        pipeline_skills.declared_skills({"skills": ["no_such_skill"]},
                                        "validate")


def test_the_refusal_can_actually_fire():
    """A gate that cannot fail reads as coverage (AGENTS.md 10.4)."""
    with pytest.raises(pipeline_skills.UnknownSkill):
        pipeline_skills.declared_skills(
            {"skills": ["verify_render", "bogus"]}, "validate")


def test_skills_under_interface_are_refused_not_ignored():
    """The step-3.04 shape: declared where nothing reads it."""
    with pytest.raises(pipeline_skills.MisplacedSkills,
                       match="top level"):
        pipeline_skills.declared_skills(
            {"interface": {"skills": ["verify_render"]}}, "validate")


def test_a_non_list_declaration_is_refused():
    with pytest.raises(pipeline_skills.UnknownSkill):
        pipeline_skills.declared_skills({"skills": "verify_render"},
                                        "validate")


# ── Refusal direction two: declared but never reaching the prompt ─

def test_declared_skill_missing_from_prompt_fails():
    with pytest.raises(pipeline_skills.UnreachedSkill,
                       match="verify_render"):
        pipeline_skills.assert_declared_skills_reach_prompt(
            "validate", {"skills": ["verify_render"]},
            "a prompt that names nothing")


def test_reached_skill_passes():
    pipeline_skills.assert_declared_skills_reach_prompt(
        "validate", {"skills": ["verify_render"]},
        pipeline_skills.prompt_block(
            "validate", {"skills": ["verify_render"]}, "agent"))


def test_no_manifest_means_no_block_and_no_refusal():
    assert pipeline_skills.prompt_block("render", None) == ""
    assert pipeline_skills.prompt_block(
        "render", {"interface": {}}) == ""
    pipeline_skills.assert_declared_skills_reach_prompt(
        "render", None, "anything")


# ── The block tells the model what it must do, per harness ───────

def test_block_names_each_skill_and_its_kind():
    block = pipeline_skills.prompt_block(
        "validate", {"skills": ["verify_render"]}, "agent")
    assert "verify_render" in block
    assert "GATING" in block
    block = pipeline_skills.prompt_block(
        "review_rough_cut", {"skills": ["ask_the_footage"]}, "agent")
    assert "ask_the_footage" in block
    assert "REPORTING" in block


def test_shell_harness_is_told_to_invoke_gating_before_answering():
    block = pipeline_skills.prompt_block(
        "validate", {"skills": ["verify_render"]}, "agent")
    assert "python3 -m library.skills.verify_render.skill" in block
    assert "BEFORE you answer" in block


def test_shell_less_harness_gets_the_pipeline_runs_it_route():
    block = pipeline_skills.prompt_block(
        "validate", {"skills": ["verify_render"]}, "api")
    assert "python3 -m" not in block
    assert "the pipeline runs `verify_render` for you" in block


def test_harness_enumeration_is_complete():
    assert pipeline_skills.harness_invokes_skills("agent") is True
    assert pipeline_skills.harness_invokes_skills("api") is False
    with pytest.raises(pipeline_skills.UnknownHarness, match="brand_new"):
        pipeline_skills.harness_invokes_skills("brand_new")


# ── A third skill joins without touching runner code ──────────────

def test_third_skill_needs_only_a_registry_row(monkeypatch):
    """The hook the timeline-SOP lane used to join as `verify_timeline`:
    catalogue growth is a row plus a directory, never a runner change."""
    third = pipeline_skills.Skill(
        name="timeline_sop_check",
        kind=pipeline_skills.GATE,
        module="library.skills.verify_render",
        when="WHEN: the timeline must obey the SOP.",
        cost="COST: free.",
        returns="RETURNS: a verdict.",
        pipeline_args=None,
    )
    monkeypatch.setitem(pipeline_skills.SKILLS, third.name, third)
    manifest = {"skills": ["verify_render", "timeline_sop_check"]}
    block = pipeline_skills.prompt_block("validate", manifest, "agent")
    assert "timeline_sop_check" in block
    pipeline_skills.assert_declared_skills_reach_prompt(
        "validate", manifest, block)
    assert pipeline_skills.gating_skills(manifest, "validate") == [
        "verify_render", "timeline_sop_check"]
    source = (REPO / "library/processes/edit_video/run_pipeline.py"
              ).read_text(encoding="utf-8")
    assert "timeline_sop_check" not in source


def test_run_skill_dispatches_by_registry(monkeypatch, tmp_path,
                                          good_video):
    result = pipeline_skills.run_skill(
        "verify_render", video_path=good_video,
        project_folder=str(tmp_path), step_id="validate",
        expected_resolution=[1080, 1920], expected_fps=30)
    assert result["passed"] is True
    with pytest.raises(pipeline_skills.UnknownSkill):
        pipeline_skills.run_skill("no_such_skill")


# ── verify_render gates: fails bad, passes good ───────────────────

def test_verify_render_passes_a_real_good_render(tmp_path, good_video):
    from library.skills.verify_render.skill import run
    verdict = run(good_video, str(tmp_path), "validate",
                  expected_resolution=[1080, 1920], expected_fps=30,
                  expected_duration=2.0)
    assert verdict["passed"] is True, verdict["issues"]
    assert verdict["receipt"].endswith("verify_render.json")
    # The receipt is real state: the measured verdicts, on disk.
    record = json.loads(Path(verdict["receipt"]).read_text())
    assert record["step_id"] == "validate"
    assert all(c["passed"] for c in record["result"]["checks"])


def test_verify_render_fails_a_real_black_render(tmp_path, black_video):
    from library.skills.verify_render.skill import run
    verdict = run(black_video, str(tmp_path), "validate",
                  expected_resolution=[1080, 1920], expected_fps=30)
    assert verdict["passed"] is False
    failed = [c["name"] for c in verdict["checks"] if not c["passed"]]
    assert failed, "a 2s black-and-silent render passed every check"
    assert verdict["receipt"] is not None


def test_verify_render_refuses_a_missing_file(tmp_path):
    from library.skills.verify_render.skill import run
    verdict = run(str(tmp_path / "absent.mp4"), str(tmp_path),
                  "validate")
    assert verdict["passed"] is False
    assert "not found" in verdict["issues"][0]


# ── ask_the_footage reports: deterministic half carries, model opines

def test_ask_the_footage_reports_on_good_footage(tmp_path, good_video,
                                                 monkeypatch):
    """The model's opinion is recorded with its confidence; the
    deterministic half answers on its own."""
    from library.skills.ask_the_footage import skill as ask
    monkeypatch.setattr(
        ask, "ask_vision",
        lambda stills, question, check_type: {
            "available": True, "opinion_passed": True,
            "confidence": 0.9, "detail": "caption readable",
            "issues": [], "elapsed_seconds": 0.1})
    observation = ask.run(good_video, "Is the picture intact?",
                          str(tmp_path), "review_rough_cut")
    assert observation["deterministic_passed"] is True
    assert observation["vision"]["opinion_passed"] is True
    assert "passed" not in observation, (
        "a reporting skill must not render a gating verdict")
    assert observation["receipt"].endswith("ask_the_footage.json")


def test_ask_the_footage_deterministic_half_fails_bad_footage(
        tmp_path, black_video, monkeypatch):
    from library.skills.ask_the_footage import skill as ask
    monkeypatch.setattr(
        ask, "ask_vision",
        lambda stills, question, check_type: {
            "available": True, "opinion_passed": True,
            "confidence": 0.9, "detail": "looks fine to me",
            "issues": [], "elapsed_seconds": 0.1})
    observation = ask.run(black_video, "Is the picture intact?",
                          str(tmp_path), "review_rough_cut")
    # The model said fine; the measurements say black. The
    # measurements carry it - the opinion is recorded, not enforced.
    assert observation["deterministic_passed"] is False
    assert observation["vision"]["opinion_passed"] is True


def test_unavailable_vision_is_reported_not_failed(tmp_path, good_video,
                                                   monkeypatch):
    """No model on this machine is a reported fact, not a failure -
    or the skill would fail correct footage whenever unloaded."""
    import library.tools.vision_model as vision_model
    monkeypatch.setattr(vision_model.VisionModel, "analyze_image",
                        lambda self, *a, **k: (_ for _ in ()).throw(
                            ImportError("mlx_vlm is not installed.")))
    monkeypatch.setattr(vision_model.VisionModel, "analyze_images",
                        lambda self, *a, **k: (_ for _ in ()).throw(
                            ImportError("mlx_vlm is not installed.")))
    from library.skills.ask_the_footage import skill as ask
    observation = ask.run(good_video, "Is the picture intact?",
                          str(tmp_path), "review_rough_cut")
    assert observation["vision"]["available"] is False
    assert "reason" in observation["vision"]
    assert observation["deterministic_passed"] is True


# ── The must-check rule: receipts, not self-reports ───────────────

def test_skipped_gating_skill_fails_with_the_reason_named(tmp_path):
    manifest = {"skills": ["verify_render"]}
    with pytest.raises(pipeline_skills.GatingSkillSkipped,
                       match="verify_render"):
        pipeline_skills.assert_gating_skills_ran(
            "validate", manifest, str(tmp_path))


def test_ran_gating_skill_reads_back_from_disk(tmp_path, good_video):
    from library.skills.verify_render.skill import run
    run(good_video, str(tmp_path), "validate",
        expected_resolution=[1080, 1920], expected_fps=30)
    receipts = pipeline_skills.assert_gating_skills_ran(
        "validate", {"skills": ["verify_render"]}, str(tmp_path))
    assert "verify_render" in receipts
    assert receipts["verify_render"]["result"]["passed"] is True


def test_report_only_declaration_needs_no_receipt(tmp_path):
    assert pipeline_skills.assert_gating_skills_ran(
        "review_rough_cut", {"skills": ["ask_the_footage"]},
        str(tmp_path)) == {}


def test_pipeline_runs_the_gate_for_a_shell_less_harness(
        tmp_path, good_video):
    """`api` cannot invoke: the pipeline runs verify_render itself and
    the verdict comes back as retry-context text."""
    manifest = {"skills": ["verify_render"]}
    inputs = {"project_folder": str(tmp_path),
              "rendered_output": {"output_path": good_video},
              "assembly_manifest": {
                  "project": {"frame_rate": 30,
                              "resolution": [1080, 1920],
                              "duration_seconds": 2.0}}}
    fed = pipeline_skills.ensure_gating_receipts(
        "validate", manifest, inputs, "api")
    assert "PASSED" in fed
    receipts = pipeline_skills.read_receipts(str(tmp_path), "validate")
    assert receipts["verify_render"]["result"]["passed"] is True


def test_pipeline_route_refuses_a_missing_video_path(tmp_path):
    manifest = {"skills": ["verify_render"]}
    inputs = {"project_folder": str(tmp_path),
              "rendered_output": {},
              "assembly_manifest": {}}
    with pytest.raises(pipeline_skills.UnrunnableSkill,
                       match="output_path"):
        pipeline_skills.ensure_gating_receipts(
            "validate", manifest, inputs, "api")


# ── The block reaches the archived prompt the model reads ─────────

def test_skill_block_reaches_the_archived_request(tmp_path, monkeypatch):
    """The regression this pins: a catalogue nothing is told about."""
    from library.processes.edit_video import run_pipeline
    from library.tools.project_layout import Area, layout_for

    node_id = "validate"
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()

    handoff = tmp_path / "handoff.md"
    handoff.write_text("# Step\n\nSENTINEL_HANDOFF_BODY\n",
                       encoding="utf-8")

    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = run_pipeline._agent_sleep

    def _sleep(seconds):
        (responses / f"{node_id}.json").write_text(
            json.dumps({"validation_result": {"status": "pass"}}))
        return original_sleep(0)

    monkeypatch.setattr(run_pipeline, "_agent_sleep", _sleep)

    manifest = json.loads(
        (REPO / "library/steps/step_6_02_validate_output/manifest.json"
         ).read_text(encoding="utf-8"))
    run_pipeline.present_llm_step(
        str(handoff),
        {"project_folder": str(project)},
        node_id, manifest, full_auto="agent", llm_timeout=5)

    prompt = json.loads(
        (layout.read_dir(Area.LLM_REQUESTS)
         / f"{node_id}.json").read_text())["prompt"]
    assert "verify_render" in prompt
    assert "GATING" in prompt
    assert "SENTINEL_HANDOFF_BODY" in prompt


def test_replay_bench_mirrors_the_skill_block():
    source = (REPO / "library/tools/replay_bench/reconstruct.py"
              ).read_text(encoding="utf-8")
    assert "import pipeline_skills as _bench_skills" in source
    assert "_bench_skills.prompt_block(" in source


# ── The hybrid loop enforces the must-check ───────────────────────

def _hybrid_step_dir(tmp_path, node_id):
    step_dir = tmp_path / "step"
    step_dir.mkdir()
    (step_dir / "handoff.md").write_text(
        "# Step\n\nDecide.\n\n"
        "<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->\n",
        encoding="utf-8")
    (step_dir / "post_bridge.py").write_text(
        "import json, sys\n"
        "data = json.load(sys.stdin)\n"
        "json.dump({'ok': True, "
        "'answer': data.get('the_answer')}, sys.stdout)\n",
        encoding="utf-8")
    return step_dir


def _agent_answer(monkeypatch, run_pipeline, responses_dir, node_id,
                  payload, on_attempt=None):
    original_sleep = run_pipeline._agent_sleep
    attempts = {"n": 0}

    def _sleep(seconds):
        attempts["n"] += 1
        if on_attempt:
            on_attempt(attempts["n"])
        (responses_dir / f"{node_id}.json").write_text(json.dumps(payload))
        return original_sleep(0)

    monkeypatch.setattr(run_pipeline, "_agent_sleep", _sleep)
    return attempts


def test_hybrid_step_retries_until_the_gate_has_run(tmp_path, monkeypatch,
                                                    good_video):
    """First answer claims the check with no receipt; the step is asked
    again carrying the violation, the skill runs, the step passes."""
    from library.processes.edit_video import run_pipeline
    from library.tools.project_layout import Area, layout_for

    node_id = "skill_probe"
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()
    step_dir = _hybrid_step_dir(tmp_path, node_id)
    manifest = {"id": node_id, "skills": ["verify_render"],
                "interface": {"outputs": [
                    {"name": "the_answer", "type": "string"}]}}
    inputs = {"project_folder": str(project)}

    def _on_attempt(n):
        if n >= 2:
            from library.skills.verify_render.skill import run
            run(good_video, str(project), node_id,
                expected_resolution=[1080, 1920], expected_fps=30)

    _agent_answer(monkeypatch, run_pipeline,
                  layout.write_dir(Area.LLM_RESPONSES), node_id,
                  {"the_answer": "approved"}, on_attempt=_on_attempt)

    out = run_pipeline.run_hybrid_step(
        step_dir, inputs, node_id, manifest, full_auto="agent",
        llm_timeout=5)
    assert out["ok"] is True


def test_hybrid_step_fails_when_the_gate_never_runs(tmp_path, monkeypatch):
    """At the bound the step FAILS with the reason named - it does not
    proceed on an unchecked answer."""
    from library.processes.edit_video import run_pipeline
    from library.tools.project_layout import Area, layout_for
    from library.tools import post_bridge_retry

    node_id = "skill_probe"
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()
    step_dir = _hybrid_step_dir(tmp_path, node_id)
    manifest = {"id": node_id, "skills": ["verify_render"],
                "interface": {"outputs": [
                    {"name": "the_answer", "type": "string"}]}}
    monkeypatch.setattr(post_bridge_retry, "MAX_ATTEMPTS", 2)
    _agent_answer(monkeypatch, run_pipeline,
                  layout.write_dir(Area.LLM_RESPONSES), node_id,
                  {"the_answer": "approved"})

    with pytest.raises(Exception, match="verify_render"):
        run_pipeline.run_hybrid_step(
            step_dir, {"project_folder": str(project)}, node_id,
            manifest, full_auto="agent", llm_timeout=5)


def test_gating_skill_on_non_hybrid_step_is_refused(tmp_path):
    """The read-back only covers hybrid answers; a gating skill
    elsewhere renders untracked, so it is refused at plan time."""
    from pathlib import Path as _Path
    from library.processes.edit_video.run_pipeline import (
        get_step_implementation)
    step_dir = tmp_path / "llm_step"
    step_dir.mkdir()
    (_Path(step_dir) / "handoff.md").write_text("# Step\n",
                                                encoding="utf-8")
    manifest = {"id": "probe", "skills": ["verify_render"],
                "implementation": {"default": {"runtime": "llm"}}}
    (_Path(step_dir) / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="non-hybrid"):
        get_step_implementation(_Path(step_dir))
