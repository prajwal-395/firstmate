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
`timeline.observe_current`
                       Fingerprint Resolve's current timeline and refresh
                       its full shadow only when the light read changes.
                       Never moves the cursor.
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
                        broker so its reads coalesce across agents. Only
                        the named allowlist (`_BROKER_ALLOWLIST`) is
                        admitted. `run --purpose` is the recorded custom
                        script path; an unrecorded run keeps the developer
                        opt-in. Other writes stay typed or local.
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
`reel.promote_staged`  One typed staged-reel promotion plus its version
                       record, restricted to the open `Ren Qualification`
                       project and held for the whole take. Caller payload
                       is data, never Python.
`timeline.drt_export_probe`
                       Export one non-current timeline by UID to a DRT
                       recovery archive, for `Ren Qualification` only.
                       Typed and read-only with respect to Resolve state.
`timeline.drt_recovery_probe`
                       Restore one qualification DRT into a disposable,
                       lifecycle-owned Ren Scratch project; verify and retire.
`reel.copy_tv_ending`  Copy one verified TV-off freeze comp and the
                       two-line logo onto a named geo-podcast reel. The
                       target hold is rendered from its own final picture;
                       one exclusive job archives, snapshots, edits and
                       verifies one target reel.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os

from library.tools import protocol_conformance
from library.tools.resolved.scheduler import EXCLUSIVE, PRIORITIES, SHARED

#: The ONE project a test or qualification job may touch. Live-Resolve
#: tests used to run against whatever project was open - the captain's.
QUALIFICATION_PROJECT = "Ren Qualification"

KINDS = ("lease", "timeline.snapshot", "timeline.observe_current",
         "timeline.apply_patch", "timeline.semantic_edit",
         "resolve_axi", "fusion.registry_summary",
         "resolve.ui_transaction", "reel.promote_staged",
         "reel.prepare_qualification_fixture",
         "reel.probe_qualification_marker_loss",
          "resolve.qualification_session", "qualification.workflow",
          "resolve.activity_probe",
          "timeline.drt_export_probe",
          "timeline.drt_recovery_probe",
          "reel.copy_tv_ending")

# These qualification-only writes deliberately enter from the currently
# open captain project, preserve it, and restore it around their fixed Q
# operation. The server still validates their project field before borrow.
BORROWING_QUALIFICATION_KINDS = frozenset({
    "resolve.activity_probe",
    "resolve.qualification_session",
    "reel.promote_staged",
    "reel.prepare_qualification_fixture",
    "reel.probe_qualification_marker_loss",
    "qualification.workflow",
})

# A job may opt into running Stop only with a named safe boundary. A
# `commit:` boundary returns the complete receipt before honoring Stop;
# an `inverse:` boundary restores before honoring it; a `backup:` boundary
# names both the durable backup and the verified inverse restore.
INTERRUPT_POLICIES = {
    "resolve.activity_probe": "checkpoint:borrowed-context-restored",
    "resolve_axi": "commit:successful-recorded-run",
    "timeline.apply_patch": "commit:verified-editpatch-generation",
    "timeline.semantic_edit": "commit:verified-semantic-generation",
    "resolve.ui_transaction": "commit:verified-ui-transaction",
    "resolve.qualification_session": "commit:all-steps-completed",
    "reel.promote_staged": "commit:promotion-record-verified",
    "reel.prepare_qualification_fixture": "commit:fixture-saved",
    "reel.probe_qualification_marker_loss": "inverse:marker-restored-and-saved",
    "reel.copy_tv_ending": "backup:drt+inverse:inventory-verified",
    "qualification.workflow": "commit:timeline-exported-and-read-back",
    "timeline.drt_recovery_probe": "commit:scratch-retired-after-recovery-verification",
}
MUTATING_KINDS = frozenset(INTERRUPT_POLICIES)
MUTATING_KINDS = MUTATING_KINDS - {"resolve.activity_probe"}
RUNNING_CANCELLATION_KINDS = frozenset(INTERRUPT_POLICIES)


def _commit_checkpoint(job: dict, result):
    """Honor deferred Stop only after the runner's committed receipt exists."""
    from library.tools.resolved import cancellation
    cancellation.checkpoint(job["id"], result=result)
    return result


