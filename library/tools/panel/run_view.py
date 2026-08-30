"""Driving a configured run from the panel: preview, launch, hold, resume.

Everything here goes through the modules that already own the question -
`run_profile` for what a profile is, `run_scope` for what a selection
means, `breakpoints` for where a run stops, `run_control` for the
handbrake, `review_gate` for the pause.  The panel does not re-implement
one of them, which is the scout's own structural recommendation and the
reason the step-id bug in its prototype cannot happen here.

The preview is the point
------------------------
`run_control.py` is already a file protocol, so starting a run from the
panel is a writer and a reader, not a server.  What the panel adds is
that the captain SEES the selection before it runs: which steps this
profile will and will not fire, WHY each excluded one is excluded, and -
when the selection cannot be met - the refusal, in the panel, in the
second before anything is deleted or written.  That is the same refusal
`run_scope` raises; it is not a second opinion.

The interpreter, and why it is not `sys.executable`
---------------------------------------------------
The dashboard launches the runner with `sys.executable` and AGENTS.md
section 4 says to, because the dashboard's interpreter is the one that
carries the ML stack.  The panel's is not: Resolve launches whatever
Python it finds - stock `/usr/bin/python3` on macOS - and
`run_pipeline.py` imports `whisperx`, `mlx_vlm` and `torch` through its
steps.  So the panel resolves the checkout's own `.venv/bin/python3` and
REFUSES BY NAME when there is none, rather than launching an interpreter
that will die on the first import forty seconds in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from library.tools import (
    breakpoints as run_breakpoints,
    external_inputs,
    review_gate,
    run_control,
    run_profile,
    run_scope,
)


# ── Which interpreter runs the pipeline ──────────────────────────────

VENV_INTERPRETER = os.path.join(".venv", "bin", "python3")


def pipeline_interpreter(repo_root: str) -> Tuple[str, str]:
    """`(path, "")` or `("", why not)`.

    Never falls back to `sys.executable`: under Resolve that is a stock
    interpreter with none of the ML stack, and a run launched with it
    dies inside a step's import with a traceback the captain has to go
    looking for in a log.
    """
    candidate = os.path.join(repo_root, VENV_INTERPRETER)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate, ""
    return "", (
        f"This checkout has no {VENV_INTERPRETER}. `run_pipeline.py` imports "
        f"whisperx, mlx_vlm and torch through its steps, and the interpreter "
        f"Resolve launched this panel with ({sys.executable}) carries none of "
        f"them. Make one with `python3 -m venv .venv && pip install -r "
        f"requirements.txt` in {repo_root}, or start the run from a terminal.")


# ── The configuration, previewed ─────────────────────────────────────

@dataclass
class Preview:
    """What a run WOULD do, resolved before anything is launched."""

    profile: Any = None
    profile_error: str = ""
    steps_to_run: Tuple[str, ...] = ()
    skipped: Tuple[str, ...] = ()
    reasons: Dict[str, str] = field(default_factory=dict)
    default_off: Tuple[str, ...] = ()
    from_cache: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    from_external: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    breakpoints: Any = None
    breakpoint_error: str = ""
    unreachable_breakpoints: Tuple[str, ...] = ()
    refusal: str = ""
    """The `run_scope` refusal, verbatim. Empty when the selection can
    run. This is the whole reason the preview exists: the captain sees it
    here rather than forty minutes into a run."""

    estimated_seconds: Dict[str, int] = field(default_factory=dict)

    @property
    def can_run(self) -> bool:
        return not (self.refusal or self.profile_error
                    or self.breakpoint_error)


def preview(project_folder: str, profile_name: str = "",
            skip: Sequence[str] = (), with_steps: Sequence[str] = (),
            break_at: Sequence[str] = (), no_break_at: Sequence[str] = (),
            review_all: bool = False, state: Optional[dict] = None
            ) -> Preview:
    """Resolve a run configuration WITHOUT running anything.

    Every failure comes back on the result rather than as an exception -
    the panel has to draw something, and "here is why this cannot run" is
    the thing worth drawing.
    """
    result = Preview()
    dag = run_scope.load_dag()
    manifests = run_scope.load_manifests(dag)
    known = {node["id"] for node in dag.get("nodes", [])}

    try:
        result.profile = run_profile.resolve_for_run(
            project_folder, profile_name or None, known)
    except run_profile.ProfileError as exc:
        result.profile = run_profile.NO_PROFILE
        result.profile_error = str(exc)
        return result

    try:
        result.breakpoints = run_breakpoints.resolve(
            known_steps=known,
            profile_breakpoints=result.profile.breakpoints,
            review_all=review_all,
            break_at=tuple(break_at),
            no_break_at=tuple(no_break_at),
            profile_name=result.profile.name)
    except run_breakpoints.BreakpointError as exc:
        result.breakpoints = run_breakpoints.NO_BREAKPOINTS
        result.breakpoint_error = str(exc)
        return result

    selection = run_profile.compose(
        result.profile, skip=tuple(skip), with_steps=tuple(with_steps))

    try:
        external = {key: entry.value for key, entry
                    in external_inputs.load(project_folder, state).items()}
    except external_inputs.ExternalStateError as exc:
        result.refusal = "External state does not check out:\n\n%s" % exc
        return result

    try:
        scope = run_scope.resolve(selection, dag=dag, manifests=manifests,
                                  state=state, external=external)
    except run_scope.ScopeError as exc:
        result.refusal = str(exc)
        return result

    result.steps_to_run = scope.steps_to_run
    result.skipped = scope.skipped
    result.reasons = dict(scope.reasons)
    result.default_off = scope.default_off
    result.from_cache = dict(scope.from_cache)
    result.from_external = dict(scope.from_external)
    result.estimated_seconds = run_scope.estimated_seconds(scope, dag=dag)
    result.unreachable_breakpoints = result.breakpoints.unreachable(
        scope.steps_to_run)
    return result


def available_profiles(project_folder: str) -> List[run_profile.ProfileFile]:
    catalogue = run_profile.available(project_folder)
    return [catalogue[name] for name in sorted(catalogue)]


def adopted_profile(project_folder: str) -> str:
    try:
        return run_profile.adopted_name(project_folder)
    except Exception:                              # noqa: BLE001 - a bad
        return ""                                  # project.yaml is reported
                                                   # by the preview instead


# ── Launching, holding, resuming ─────────────────────────────────────

RUNNER = os.path.join("library", "processes", "edit_video", "run_pipeline.py")


def run_argv(repo_root: str, project_folder: str, preview_result: Preview,
             skip: Sequence[str] = (), with_steps: Sequence[str] = (),
             break_at: Sequence[str] = (), no_break_at: Sequence[str] = (),
             review_all: bool = False, resume: bool = False,
             full_auto: str = "") -> Tuple[List[str], str]:
    """`(argv, "")` or `([], why not)` for the run this panel would start.

    The argv is built from the SAME words the preview resolved, so what
    the captain was shown and what is launched cannot differ.
    """
    interpreter, why_not = pipeline_interpreter(repo_root)
    if not interpreter:
        return [], why_not
    argv = [interpreter, os.path.join(repo_root, RUNNER),
            "--project", project_folder]
    profile = getattr(preview_result, "profile", None)
    if profile is not None and profile.is_declared and not profile.adopted:
        argv += ["--profile", profile.name]
    for step in skip:
        argv += ["--skip", step]
    for step in with_steps:
        argv += ["--with", step]
    for step in break_at:
        argv += ["--break", step]
    for step in no_break_at:
        argv += ["--no-break", step]
    if review_all:
        argv.append("--review")
    if resume:
        argv.append("--resume")
    if full_auto:
        argv += ["--full-auto", full_auto]
    return argv, ""


@dataclass
class Launch:
    ok: bool
    message: str
    pid: Optional[int] = None
    log_path: str = ""
    argv: List[str] = field(default_factory=list)


def start_run(repo_root: str, project_folder: str, argv: Sequence[str]
              ) -> Launch:
    """Start the runner as a child process, logging where the dashboard does.

    Refuses while a run is already up: two runners on one project would
    both write `pipeline_data.json`.
    """
    if run_control.is_running(project_folder):
        return Launch(False, "A run is already up on this project (pid %s). "
                             "Use the handbrake first."
                      % run_control.running_pid(project_folder))
    from library.tools.project_layout import Area, ProjectLayout

    logs = ProjectLayout(project_folder).write_dir(Area.LOGS)
    log_path = os.path.join(str(logs), "run_panel.log")
    try:
        handle = open(log_path, "ab")
    except OSError as exc:
        return Launch(False, "Cannot open the run log %s: %s" % (log_path, exc))
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
        process = subprocess.Popen(
            list(argv), stdout=handle, stderr=subprocess.STDOUT,
            cwd=repo_root, env=env)
    except OSError as exc:
        handle.close()
        return Launch(False, "Could not start the runner: %s" % exc)
    finally:
        try:
            handle.close()
        except OSError:
            pass
    return Launch(True, "Started. Output goes to %s" % log_path,
                  pid=process.pid, log_path=log_path, argv=list(argv))


def handbrake(project_folder: str) -> str:
    """Engage the hold. The step in flight FINISHES first.

    `run_control.py`'s docstring explains why it is advisory and that
    reasoning binds this: killing a process mid-step leaves
    `pipeline_data.json` describing a step that only half happened, and
    nothing downstream could tell. The panel never kills the runner.
    """
    if not run_control.is_running(project_folder):
        return "No run is up on this project."
    record = run_control.request_hold(project_folder, requested_by="resolve panel")
    return ("Handbrake engaged at %s. The step that is running now will "
            "finish and write its state, then the run stops. Nothing is "
            "interrupted mid-step." % record["requested_at"])


def release(project_folder: str) -> str:
    return ("Handbrake released." if run_control.release_hold(project_folder)
            else "The handbrake was not engaged.")


def run_state(project_folder: str) -> dict:
    """What the runner says about itself, plus whether it is really up."""
    status = dict(run_control.read_run_status(project_folder))
    status["live_pid"] = run_control.running_pid(project_folder)
    status["hold"] = run_control.hold_requested(project_folder)
    return status


def tail_log(path: str, lines: int = 60) -> str:
    """The end of the run log. Read on a worker thread, like everything."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 64000))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return "(could not read %s: %s)" % (path, exc)
    return "\n".join(text.splitlines()[-lines:])


