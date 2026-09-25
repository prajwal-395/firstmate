"""Recallable skills: the catalogue a step can call, that the model is told about.

The captain's ask: *"refactor the pipeline to have like recallable
skills ... that various steps in the pipeline can call upon ... a
checker skill where the pipeline would be able to verify edits ...
this is something the LLM in the pipeline needs to understand that it
must do so that we don't end up in cases where we just start creating
and rendering pipelines that have errors that could just be fixed if
the LLM bothered to check."*

Two halves, equally important: the catalogue exists in
`library/skills/`, AND the model knows it must reach for it. A
catalogue nothing is told about is 177 more tools in `library/tools/`.

This module is the single owner of what skills exist and how they are
rendered into a prompt - the same shape as `craft_role`: one function
returning a block, empty for a step that declares none, so a step that
gains a skill needs no change in the runner. Adding a skill costs one
`SKILLS` row, its entry point under `library/skills/<name>/` and its
text at `.agents/skills/<name>/SKILL.md` - the one skill source every
agent harness reads (`SKILL_TEXT_ROOT`); the runner,
the prompt site, the receipt paths and the gating check all read the
registry, so none of them changes. `tests/test_pipeline_skills.py`
pins that by registering a synthetic further skill against the
registry without touching runner code.

A manifest declares its skills at the TOP LEVEL under `skills`, a list
of names. The `context_fields` precedent
(`library/tools/context_projector.py`) is followed exactly: a
declaration nothing reads cost this pipeline 817,317 characters of raw
transcript in every reel selection ever made, so an unread declaration
is REFUSED, not ignored - in BOTH directions:

- a named skill that does not exist fails (`UnknownSkill`);
- a declared skill that never reaches the prompt fails
  (`assert_declared_skills_reach_prompt`), because a skill the model
  was never told about is the catalogue-nothing-is-told-about again;
- a `skills` list under `interface`, where it reads as though it
  belongs, is REFUSED (`MisplacedSkills`) - that is exactly how step
  3.04 shipped an allow-list the projection never applied.

Kinds
-----
- `gate`: deterministic, reads real state, can fail. `verify_render`
  wraps the file-only half of `render_qa`; `verify_treatment` wraps
  the before/after half of `treatment_verify`; `verify_timeline`
  wraps `timeline_conformance.verify_timeline` over the live
  timeline. A step that declares a gating skill and does not run it
  FAILS (`GatingSkillSkipped`),
  enforced in `run_hybrid_step` through the post-bridge retry path -
  the violation is carried back to the model bounded, and at the bound
  the step fails with the reason named. Skills feed the existing
  feedback path (`qa_feedback_loop.LLMStepQA`, `post_bridge_retry`); no
  second feedback path is built.
- `report`: model-judged, on request only. `ask_the_footage` wraps the
  `visual_qa_router` still/segment paths plus Gemma vision. Its opinion
  is recorded into the retry context, never enforced - AGENTS.md 10.4:
  a model-judged addition gets a deterministic half that can carry the
  verdict, and the model's opinion is recorded rather than enforced.

Two delivery routes, one contract
---------------------------------
`HARNESS_INVOKES_SKILLS` says which harnesses can invoke a skill
themselves. `agent` has a shell - proven on the run of record, where
the answering agent read repository source and ran ffmpeg
(`brief_reference.HARNESS_READS_FILES`) - so under `agent` the model is
told to invoke the skill itself via its shell entry point. `api` posts
one string with no tool loop on the other side, so the model there
cannot invoke anything: the pipeline runs the skill for it and feeds
the result back through the retry context, the same route a contract
rejection travels. `mock` replays a recorded answer - no model runs,
so the prompt block still renders (a recorded answer is unaffected
either way) but the must-check read-back is skipped: there is no model
to correct. An unknown harness raises rather than being assumed either
way.

The must-check rule, and what it is read back from
-----------------------------------------------
A step that declares a gating skill records which skills it ran - as
RECEIPTS, written by the skill entry point itself (code, not the
model) to `<project>/pipeline_output/skill_runs/<step_id>/<skill>.json`
carrying the measured per-check verdicts. `assert_gating_skills_ran`
reads those receipts back from disk. A self-reported check writes no
receipt, so saying "I checked" without invoking fails the step - a
self-reported check is the gate-that-cannot-fail again, and AGENTS.md
10.4 refuses it as coverage.

Coverage of the read-back, stated honestly: it runs where an answer
passes through `run_hybrid_step` with a `project_folder` in its
inputs. Deterministic steps take no prompt and run their checks inline
in their own step.py - the code path IS the check there, so there is
nothing to read back. `llm_only` / `deterministic_with_llm` answers
returned by `present_llm_step` are told about their skills in the
prompt but have no post-answer read-back; a gating skill declared on
such a step is refused at the runner's check rather than silently
unenforced... (see `GATING_COVERED_TYPES`).
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

#: Where receipts live, relative to the project folder. Skill runs are
#: this run's evidence that a check ran - reproducible by a re-run, so
#: they sit under pipeline_output/ beside llm_requests/, not beside the
#: captain's irreplaceable inputs.
RECEIPTS_RELDIR = os.path.join("pipeline_output", "skill_runs")

GATE = "gate"
REPORT = "report"

#: The one source of skill TEXT, relative to the repository root. Codex
#: and opencode read `.agents/skills/` natively; `.claude/skills` is a
#: link to it for Claude Code. A skill's entry point stays a Python
#: package under `library/skills/`; its SKILL.md lives here, so every
#: harness - and a pipeline model told to read it - reads one text.
SKILL_TEXT_ROOT = os.path.join(".agents", "skills")


def skill_text(name: str) -> str:
    """Where a skill's SKILL.md lives, repository-relative."""
    return f"`{os.path.join(SKILL_TEXT_ROOT, name, 'SKILL.md')}`"


