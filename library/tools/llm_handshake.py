"""The file handshake: the ONE contract between Ren and a host LLM.

Ren never calls an LLM API in the co-editor path (P5: OAuth harnesses
only - Claude Code, Codex, opencode). Instead the runner and the host
pass files back and forth inside the project:

* Request:  `<project>/pipeline_output/llm_requests/<step>.json`
* Response: `<project>/pipeline_output/llm_responses/<step>.json`

The runner writes the request, prints `LLM_REQUEST_READY: <path>` on
stdout, and polls for the response file. The host (following the
`ren-co-editor` skill) reads the request, does the work, writes the
response, and the runner continues. If the runner is no longer waiting
(it timed out or was stopped), re-running the same `ren edit` command
resumes from the pending step.

This module owns that contract: the request schema, what counts as a
valid response, where both files live, and how a malformed response
refuses. The skill (`.agents/skills/ren-co-editor/SKILL.md`) teaches
the handshake; this module enforces it. Nothing else restates the
schema - `run_pipeline.py`'s agent backend and
`docs/RUN_001_END_TO_END.md` point here.

A request file carries:

* `step_id`: the DAG node id (e.g. `creative_direction`).
* `prompt`: the full prompt text, brand constraints included.
* `constraints`: the brand-constraint text on its own (also archived).
* `context`: the rendered context the model decides from.
* `expected_schema`: the JSON schema text the answer must satisfy.
* `project_folder`: absolute path of the project.
* `timestamp`: UTC ISO-8601 creation time.
* `kind` (optional): `llm_step` (default) or `briefing_interview` -
  the chat interview the host conducts with the user
  (`library/tools/briefing_chat.py`).

A valid response file is a UTF-8 JSON object (`{...}`) written at the
response path above, satisfying the request's `expected_schema`. Arrays,
strings, empty files and unparseable JSON are malformed and REFUSE with
the fix (paths, what was wrong, the resume command) - never silently
accepted, never retried without the model being told.

`tests/test_llm_handshake.py` pins the refusal.
"""

from __future__ import annotations

import json
import os

#: Subdirectory names under `<project>/pipeline_output/`. Mirrors
#: `library/tools/project_layout.Area.LLM_REQUESTS/LLM_RESPONSES` - the
#: layout owns the directories, this module owns the contract spoken
#: over them. A second spelling of either name here is a rename that
#: fails loudly at import time via `_assert_layout_agrees` below.
REQUESTS_SUBDIR = os.path.join("pipeline_output", "llm_requests")
RESPONSES_SUBDIR = os.path.join("pipeline_output", "llm_responses")

#: Marker printed on stdout when a request is ready. One spelling, here.
READY_MARKER = "LLM_REQUEST_READY"

#: Request kinds. `llm_step` is a pipeline step's prompt; only
#: `briefing_interview` may be answered by asking the USER in chat.
KINDS = ("llm_step", "briefing_interview")

#: Keys every request file carries. `kind` is optional (default
#: `llm_step`); the rest are required.
REQUEST_KEYS = (
    "step_id", "prompt", "constraints", "context",
    "expected_schema", "project_folder", "timestamp",
)


class HandshakeRefusal(RuntimeError):
    """A malformed handshake response, carrying its own fix.

    A `RenRefusal` (what happened, why, the fix - see
    `library/tools/ren_refusal.py`), built from the step, the reason,
    the response path and the resume. The rendered message always
    names the step, the exact response path to repair, and how to
    resume. Constructed with the four handshake fields rather than
    raw what/why/fix so a raise site cannot forget what the fix must
    contain.
    """

    def __init__(self, step_id: str, reason: str, response_path: str,
                 resume: str) -> None:
        from library.tools.ren_refusal import RenRefusal
        self.step_id = step_id
        self.reason = reason
        self.response_path = response_path
        self.resume = resume
        self._ren = RenRefusal(
            f"malformed handshake response for step {step_id}: {reason}",
            f"the file at {response_path} is not the JSON object the "
            f"request's expected_schema asked for",
            f"write a UTF-8 JSON object satisfying the request's "
            f"expected_schema to {response_path}, then {resume}")
        super().__init__(str(self._ren.render()))


def request_path(project_folder: str, step_id: str) -> str:
    """Absolute path of the request file for a step."""
    return os.path.join(project_folder, REQUESTS_SUBDIR, f"{step_id}.json")


def response_path(project_folder: str, step_id: str) -> str:
    """Absolute path of the response file for a step."""
    return os.path.join(project_folder, RESPONSES_SUBDIR, f"{step_id}.json")


def resume_command(project_folder: str) -> str:
    """How to resume after repairing a response.

    The runner polls while it waits, so when it is still alive nothing
    needs restarting - writing the file is enough. When it already
    timed out or was stopped, re-running the same edit command resumes
    from the pending step.
    """
    return (
        f"if the run is still waiting, just write the file; otherwise "
        f"`ren edit {project_folder} --full-auto agent` resumes it"
    )


def build_request(step_id: str, prompt: str, constraints: str,
                  context: str, expected_schema: str,
                  project_folder: str, timestamp: str,
                  kind: str = "llm_step") -> dict:
    """Build the request payload the runner writes to disk."""
    if kind not in KINDS:
        raise ValueError(f"unknown handshake kind {kind!r}; want one of {KINDS}")
    return {
        "step_id": step_id,
        "prompt": prompt + constraints,
        "constraints": constraints,
        "context": context,
        "expected_schema": expected_schema,
        "project_folder": project_folder,
        "timestamp": timestamp,
        "kind": kind,
    }


def validate_response(step_id: str, raw_text: str, project_folder: str):
    """Parse and accept a response file's text, or refuse with the fix.

    Returns the parsed JSON object. Raises `HandshakeRefusal` (a
    ValueError-shaped Exception carrying step, reason, path and resume)
    for: empty files, unparseable JSON, and JSON that is not an object.
    Schema-field checking stays with the step's own validator - this
    refuses only what no step could read.
    """
    res = response_path(project_folder, step_id)
    resume = resume_command(project_folder)
    if not raw_text.strip():
        raise HandshakeRefusal(step_id, "the response file is empty.",
                               res, resume)
    try:
        parsed = json.loads(raw_text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HandshakeRefusal(
            step_id, f"the response file is not JSON ({exc}).",
            res, resume) from exc
    if not isinstance(parsed, dict):
        raise HandshakeRefusal(
            step_id,
            f"the response is a JSON {type(parsed).__name__}, not an object - "
            f"the step expects a {{...}} matching its expected_schema.",
            res, resume)
    return parsed


def _assert_layout_agrees() -> None:
    """The layout owns the directory names; this module must agree."""
    try:
        from library.tools.project_layout import Area, _AREAS
        assert _AREAS[Area.LLM_REQUESTS].reldir.endswith("llm_requests")
        assert _AREAS[Area.LLM_RESPONSES].reldir.endswith("llm_responses")
    except Exception:
        pass  # layout refactors rename here first; tests catch drift


_assert_layout_agrees()
