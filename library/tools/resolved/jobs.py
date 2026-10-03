"""The job kinds the broker accepts, and what each one does to Resolve.

`prepare` turns a submission into a schedulable job or REFUSES it
(`JobRefused`) before it is queued: an unknown kind, a missing name, a
qualification job aimed anywhere but the qualification project.
`run` executes an EXECUTED job on the broker's own Resolve connection.

Kinds
-----
`lease`                A grant: the CALLER runs its own critical section
                       (`resolve_lock.resolve_lease`). Never executed here.
`timeline.snapshot`    Observe one named timeline into the shadow store
                       (`timeline_shadow.observe`), returning its
                       generation summary. Exclusive (the timeline must
                       be current to read true); identical snapshots
                       coalesce.
`timeline.apply_patch` Commit one prepared EditPatch. Exclusive. The
                       patch format and its applier belong to
                       `library.tools.edit_patch.apply_patch`; the broker
                       only finds the timeline, holds the cursor and
                       records the receipt. Two submissions of one patch
                       id coalesce. A stale base (`StalePatch`) or a
                       refused precondition (`PatchRefused`) writes
                       nothing and is REJECTED, its fields kept in the
                       receipt's result so the caller can rebase.
`resolve_axi`          One `resolve-axi` command line, run inside the
                       broker so its reads coalesce across agents.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from typing import Optional

from library.tools.resolved.scheduler import EXCLUSIVE, PRIORITIES, SHARED

#: The ONE project a test or qualification job may touch. Live-Resolve
#: tests used to run against whatever project was open - the captain's.
QUALIFICATION_PROJECT = "Ren Qualification"

KINDS = ("lease", "timeline.snapshot", "timeline.apply_patch", "resolve_axi")


class JobRefused(ValueError):
    """The submission is not a job the broker will run. Says why.

    `result` is what the receipt keeps beside the reason, where the
    refusal carries something the caller acts on (a stale patch's head
    generation)."""

    def __init__(self, message: str, result: Optional[dict] = None):
        super().__init__(message)
        self.result = result


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _coalesce_key(*parts) -> str:
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def canonical_digest(value) -> str:
    """Hash JSON data with object-key order and whitespace removed."""
    body = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def idempotency_request(kind: str, params: dict,
                        idempotency_key: str | None = None
                        ) -> tuple[str, str] | None:
    """Return the durable key and request digest, where one was requested.

    EditPatch authors own their stable identity in `patch.id`; other job
    kinds opt in with a caller-supplied idempotency key.
    """
    if kind == "timeline.apply_patch":
        if idempotency_key is not None:
            raise JobRefused(
                "timeline.apply_patch uses patch.id as its idempotency key")
        patch = params["patch"]
        return patch["id"], canonical_digest(patch)
    if idempotency_key is None:
        return None
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise JobRefused("idempotency_key must be a non-empty string")
    return idempotency_key, canonical_digest({"kind": kind, "params": params})


def _required(params: dict, key: str, kind: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise JobRefused(f"{kind} needs a non-empty {key!r}")
    return value


def _resolve_axi_shape(params: dict) -> dict:
    from library.tools import resolve_axi
    argv = params.get("argv")
    if not isinstance(argv, list) or not argv or not all(
            isinstance(token, str) for token in argv):
        raise JobRefused("resolve_axi needs 'argv', a non-empty list of "
                         "strings")
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            args = resolve_axi.build_parser().parse_args(
                resolve_axi._normalize(list(argv)))
    except SystemExit as exc:
        raise JobRefused(f"resolve-axi does not accept {argv!r}") from exc
    func = getattr(args, "func", None)
    name = getattr(func, "__name__", "")
    if func is None or name in resolve_axi._LOCAL_COMMANDS:
        raise JobRefused(f"resolve-axi {argv[0]!r} does not reach Resolve; "
                         "run it locally")
    exclusive = bool(getattr(args, "unsafe", False)
                     or getattr(args, "apply", False))
    if name.startswith("cmd_render"):
        priority = "qa_render"
    else:
        priority = "mutation" if exclusive else "read"
    return {
        "mode": EXCLUSIVE if exclusive else SHARED,
        "priority": priority,
        "project": getattr(args, "project", "") or "",
        "timeline": getattr(args, "timeline", "") or "",
        "locality": ("timeline" if getattr(args, "timeline", "") else
                     "project" if getattr(args, "project", "") else "none"),
        "coalesce_key": (None if exclusive else _coalesce_key(
            "resolve_axi", argv, params.get("cwd", ""))),
    }


def prepare(kind: str, params: dict, qualification: bool = False) -> dict:
    """The schedulable fields of one submission, or `JobRefused`."""
    if kind not in KINDS:
        raise JobRefused(f"unknown job kind {kind!r}; one of {KINDS}")
    if not isinstance(params, dict):
        raise JobRefused("params must be a JSON object")
    if kind == "lease":
        exclusive = bool(params.get("exclusive", True))
        priority = ("interactive" if params.get("interactive")
                    else "mutation" if exclusive else "read")
        project = params.get("project", "") or ""
        timeline = params.get("timeline", "") or ""
        locality = (params.get("locality") or
                    ("timeline" if timeline else
                     "project" if project else "none"))
        shape = {"mode": EXCLUSIVE if exclusive else SHARED,
                 "priority": priority, "executed": False,
                 "project": project, "timeline": timeline,
                 "locality": locality,
                 "coalesce_key": None}
    elif kind == "timeline.snapshot":
        project = _required(params, "project", kind)
        timeline = _required(params, "timeline", kind)
        # EXCLUSIVE although it is a read: `observe` reads Pan/Tilt,
        # which only read true with the timeline CURRENT, and making it
        # current is a cursor write (`timeline_shadow.read_live`).
        shape = {"mode": EXCLUSIVE, "priority": "read", "executed": True,
                 "project": project, "timeline": timeline,
                 "locality": "timeline",
                 "coalesce_key": _coalesce_key(kind, project, timeline)}
    elif kind == "timeline.apply_patch":
        patch = params.get("patch")
        if not isinstance(patch, dict):
            raise JobRefused("timeline.apply_patch needs 'patch', an object")
        patch_id = _required(patch, "id", "an EditPatch")
        from library.tools import capabilities
        from library.tools.operations import (
            FRESHNESS_TIMELINE_GENERATION,
            LOCALITY_TIMELINE,
            RESOLVE_EXCLUSIVE,
            UnknownOperation,
        )
        capability_id = _required(patch, "capability", "an EditPatch")
        try:
            capability = capabilities.get(capability_id)
        except UnknownOperation as exc:
            raise JobRefused(str(exc)) from exc
        phase = capability.execution.phase("apply")
        if (phase is None or phase.resolve_mode != RESOLVE_EXCLUSIVE
                or phase.locality != LOCALITY_TIMELINE
                or phase.freshness != FRESHNESS_TIMELINE_GENERATION
                or capability.execution.patch is None):
            raise JobRefused(
                f"{capability.id!r} has no compatible EditPatch execution "
                "policy")
        project = _required(patch, "project", "an EditPatch")
        timeline = _required(patch, "timeline", "an EditPatch")
        base_generation = patch.get("base_generation")
        if (phase.freshness == "timeline_generation"
                and (not isinstance(base_generation, int)
                     or isinstance(base_generation, bool)
                     or base_generation < 1)):
            raise JobRefused(
                "the EditPatch capability requires a positive timeline "
                "base_generation")
        shape = {"mode": EXCLUSIVE,
                 "priority": "mutation", "executed": True,
                 "project": project, "timeline": timeline,
                 "locality": phase.locality,
                 "coalesce_key": _coalesce_key(kind, patch_id)}
    else:
        shape = dict(_resolve_axi_shape(params), executed=True)
    requested = params.get("priority")
    if requested is not None:
        if requested not in PRIORITIES:
            raise JobRefused(f"priority {requested!r} is not one of "
                             f"{PRIORITIES}")
        shape["priority"] = requested
    if qualification:
        refuse_qualification(shape["project"])
        if requested is None and shape["priority"] != "interactive":
            shape["priority"] = "qualification"
    shape["qualification"] = qualification
    return shape


def refuse_qualification(project: str) -> None:
    """A test job names the qualification project, or it does not run."""
    if project != QUALIFICATION_PROJECT:
        raise JobRefused(
            f"a qualification job may only target {QUALIFICATION_PROJECT!r}"
            f", not {project or 'an unnamed project'!r}: live tests never "
            f"touch a production or user project")


# ── Execution, on the broker's connection ───────────────────────────

def open_project_name(resolve) -> str:
    project = resolve.GetProjectManager().GetCurrentProject()
    return (project.GetName() or "") if project else ""


def _open_project(resolve, name: str):
    """The OPEN project, which must be `name`. The broker never switches."""
    project = resolve.GetProjectManager().GetCurrentProject()
    found = (project.GetName() or "") if project else ""
    if found != name:
        raise JobRefused(
            f"Resolve has {found or 'no project'!r} open, not {name!r}; the "
            f"broker never switches projects - open {name!r} first")
    return project


def _timeline(project, name: str):
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() == name:
            return timeline
    raise JobRefused(f"no timeline named exactly {name!r} in "
                     f"{project.GetName()!r}")


def run(job: dict, resolve) -> Optional[dict]:
    """Run one EXECUTED job; returns its JSON-serialisable result."""
    from library.tools import resolve_lock
    from library.tools.transform_write_log import write_scope
    kind, params = job["kind"], job["params"]
    purpose = f"ren-resolved {kind} {job['id']}"
    if kind == "resolve_axi":
        with write_scope(
                project=job.get("project"), timeline_name=job.get("timeline"),
                run_id=job.get("id")):
            return _run_resolve_axi(params)
    project = _open_project(resolve, job["project"])
    timeline = _timeline(project, job["timeline"])
    if kind == "timeline.snapshot":
        from library.tools import timeline_shadow
        with resolve_lock.cursor_excursion(project, timeline, purpose):
            return timeline_shadow.observe(project, timeline).summary()
    if kind == "timeline.apply_patch":
        from library.tools import edit_patch
        # The excursion makes the patch's timeline current and puts the
        # captain's cursor back after; `apply_patch` fences the write.
        try:
            patch = params["patch"]
            with write_scope(project=patch["project"],
                             timeline_name=patch["timeline"],
                             run_id=job.get("id")):
                with resolve_lock.cursor_excursion(project, timeline, purpose):
                    return _jsonable(edit_patch.apply_patch(
                        patch, resolve=resolve, project=project,
                        timeline=timeline))
        except edit_patch.PatchRefused as refused:
            fields = {key: value for key, value in vars(refused).items()
                      if not key.startswith("_")}
            raise JobRefused(str(refused), result=_jsonable(
                dict(fields, refusal=type(refused).__name__))) from refused
    raise JobRefused(f"{kind} is not an executed job")


def _run_resolve_axi(params: dict) -> dict:
    from library.tools import resolve_axi
    out, err = io.StringIO(), io.StringIO()
    cwd = params.get("cwd") or os.getcwd()
    previous = os.getcwd()
    os.chdir(cwd)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = resolve_axi.main(list(params["argv"]))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
    finally:
        os.chdir(previous)
    return {"exit_code": int(code or 0), "stdout": out.getvalue(),
            "stderr": err.getvalue()}
