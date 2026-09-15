"""The model answers a reels build still owes, counted across reels.

A reel built with no model answer on file builds without that layer and
SAYS so per reel on stderr (`awaiting_model_answer` in
`reel_semantic_visual` for semantic visuals, `MOTION_AWAITING_ANSWER` in
`reel_look` for picture motion). The per-reel lines are SAID, not
REPORTED: nothing anywhere answered "this build owes N model answers",
and the run summary's SUCCESS is DAG-completion only - so a build that
owes a model answer reported success and said nothing about it.

This module is that surface. `collect` counts the outstanding answers
across reels from the records the build wrote (never re-derived), and
`summary_lines` renders them for a run summary. Reading is not gating:
callers print after `status` is decided, and nothing here fails a run.
A build the captain wants to look at is still worth building; what is
not acceptable is claiming it is finished (AGENTS.md 10.4).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

SEMANTIC_LAYER = "semantic_visuals"
MOTION_LAYER = "picture_motion"


def collect(project_folder: str,
            motion_records: Optional[Sequence[dict]] = None,
            state: Optional[dict] = None) -> dict:
    """The outstanding model answers, per reel, from what the build wrote.

    The semantic half is read off the stored record file
    (`reel_semantic_visual.read_records`) - the same record F22 grades
    against - so a partial build that did not touch a reel still counts
    the answer that reel owes. The motion half is NOT file-persisted
    anywhere except the build record itself, so it comes from
    `motion_records` when the caller just built (the in-memory list it
    is about to return), else from
    `state["step_outputs"]["build_reels"]["reel_build"]["picture_motion"]`.
    """
    owing: Dict[str, List[str]] = {}

    def _owe(reel: str, layer: str) -> None:
        name = str(reel or "")
        if not name:
            return
        if layer not in owing.setdefault(name, []):
            owing[name].append(layer)

    try:
        from library.tools import reel_semantic_visual as sem_vis
        stored = sem_vis.read_records(project_folder)
        for record in (stored.get("plans") or []):
            if not isinstance(record, dict):
                continue
            if (record.get("basis") == sem_vis.AWAITING_MODEL_ANSWER
                    and not (record.get("segments") or [])):
                _owe(record.get("reel"), SEMANTIC_LAYER)
    except (OSError, ValueError):
        pass

    motion = motion_records
    if motion is None and isinstance(state, dict):
        try:
            motion = (((state.get("step_outputs") or {})
                       .get("build_reels") or {}).get("reel_build") or {}
                      ).get("picture_motion")
        except AttributeError:
            motion = None
    try:
        from library.tools import reel_look as _look
        awaiting = _look.MOTION_AWAITING_ANSWER
    except Exception:  # noqa: BLE001 - degraded read still counts semantics
        awaiting = "awaiting_model_answer"
    for record in (motion or []):
        if not isinstance(record, dict):
            continue
        if record.get("basis") == awaiting:
            _owe(record.get("reel"), MOTION_LAYER)

    reels = [{"reel": name, "layers": sorted(layers)}
             for name, layers in sorted(owing.items())]
    return {"reels": reels,
            "outstanding_answers": sum(len(r["layers"]) for r in reels)}


def summary_lines(report: Optional[dict] = None,
                  project_folder: str = "",
                  state: Optional[dict] = None) -> List[str]:
    """What the run summary prints about answers the build still owes.

    Empty when nothing is owed: an edit_video run that never touched
    reels must not gain a line about reels. Reading is not gating.
    """
    if report is None:
        if not project_folder:
            return []
        report = collect(project_folder, state=state)
    reels = (report or {}).get("reels") or []
    if not reels:
        return []
    total = (report or {}).get(
        "outstanding_answers", sum(len(r["layers"]) for r in reels))
    lines = [f"Model answers outstanding: {total} "
             f"answer(s) owed across {len(reels)} reel(s) - "
             f"these reels built without them and are incomplete, "
             f"not finished:"]
    for row in reels:
        lines.append(f"  {row['reel']}: {', '.join(row['layers'])}")
    lines.append("  Answer the asks the build wrote "
                 "(reel_semantic_NN.json / reel_motion_NN.json under "
                 "pipeline_output/llm_requests) and rebuild.")
    return lines


def outstanding_answer_count(project_folder: str,
                             state: Optional[dict] = None) -> int:
    """The one number: how many model answers this build still owes."""
    return int(collect(project_folder, state=state)
               .get("outstanding_answers", 0) or 0)
