"""The Workflow Integration's one route into this repository's Python.

The plugin (``resolve_workflow_integration/``) is an Electron app, so it
speaks JavaScript, and everything this pipeline knows is Python.  This
module is the join between them, and it is deliberately the smallest
thing that works: **one request object on stdin, one answer object on
stdout, one process per request.**

    $ echo '{"op": "ping"}' | python3 -m library.tools.workflow_bridge
    {"ok": true, "op": "ping", ...}

Why a bridge at all, rather than moving the logic
-------------------------------------------------
The Qt panel was built around three measured properties (AGENTS.md
section 15), and the third one - *its logic is testable without Resolve*
- is the one a second surface most easily loses.  It is not lost here,
because nothing moved: ``library/tools/panel/`` is still five
Resolve-free modules with tests that never open the application, and the
JavaScript side reads Resolve and draws.  **Every judgement about the
footage is on this side of the bridge**, which is why the bridge exists
rather than being a cost the plugin pays for nothing.

What the plugin may ask for
---------------------------
``OPERATIONS`` is the whole of it, and an unknown op is refused BY NAME.
Every operation is a READ: this module opens no Resolve, writes nothing
under the project, and starts no run.  That is a property of the
enumeration, not of any caller's good behaviour.

The project is MEASURED, never asked for
----------------------------------------
The plugin knows the source file under the playhead; ``project_for_file``
walks up from it until ``pipeline_data.json`` appears, which is the same
thing the panel does and the thing the browser dashboard structurally
cannot do.  A file under no project comes back as a stated absence.

The cost is measured on both sides
----------------------------------
Every answer carries ``python_ms`` - the time inside this process - and
the plugin adds the round trip as ``ms``.  The difference between them is
what the process spawn costs, and it is a number rather than a belief.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Optional

# The name of the state file, which is also what identifies a project
# folder.  One spelling, here, because the walk below is the only place
# this module decides where it is.
STATE_FILE = "pipeline_data.json"

# How far up from a source file a project may be.  A clip lives in
# `<project>/raw/`, so two levels is the real case and six is slack; an
# unbounded walk would reach the filesystem root and answer with whatever
# it found there.
PROJECT_WALK_LIMIT = 6


class BridgeError(Exception):
    """A request that cannot be answered, with the reason as its text."""


# ── Finding the project ──────────────────────────────────────────────

def project_for_file(source_file: str) -> Optional[str]:
    """The pipeline project a source file belongs to, or None.

    Measured by walking up until the state file appears.  Never guessed
    at from a name, and never defaulted to a configured root: the whole
    point of reading it off the timeline is that the captain's own
    project may sit outside `PIPELINE_PROJECTS_ROOT` entirely.
    """
    if not source_file:
        return None
    probe = os.path.dirname(os.path.realpath(source_file))
    for _ in range(PROJECT_WALK_LIMIT):
        if os.path.isfile(os.path.join(probe, STATE_FILE)):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return None


def load_state(project_folder: str) -> dict:
    """`pipeline_data.json`, read.  A read, and only ever a read."""
    path = os.path.join(project_folder, STATE_FILE)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ── Operations ───────────────────────────────────────────────────────

def op_ping(request: Dict[str, Any]) -> Dict[str, Any]:
    """The cost of the bridge itself, with nothing else in it.

    This is what makes the round trip measurable: the plugin's `ms`
    minus this answer's `python_ms` is what one process spawn costs.
    """
    return {
        "op": "ping",
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "cwd": os.getcwd(),
    }


def op_render_output(request: Dict[str, Any]) -> Dict[str, Any]:
    """Where the last render went, if a project recorded one.

    The key is `render_output.output_path`, which is step 6.01's own
    name for it (AGENTS.md section 3, "State the pipeline did not
    produce": a file is named for the PRODUCER's key).  An absence is
    said, and never filled in with a plausible path.
    """
    folder = _project(request)
    if folder is None:
        return {"op": "render_output", "path": "",
                "reason": "no pipeline project was found above this file"}
    state = load_state(folder)
    outputs = (state or {}).get("step_outputs") or {}
    recorded = (outputs.get("render") or {}).get("render_output") or {}
    path = str(recorded.get("output_path") or "")
    if not path:
        return {"op": "render_output", "path": "", "project": folder,
                "reason": "step 6.01 has recorded no render_output for this "
                          "project, so there is no master to play"}
    if not os.path.isabs(path):
        path = os.path.join(folder, path)
    return {
        "op": "render_output",
        "path": path,
        "project": folder,
        "on_disk": os.path.isfile(path),
    }


def op_clip_facts(request: Dict[str, Any]) -> Dict[str, Any]:
    """The clip under the playhead, joined to what the pipeline measured.

    The join itself is `library/tools/panel/clip_context.py`, unchanged
    and shared with the Qt panel - two surfaces answering "what is this
    clip" differently would be a defect nobody could see.  This function
    only turns the plugin's JSON into the `ResolveContext` that module
    already takes.
    """
    from library.tools.panel import clip_context

    raw = request.get("context") or {}
    context = clip_context.ResolveContext(
        page=str(raw.get("page") or ""),
        project=str(raw.get("project") or ""),
        timeline=str(raw.get("timeline") or ""),
        timecode=str(raw.get("timecode") or ""),
        timeline_frame=raw.get("timeline_frame"),
        fps=float(raw.get("fps") or 30.0),
        clip=raw.get("clip"),
        markers=list(raw.get("markers") or []),
        overlays=list(raw.get("overlays") or []),
    )
    folder = project_for_file(context.source_file)
    if folder is None:
        return {
            "op": "clip_facts",
            "project_folder": "",
            "reason": ("this clip's source file sits under no pipeline "
                       "project, so nothing measured can be joined to it"),
        }
    state = load_state(folder)
    facts = clip_context.clip_facts(state, context)
    return {
        "op": "clip_facts",
        "project_folder": folder,
        "clip_id": facts.clip_id,
        "catalog": facts.catalog,
        "absences": list(facts.absences),
        "prompt_block": clip_context.prompt_block(context, facts),
    }


def op_surface(request: Dict[str, Any]) -> Dict[str, Any]:
    """What this bridge can be asked, as data.

    A plugin that has to be re-installed to learn a new operation is a
    plugin that goes stale; asking is one round trip.
    """
    return {"op": "surface", "operations": sorted(OPERATIONS)}


#: The whole of what the plugin may ask for.  An op outside it is
#: refused by name - a request that does nothing and says nothing is the
#: failure mode this enumeration exists to prevent.
OPERATIONS = {
    "ping": op_ping,
    "render_output": op_render_output,
    "clip_facts": op_clip_facts,
    "surface": op_surface,
}


def _project(request: Dict[str, Any]) -> Optional[str]:
    """The project folder a request is about.

    Named explicitly if the caller knows it, measured off the source
    file if it does not.  Both, because the plugin learns the folder
    once and then has no reason to re-derive it every call.
    """
    named = request.get("project_folder")
    if named and os.path.isfile(os.path.join(str(named), STATE_FILE)):
        return str(named)
    return project_for_file(str(request.get("source_file") or ""))


def answer(request: Dict[str, Any]) -> Dict[str, Any]:
    """One request in, one answer out.  Never raises."""
    started = time.time()
    op = str(request.get("op") or "")
    handler = OPERATIONS.get(op)
    if handler is None:
        return {
            "ok": False,
            "error": "unknown op %r; this bridge answers %s"
                     % (op, ", ".join(sorted(OPERATIONS))),
            "python_ms": round((time.time() - started) * 1000, 1),
        }
    try:
        result = handler(request)
        result["ok"] = True
    except Exception as exc:                        # noqa: BLE001 - reported
        result = {"ok": False, "op": op,
                  "error": "%s: %s" % (type(exc).__name__, exc)}
    result["python_ms"] = round((time.time() - started) * 1000, 1)
    return result


def main(argv=None) -> int:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        request = {}
        print(json.dumps({"ok": False,
                          "error": "request was not JSON: %s" % exc}))
        return 0
    print(json.dumps(answer(request)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