@dataclass(frozen=True)
class Skill:
    """One row of the catalogue.

    Attributes:
        name: The name a manifest declares and the prompt names. One
            spelling, here - a key spelled twice is this repository's
            dominant bug class (AGENTS.md 10.1).
        kind: `gate` (deterministic, can fail the step) or `report`
            (model-judged, recorded never enforced).
        module: Import path of the skill package under `library/skills/`.
            The entry point is `<module>.skill.run`, and the shell route
            is `python3 -m <module>.skill`. Registry-driven dispatch, so
            a new skill needs no runner change.
        when: WHEN a step should reach for it. Rendered into the prompt.
        cost: What invoking it spends. Rendered into the prompt.
        returns: What it hands back and what that means. Rendered into
            the prompt.
        pipeline_args: How the pipeline invokes this skill on a model's
            behalf (the harness-without-a-shell route). Takes the step's
            inputs, returns kwargs for `run`, or raises
            `UnrunnableSkill` saying what is missing. None means the
            pipeline cannot run it for the model - allowed only for
            `report` skills, where asking is optional.
    """
    name: str
    kind: str
    module: str
    when: str
    cost: str
    returns: str
    pipeline_args: Optional[Callable[[dict], dict]] = None


def _verify_render_args(inputs: dict) -> dict:
    """Where the render and what was asked of it, off the step's inputs.

    `validate` is handed `rendered_output` (step 6.01's product, whose
    video path is `output_path`) and `assembly_manifest` (whose
    `project` block carries what the compile asked for). Missing video
    path is `UnrunnableSkill`, not a default - a gate that checks a
    guessed file is worse than one that says it cannot run.
    """
    rendered = inputs.get("rendered_output") or {}
    if not isinstance(rendered, dict):
        rendered = {}
    video = (rendered.get("output_path")
             or inputs.get("video_path") or "")
    if not video:
        raise UnrunnableSkill(
            "verify_render needs rendered_output.output_path and the "
            "inputs carry no video path.")
    manifest = inputs.get("assembly_manifest") or {}
    project = (manifest.get("project") or {}) if isinstance(
        manifest, dict) else {}
    duration = project.get("duration_seconds") or None
    if duration is not None and not float(duration) > 0:
        duration = None
    return {
        "video_path": video,
        "expected_duration": duration,
        "expected_resolution": project.get("resolution"),
        "expected_fps": project.get("frame_rate"),
    }