def _start_checkpoint(job: dict) -> None:
    from library.tools.resolved import cancellation
    cancellation.checkpoint(job["id"])


#: The resolve-axi commands the broker will run, by name. The broker is
#: customer runtime: a queued command runs on the shared Resolve
#: connection, so a command is broker-eligible only by being named here.
#: The broker's whole purpose is coalescing reads across agents, so the
#: allowlist is the read-only verbs; a write goes through the typed
#: `timeline.apply_patch` kind or runs locally under the caller's own
#: lease. A new resolve-axi verb is refused until it is named here on
#: purpose - the boundary is this set, not "whatever reaches Resolve".
#:
#: `cmd_run` is the one caller-script verb on the list. The recorded
#: path requires a purpose and keeps its own backup and inventory guard;
#: unrecorded runs still need the developer opt-in (checked below).
#: `cmd_project_scratch` is the one project-lifecycle verb on the list:
#: the scratch create|import|open|restore|delete verbs switch projects
#: by design, so they can never go through `timeline.apply_patch`, and
#: running them locally would dodge the broker's exclusive section.
#: They carry their own guard instead - `Ren Scratch - ` prefix only,
#: dirty-state refusal that never saves or discards, origin read-back
#: and restore - and always run EXCLUSIVE (forced in `_resolve_axi_shape`,
#: since `--apply` is checked in the command body, after scheduling).
#: `cmd_render_inspect` is the one render verb on the list: it renders
#: the OPEN timeline to a scratch file and changes no Resolve state - no
#: timeline, no clip, no media-pool item - so the review loop can LOOK
#: at a timeline through the same queue every other Resolve job uses.
#: It is EXCLUSIVE (forced below), because a render may not run beside
#: another writer, and it costs machine time, so it carries the
#: `qa_render` priority band rather than a read's.
_BROKER_ALLOWLIST = frozenset({
    # reads that reach Resolve on every call
    "cmd_cursor", "cmd_project", "cmd_pool", "cmd_renders",
    "cmd_markers_audit_replies", "cmd_audio",
    # recorded reads; `--refresh-live` reads Resolve and coalesces
    "cmd_timeline_list", "cmd_timeline_get", "cmd_markers", "cmd_items",
    "cmd_fusion", "cmd_captions", "cmd_frames", "cmd_edit_transform",
    # pool read; `--apply` is refused by the command itself
    "cmd_multicam_sync",
    # scratch-project lifecycle; EXCLUSIVE with its own prefix/dirty/read-back guard
    "cmd_project_scratch",
    # read-only render for inspection; EXCLUSIVE, qa_render priority
    "cmd_render_inspect",
    # developer escape hatch, behind RESOLVE_AXI_ALLOW_RUN on the broker
    "cmd_run",
})

# These commands are safe on Ren-owned fixture projects only. Keep them
# outside the general allowlist: both commands accept a caller-selected
# project, and a missing project would otherwise silently address whichever
# project Resolve currently has open.
_SCOPED_RESOLVE_AXI_COMMANDS = frozenset({
    "cmd_markers_snapshot",
    "cmd_timeline_duplicate",
})


def _ren_owned_fixture_target(project: str, timeline: str) -> bool:
    """Whether this exact Resolve target belongs to Ren's fixture space.

    Qualification's reusable fixtures are named ``Q*`` and
    ``fm-scratch*``. Other qualification projects use the dedicated
    ``Ren Scratch - `` namespace and may carry their own reel names.
    These checks intentionally use Resolve's exact project name, never a
    project path or substring that could match a captain-owned project.
    """
    project = (project or "").strip()
    timeline = (timeline or "").strip()
    if not project or not timeline:
        return False
    if project == "Ren Qualification":
        return timeline.startswith(("Q", "fm-scratch"))
    prefix = "Ren Scratch - "
    return project.startswith(prefix) and bool(project[len(prefix):].strip())


class JobRefused(ValueError):
    """The submission is not a job the broker will run. Says why.

    `result` is what the receipt keeps beside the reason, where the
    refusal carries something the caller acts on (a stale patch's head
    generation)."""

    def __init__(self, message: str, result: dict | None = None):
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


