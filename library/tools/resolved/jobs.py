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
                        broker so its reads coalesce across agents. Reads
                        only, and only the named allowlist
                        (`_BROKER_ALLOWLIST`): a command not on it is
                        refused before it is queued, so a new resolve-axi
                        verb is not broker-eligible by default.
`fusion.registry_summary`
                       The installed OFX/ResolveFX effects and the
                       running Resolve version, read on the broker's own
                       connection. Ren-owned and read-only: what runs is
                       fixed here, never caller-supplied, so the job
                       carries no script text and needs no developer
                       opt-in. Identical reads coalesce.
`resolve.ui_transaction`
                       One verified UI transaction, run on the broker's
                       connection through the resolve-control driver.
                       EXCLUSIVE: a UI action changes the page, the
                       selection, the cursor, the panels, the focus and
                       the window state, so it cannot run beside any
                       other Resolve job. The runner snapshots the
                       interaction context, establishes the target
                       project/timeline/playhead, runs the declared
                       actions through the driver, runs the declared
                       verification, restores the recoverable UI state
                       and files a receipt into the edit history. The
                       sandbox is enforced in the runner, not by
                       convention. Detail: `resolved/ui_transaction.py`.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from typing import Optional

from library.tools import protocol_conformance
from library.tools.resolved.scheduler import EXCLUSIVE, PRIORITIES, SHARED

#: The ONE project a test or qualification job may touch. Live-Resolve
#: tests used to run against whatever project was open - the captain's.
QUALIFICATION_PROJECT = "Ren Qualification"

KINDS = ("lease", "timeline.snapshot", "timeline.apply_patch",
          "resolve_axi", "fusion.registry_summary",
          "resolve.ui_transaction", "resolve.qualification_session")


#: The resolve-axi commands the broker will run, by name. The broker is
#: customer runtime: a queued command runs on the shared Resolve
#: connection, so a command is broker-eligible only by being named here.
#: The broker's whole purpose is coalescing reads across agents, so the
#: allowlist is the read-only verbs; a write goes through the typed
#: `timeline.apply_patch` kind or runs locally under the caller's own
#: lease. A new resolve-axi verb is refused until it is named here on
#: purpose - the boundary is this set, not "whatever reaches Resolve".
#:
#: `cmd_run` is the one write-capable verb on the list, eligible only
#: behind the developer opt-in on the broker process (checked below).
_BROKER_ALLOWLIST = frozenset({
    # reads that reach Resolve on every call
    "cmd_cursor", "cmd_project", "cmd_pool", "cmd_renders",
    "cmd_markers_audit_replies", "cmd_audio",
    # recorded reads; `--refresh-live` reads Resolve and coalesces
    "cmd_timeline_list", "cmd_timeline_get", "cmd_markers", "cmd_items",
    "cmd_fusion", "cmd_captions", "cmd_frames",
    # pool read; `--apply` is refused by the command itself
    "cmd_multicam_sync",
    # developer escape hatch, behind RESOLVE_AXI_ALLOW_RUN on the broker
    "cmd_run",
})


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
    if name not in _BROKER_ALLOWLIST:
        raise JobRefused(
            f"resolve-axi {argv[0]!r} is not on the broker allowlist; the "
            "broker runs reads so they coalesce across agents - a write "
            "goes through the typed timeline.apply_patch kind or runs "
            "locally under your own lease")
    if name == "cmd_run" and not resolve_axi.run_script_enabled():
        # The broker is customer runtime: a queued `run` script would be
        # caller-supplied Python on the shared Resolve connection. The
        # developer opt-in lives on the broker process, and without it
        # the job is refused before it is queued (cmd_run refuses again
        # at execution, fail-closed on both sides).
        raise JobRefused(
            f"resolve-axi run is developer tooling and needs explicit "
            f"opt-in ({resolve_axi.RUN_OPT_IN_ENV}=1 on the broker) - "
            f"normal Ren operation never runs caller-supplied Python")
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
    elif kind == "fusion.registry_summary":
        # A project-less read: it names no project because it reads the
        # Fusion registry, not a timeline. SHARED - it writes nothing.
        shape = {"mode": SHARED,
                 "priority": "read", "executed": True,
                 "project": "", "timeline": "",
                 "locality": "none",
                 "coalesce_key": _coalesce_key(kind)}
    elif kind == "resolve.ui_transaction":
        project = _required(params, "project", kind)
        timeline = params.get("timeline", "") or ""
        # The spec is validated before it is queued, so a malformed
        # transaction is refused at the same fail-closed boundary as
        # every other kind - never mid-run.
        from library.tools.resolved import ui_transaction
        ui_transaction.validate_spec(params)
        # EXCLUSIVE although it is mostly reads: a UI action changes the
        # page, the selection, the cursor, the panels, the focus and the
        # window state, so it cannot run beside any other Resolve job.
        shape = {"mode": EXCLUSIVE,
                 "priority": "mutation", "executed": True,
                 "project": project, "timeline": timeline,
                 "locality": "timeline" if timeline else "project",
                 "coalesce_key": None}
    elif kind == "resolve.qualification_session":
        project = _required(params, "project", kind)
        steps = params.get("steps")
        if not isinstance(steps, list) or not steps:
            raise JobRefused(
                "resolve.qualification_session needs 'steps', a non-empty "
                "list")
        for index, step in enumerate(steps):
            _validate_session_step(step, index)
        # EXCLUSIVE for the whole session: it switches projects, runs
        # the steps, and restores - the take, held by one job. Never
        # coalesced: a borrowed session is not a repeatable read.
        shape = {"mode": EXCLUSIVE,
                 "priority": "mutation", "executed": True,
                 "project": project, "timeline": "",
                 "locality": "project",
                 "coalesce_key": None}
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
    # The submission boundary: the schedulable shape is about to be
    # queued. The mode decides - log in production (a hard refusal here
    # needs live evidence first), refuse in tests/dev.
    protocol_conformance.validate(shape, "job-shape")
    return shape