SKILLS: Dict[str, Skill] = {
    "verify_render": Skill(
        name="verify_render",
        kind=GATE,
        module="library.skills.verify_render",
        when=("You are judging a render, or about to approve, revise or "
              "build on one. Run this FIRST, before writing any judgement "
              "of your own. Never to judge a plan on paper - it measures "
              "a file, and with no file it refuses rather than passes."),
        cost=("Free. No model, no Resolve, no GPU - seconds of ffmpeg on "
              "the file."),
        returns=("A verdict that GATES: `passed` plus one row per check. "
                 "`passed: false` means the render is wrong - say which "
                 "check failed and what it measured, and do not approve "
                 "the render."),
        pipeline_args=_verify_render_args,
    ),
    "ask_the_footage": Skill(
        name="ask_the_footage",
        kind=REPORT,
        module="library.skills.ask_the_footage",
        when=("You are judging a visual decision and the prose does not "
              "carry appearance - readability, transitions, grade intent, "
              "cropping - or a deterministic check failed and you want to "
              "know whether it is real before acting on it. Never on "
              "every build and never as a substitute for verify_render; "
              "never when the numbers in front of you already answer."),
        cost=("The deterministic half is free. The Gemma half loads a "
              "~7.5GB model on first use and spends ~6s per still; the "
              "receipt records what your call cost."),
        returns=("An OBSERVATION, never a verdict: what ffmpeg measured "
                 "(which can fail) plus the model's recorded opinion with "
                 "its confidence - or `available: false` with the reason. "
                 "Recorded into the retry context, never enforced."),
        pipeline_args=None,
    ),
    "verify_treatment": Skill(
        name="verify_treatment",
        kind=GATE,
        module="library.skills.verify_treatment",
        when=("You are planning a visual treatment - drift, a switch "
              "animation, any per-clip comp key - and the picture it "
              "draws is your claim to check. Run this FIRST, on your own "
              "params, before you answer. Never to judge a finished "
              "file - that is verify_render - and never when the numbers "
              "in front of you already answer."),
        cost=("Free. Two comp builds plus a spline evaluation per "
              "rendered frame - milliseconds per clip, no Resolve, no "
              "model; the receipt records what your call cost. Stills "
              "are cheap ffmpeg seeks. The Gemma pass runs only when "
              "you ask for it, and its answer is recorded, never "
              "enforced."),
        returns=("A verdict that GATES: `passed` plus the before/after "
                  "frames that changed, the declared window, and what "
                  "the picture keeps. `passed: false` names the failure "
                  "(`outside_window`, `drew_nothing`, `never_settles`) - "
                  "say which and what it measured, and do not ship the "
                  "treatment. Change or drop the entry instead: the "
                  "applier undoes a failed treatment itself."),
        pipeline_args=None,
    ),
    "verify_timeline": Skill(
        name="verify_timeline",
        kind=GATE,
        module="library.skills.verify_timeline",
        when=("You just built or placed a timeline, or you are about "
              "to approve, revise or build on one. Run this FIRST, "
              "before writing any judgement of your own. Never to "
              "judge a plan on paper - that is verify_treatment - or "
              "a finished file - that is verify_render: it reads the "
              "live timeline in Resolve, and with no timeline it "
              "refuses rather than passes."),
        cost=("A shared read lease on the one Resolve instance - "
              "seconds, no render, no model, no GPU. Needs Resolve "
              "running with the exact project open; otherwise it "
              "refuses rather than passes. Pass the build result's "
              "track_plan or the link and stream checks are skipped "
              "openly."),
        returns=("A verdict that GATES: `passed`, one row per check "
                 "it ran, the checks it openly skipped, and the SOP "
                 "violations. `passed: false` means the timeline "
                 "disobeys the SOP - say which check failed and what "
                 "it measured, and do not approve the timeline."),
        pipeline_args=None,
    ),
    "hear_the_reel": Skill(
        name="hear_the_reel",
        kind=REPORT,
        module="library.skills.hear_the_reel",
        when=("You are judging, revising or building on a DELIVERED "
              "reel and the question is whether what it says lines up "
              "with what was planned - a caption, a karaoke highlight, "
              "a take boundary or a cut that looks wrong, or before you "
              "tell the captain a reel is fine. Never to judge a plan "
              "on paper: it hears a FILE, and with no rendered file it "
              "refuses rather than passes."),
        cost=("Seconds. 3.5s measured for a 45.9s reel - no Resolve, no "
              "GPU, no render, no model call. It needs the on-device "
              "transcriber installed; absent, it says so rather than "
              "reporting a reel nobody listened to as clean."),
        returns=("An OBSERVATION, never a verdict: one row per check - "
                 "script divergence, timing drift, caption coverage and "
                 "transcript row fit - with the evidence under each, or "
                 "`available: false` with the reason. Nothing here "
                 "gates. Read `transcriber_anomalies` and "
                 "`unfitted_transcript_rows` before blaming the edit."),
        pipeline_args=None,
    ),
}