def activity_description(kind: str, params: dict) -> str:
    """The short, user-facing action for a broker job receipt."""
    def one_line(value: str) -> str:
        return " ".join(value.splitlines())[:80]

    description = params.get("description")
    if description:
        return description
    purpose = params.get("purpose")
    if isinstance(purpose, str) and purpose:
        return one_line(purpose)
    if kind == "timeline.snapshot":
        return one_line(f"Reading {params['timeline']} in {params['project']}")
    if kind == "timeline.semantic_edit":
        request = params["request"]
        return one_line(
            f"Applying semantic edits to {request['timeline']} in "
            f"{request['project']}")
    if kind == "timeline.apply_patch":
        patch = params["patch"]
        return one_line(f"Applying edits to {patch['timeline']}")
    if kind == "resolve_axi":
        argv = params.get("argv") or ["Resolve"]
        if argv[0] == "run":
            purpose = ""
            for index, token in enumerate(argv):
                if token == "--purpose" and index + 1 < len(argv):
                    purpose = argv[index + 1]
                    break
                if token.startswith("--purpose="):
                    purpose = token.partition("=")[2]
                    break
            if purpose:
                return one_line(f"Running Resolve task: {purpose}")
        return one_line(f"Running Resolve command: {argv[0]}")
    return one_line({
        "timeline.observe_current": "Checking the current Resolve timeline",
        "fusion.registry_summary": "Checking installed Resolve effects",
        "resolve.ui_transaction": "Completing a verified Resolve task",
        "resolve.qualification_session": "Running the Resolve qualification",
        "qualification.workflow": "Running a qualification workflow",
        "timeline.drt_export_probe": "Exporting a timeline recovery archive",
        "timeline.drt_recovery_probe": "Restoring and retiring a DRT qualification project",
        "reel.promote_staged": "Promoting staged reels",
        "reel.copy_tv_ending": "Copying approved reel endings",
        "lease": "Using DaVinci Resolve",
    }.get(kind, kind.replace(".", " ").replace("_", " ").capitalize()))


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
    if name in _SCOPED_RESOLVE_AXI_COMMANDS:
        project = getattr(args, "project", "") or ""
        timeline = getattr(args, "timeline", "") or ""
        if not _ren_owned_fixture_target(project, timeline):
            raise JobRefused(
                f"resolve-axi {argv[0]!r} is permitted through the broker "
                "only for an explicit Ren-owned fixture project and "
                "timeline (Ren Qualification Q*/fm-scratch* fixtures or "
                "Ren Scratch - * projects)")
    elif name not in _BROKER_ALLOWLIST:
        raise JobRefused(
            f"resolve-axi {argv[0]!r} is not on the broker allowlist; the "
            "broker runs reads so they coalesce across agents - a write "
            "goes through the typed timeline.apply_patch kind or runs "
            "locally under your own lease")
    if name == "cmd_run" and not resolve_axi.run_script_enabled():
        purpose = getattr(args, "purpose", "") or ""
        workflow = getattr(args, "workflow_name", "") or ""
        if not purpose and not workflow:
            raise JobRefused(
                "resolve-axi run needs a short --purpose to create a durable "
                "Ren record or the developer-shell opt-in; developer shells "
                "may keep using "
                f"{resolve_axi.RUN_OPT_IN_ENV}=1")
    exclusive = bool(getattr(args, "unsafe", False)
                     or getattr(args, "apply", False)
                     # Snapshot writes its requested output file in the
                     # broker process, so serialize that side effect too.
                     or name == "cmd_markers_snapshot"
                     or name == "cmd_project_scratch"
                     or name == "cmd_render_inspect"
                     or (name == "cmd_run" and bool(
                         getattr(args, "purpose", "")
                         or getattr(args, "workflow_name", ""))))
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
        "coalesce_key": (None if exclusive or name == "cmd_run" else _coalesce_key(
            "resolve_axi", argv, params.get("cwd", ""))),
    }