# ── The gates, at the timeline ───────────────────────────────────────

@dataclass
class Gate:
    step_id: str
    status: str
    step_name: str = ""
    created_at: str = ""
    output_keys: List[str] = field(default_factory=list)
    feedback_action: str = ""
    feedback_text: str = ""


def gates(project_folder: str) -> List[Gate]:
    """Every gate this project has, pending first."""
    out: List[Gate] = []
    for step_id, status in sorted(
            review_gate.get_all_gate_statuses(project_folder).items()):
        snapshot = review_gate.load_gate_snapshot(project_folder, step_id)
        feedback = review_gate.load_gate_feedback(project_folder, step_id)
        out.append(Gate(
            step_id=step_id,
            status=status,
            step_name=getattr(snapshot, "step_name", "") or "",
            created_at=getattr(snapshot, "created_at", "") or "",
            output_keys=sorted((getattr(snapshot, "step_output", None) or {})
                               if isinstance(getattr(snapshot, "step_output",
                                                     None), dict) else []),
            feedback_action=getattr(feedback, "action", "") or "",
            feedback_text=getattr(feedback, "feedback", "") or "",
        ))
    out.sort(key=lambda g: (g.status != "pending", g.step_id))
    return out


ACTIONS = ("approved", "rejected", "revised")