SKILLS_KEY = "skills"


class MisplacedSkills(ValueError):
    """A manifest declared its skills where nothing reads them."""


class UnknownSkill(ValueError):
    """A manifest named a skill the catalogue does not hold."""


class UnreachedSkill(RuntimeError):
    """A declared skill the rendered prompt never told the model about."""


class UnrunnableSkill(RuntimeError):
    """The pipeline cannot run this skill on the model's behalf."""


class GatingSkillSkipped(RuntimeError):
    """A declared gating skill with no receipt: the check never ran."""


class UnknownHarness(ValueError):
    """A harness whose skill reach has not been established."""


# Which harnesses can invoke a skill themselves. A COMPLETE
# enumeration: an unknown name raises rather than being assumed either
# way, because a harness whose reach nobody has established is not a
# harness known to reach.
#
# - `agent`: the request is a file on disk answered by an agent with a
#   shell and its own file tools - proven on the run of record, where
#   the answering agent read repository source and ran ffmpeg (the same
#   establishment `brief_reference.HARNESS_READS_FILES` records).
# - `mock`: replays a recorded answer. No model runs; listed as
#   invoking because a recorded answer is unaffected either way - the
#   same reading the file/frames enumerations give. The must-check
#   read-back is still skipped under mock: there is no model to
#   correct, only history.
# - `api`: the removed `llm_client.LLMClient.generate` took ONE string
#   and posted it. There was no tool loop and no shell on the other
#   side, so a shell command in the prompt was a dead end - the
#   pipeline runs the skill and feeds the result back instead.
HARNESS_INVOKES_SKILLS = {
    "agent": True,
    "mock": True,
    "api": False,
}

# Runner paths whose answers pass the must-check read-back. Hybrid
# answers return through `run_hybrid_step`, which owns the bounded
# retry the violation travels on. `llm_only` and
# `deterministic_with_llm` answers return straight out of
# `present_llm_step` with no retry loop to carry a violation on, so a
# gating skill declared on one is refused rather than silently
# unenforced - that is the coverage as built, not as wished.
GATING_COVERED_TYPES = ("hybrid",)


def harness_invokes_skills(harness: str) -> bool:
    """True when `harness` can invoke a skill itself via a shell."""
    if harness not in HARNESS_INVOKES_SKILLS:
        raise UnknownHarness(
            f"harness {harness!r} is not in HARNESS_INVOKES_SKILLS "
            f"({sorted(HARNESS_INVOKES_SKILLS)}). Establish whether a "
            f"model answering through it has a shell to invoke a skill "
            f"with, and record the answer there - a harness whose reach "
            f"nobody has established is not a harness known to reach."
        )
    return HARNESS_INVOKES_SKILLS[harness]