def refuse_qualification(project: str) -> None:
    """A test job names the qualification project, or it does not run."""
    if project != QUALIFICATION_PROJECT:
        raise JobRefused(
            f"a qualification job may only target {QUALIFICATION_PROJECT!r}"
            f", not {project or 'an unnamed project'!r}: live tests never "
            f"touch a production or user project")


def _validate_session_step(step, index: int) -> None:
    """One borrowed-session step: resolve_axi argv or a ui_transaction spec.

    Steps run inside `borrowed()` on the broker's own connection, so no
    step opens a scripting handshake of its own. The argv is checked to
    PARSE only - not broker-allowlisted: the session is EXCLUSIVE and
    qualification-scoped, the two properties the allowlist protects,
    and writes land in the open (qualification) project or the command
    refuses them itself. `cmd_run` stays gated by the broker opt-in at
    execution, fail-closed on both sides.
    """
    if not isinstance(step, dict) or len(step) != 1:
        raise JobRefused(
            f"step {index} must name exactly one runner: 'resolve_axi' "
            f"argv or a 'ui_transaction' spec")
    if "resolve_axi" in step:
        argv = step["resolve_axi"]
        if (not isinstance(argv, list) or not argv or not all(
                isinstance(token, str) for token in argv)):
            raise JobRefused(
                f"step {index}: resolve_axi needs 'argv', a non-empty "
                f"list of strings")
        from library.tools import resolve_axi
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                resolve_axi.build_parser().parse_args(
                    resolve_axi._normalize(list(argv)))
        except SystemExit as exc:
            raise JobRefused(
                f"step {index}: resolve-axi does not accept "
                f"{argv!r}") from exc
        return
    if "ui_transaction" in step:
        from library.tools.resolved import ui_transaction
        try:
            ui_transaction.validate_spec(step["ui_transaction"])
        except JobRefused as exc:
            raise JobRefused(f"step {index}: {exc}") from exc
        return
    raise JobRefused(
        f"step {index}: unknown runner {sorted(step)!r}; one of "
        f"['resolve_axi', 'ui_transaction']")


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
    if kind == "fusion.registry_summary":
        return _run_registry_summary(resolve)
    if kind == "timeline.apply_patch":
        # The external boundary: the patch arrives from the socket. The
        # mode decides - log in production (a hard refusal on the live
        # edit path needs live evidence first), refuse in tests/dev.
        protocol_conformance.validate(params["patch"], "edit-patch")
    if kind == "resolve.qualification_session":
        # The broker never switches - except through borrowed(), which
        # saves the open project first and reads the restore back.
        from library.tools import qualification_project
        return qualification_project.run_qualification_session(job, resolve)
    project = _open_project(resolve, job["project"])
    if kind == "resolve.ui_transaction":
        # The transaction establishes the timeline itself - the cursor
        # moves to it - so it is NOT required to be current here, and
        # the runner does the establishing under the exclusive lease.
        from library.tools.resolved import ui_transaction
        with write_scope(project=job.get("project"),
                         timeline_name=job.get("timeline"),
                         run_id=job.get("id")):
            return ui_transaction.run(job, resolve, project=project)
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


def _run_registry_summary(resolve) -> dict:
    """The installed OFX/ResolveFX effects and the running version.

    Ren-owned and read-only: what runs is fixed here in Ren, never
    caller-supplied, so the job carries no script text over the socket
    and needs no developer opt-in. `REGI_ClassType == 3.0` is the
    registry's own marker for an effect plugin.
    """
    summary = resolve.Fusion().GetRegSummary(0, False)
    effects = []
    for value in summary.values():
        if value.get("REGI_ClassType") == 3.0:
            effects.append({"id": value["REGS_ID"],
                            "name": value["REGS_Name"]})
    effects.sort(key=lambda e: e["name"])
    return {"version": str(resolve.GetVersionString() or ""),
            "effects": effects}