def answer_gate(project_folder: str, step_id: str, action: str,
                note: str = "", revision_json: str = "") -> str:
    """Take one of the three actions, through the gate's own writer.

    A revision must be a JSON OBJECT, because `apply_feedback_to_output`
    deep-merges it into the step's recorded output. Anything else is
    refused here rather than written and discovered on resume.
    """
    import json

    if action not in ACTIONS:
        return ("Unknown action %r. A gate takes one of: %s."
                % (action, ", ".join(ACTIONS)))
    revisions = {}
    if action == "revised":
        if not (revision_json or "").strip():
            return ("A revision needs a JSON object saying what to change. "
                    "Approve instead if nothing should change.")
        try:
            revisions = json.loads(revision_json)
        except ValueError as exc:
            return "That is not JSON: %s" % exc
        if not isinstance(revisions, dict):
            return ("A revision must be a JSON object - it is deep-merged "
                    "into the step's recorded output.")
    path = review_gate.save_gate_feedback(
        project_folder, step_id, action, feedback=note, revisions=revisions)
    return ("%s: %s. Written to %s. The run picks it up on --resume."
            % (step_id, action, path))


def resume_hint(project_folder: str) -> str:
    """What actually carries on from a gate, read off the run's own record.

    A breakpoint is armed per RUN, so a resume without the flags that
    armed it sails past the next one. The runner records its own argv,
    so the answer is on disk rather than something the captain has to
    remember (`breakpoints.resume_command`).
    """
    record = run_control.read_run_status(project_folder)
    argv = record.get("argv") or []
    if not argv:
        return ("This project has no recorded run to resume. Start one from "
                "the Run tab.")
    return run_breakpoints.resume_command(
        [os.path.join("<repo>", RUNNER)] + list(argv), "<repo>/.venv/bin/python3")