def declared_skills(manifest, step_id: str = "") -> Optional[List[str]]:
    """The skills this manifest declares, or None for "none".

    ONE location - the manifest's top level. A declaration under
    `interface`, beside `inputs` and `outputs` where it reads as though
    it belongs, is REFUSED: that is exactly how step 3.04 shipped a
    context allow-list the projection never applied, and a
    silently-ignored declaration is indistinguishable from a step that
    declares none. A name the catalogue does not hold is REFUSED for
    the same reason a misspelt key is: the model would be told about a
    skill nothing can run.
    """
    if not isinstance(manifest, dict):
        return None
    interface = manifest.get("interface")
    if isinstance(interface, dict) and SKILLS_KEY in interface:
        where = f"{step_id}: " if step_id else ""
        raise MisplacedSkills(
            f"{where}`{SKILLS_KEY}` is declared under `interface`, where "
            f"nothing reads it, so the skill block would never reach the "
            f"prompt and the model would never be told. Move it to the "
            f"manifest's top level - see "
            f"library/tools/pipeline_skills.declared_skills."
        )
    names = manifest.get(SKILLS_KEY)
    if names is None:
        return None
    if not isinstance(names, list) or not all(
            isinstance(n, str) for n in names):
        raise UnknownSkill(
            f"{step_id or 'step'}: `{SKILLS_KEY}` must be a list of skill "
            f"names, got {names!r}.")
    unknown = [n for n in names if n not in SKILLS]
    if unknown:
        raise UnknownSkill(
            f"{step_id or 'step'}: unknown skill(s) "
            f"{', '.join(unknown)} - the catalogue holds "
            f"{', '.join(sorted(SKILLS))}. A skill nothing can run must "
            f"not be promised to the model.")
    return list(names)


def _invocation_lines(skill: Skill, full_auto: Optional[str]) -> str:
    """How THIS model invokes THIS skill, for the harness answering."""
    module_cli = f"{skill.module}.skill"
    if full_auto is None:
        return (
            f"- You are answering by hand: run it yourself with "
            f"`python3 -m {module_cli} ...` "
            f"({skill_text(skill.name)} carries its exact flags) before "
            f"you answer.")
    invokes = harness_invokes_skills(full_auto)
    if invokes and full_auto != "mock":
        if skill.kind == GATE:
            return (
                f"- You have a shell: invoke it yourself with "
                f"`python3 -m {module_cli} ...` (exact flags in "
                f"{skill_text(skill.name)}) BEFORE you answer. The run "
                f"writes a receipt "
                f"the pipeline reads back - asserting you checked "
                f"without invoking fails the step.")
        return (
            f"- You have a shell: invoke it yourself with "
            f"`python3 -m {module_cli} ...` (exact flags in "
            f"{skill_text(skill.name)}) whenever the WHEN below holds.")
    if full_auto == "mock":
        return ("- This run replays a recorded answer: no invocation is "
                "expected from you.")
    if skill.pipeline_args is not None:
        return (
            f"- You have no shell, so say which check you need and the "
            f"pipeline runs `{skill.name}` for you: its verdict is fed "
            f"back into your context and still "
            f"{'gates' if skill.kind == GATE else 'reports'}.")
    if skill.kind == GATE:
        return (
            f"- You have no shell and the pipeline has no route to run "
            f"`{skill.name}` for you - it needs your answer's own effect "
            f"params, which exist only once you answer. Say so and do "
            f"not answer until the check runs: a declared gating skill "
            f"that did not run fails the step.")
    return (
        "- You have no shell and this skill is ask-on-request: say what "
        "you need to see and the pipeline runs it for you, or decide "
        "from the numbers in front of you.")


def prompt_block(step_id: str, manifest=None,
                 full_auto: Optional[str] = None) -> str:
    """The skills, as the text appended to that step's prompt.

    Empty for a step that declares none, so the call site is one
    unconditional line and a step that gains a skill needs no runner
    change. A step that declares skills gets one section naming each:
    what it is, WHEN to reach for it, what it costs, what it returns,
    and - branched on the answering harness - how to invoke it.
    """
    names = declared_skills(manifest, step_id) if manifest else None
    if not names:
        return ""
    lines = ["## Recallable skills: checks you can call, and must",
             "",
             ("These are reusable checks the pipeline can run. A skill "
              "you are told about here is one you can invoke; a gating "
              "skill is one you MUST invoke before you answer - the "
              "pipeline reads back whether it ran, and answering without "
              "running it fails the step."),
             ""]
    for name in names:
        skill = SKILLS[name]
        kind_word = ("GATING - you MUST run this before you answer"
                     if skill.kind == GATE else
                     "REPORTING - on request only, never enforced")
        lines += [f"### {name} ({kind_word})",
                  "",
                  f"WHEN: {skill.when}",
                  "",
                  f"COST: {skill.cost}",
                  "",
                  f"RETURNS: {skill.returns}",
                  "",
                  _invocation_lines(skill, full_auto),
                  ""]
    lines += ["---", "", ""]
    return "\n".join(lines)