def prepare(kind: str, params: dict, qualification: bool = False) -> dict:
    """The schedulable fields of one submission, or `JobRefused`."""
    if kind not in KINDS:
        raise JobRefused(f"unknown job kind {kind!r}; one of {KINDS}")
    if not isinstance(params, dict):
        raise JobRefused("params must be a JSON object")
    description = params.get("description")
    if description is not None and (
            not isinstance(description, str) or not description.strip()
            or len(description) > 80 or "\n" in description
            or "\r" in description):
        raise JobRefused(
            "description must be one non-empty line of at most 80 characters")
    progress = params.get("progress")
    if progress is not None and (
            not isinstance(progress, str) or len(progress) > 20
            or "\n" in progress or "\r" in progress):
        raise JobRefused(
            "progress must be one line of at most 20 characters")
    if (description is not None and progress
            and len(description) + len(progress) + 3 > 80):
        raise JobRefused(
            "description and progress must fit in 80 characters")
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
    elif kind == "timeline.observe_current":
        project = _required(params, "project", kind)
        # The current timeline is resolved at execution time. A cursor move
        # while this job waits must change what it reads, never what Resolve
        # displays, so this job does not single-flight against a stale target.
        shape = {"mode": EXCLUSIVE, "priority": "read", "executed": True,
                 "project": project, "timeline": "", "locality": "project",
                 "coalesce_key": None}
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
    elif kind == "timeline.semantic_edit":
        from library.tools.edit_request import EditRequest, EditRequestError
        request_data = params.get("request")
        if not isinstance(request_data, dict):
            raise JobRefused(
                "timeline.semantic_edit needs 'request', an object")
        try:
            request = EditRequest.from_dict(request_data)
        except (EditRequestError, TypeError, ValueError) as exc:
            raise JobRefused(f"invalid semantic edit request: {exc}") from exc
        backend = _required(params, "backend", kind)
        if backend in {"edit_patch", "edit_patch.property", "no_op"}:
            raise JobRefused(
                f"{backend!r} uses a different semantic execution path")
        base_generation = params.get("base_generation")
        if (not isinstance(base_generation, int)
                or isinstance(base_generation, bool) or base_generation < 1):
            raise JobRefused(
                "timeline.semantic_edit needs a positive base_generation")
        shape = {"mode": EXCLUSIVE, "priority": "mutation", "executed": True,
                 "project": request.project, "timeline": request.timeline,
                 "locality": "timeline", "coalesce_key": None}
    elif kind == "fusion.registry_summary":
        # A project-less read: it names no project because it reads the
        # Fusion registry, not a timeline. SHARED - it writes nothing.
        shape = {"mode": SHARED,
                 "priority": "read", "executed": True,
                 "project": "", "timeline": "",
                 "locality": "none",
                 "coalesce_key": _coalesce_key(kind)}
    elif kind == "resolve.activity_probe":
        # A no-edit job used to verify the lease activity overlay and its
        # cooperative Stop path on a live Resolve instance. It holds the
        # same broker lease as every other job but makes no project edits.
        if not qualification:
            raise JobRefused(
                "resolve.activity_probe is restricted to qualification runs")
        requested_project = params.get("project", "") or ""
        refuse_qualification(requested_project or QUALIFICATION_PROJECT)
        seconds = params.get("seconds", 10)
        if (not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
                or not 0.1 <= seconds <= 30):
            raise JobRefused(
                "resolve.activity_probe seconds must be between 0.1 and 30")
        project = QUALIFICATION_PROJECT
        shape = {"mode": EXCLUSIVE, "priority": "interactive",
                 "executed": True, "project": project, "timeline": "",
                 "locality": "project", "coalesce_key": None}
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
    elif kind == "qualification.workflow":
        from library.tools.qualification_workflow import prepare_broker_job
        shape = prepare_broker_job(params)
        qualification = True
    elif kind == "timeline.drt_export_probe":
        from library.tools.resolved import drt_export_probe
        shape = drt_export_probe.prepare(params)
        # This is a qualification job even when the caller omits the
        # generic qualification flag. It must never reach a user project.
        qualification = True
    elif kind == "timeline.drt_recovery_probe":
        from library.tools.resolved import drt_recovery_probe
        shape = drt_recovery_probe.prepare(params)
        qualification = True
    elif kind == "reel.promote_staged":
        from library.tools.resolved import reel_promotion
        shape = reel_promotion.prepare(params)
        qualification = True
    elif kind == "reel.prepare_qualification_fixture":
        from library.tools.resolved import reel_promotion_fixture
        shape = reel_promotion_fixture.prepare(params)
        qualification = True
    elif kind == "reel.probe_qualification_marker_loss":
        from library.tools.resolved import marker_loss_probe
        shape = marker_loss_probe.prepare(params)
        qualification = True
    elif kind == "reel.copy_tv_ending":
        from library.tools.resolved import reel_ending_copy
        shape = reel_ending_copy.prepare(params)
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
    # This private broker capability is returned to Rust after the public
    # schedulable shape has passed the generated wire contract. Stop is
    # admitted by the runner's declared checkpoint policy, not a second
    # hard-coded list in the native daemon.
    shape["interrupt_policy"] = INTERRUPT_POLICIES.get(kind, "")
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