def assert_declared_skills_reach_prompt(step_id: str, manifest,
                                        prompt: str) -> None:
    """Every declared skill reaches the prompt the model reads.

    The second refusal direction: a declaration the prompt never
    carries is the catalogue-nothing-is-told-about again, and it must
    fail rather than read as a step that declares none.
    """
    names = declared_skills(manifest, step_id) if manifest else None
    for name in names or []:
        if name not in (prompt or ""):
            raise UnreachedSkill(
                f"{step_id}: declared skill {name!r} never reaches the "
                f"prompt. A skill the model is not told about is a "
                f"catalogue entry nothing can recall - wire the block "
                f"through present_llm_step or undeclare it.")


# ── Receipts: the record a check ran ──────────────────────────────

def receipt_path(project_folder: str, step_id: str,
                 skill_name: str) -> str:
    """Where this step's receipt for this skill lives."""
    return os.path.join(str(project_folder), RECEIPTS_RELDIR,
                        step_id, f"{skill_name}.json")


def write_receipt(project_folder: str, step_id: str,
                  skill_name: str, result: Dict[str, Any]) -> str:
    """Record a skill run. Written by the skill entry point (code).

    The receipt carries the measured verdict, not the model's word
    that it checked - which is what makes it readable-back state
    rather than a self-report. Returns the receipt path.
    """
    path = receipt_path(project_folder, step_id, skill_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "step_id": step_id,
        "skill": skill_name,
        "recorded_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "result": result,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path


def read_receipts(project_folder: str,
                  step_id: str) -> Dict[str, dict]:
    """Every receipt this step has on disk, by skill name."""
    receipts: Dict[str, dict] = {}
    step_dir = os.path.join(str(project_folder), RECEIPTS_RELDIR,
                            step_id)
    if not os.path.isdir(step_dir):
        return receipts
    for name in sorted(os.listdir(step_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(step_dir, name), encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError):
            continue
        skill = record.get("skill") or name[:-len(".json")]
        receipts[skill] = record
    return receipts


def gating_skills(manifest, step_id: str = "") -> List[str]:
    """The declared skills that gate: run-or-fail."""
    return [n for n in
            (declared_skills(manifest, step_id) or [])
            if SKILLS[n].kind == GATE]


def _gate_applies(skill_name: str, step_id: str,
                  llm_output: Optional[dict]) -> bool:
    """Whether this gating skill's receipt is required for this answer.

    A gate scoped to what its skill knows (finding 5): a skill module
    may expose `gate_applies(step_id, llm_output)`, and where it does
    the must-check rule asks it. No hook, or no answer to judge, means
    the gate applies - scoping is opt-in per skill, and a failure to
    scope fails CLOSED (the check runs) rather than open. See
    `library/skills/verify_treatment/skill.gate_applies`.
    """
    if llm_output is None:
        return True
    if skill_name not in SKILLS:
        return True
    try:
        module = __import__(SKILLS[skill_name].module + ".skill",
                            fromlist=["gate_applies"])
    except ImportError:
        return True
    applies = getattr(module, "gate_applies", None)
    if applies is None:
        return True
    try:
        return bool(applies(step_id, llm_output))
    except Exception:  # noqa: BLE001 - a scoping bug must not open the gate
        return True


def assert_gating_skills_ran(step_id: str, manifest,
                             project_folder: str,
                             llm_output: Optional[dict] = None
                             ) -> Dict[str, dict]:
    """The must-check rule: every declared gating skill ran.

    Read back from the receipts on disk - real state the skill entry
    point wrote - never from the answer's claim that it checked.
    Returns the receipts so the caller can also read their verdicts.
    Raises `GatingSkillSkipped` naming the skill when its receipt is
    absent. Skipped when the step declares no gating skill, and when
    there is no project folder to read back from.

    `llm_output` scopes the rule to what the skill knows: a gating
    skill whose module says the gate does not apply to this answer
    needs no receipt (finding 5 - an empty 4.03 plan, or one naming
    only effects with no plan-time verifier). Absent, the gate
    applies unconditionally, as before.
    """
    needed = gating_skills(manifest, step_id)
    if not needed or not project_folder:
        return {}
    receipts = read_receipts(project_folder, step_id)
    missing = [n for n in needed if n not in receipts]
    if missing:
        missing = [n for n in missing
                   if _gate_applies(n, step_id, llm_output)]
    if missing:
        raise GatingSkillSkipped(
            f"{step_id}: declared gating skill(s) "
            f"{', '.join(missing)} never ran - no receipt at "
            f"{RECEIPTS_RELDIR}/{step_id}/. Run the skill before "
            f"answering; asserting the check without invoking it fails "
            f"the step.")
    return {n: receipts[n] for n in needed if n in receipts}


# ── The pipeline-runs-it route ────────────────────────────────────

def run_skill(name: str, **kwargs) -> Dict[str, Any]:
    """Invoke a skill by registry name. No runner change per skill.

    Dispatch reads `SKILLS`, so a third skill is one registry row plus
    its directory - this function, the prompt block and the receipts
    never change. Raises `UnknownSkill` for a name nothing holds.
    """
    if name not in SKILLS:
        raise UnknownSkill(
            f"unknown skill {name!r} - the catalogue holds "
            f"{', '.join(sorted(SKILLS))}.")
    module = __import__(SKILLS[name].module + ".skill",
                        fromlist=["run"])
    return module.run(**kwargs)


def ensure_gating_receipts(node_id: str, manifest, inputs: dict,
                            full_auto: Optional[str]) -> str:
    """Run what the model cannot invoke itself, before it answers.

    For each declared gating skill with no receipt yet: when the
    answering harness has a shell, the skill is the model's to invoke
    (its prompt says so) and nothing runs here. When it has none, the
    pipeline invokes the skill itself and the verdict returns as text
    for the retry context - the `LLMStepQA` route, not a second
    feedback path. Skipped entirely under `mock`: a replayed answer
    has no model to correct. Returns context text ("" when nothing
    ran) for the caller to seed the retry context with.
    """
    needed = gating_skills(manifest, node_id)
    if not needed:
        return ""
    if (full_auto or "") == "mock":
        return ""
    project_folder = (inputs or {}).get("project_folder", "")
    if not project_folder:
        return ""
    invokes = (harness_invokes_skills(full_auto)
               if full_auto else False)
    if invokes:
        return ""
    fed: List[str] = []
    for name in needed:
        if name in read_receipts(project_folder, node_id):
            continue
        skill = SKILLS[name]
        if skill.pipeline_args is None:
            raise UnrunnableSkill(
                f"{node_id}: harness {full_auto!r} cannot invoke "
                f"{name!r} and the pipeline has no route to run it "
                f"either. Declare it only on steps answered through a "
                f"harness with a shell.")
        kwargs = dict(skill.pipeline_args(inputs))
        kwargs.setdefault("project_folder", project_folder)
        kwargs.setdefault("step_id", node_id)
        result = run_skill(name, **kwargs)
        checks = "; ".join(
            f"{c['name']}: {'pass' if c['passed'] else 'FAIL'}"
            for c in result.get("checks", []))
        fed.append(
            f"\n\n[{name} ran for you - harness {full_auto!r} has no "
            f"shell to invoke it with. Verdict: "
            f"{'PASSED' if result.get('passed') else 'FAILED'}. "
            f"{checks or result.get('issues', [])}]")
    return "".join(fed)