def run(job: dict, resolve) -> dict | None:
    """Run one EXECUTED job; returns its JSON-serialisable result.

    A shadow write attempted by a worker whose code predates the
    shadow DB schema is a REFUSAL (`JobRefused`, receipt `rejected`),
    never a silent per-job failure: the stale worker restarts on
    current code instead of failing writes (2026-10-11: pre-A3 code
    failed every first-record shadow write machine-wide while
    re-reads looked healthy).
    """
    from library.tools.timeline_shadow import StaleShadowWorker

    try:
        return _run_impl(job, resolve)
    except StaleShadowWorker as stale:
        raise JobRefused(str(stale)) from stale


def _run_impl(job: dict, resolve) -> dict | None:
    """The body of `run`: one EXECUTED job on the broker's connection."""
    from library.tools import resolve_lock
    from library.tools.transform_write_log import write_scope
    kind, params = job["kind"], job["params"]
    purpose = f"ren-resolved {kind} {job['id']}"
    if kind == "resolve.activity_probe":
        # Test and qualification hook: the only work is a timed wait, so
        # Stop can be honored between short sleeps; borrowed() restores the
        # captain's exact project, timeline and page before the lease ends.
        import time

        from library.tools import qualification_project
        from library.tools.resolved import cancellation
        with qualification_project.borrowed(resolve) as before:
            manager = resolve.GetProjectManager()
            qualification_project.switch_project(
                manager, QUALIFICATION_PROJECT)
            deadline = time.monotonic() + float(params.get("seconds", 10))
            while time.monotonic() < deadline:
                cancellation.checkpoint(job["id"])
                time.sleep(min(0.05, deadline - time.monotonic()))
        return {"elapsed": float(params.get("seconds", 10)),
                "timeline_edits": 0, "restored": before}
    if kind == "resolve_axi":
        _start_checkpoint(job)
        with write_scope(
                project=job.get("project"), timeline_name=job.get("timeline"),
                run_id=job.get("id")):
            result = _run_resolve_axi(dict(
                params, caller=(params.get("caller") or job.get("owner") or "")))
        if result["exit_code"] == 0:
            return _commit_checkpoint(job, result)
        return result
    if kind == "fusion.registry_summary":
        return _run_registry_summary(resolve)
    if kind == "timeline.apply_patch":
        # The external boundary: the patch arrives from the socket. The
        # mode decides - log in production (a hard refusal on the live
        # edit path needs live evidence first), refuse in tests/dev.
        protocol_conformance.validate(params["patch"], "edit-patch")
    if kind == "timeline.semantic_edit":
        from library.tools.resolved import cancellation

        _start_checkpoint(job)
        project = _open_project(resolve, job["project"])
        timeline = _timeline(project, job["timeline"])
        with (write_scope(project=job["project"],
                          timeline_name=job["timeline"],
                          run_id=job.get("id")),
              resolve_lock.cursor_excursion(project, timeline, purpose)):
            cancellation.checkpoint(job["id"])
            from library.tools import resolve_axi_core
            try:
                receipt = resolve_axi_core.execute_semantic_edit_job(
                    job, resolve=resolve)
            except ValueError as exc:
                raise JobRefused(str(exc)) from exc
        if receipt.get("status") == "committed":
            return _commit_checkpoint(job, receipt)
        raise JobRefused(
            "semantic edit did not reach verified committed completion",
            result=receipt)
    if kind == "resolve.qualification_session":
        # The broker never switches - except through borrowed(), which
        # saves the open project first and reads the restore back. If the
        # fixed Qualification target is already current, keep that session
        # in place: there is no project to borrow or restore.
        from library.tools import qualification_project
        _start_checkpoint(job)
        return _commit_checkpoint(
            job, qualification_project.run_qualification_session(job, resolve))
    if kind == "reel.promote_staged":
        # The target is fixed to Ren Qualification by prepare(). The typed
        # runner borrows the current project and opens Qualification under
        # the same exclusive lease before it touches a timeline.
        from library.tools.resolved import reel_promotion
        _start_checkpoint(job)
        with write_scope(project=QUALIFICATION_PROJECT,
                         timeline_name=job.get("timeline"),
                         run_id=job.get("id")):
            return _commit_checkpoint(job, reel_promotion.run(job, resolve))
    if kind == "reel.prepare_qualification_fixture":
        from library.tools.resolved import reel_promotion_fixture
        _start_checkpoint(job)
        with write_scope(project=QUALIFICATION_PROJECT,
                         timeline_name=reel_promotion_fixture.FINAL,
                         run_id=job.get("id")):
            return _commit_checkpoint(
                job, reel_promotion_fixture.run(job, resolve))
    if kind == "qualification.workflow":
        from library.tools.qualification_workflow import run_broker_job
        _start_checkpoint(job)
        return _commit_checkpoint(job, run_broker_job(job, resolve))
    if kind == "timeline.drt_recovery_probe":
        from library.tools.resolved import drt_recovery_probe
        _start_checkpoint(job)
        return _commit_checkpoint(job, drt_recovery_probe.run(job, resolve))
    if kind == "reel.probe_qualification_marker_loss":
        from library.tools.resolved import marker_loss_probe
        _start_checkpoint(job)
        with write_scope(project=QUALIFICATION_PROJECT,
                         timeline_name=marker_loss_probe.FINAL,
                         run_id=job.get("id")):
            return marker_loss_probe.run(job, resolve)
    if kind == "reel.copy_tv_ending":
        from library.tools.resolved import reel_ending_copy
        return _commit_checkpoint(job, reel_ending_copy.run(job, resolve))
    project = _open_project(resolve, job["project"])
    if kind == "timeline.observe_current":
        from library.tools import reel_read, timeline_shadow
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            raise JobRefused(
                f"no current timeline in {project.GetName()!r}; "
                "the broker will not select one for a background snapshot")
        store = timeline_shadow.ShadowStore()
        project_name, timeline_id, _timeline_name = timeline_shadow.timeline_key(
            project, timeline)
        head = store.head(project_name, timeline_id)
        fingerprint = timeline_shadow.light_fingerprint(
            reel_read.read_light_state(timeline))
        if head is not None:
            recorded = store.snapshot(head)
            if fingerprint == timeline_shadow.light_fingerprint(
                    timeline_shadow.light_state_of_snapshot(recorded)):
                return head.summary()
        return timeline_shadow.observe(project, timeline, store).summary()
    if kind == "resolve.ui_transaction":
        # The transaction establishes the timeline itself - the cursor
        # moves to it - so it is NOT required to be current here, and
        # the runner does the establishing under the exclusive lease.
        from library.tools.resolved import ui_transaction
        _start_checkpoint(job)
        with write_scope(project=job.get("project"),
                         timeline_name=job.get("timeline"),
                         run_id=job.get("id")):
            receipt = ui_transaction.run(job, resolve, project=project)
            if receipt.get("status") == "committed":
                return _commit_checkpoint(job, receipt)
            return receipt
    if kind == "timeline.drt_export_probe":
        from library.tools.resolved import drt_export_probe
        return drt_export_probe.run(job, resolve, project=project)
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
            from library.tools.resolved import cancellation
            cancellation.checkpoint(job["id"])
            patch = params["patch"]
            with (write_scope(project=patch["project"],
                              timeline_name=patch["timeline"],
                              run_id=job.get("id")),
                  resolve_lock.cursor_excursion(project, timeline, purpose)):
                receipt = _jsonable(edit_patch.apply_patch(
                    patch, resolve=resolve, project=project,
                    timeline=timeline))
            if receipt.get("status") == "committed":
                return _commit_checkpoint(job, receipt)
            return receipt
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
    caller_token = resolve_axi._RECORDED_CALLER.set(
        str(params.get("caller") or ""))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = resolve_axi.main(list(params["argv"]))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
    finally:
        try:
            os.chdir(previous)
        finally:
            resolve_axi._RECORDED_CALLER.reset(caller_token)
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
