"""resolve-axi: agent-ergonomic timeline reads and guarded Resolve writes.

The captain's ask: unify the hand-written-script-through-MCP paths that
drive DaVinci Resolve every day into one command surface, in TOON rather
than escaped JSON, so LLM-driven Resolve control costs fewer tokens and
fewer turns.

The command surface comes from six measured incidents, not from the API
surface: `timeline list` (staging siblings visible), `timeline get`
(exact names win, unique prefixes resolve, ambiguous ones are
refused), `markers` (both marker planes in one call),
`markers snapshot`/`restore`/`reply` (content round-trip, dry-run
default), `items` (with `--transforms`), `captions`, `fusion` (comp
coverage per clip), `cursor` (the global cursor made visible
and assertable), `frames` (frame counts for drift checks),
`pool` (every clip in the media pool with its bin, plus the offline
count), `renders` (the queue with locale-independent states, failures
with their errors), `api` (the native MCP's knowledge tools - stubs,
search, docs, whats-new - wrapped as TOON), `luts` (the shared LUT
shelf listed locally; DCTL/LUT writes wrapped with compile check,
dry-run default), `launch` (idempotent app start, never opens a
project), `project` (identity plus the delivery-relevant settings),
`audio` (audio tracks with enable state and clip counts), `run`
(a caller script with ready Resolve names in scope, its `result`
rendered as TOON rows; `--purpose` records the run without a developer
opt-in), `workflow` (review recurring scripts, promote one explicitly,
and re-run it by name), and the write verbs:
`edit place|trim|move|delete|title|speed` (reel items,
cursor-asserted), `edit transition` (carriers reported; native
`AddTransition` writes behind `--apply`, verified by the returned
transition item), `multicam build|sync` (pool multicam clips from
source clips; build verified by the pool listing, sync planned with
`--apply` refused until a read-back is measured), `color lut|group`
(node LUTs and color-group assignment, both verified by re-read),
`audio isolate` (per-track voice isolation, verified by re-read),
`ingest` (pool imports), `relink` (re-point moved media, verified by
re-read), `render queue|start|stop`,
`project set` (one setting), `project scratch create|import|open|restore|delete`
(Ren Scratch - prefixed projects only, with origin read-back/restore),
`project export-timeline` (one named timeline copied out of ANY project
into a Ren-owned scratch project; the source is read only, never saved,
and the origin project, timeline and page are restored and read back),
`timeline duplicate` (versioning), `timeline delete` (exact owned-resource
retirement), and
`sense transcribe|cuts|classify|intellisearch|reframe|mask|switch`
(Resolve's own AI beside Ren's - transcription with speakers, scene
cuts, audio classes, IntelliSearch, Smart Reframe, Magic Mask and
Smart Switch - each judged by read-back, never wired into pipeline
decisions).

Safety shape, stated once:

- Every command here is READ-ONLY except the write verbs behind
  `--apply` (`markers restore`, `markers reply`, `edit *`,
  `multicam build`, `color lut|group`, `audio isolate`, `ingest`,
  `relink`,
  `render queue|start|stop`, `project set`,
  `project scratch create|import|open|restore|delete`, `project export-timeline`,
  `timeline duplicate`,
  `timeline delete`,
  `sense *`)
  and `run --unsafe`.
- No command opens or creates projects except the explicitly named
  `project scratch` lifecycle and `project export-timeline`, and
  nothing creates timelines except
  `project scratch create`. `timeline list/get` use recorded
  generations; `timeline get --refresh-live` requires the exact target
  to be current. Other timeline reads use `GetTimelineByIndex`, never
  `SetCurrentTimeline`. Opening something is a write to the captain's
  session.
- Reads hold the Resolve lease SHARED (`exclusive=False`); any call
  with `--apply` or `--unsafe` holds it EXCLUSIVE. `restore --apply` and `reply --apply`
  refuse unless the cursor already sits on the reel; `run --unsafe`
  reports the cursor before and after the script instead (an arbitrary
  script owns its own cursor, so there is nothing to assert it against).
- `run` without `--unsafe` refuses scripts that call Resolve writers
  (a prefix rule over mutator verbs, stated at `_RUN_WRITE_PREFIXES`).
  Reads must still never move the cursor, and the AST test still holds
  for everything that is not a declared write.
- A recorded `run --purpose` carries a durable record in Ren's project
  or user script store. A writing script also requires `--unsafe`, takes
  the exclusive lease, saves a per-run `.drt` recovery archive and
  compares the before/after timeline and project inventory. Project
  switching is refused through the script scope and restored if a raw
  API call switches anyway. Recurring scripts appear as workflow
  candidates; promotion is explicit.
- A run without `--purpose` keeps the developer-shell opt-in
  (`RESOLVE_AXI_ALLOW_RUN=1`) for compatibility. The broker accepts
  unopted runs only when they carry `--purpose`.
- `run --unsafe` still refuses `CopyGrades` unless
  `--acknowledge-copy-grades` names it out loud: the call replaces
  the target's whole grade, reports success, and versions nothing
  to go back to (`_COPYGRADES_TRAP`). No write verb here calls it.
- Every write verb is dry-run by default and `--apply` to write;
  timeline-scoped writes assert the cursor sits on the reel (appends
  and queueing act on the CURRENT timeline); every write is read
  back and the read-back decides. An append is never retried, and a
  delete that answers False is re-read before one retry at most.
- Write verbs keep explicit flags: the bare-positional rule covers
  reads, and a destructive path with a bare primary argument is one
  typo from the wrong reel.
- `markers restore` defaults to a dry-run diff. `--apply` is the
  explicit flag, and it restores the TIMELINE plane only; a snapshot
  holding clip/pool-plane rows is REFUSED unless `--allow-partial`
  acknowledges the gap (the refusal names the count, the planes, and
  how to re-apply the clip half by hand - see `RESTORE_SCOPE`).

Output contract (axi.md principles 1-6, 9-10):

- stdout carries TOON tables (`name[N]{cols}:` plus backtick-quoted
  CSV rows - the same quote character `toon_serializer` uses, because
  prose here holds apostrophes and embedded JSON holds double quotes)
  followed by `help[]` next-step hints.
- A command's own refusals go to stdout as `error: ...` plus a
  `help: ...` fix line. Exit 0 is success (including no-ops), 1 is an
  error, 2 is a usage error such as an unknown flag - which is always
  rejected loudly, never ignored.
- A USAGE error is a misuse of the command line, so it goes to stderr
  with exit 2 - the way argparse's own usage errors do and the way
  `ren` answers an unknown verb. `--help` is not an error: it prints
  on stdout and exits 0.
- Large text (marker notes) is truncated with a size hint and a
  `--full` escape hatch.
- `-v` / `-V` / `--version` answer from this leaf module before any
  heavy import runs.

Command-shape rule (the round-2 lesson, stated as a rule after round 5
broke it again): any command taking one obvious primary argument
accepts it positionally. A bare timeline name routes onto `--timeline`
for the read commands (`_BARE_TIMELINE_COMMANDS`, via `_normalize`);
a bare script IS the script for `run`. `--timeline`/`--script`/`--file`
keep working as the explicit forms. `test_positional_primary_args`
enumerates the commands and asserts the shape, so the next command
built without a positional fails here instead of on first real use.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import contextvars
import csv
import io
import json
import os
import sys
import time as _time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from library.tools import refusal_taxonomy

VERSION = "0.7.0"

DESCRIPTION = ("Read recorded timeline generations or explicitly refresh "
               "the live Resolve session in token-cheap TOON rows")

TOOL = "resolve-axi"

# Resolve's render API returns from StartRendering while the job is still
# running. `render start --apply` therefore keeps its exclusive instance
# lease and polls until the completion read-back says rendering stopped.
RENDER_POLL_SECONDS = 3.0
RENDER_READ_ERROR_NOTICE_SECONDS = 30.0

#: Marker-note text shown inline before the truncation hint fires.
NOTE_PREVIEW_CHARS = 500

#: What `markers restore --apply` writes. Timeline-plane markers round
#: trip exactly through `AddMarker(frame, colour, name, note, duration,
#: customData)`; clip/pool-plane rows carry no verified write path in
#: this repo (no caller writes them today), so they are compared in the
#: dry run and reported, never guessed at. A refused restore tells the
#: caller to re-apply that half by hand from the snapshot file (each
#: skipped row carries its plane, frame, name and words) - the refusal
#: names no machine-local path, because the file lives wherever the
#: caller wrote the snapshot.
RESTORE_SCOPE = "timeline-plane"

#: Snapshot fields that name each frame number for what it IS.
#: `frame_in_timeline_space` is the inverted legacy name: for a clip
#: note it holds the clip-local SOURCE frame, not a timeline position.
#: It stays in the payload (restore and older readers key on it) but
#: new callers must read `timeline_frame` / `source_frame` instead.
TIMELINE_FRAME_FIELD = "timeline_frame"
SOURCE_FRAME_FIELD = "source_frame"
DEPRECATED_FRAME_FIELD = "frame_in_timeline_space"
DEPRECATED_FRAME_NOTE = (
    f"{DEPRECATED_FRAME_FIELD} is misnamed: for clip notes it holds "
    f"the clip-local source frame - read {SOURCE_FRAME_FIELD} for that "
    f"and {TIMELINE_FRAME_FIELD} for the timeline position"
)

# NOTE: there is deliberately no allowlist of restorable planes beside
# `timeline_marker` itself. The restore filter below refuses anything
# whose source is not the timeline plane, so a future plane fails
# closed rather than slipping through an enumeration nobody updated.


# ── TOON output ──────────────────────────────────────────────────────
#
# Named-table headers (`timelines[32]{name,frames}:`) in the axi
# catalog shape; row quoting follows `toon_serializer`'s rule (a
# BACKTICK, doubled when it occurs in content).


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return " ".join(text.split())


def _format_row(vals: list) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quotechar="`", doublequote=True,
                        quoting=csv.QUOTE_MINIMAL, lineterminator="")
    writer.writerow([_cell(v) for v in vals])
    return buffer.getvalue()


def table(name: str, rows: list, cols: list) -> str:
    """One named TOON table. Empty collections say zero explicitly."""
    if not rows:
        return f"{name}: 0 rows"
    colspec = ",".join(cols)
    lines = [name + "[" + str(len(rows)) + "]{" + colspec + "}:"]
    for row in rows:
        if isinstance(row, dict):
            lines.append("  " + _format_row([row.get(c) for c in cols]))
        else:
            lines.append("  " + _format_row(list(row)))
    return "\n".join(lines)


def kv_block(name: str, pairs: dict) -> str:
    lines = [f"{name}:"]
    for key, value in pairs.items():
        lines.append(f"  {key}: {_cell(value)}")
    return "\n".join(lines)


def help_block(suggestions: list) -> str:
    lines = [f"help[{len(suggestions)}]:"]
    lines.extend(f"  Run `{s}`" for s in suggestions)
    return "\n".join(lines)


def emit(parts: list) -> None:
    sys.stdout.write("\n".join(p for p in parts if p) + "\n")


def emit_usage_error(parts: list) -> None:
    """The same shape as `emit`, on stderr: a usage error is a misuse of
    the command line, not one of the command's own refusals."""
    sys.stderr.write("\n".join(p for p in parts if p) + "\n")


def preview(text: str, full: bool) -> str:
    text = text or ""
    if full or len(text) <= NOTE_PREVIEW_CHARS:
        return text
    return (f"{text[:NOTE_PREVIEW_CHARS]}... "
            f"(truncated, {len(text)} chars total - use --full)")


# ── Errors ───────────────────────────────────────────────────────────


class AxiError(RuntimeError):
    """A structured failure: what went wrong plus the fixing command.

    PROD-003: it carries the BLOCKER - which of the six kinds of block
    this is - and the NEXT ACTION, the move the caller can take, so an
    AXI refusal says the same thing the engine's own refusals say.  The
    fixing command is REQUIRED: a refusal with no next step is the
    defect this removes, and it is refused here, at the raise site.
    """

    def __init__(self, message: str, fix: str = "",
                 blocker: str = "unsupported_operation",
                 next_action: str = "") -> None:
        super().__init__(message)
        self.fix = fix
        self.blocker = blocker
        self.next_action = (next_action
                            or refusal_taxonomy.next_action_for(blocker))
        if not str(fix).strip():
            raise ValueError(
                "an AXI refusal must carry the command or edit that "
                "answers it; state the next step the caller can take "
                f"(message: {message!r})")
        refusal_taxonomy.assert_supplyable(
            str(fix), supplyable_commands(),
            where="the AXI refusal's fix")


def fail(message: str, fix: str = "", blocker: str = "",
         next_action: str = "") -> int:
    parts = [f"error: {message}"]
    if blocker:
        parts.append(f"blocker: {blocker} - "
                     f"{refusal_taxonomy.plain_reason(blocker)}")
    if fix:
        parts.append(f"help: {fix}")
    if next_action:
        parts.append(f"next action: {next_action}")
    emit(parts)
    return 1


def _read_notes(timeline, fix: str):
    """`marker_feedback.read_notes`, failing clean instead of tracebacking.

    A timeline that will not report its markers, rate, start frame or
    items used to fail deep inside the listing with a bare TypeError,
    so `resolve-axi markers` crashed on a scratch timeline instead of
    refusing (finding 19, execution-frontier report 2026-09-24). The
    unreadable shape now arrives as `TimelineUnreadableError` and
    leaves here as an `AxiError` the call sites answer with `fail`.
    Anything else stays loud: an unexpected traceback is a bug, and a
    clean refusal would hide it.
    """
    from library.tools import marker_feedback
    try:
        return marker_feedback.read_notes(timeline)
    except marker_feedback.TimelineUnreadableError as exc:
        raise AxiError(str(exc), fix) from exc


class Parser(argparse.ArgumentParser):
    """argparse that fails loud onto stderr with exit 2 (axi principle 6).

    The error TEACHES the working invocation (axi principle 9),
    because the predictable wrong guess is known: timelines are
    addressed with `--timeline` (or a bare name), and an agent
    guessing `--reel` should land on the right flag in one turn,
    not after a `--help` round trip.

    A usage error is a misuse of the COMMAND LINE, not one of the
    command's own refusals: it goes to stderr with exit 2, the way
    argparse's own usage errors do and the way `ren` answers an
    unknown verb. A command's own refusals stay on stdout with exit
    1, and `--help` is not an error at all - it prints on stdout and
    exits 0.
    """

    def error(self, message):
        lines = [f"error: {message}"]
        if "--reel" in message or (
                self.prog.endswith("markers")
                and "invalid choice" in message):
            # The only positionals `markers` owns are snapshot/restore,
            # so anything else in that slot is a reel-name attempt
            # (`markers --reel "Reel 29"`, or a bare name that missed
            # the rewrite). Name the working shape, not the manual.
            # (`--reel` itself is spelled on the recorded-read
            # commands, so this fires for the verbs that do not take
            # it - the writes.)
            lines.append(
                f"help: timelines take --timeline \"<name>\" (exact or "
                f"unique prefix), or a bare name: {TOOL} markers "
                f"\"Reel 29\" - or, on a recorded read, --reel 7 "
                f"--project-folder <project>")
        else:
            lines.append(f"help: {self.prog} --help lists the valid flags")
        emit_usage_error(lines)
        raise SystemExit(2)


# ── Resolve access (all lazy: nothing here imports at module load) ───


def _connect():
    # The scripting handshake itself can block behind an in-flight Resolve
    # operation.  Callers must acquire the bounded instance lease before
    # opening a client connection, not after it.
    from library.tools.resolve_lock import held
    if not held():
        raise AxiError(
            "Resolve connection attempted before taking the instance "
            "lease - route this command through resolve-axi main()",
            f"{TOOL} --help")
    from library.tools.marker_feedback import connect_resolve
    try:
        return connect_resolve()
    except Exception as exc:
        raise AxiError(
            f"cannot reach Resolve: {exc}",
            f"{TOOL} --help") from exc


def _lease(exclusive: bool):
    from library.tools.resolve_lock import resolve_lease
    return resolve_lease(f"resolve-axi ({TOOL})", exclusive=exclusive)


def _qualification_context(resolve, project, timeline, operations) -> dict:
    """The routing context for an AXI dispatch, with the MEASURED state.

    A promoted operation is routed only when the runtime satisfies the
    recorded qualification predicates, and that state has to be READ
    (`library/tools/axi_qualification_state.py`). The command already
    holds the lease, so the read happens under it - no second lease, and
    no second scripting client.
    """
    from library.tools import axi_qualification_state

    return {"qualification_state": axi_qualification_state.inspect_handles(
        resolve=resolve, resolve_project=project, timeline=timeline,
        operations=operations)}


# These verbs run locally, never wrapped by the broker's argv router:
# the api/luts verbs use the native MCP or local files only, and
# intellisearch-collect submits its own job kinds to the broker rather
# than being parsed by it (the serving broker runs main and refuses
# verbs it does not know yet).
_LOCAL_COMMANDS = frozenset({
    "cmd_capabilities",
    "cmd_actions",
    "cmd_operation_map",
    "cmd_capability_coverage",
    "cmd_support",
    "cmd_api_search", "cmd_api_stubs", "cmd_api_docs",
    "cmd_api_whats_new", "cmd_api_intellisearch_query",
    "cmd_intellisearch_collect",
    "cmd_luts_list", "cmd_luts_update",
    "cmd_luts_delete", "cmd_luts_generate", "cmd_setup_hooks",
    "cmd_update", "cmd_workflow_list", "cmd_workflow_candidates",
    "cmd_workflow_promote",
})


def cmd_operation_map(args) -> int:
    """Serve the canonical operation map: what AXI can do, and how.

    JSON is the default because this verb exists to be READ BY A MODEL
    rather than by a person: a model that picks its own execution path
    is how a semantically different edit reaches the timeline.  `--toon`
    prints the same facts as a table for a person reading them.

    `capabilities` answers "is this operation available"; this answers
    "what does it MEAN, what does it DEPEND on, which backend executes
    it, what does it PRESERVE, what QUALIFIES it, and in which project
    STATES is it supported" - and audits the registry against the code.
    """
    from library.tools import operation_map

    requested = (getattr(args, "operation", "") or "").strip()
    as_toon = bool(getattr(args, "toon", False))
    audit_only = bool(getattr(args, "audit", False))

    if audit_only:
        payload = operation_map.audit()
    else:
        payload = operation_map.operation_map()
        if requested:
            entry = payload["operations"].get(requested)
            if entry is None:
                control = payload["command_controls"].get(requested)
                if control is None:
                    return fail(f"unknown operation or command control {requested!r}",
                                f"{TOOL} operation-map")
                payload = {"schema": payload["schema"],
                           "command_control": control,
                           "audit": payload["audit"]}
            else:
                payload = {"schema": payload["schema"],
                           "operation": entry,
                           "audit": payload["audit"]}

    if as_toon:
        sys.stdout.write(_operation_map_toon(payload) + "\n")
        return 0
    sys.stdout.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return 0


def cmd_capability_coverage(args) -> int:
    """Serve the capability coverage report: what is covered, and what is not.

    One report over the whole edit vocabulary, generated from the
    registry (`edit_algebra.OP_STATUS`), the qualification records and
    the router itself - never from a list written here.  The default
    output is the SUMMARY, because the full report is a megabyte of
    evidence and a reader who wants one operation asks for one
    operation.
    """
    from library.tools import capability_coverage

    requested = (getattr(args, "operation", "") or "").strip()
    full = bool(getattr(args, "full", False))
    report = capability_coverage.coverage_report()
    if requested:
        row = report["operations"].get(requested)
        if row is None:
            return fail(f"unknown operation {requested!r}",
                        f"{TOOL} capability-coverage --operation <id>")
        payload = {"schema": report["schema"],
                   "operation": row}
    elif full:
        payload = report
    else:
        payload = {key: report[key] for key in (
            "schema", "generated_by", "sources", "operation_count",
            "qualification_status_counts", "production_enabled_operations",
            "operations_refused_by_the_production_route",
            "production_route_note", "qualification_gap_priority",
            "priority_note", "priority")}
    sys.stdout.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return 0


def cmd_actions(args) -> int:
    """PLAN-010: the query that says what an agent MAY do, and what it costs.

    `capabilities` answers about an operation the caller already named.
    This verb answers the question a caller actually has - "what can I do
    about this?" - and returns the smallest relevant slice for each
    answer: the schema and a worked example, the known preservation
    limits, the expected verification, the refusal categories with their
    recovery options, and how to request it.

    The menu holds the operations ordinary routing may use.  The
    operations that are declared but not routable are reported beside
    it, each with the blocker that keeps it out, so a caller learns the
    gap instead of naming an operation that cannot run.
    """
    from library.tools import action_discovery

    request = (getattr(args, "request", "") or "").strip()
    limit = int(getattr(args, "limit", 5) or 5)
    if not request:
        payload = action_discovery.catalog()
    else:
        payload = action_discovery.discover(request, limit=limit)
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
        return 0
    sys.stdout.write(_actions_toon(payload) + "\n")
    return 0


def _actions_toon(payload: dict) -> str:
    """The action query as compact tables, for a person reading it."""
    if "operations" in payload:
        rows = [{
            "id": row["id"],
            "effect": row["effect"],
            "required": ", ".join(row["required"]) or "none",
            "verified_by": row["verified_by"],
        } for row in payload["operations"]]
        return "\n".join([
            f"{payload['supported_count']} of {payload['total_operations']} "
            "operations are production-enabled; these are they.",
            "",
            table("supported_operations", rows,
                  ["id", "effect", "required", "verified_by"]),
        ])
    parts = [
        f"query: {payload['query']}",
        (f"matched the effect of: {', '.join(payload['terms'])}"
         if payload["effect_match"] else
         "no operation's name matched this effect; the menu below is the "
         "supported set, ordered by how often the editorial loop asks "
         "for it"),
        "",
    ]
    rows = []
    for match in payload["matches"]:
        card = match["card"]
        rows.append({
            "id": card["id"],
            "effect": card["effect"],
            "required": ", ".join(card["schema"]["required"]) or "none",
            "preserves": "; ".join(card["preserves"]["limits"]) or "not recorded",
            "verified_by": card["verifies"]["contract"],
        })
    parts.append(table("supported_actions", rows,
                       ["id", "effect", "required", "preserves", "verified_by"]))
    if payload.get("not_routable"):
        parts.append("")
        parts.append(table("declared_not_routable", [
            {"id": row["id"],
             "effect": row["effect"],
             "blocker": row["blocker"],
             "next_action": row["next_action"],
             "supported_alternatives": ", ".join(
                 row["supported_alternatives"]) or "none"}
            for row in payload["not_routable"]],
            ["id", "effect", "blocker", "next_action", "supported_alternatives"]))
    parts.append("")
    parts.append(table("refusal_categories", [
        {"blocker": row["blocker"], "plain": row["plain"],
         "next_action": row["next_action"]}
        for row in payload["refusal_catalog"]],
        ["blocker", "plain", "next_action"]))
    return "\n".join(parts)


def cmd_support(args) -> int:
    """Answer whether one operation may run on a timeline in these conditions.

    The read-only question BASE-002 asks for: "is this edit supported on
    the timeline in front of me, and what prevents it if not".  The
    conditions are the measured state of that timeline, in the
    qualification dimension vocabulary; with none, the answer is
    "unknown" - never "supported".
    """
    from library.tools import capability_coverage

    requested = (getattr(args, "operation", "") or "").strip()
    if not requested:
        return fail("an operation id is required",
                    f"{TOOL} support <operation> --conditions '<json>'")
    conditions = _support_conditions(args)
    params = _support_params(args)
    payload = capability_coverage.support(requested, conditions=conditions,
                                          params=params)
    if not payload.get("known"):
        return fail(payload["why"][0], f"{TOOL} capability-coverage")
    sys.stdout.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return 0


def _support_conditions(args) -> dict | None:
    """The measured state the support question is asked about."""
    inline = (getattr(args, "conditions", "") or "").strip()
    path = (getattr(args, "conditions_file", "") or "").strip()
    if inline and path:
        raise AxiError("give the conditions once, not twice",
                       f"pass --conditions or --conditions-file, not both")
    text = inline
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise AxiError(f"cannot read the conditions file: {exc}",
                           f"pass --conditions '<json>' instead") from exc
    if not text:
        return None
    try:
        conditions = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AxiError(f"the conditions are not JSON: {exc}",
                       "pass the measured state as a JSON object, e.g. "
                       "'{\"media_type\": \"video\", ...}'") from exc
    if not isinstance(conditions, dict):
        raise AxiError("the conditions must be a JSON object",
                       "the measured state is a mapping of qualification "
                       "dimension to value")
    return conditions


def _support_params(args) -> dict | None:
    """The operation parameters the caller has, if any."""
    inline = (getattr(args, "params", "") or "").strip()
    if not inline:
        return None
    try:
        params = json.loads(inline)
    except json.JSONDecodeError as exc:
        raise AxiError(f"the parameters are not JSON: {exc}",
                       "pass the parameters as a JSON object") from exc
    if not isinstance(params, dict):
        raise AxiError("the parameters must be a JSON object",
                       "pass the operation's own parameters as a JSON object")
    return params


def _operation_map_toon(payload: dict) -> str:
    """Render the map as compact tables for a reader, not a model."""
    if "operation" in payload:
        entry = payload["operation"]
        parts = [kv_block("operation", {
            "id": entry["id"],
            "purpose": entry["semantics"]["purpose"],
            "support_level": entry["support_level"],
            "execution_paths": ", ".join(entry["execution_paths"]),
            "axi_controls": ", ".join(entry["axi_controls"]) or "none",
            "axi_commands": ", ".join(entry["axi_verbs"]) or "none",
            "edit_request_routable": str(
                entry["how_to_request"]["edit_request_routable"]),
        })]
        parts.append(table("backends", [
            {"id": row["backend"],
             "path": row["execution_path"] or "undeclared",
             "implemented": row["implemented"],
             "production_enabled": row["production_enabled"],
             "qualification": row["qualification_status"]}
            for row in entry["backend_capabilities"]],
            ["id", "path", "implemented", "production_enabled",
             "qualification"]))
        return "\n".join(parts)
    if "command_control" in payload:
        control = payload["command_control"]
        return kv_block("command_control", {
            "id": control["id"],
            "type": control["type"],
            "control_kind": control["control_kind"],
            "command": control["command"] or "unavailable",
            "edit_request_routable": str(control["edit_request_routable"]),
        })
    if "counts" in payload:
        return kv_block("audit_counts", payload["counts"])
    return kv_block("map", {
        "schema": payload["schema"],
        "operations": payload["operation_count"],
    })


def cmd_capabilities(args) -> int:
    """Discover operations and answer focused questions from registry evidence."""
    from library.tools import edit_algebra, operation_map
    from library.tools.native_mcp import capabilities as native_capabilities

    requested = getattr(args, "operation", "") or ""
    query = (getattr(args, "search", "") or "").strip()
    as_json = bool(getattr(args, "json", False))

    def purpose_text(op) -> str:
        if op.note:
            return op.note
        if op.id == "overlay.resize":
            return "Resize an existing overlay graphic, logo, title, or lower third."
        if op.id.startswith("overlay."):
            return f"{op.id.removeprefix('overlay.').replace('_', ' ')} an overlay graphic."
        return f"{op.id.replace('.', ' ').replace('_', ' ')} operation."

    def concise_purpose(op) -> str:
        purpose = purpose_text(op)
        return purpose if len(purpose) <= 120 else purpose[:117] + "..."

    def operation_detail(op_id: str) -> dict:
        data = edit_algebra.capability(op_id)
        entry = edit_algebra.status(op_id)
        if data is None or entry is None:
            return {}
        registered = {backend["id"]: backend
                      for backend in data["backends"]}
        known_backends = sorted({
            backend.backend
            for status in edit_algebra.OP_STATUS.values()
            for backend in status.backends
        })
        all_backends = []
        for backend_id in known_backends:
            backend = registered.get(backend_id)
            if backend is None:
                all_backends.append({
                    "id": backend_id, "eligible": False,
                    "reason": "operation is not registered with this executor",
                })
            else:
                eligible = backend["production_enabled"]
                all_backends.append({
                    **backend, "eligible": eligible,
                    "reason": ("qualified for production" if eligible else
                               "implemented, but lacks live qualification"),
                })
        operation = edit_algebra.ALGEBRA[op_id]
        return {
            **data,
            "schema": entry.param_schema,
            "purpose": purpose_text(operation),
            "expected_effect": operation.note or (
                f"Apply {op_id} to {entry.state_domains[0]} state; "
                f"verify with {entry.verification_contract}."),
            "eligible_backends": [backend["id"] for backend in all_backends
                                  if backend["eligible"]],
            "backends": all_backends,
            "prerequisites": {
                "resolve_capabilities": data["required_resolve_capabilities"],
                "media_types": data["media_types"],
                "timeline_conditions": data["timeline_conditions"],
            },
            "recovery_limits": {
                "inverse": data["inverse_recovery"],
                "qualified_backends": data["recovery_capability"]["proven_backends"],
                "qualification_evidence": data["recovery_capability"]["proven_evidence"],
            },
            "qualification_evidence": sorted({
                evidence for backend in all_backends
                for evidence in backend.get("qualification_evidence", [])
            }),
        }

    def render(payload: dict, code: int = 0) -> int:
        if as_json:
            sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        else:
            sys.stdout.write(_capabilities_toon(payload) + "\n")
        return code

    def search_text(op) -> str:
        words = [op.id, op.domain, op.note, *op.required]
        if op.id.startswith("overlay."):
            words.extend(("overlay", "graphic", "logo", "lower", "third",
                          "title", "on-screen", "element"))
            action = op.id.removeprefix("overlay.").replace("_", " ")
            words.extend((action, "size dimensions") if action == "resize" else ())
        if op.id.startswith("clip."):
            words.extend(("video", "shot", "footage", "clip"))
        if op.id.startswith("caption"):
            words.extend(("subtitle", "text", "styling"))
        return " ".join(words).casefold()

    def production_enabled_ids() -> list:
        """Every operation id this build may route in production.

        Both vocabularies, because a caller who asks for an unknown id
        does not know which one it meant: the edit algebra's operation
        ids and the AXI command names that carry them.
        """
        algebra = [status.id for status in edit_algebra.OP_STATUS.values()
                   if status.production_enabled]
        axi = [name for name, ids in edit_algebra.AXI_OPERATION_MAP.items()
               if ids and all(edit_algebra.status(op_id).production_enabled
                              for op_id in ids)]
        return sorted(set(algebra) | set(axi))

    def domain_alternatives(op_id: str) -> list:
        """The production-enabled operations in the same domain.

        The answer to "what CAN I do instead" is not a guess: it is the
        operations of the same domain that carry live qualification
        evidence, read off the registry.
        """
        entry = edit_algebra.status(op_id)
        if entry is not None:
            domain = edit_algebra.ALGEBRA[op_id].domain
            rows = []
            for status in edit_algebra.OP_STATUS.values():
                if not status.production_enabled or status.id == op_id:
                    continue
                if edit_algebra.ALGEBRA[status.id].domain != domain:
                    continue
                rows.append({"id": status.id,
                             "summary": concise_purpose(
                                 edit_algebra.ALGEBRA[status.id]),
                             "verdict": "production_enabled"})
            rows.sort(key=lambda row: row["id"])
            return rows
        # An AXI command name with no algebra operation of its own: the
        # alternatives are the commands of the same family that ARE
        # production-enabled.
        prefix = op_id.split(".")[0]
        rows = []
        for name, ids in edit_algebra.AXI_OPERATION_MAP.items():
            if not name.startswith(prefix + ".") or not ids:
                continue
            if not all(edit_algebra.status(op_id).production_enabled
                       for op_id in ids):
                continue
            rows.append({"id": name, "summary": "AXI command",
                         "verdict": "production_enabled"})
        rows.sort(key=lambda row: row["id"])
        return rows

    def support_level(requested: str) -> str:
        """The operation's support band, from the ONE reading.

        `operation_map.support_level` is the canonical band - production
        enabled, context qualified, implemented, or unsupported - and
        the map is what the canonical operation record publishes.  This
        answer uses the same four bands, so a caller reading both
        surfaces sees one vocabulary.

        An AXI command name is read through the algebra operations it
        carries, and the WEAKEST of them is the command's band: a
        command whose operations are not all production-enabled is not
        production-enabled.
        """
        from library.tools import operation_map
        entry = edit_algebra.status(requested)
        if entry is not None:
            return operation_map.support_level(entry)
        if requested.startswith("native_mcp."):
            # Blackmagic's own MCP tools are implemented mechanisms with
            # no live qualification - and they are explicitly not a
            # timeline-edit backend.
            return operation_map.SUPPORT_IMPLEMENTED
        op_ids = edit_algebra.AXI_OPERATION_MAP.get(requested, ())
        if not op_ids:
            return operation_map.SUPPORT_UNSUPPORTED
        levels = []
        for op_id in op_ids:
            status = edit_algebra.status(op_id)
            if status is None:
                return operation_map.SUPPORT_UNSUPPORTED
            levels.append(operation_map.support_level(status))
        order = (operation_map.SUPPORT_PRODUCTION,
                 operation_map.SUPPORT_CONTEXT,
                 operation_map.SUPPORT_IMPLEMENTED,
                 operation_map.SUPPORT_UNSUPPORTED)
        return min(levels, key=order.index)

    def guidance_for(payload: dict, requested: str) -> dict | None:
        """Structured guidance for an operation this build cannot route.

        None when the operation is production-enabled: there is nothing
        to guide about.  Otherwise the record names the gap, the
        operations that ARE available, and the exact next step - the
        same shape the `ren` interface answers an unknown id with, so a
        host reads one answer whichever interface produced it.
        """
        from library.tools import capability_guidance, operation_map
        level = payload.get("support_level") or support_level(requested)
        if level == operation_map.SUPPORT_PRODUCTION:
            return None
        alternatives = domain_alternatives(requested)
        supported = production_enabled_ids()
        if level == operation_map.SUPPORT_CONTEXT:
            status = capability_guidance.NOT_PRODUCTION_ENABLED
            what = (f"{requested!r} is qualified only inside the context "
                    f"its evidence was earned in, so ordinary production "
                    f"routing does not reach it.")
            fix = ("use an operation that is production-enabled, or reach "
                   "for this one inside the context the operation map "
                   "names")
        elif level == operation_map.SUPPORT_IMPLEMENTED:
            status = capability_guidance.UNQUALIFIED
            what = (f"{requested!r} is implemented but carries no live "
                    f"qualification evidence, so it may not be routed.")
            fix = ("use an operation that is production-enabled, or record "
                   "the live qualification this one needs")
        else:
            status = capability_guidance.UNAVAILABLE
            what = (f"{requested!r} is not supported for any executor "
                    f"this build knows.")
            fix = ("use one of the operations listed as available instead, "
                   "or ask for the qualification that would enable this one")
        return capability_guidance.guidance(
            status, requested, what,
            "the registry distinguishes production enabled, context "
            "qualified, implemented and unsupported, and production "
            "routing fails closed",
            fix,
            next_action=(f"{TOOL} capabilities --search \"{requested}\""),
            alternatives=alternatives,
            supported=supported,
        )

    def unknown_guidance(requested: str) -> dict:
        """Guidance for an id the registry does not hold."""
        from library.tools import capability_guidance
        known = list(edit_algebra.OP_STATUS)
        nearest = capability_guidance.closest_ids(requested, known)
        alternatives = [{"id": name,
                         "summary": concise_purpose(
                             edit_algebra.ALGEBRA[name]),
                         "verdict": ("production_enabled"
                                     if edit_algebra.status(name).production_enabled
                                     else edit_algebra.status(name).qualification_status)}
                        for name in nearest]
        return capability_guidance.guidance(
            capability_guidance.UNKNOWN, requested,
            f"{requested!r} is not an operation this build knows.",
            "the operation vocabulary is the edit algebra's ids; the "
            "nearest ids are listed below",
            "ask for one of the nearest ids instead, or search the "
            "registry for the effect you want",
            next_action=f"{TOOL} capabilities --search \"{requested}\"",
            alternatives=alternatives,
            supported=production_enabled_ids(),
        )

    if query:
        stop_words = {"a", "an", "and", "existing", "for", "in",
                      "of", "the", "to", "with"}
        terms = [term.casefold() for term in query.split()
                 if term.casefold() not in stop_words]
        rows = []
        for op in edit_algebra.ops():
            status = edit_algebra.status(op.id)
            haystack = search_text(op)
            matches = [term in haystack for term in terms]
            score = sum(matches)
            if score and all(matches):
                rows.append((score, op.id, status, op))
        command_rows = operation_map.command_control_rows()
        command_matches = []
        for control_name, control in command_rows.items():
            haystack = (control_name + " " + control["control_kind"]
                        + " " + str(control["command"] or "")).casefold()
            matches = [term in haystack for term in terms]
            score = sum(matches)
            if score and all(matches):
                command_matches.append((score, control_name, control))
        if not rows:
            for op in edit_algebra.ops():
                status = edit_algebra.status(op.id)
                score = sum(term in search_text(op) for term in terms)
                if score:
                    rows.append((score, op.id, status, op))
        if not rows and not command_matches:
            for control_name, control in command_rows.items():
                haystack = (control_name + " " + control["control_kind"]
                            + " " + str(control["command"] or "")).casefold()
                score = sum(term in haystack for term in terms)
                if score:
                    command_matches.append((score, control_name, control))
        rows.sort(key=lambda row: (-row[0], row[1]))
        result_rows = [{
            "id": op.id,
            "purpose": concise_purpose(op),
            "implemented": status.compilable,
            "production_enabled": status.production_enabled,
        } for _, _, status, op in rows[:10]]
        result_rows.extend({
            "id": f"axi-command:{name}",
            "purpose": f"Command-only AXI control: {control['control_kind']}",
            "type": "command_only",
            "command": control["command"],
            "implemented": control["command_available"],
            "production_enabled": False,
        } for _, name, control in sorted(
            command_matches, key=lambda row: (-row[0], row[1]))[:10])
        return render({"query": query, "matches": result_rows})

    if getattr(args, "references", False):
        # The narrow read: one operation's rows out of 141 KB of flat
        # tables. The rows are the SAME ones the generated files carry,
        # read off the registry, so the slice cannot drift from it.
        if not requested:
            return fail("no operation was named",
                        f'{TOOL} capabilities <operation> --references')
        from library.tools import operations
        try:
            sections = operations.reference_sections(requested)
        except KeyError as exc:
            return fail(str(exc),
                        f'{TOOL} capabilities --search "{requested}"')
        if as_json:
            return render({"id": requested, "references": sections})
        parts = [f"# {requested} - capability reference rows", "",
                 "This is the slice of the six generated references that "
                 "concerns this operation. Read the whole file only when "
                 "the task needs more than one operation.", ""]
        for name, body in sections.items():
            if not body:
                continue
            parts.append(f"## {name}")
            parts.append(body.rstrip())
        parts.append("")
        sys.stdout.write("\n".join(parts) + "\n")
        return 0

    if not requested:
        rows = [{
            "id": op.id,
            "purpose": concise_purpose(op),
            "implemented": edit_algebra.status(op.id).compilable,
            "production_enabled": edit_algebra.status(op.id).production_enabled,
        } for op in edit_algebra.ops()]
        rows.extend({
            "id": f"axi:{name}", "purpose": "AXI command",
            "implemented": name == "timeline.delete" or (bool(ids) and all(
                edit_algebra.status(op_id).compilable for op_id in ids)),
            "production_enabled": name == "timeline.delete" or (bool(ids) and all(
                edit_algebra.status(op_id).production_enabled for op_id in ids)),
        } for name, ids in edit_algebra.AXI_OPERATION_MAP.items())
        rows.extend({
            "id": f"axi-command:{name}",
            "purpose": f"Command-only AXI control: {control['control_kind']}",
            "type": "command_only",
            "command": control["command"],
            "implemented": control["command_available"],
            "production_enabled": False,
        } for name, control in operation_map.command_control_rows().items())
        rows.sort(key=lambda row: row["id"])
        return render({"operations": rows})

    if requested == "timeline.delete":
        return render({
            "id": requested, "documented": True,
            "available": operation_map.command_control_rows()[requested][
                "command_available"],
            "implemented": operation_map.command_control_rows()[requested][
                "command_available"],
            "live_qualified": False,
            "production_enabled": False,
            "support_level": "command_only",
            "supported_backend": ["resolve_axi"],
            "type": "command_only",
            "algebra_operations": [],
            "command": operation_map.command_control_rows()[requested]["command"],
            "requires": ["recorded lifecycle ownership", "disposable state",
                         "exact project and timeline IDs", "exclusive Resolve lease",
                         "verified plan and post-delete census"],
            "limitations": [
                ("Deletes only job-owned qualification timelines whose final "
                 "content fingerprint still matches. Human-created or changed "
                 "timelines are refused.")],
        })
    if requested.startswith("native_mcp."):
        name = requested.removeprefix("native_mcp.")
        result = native_capabilities()["delegated_tools"].get(name)
        if result is None:
            return fail(f"unknown native MCP capability {requested!r}",
                        f"{TOOL} capabilities")
        payload = {"id": requested, **result}
    elif requested in operation_map.command_control_rows():
        payload = dict(operation_map.command_control_rows()[requested])
        payload.update({
            "documented": True,
            "available": payload["command_available"],
            "implemented": payload["command_available"],
            "live_qualified": False,
            "production_enabled": False,
            "support_level": "command_only",
        })
    elif requested in edit_algebra.AXI_OPERATION_MAP:
        ids = edit_algebra.AXI_OPERATION_MAP[requested]
        payload = {
            "id": requested,
            "documented": True,
            "algebra_operations": [edit_algebra.capability(op_id)
                                   for op_id in ids],
            "available": bool(ids) and all(
                edit_algebra.status(op_id).compilable for op_id in ids),
            "implemented": bool(ids) and all(
                edit_algebra.status(op_id).compilable for op_id in ids),
            "live_qualified": bool(ids) and all(
                edit_algebra.status(op_id).qualified for op_id in ids),
            "production_enabled": bool(ids) and all(
                edit_algebra.status(op_id).production_enabled for op_id in ids),
            "supported_backend": sorted({
                backend.backend
                for op_id in ids
                for backend in edit_algebra.status(op_id).backends}),
            "preserves_fusion": {
                "value": None,
                "conditions": [],
                "qualification_evidence": [],
            },
            "limitations": list(edit_algebra.AXI_OPERATION_LIMITATIONS.get(
                requested, (() if ids else (
                    "AXI command has no equivalent edit-algebra operation declared",)))),
        }
    elif requested:
        payload = edit_algebra.capability(requested)
        if payload is None:
            guidance = unknown_guidance(requested)
            return render({"id": requested, "documented": False,
                           "available": False, "implemented": False,
                           "live_qualified": False,
                           "production_enabled": False,
                           "support_level": "unsupported",
                           "guidance": guidance}, code=1)
    if requested in edit_algebra.OP_STATUS:
        payload = operation_detail(requested)
    elif requested in operation_map.command_control_rows():
        payload["support_level"] = "command_only"
        payload["guidance"] = None
        return render(payload)
    elif requested in edit_algebra.AXI_OPERATION_MAP:
        op_ids = edit_algebra.AXI_OPERATION_MAP[requested]
        if len(op_ids) == 1:
            payload = operation_detail(op_ids[0])
            payload["id"] = requested
            payload["algebra_operations"] = list(op_ids)
    # The ONE support band, from the canonical operation map, so this
    # answer and the map never disagree about what an operation is.
    payload["support_level"] = support_level(requested)
    payload["guidance"] = guidance_for(payload, requested)
    return render(payload)


def _capabilities_toon(payload: dict) -> str:
    """Render compact discovery tables without embedding the whole registry."""
    if "operations" in payload:
        rows = payload["operations"]
        return table("operations", rows,
                     ["id", "purpose", "implemented", "production_enabled"])
    if "matches" in payload:
        return (kv_block("search", {"query": payload["query"]}) + "\n"
                + table("matches", payload["matches"],
                        ["id", "purpose", "implemented", "production_enabled"]))
    parts = [kv_block("capability", {
        "id": payload.get("id"), "purpose": payload.get("purpose", ""),
        "implemented": payload.get("implemented", False),
        "live_qualified": payload.get("live_qualified", False),
        "production_enabled": payload.get("production_enabled", False),
    })]
    if "schema" in payload:
        parts.append(table("inputs", [{"schema": json.dumps(
            payload["schema"], sort_keys=True, separators=(",", ":"))}], ["schema"]))
        parts.append(kv_block("prerequisites", {
            "resolve_capabilities": ", ".join(payload["prerequisites"]["resolve_capabilities"]),
            "media_types": ", ".join(payload["prerequisites"]["media_types"]),
            "timeline_conditions": "; ".join(payload["prerequisites"]["timeline_conditions"]),
        }))
        parts.append(table("backends", payload.get("backends", []),
                           ["id", "implemented", "live_qualified",
                            "production_enabled", "eligible", "reason"]))
        parts.append(kv_block("verification", {
            "expected_effect": payload.get("expected_effect", ""),
            "contract": payload.get("verification", ""),
            "recovery_limits": json.dumps(payload["recovery_limits"],
                                           sort_keys=True, separators=(",", ":")),
            "qualification_evidence": ", ".join(payload.get("qualification_evidence", [])) or "none",
            "measured_performance": json.dumps(payload.get("measured_cost"),
                                                sort_keys=True, separators=(",", ":")),
        }))
    else:
        details = {key: value for key, value in payload.items()
                   if key != "guidance"}
        parts.append(kv_block("details", details))
    guidance = payload.get("guidance")
    if guidance:
        alternatives = ", ".join(str(row.get("id"))
                                 for row in guidance.get("alternatives", []))
        parts.append(kv_block("guidance", {
            "status": guidance.get("status", ""),
            "what": guidance.get("what", ""),
            "why": guidance.get("why", ""),
            "fix": guidance.get("fix", ""),
            "available_instead": alternatives or "none",
            "next_action": guidance.get("next_action", ""),
        }))
    return "\n".join(parts)


_SHADOW_READ_COMMANDS = frozenset({
    "cmd_timeline_list", "cmd_timeline_get", "cmd_markers",
    "cmd_items", "cmd_fusion", "cmd_captions", "cmd_frames",
    "cmd_target",
})

#: Commands that read Resolve under their own lease but write only local
#: state, so they run locally - never through the broker. The broker
#: coalesces reads across agents; a local-ledger write does not belong in
#: that queue.
_NO_BROKER_COMMANDS = frozenset({
    "cmd_ownership_adopt",
})


def _dispatch(func, args) -> int:
    """Run Resolve-backed commands inside their lease, before connection.

    Command bodies keep their narrower leases around operations. The outer
    hold closes the earlier connection-handshake gap; `resolve_lease` is
    reentrant for same-mode nesting, and write flags select the same mode
    as the operation's existing inner hold. Recorded structural reads run
    locally and take neither a broker job nor a Resolve lease.
    """
    name = getattr(func, "__name__", "")
    if name in _LOCAL_COMMANDS:
        return func(args)
    if (name in _SHADOW_READ_COMMANDS
            and not getattr(args, "refresh_live", False)):
        return func(args)
    exclusive = bool(getattr(args, "unsafe", False)
                     or getattr(args, "apply", False)
                     or (name == "cmd_run" and bool(
                         getattr(args, "purpose", "")
                         or getattr(args, "workflow_name", ""))))
    with _lease(exclusive=exclusive):
        return func(args)


def _project(resolve, project_name: str):
    """The open project, optionally checked against an expected name."""
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    if project is None:
        raise AxiError("Resolve has no project open - open one and re-run.",
                       f"{TOOL} cursor")
    if project_name and project.GetName() != project_name:
        raise AxiError(
            f"the open project is {project.GetName()!r}, not "
            f"{project_name!r} - exact names only, refusing rather than "
            f"reading the wrong project.",
            f"{TOOL} cursor")
    return project


def _timeline_names(project) -> list:
    names = []
    for index in range(1, (project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is None:
            continue
        names.append(timeline.GetName())
    return names


def _target_timeline(project, name, resolve=None):
    """The named timeline, or the open one when no name is given.

    `resolve` is the caller's held connection: the unnamed case reads
    the open timeline through it instead of opening a second scripting
    client for one read (the multi-client shape behind the 2026-10-06
    crash). Callers that do not pass one keep today's connect.

    Returns `(timeline, is_current, note)`. Exact names win; a prefix
    matching exactly ONE timeline resolves to it (with a `note` saying
    so, so the caller learns the full name); a prefix matching several
    is refused loudly - that refusal is what makes incident 2's
    `startswith("Reel 16")` trap impossible.

    `timeline_named`'s own refusal lists every timeline on the project
    (~2 KB on the error path, the token cost this tool removes), so a
    miss is rephrased compactly: the colliding candidates when that is
    what it is, near matches otherwise, plus the one command that
    lists names by choice.
    """
    from library.tools.marker_feedback import current_timeline
    from library.tools.timeline_ingest import (
        TimelineIngestError,
        timeline_named,
    )
    if not name:
        timeline, _ = current_timeline(resolve)
        if timeline.GetName() not in _timeline_names(project):
            raise AxiError(
                "the open timeline is not on the open project - "
                "re-open it and re-run.",
                f"{TOOL} cursor")
        return timeline, True, ""
    try:
        return timeline_named(project, name), False, ""
    except TimelineIngestError:
        listed = _timeline_names(project)
        exact = []
        for index in range(1, (project.GetTimelineCount() or 0) + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline is not None and timeline.GetName() == name:
                exact.append(timeline)
        if len(exact) > 1:
            candidates = []
            for timeline in exact:
                try:
                    candidates.append(str(timeline.GetUniqueId()))
                except Exception:  # noqa: BLE001 - keep refusal actionable
                    candidates.append("(id unavailable)")
            raise AxiError(
                f"timeline name {name!r} is duplicated on project "
                f"{project.GetName()!r}: {candidates}; pass its timeline id.",
                f"{TOOL} timeline list") from None
        hits = [other for other in listed if other.startswith(name)]
        if len(hits) == 1:
            return (timeline_named(project, hits[0]), False,
                    f"resolved: {name!r} is a unique prefix of "
                    f"{hits[0]!r}")
        if hits:
            # The case that bites is a (rebuild staging) sibling: the
            # caller asked for the reel and the unpromoted rebuild
            # answered the prefix. Name the cause, not just the
            # collision, so the next action is obvious.
            staged = [h for h in hits
                      if h.endswith(_staging_suffix())]
            cause = (f" One of them is an unpromoted (rebuild staging) "
                     f"sibling - promote or discard it, or pass the "
                     f"full name." if staged else "")
            detail = (f"no timeline named exactly {name!r}; it is a "
                      f"prefix of {len(hits)} timelines: {hits} - pass "
                      f"the full name.{cause}")
        else:
            near = [other for other in listed if name.lower() in
                    other.lower()][:5]
            detail = (f"no timeline named exactly {name!r}."
                      + (f" Resembling: {near}." if near else ""))
        raise AxiError(detail,
                       f"{TOOL} timeline list") from None


def _staging_suffix() -> str:
    from library.tools.resolve_bin_layout import STAGING_TIMELINE_SUFFIX
    return STAGING_TIMELINE_SUFFIX


def _frames_of(timeline) -> tuple:
    try:
        start = int(timeline.GetStartFrame())
        end = int(timeline.GetEndFrame())
    except (TypeError, ValueError) as exc:
        raise AxiError(
            f"timeline {timeline.GetName()!r} would not report its "
            f"start/end frames ({exc}) - refusing rather than "
            f"reporting half a row.",
            f"{TOOL} timeline list") from exc
    return start, end, end - start + 1


def _timeline_marker_summary(timeline) -> tuple:
    """(count, sorted colours) off the timeline plane only - the cheap half."""
    try:
        markers = timeline.GetMarkers() or {}
    except Exception as exc:
        raise AxiError(
            f"timeline {timeline.GetName()!r} would not report its "
            f"markers ({exc}).",
            f"{TOOL} timeline list") from exc
    colours = sorted({(m.get("color") or "") for m in markers.values()
                      if isinstance(m, dict)} - {""})
    return len(markers), colours


# ── Commands ─────────────────────────────────────────────────────────


def cmd_home(_args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        emit([f"bin: {_bin_path()}",
              f"description: {DESCRIPTION}",
              f"error: {exc}",
              help_block([f"{TOOL} --help"])])
        return 1
    with _lease(exclusive=False):
        project = _project(resolve, "")
        from library.tools.marker_feedback import current_timeline
        try:
            open_timeline, _ = current_timeline(resolve)
            cursor = open_timeline.GetName()
        except Exception:
            cursor = "(none open)"
        from library.tools import timeline_read
        try:
            views = timeline_read.list_timelines(
                project.GetName(), caller="resolve_axi.home")
            names = [view.generation.timeline_name for view in views]
            timeline_count = len(names)
            timeline_freshness = "latest stored generations; later live " \
                "edits may be absent"
        except timeline_read.ShadowError:
            names = []
            timeline_count = "unknown (none recorded)"
            timeline_freshness = "no recorded generation"
        suffix = _staging_suffix()
        staged = [n for n in names if n.endswith(suffix)]
    emit([f"bin: {_bin_path()}",
          f"description: {DESCRIPTION}",
          kv_block("resolve", {
              "project": project.GetName(),
              "cursor": cursor,
              "timelines": timeline_count,
              "timeline_source": "recorded generations",
              "timeline_freshness": timeline_freshness,
              "staging": len(staged),
          }),
          table("staging", [{"timeline": n} for n in staged],
                ["timeline"]) if staged else "staging: 0 unpromoted siblings",
          help_block([f"{TOOL} timeline list",
                      f"{TOOL} timeline get \"<exact-name>\"",
                      f"{TOOL} markers --timeline \"<exact-name>\""])])
    return 0


def _bin_path() -> str:
    path = os.path.abspath(sys.argv[0] or TOOL)
    home = os.path.expanduser("~")
    return f"~{path[len(home):]}" if path.startswith(home) else path


def cmd_timeline_list(args) -> int:
    from library.tools import timeline_read
    try:
        views = timeline_read.list_timelines(
            args.project or None, caller="resolve_axi.timeline.list")
    except timeline_read.ShadowError as exc:
        return fail(str(exc),
                    "ren resolved submit timeline.snapshot "
                    "'{\"project\":\"<project>\","
                    "\"timeline\":\"<exact timeline>\"}'")
    suffix = _staging_suffix()
    names_by_project = {}
    for view in views:
        names_by_project.setdefault(view.generation.project, set()).add(
            view.generation.timeline_name)
    rows = []
    for view in views:
        name = view.generation.timeline_name
        snapshot = view.snapshot
        start = int(snapshot["start_frame"])
        end = int(snapshot["end_frame"])
        frames = end - start + 1
        timeline_markers = snapshot["markers"]["timeline"]
        count = len(timeline_markers)
        colours = sorted({(marker["color"] or "")
                          for marker in timeline_markers} - {""})
        base = (name[:-len(suffix)] if name.endswith(suffix) else None)
        rows.append({
            "project": view.generation.project,
            "timeline_id": view.generation.timeline_id,
            "name": name,
            "frames": frames,
            "markers": count,
            "colors": ",".join(colours),
            "staging": "yes" if base is not None else "no",
            "promotion_pending": ("yes" if base in
                                  names_by_project[view.generation.project]
                                  else "no") if base is not None else "no",
            "generation": view.generation.generation,
            "verified_at": datetime.fromtimestamp(
                view.generation.verified_at, timezone.utc
            ).isoformat(timespec="seconds"),
        })
    emit([kv_block("timelines", {
                               "project_filter": args.project or "all",
                               "timelines": len(rows),
                               "source": "recorded generations",
                               "freshness": "latest stored generation; "
                               "later live edits may be absent"}),
          table("timelines", rows,
                ["project", "timeline_id", "name", "frames", "markers",
                 "colors",
                 "staging", "promotion_pending", "generation",
                 "verified_at"]),
          help_block([f"{TOOL} timeline get \"<exact-name>\"",
                      f"{TOOL} markers --timeline \"<exact-name>\""])])
    return 0


def cmd_timeline_get(args) -> int:
    scope_error = _resolve_reel_scope(args, "timeline get")
    if scope_error is not None:
        return fail(*scope_error)
    if not args.name:
        return fail("timeline get needs a timeline name or --reel N.",
                    f"{TOOL} timeline get \"<exact-name>\" or {TOOL} "
                    f"timeline get --reel 7 --project-folder <project>")
    try:
        view, note = _timeline_view(
            args.name, args.project, caller="resolve_axi.timeline.get",
            refresh_live=getattr(args, "refresh_live", False),
            exact=bool(getattr(args, "_reel_exact", False)))
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    snapshot = view.snapshot
    timeline_name = view.generation.timeline_name
    start = int(snapshot["start_frame"])
    end = int(snapshot["end_frame"])
    frames = end - start + 1
    fps = snapshot["reported_fps"]
    notes = snapshot["markers"]["notes"]
    planes: dict = {}
    colours: dict = {}
    for entry in notes:
        planes[entry["source"]] = planes.get(entry["source"], 0) + 1
        if entry["color"]:
            colours[entry["color"]] = colours.get(entry["color"], 0) + 1
    tracks = [{
        "track": f"{track['type']}{track['index']}",
        "name": track["name"],
        "clips": len(track["clips"]),
    } for track in snapshot["tracks"]]
    emit([kv_block("timeline", {
              "name": timeline_name,
              "start": start, "end": end, "frames": frames, "fps": fps,
              **_timeline_view_metadata(
                  view, getattr(args, "refresh_live", False)),
          }),
          note,
          table("tracks", tracks, ["track", "name", "clips"]),
          table("marker_planes",
                [{"plane": k, "notes": v} for k, v in sorted(planes.items())],
                ["plane", "notes"]),
          table("marker_colors",
                [{"color": k, "notes": v} for k, v in sorted(colours.items())],
                ["color", "notes"]) if colours else "marker_colors: 0 colors",
          help_block([f"{TOOL} markers --timeline \"{timeline_name}\"",
                      f"{TOOL} frames --timeline \"{timeline_name}\""])])
    return 0


def _timeline_view(timeline_name: str, project_name: str = "", *, caller: str,
                   refresh_live: bool = False, exact: bool = False,
                   resolve=None):
    """Read one latest generation, or explicitly refresh the current reel.

    The name and snapshot are resolved by `timeline_read`; only a live
    refresh consults Resolve's current-timeline handle. It never switches
    the cursor to satisfy a query. A reel number already resolved to its
    recorded exact name passes `exact=True`, so the lookup never falls
    back to a prefix match (AGENTS.md 5).

    `resolve` is the caller's held connection: a live refresh reads the
    open timeline through it instead of opening a second scripting client
    beside the one the caller already holds.
    """
    from library.tools import timeline_read
    from library.tools.marker_feedback import current_timeline

    if refresh_live:
        resolve = resolve if resolve is not None else _connect()
        project = _project(resolve, project_name)
        current, _ = current_timeline(resolve)
        if not timeline_name:
            timeline_name = current.GetName()
        if current.GetName() != timeline_name:
            raise AxiError(
                f"live refresh requires the exact timeline to be current; "
                f"Resolve has {current.GetName()!r}, not {timeline_name!r}.",
                f"open {timeline_name!r}, then run {TOOL} timeline get "
                f"\"{timeline_name}\" --refresh-live")
        return timeline_read.refresh(project, current), ""
    if not timeline_name:
        raise AxiError(
            "a recorded read needs --timeline; only --refresh-live can use "
            "the current Resolve timeline.",
            f"{TOOL} markers --timeline \"<exact-name>\"")
    try:
        if project_name:
            view = timeline_read.read(
                project_name, timeline_name, allow_prefix=not exact,
                caller=caller)
        else:
            view = timeline_read.read_any(
                timeline_name, allow_prefix=not exact, caller=caller)
    except timeline_read.ShadowError as exc:
        raise AxiError(
            str(exc),
            f"{TOOL} timeline list, or refresh with {TOOL} timeline get "
            "\"<exact-name>\" --refresh-live") from exc
    note = (f"resolved: {timeline_name!r} is a unique recorded prefix of "
            f"{view.generation.timeline_name}"
            if (timeline_name != view.generation.timeline_name
                and view.generation.timeline_name.startswith(timeline_name))
            else "")
    return view, note


def _timeline_view_metadata(view, refresh_live: bool) -> dict:
    """Provenance every structural query reports beside its snapshot."""
    return {
        "project": view.generation.project,
        "timeline_id": view.generation.timeline_id,
        "generation": view.generation.generation,
        "verified_at": datetime.fromtimestamp(
            view.generation.verified_at, timezone.utc
        ).isoformat(timespec="seconds"),
        "source": ("live refresh" if refresh_live else
                   "recorded generation"),
        "freshness": ("captured during this read" if refresh_live else
                      "latest stored generation; later live edits may be "
                      "absent"),
    }


def _note_rows(notes, full: bool, plane: str) -> list:
    rows = []
    for note in notes:
        values = note if isinstance(note, dict) else vars(note)
        source = values["source"]
        if plane and source != plane:
            continue
        text = values.get("note", "") or values.get("text", "")
        frame = values.get("frame")
        rows.append({
            "frame": frame if frame is not None else "",
            "timecode": values.get("timecode", "") or "",
            "color": values.get("color", "") or "",
            "plane": source,
            "name": values.get("name", "") or "",
            "note": preview(text, full),
        })
    return rows


def cmd_markers(args) -> int:
    scope_error = _resolve_reel_scope(args, "markers")
    if scope_error is not None:
        return fail(*scope_error)
    if args.plane and args.plane not in ("timeline_marker", "clip_marker",
                                         "media_pool_marker",
                                         "clip_comment"):
        return fail(f"unknown plane {args.plane!r}.",
                    f"{TOOL} markers --timeline \"{args.timeline or '<name>'}\""
                    f" [--plane timeline_marker|clip_marker|"
                    f"media_pool_marker|clip_comment]")
    try:
        view, note = _timeline_view(
            args.timeline, args.project, caller="resolve_axi.markers",
            refresh_live=getattr(args, "refresh_live", False),
            exact=bool(getattr(args, "_reel_exact", False)))
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    timeline_name = view.generation.timeline_name
    notes = view.snapshot["markers"]["notes"]
    rows = _note_rows(notes, args.full, args.plane or "")
    truncated = sum(1 for r in rows if "(truncated," in r["note"])
    emit([kv_block("markers", {
              "timeline": timeline_name,
              "notes": len(rows),
              "truncated": truncated,
              **_timeline_view_metadata(
                  view, getattr(args, "refresh_live", False)),
          }),
          note,
          table("notes", rows,
                ["frame", "timecode", "color", "plane", "name", "note"]),
          help_block([f"{TOOL} markers --timeline \"{timeline_name}\""
                      f" --full"]) if truncated and not args.full else ""])
    return 0


def _snapshot_payload(project_name: str, timeline_name: str,
                       notes) -> dict:
    payload_notes = []
    for note in notes:
        raw_key = note.frame_in_timeline_space
        payload_notes.append({
            "source": note.source,
            "frame": note.frame,
            "frame_in_timeline_space": raw_key,
            "timeline_frame": note.frame,
            "source_frame": (raw_key if note.source in
                             ("clip_marker", "media_pool_marker")
                             else None),
            "timecode": note.timecode,
            "color": note.color,
            "name": note.name,
            "note": note.note,
            "duration_frames": note.duration_frames,
            "custom_data_raw": note.custom_data_raw,
        })
    return {
        "tool": TOOL,
        "tool_version": VERSION,
        "scope": RESTORE_SCOPE,
        "project": project_name,
        "timeline": timeline_name,
        "read_at": datetime.now(timezone.utc).isoformat(),
        "deprecated_fields": {
            DEPRECATED_FRAME_FIELD: DEPRECATED_FRAME_NOTE,
        },
        "notes": payload_notes,
    }


def cmd_markers_snapshot(args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, _, note = _target_timeline(project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            notes = _read_notes(
                timeline, f"{TOOL} markers --timeline "
                f"\"{args.timeline or '<name>'}\"")
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        payload = _snapshot_payload(project.GetName(), timeline.GetName(),
                                    notes)
    try:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True,
                      default=str)
            handle.write("\n")
    except OSError as exc:
        return fail(f"cannot write {args.out!r} ({exc}).",
                    f"{TOOL} markers snapshot --timeline "
                    f"\"{timeline.GetName()}\" --out <path>")
    scoped = [n for n in payload["notes"] if n["source"] == "timeline_marker"]
    emit([kv_block("snapshot", {
              "timeline": timeline.GetName(),
              "notes": len(payload["notes"]),
              "restorable": len(scoped),
              "out": args.out,
          }),
          note,
          f"deprecated: {DEPRECATED_FRAME_NOTE}",
          help_block([f"{TOOL} markers restore --in {args.out} "
                      f"--timeline \"{timeline.GetName()}\""])])
    return 0


def _marker_add_operation(row: dict) -> dict:
    """One snapshot note as the algebra op that restores it."""
    return {
        "op": "marker.add",
        "frame": int(row["frame_in_timeline_space"]),
        "color": row.get("color") or "",
        "name": row.get("name") or "",
        "note": row.get("note") or "",
        "duration": int(row.get("duration_frames") or 1),
        "custom_data": row.get("custom_data_raw") or "",
    }


def _restore_missing_markers(resolve, project, timeline, missing, *,
                             route_axi_dispatch, apply_live_patch):
    """Restore every missing marker, in ONE transaction where it can.

    A restore used to commit one EditPatch per marker: N transactions,
    N live reads, N read-backs and N recorded generations for one
    restore of one timeline's own words. The markers are compatible by
    construction - each is a `marker.add` at its own frame - so they
    belong in ONE transaction with one precondition per frame.

    The group is refused as a whole when one frame's precondition does
    not hold. Then, and only then, the per-marker path runs: it is the
    path that restores the other markers when one frame is contested.
    It runs only when the group wrote NOTHING, which is read back from
    the timeline rather than assumed - a group that wrote some markers
    is reported as refused, never re-run on top of its own writes.
    """
    refused: list = []
    rows: list = []
    operations: list = []
    for row in missing:
        try:
            operations.append(_marker_add_operation(row))
        except (KeyError, TypeError, ValueError) as exc:
            refused.append((row, f"the snapshot row names no usable "
                                 f"frame: {exc}"))
            continue
        rows.append(row)
    if not operations:
        return 0, refused
    before = _live_marker_keys(timeline)
    routing = None
    try:
        # VC-09: AXI dispatches share the routing contract - a refusal
        # lands in the refused list below, never a write.
        routing = route_axi_dispatch(
            operations,
            context=_qualification_context(
                resolve, project, timeline, operations))
        receipt = apply_live_patch(
            project=project, timeline=timeline,
            capability="reel.touchup",
            operations=operations,
            conflict_domains=("markers",),
            idempotency_key=f"markers.restore:{uuid.uuid4().hex}",
            preconditions=[{"kind": "marker_absent",
                            "frame": operation["frame"]}
                           for operation in operations],
            postconditions=[{"kind": "duration_unchanged"}])
    except Exception as exc:
        if _live_marker_keys(timeline) != before:
            return 0, refused + [
                (row, f"the restore wrote before it was refused: {exc}")
                for row in rows]
        restored, per_row = _restore_markers_one_by_one(
            resolve, project, timeline, rows,
            route_axi_dispatch=route_axi_dispatch,
            apply_live_patch=apply_live_patch)
        return restored, refused + per_row
    if receipt.get("status") == "committed":
        receipt["execution_routing"] = routing
        return len(rows), refused
    if _live_marker_keys(timeline) != before:
        return 0, refused + [
            (row, f"the restore wrote before it was refused: {receipt}")
            for row in rows]
    restored, per_row = _restore_markers_one_by_one(
        resolve, project, timeline, rows,
        route_axi_dispatch=route_axi_dispatch,
        apply_live_patch=apply_live_patch)
    return restored, refused + per_row


def _restore_markers_one_by_one(resolve, project, timeline, rows, *,
                                 route_axi_dispatch, apply_live_patch):
    """The per-marker restore: one transaction each, as before.

    This is the fallback for a group that was refused before it wrote
    anything. One contested frame must not cost the captain the other
    markers, and each marker still lands in its own verified
    transaction.
    """
    restored = 0
    refused: list = []
    for row in rows:
        try:
            operations = [_marker_add_operation(row)]
            routing = route_axi_dispatch(
                operations,
                context=_qualification_context(
                    resolve, project, timeline, operations))
            receipt = apply_live_patch(
                project=project, timeline=timeline,
                capability="reel.touchup",
                operations=operations,
                conflict_domains=("markers",),
                idempotency_key=f"markers.restore:{uuid.uuid4().hex}",
                preconditions=[{"kind": "marker_absent",
                                "frame": operations[0]["frame"]}],
                postconditions=[{"kind": "duration_unchanged"}])
        except Exception as exc:
            refused.append((row, str(exc)))
            continue
        receipt["execution_routing"] = routing
        if receipt["status"] == "committed":
            restored += 1
        else:
            refused.append((row, str(receipt)))
    return restored, refused


def _live_marker_keys(timeline) -> set:
    """The live timeline's marker identities: what a write would change."""
    live = timeline.GetMarkers() or {}
    return {(key, (marker.get("color") or ""),
             (marker.get("name") or ""), (marker.get("note") or ""),
             int(marker.get("duration") or 1),
             (marker.get("customData") or ""))
            for key, marker in live.items() if isinstance(marker, dict)}


def _skipped_planes(notes: list) -> dict:
    """Count of snapshot notes restore cannot write, by plane.

    Anything outside the timeline plane is unrestorable (see
    RESTORE_SCOPE): the check is `!= "timeline_marker"`, never a
    membership list, so a future plane fails closed.
    """
    counts: dict = {}
    for row in notes or []:
        if (row.get("source") or "") != "timeline_marker":
            plane = row.get("source") or "(unknown plane)"
            counts[plane] = counts.get(plane, 0) + 1
    return counts


def _refuse_partial_snapshot(in_file: str, timeline_name, skipped: dict,
                             apply: bool) -> int:
    """Refuse a snapshot holding notes restore cannot write.

    The refusal IS the safety: "snapshot" reads as a backup, and a
    caller using it as a pre-rebuild safety net must learn before the
    rebuild - not after - that the clip half is not in this file. It
    names how many notes and in which plane, prints where the clip
    half actually lives, and teaches the acknowledgement flag, the
    way the ambiguous-prefix refusal teaches the full name.
    """
    total = sum(skipped.values())
    planes = ", ".join(f"{plane}: {count}"
                       for plane, count in sorted(skipped.items()))
    verb = "would leave" if not apply else "leaves"
    emit([f"error: snapshot {in_file!r} holds {total} note(s) restore "
          f"cannot write ({planes}) - restore covers the timeline "
          f"plane ({RESTORE_SCOPE}) only, so restoring it "
          f"{verb} the clip half behind",
          f"recovery: re-apply the clip-plane note(s) by hand from the "
          f"snapshot file - each skipped row carries its plane, frame, "
          f"name and words",
          f"help: {TOOL} markers restore --in {in_file} --timeline "
          f"\"{timeline_name}\""
          f"{' --apply' if apply else ''} --allow-partial to proceed "
          f"with the timeline plane only"])
    return 1


def _snapshot_key(row: dict):
    return (row.get("frame_in_timeline_space"), row.get("color") or "",
            row.get("name") or "", row.get("note") or "",
            row.get("duration_frames") or 0,
            row.get("custom_data_raw") or "")


def cmd_markers_restore(args) -> int:
    try:
        with open(args.in_file, encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, ValueError) as exc:
        return fail(f"cannot read snapshot {args.in_file!r} ({exc}).",
                    f"{TOOL} markers snapshot --timeline \"<name>\" "
                    f"--out {args.in_file}")
    if not isinstance(snapshot, dict) or snapshot.get("tool") != TOOL:
        return fail(f"{args.in_file!r} is not a {TOOL} marker snapshot.",
                    f"{TOOL} markers snapshot --timeline \"<name>\" "
                    f"--out {args.in_file}")
    wanted_name = args.timeline or snapshot.get("timeline")
    skipped = _skipped_planes(snapshot.get("notes", []))
    if skipped and not args.allow_partial:
        # Before any Resolve contact: no lease, no cursor assertion,
        # no write. The file's content alone decides this.
        return _refuse_partial_snapshot(args.in_file, wanted_name,
                                        skipped, args.apply)
    wanted = [n for n in snapshot.get("notes", [])
              if n.get("source") == "timeline_marker"]
    skipped_rows = [n for n in snapshot.get("notes", [])
                    if (n.get("source") or "") != "timeline_marker"]
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, _, note = _target_timeline(project, wanted_name)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        if timeline.GetName() != snapshot.get("timeline"):
            return fail(
                f"snapshot is for {snapshot.get('timeline')!r} but the "
                f"target is {timeline.GetName()!r} - refusing to restore "
                f"one reel's words onto another.",
                f"{TOOL} markers restore --in {args.in_file} "
                f"--timeline \"{snapshot.get('timeline')}\"")
        from library.tools.marker_feedback import current_timeline
        open_timeline, _ = current_timeline(resolve)
        if open_timeline.GetName() != timeline.GetName():
            return fail(
                f"the cursor sits on {open_timeline.GetName()!r}, not "
                f"{timeline.GetName()!r} - a restore asserts the cursor "
                f"rather than depending on it. Open the reel and re-run.",
                f"{TOOL} cursor --expect \"{timeline.GetName()}\"")
        live = timeline.GetMarkers() or {}
        live_keys = {(key, (m.get("color") or ""),
                      (m.get("name") or ""), (m.get("note") or ""),
                      int(m.get("duration") or 1),
                      (m.get("customData") or ""))
                     for key, m in live.items() if isinstance(m, dict)}
        missing = [row for row in wanted
                   if _snapshot_key(row) not in live_keys]
        if not args.apply:
            parts = [kv_block("restore_plan", {
                      "timeline": timeline.GetName(),
                      "snapshot_notes": len(wanted),
                      "already_present": len(wanted) - len(missing),
                      "to_restore": len(missing),
                  }),
                  table("missing", [{
                      "frame": r.get("frame_in_timeline_space"),
                      "color": r.get("color") or "",
                      "name": r.get("name") or "",
                      "note": preview(r.get("note") or "", False),
                  } for r in missing],
                  ["frame", "color", "name", "note"])]
            if skipped_rows:
                parts.append(table("skipped", [{
                    "plane": r.get("source") or "",
                    "frame": r.get(TIMELINE_FRAME_FIELD),
                    "name": r.get("name") or "",
                    "note": preview(r.get("note") or "", False),
                } for r in skipped_rows],
                ["plane", "frame", "name", "note"]))
                parts.append(f"recovery: re-apply the skipped note(s) "
                             f"by hand from the snapshot file - each row "
                             f"above carries its plane, frame, name and "
                             f"words")
            parts.extend([
                  f"dry run - pass --apply to write under the Resolve lease",
                  help_block([f"{TOOL} markers restore --in {args.in_file} "
                              f"--timeline \"{timeline.GetName()}\" "
                              f"--apply"
                              f"{' --allow-partial' if skipped_rows else ''}"])])
            emit(parts)
            return 0
        from library.tools import edit_patch
        from library.tools.execution_router import route_axi_dispatch
        restored, refused = _restore_missing_markers(
            resolve, project, timeline, missing,
            route_axi_dispatch=route_axi_dispatch,
            apply_live_patch=edit_patch.apply_live_patch)
    summary = {
        "timeline": timeline.GetName(),
        "restored": restored,
        "refused": len(refused),
    }
    if skipped_rows:
        summary["skipped"] = len(skipped_rows)
    out = [kv_block("restore", summary), note]
    if refused:
        out.append(table("refused", [{
            "frame": r.get("frame_in_timeline_space"),
            "name": r.get("name") or "",
            "reason": reason,
        } for r, reason in refused], ["frame", "name", "reason"]))
    if skipped_rows:
        out.append(f"skipped: {len(skipped_rows)} clip-plane note(s) left "
                   f"behind by --allow-partial - re-apply them by hand "
                   f"from the snapshot file")
    out.append(help_block(
        [f"{TOOL} markers --timeline \"{timeline.GetName()}\""]))
    emit(out)
    return 0 if not refused else 1


def cmd_markers_reply(args) -> int:
    """Record our answer on a reel as a marker, linked to what it answers.

    The write half of the reply workflow `marker_feedback` owns:
    `place_reply_marker` (EditPatch + read-back verification) carries
    a `reply_custom_data` payload built with `reply_custom_data`, so
    the marker states mechanically that it is OURS and what it
    answers. Nothing here invents that path - this command exposes
    it the way `markers restore` exposes the snapshot path.

    `--frame` is ABSOLUTE (the space `markers` reports). `--color` is
    required: colour carries meaning and is never defaulted (AGENTS.md
    10.5). `--answers-frame` names the note being answered; its text
    is looked up live for the payload and shown in the dry run, so a
    reply cannot silently answer the wrong words. Default is a
    dry-run diff; `--apply` writes under the exclusive lease with the
    cursor asserted on the reel first.
    """
    from library.tools.marker_feedback import (
        MarkerWriteError,
        place_reply_marker,
        reply_custom_data,
    )
    if not args.name or not args.note or not args.color:
        return fail("a reply needs --name, --note and --color.",
                    f"{TOOL} markers reply --timeline \"<name>\" --frame "
                    f"<n> --color <c> --name \"<n>\" --note \"<text>\"")
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, _, note = _target_timeline(project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            notes = _read_notes(
                timeline, f"{TOOL} markers --timeline "
                f"\"{args.timeline or '<name>'}\"")
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        answered = [n for n in notes
                    if args.answers_frame is not None
                    and n.frame == args.answers_frame]
        answered.sort(key=lambda n: (n.source != "timeline_marker",))
        answers_text = answered[0].note if answered else ""
        # The pairing the reply carries: the answered note's durable
        # identity plus the picture it sat on (`marker_carry` re-pairs
        # by this after a rebuild, never by frame). `--answers` is an
        # explicit identity, validated, never prose: it must BE the
        # note's own identity when both are given.
        from library.tools.feedback_ledger import (
            durable_identity as _durable_identity,
            is_identity as _is_identity,
        )
        derived_answers, derived_anchor = "", None
        if answered:
            derived_answers = _durable_identity(
                timeline.GetName(), answered[0].text)
            # The SAME anchor `marker_carry` will read for this note:
            # the picture under its frame, not the frame number. A
            # mirror computed off the note's clip list would disagree
            # wherever an overlay row sits on top (carry anchors to
            # picture rows only), so this calls carry's own function.
            from library.tools import marker_carry as _carry
            try:
                start_frame = int(timeline.GetStartFrame())
            except (TypeError, ValueError):
                start_frame = None
            if answered[0].frame is not None and start_frame is not None:
                pictured = _carry.picture_at(
                    timeline, answered[0].frame - start_frame)
                if pictured is not None:
                    derived_anchor = {"source_file": pictured[0],
                                      "source_frame": pictured[1]}
        if args.answers and not _is_identity(args.answers):
            return fail(
                f"--answers {args.answers!r} is not a note identity - "
                f"prose never joins to a note and a frame is invalidated "
                f"by the next rebuild.",
                f"omit --answers and keep --answers-frame "
                f"{args.answers_frame} to take the note's own identity "
                f"({derived_answers or 'none - no note at that frame'}), "
                f"or pass that identity explicitly")
        if (args.answers and derived_answers
                and args.answers != derived_answers):
            return fail(
                f"--answers {args.answers!r} is not the note at frame "
                f"{args.answers_frame} ({derived_answers}) - a reply "
                f"cannot silently answer the wrong words.",
                f"omit --answers to take the note's own identity, or "
                f"check the frame with {TOOL} markers --timeline "
                f"\"{timeline.GetName()}\"")
        effective_answers = args.answers or derived_answers
        effective_anchor = derived_anchor if answered else None
        try:
            start = int(timeline.GetStartFrame())
            span = int(timeline.GetEndFrame()) - start
        except (TypeError, ValueError) as exc:
            return fail(f"timeline {timeline.GetName()!r} would not "
                        f"report its span ({exc}).",
                        f"{TOOL} timeline list")
        key = int(args.frame) - start
        live = timeline.GetMarkers() or {}
        problem = ("off the timeline "
                   f"(0..{span - 1} in timeline space)" if not
                   0 <= key < span else
                   ("occupied - one marker per frame" if key in live
                    else ""))
        if not args.apply:
            emit([kv_block("reply_plan", {
                      "timeline": timeline.GetName(),
                      "frame": args.frame,
                      "color": args.color,
                      "name": args.name,
                      "answers_frame": (args.answers_frame
                                        if args.answers_frame is not None
                                        else ""),
                      "answers": effective_answers or "(none - words only)",
                      "answers_anchor": (
                          f"{effective_anchor['source_file']}@"
                          f"{effective_anchor['source_frame']}"
                          if effective_anchor else "(no picture under "
                          "the note - pairs by words alone)"),
                      "answers_text": preview(answers_text, False),
                      "blocked": problem or "no",
                  }),
                  note,
                  f"dry run - pass --apply to write under the Resolve "
                  f"lease",
                  help_block([f"{TOOL} markers reply --timeline "
                              f"\"{timeline.GetName()}\" --frame "
                              f"{args.frame} --color \"{args.color}\" "
                              f"--name \"{args.name}\" --note "
                              f"\"{preview(args.note, False)}\" "
                              f"--apply"])])
            return 0
        if problem:
            return fail(f"reply at frame {args.frame} refused: {problem}.",
                        f"{TOOL} markers --timeline "
                        f"\"{timeline.GetName()}\"")
        from library.tools.marker_feedback import current_timeline
        open_timeline, _ = current_timeline(resolve)
        if open_timeline.GetName() != timeline.GetName():
            return fail(
                f"the cursor sits on {open_timeline.GetName()!r}, not "
                f"{timeline.GetName()!r} - a reply asserts the cursor "
                f"rather than depending on it. Open the reel and re-run.",
                f"{TOOL} cursor --expect \"{timeline.GetName()}\"")
        payload = reply_custom_data(
            "", effective_answers, answers_text, args.summary or "",
            effective_anchor)
        try:
            place_reply_marker(
                timeline, int(args.frame), args.color, args.name, args.note,
                int(args.duration or 1), payload, project=project,
                idempotency_key=f"markers.reply:{uuid.uuid4().hex}")
        except MarkerWriteError as exc:
            return fail(str(exc), f"{TOOL} markers --timeline "
                                  f"\"{timeline.GetName()}\"")
        except ValueError as exc:
            return fail(str(exc), f"{TOOL} markers --timeline "
                                  f"\"{timeline.GetName()}\"")
    emit([kv_block("reply", {
              "timeline": timeline.GetName(),
              "frame": args.frame,
              "placed": "yes (read back)",
          }),
          note,
          help_block(
              [f"{TOOL} markers --timeline \"{timeline.GetName()}\""])])
    return 0


def cmd_markers_audit_replies(args) -> int:
    """Report which replies still sit beside the notes they answer.

    Read-only: the sweep the replies-decay task asks for before
    anything is changed. Each green reply is bound to its note by
    identity (`marker_carry.audit_replies`); `paired-drifted` is the
    Reel 14 shape - the note moved on under a rebuild and the reply
    stayed. This command reports and writes nothing: re-pairing
    happens in the next promotion carry, or by a directed write that
    firstmate routes, never here.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        from library.tools import marker_carry as _carry
        from library.tools import marker_feedback
        try:
            notes = _carry.read_markers(timeline, timeline.GetName())
        except _carry.MarkerCarryUnreadable as exc:
            return fail(str(exc), f"{TOOL} markers --timeline "
                                  f"\"{args.timeline or '<name>'}\"")
        rows = _carry.audit_replies(notes, timeline.GetName())
        try:
            live = _read_notes(
                timeline, f"{TOOL} markers --timeline "
                f"\"{args.timeline or '<name>'}\"")
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        clip_replies = [
            n for n in live if n.source != "timeline_marker"
            and marker_feedback.reply_records_in(n.custom_data_raw)]
    table_rows = [{
        "frame": r["frame"],
        "color": r["color"] or "",
        "name": preview(r["name"] or "", args.full),
        "status": r["status"],
        "ask_frame": (r["ask_frame"] if r["ask_frame"] is not None
                      else ""),
        "distance": (r["distance"] if r["distance"] is not None else ""),
        "answers": preview(r["answers"] or "", args.full),
    } for r in rows]
    drifted = sum(1 for r in rows if r["status"] == "paired-drifted")
    emit([kv_block("reply_audit", {
              "timeline": timeline.GetName() +
              (" (current)" if is_current else ""),
              "replies": len(rows),
              "drifted": drifted,
              "clip_plane_replies": len(clip_replies),
          }),
          note,
          table("replies", table_rows,
                ["frame", "color", "name", "status", "ask_frame",
                 "distance", "answers"]),
          (f"note: {len(clip_replies)} replie(s) of ours sit on the "
           f"clip plane, which marker carry does not read - this table "
           f"covers the timeline plane only.")
          if clip_replies else "",
          help_block([f"{TOOL} markers --timeline "
                      f"\"{timeline.GetName()}\" --full"])])
    return 0


def cmd_items(args) -> int:
    scope_error = _resolve_reel_scope(args, "items")
    if scope_error is not None:
        return fail(*scope_error)
    try:
        view, note = _timeline_view(
            args.timeline, args.project, caller="resolve_axi.items",
            refresh_live=getattr(args, "refresh_live", False),
            exact=bool(getattr(args, "_reel_exact", False)))
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    timeline_name = view.generation.timeline_name
    tracks = view.snapshot["tracks"]
    rows = []
    for track in tracks:
        for clip in track.get("clips", []):
            row = {
                "track": f"{clip['track_type']}{clip['track_index']}",
                "name": clip["name"],
                "record_in": clip["record_in"],
                "record_out": clip["record_out"],
                "duration": clip["duration"],
                "source_in": clip["source_in_frame"],
                "source_out": clip["source_out_frame"],
                "source_file": clip["source_file"],
            }
            if args.transforms:
                # The stored transform, read back verbatim - the same
                # `GetProperty()` (no argument) reading
                # `timeline_ingest` trusts (AGENTS.md 5). No units are
                # converted here: Pan/Tilt is one model and a unit is
                # not a pixel (`resolve_transform`), so the numbers
                # travel as captured. `timeline_shadow.observe` only
                # records a full generation after `read_reel` proves
                # this timeline current; the generation timestamp below
                # is the currency evidence for these values.
                stored = clip.get("transform") or {}
                for key in ("Pan", "Tilt", "ZoomX", "ZoomY", "Opacity"):
                    row[key.lower()] = stored.get(key, "")
            rows.append(row)
    placed = sum((r["duration"] or 0) for r in rows)
    cols = ["track", "name", "record_in", "record_out", "duration",
            "source_in", "source_out", "source_file"]
    if args.transforms:
        cols += ["pan", "tilt", "zoomx", "zoomy", "opacity"]
    emit([kv_block("items", {
              "timeline": timeline_name,
              "clips": len(rows),
              "placed_frames": placed,
              **_timeline_view_metadata(
                  view, getattr(args, "refresh_live", False)),
          }),
          table("clips", rows, cols),
          note,
          "source_out is inclusive (GetSourceEndFrame); record_out is "
          "exclusive - the mix-up that cost a frame is visible here",
          help_block([f"{TOOL} frames --timeline \"{timeline_name}\""])])
    return 0


def cmd_target(args) -> int:
    """Resolve a DESCRIBED target to the clip it names.

    An agent that knows the task - "trim the second interview B-roll" -
    used to read `items`, count rows in a TOON table and hand a
    `--track video2 --index 1` to the next command. The count was the
    model's, and the table did not print the one column that would let
    it address a clip by its stable identity.

    `target_resolution` is the one deterministic owner of that question.
    This command is its Resolve-side face: the description is resolved
    against the timeline's recorded generation (or a live refresh), and
    the answer carries the exact addressing arguments the mutating
    verbs take. A description that does not name exactly one clip is
    refused, with the candidates named.

    Read-only. It touches no Resolve unless `--refresh-live` is passed.
    """
    from library.tools import target_resolution

    scope_error = _resolve_reel_scope(args, "target")
    if scope_error is not None:
        return fail(*scope_error)
    description = (getattr(args, "description", "") or "").strip()
    if not description:
        return fail(
            "no target was described",
            f'{TOOL} target "row=V2 item=2" --timeline "<name>"')
    try:
        view, note = _timeline_view(
            args.timeline, args.project, caller="resolve_axi.target",
            refresh_live=getattr(args, "refresh_live", False),
            exact=bool(getattr(args, "_reel_exact", False)))
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    resolution = target_resolution.resolve_text(
        description, target_resolution.clips_of(view.snapshot))
    rows = [match.as_dict() for match in resolution.matches]
    if getattr(args, "json", False):
        emit([json.dumps({
            "timeline": view.generation.timeline_name,
            "project": view.generation.project,
            "generation": view.generation.generation,
            **resolution.as_dict(),
            "arguments": _target_arguments(resolution),
        }, indent=2, sort_keys=True, default=str)])
        return 0
    single = resolution.single if len(resolution.matches) == 1 else None
    emit([kv_block("target", {
              "timeline": view.generation.timeline_name,
              "project": view.generation.project,
              "status": resolution.status,
              "selector": description,
              "matched": f"{len(resolution.matches)} clip(s)",
              "note": resolution.note,
              "assumption": single.assumption if single else "",
              **_timeline_view_metadata(
                  view, getattr(args, "refresh_live", False)),
          }),
          table("clips", rows,
                ["row", "index", "name", "unique_id", "record_in",
                 "record_out", "source_in", "source_out", "source_file"]),
          note])
    if single is not None:
        emit([kv_block("arguments", _target_arguments(resolution)),
              help_block([_target_command(args, resolution)])])
    else:
        emit([help_block([
            f'{TOOL} items --timeline "{view.generation.timeline_name}"',
            f'{TOOL} target "<description>" --timeline '
            f'"{view.generation.timeline_name}"'])])
    return 0

def _target_arguments(resolution) -> dict:
    """The addressing arguments a mutating AXI verb takes for this clip."""
    if len(resolution.matches) != 1:
        return {}
    match = resolution.matches[0]
    record = match.record_span or (None, None)
    source = match.source_span or (None, None)
    return {
        "track": f"{match.track_type}{match.track_index}",
        "index": match.position,
        "target-unique-id": match.unique_id,
        "expected-source-file": match.source_file,
        "expected-source-in-frame": source[0],
        "expected-source-out-frame": source[1],
        "expected-record-in-frame": record[0],
        "expected-record-out-frame": record[1],
    }


def _target_command(args, resolution) -> str:
    """The mutating command these arguments address, ready to paste.

    The verb is left as `<verb>`: this command resolves a TARGET, it does
    not know which change the caller intends, and a command that guessed
    one would read as a recommendation.
    """
    arguments = _target_arguments(resolution)
    timeline = getattr(args, "timeline", "") or ""
    return (f"{TOOL} edit <verb> --timeline \"{timeline}\" "
            f"--track {arguments.get('track')} --index {arguments.get('index')} "
            f"--target-unique-id \"{arguments.get('target-unique-id')}\" "
            f"--expected-source-file \"{arguments.get('expected-source-file')}\" "
            f"--expected-source-in-frame {arguments.get('expected-source-in-frame')} "
            f"--expected-source-out-frame {arguments.get('expected-source-out-frame')} "
            f"--expected-record-in-frame {arguments.get('expected-record-in-frame')} "
            f"--expected-record-out-frame {arguments.get('expected-record-out-frame')}")


def cmd_fusion(args) -> int:
    """Every clip carrying Fusion comps, and whether each comp COVERS it.

    Surfaces `reel_read`'s per-item fusion detail: comp count and
    names plus each comp's media window verdict from
    `comp_media_window`. A comp whose MediaIn misses a frame its item
    PLAYS fails the whole render at that frame (six of the captain's
    eight reels, 2026-09-12) - `uncovered` names the reason per comp,
    or `no` when every comp covers every played frame. Read-only.
    """
    scope_error = _resolve_reel_scope(args, "fusion")
    if scope_error is not None:
        return fail(*scope_error)
    try:
        view, note = _timeline_view(
            args.timeline, args.project, caller="resolve_axi.fusion",
            refresh_live=getattr(args, "refresh_live", False),
            exact=bool(getattr(args, "_reel_exact", False)))
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    timeline_name = view.generation.timeline_name
    tracks = view.snapshot["tracks"]
    rows = []
    for track in tracks:
        for clip in track.get("clips", []):
            fusion = clip.get("fusion") or {}
            if not fusion.get("comp_count"):
                continue
            windows = fusion.get("media_windows") or []
            uncovered = "; ".join(
                w.get("uncovered_reason") or ""
                for w in windows if w.get("uncovered_reason")) or "no"
            rows.append({
                "clip": clip["name"],
                "track": f"{clip['track_type']}{clip['track_index']}",
                "record_in": clip["record_in"],
                "record_out": clip["record_out"],
                "comps": fusion["comp_count"],
                "comp_names": ",".join(fusion.get("comp_names") or []),
                "uncovered": uncovered,
            })
    emit([kv_block("fusion", {
              "timeline": timeline_name,
              "clips_with_comps": len(rows),
              **_timeline_view_metadata(
                  view, getattr(args, "refresh_live", False)),
          }),
          note,
          table("comps", rows,
                ["clip", "track", "record_in", "record_out", "comps",
                 "comp_names", "uncovered"]),
          help_block([f"{TOOL} items --timeline \"{timeline_name}\""])])
    return 0


def cmd_captions(args) -> int:
    scope_error = _resolve_reel_scope(args, "captions")
    if scope_error is not None:
        return fail(*scope_error)
    try:
        view, note = _timeline_view(
            args.timeline, args.project, caller="resolve_axi.captions",
            refresh_live=getattr(args, "refresh_live", False),
            exact=bool(getattr(args, "_reel_exact", False)))
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    from library.tools.caption_swap import SUBTITLES_TRACK_NAME
    timeline_name = view.generation.timeline_name
    rows = [{"start": clip["record_in"],
             "end": clip["record_out"],
             "name": clip["name"],
             "file": clip["source_file"]}
            for track in view.snapshot["tracks"]
            if track["type"] == "video"
            and track["name"] == SUBTITLES_TRACK_NAME
            for clip in track["clips"]]
    emit([kv_block("captions", {
              "timeline": timeline_name,
              "cards": len(rows),
              **_timeline_view_metadata(
                  view, getattr(args, "refresh_live", False)),
          }),
          note,
          table("cards", rows, ["start", "end", "name", "file"])])
    return 0


def _pool_clip_rows(folder, bin_path: str) -> tuple:
    """One row per media-pool item under `folder`, recursively.

    Returns `(rows, bins, skipped)`. A row that would not report its name
    or properties is SKIPPED and counted, never half-reported: the
    caller says the count, so "12 clips, 1 unreadable" cannot read as
    "11 clips".
    """
    rows, skipped, bins = [], 0, 0
    stack = [(folder, bin_path)]
    while stack:
        current, path = stack.pop()
        bins += 1
        try:
            clips = current.GetClipList() or []
        except Exception:
            clips = []
        for clip in clips:
            try:
                name = clip.GetName()
            except Exception:
                skipped += 1
                continue
            try:
                props = clip.GetClipProperty() or {}
            except Exception:
                props = {}
            if not isinstance(props, dict):
                props = {}
            rows.append({
                "name": name,
                "bin": path,
                "kind": props.get("Type") or "",
                "file": props.get("File Path") or "",
            })
        try:
            subs = current.GetSubFolderList() or []
        except Exception:
            subs = []
        for sub in subs:
            try:
                sub_name = sub.GetName()
            except Exception:
                skipped += 1
                continue
            stack.append((sub, f"{path}/{sub_name}" if path else sub_name))
    rows.sort(key=lambda r: (r["bin"], r["name"]))
    return rows, bins, skipped


def _resolve_bin(root, bin_path: str):
    """The pool folder at `bin_path` (`A/B/C` from the root), or None."""
    if not bin_path:
        return root, ""
    current, walked = root, []
    for part in [p for p in bin_path.split("/") if p]:
        try:
            subs = current.GetSubFolderList() or []
        except Exception:
            return None, "/".join(walked)
        names = {}
        for sub in subs:
            try:
                names[sub.GetName()] = sub
            except Exception:
                continue
        if part not in names:
            return None, "/".join(walked)
        current, walked = names[part], walked + [part]
    return current, "/".join(walked)


def cmd_pool(args) -> int:
    """Every clip in the media pool, with the bin it sits in.

    The ingest/catalog check `run` covers only at arbitrary token
    cost: which footage is in the pool, where it lives, and whether
    its file is still on disk (the offline aggregate). Read-only:
    pool/folder/clip getters only, shared lease, nothing here moves
    the cursor or opens anything.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            pool = project.GetMediaPool()
            root = pool.GetRootFolder()
        except Exception as exc:
            return fail(f"project {project.GetName()!r} would not open "
                        f"its media pool ({exc}).",
                        f"{TOOL} cursor")
        folder, walked = _resolve_bin(root, args.bin or "")
        if folder is None:
            return fail(
                f"no bin {args.bin!r} under "
                f"{('/' + walked) if walked else 'the pool root'} - "
                f"bins are addressed A/B/C from the root.",
                f"{TOOL} pool")
        rows, bins, skipped = _pool_clip_rows(folder, walked)
    import os as _os
    offline = 0
    for row in rows:
        if not row["file"]:
            continue
        try:
            if not _os.path.exists(row["file"]):
                offline += 1
        except Exception:
            offline += 1
    emit([kv_block("pool", {
              "project": project.GetName(),
              "bin": ("/" + walked) if walked else "/ (root)",
              "bins": bins,
              "clips": len(rows),
              "offline": offline,
              "unreadable": skipped,
          }),
          table("clips", rows, ["name", "bin", "kind", "file"]),
          (f"note: {skipped} item(s) would not report their "
           f"properties and are counted, not listed.")
          if skipped else "",
          help_block([f"{TOOL} items --timeline \"<exact-name>\"",
                      f"{TOOL} pool \"<bin/A/B>\""])])
    return 0


def _render_state(status: dict) -> str:
    """The job's state without reading English.

    `JobStatus` is a LOCALIZED display string ("Complete" on an
    English install, "Concluso" on an Italian one) - comparing it to
    the English literal fails every non-English Resolve with an error
    saying the opposite of what happened. The locale-independent
    signals decide: a populated `Error` is failed, 100% is complete.
    The raw string travels untouched in its own column, never parsed.
    """
    status = status or {}
    if status.get("Error"):
        return "failed"
    try:
        if float(status.get("CompletionPercentage")) >= 100:
            return "complete"
    except (TypeError, ValueError):
        pass
    if str(status.get("JobStatus") or "") == "Complete":
        return "complete"
    return "queued"


def cmd_renders(args) -> int:
    """The render queue, as rows: which timeline, how far, what failed.

    The delivery loop's missing read: `render_qa` measures the FILE,
    nothing shows the QUEUE cheaply. Read-only (`GetRenderJobList` /
    `GetRenderJobStatus` / `IsRenderingInProgress`), shared lease.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            jobs = project.GetRenderJobList() or []
        except Exception as exc:
            return fail(f"project {project.GetName()!r} would not report "
                        f"its render queue ({exc}).",
                        f"{TOOL} cursor")
        rows = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = job.get("JobId") or ""
            try:
                status = project.GetRenderJobStatus(job_id) or {}
            except Exception:
                status = {}
            if not isinstance(status, dict):
                status = {}
            try:
                percent = status.get("CompletionPercentage")
                percent = ("" if percent is None else int(percent))
            except (TypeError, ValueError):
                percent = ""
            rows.append({
                "job": job.get("RenderJobName") or job_id,
                "timeline": job.get("TimelineName") or "",
                "percent": percent,
                "state": _render_state(status),
                "status": status.get("JobStatus") or "",
                "error": status.get("Error") or "",
                "output": "/".join([
                    str(job.get("TargetDir") or "").rstrip("/"),
                    str(job.get("OutputFilename") or "")]).strip("/"),
            })
        try:
            rendering = bool(project.IsRenderingInProgress())
        except Exception:
            rendering = False
    states: dict = {}
    for row in rows:
        states[row["state"]] = states.get(row["state"], 0) + 1
    queue_cols = ["job", "timeline", "percent", "state"]
    if args.full:
        queue_cols += ["status", "output"]
    parts = [kv_block("renders", {
              "project": project.GetName(),
              "jobs": len(rows),
              "rendering": "yes" if rendering else "no",
              "complete": states.get("complete", 0),
              "failed": states.get("failed", 0),
          }),
          table("queue", [{c: r[c] for c in queue_cols} for r in rows],
                queue_cols)]
    failed = [r for r in rows if r["state"] == "failed"]
    if failed:
        parts.append(table("failed", [{
            "job": r["job"],
            "error": preview(r["error"], args.full),
            "output": r["output"],
        } for r in failed], ["job", "error", "output"]))
    hints = [f"{TOOL} frames --timeline \"<exact-name>\""]
    if failed and not args.full:
        hints = [f"{TOOL} renders --full"] + hints
    parts.append(help_block(hints))
    emit(parts)
    return 0


# ── api: Blackmagic's knowledge tools, wrapped as TOON ─────────
#
# `search_scripting_api`, `get_scripting_api`, `get_scripting_docs`
# and `get_whats_new` are documentation, not session state: they never
# touch the captain's project, so they wrap the native MCP server
# (`library/tools/native_mcp.py`) instead of being reimplemented. What
# this layer adds is the axi shape - minimal tables, truncation with
# `--full`, structured errors naming the fix.


def _native(tool: str, args: dict, fix: str):
    """One native MCP call, or a structured failure. Never raises."""
    from library.tools.native_mcp import NativeMcpError, call
    try:
        return call(tool, args), ""
    except NativeMcpError as exc:
        return None, fail(str(exc), fix)


def _unwrap_text(text: str) -> str:
    """The text inside the native envelope, when there is one.

    `get_scripting_docs` answers with a JSON-stringified
    `{content: [{text}]}` envelope AS its text block; plain tools
    answer with bare text. Unwrap the former, pass the latter through
    untouched - a stub that happens to parse as JSON must survive.
    """
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict):
        blocks = parsed.get("content")
        if isinstance(blocks, list):
            return "\n".join(b.get("text", "") for b in blocks
                             if isinstance(b, dict))
    return text


#: Search hits shown before the `--full` escape hatch fires.
API_SEARCH_ROWS = 50


def cmd_api_search(args) -> int:
    text, _ = _native("search_scripting_api", {"pattern": args.pattern},
                      f"{TOOL} api search --help")
    if text is None:
        return 1
    lines = [line for line in text.splitlines() if line.strip()]
    shown = lines if args.full else lines[:API_SEARCH_ROWS]
    rest = len(lines) - len(shown)
    emit([kv_block("api_search", {
              "pattern": args.pattern,
              "matches": len(lines),
          }),
          table("matches", [{"line": line} for line in shown], ["line"]),
          (f"+{rest} more line(s) - re-run with --full")
          if rest else "",
          help_block([f"{TOOL} api stubs <Type>",
                      f"{TOOL} api docs --section \"<heading>\""])])
    return 0


def cmd_api_stubs(args) -> int:
    text, _ = _native("get_scripting_api", {"types": list(args.types)},
                      f"{TOOL} api stubs --help")
    if text is None:
        return 1
    emit([kv_block("api_stubs", {
              "types": ",".join(args.types),
              "chars": len(text),
          }),
          text,
          help_block([f"{TOOL} api search \"<pattern>\"",
                      f"{TOOL} api docs --section \"<heading>\""])])
    return 0


def cmd_api_docs(args) -> int:
    text, _ = _native(
        "get_scripting_docs",
        {"document": args.document, "section": args.section},
        f"{TOOL} api docs --help")
    if text is None:
        return 1
    body = _unwrap_text(text)
    emit([kv_block("api_docs", {
              "document": args.document,
              "section": args.section,
              "chars": len(body),
          }),
          body,
          help_block([f"{TOOL} api search \"<pattern>\"",
                      f"{TOOL} luts update --help" if args.document == "DCTLReadme.txt"
                      else f"{TOOL} api docs --section \"<heading>\""])])
    return 0


def cmd_api_whats_new(args) -> int:
    text, _ = _native("get_whats_new", {"since": args.since},
                      f"{TOOL} api whats-new --since <version>")
    if text is None:
        return 1
    try:
        entries = json.loads(text).get("entries", [])
    except ValueError:
        return fail(f"the native changelog came back unparseable.",
                    f"{TOOL} api whats-new --since {args.since}")
    if not isinstance(entries, list):
        entries = []
    cols = ["version", "date"] + (["changelog"] if args.full else [])
    rows = [{
        "version": e.get("version", ""),
        "date": e.get("date", ""),
        "changelog": e.get("changelog", ""),
    } for e in entries if isinstance(e, dict)]
    emit([kv_block("whats_new", {
              "since": args.since,
              "entries": len(rows),
          }),
          table("releases", rows, cols),
          (f"changelogs hidden - re-run with --full")
          if rows and not args.full else "",
          help_block([f"{TOOL} api whats-new --since {args.since} --full"]
                     if rows and not args.full else [])])
    return 0


# ── api intellisearch-query: does scripting expose IntelliSearch search? ──
#
# Ren search stays primary; Resolve IntelliSearch may contribute extra
# evidence. The scripting API is known to expose ONE IntelliSearch verb,
# `MediaPoolItem.AnalyzeForIntellisearch`, which INDEXES a pool clip and
# answers no question (`sense intellisearch`). This probe asks the three
# knowledge routes whether a QUERY verb exists too, and says which. The
# verdict rule is conservative on purpose: an index-only verb counted as
# query access would send the AX lane home early with nothing to drive.

#: IntelliSearch scripting verbs that only index. Their existence proves
#: nothing about querying: each takes a pool clip and answers True/False.
INTELLISEARCH_INDEX_ONLY_VERBS = ("AnalyzeForIntellisearch",)

#: What a query-shaped verb name carries. A verb that ASKS has one of
#: these in its name; `Analyze` indexes, `Reset` clears. The match runs
#: on the name with the `IntelliSearch` token itself removed - every
#: IntelliSearch verb contains `search` inside its own name, which is
#: how `ResetIntellisearchAnalysis` was once reported as a query verb
#: (measured live 2026-10-06). The probe prints every IntelliSearch
#: line it found beside the verdict, so a reviewer reads the evidence,
#: not just the summary.
_INTELLISEARCH_QUERY_NAME = ("search", "query", "find", "match", "result")


def _intellisearch_remainder(name: str) -> str:
    """The verb name minus the `IntelliSearch` token it is named for."""
    import re

    return re.sub(r"intellisearch", "", name, flags=re.IGNORECASE)


def intellisearch_query_verdict(intelli_lines: list) -> dict:
    """Whether the scripting surface exposes an IntelliSearch QUERY verb.

    `intelli_lines` are the stub/search lines naming IntelliSearch.
    Returns `query_via_scripting` ("available"/"unavailable"), the
    query-shaped verbs, the index-only verbs, the other IntelliSearch
    verbs (reset and friends - neither), and the reason in words.
    Never raises: unparseable input is no query verb, said plainly.
    """
    import re

    names = []
    for line in intelli_lines or []:
        if not isinstance(line, str):
            continue
        for found in re.findall(
                r"[A-Za-z_][A-Za-z0-9_]*[Ii]ntelli[A-Za-z0-9_]*", line):
            if found not in names:
                names.append(found)
    lowered_index = {verb.lower() for verb in INTELLISEARCH_INDEX_ONLY_VERBS}
    index_only = [name for name in names if name.lower() in lowered_index]
    query_shaped = [
        name for name in names
        if name.lower() not in lowered_index
        and any(part in _intellisearch_remainder(name).lower()
                for part in _INTELLISEARCH_QUERY_NAME)]
    other = [name for name in names
             if name not in index_only and name not in query_shaped]
    if query_shaped:
        return {"query_via_scripting": "available",
                "query_verbs": query_shaped,
                "index_only_verbs": index_only,
                "other_verbs": other,
                "reason": (f"{', '.join(query_shaped)} asks - drive it, "
                           f"not the UI")}
    roles = ([f"{verb} indexes a pool clip" for verb in index_only]
             + [f"{verb} manages the analysis without asking" for verb in other])
    if roles:
        return {"query_via_scripting": "unavailable",
                "query_verbs": [],
                "index_only_verbs": index_only,
                "other_verbs": other,
                "reason": (f"{'; '.join(roles)} - "
                           f"nothing here answers a question")}
    return {"query_via_scripting": "unavailable",
            "query_verbs": [],
            "index_only_verbs": [],
            "other_verbs": [],
            "reason": "no IntelliSearch verb in the scripting surface at all"}


def cmd_api_intellisearch_query(args) -> int:
    """Probe the scripting surface for an IntelliSearch QUERY verb.

    Reads the API the way the lane spec asks - stubs and whats-new
    first - and reports the measured answer: `available` names the verb
    to drive, `unavailable` sends the collector down the AX/UI route.
    Documentation only: it never touches any project.
    """
    search_text, _ = _native("search_scripting_api", {"pattern": "[Ii]ntelli"},
                             f"{TOOL} api intellisearch-query --help")
    if search_text is None:
        return 1
    stub_text, _ = _native("get_scripting_api",
                           {"types": ["MediaPool", "MediaPoolItem"]},
                           f"{TOOL} api intellisearch-query --help")
    if stub_text is None:
        return 1
    changelog_text, _ = _native("get_whats_new", {"since": args.since},
                                f"{TOOL} api intellisearch-query --help")
    if changelog_text is None:
        return 1
    search_lines = [line for line in _unwrap_text(search_text).splitlines()
                    if line.strip()]
    stub_lines = [line for line in _unwrap_text(stub_text).splitlines()
                  if "ntelli" in line.lower()]
    try:
        entries = json.loads(changelog_text).get("entries", [])
    except ValueError:
        return fail(f"the native changelog came back unparseable.",
                    f"{TOOL} api whats-new --since {args.since}")
    if not isinstance(entries, list):
        entries = []
    mentions = [{"version": e.get("version", ""), "date": e.get("date", "")}
                for e in entries
                if isinstance(e, dict) and "ntelli" in str(
                    e.get("changelog", "")).lower()]
    verdict = intellisearch_query_verdict(search_lines + stub_lines)
    lines = search_lines + [line for line in stub_lines
                            if line not in search_lines]
    shown = lines if args.full else lines[:API_SEARCH_ROWS]
    rest = len(lines) - len(shown)
    parts = [kv_block("intellisearch_query", {
                "since": args.since,
                "query_via_scripting": verdict["query_via_scripting"],
                "reason": verdict["reason"],
            }),
            table("intellisearch_lines",
                  [{"line": line} for line in shown], ["line"]),
            (f"+{rest} more line(s) - re-run with --full")
            if rest else ""]
    if mentions:
        parts.append(table("changelog_mentions", mentions,
                           ["version", "date"]))
    if verdict["query_via_scripting"] == "available":
        parts.append(help_block(
            [f"{TOOL} run --help (call it and shape rows for "
             f"ren search --external-hits)"]))
    else:
        parts.append(help_block(
            [f"{TOOL} sense intellisearch --help (index-only; querying "
             f"goes through the UI route)"]))
    emit(parts)
    return 0


def cmd_intellisearch_collect(args) -> int:
    """Collect one IntelliSearch query into an `--external-hits` file.

    The query half the probe found missing: builds the foreground
    collection spec, runs it inside a borrowed `resolve.qualification_session`
    on the serving broker, reads the query and result names from the same
    transaction receipt, intersects them with the project catalog, and
    files the rows `ren search --external-hits` merges. `--survey` runs
    the background-safe half alone (menu/AX reach, anytime); `--dry-run`
    prints the validated spec without spending a take.
    """
    from library.tools import intellisearch_collect as collect_mod
    from library.tools.ren_refusal import RenRefusal
    from library.tools.resolved.jobs import JobRefused

    if args.survey:
        try:
            report = collect_mod.survey()
        except Exception as exc:                          # noqa: BLE001
            return fail(f"the survey failed ({exc}).",
                        f"{TOOL} intellisearch-collect --survey")
        emit([kv_block("intellisearch_survey", {
                    "menu_commands": report["menu_commands"],
                    "intellisearch_menu": len(report["intellisearch_menu"]),
                    "ax_text_fields": len(report["ax_text_fields"]),
                }),
              table("intellisearch_menu",
                    [{"path": " > ".join(path)}
                     for path in report["intellisearch_menu"]], ["path"])
              if report["intellisearch_menu"]
              else "no IntelliSearch menu entry - the box is Qt-only",
              table("ax_text_fields", report["ax_text_fields"],
                    ["title", "label"])])
        return 0
    box = None
    if args.box:
        try:
            x, y = (float(bit) for bit in str(args.box).split(","))
            box = (x, y)
        except ValueError:
            return fail(f"--box {args.box!r} is not x,y in window points.",
                        f"{TOOL} intellisearch-collect --help")
    try:
        if args.dry_run:
            spec = collect_mod.build_collection_spec(
                args.query, box, timeline=args.timeline,
                budget_seconds=args.budget)
            emit([kv_block("intellisearch_collect_plan", {
                        "query": args.query,
                        "actions": len(spec["actions"]),
                    }),
                  json.dumps(spec, indent=2)])
            return 0
        result = collect_mod.collect(
            args.project, args.query, box, out=args.out or None,
            timeline=args.timeline, budget_seconds=args.budget)
    except (JobRefused, RenRefusal) as exc:
        return fail(str(exc),
                    f"{TOOL} intellisearch-collect --help")
    emit([kv_block("intellisearch_collect", {
                "query": args.query,
                "rows": result["rows"],
                "seen_strings": result["seen_strings"],
                "read_text": result["screen_text"],
                "out": result["out"],
            }),
          table("matching_clips", [
                    {"name": name}
                    for name in result["matching_clip_names"]],
                ["name"]),
          help_block(
              [f"ren search '{args.project}' --external-hits "
               f"\"{result['out']}\""])])
    return 0


# ── luts: the shared shelf, listed locally, written wrapped ──
#
# Listing walks the on-disk LUT directory (no Resolve contact at
# all). Writes go through the native MCP's `update_dctl` /
# `delete_dctl` / `delete_lut` / `generate_lut`, which compile-check
# before writing - this layer adds the axi discipline those tools
# lack: a dry-run plan by default, `--apply` to write, and a read-back
# verification. All of it lands in the shared `LUT/MCP` shelf, never
# in the captain's project.

#: Where Resolve keeps third-party transforms, and our shelf in it.
LUT_DIR = ("/Library/Application Support/Blackmagic Design/"
           "DaVinci Resolve/LUT")
LUT_MCP_SHELF = "MCP"

LUT_EXTS = {".dctl": "dctl", ".3dl": "lut", ".cube": "lut",
            ".dat": "lut", ".lut": "lut", ".olut": "lut"}


def _lut_rows() -> list:
    rows = []
    for base, _dirs, files in os.walk(LUT_DIR):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in LUT_EXTS:
                continue
            full = os.path.join(base, name)
            rows.append({
                "name": os.path.relpath(full, LUT_DIR),
                "kind": LUT_EXTS[ext],
                "file": full,
            })
    rows.sort(key=lambda r: r["name"])
    return rows


def cmd_luts_list(_args) -> int:
    if not os.path.isdir(LUT_DIR):
        emit(["luts: 0 rows (no shared LUT directory on this machine)",
              help_block([f"{TOOL} api docs --document DCTLReadme.txt"])])
        return 0
    rows = _lut_rows()
    kinds: dict = {}
    for row in rows:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    emit([kv_block("luts", {
              "dir": LUT_DIR,
              "files": len(rows),
              "dctl": kinds.get("dctl", 0),
              "lut": kinds.get("lut", 0),
          }),
          table("files", rows, ["name", "kind", "file"]),
          help_block([f"{TOOL} luts update --name \"<file>\" --file <path>",
                      f"{TOOL} api docs --document DCTLReadme.txt "
                      f"--section TOC"])])
    return 0


def _lut_name(args, tool: str):
    """The shelf-relative target, or a structured refusal (None)."""
    name = args.name or args.name_pos or ""
    if not name:
        fail(f"{tool} needs a shelf file name.",
             f"{TOOL} luts {tool} --name \"<file>\"")
        return None
    if os.path.isabs(name) or ".." in name.split("/"):
        fail(f"{name!r} escapes the {LUT_MCP_SHELF} shelf - pass a "
             f"shelf-relative name like \"cool.dctl\".",
             f"{TOOL} luts {tool} --name \"cool.dctl\"")
        return None
    return name


def _lut_target(name: str) -> str:
    return os.path.join(LUT_DIR, LUT_MCP_SHELF, name)


def cmd_luts_update(args) -> int:
    if args.text and args.file:
        return fail("pass --text or --file, not both.",
                    f"{TOOL} luts update --help")
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                content = handle.read()
        except OSError as exc:
            return fail(f"cannot read {args.file!r} ({exc}).",
                        f"{TOOL} luts update --name \"{args.name}\" "
                        f"--text \"...\"")
    elif args.text:
        content = args.text
    else:
        return fail("update needs the DCTL source: --text or --file.",
                    f"{TOOL} api docs --document DCTLReadme.txt")
    name = _lut_name(args, "update")
    if name is None:
        return 1
    target = _lut_target(name)
    if not args.apply:
        emit([kv_block("lut_plan", {
                  "name": name,
                  "target": target,
                  "bytes": len(content.encode("utf-8")),
                  "exists": "yes" if os.path.exists(target) else "no",
              }),
              preview(content, False),
              f"dry run - pass --apply to compile-check and write",
              help_block([f"{TOOL} luts update --name \"{name}\" "
                          f"--file <path> --apply"])])
        return 0
    text, _ = _native("update_dctl", {"path": name, "content": content},
                      f"{TOOL} luts list")
    if text is None:
        return 1
    placed = os.path.exists(target)
    emit([kv_block("lut_update", {
              "name": name,
              "target": target,
              "placed": "yes (read back)" if placed else
              "NO - the server said written but the file is missing",
          }),
          preview(text, args.full),
          help_block([f"{TOOL} luts list"])])
    return 0 if placed else 1


def cmd_luts_delete(args) -> int:
    name = _lut_name(args, "delete")
    if name is None:
        return 1
    target = _lut_target(name)
    if not args.apply:
        emit([kv_block("lut_plan", {
                  "name": name,
                  "target": target,
                  "exists": "yes" if os.path.exists(target) else "no",
              }),
              f"dry run - pass --apply to delete",
              help_block([f"{TOOL} luts delete --name \"{name}\" "
                          f"--apply"])])
        return 0
    tool = ("delete_dctl" if name.lower().endswith(".dctl")
            else "delete_lut")
    text, _ = _native(tool, {"path": name}, f"{TOOL} luts list")
    if text is None:
        return 1
    gone = not os.path.exists(target)
    emit([kv_block("lut_delete", {
              "name": name,
              "deleted": "yes (read back)" if gone else
              "NO - the file is still there",
          }),
          preview(text, args.full),
          help_block([f"{TOOL} luts list"])])
    return 0 if gone else 1


def cmd_luts_generate(args) -> int:
    if args.transform and args.transform_file:
        return fail("pass --transform or --transform-file, not both.",
                    f"{TOOL} luts generate --help")
    if args.transform_file:
        try:
            with open(args.transform_file,
                       encoding="utf-8") as handle:
                transform = handle.read()
        except OSError as exc:
            return fail(f"cannot read {args.transform_file!r} ({exc}).",
                        f"{TOOL} luts generate --help")
    elif args.transform:
        transform = args.transform
    else:
        return fail("generate needs the transform function body.",
                    f"{TOOL} luts generate --help")
    name = _lut_name(args, "generate")
    if name is None:
        return 1
    target = _lut_target(name)
    if not args.apply:
        emit([kv_block("lut_plan", {
                  "name": name,
                  "target": target,
                  "size": args.size,
                  "exists": "yes" if os.path.exists(target) else "no",
              }),
              preview(transform, False),
              f"dry run - pass --apply to evaluate and write",
              help_block([f"{TOOL} luts generate --name \"{name}\" "
                          f"--size {args.size} "
                          f"--transform \"...\" --apply"])])
        return 0
    text, _ = _native("generate_lut",
                      {"path": name, "size": args.size,
                       "transform": transform},
                      f"{TOOL} luts list")
    if text is None:
        return 1
    placed = os.path.exists(target)
    emit([kv_block("lut_generate", {
              "name": name,
              "target": target,
              "placed": "yes (read back)" if placed else
              "NO - the server said written but the file is missing",
          }),
          preview(text, args.full),
          help_block([f"{TOOL} luts list"])])
    return 0 if placed else 1


# ── launch / project / audio ─────────────────────────────────────


#: How the app is started. One place, so a renamed bundle fails in
#: tests instead of opening the wrong app (or nothing) live.
#: Verified against the installed bundle id
#: `com.blackmagic-design.DaVinciResolve` via
#: `osascript -e 'id of app "DaVinci Resolve"'` (resolves the name
#: without launching anything).
_LAUNCH_ARGV = ("open", "-a", "DaVinci Resolve")


def _launch_app() -> tuple:
    """Ask macOS to open Resolve. Returns `(ok, detail)`.

    A module-level seam so tests never shell out: they monkeypatch
    this, not the caller.
    """
    import subprocess as _subprocess
    try:
        started = _subprocess.run(
            list(_LAUNCH_ARGV),
            capture_output=True, text=True, encoding="utf-8",
            timeout=60, check=False)
    except Exception as exc:
        return False, f"could not ask macOS to open Resolve ({exc})"
    if started.returncode != 0:
        detail = (started.stderr or "").strip() or "no reason given"
        return False, f"macOS would not open Resolve ({detail})"
    return True, ""


def cmd_launch(_args) -> int:
    """Start the Resolve app when it is not running. Nothing else.

    Idempotent: when the scripting API already answers, nothing is
    launched and the session is reported. This never opens or creates
    a project or timeline - if Resolve starts with no project open,
    that is reported, not fixed.
    """
    try:
        resolve = _connect()
    except AxiError:
        resolve = None
    if resolve is not None:
        with _lease(exclusive=False):
            project = _project(resolve, "")
            from library.tools.marker_feedback import current_timeline
            try:
                cursor, _ = current_timeline(resolve)
                cursor_name = cursor.GetName()
            except Exception:
                cursor_name = "(none open)"
        emit([kv_block("launch", {
                  "running": "yes (already - nothing launched)",
                  "project": project.GetName(),
                  "cursor": cursor_name,
              }),
              help_block([f"{TOOL} timeline list"])])
        return 0
    ok, detail = _launch_app()
    if not ok:
        return fail(detail, f"{TOOL} launch")
    import time as _time
    launched = None
    for _ in range(30):
        _time.sleep(2)
        try:
            launched = _connect()
            break
        except AxiError:
            continue
    if launched is None:
        return fail("Resolve did not answer within 60s of launching.",
                    f"{TOOL} launch")
    with _lease(exclusive=False):
        try:
            project = _project(launched, "")
            project_name = project.GetName()
        except AxiError:
            project_name = "(no project open)"
    emit([kv_block("launch", {
              "running": "yes (launched now)",
              "project": project_name,
          }),
          help_block([f"{TOOL} timeline list"])])
    return 0


def _named_list(entries) -> list:
    rows = []
    for entry in entries or []:
        if isinstance(entry, str):
            rows.append({"name": entry})
        elif isinstance(entry, dict):
            rows.append({"name": entry.get("PresetName")
                         or entry.get("name") or str(entry)})
        else:
            rows.append({"name": str(entry)})
    return rows


def cmd_project(args) -> int:
    """The open project: identity plus the delivery-relevant settings.

    Read-only. The settings are the ones a render decision needs
    (frame rate, delivery frame) plus the preset inventory; the full
    settings dict stays one `run` script away.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        from library.tools.marker_feedback import current_timeline
        try:
            cursor, _ = current_timeline(resolve)
            cursor_name = cursor.GetName()
        except Exception:
            cursor_name = "(none open)"
        settings = {}
        for key in ("timelineFrameRate", "timelineResolutionWidth",
                    "timelineResolutionHeight"):
            try:
                settings[key] = project.GetSetting(key)
            except Exception:
                settings[key] = ""
        try:
            presets = _named_list(project.GetPresetList())
        except Exception:
            presets = []
        try:
            render_presets = _named_list(project.GetRenderPresetList())
        except Exception:
            render_presets = []
        from library.tools import timeline_read
        try:
            views = timeline_read.list_timelines(
                project.GetName(), caller="resolve_axi.project")
            names = [view.generation.timeline_name for view in views]
            timeline_count = len(names)
            timeline_freshness = "latest stored generations; later live " \
                "edits may be absent"
        except timeline_read.ShadowError:
            timeline_count = "unknown (none recorded)"
            timeline_freshness = "no recorded generation"
    emit([kv_block("project", {
              "name": project.GetName(),
              "timelines": timeline_count,
              "timeline_source": "recorded generations",
              "timeline_freshness": timeline_freshness,
              "cursor": cursor_name,
              "fps": settings["timelineFrameRate"],
              "resolution": (f"{settings['timelineResolutionWidth']}x"
                             f"{settings['timelineResolutionHeight']}"),
          }),
          table("render_presets", render_presets, ["name"]),
          table("presets", presets, ["name"]),
          help_block([f"{TOOL} timeline list",
                      f"{TOOL} renders"])])
    return 0


def cmd_ownership_adopt(args) -> int:
    """Adopt final reel timelines by exact Resolve id, without editing them."""
    try:
        from library.tools.project_registry import get_project
        config = get_project(args.project)
        expected_name = str(config.resolve.project_name or "").strip()
        if not expected_name:
            return fail(
                f"project {config.slug!r} has no Resolve project binding.",
                "ren info <project>")
        resolve = _connect()
        project = _project(resolve, expected_name)

        def snapshot(live_project):
            try:
                current = live_project.GetCurrentTimeline()
                if current is None:
                    raise ValueError("Resolve has no current timeline")
                current_name = str(current.GetName())
                current_id = str(current.GetUniqueId() or "").strip()
                count = int(live_project.GetTimelineCount() or 0)
                inventory = []
                for index in range(1, count + 1):
                    timeline = live_project.GetTimelineByIndex(index)
                    if timeline is None:
                        raise ValueError(
                            f"timeline {index} returned no object")
                    unique_id = timeline.GetUniqueId()
                    inventory.append({
                        "name": str(timeline.GetName()),
                        "unique_id": str(unique_id) if unique_id else None,
                    })
            except Exception as exc:  # noqa: BLE001
                raise AxiError(
                    f"the live project inventory could not be read ({exc}).",
                    f"{TOOL} ownership <project> --who <name>") from exc
            if not current_id:
                raise AxiError(
                    f"the current timeline {current_name!r} has no unique "
                    "id, so the pre-adoption Resolve state cannot be "
                    "recorded.",
                    f"{TOOL} ownership <project> --who <name>")
            return {
                "project": str(live_project.GetName()),
                "current_timeline": {
                    "name": current_name,
                    "unique_id": current_id,
                },
                "timeline_count": count,
                "inventory": inventory,
            }

        before = snapshot(project)
        if before["project"] != expected_name:
            raise AxiError(
                f"the open project is {before['project']!r}, not the exact "
                f"configured name {expected_name!r}.",
                f"{TOOL} cursor")
        # Report the live starting point before writing the local ownership
        # ledger. Resolve itself remains under a shared read lease.
        emit([kv_block("ownership_adoption_before", {
                  "project": before["project"],
                  "timeline": before["current_timeline"]["name"],
                  "timeline_id": before["current_timeline"]["unique_id"],
                  "timeline_count": before["timeline_count"],
              })])
        sys.stdout.flush()

        from library.tools.plan_provenance import (
            adopt_reel_timeline_identities,
        )
        review_dir = str(config.project_root / "pipeline_output" / "review")
        result = adopt_reel_timeline_identities(
            review_dir, before["inventory"], who=args.who)

        manager = resolve.GetProjectManager()
        current_project = manager.GetCurrentProject()
        if current_project is None:
            raise AxiError(
                "Resolve no longer has the project open after adoption; "
                "the local ownership record was written, but live state "
                "could not be confirmed.",
                f"{TOOL} cursor")
        after = snapshot(current_project)
        if (after["project"] != before["project"]
                or after["current_timeline"] != before["current_timeline"]
                or after["timeline_count"] != before["timeline_count"]
                or after["inventory"] != before["inventory"]):
            raise AxiError(
                "Resolve project, current timeline, timeline count, or "
                "timeline identities changed during adoption. The local "
                "ownership record was written; no timeline write was "
                "issued. Inspect the recorded ids before rebuilding.",
                f"{TOOL} timeline list")

        names = result["adopted"]
        emit([kv_block("ownership_adoption_after", {
                  "project": after["project"],
                  "timeline": after["current_timeline"]["name"],
                  "timeline_id": after["current_timeline"]["unique_id"],
                  "timeline_count": after["timeline_count"],
                  "resolve_timeline_writes": 0,
              }),
              table("adopted_reels", [{"name": name} for name in names],
                    ["name"]) if names else "adopted_reels: 0 new reels",
              table("already_adopted", [
                        {"name": name}
                        for name in result["already_adopted"]], ["name"])
              if result["already_adopted"]
              else "already_adopted: 0 reels",
              help_block([f"{TOOL} ownership <project> --who <name>"])])
        return 0
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    except Exception as exc:  # noqa: BLE001
        return fail(
            f"reel timeline adoption failed ({type(exc).__name__}: {exc}).",
            f"{TOOL} ownership <project> --who <name>")


def _voice_state(value) -> str:
    if isinstance(value, dict):
        if value.get("isEnabled"):
            return f"on {value.get('amount', '')}".strip()
        return "off"
    return _cell(value)


def cmd_audio(args) -> int:
    """Audio tracks: which rows carry sound, enabled or not, how much.

    Read-only. Voice isolation and lock state ride along behind
    `--full`; nothing here changes a mix - the mix goes through OTIO
    at placement time.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            count = timeline.GetTrackCount("audio") or 0
        except Exception as exc:
            return fail(f"timeline {timeline.GetName()!r} would not "
                        f"report its audio tracks ({exc}).",
                        f"{TOOL} timeline list")
        try:
            on_cursor = (project.GetCurrentTimeline().GetName()
                         == timeline.GetName())
        except Exception:
            on_cursor = False
        rows = []
        for index in range(1, count + 1):
            try:
                items = (timeline.GetItemListInTrack("audio", index)
                         or [])
            except Exception:
                items = []
            row: dict = {
                "track": f"audio{index}",
                "clips": len(items),
            }
            try:
                row["name"] = timeline.GetTrackName("audio", index) or ""
            except Exception:
                row["name"] = ""
            # GetIsTrackEnabled answers False for every track of a
            # timeline that is not current - measured 2026-10-01 on
            # Podcast (field test): 34 reels read "no" by name and
            # "yes" with each made current. A read that cannot see the
            # flag says so rather than reporting a muted row.
            if not on_cursor:
                row["enabled"] = "not-current"
            else:
                try:
                    row["enabled"] = ("yes" if timeline.GetIsTrackEnabled(
                        "audio", index) else "no")
                except Exception:
                    row["enabled"] = ""
            full: dict = {}
            for key, method in (("sub_type", "GetTrackSubType"),
                                ("locked", "GetIsTrackLocked")):
                try:
                    value = getattr(timeline, method)("audio", index)
                    full[key] = ("yes" if value is True else
                                 "no" if value is False else _cell(value))
                except Exception:
                    full[key] = ""
            try:
                full["voice_isolation"] = _voice_state(
                    timeline.GetVoiceIsolationState(index))
            except Exception:
                full["voice_isolation"] = ""
            row.update(full if args.full else {})
            rows.append(row)
    cols = ["track", "name", "enabled", "clips"]
    if args.full:
        cols += ["sub_type", "locked", "voice_isolation"]
    emit([kv_block("audio", {
              "timeline": timeline.GetName() +
              (" (current)" if is_current else ""),
              "tracks": len(rows),
          }),
          note,
          table("tracks", rows, cols),
          help_block([f"{TOOL} items --timeline \"{timeline.GetName()}\""])])
    return 0


def cmd_cursor(args) -> int:
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=False):
        project = _project(resolve, "")
        from library.tools.marker_feedback import current_timeline
        try:
            timeline, _ = current_timeline(resolve)
            cursor = timeline.GetName()
        except Exception as exc:
            return fail(f"no timeline open ({exc}) - open one and re-run.",
                        f"{TOOL} timeline list")
    if args.expect and cursor != args.expect:
        return fail(f"cursor is on {cursor!r}, expected {args.expect!r} - "
                    f"a sibling lane may have moved it.",
                    f"{TOOL} timeline get \"{args.expect}\"")
    emit([kv_block("cursor", {"project": project.GetName(),
                              "timeline": cursor})] +
         ([f"asserted: cursor is on {args.expect!r}"] if args.expect
          else []))
    return 0


def cmd_frames(args) -> int:
    scope_error = _resolve_reel_scope(args, "frames")
    if scope_error is not None:
        return fail(*scope_error)
    try:
        view, note = _timeline_view(
            args.timeline, args.project, caller="resolve_axi.frames",
            refresh_live=getattr(args, "refresh_live", False),
            exact=bool(getattr(args, "_reel_exact", False)))
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    snapshot = view.snapshot
    timeline_name = view.generation.timeline_name
    start, end = int(snapshot["start_frame"]), int(snapshot["end_frame"])
    frames = end - start + 1
    fps = snapshot["reported_fps"]
    rows = [{
        "track": f"{track['type']}{track['index']}",
        "clips": len(track["clips"]),
        "placed_frames": sum(clip["duration"]
                             for clip in track["clips"]),
    } for track in snapshot["tracks"]]
    emit([kv_block("frames", {
              "timeline": timeline_name,
              "start": start, "end": end, "frames": frames, "fps": fps,
              **_timeline_view_metadata(
                  view, getattr(args, "refresh_live", False)),
          }),
          note,
          table("tracks", rows, ["track", "clips", "placed_frames"])])
    return 0


def _readable(item) -> bool:
    try:
        item.GetDuration()
        return True
    except Exception:
        return False


# ── run: the cheap escape hatch ──────────────────────────────────


#: Resolve mutators refused by `run` unless `--unsafe` is passed.
#: Reads in this API are `Get*`; every mutator starts with one of
#: these prefixes, so the rule is a prefix match rather than an
#: enumeration of an open-ended API: owning the serialization cost
#: does not require listing what the scripting API can do. The
#: second row covers the 21.1 mutators outside Set/Add/Create:
#: PerformMulticamSmartSwitch, FlattenMulticam, TranscribeAudio,
#: AssignToColorGroup, AutoSyncAudio, SmartReframe, DetectSceneCuts,
#: AnalyzeForIntellisearch, AnalyzeForSlate and GenerateSpeech would
#: otherwise read as plain calls.
_RUN_WRITE_PREFIXES = ("Set", "Add", "Create", "Delete", "Import",
                       "Move", "Update", "Apply", "Replace", "Remove",
                       "Clear", "Load", "Close", "Open", "Duplicate",
                       "Start", "Stop", "Copy", "Paste", "Undo", "Save",
                       "Export", "Render", "Grab", "Perform", "Flatten",
                       "Transcribe", "Assign", "Auto", "Smart",
                       "Detect", "Generate", "Analyze")

#: What a `run` script may assume in scope. This list is exact: the
#: script is `exec`d with exactly these names (plus what Python puts
#: there itself), so a script that is only the `result = ...` line
#: needs no boilerplate at all. `open_project` is the one sanctioned
#: project switch: `qualification_project.switch_project` with the
#: switch settle and the readback (a raw `manager.LoadProject` inside
#: a script races the switch the way the 2026-10-06 lanes did).
RUN_SCOPE = ("resolve", "manager", "project", "project_name",
             "timeline", "timeline_name", "is_current",
             "timeline_names", "by_index", "read_notes",
             "open_project")


def _run_open_project(manager, name: str):
    """Open one project with switch discipline, for `run` scripts.

    The scope-provided `open_project(name)`: live-handle check before
    and after, the switch settle, the broker guard stamp, and the name
    read back - refusing on a dead handle instead of scripting into a
    project load. Scripts that switch projects call this, never a raw
    `manager.LoadProject`.
    """
    from library.tools.qualification_project import switch_project
    return switch_project(manager, name)

#: Columns kept when a script returns wide dicts. Past this the table
#: names the overflow and points at `--json`: a TOON table with fifty
#: columns is not cheaper than the JSON it replaces.
RUN_MAX_COLUMNS = 12

#: Unrecorded `run` remains developer tooling behind this opt-in. A
#: recorded `run --purpose` carries its own durable Ren run record and
#: is admitted by the broker only with that stated purpose.
RUN_OPT_IN_ENV = "RESOLVE_AXI_ALLOW_RUN"
_RECORDED_RUN_CONTEXT = contextvars.ContextVar(
    "resolve_axi_recorded_run", default=False)
_RECORDED_CALLER = contextvars.ContextVar(
    "resolve_axi_recorded_caller", default="")


def run_script_enabled() -> bool:
    """Whether this is a developer shell or a recorded broker run."""
    return (os.environ.get(RUN_OPT_IN_ENV) == "1"
            or _RECORDED_RUN_CONTEXT.get())


def _run_opt_in_refusal() -> tuple:
    """The refusal `cmd_run` and the broker answer without opt-in."""
    return (
        f"`run` without `--purpose` is developer tooling and needs "
        f"explicit opt-in ({RUN_OPT_IN_ENV}=1).",
        f"pass `--purpose \"what this script does\"` for a recorded Ren "
        f"run, or export {RUN_OPT_IN_ENV}=1 for an unrecorded developer "
        f"run, then re-run `{TOOL} run --help`",
    )


def _refused_resolve_writes(script: str) -> list:
    """Mutator and opaque dynamic calls in a `run` script, in order.

    A `SyntaxError` is an `AxiError` (the script never runs); anything
    else returns the offending names, possibly empty. String literals
    never match on their own: attribute access (`timeline.AddMarker`)
    does. Literal `getattr` writer names are scanned too; opaque attribute
    access or dynamic code execution needs `--unsafe` because static
    analysis cannot prove it is read-only.
    """
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        raise AxiError(f"script would not parse ({exc}).",
                       f"{TOOL} run --help") from exc
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            name = node.attr
            if name.startswith(_RUN_WRITE_PREFIXES):
                if name not in hits:
                    hits.append(name)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "getattr":
                name = (node.args[1].value
                        if len(node.args) > 1
                        and isinstance(node.args[1], ast.Constant)
                        and isinstance(node.args[1].value, str) else None)
                if name and name.startswith(_RUN_WRITE_PREFIXES):
                    if name not in hits:
                        hits.append(name)
                elif name is None and "dynamic getattr" not in hits:
                    hits.append("dynamic getattr")
            elif node.func.id in {"setattr", "delattr", "exec", "eval",
                                  "compile", "vars"}:
                label = f"dynamic {node.func.id}"
                if label not in hits:
                    hits.append(label)
        elif (isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr in {"__getattribute__", "__getattr__",
                                     "methodcaller", "attrgetter"}):
            label = f"dynamic {node.func.attr.removeprefix('__').removesuffix('__')}"
            if label not in hits:
                hits.append(label)
    return hits


#: Resolve calls that report success while destroying work that cannot
#: be recovered. `TimelineItem.CopyGrades` replaces the target's whole
#: grade, returns True while doing it, and versions nothing to go back
#: to (measured by baking each state to a 33-point LUT and comparing
#: bytes). `--unsafe` declares A write, not THIS one: the script must
#: name it out loud with `--acknowledge-copy-grades` first.
_COPYGRADES_TRAP = "CopyGrades"


def _copygrades_access(script: str) -> str | None:
    """Classify a named or statically opaque path to CopyGrades."""
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == _COPYGRADES_TRAP:
            return "named"
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            function = node.func.id
            name_index = 1
        elif isinstance(node.func, ast.Attribute):
            function = node.func.attr
            name_index = 0
        else:
            continue
        if function in {"getattr", "__getattribute__", "__getattr__",
                        "methodcaller", "attrgetter"}:
            name = (node.args[name_index].value
                    if len(node.args) > name_index
                    and isinstance(node.args[name_index], ast.Constant)
                    and isinstance(node.args[name_index].value, str) else None)
            if name == _COPYGRADES_TRAP:
                return "named"
            if name is None:
                return "opaque"
        elif function in {"exec", "eval", "compile", "vars"}:
            return "opaque"
    return None


def _names_copygrades(script: str) -> bool:
    """Whether CopyGrades is named or hidden behind opaque Python access."""
    return _copygrades_access(script) is not None


def _copygrades_refusal(script: str, purpose: str = "") -> tuple[str, str]:
    access = _copygrades_access(script)
    if access == "opaque":
        message = ("script uses dynamic Python access, so Ren cannot rule "
                   f"out {_COPYGRADES_TRAP}, which replaces the target's "
                   "whole grade, reports success, and versions nothing to "
                   "go back to")
    else:
        message = (f"script calls {_COPYGRADES_TRAP}, which replaces the "
                   "target's whole grade, reports success, and versions "
                   "nothing to go back to")
    fix = f"{TOOL} run --unsafe --acknowledge-copy-grades"
    if purpose:
        fix += f' --purpose "{purpose}"'
    return message + " - --unsafe declares a write, not this one.", fix


def _run_cell(value, full: bool) -> tuple:
    """One result cell under the same truncation discipline as markers.

    Returns `(text, truncated)`: `text` is already truncated unless
    `full`, so `table()` quoting is the only further shaping.
    """
    text = _cell(value)
    if full or len(text) <= NOTE_PREVIEW_CHARS:
        return text, False
    return (f"{text[:NOTE_PREVIEW_CHARS]}... "
            f"(truncated, {len(text)} chars total - use --full)"), True


def _run_parts(result, full: bool, as_json: bool) -> list:
    """Render a `run` script's `result` as TOON (or JSON on request)."""
    if as_json:
        try:
            return [json.dumps(result, indent=2, default=str,
                               ensure_ascii=False)]
        except (TypeError, ValueError) as exc:
            raise AxiError(f"result would not serialize to JSON ({exc}).",
                           f"{TOOL} run --help") from exc
    if result is None:
        return ["result: (no result - set `result = ...` in the script)"]
    if isinstance(result, dict):
        pairs, truncated = {}, 0
        for key, value in result.items():
            text, cut = _run_cell(value, full)
            pairs[key] = text
            truncated += cut
        meta = (f"{truncated} cells truncated - re-run with --full"
                if truncated and not full else "")
        return [kv_block("result", pairs), meta]
    if isinstance(result, (list, tuple)):
        rows = list(result)
        if not rows:
            return ["result: 0 rows"]
        if all(isinstance(row, dict) for row in rows):
            cols = []
            for row in rows:
                for key in row:
                    if key not in cols:
                        cols.append(key)
            overflow = ""
            if len(cols) > RUN_MAX_COLUMNS:
                overflow = (f"+{len(cols) - RUN_MAX_COLUMNS} more "
                            f"columns ({cols[RUN_MAX_COLUMNS:]}) - "
                            f"use --json for the wide shape")
                cols = cols[:RUN_MAX_COLUMNS]
            shaped, truncated = [], 0
            for row in rows:
                shaped_row = {}
                for col in cols:
                    text, cut = _run_cell(row.get(col), full)
                    shaped_row[col] = text
                    truncated += cut
                shaped.append(shaped_row)
            meta = (f"{truncated} cells truncated - re-run with --full"
                    if truncated and not full else "")
            return [table("result", shaped, cols), overflow, meta]
        shaped, truncated = [], 0
        for value in rows:
            text, cut = _run_cell(value, full)
            shaped.append({"value": text})
            truncated += cut
        meta = (f"{truncated} cells truncated - re-run with --full"
                if truncated and not full else "")
        return [table("result", shaped, ["value"]), meta]
    text, cut = _run_cell(result, full)
    return [kv_block("result", {"value": text}),
            ("re-run with --full for the whole value"
             if cut and not full else "")]


def _workflow_store_scope(args, project_root=None) -> str:
    selected = getattr(args, "scope", "auto") or "auto"
    if selected != "auto":
        return selected
    return "project" if project_root is not None else "user"


def _workflow_project_root(args):
    from library.tools import resolve_script_store

    return resolve_script_store.project_root_from(
        os.getcwd(), getattr(args, "project_dir", "") or "")


def cmd_workflow_candidates(args) -> int:
    from library.tools import resolve_script_store

    try:
        project_root = _workflow_project_root(args)
        scope = _workflow_store_scope(args, project_root)
        rows = resolve_script_store.candidates(scope, project_root)
    except Exception as exc:
        return fail(str(exc), f"{TOOL} workflow candidates --help")
    emit([table("candidates", rows,
                ["candidate_id", "matched_by", "occurrences",
                 "script_variants", "purposes", "sample_run_id"]),
          help_block([f"{TOOL} workflow promote --help"])])
    return 0


def cmd_workflow_list(args) -> int:
    from library.tools import resolve_script_store

    try:
        project_root = _workflow_project_root(args)
        scope = _workflow_store_scope(args, project_root)
        rows = resolve_script_store.workflows(scope, project_root)
    except Exception as exc:
        return fail(str(exc), f"{TOOL} workflow list --help")
    shaped = [{"name": row["name"], "scope": row["scope"],
               "purpose": row["purpose"],
               "promoted_from": row["promoted_from"]["run_id"]}
              for row in rows]
    emit([table("workflows", shaped,
                ["name", "scope", "purpose", "promoted_from"]),
          help_block([f"{TOOL} workflow run <name>"])])
    return 0


def cmd_workflow_promote(args) -> int:
    from library.tools import resolve_script_store
    from library.tools.resolve_lock import default_owner

    try:
        project_root = _workflow_project_root(args)
        scope = _workflow_store_scope(args, project_root)
        path, commit = resolve_script_store.promote(
            scope, project_root, args.candidate_id, args.run_id,
            args.name, default_owner())
    except Exception as exc:
        return fail(str(exc), f"{TOOL} workflow candidates")
    emit([kv_block("workflow", {
        "name": args.name, "scope": scope, "path": path,
        "project_store_commit": commit.get("committed", False),
        "commit_note": commit.get("reason", ""),
    }), help_block([f"{TOOL} workflow run {args.name}"])])
    return 0


class _NoProjectSwitchManager:
    """A script-facing manager view that refuses project lifecycle calls."""

    _BLOCKED = frozenset({"LoadProject", "CreateProject", "DeleteProject",
                           "CloseProject", "OpenProject", "ImportProject"})

    def __init__(self, manager):
        self._manager = manager

    def __getattr__(self, name):
        if name in self._BLOCKED:
            def refuse(*_args, **_kwargs):
                raise AxiError(
                    f"recorded scripts cannot call ProjectManager.{name}; "
                    "Resolve's open project is restored and project switches "
                    "are not part of this run.", fix="run the ren-side command for this instead of a recorded script")
            return refuse
        return getattr(self._manager, name)


class _NoProjectSwitchResolve:
    """Pass through Resolve calls while guarding project-manager access."""

    def __init__(self, resolve):
        self._resolve = resolve

    def __getattr__(self, name):
        if name == "GetProjectManager":
            return lambda: _NoProjectSwitchManager(
                self._resolve.GetProjectManager())
        if name in {"OpenProject", "CreateProject", "LoadProject"}:
            def refuse(*_args, **_kwargs):
                raise AxiError(
                    f"recorded scripts cannot call Resolve.{name}; project "
                    "switches are refused", fix="run the command against the project already open in Resolve")
            return refuse
        return getattr(self._resolve, name)


def _cmd_run_recorded(args) -> int:
    """Execute a purpose-named script and durably file its exact run."""
    import copy
    from library.tools import resolve_script_store

    workflow_name = getattr(args, "workflow_name", "") or ""
    purpose = (getattr(args, "purpose", "") or "").strip()
    selected_scope = getattr(args, "scope", "auto") or "auto"
    try:
        project_root = resolve_script_store.project_root_from(
            os.getcwd(), getattr(args, "project_dir", "") or "")
        workflow = None
        scope = selected_scope
        if workflow_name:
            scopes = ([scope] if scope != "auto" else
                      (["project", "user"] if project_root else ["user"]))
            last_error = None
            for candidate_scope in scopes:
                try:
                    workflow = resolve_script_store.load_workflow(
                        candidate_scope, project_root, workflow_name)
                    scope = candidate_scope
                    break
                except resolve_script_store.ScriptStoreError as exc:
                    last_error = exc
            if workflow is None:
                raise last_error or resolve_script_store.ScriptStoreError(
                    f"no workflow named {workflow_name!r}")
            if purpose and purpose != workflow["purpose"]:
                raise resolve_script_store.ScriptStoreError(
                    "a saved workflow's purpose cannot be overridden")
            purpose = workflow["purpose"]
        if not purpose:
            raise resolve_script_store.ScriptStoreError(
                "a recorded Resolve script needs --purpose")
        if len(purpose) > 200 or "\n" in purpose:
            raise resolve_script_store.ScriptStoreError(
                "--purpose needs one short line of at most 200 characters")
        if scope == "auto":
            scope = "project" if project_root else "user"
        if scope == "project" and project_root is None:
            raise resolve_script_store.ScriptStoreError(
                "project scope needs a Ren project directory; pass "
                "--project-dir or run from inside the project")
        if getattr(args, "borrow", False):
            raise resolve_script_store.ScriptStoreError(
                "recorded scripts cannot use --borrow; they may not switch "
                "Resolve projects")

        if workflow is not None:
            if (getattr(args, "script", "") or getattr(args, "file", "")
                    or getattr(args, "script_pos", "")):
                raise resolve_script_store.ScriptStoreError(
                    "workflow run takes its script from the saved workflow")
            script = workflow["script"]
        elif getattr(args, "script", "") and getattr(args, "file", ""):
            raise resolve_script_store.ScriptStoreError(
                "pass --script or --file, not both")
        elif getattr(args, "script_pos", "") and (
                getattr(args, "script", "") or getattr(args, "file", "")):
            raise resolve_script_store.ScriptStoreError(
                "pass the script positionally or with --script/--file, not both")
        elif getattr(args, "file", ""):
            with open(args.file, encoding="utf-8") as handle:
                script = handle.read()
        elif getattr(args, "script", ""):
            script = args.script
        elif getattr(args, "script_pos", ""):
            script = args.script_pos
        else:
            raise resolve_script_store.ScriptStoreError(
                "run needs a script, --file, --script, or a named workflow")
    except (OSError, resolve_script_store.ScriptStoreError) as exc:
        return fail(str(exc), f"{TOOL} run --help")

    try:
        refused = _refused_resolve_writes(script)
        tree = ast.parse(script)
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    except SyntaxError as exc:
        return fail(f"script would not parse ({exc}).", f"{TOOL} run --help")
    # `--unsafe` is the caller's declaration that a dynamic or indirect
    # Resolve writer may run. Treat it as a write even when static analysis
    # cannot name the mutator, so it still gets a recovery archive and diff.
    write_mode = bool(refused or getattr(args, "unsafe", False))
    if write_mode and not getattr(args, "unsafe", False):
        return fail(
            f"script calls Resolve writers ({', '.join(refused)}) - "
            "run is read-only by default.",
            f"{TOOL} run --unsafe --purpose \"{purpose}\" to declare the write")
    if (getattr(args, "unsafe", False)
            and not getattr(args, "acknowledge_copy_grades", False)
            and _names_copygrades(script)):
        message, fix = _copygrades_refusal(script, purpose)
        return fail(message, fix)
    switch_names = {"LoadProject", "CreateProject", "DeleteProject",
                    "CloseProject", "OpenProject", "ImportProject",
                    "open_project"}
    switch_calls = {node.attr for node in ast.walk(tree)
                    if isinstance(node, ast.Attribute)
                    and node.attr in switch_names}
    switch_calls.update(
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open_project")
    switch_calls.update(
        node.args[1].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "getattr" and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in switch_names)
    if switch_calls:
        return fail("recorded scripts may not switch Resolve projects "
                    f"(found {', '.join(sorted(switch_calls))}).",
                    f"{TOOL} run --help")

    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)

    run_id = resolve_script_store.new_run_id()
    started = datetime.now(timezone.utc).isoformat()
    caller = (_RECORDED_CALLER.get()
              or os.environ.get("REN_RESOLVED_CALLER", ""))
    if not caller:
        from library.tools.resolve_lock import default_owner
        caller = default_owner()
    script_hash = resolve_script_store.normalized_script_hash(script)
    drt_path = ""
    backup_receipt = None
    backup_verified = False
    target_timeline_id = ""
    project_before = None
    timeline_before = None
    timeline_after = None
    timeline_changes = None
    project_change = None
    original_project = ""
    original_timeline_id = None
    original_timeline_name = None
    original_page = None
    original_project_folder = ()
    project_name = ""
    timeline_name = ""
    note = ""
    inner_out, inner_err = io.StringIO(), io.StringIO()
    exit_code = 1
    outcome_error = ""
    switched = False
    restore_error = ""

    with _lease(exclusive=True):
        project = _project(resolve, getattr(args, "project", ""))
        try:
            timeline, is_current, note = _target_timeline(
                project, getattr(args, "timeline", ""), resolve)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        project_name = project.GetName()
        timeline_name = timeline.GetName()
        try:
            target_timeline_id = str(timeline.GetUniqueId() or "")
        except Exception:
            target_timeline_id = ""
        try:
            current = project.GetCurrentTimeline()
            original_project = project_name
            original_timeline_id = current.GetUniqueId() if current else None
            original_timeline_name = current.GetName() if current else None
            original_page = resolve.GetCurrentPage()
            from library.tools.qualification_project import (
                _current_project_folder_path,
            )
            original_project_folder = _current_project_folder_path(
                resolve.GetProjectManager())
        except Exception as exc:
            return fail(f"could not capture Resolve project identity ({exc}).",
                        f"{TOOL} cursor")

        if write_mode:
            if not is_current:
                return fail("a recorded writing script must target the current "
                            "timeline so its backup and inventory read the "
                            "exact live picture.", f"{TOOL} cursor")
            if project_root is None:
                return fail("a recorded writing script needs a Ren project "
                            "directory for its .drt backup; pass --project-dir.",
                            f"{TOOL} run --help")
            try:
                from library.tools import reel_replace_guard, timeline_shadow
                project_before = reel_replace_guard.timeline_inventory(project)
                timeline_before = timeline_shadow.read_live(
                    project, timeline, project_root)
                from library.tools.versions import store as version_store
                backup = version_store.export_timeline_recovery_archive(
                    resolve, timeline, timeline_name, str(project_root),
                    label=f"resolve-axi run {run_id}",
                    archive_suffix=f"custom-{run_id}")
                if not backup.get("archived"):
                    return fail(f"no .drt backup ({backup.get('reason', '')}) - "
                                "refusing before the script runs.",
                                f"{TOOL} run --help")
                drt_path = backup["path"]
                # VC-002: a backup that is not a readable Resolve
                # export of ONE timeline is not a backup. It is read
                # back, and the media it plays is named and hashed, so
                # the run record says what could be recovered.
                from library.tools.versions import replaced_generation
                try:
                    backup_receipt = replaced_generation.read_archive_receipt(
                        drt_path, timeline_name=timeline_name,
                        timeline_id=target_timeline_id,
                        project_name=project_name)
                except replaced_generation.ReplacedGenerationArchiveRefused as refused:
                    return fail(
                        f"the .drt backup could not be read back "
                        f"({refused}) - refusing before the script runs.",
                        f"{TOOL} run --help")
                backup_verified = True
            except Exception as exc:
                return fail(f"could not back up and inventory the target "
                            f"timeline ({exc}).", f"{TOOL} run --help")

        # The recorded path is exclusive even for read scripts, so an
        # unexpected project switch can be restored before the broker yields.
        core_args = copy.copy(args)
        core_args.script = script
        core_args.file = ""
        core_args.script_pos = ""
        core_args.workflow_name = ""
        core_args.purpose = ""
        core_args.borrow = False
        core_args.unsafe = True
        token = _RECORDED_RUN_CONTEXT.set(True)
        try:
            with contextlib.redirect_stdout(inner_out), contextlib.redirect_stderr(inner_err):
                try:
                    exit_code = cmd_run(core_args)
                except SystemExit as exc:
                    exit_code = exc.code if isinstance(exc.code, int) else 1
                    outcome_error = (
                        f"script runner exited via SystemExit({exc.code})")
                    inner_err.write(outcome_error + "\n")
                except Exception as exc:  # preserve the failure in the run record
                    exit_code = 1
                    outcome_error = (
                        f"script runner raised {type(exc).__name__}: {exc}")
                    inner_err.write(outcome_error + "\n")
        finally:
            _RECORDED_RUN_CONTEXT.reset(token)

        try:
            live_manager = resolve.GetProjectManager()
            live_project = live_manager.GetCurrentProject()
            live_name = live_project.GetName() if live_project else ""
            original_id = ""
            live_id = ""
            try:
                original_id = str(project.GetUniqueId() or "")
            except Exception:
                pass
            try:
                live_id = str(live_project.GetUniqueId() or "")
            except Exception:
                pass
            switched = (live_project is None or live_name != original_project
                        or bool(original_id and live_id
                                and original_id != live_id))
            if switched:
                from library.tools.qualification_project import restore
                restore(resolve, {"project": original_project,
                                  "project_folder": original_project_folder,
                                  "timeline_id": original_timeline_id,
                                  "timeline": original_timeline_name,
                                  "page": original_page})
                outcome_error = (
                    "script switched the Resolve project; Ren restored the "
                    "original project and timeline")
        except Exception as exc:
            restore_error = f"project restore failed ({exc})"
            outcome_error = restore_error

        if write_mode and not restore_error:
            try:
                from library.tools import feedback_patch_apply, reel_replace_guard, timeline_shadow
                live_project = resolve.GetProjectManager().GetCurrentProject()
                target_after = None
                for index in range(1, int(live_project.GetTimelineCount() or 0) + 1):
                    item = live_project.GetTimelineByIndex(index)
                    if item and str(item.GetUniqueId() or "") == target_timeline_id:
                        target_after = item
                        break
                project_after = reel_replace_guard.timeline_inventory(live_project)
                project_change = resolve_script_store.project_inventory_diff(
                    project_before, project_after, timeline_name,
                    target_timeline_id)
                if target_after is not None:
                    from library.tools.resolve_lock import cursor_excursion
                    with cursor_excursion(live_project, target_after,
                                          f"resolve-axi inventory {run_id}"):
                        timeline_after = timeline_shadow.read_live(
                            live_project, target_after, project_root)
                    timeline_changes = feedback_patch_apply.inventory_changes(
                        timeline_before, timeline_after)
                if (not project_change["guard_passed"] or target_after is None):
                    outcome_error = (outcome_error + "; " if outcome_error else "") + (
                        "project inventory guard found a timeline added, "
                        "removed, renamed, or changed outside the target")
            except Exception as exc:
                outcome_error = (outcome_error + "; " if outcome_error else "") + (
                    f"inventory guard could not finish ({exc})")

    if exit_code != 0:
        outcome_error = (outcome_error + "; " if outcome_error else "") + (
            inner_err.getvalue() or inner_out.getvalue() or
            f"script exited {exit_code}")
    record = {
        "run_id": run_id,
        "at": started,
        "scope": scope,
        "purpose": purpose,
        "purpose_hash": resolve_script_store.purpose_hash(purpose),
        "script_hash": script_hash,
        "script": script,
        "project": project_name,
        "timeline": timeline_name,
        "caller": caller,
        "writing": write_mode,
        "workflow": workflow_name or None,
        "drt_backup": drt_path or None,
        "drt_backup_verified": backup_verified,
        "recovery_archive": backup_receipt,
        "project_switched": switched,
        "project_restored": switched and not restore_error,
        "inventory": ({
            "timeline": {"before": timeline_before,
                         "after": timeline_after,
                         "changes": timeline_changes},
            "project": project_change,
        } if write_mode else None),
        "result": inner_out.getvalue() if exit_code == 0 else None,
        "error": outcome_error or None,
    }
    try:
        record_path, project_commit = resolve_script_store.save_run(
            scope, record, project_root,
            (Path(drt_path),) if drt_path else ())
        rows = resolve_script_store.candidates(
            scope, project_root if scope == "project" else None)
        candidate = next((row for row in rows if row["candidate_id"] in {
            f"script-{script_hash[:16]}",
            f"purpose-{resolve_script_store.purpose_hash(purpose)[:16]}",
        }), None)
    except Exception as exc:
        outcome_error = (outcome_error + "; " if outcome_error else "") + (
            f"run executed but durable recording failed ({exc})")
        record_path, project_commit, candidate = "", {}, None

    if inner_out.getvalue():
        sys.stdout.write(inner_out.getvalue())
    if inner_err.getvalue():
        sys.stderr.write(inner_err.getvalue())
    emit([kv_block("recorded", {
        "run_id": run_id,
        "scope": scope,
        "project": project_name,
        "timeline": timeline_name,
        "caller": caller,
        "path": record_path,
        "project_store_commit": project_commit.get("committed", False),
        "store_note": project_commit.get("reason", ""),
        "drt_backup": drt_path,
        "inventory_guard": (project_change.get("guard_passed", False)
                            if project_change else "not-writing"),
        "workflow_candidate": candidate["candidate_id"] if candidate else "no-repeat-yet",
    })])
    if outcome_error:
        return fail(outcome_error, f"{TOOL} workflow candidates")
    return exit_code


def cmd_run(args) -> int:
    """Execute a caller script with ready Resolve names, render `result`.

    The point is NOT to enumerate the scripting API: it is that an
    arbitrary script stops costing arbitrary tokens. The script runs
    with exactly `RUN_SCOPE` in scope (documented above and in
    `--help`), and whatever it leaves in `result` renders as TOON rows
    under the same truncation discipline as every other command
    (`--full` and `--json` escape it).

    Safety, stated plainly:

    - Default is a READ: the script is AST-scanned for Resolve writers
      (`_RUN_WRITE_PREFIXES`) and refused loudly when one appears.
    - `--unsafe` is the declared write path: it skips the refusal,
      holds the Resolve lease EXCLUSIVE, and reports the cursor before
      and after (an arbitrary script owns its own cursor, so there is
      nothing to assert it against). Reads must still never move the
      cursor, and nothing in this module moves it either way.
    - `--unsafe` still refuses `CopyGrades` without
      `--acknowledge-copy-grades`: that call destroys the target's
      grade while reporting success.
    - A recorded `run --purpose` is available in Ren runtime and saves
      its run record. A run without `--purpose` still needs developer
      opt-in (`RESOLVE_AXI_ALLOW_RUN=1`).
    - `--borrow` runs a project-switching one-off the way a lane must:
      the captain's project is saved and restored around the script
      (`qualification_project.borrowed`), so it needs `--unsafe` (the
      exclusive lease). Inside, scripts switch projects with the
      scope-provided `open_project(name)` - live-handle check, switch
      settle, readback - never a raw `manager.LoadProject`.
    """
    if (getattr(args, "purpose", "")
            or getattr(args, "workflow_name", "")):
        return _cmd_run_recorded(args)
    if not run_script_enabled():
        message, fix = _run_opt_in_refusal()
        return fail(message, fix)
    use_borrow = bool(getattr(args, "borrow", False))
    if use_borrow and not args.unsafe:
        return fail(
            "`--borrow` saves and restores the captain's project around "
            "a project-switching script, so it needs the exclusive lease.",
            f"{TOOL} run --file <script> --borrow --unsafe "
            f"(omit --project/--timeline; the script opens what it needs "
            f"with `open_project(name)`)")
    if args.script and args.file:
        return fail("pass --script or --file, not both.",
                    f"{TOOL} run --help")
    if args.script_pos and (args.script or args.file):
        return fail("pass the script positionally or with --script/--file,"
                    " not both.",
                    f"{TOOL} run --help")
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                script = handle.read()
        except OSError as exc:
            return fail(f"cannot read script file {args.file!r} ({exc}).",
                        f"{TOOL} run --script \"result = ...\"")
        unsafe_form = f"--file {args.file}"
    elif args.script:
        script = args.script
        unsafe_form = "--script \"...\""
    elif args.script_pos:
        script = args.script_pos
        unsafe_form = "\"...\""
    else:
        return fail("run needs a script: `run \"result = ...\"`, --script "
                    "\"result = ...\", or --file <path>.",
                    f"{TOOL} run --help")
    try:
        refused = _refused_resolve_writes(script)
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    if refused and not args.unsafe:
        timeline_flag = (f"--timeline \"{args.timeline}\" "
                         if args.timeline else "")
        return fail(
            f"script calls Resolve writers ({', '.join(refused)}) - "
            f"run is read-only by default.",
            f"{TOOL} run {timeline_flag}{unsafe_form} --unsafe "
            f"to declare the write")
    if (args.unsafe and not args.acknowledge_copy_grades
            and _names_copygrades(script)):
        timeline_flag = (f"--timeline \"{args.timeline}\" "
                         if args.timeline else "")
        message, fix = _copygrades_refusal(script)
        return fail(message, f"{TOOL} run {timeline_flag}{unsafe_form} "
                    f"{fix.split(TOOL + ' run ', 1)[-1]} to say you know "
                    "what it does")
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.unsafe):
        if use_borrow:
            from library.tools.qualification_project import borrowed as _borrowed
            borrow_ctx = _borrowed(resolve)
        else:
            borrow_ctx = contextlib.nullcontext()
        with borrow_ctx:
            project = _project(resolve, args.project)
            try:
                timeline, is_current, note = _target_timeline(
                    project, args.timeline, resolve)
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
            from library.tools import marker_feedback
            from library.tools.marker_feedback import current_timeline
            try:
                open_timeline, _ = current_timeline(resolve)
                cursor_before = open_timeline.GetName()
            except Exception:
                cursor_before = "(none open)"
            recorded = _RECORDED_RUN_CONTEXT.get()
            script_resolve = (_NoProjectSwitchResolve(resolve)
                              if recorded else resolve)
            script_manager = script_resolve.GetProjectManager()
            def refuse_project_switch(_name):
                raise AxiError(
                    "recorded scripts cannot switch Resolve projects", fix="open the project in Resolve, then run the command against it")
            scope = {
                "resolve": script_resolve,
                "manager": script_manager,
                "project": project,
                "project_name": project.GetName(),
                "timeline": timeline,
                "timeline_name": timeline.GetName(),
                "is_current": is_current,
                "timeline_names": _timeline_names(project),
                "by_index": project.GetTimelineByIndex,
                "read_notes": marker_feedback.read_notes,
                "open_project": (refuse_project_switch if recorded else
                                 lambda name: _run_open_project(
                                     resolve.GetProjectManager(), name)),
            }
            sandbox = dict(scope)
            try:
                exec(compile(script, "<resolve-axi run>", "exec"), sandbox)  # noqa: S102
            except Exception as exc:
                return fail(f"script raised {type(exc).__name__}: {exc}.",
                            f"{TOOL} run --help")
            result = sandbox.get("result")
            try:
                open_after, _ = current_timeline(resolve)
                cursor_after = open_after.GetName()
            except Exception:
                cursor_after = "(none open)"
            try:
                rendered = _run_parts(result, args.full, args.json)
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    try:
        _meta_name = timeline.GetName()
    except Exception:
        _meta_name = None
    if _meta_name is None:
        # The script switched projects mid-run (a setup/teardown
        # script legitimately does), so the held timeline proxy is
        # stale and reads None. Crash the after-report on that and
        # the script's own result is lost with it - read the cursor
        # fresh instead, best effort.
        try:
            _fresh_proj = (_connect().GetProjectManager()
                           .GetCurrentProject())
            _meta_name = _fresh_proj.GetCurrentTimeline().GetName()
            _meta_current = True
        except Exception:
            _meta_name, _meta_current = "(unknown)", False
    else:
        _meta_current = is_current
    meta = {"timeline": (_meta_name or "(unknown)") +
            (" (current)" if _meta_current else ""),
            "unsafe": "yes" if args.unsafe else "no"}
    if args.unsafe:
        meta["cursor_before"] = cursor_before
        meta["cursor_after"] = cursor_after
        meta["cursor_moved"] = ("yes" if cursor_before != cursor_after
                                else "no")
    out = [kv_block("run", meta), note] + rendered
    if args.unsafe and cursor_before != cursor_after:
        out.append(f"cursor moved: {cursor_before!r} -> "
                   f"{cursor_after!r}")
    out.append(help_block([f"{TOOL} timeline list",
                           f"{TOOL} markers --timeline "
                           f"\"{_meta_name or ''}\""]))
    emit(out)
    return 0


# ── writes: first-class mutating verbs ─────────────────────────
#
# Reads stop at `run --unsafe`'s escape hatch; these verbs are the
# control surface for the mutations agents actually perform, grouped
# small (`edit`, `ingest`, `render`, `project set`,
# `timeline duplicate`) instead of one command per API method.
#
# Every verb shares one discipline, stated once:
#
# - Dry-run by default: the plan prints and nothing is written.
#   `--apply` is the explicit flag that writes.
# - Reads hold the Resolve lease SHARED; writes hold it EXCLUSIVE.
# - Timeline-scoped writes ASSERT the cursor sits on the reel first
#   (the restore/reply shape) - `AppendToTimeline` and `AddRenderJob`
#   act on the CURRENT timeline, so writing while the cursor sits
#   elsewhere would land somewhere unasked.
# - Project-scoped writes (`ingest`, `project set`, `timeline
#   duplicate`, `render stop`) report the cursor before and after
#   instead: they depend on nothing positional, and the report proves
#   they moved nothing.
# - Every write is read back, and the read-back decides the exit
#   code - never the API's return value alone. Two measured traps
#   from the comparison that scoped this work are implemented here:
#   an append is never retried (a retry after a landed append places
#   the clip twice), and a delete that answers False is re-read
#   before any retry, and retried at most once.
# - `CopyGrades` is used only to carry an item's captured grade through a
#   verified replacement; the `run` guard still protects arbitrary scripts.
# - Write verbs keep explicit flags by design. The bare-positional
#   rule covers reads; a destructive path that accepts a bare
#   timeline name is one typo from the wrong reel.


import re as _re

#: `--track video1`: the only addressing a write accepts. A bare
#: position on a track (`--index 0`) resolves against the live item
#: list and the dry run names what it found, so a shifted timeline
#: cannot silently retarget the write.
_EDIT_TRACK_RE = _re.compile(r"^(video|audio|subtitle)(\d+)$")

#: Title-text property keys, plain text first. `SetProperty` exposes
#: no title key on every build; when none takes, the verb refuses and
#: names the Fusion-comp route instead of guessing at one.
_TITLE_KEYS = ("Styled Text", "StyledText", "Text", "Rich Text")


def _parse_track(spec: str) -> tuple:
    match = _EDIT_TRACK_RE.match(spec or "")
    if not match:
        raise AxiError(
            f"bad track {spec!r} - tracks read video1, audio2, ...",
            f"{TOOL} items --timeline \"<name>\"")
    return match.group(1), int(match.group(2))


def _cursor_name(resolve) -> str:
    from library.tools.marker_feedback import current_timeline
    try:
        open_timeline, _ = current_timeline(resolve)
        return open_timeline.GetName()
    except Exception:
        return "(none open)"


def _assert_cursor_on(resolve, timeline_name: str) -> None:
    """Refuse unless the cursor already sits on the reel.

    Appends and render queueing act on the CURRENT timeline: writing
    while the cursor sits elsewhere lands somewhere unasked. This
    asserts rather than moving, the way the marker writes do.
    """
    cursor = _cursor_name(resolve)
    if cursor != timeline_name:
        raise AxiError(
            f"the cursor sits on {cursor!r}, not {timeline_name!r} - "
            f"a write asserts the cursor rather than depending on "
            f"it. Open the reel and re-run.",
            f"{TOOL} cursor --expect \"{timeline_name}\"")


def _edit_item(timeline, track_spec: str, index: int):
    """The live timeline item at `--track`/`--index`, or a refusal.

    Returns `(item, track_type, track_index, uid)`. An index past the
    end is refused with the track's size - the dry run then names
    what a corrected index would hit.
    """
    track_type, track_index = _parse_track(track_spec)
    if index is None:
        raise AxiError(
            f"{track_type}{track_index} names the track - pass --index "
            f"for the position on it (0-based).",
            f"{TOOL} items --timeline \"{timeline.GetName()}\"")
    try:
        items = (timeline.GetItemListInTrack(track_type, track_index)
                 or [])
    except Exception as exc:
        raise AxiError(
            f"timeline {timeline.GetName()!r} would not read "
            f"{track_type}{track_index} ({exc}).",
            f"{TOOL} items --timeline \"{timeline.GetName()}\"") from exc
    if index < 0 or index >= len(items):
        raise AxiError(
            f"{track_type}{track_index} holds {len(items)} item(s) - "
            f"index {index} names nothing.",
            f"{TOOL} items --timeline \"{timeline.GetName()}\"")
    item = items[index]
    try:
        uid = item.GetUniqueId()
    except Exception:
        uid = ""
    return item, track_type, track_index, uid


def _edit_item_by_identity(timeline, args):
    """Resolve a live item by stable Resolve id and verify its media span.

    The track/index pair remains in the command for old callers and display,
    but semantic AXI adapters supply this identity so preceding structural
    mutations cannot redirect a later write to the old position.
    """
    unique_id = getattr(args, "target_unique_id", None)
    if not unique_id:
        return _edit_item(timeline, args.track, args.index)
    required = ("expected_source_file", "expected_source_in_frame",
                "expected_source_out_frame", "expected_record_in_frame",
                "expected_record_out_frame")
    missing = [name for name in required if getattr(args, name, None) is None]
    if missing:
        raise AxiError(
            "stable item targeting requires media identity fields: "
            + ", ".join(missing), fix="supply the media identity the target needs (source file and source range), then re-run")
    track_type, _track_index = _parse_track(args.track)
    selector = {
        "resolve_item_id": str(unique_id),
        "source_file": str(args.expected_source_file),
        "source_in_frame": int(args.expected_source_in_frame),
        "source_out_frame": int(args.expected_source_out_frame),
        "track_type": track_type,
    }
    from library.tools import timeline_addressing
    try:
        address = timeline_addressing.resolve_item(timeline, selector)
        timeline_addressing.verify_item(timeline, address)
    except timeline_addressing.AddressingError as exc:
        raise AxiError(f"stable target revalidation failed: {exc}", fix="re-run once Resolve answers the target read; nothing was written") from exc
    try:
        items = timeline.GetItemListInTrack(address.track_type,
                                            address.track_index) or []
        matches = [(index, item) for index, item in enumerate(items)
                   if str(item.GetUniqueId() or "") == address.resolve_item_id]
    except Exception as exc:
        raise AxiError(f"could not re-read the stable target position: {exc}", fix="re-run once Resolve answers the target read; nothing was written") from exc
    if len(matches) != 1:
        raise AxiError(
            f"stable target {unique_id!r} occupies {len(matches)} live positions", fix="the target moved: re-read the timeline, state the item by its current position, then re-run")
    index, item = matches[0]
    span = _item_span(item)
    if (int(span["record_in"]) != int(args.expected_record_in_frame)
            or int(span["record_out"]) != int(args.expected_record_out_frame)):
        raise AxiError(
            f"stable target {unique_id!r} changed record span from "
            f"[{args.expected_record_in_frame}, {args.expected_record_out_frame}] "
            f"to [{span['record_in']}, {span['record_out']}] since preparation", fix="the target moved: re-read the timeline, state the item's current record span, then re-run")
    args.index = index
    return item, address.track_type, address.track_index, address.resolve_item_id


def _item_span(item) -> dict:
    """The span a dry run names and a read-back checks. Best effort
    per field: a verb refuses on the fields it needs, never on a
    neighbour's blank.

    Read through `getattr`, never `item.__getattribute__`: on
    Resolve's scripting proxies the dunder lookup answers None for
    the method, so calling it raises `TypeError: 'NoneType' object
    is not callable` and every field reads blank - trim and move
    then refuse ("would not report its spans") and every verb prints
    `name: ''` (finding 6). `getattr` serves the bound method."""
    span: dict = {"name": "", "record_in": "", "record_out": "",
                   "source_in": "", "source_out": "", "uid": ""}
    for key, method in (("name", "GetName"),
                        ("record_in", "GetStart"),
                        ("record_out", "GetEnd"),
                        ("source_in", "GetSourceStartFrame"),
                        ("source_out", "GetSourceEndFrame"),
                        ("uid", "GetUniqueId")):
        try:
            span[key] = getattr(item, method)()
        except Exception:
            continue
    return span


def _pool_file_name(item):
    """The media pool file backing one timeline item, or None.

    None covers a proxy that does not serve the calls and an item
    with no pool file (a generator, a Fusion comp) - the caller
    refuses linkage it cannot prove rather than guessing.
    """
    try:
        mpi = item.GetMediaPoolItem()
    except Exception:
        return None
    if mpi is None:
        return None
    try:
        return mpi.GetClipProperty("File Name")
    except Exception:
        return None


def _linked_audio_for_speed(timeline, item, span) -> tuple:
    """The audio items one speed write must carry, selected, not guessed.

    Finding 17: a retimed talking shot without its dialogue loses
    sync. Linked is a same-span (within 1.5 frames) audio item from
    the SAME source file - the bed shares spans, never sources, so a
    span match alone would retime the music too.

    Returns (linked, skipped, refused, unchecked): `linked` the items
    to retime, `skipped` the names left at full speed (same span,
    different source), `refused` the failure naming what would not
    read beside real candidates (the caller fails rather than
    guessing), `unchecked` the note where the span itself would not
    read so there was nothing to match against (the caller says the
    audio was NOT checked). No exception escapes; the dry run names
    and the apply path retimes-or-refuses off the same answer.
    """
    try:
        v_in = float(span["record_in"])
        v_out = float(span["record_out"])
    except (TypeError, ValueError, KeyError):
        return [], [], "", ("the video span would not read - verify "
                            "the audio by hand")
    try:
        n_audio = timeline.GetTrackCount("audio") or 0
    except Exception:
        n_audio = 0
    cands = []
    for track in range(1, n_audio + 1):
        try:
            cands.extend(timeline.GetItemListInTrack("audio", track)
                         or [])
        except Exception:
            continue
    same = []
    for cand in cands:
        try:
            if (abs(float(cand.GetStart()) - v_in) <= 1.5
                    and abs(float(cand.GetStart())
                            + float(cand.GetDuration()) - v_out) <= 1.5):
                same.append(cand)
        except Exception:
            continue
    if not same:
        return [], [], "", ""
    v_src = _pool_file_name(item)
    if v_src is None:
        return [], [], (
            f"{len(same)} same-span audio item(s) overlap the video "
            f"item and its source will not read - refusing to guess "
            f"which audio is linked"), ""
    linked, skipped = [], []
    for cand in same:
        try:
            cand_name = cand.GetName()
        except Exception:
            cand_name = "?"
        cand_src = _pool_file_name(cand)
        if cand_src is None:
            return [], [], (
                f"same-span audio {cand_name!r} will not read its "
                f"source - refusing to guess linkage"), ""
        if cand_src == v_src:
            linked.append(cand)
        else:
            skipped.append(cand_name)
    return linked, skipped, "", ""


def _presence(timeline, uid: str) -> bool:
    """Whether the unique id still sits anywhere on the timeline."""
    if not uid:
        return False
    for track_type in ("video", "audio", "subtitle"):
        try:
            count = timeline.GetTrackCount(track_type) or 0
        except Exception:
            continue
        for track_index in range(1, count + 1):
            try:
                items = (timeline.GetItemListInTrack(track_type,
                                                     track_index) or [])
            except Exception:
                continue
            for item in items:
                try:
                    if item.GetUniqueId() == uid:
                        return True
                except Exception:
                    continue
    return False


def _pool_find_clip(project, clip_name: str, bin_path: str):
    """The one pool item named `clip_name` under `--bin`, or a refusal.

    An empty bin scopes the search; several hits refuse with the
    candidates instead of guessing. Timelines in the pool are not
    placeable and never match.
    """
    try:
        pool = project.GetMediaPool()
        root = pool.GetRootFolder()
    except Exception as exc:
        raise AxiError(
            f"project {project.GetName()!r} would not open its media "
            f"pool ({exc}).",
            f"{TOOL} pool") from exc
    folder, walked = _resolve_bin(root, bin_path or "")
    if folder is None:
        raise AxiError(
            f"no bin {bin_path!r} under "
            f"{('/' + walked) if walked else 'the pool root'}.",
            f"{TOOL} pool")
    rows, _bins, _skipped = _pool_clip_rows(folder, walked)
    hits = [r for r in rows if r["name"] == clip_name]
    if not hits:
        raise AxiError(
            f"no clip named {clip_name!r} under "
            f"{('/' + walked) if walked else 'the pool root'}.",
            f"{TOOL} pool"
            + (f" \"{walked}\"" if walked else ""))
    if len(hits) > 1:
        bins = sorted({h["bin"] for h in hits})
        raise AxiError(
            f"{clip_name!r} names {len(hits)} clips - pass --bin to "
            f"choose: {bins}.",
            f"{TOOL} pool")
    wanted = hits[0]
    found = []

    def visit(current):
        try:
            clips = current.GetClipList() or []
        except Exception:
            clips = []
        for clip in clips:
            try:
                if clip.GetName() == clip_name:
                    found.append(clip)
            except Exception:
                continue
        try:
            subs = current.GetSubFolderList() or []
        except Exception:
            subs = []
        for sub in subs:
            visit(sub)

    visit(folder)
    for clip in found:
        try:
            props = clip.GetClipProperty() or {}
        except Exception:
            continue
        if isinstance(props, dict) and (props.get("File Path") or ""):
            return clip, wanted
    raise AxiError(
        f"{clip_name!r} is not a placeable source clip.",
        f"{TOOL} pool")


def _pool_find_clip_by_id(project, clip_id: str):
    """The one placeable pool item with this exact unique id.

    `clip.insert` names the pool item by its unique id (the identity the
    algebra carries), while `edit place --clip` names it by its display
    name.  This resolves the id to the SAME item the name would have
    found, so the two never disagree about what is placed: several items
    answering to one id refuse rather than guess.
    """
    try:
        pool = project.GetMediaPool()
        root = pool.GetRootFolder()
    except Exception as exc:
        raise AxiError(
            f"project {project.GetName()!r} would not open its media "
            f"pool ({exc}).",
            f"{TOOL} pool") from exc
    hits = []
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            clips = list(folder.GetClipList() or ())
        except Exception:
            clips = []
        for clip in clips:
            try:
                uid = str(clip.GetUniqueId())
            except Exception:
                continue
            if uid == clip_id:
                hits.append(clip)
        try:
            stack.extend(folder.GetSubFolderList() or ())
        except Exception:
            pass
    if not hits:
        raise AxiError(
            f"no pool item has the unique id {clip_id!r}.",
            f"{TOOL} pool")
    if len(hits) > 1:
        raise AxiError(
            f"the pool item id {clip_id!r} answers to {len(hits)} items - "
            f"refusing to guess which one to place.",
            f"{TOOL} pool")
    clip = hits[0]
    try:
        props = clip.GetClipProperty() or {}
    except Exception:
        props = {}
    if isinstance(props, dict) and not (props.get("File Path") or ""):
        raise AxiError(
            f"the pool item {clip_id!r} is not a placeable source clip.",
            f"{TOOL} pool")
    try:
        name = str(clip.GetName())
    except Exception as exc:
        raise AxiError(
            f"the pool item {clip_id!r} would not report its name ({exc}).",
            f"{TOOL} pool") from exc
    return clip, {"name": name, "bin": ""}


def _item_uid(item) -> str:
    """The item's unique id, or "" when the handle has not caught up."""
    try:
        uid = str(item.GetUniqueId() or "")
    except Exception:  # noqa: BLE001 - an unreadable item has no id
        return ""
    return "" if uid in ("", "None") else uid


def _timeline_items(timeline) -> list:
    """Every item on the timeline, with the row it sits on."""
    rows = []
    for kind in ("video", "audio", "subtitle"):
        try:
            count = int(timeline.GetTrackCount(kind) or 0)
        except Exception:  # noqa: BLE001 - an unreadable row is not a row
            count = 0
        for index in range(1, count + 1):
            try:
                items = list(timeline.GetItemListInTrack(kind, index) or ())
            except Exception:  # noqa: BLE001
                items = []
            for item in items:
                rows.append((kind, index, item))
    return rows


def _timeline_item_uids(timeline) -> set:
    """Every item id on the timeline: the pre-write state, as ids."""
    return {uid for _kind, _index, item in _timeline_items(timeline)
            if (uid := _item_uid(item))}


def _placed_item(timeline, before_uids: set, returned):
    """The item the placement put on the timeline.

    The timeline's own pre/post difference is the first answer, and it
    is the fresh one: the handle `AppendToTimeline` returns may not have
    caught up yet, and a placement that landed with a falsy answer is
    only visible in the timeline's own reading.  The returned handle is
    the fallback, and a placement that cannot be identified either way
    is NOT identified: the caller refuses it.
    """
    from library.tools import resolve_write_settle

    def fresh():
        found = [item for _kind, _index, item in _timeline_items(timeline)
                 if _item_uid(item) and _item_uid(item) not in before_uids]
        return found[0] if len(found) == 1 else None

    resolve_write_settle.wait_for(lambda: fresh() is not None)
    item = fresh()
    if item is not None:
        return item
    candidates = [item for item in (returned or ()) if item is not None]
    return candidates[0] if len(candidates) == 1 else None


def _placed_track(item):
    """The row a placed item sits on, as (kind, index), or None."""
    try:
        kind, index = item.GetTrackTypeAndIndex()
    except Exception:  # noqa: BLE001 - an unreadable row is unknown
        return None
    try:
        return str(kind), int(index)
    except (TypeError, ValueError):
        return None


def _pool_identity(clip) -> dict:
    """The identity of one media-pool item, as the engine names it.

    AXI-013: a mutation is about a THING, and the thing is captured
    BEFORE the write so the write can be judged against it.  The unique
    id is the identity the algebra carries; the name and the file path
    are what a reader can see.
    """
    identity = {"unique_id": "", "name": "", "file": ""}
    if clip is None:
        return identity
    try:
        identity["unique_id"] = str(clip.GetUniqueId() or "")
    except Exception:  # noqa: BLE001 - an unreadable item has no identity
        pass
    try:
        identity["name"] = str(clip.GetName() or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        props = clip.GetClipProperty() or {}
    except Exception:  # noqa: BLE001
        props = {}
    if isinstance(props, dict):
        identity["file"] = str(props.get("File Path") or "")
    return identity


def _placed_pool_identity(item) -> dict:
    """The identity of the media a PLACED timeline item carries.

    The read waits for Resolve's handle to catch up: a freshly appended
    item reports nothing about its media until the handle has caught up
    (`library/tools/resolve_write_settle.py`).
    """
    from library.tools import resolve_write_settle

    def identity() -> dict:
        found = {"unique_id": "", "name": "", "file": ""}
        try:
            pool_item = item.GetMediaPoolItem()
        except Exception:  # noqa: BLE001 - not settled yet
            return found
        if pool_item is None:
            return found
        try:
            found["unique_id"] = str(pool_item.GetUniqueId() or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            found["name"] = str(pool_item.GetName() or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            props = pool_item.GetClipProperty() or {}
        except Exception:  # noqa: BLE001
            props = {}
        if isinstance(props, dict):
            found["file"] = str(props.get("File Path") or "")
        return found

    settled, _waited = resolve_write_settle.wait_for(
        lambda: identity().get("unique_id") or "")
    found = identity()
    found["settled"] = settled
    return found


def cmd_edit_place(args) -> int:
    """Append a pool clip onto the reel. The plan says where; the
    read-back says where it actually landed.

    `AppendToTimeline` acts on the CURRENT timeline, so the cursor is
    asserted first. The call is never retried: a retry after a landed
    append places the clip twice, and the item-count delta tells the
    two cases apart.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        if not args.clip and not getattr(args, "clip_id", ""):
            return fail("place needs --clip or --clip-id.",
                        f"{TOOL} pool")
        if args.clip and getattr(args, "clip_id", ""):
            return fail("place takes --clip or --clip-id, not both.",
                        f"{TOOL} pool")
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            if getattr(args, "clip_id", ""):
                clip, row = _pool_find_clip_by_id(project, args.clip_id)
            else:
                clip, row = _pool_find_clip(project, args.clip,
                                            args.bin or "")
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        # The clip is named by its DISPLAY name everywhere the plan, the
        # journal and the receipt are written, whether it was asked for
        # by name or by its pool unique id.
        clip_name = str(row.get("name") or args.clip or "")
        if args.media not in ("both", "video", "audio"):
            return fail(f"bad --media {args.media!r}: both|video|audio.",
                        f"{TOOL} edit place --help")
        if args.track is not None:
            # The row is NAMED, so the row must exist. Resolve answers an
            # out-of-range track index by placing the clip on a row the
            # request never named, which is a silent substitution of the
            # edit - the thing the punch list forbids.
            track_kind = "audio" if args.media == "audio" else "video"
            try:
                count = int(timeline.GetTrackCount(track_kind) or 0)
            except Exception as exc:
                return fail(f"the {track_kind} rows would not read ({exc}) "
                            "- nothing was placed.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            if args.track < 1 or args.track > count:
                return fail(
                    f"place names {track_kind} track {args.track}, and this "
                    f"timeline has {count} {track_kind} row(s) - nothing "
                    "was placed.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        if args.record is not None and args.record < 0:
            return fail(f"place names record frame {args.record}, which is "
                        "before the start of the timeline - nothing was "
                        "placed.",
                        f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        payload: dict = {"mediaPoolItem": clip}
        if args.source_in is not None:
            payload["startFrame"] = args.source_in
        if args.source_out is not None:
            # AppendToTimeline uses an exclusive end frame. The CLI's
            # source_out is inclusive, like GetSourceEndFrame.
            payload["endFrame"] = args.source_out + 1
        if args.media == "video":
            payload["mediaType"] = 1
        elif args.media == "audio":
            payload["mediaType"] = 2
        if args.track is not None:
            payload["trackIndex"] = args.track
        if args.record is not None:
            payload["recordFrame"] = args.record
        where = (f"track {args.track} at frame {args.record}"
                 if args.track is not None and args.record is not None
                 else "the reel end (Resolve positions it)")
        if not args.apply:
            emit([kv_block("place_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "clip": clip_name,
                      "bin": row["bin"] or "/ (root)",
                      "where": where,
                  }),
                  note,
                  f"dry run - pass --apply to append under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([
                      f"{TOOL} edit place --timeline "
                      f"\"{timeline.GetName()}\" --clip \"{clip_name}\" "
                      f"--apply"])])
            return 0
        before = sum(
            len(timeline.GetItemListInTrack(t, i) or [])
            for t in ("video", "audio", "subtitle")
            for i in range(1, (timeline.GetTrackCount(t) or 0) + 1))
        # AXI-013: the pre-write state, captured so the placement can be
        # judged against it - the item that appears is THE item the
        # request named, on the row it named, at the frame it named.
        before_uids = _timeline_item_uids(timeline)
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "edit_place", "timeline": timeline.GetName(),
                      "clip": clip_name, "media": args.media,
                      "source_in": args.source_in,
                      "source_out": args.source_out,
                      "track": args.track, "record": args.record},
                gate_class="edit_place") as (journal, journal_state):
            # AXI-013: the target identity is captured BEFORE the write,
            # so the write can be judged against the thing it was asked
            # to place rather than against whatever landed.
            requested_identity = _pool_identity(clip)
            try:
                placed = project.GetMediaPool().AppendToTimeline([payload])
            except Exception as exc:
                return fail(f"append answered with an exception ({exc}) - "
                            f"nothing was re-read, so verify by hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            after = sum(
                len(timeline.GetItemListInTrack(t, i) or [])
                for t in ("video", "audio", "subtitle")
                for i in range(1, (timeline.GetTrackCount(t) or 0) + 1))
            delta = after - before
            new_items = list(placed or []) if placed else []
            if delta == 0:
                # The handle may not have caught up yet: a dense reel
                # refreshes slower than a fixture's, and a read taken
                # straight after the append reports a write that landed
                # as one that did not (measured 2026-10-21: the clip WAS
                # on the cover row at the requested frame).
                from library.tools import resolve_write_settle

                after, _settled, _waited = (
                    resolve_write_settle.settled_item_count(
                        lambda: sum(
                            len(timeline.GetItemListInTrack(t, i) or [])
                            for t in ("video", "audio", "subtitle")
                            for i in range(
                                1, (int(timeline.GetTrackCount(t) or 0) + 1))),
                        before))
                delta = after - before
            if delta == 0:
                return fail(f"append reported "
                            f"{'success' if placed else 'nothing'} but the "
                            f"item count did not move ({before} -> {after}) "
                            f"- nothing landed.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            # AXI-013: the item count moving says that SOMETHING was
            # placed, not that the thing the request NAMED was placed.
            # The requested pool item's identity, captured above, is
            # compared against the placed item's own media identity, so
            # a placement of the wrong media is refused instead of
            # reported as a success.
            placed_item = _placed_item(timeline, before_uids, new_items)
            if placed_item is None:
                return fail(
                    "the placement could not be identified on the "
                    "timeline - nothing is claimed. Verify by hand.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            placed_identity = _placed_pool_identity(placed_item)
            if (not placed_identity.get("unique_id")
                    or not requested_identity.get("unique_id")):
                return fail(
                    "the placed item's media identity could not be read "
                    f"(requested {requested_identity.get('unique_id')!r}, "
                    f"placed {placed_identity.get('unique_id')!r}) - the "
                    "placement is not verified. Verify by hand.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            if (placed_identity["unique_id"]
                    != requested_identity["unique_id"]):
                return fail(
                    f"the placement put "
                    f"{placed_identity.get('name') or placed_identity['unique_id']!r} "
                    f"on the timeline, not the requested "
                    f"{requested_identity.get('name') or requested_identity['unique_id']!r} "
                    "- the write changed the wrong item.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            if (requested_identity.get("file")
                    and placed_identity.get("file")
                    and placed_identity["file"] != requested_identity["file"]):
                return fail(
                    f"the placed item carries {placed_identity['file']!r}, "
                    f"not the requested {requested_identity['file']!r} - the "
                    "write changed the wrong media.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            placed_span = _item_span(placed_item)
            if args.record is not None:
                try:
                    landed_at = int(placed_span["record_in"])
                except (KeyError, TypeError, ValueError):
                    landed_at = None
                if landed_at is not None and landed_at != int(args.record):
                    return fail(
                        f"the item landed at record frame {landed_at}, not "
                        f"the requested {int(args.record)} - the write "
                        "changed the wrong place.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
            if args.track is not None:
                placed_track = _placed_track(placed_item)
                if placed_track is not None:
                    kind, index = placed_track
                    # `media="both"` places the pool item's own kind,
                    # so only an explicit video or audio request names
                    # the row's kind.
                    expected_kind = (args.media if args.media in ("video",
                                                                 "audio")
                                     else None)
                    if (int(index) != int(args.track)
                            or (expected_kind is not None
                                and kind in ("video", "audio")
                                and kind != expected_kind)):
                        return fail(
                            f"the item landed on {kind}{index}, not the "
                            f"requested {expected_kind or ''}{int(args.track)}"
                            " - the write changed the wrong row.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            journal_state["success"] = True
        spans = [_item_span(item) for item in new_items] or [
            {"name": clip_name, "record_in": "", "record_out": "",
             "source_in": "", "source_out": "", "uid": ""}]
        emit([kv_block("placed", {
                  "timeline": timeline.GetName(),
                  "clip": clip_name,
                  "items_delta": delta,
                  "placed_media_id": placed_identity.get("unique_id", ""),
                  "verified": ("yes (the placed item carries the requested "
                               "media, at the requested place; no retry - a "
                               "retry would place it twice)"),
              }),
              table("new", spans,
                    ["name", "record_in", "record_out", "source_in",
                     "source_out", "uid"]),
              _journal_block(journal),
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def _placement_timebase(timeline, pool_item) -> tuple[float, float]:
    """The timeline's frame rate and the media's, as the numbers they are.

    A SOURCE span is counted in the media's frames and a RECORD span in
    the timeline's, so the two are the same number only when the two
    rates agree.  Measured 2026-10-21 on the product-format scratch
    (1080x1920 at 23.976) with 24fps media: the SAME 96-frame clip
    occupies 96 record frames on a 24fps timeline and 95 on the 23.976
    one, and a 73-frame trim of it occupies 72.

    A rate that cannot be read is 0.0, and a rate of 0.0 leaves the
    record span equal to the source span - the old reading, which is
    right whenever the two rates agree.
    """
    timeline_fps = 0.0
    try:
        timeline_fps = float(timeline.GetSetting("timelineFrameRate") or 0)
    except Exception:  # noqa: BLE001 - an unread rate is not a rate
        timeline_fps = 0.0
    media_fps = 0.0
    try:
        media_fps = float(pool_item.GetClipProperty("FPS") or 0)
    except Exception:  # noqa: BLE001 - an unread rate is not a rate
        media_fps = 0.0
    return timeline_fps, media_fps


def _record_frames_for_source(source_frames: int, *, timeline_fps: float,
                              media_fps: float) -> int:
    """The record frames a source span occupies on this timeline.

    Resolve converts the media's frames into the timeline's rate and
    TRUNCATES the result: 96 media frames at 24fps are 4.0 seconds,
    which is 95.904 frames on a 23.976 timeline, and the clip occupies
    95 of them.

    Judging a re-place by the EQUALITY of the two spans therefore
    refuses a write that landed: on the product format a trim's
    re-place "returned an item that did not verify at the requested
    source range and record frame", and the restoration of the pre-trim
    snapshot could not be proven either (measured 2026-10-21, the live
    reel.opening qualification).  The source range and the record frame
    are still verified exactly; only the derived record SPAN is read in
    the timeline's timebase.
    """
    try:
        frames = int(source_frames)
    except (TypeError, ValueError):
        return 0
    if not timeline_fps or not media_fps or media_fps <= 0:
        return frames
    return int(frames * (float(timeline_fps) / float(media_fps)))


def _replace_item(timeline, pool, orig, source_in: int,
                  source_out: int, record_frame: int,
                  track_index: int | None, media_type: int,
                  pool_item=None, preservation_state=None,
                  write_context=None, comps_rederived: bool = False):
    """Re-place one item at a new source range/position.

    Returns `(new_item, error)`: the scripting API offers no move or
    trim, only append plus delete. A move appends before deleting; a
    trim caches its pool item and frees the occupied range first, then
    uses this helper to verify the narrowed replacement or restore.

    `comps_rederived` says the caller will RE-DERIVE the item's Fusion
    composition from the reel's own record after the write
    (`library/tools/comp_carry.py`), so the capture does not refuse the
    composition it is about to replace.
    """
    if pool_item is None:
        try:
            pool_item = orig.GetMediaPoolItem()
        except Exception as exc:
            return None, (f"the item would not name its pool clip ({exc}) "
                           f"- nothing was changed.")
    if pool_item is None:
        return None, ("the item carries no pool clip (a generator or "
                      "title?) - nothing was changed.")
    if preservation_state is None and orig is not None:
        from library.tools import edit_state_preservation
        try:
            preservation_state = edit_state_preservation.capture(
                orig, picture=media_type == 1,
                comps_rederived=comps_rederived)
        except edit_state_preservation.StatePreservationError as exc:
            return None, f"{exc} - nothing was changed."
    if preservation_state is not None:
        from library.tools import edit_state_preservation
        try:
            edit_state_preservation.preflight_source(
                preservation_state, pool_item)
        except edit_state_preservation.StatePreservationError as exc:
            return None, f"{exc} - nothing was changed."
    # The record span the re-placed item will occupy: the source span
    # converted from the media's frame rate into the timeline's.
    timeline_fps, media_fps = _placement_timebase(timeline, pool_item)
    expected_record_frames = _record_frames_for_source(
        int(source_out) - int(source_in) + 1, timeline_fps=timeline_fps,
        media_fps=media_fps)
    # Measured against live Resolve 21.0.0b.28: AppendToTimeline's
    # endFrame is exclusive. For [4, 60] inclusive, endFrame=61 yields
    # 57 record frames; GetSourceEndFrame on that new item returns 61.
    payload = {"mediaPoolItem": pool_item,
               "startFrame": source_in, "endFrame": source_out + 1,
               "mediaType": media_type,
               "recordFrame": record_frame}
    if track_index is not None:
        payload["trackIndex"] = track_index
    try:
        before = (_presence(timeline, _item_span(orig)["uid"])
                  if orig is not None else False)
        placed = pool.AppendToTimeline([payload])
        new_items = list(placed or []) if placed else []
    except Exception as exc:
        return None, (f"the re-place raised ({exc}) - the original is "
                       f"untouched.")
    if not new_items:
        return None, ("the re-place returned nothing - the original "
                      "is untouched.")
    replacement = new_items[0]

    def discard_unverified(reason):
        try:
            timeline.DeleteClips([replacement], False)
            if _presence(timeline, _item_span(replacement)["uid"]):
                reason += " The unverified replacement could not be removed."
        except Exception as exc:
            reason += f" The unverified replacement could not be removed ({exc})."
        return None, reason

    from library.tools import resolve_write_settle

    # The re-placed item reports the state from BEFORE the append until
    # Resolve's handle has caught up, so the preservation state is
    # carried onto the replacement only once the replacement reads the
    # requested range (measured 2026-10-21 on a real reel: a dense reel
    # refreshes slower than a fixture's, and the carry was judged
    # against a stale read).
    def placed_at_requested_range() -> bool:
        span = _item_span(replacement)
        try:
            return (bool(span["uid"])
                    and int(span["record_in"]) == int(record_frame)
                    and int(span["record_out"]) - int(span["record_in"])
                    == expected_record_frames)
        except (TypeError, ValueError):
            return False

    _placed, _waited = resolve_write_settle.wait_for(
        placed_at_requested_range)

    state_diff = {}
    if preservation_state is not None:
        from library.tools import edit_state_preservation
        try:
            state_diff = edit_state_preservation.restore(
                replacement, preservation_state,
                source_in=source_in, source_out=source_out,
                timeline=timeline,
                write_context=write_context)
        except Exception as exc:
            state_diff = {"state": f"preservation read/write failed: {exc}"}
    if state_diff:
        return discard_unverified(
            "the re-place did not preserve supported editorial state: "
            f"{state_diff}.")
    readback = _item_span(replacement)

    def verifies() -> bool:
        """Whether the re-placed item reads the requested range.

        The read waits for Resolve's handle to catch up: a re-placed
        item reports the state from BEFORE the append until the handle
        refreshes, and a dense reel refreshes slower than a fixture's
        (measured 2026-10-21: the replacement was on the row while the
        read said the item did not verify).
        """
        nonlocal readback
        readback = _item_span(replacement)
        try:
            return (
                bool(readback["uid"])
                and int(readback["record_in"]) == int(record_frame)
                and int(readback["record_out"]) - int(readback["record_in"])
                == expected_record_frames
                and int(readback["source_in"]) == int(source_in)
                # Live Resolve reports the exclusive endpoint for appended
                # subranges, but clamps an endpoint at the media's final
                # frame to that final inclusive frame. The record duration
                # and source start are the stable proof of the requested
                # range; accept exactly those two measured endpoint forms.
                and int(readback["source_out"]) in {
                    int(source_out), int(source_out) + 1}
                and _presence(timeline, readback["uid"]))
        except (TypeError, ValueError):
            return False

    from library.tools import resolve_write_settle

    verified, _waited = resolve_write_settle.wait_for(verifies)
    if not verified:
        return None, ("the re-place returned an item that did not verify "
                      "at the requested source range and record frame.")
    return replacement, ""


def _timeline_trim_snapshot(timeline):
    """Capture every timeline item with source duration normalized.

    Resolve reports inclusive source ends on existing clips and the
    exclusive AppendToTimeline endpoint on newly placed clips. Source
    end plus duration is therefore not a stable equality; start plus
    the record duration is the measured source end-exclusive span for
    this unit-speed replacement operation.
    """
    rows = []
    for track_type in ("video", "audio"):
        try:
            count = int(timeline.GetTrackCount(track_type) or 0)
        except Exception as exc:
            raise AxiError(
                f"timeline snapshot could not read {track_type} track count: "
                f"{exc}", fix="re-run once Resolve answers the track read; nothing was written") from exc
        for track_index in range(1, count + 1):
            try:
                items = list(timeline.GetItemListInTrack(
                    track_type, track_index) or [])
            except Exception as exc:
                raise AxiError(
                    f"timeline snapshot could not read {track_type}{track_index}: "
                    f"{exc}", fix="re-run once Resolve answers the track read; nothing was written") from exc
            for item in items:
                span = _item_span(item)
                try:
                    start = int(span["record_in"])
                    end = int(span["record_out"])
                    source_start = int(span["source_in"])
                    source_out = int(span["source_out"])
                    duration = end - start
                except (TypeError, ValueError):
                    raise AxiError(
                        "timeline snapshot could not read every item span", fix="re-run once Resolve answers the item read; nothing was written")
                if not span["uid"] or duration <= 0:
                    raise AxiError(
                        "timeline snapshot found an item without a stable "
                        "identity or positive duration", fix="re-run against a timeline whose items carry stable identities and durations")
                pool_item = None
                try:
                    pool_item = item.GetMediaPoolItem()
                    pool_uid = (str(pool_item.GetUniqueId())
                                if pool_item else "")
                except Exception:
                    pool_uid = ""
                from library.tools import edit_state_preservation
                try:
                    item_state = edit_state_preservation.capture(
                        item, picture=track_type == "video")
                except edit_state_preservation.StatePreservationError as exc:
                    raise AxiError(
                        f"timeline snapshot cannot preserve {span['name']!r}: "
                        f"{exc}", fix="re-run against a timeline whose items can be read back") from exc
                rows.append({
                    "track_type": track_type,
                    "track_index": track_index,
                    "uid": str(span["uid"] or ""),
                    "name": str(span["name"] or ""),
                    "record_in": start,
                    "record_out": end,
                    "source_in": source_start,
                    "source_out": source_out,
                    "source_end_exclusive": source_start + duration,
                    "pool_uid": pool_uid,
                    "pool_item": pool_item,
                    "preservation_state": item_state,
                })
    return rows


def _trim_row_signature(row):
    return tuple(row[key] for key in (
        "track_type", "track_index", "name", "record_in", "record_out",
        "source_in", "source_out", "source_end_exclusive", "pool_uid"))


def _trim_row_semantic_signature(row):
    """Same source/record content across Resolve's measured endpoint forms."""
    return tuple(row[key] for key in (
        "track_type", "track_index", "name", "record_in", "record_out",
        "source_in", "source_end_exclusive", "pool_uid"))


def _restore_trim_snapshot(timeline, pool, before, target_uid,
                           write_context=None):
    """Remove trim attempts, restore the original span, prove all rows.

    The failed attempt can have caused Resolve to split the original at
    the append point. Remove every row that differs from the captured
    snapshot, recreate each missing row from its cached pool handle, and
    compare the complete live inventory before returning success.
    """
    try:
        before_by_uid = {row["uid"]: row for row in before if row["uid"]}
        current = _timeline_trim_snapshot(timeline)
        current_by_uid = {row["uid"]: row for row in current if row["uid"]}
        unchanged = {
            uid for uid, row in current_by_uid.items()
            if uid in before_by_uid
            and _trim_row_signature(row)
            == _trim_row_signature(before_by_uid[uid])}
        handles = {}
        for kind in ("video", "audio"):
            for index in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
                for item in timeline.GetItemListInTrack(kind, index) or []:
                    span = _item_span(item)
                    if span["uid"]:
                        handles[str(span["uid"])] = item
        for row in current:
            if row["uid"] in unchanged:
                continue
            item = handles.get(row["uid"])
            if item is None:
                return None, "cannot identify every changed item for cleanup"
            timeline.DeleteClips([item], False)
        cleared = _timeline_trim_snapshot(timeline)
        cleared_by_uid = {row["uid"]: row for row in cleared if row["uid"]}
        if any(uid not in cleared_by_uid
               or _trim_row_signature(cleared_by_uid[uid])
               != _trim_row_signature(row)
               for uid, row in before_by_uid.items() if uid in unchanged):
            return None, "unchanged timeline rows did not survive trim cleanup"
        if any(row["uid"] not in unchanged for row in cleared):
            return None, "changed timeline rows remain after trim cleanup"

        missing = [row for row in before
                   if row["uid"] not in unchanged]
        for row in missing:
            source_pool = row["pool_item"]
            if source_pool is None:
                return None, (f"cannot restore {row['name']!r}: its captured "
                              "media-pool item is unavailable")
            duration = row["record_out"] - row["record_in"]
            source_end = row["source_in"] + duration - 1
            restored, error = _replace_item(
                timeline, pool, None, row["source_in"], source_end,
                row["record_in"], row["track_index"],
                1 if row["track_type"] == "video" else 2,
                pool_item=source_pool,
                preservation_state=row["preservation_state"],
                write_context=write_context)
            if error:
                return None, (f"could not restore {row['name']!r} from the "
                              f"captured snapshot: {error}")
        after = _timeline_trim_snapshot(timeline)
        expected = [_trim_row_semantic_signature(row) for row in before]
        observed = [_trim_row_semantic_signature(row) for row in after]
        if sorted(expected, key=repr) != sorted(observed, key=repr):
            return None, "full timeline inventory differs from the captured snapshot"
        restored_target = next(
            (row for row in after
             if _trim_row_semantic_signature(row)
             == _trim_row_semantic_signature(next(row for row in before
                                                  if row["uid"] == target_uid))),
            None)
        return restored_target, ""
    except Exception as exc:
        return None, f"snapshot restoration raised ({exc})"


def _drop_original(resolve, project, timeline, uid: str):
    """Delete a verified move/trim source item as a local EditPatch."""
    from library.tools import edit_patch
    from library.tools.execution_router import route_axi_dispatch

    try:
        axi_operations = [{"op": "clip.delete", "unique_id": uid}]
        # VC-09: AXI dispatches share the routing contract.
        axi_routing = route_axi_dispatch(
            axi_operations,
            context=_qualification_context(
                resolve, project, timeline, axi_operations))
        receipt = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.touchup",
            operations=axi_operations,
            conflict_domains=("timeline_structure",),
            idempotency_key=f"resolve-axi.replace-delete:{uuid.uuid4().hex}")
    except Exception as exc:
        return (f"the replacement landed but the local-delete EditPatch "
                f"failed ({exc}) - remove one by hand.")
    receipt["execution_routing"] = axi_routing
    if receipt["status"] != "committed":
        return (f"the replacement landed but the original could not be "
                f"removed by EditPatch ({receipt}) - remove one by hand.")
    return ""


def _inclusive_source_end(source_in: int, source_out: int,
                            record_duration: int) -> int | None:
    """Normalize Resolve's two source-endpoint forms to an inclusive bound.

    Measured against live Resolve: existing clips report the inclusive
    GetSourceEndFrame, while fresh AppendToTimeline placements echo the
    exclusive endFrame. The record duration decides which form a
    read-back speaks. Returns None when the span matches neither form -
    the caller refuses rather than re-placing an unfaithful range.
    """
    if record_duration == source_out - source_in + 1:
        return source_out
    if record_duration == source_out - source_in:
        return source_out - 1
    return None


def _preflight_replace_delete(resolve, project, timeline, uid: str) -> str:
    """Refuse an unsupported replacement before placing anything live.

    Move and trim are implemented as place-then-delete. Their delete must
    be routable before the first write, or a fail-closed router can leave
    both the replacement and the approved source on the timeline.
    """
    from library.tools.execution_router import route_axi_dispatch

    axi_operations = [{"op": "clip.delete", "unique_id": uid}]
    try:
        route_axi_dispatch(
            axi_operations,
            context=_qualification_context(
                resolve, project, timeline, axi_operations))
    except Exception as exc:
        return str(exc)
    return ""


def _linked_items(item):
    """Read Resolve's explicit link group, failing closed on unreadable links."""
    try:
        linked = list(item.GetLinkedItems() or [])
    except Exception as exc:
        raise AxiError(f"linked-item state could not be read ({exc})", fix="re-run once Resolve answers the linked-item read; nothing was written") from exc
    items = [item, *linked]
    by_uid = {}
    for member in items:
        try:
            uid = str(member.GetUniqueId() or "")
        except Exception as exc:
            raise AxiError(f"a linked item has no readable identity ({exc})", fix="supply the linked item's stable Resolve identity (unique id), then re-run") from exc
        if not uid:
            raise AxiError("a linked item has no stable Resolve identity", fix="supply the linked item's stable Resolve identity (unique id), then re-run")
        by_uid[uid] = member
    return list(by_uid.values())


def _ripple_member_snapshot(item, track_type, track_index):
    """Capture the source and native state a replacement must preserve."""
    span = _item_span(item)
    try:
        source_in = int(span["source_in"])
        source_out = int(span["source_out"])
        record_in = int(span["record_in"])
        record_out = int(span["record_out"])
        pool_item = item.GetMediaPoolItem()
        pool_uid = str(pool_item.GetUniqueId() or "") if pool_item else ""
    except Exception as exc:
        raise AxiError(f"{span.get('name')!r} cannot be safely re-placed ({exc})", fix="re-read the timeline, state the item by its current position, then re-run") from exc
    if pool_item is None or record_out <= record_in:
        raise AxiError(f"{span.get('name')!r} has unsupported media or span", fix="use a span whose media kind this replacement path can carry")
    # The source end has ONE reading before the re-place.  Resolve
    # reports the inclusive end on an existing clip and the exclusive
    # end on a fresh placement, and the re-place adds one to it - so a
    # snapshot that kept the raw reading re-placed a clip whose source
    # range was shorter than its media ONE FRAME LONGER than it was
    # (measured 2026-10-21: a 48-frame successor came back as 49
    # frames).  The record duration decides which form the read speaks,
    # and a span that tells neither is REFUSED rather than re-placed
    # unfaithfully - the rule the move and trim paths already follow.
    inclusive = _inclusive_source_end(
        source_in, source_out, record_out - record_in)
    if inclusive is None:
        raise AxiError(
            f"{span.get('name')!r} has a source range this replacement path "
            f"cannot read faithfully (source {source_in}..{source_out} over "
            f"{record_out - record_in} record frames) - nothing was "
            f"changed.", fix="use a source range this path can read faithfully, or re-cut the clip")
    try:
        from library.tools import edit_state_preservation
        from library.tools.reel_read import _speed_percent

        speed = _speed_percent(item)
        preservation_state = edit_state_preservation.capture(
            item, picture=track_type == "video")
    except edit_state_preservation.StatePreservationError as exc:
        raise AxiError(
            f"{span.get('name')!r} cannot be safely re-placed ({exc})", fix="re-read the timeline, state the item by its current position, then re-run") from exc
    except (AttributeError, TypeError, ValueError) as exc:
        raise AxiError(
            f"{span.get('name')!r} speed state cannot be read ({exc})", fix="re-run once Resolve answers the speed read; nothing was written") from exc
    if speed is None or abs(speed - 100.0) > 1e-6:
        raise AxiError(
            f"{span.get('name')!r} is retimed; this replacement path cannot "
            "preserve speed changes", fix="replace a clip that carries no speed change, or re-time it after the replacement")
    # The shared preservation contract re-links by live handle after each
    # group has been re-placed below, so old native ids are only evidence.
    preservation_state = dict(preservation_state)
    preservation_state["linked_item_ids"] = []
    return {
        "item": item, "uid": str(span["uid"]), "name": str(span["name"]),
        "track_type": track_type, "track_index": int(track_index),
        "record_in": record_in, "record_out": record_out,
        "source_in": source_in, "source_out": inclusive,
        "duration": record_out - record_in, "pool_item": pool_item,
        "pool_uid": pool_uid,
        "preservation_state": preservation_state,
    }


def _read_inclusive_source_end(span, record_duration):
    """The item's source end in the inclusive reading, or None.

    `None` means the reading cannot be told - the span matches neither
    of Resolve's two endpoint forms - and the caller decides what to do
    with an unreadable range rather than assuming one.
    """
    try:
        source_in = int(span["source_in"])
        source_out = int(span["source_out"])
    except (TypeError, ValueError, KeyError):
        return None
    return _inclusive_source_end(source_in, source_out, record_duration)


def _track_ripple_inventory(timeline):
    """Read structural identity and span without requiring unrelated state carry."""
    rows = []
    for track_type in ("video", "audio"):
        count = int(timeline.GetTrackCount(track_type) or 0)
        for track_index in range(1, count + 1):
            for item in timeline.GetItemListInTrack(track_type, track_index) or []:
                span = _item_span(item)
                try:
                    pool_item = item.GetMediaPoolItem()
                    pool_uid = str(pool_item.GetUniqueId() or "") if pool_item else ""
                    start = int(span["record_in"])
                    end = int(span["record_out"])
                    source_in = (None if span["source_in"] is None
                                 else int(span["source_in"]))
                    uid = str(span["uid"] or "")
                except (TypeError, ValueError, AttributeError) as exc:
                    raise AxiError(
                        f"timeline {track_type}{track_index} contains an item "
                        f"whose structural identity cannot be verified ({exc})", fix="re-run against a timeline whose items carry stable structural identities") from exc
                if not uid or end <= start:
                    raise AxiError(
                        f"timeline {track_type}{track_index} contains an item "
                        "without a stable structural identity or positive span", fix="re-run against a timeline whose items carry stable structural identities")
                rows.append({
                    "uid": uid, "name": str(span["name"] or ""),
                    "track_type": track_type, "track_index": track_index,
                    "record_in": start, "record_out": end,
                    "source_in": source_in,
                    "source_end_exclusive": (
                        None if source_in is None else source_in + end - start),
                    # The source range the item REALLY carries, in the
                    # inclusive reading.  The arithmetic end above is
                    # computed, so it cannot see a re-place that came
                    # back with a different source range - this one is
                    # read, and the ripple verifies the shifted item
                    # against it.
                    "source_end_inclusive": _read_inclusive_source_end(
                        span, end - start),
                    "pool_uid": pool_uid,
                })
    return rows


def cmd_edit_track_ripple_remove(args) -> int:
    """Close one track's gap, shifting only its later items and link groups."""
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        journal = None
        try:
            timeline, is_current, note = _target_timeline(project, args.timeline)
            target, track_type, track_index, uid = _edit_item_by_identity(
                timeline, args)
            target_span = _item_span(target)
            start, end = int(target_span["record_in"]), int(target_span["record_out"])
            duration = end - start
            if duration <= 0:
                raise AxiError("target clip has no positive record duration", fix="state a clip that carries frames on the timeline")
            rows = []
            for kind in ("video", "audio"):
                for row in range(1, int(timeline.GetTrackCount(kind) or 0) + 1):
                    for live_item in timeline.GetItemListInTrack(kind, row) or []:
                        span = _item_span(live_item)
                        rows.append((kind, row, live_item, span))
            target_group = _linked_items(target)
            target_ids = {str(member.GetUniqueId()) for member in target_group}
            target_records = []
            for kind, row, member, span in rows:
                if str(span["uid"]) in target_ids:
                    target_records.append(_ripple_member_snapshot(member, kind, row))
            if target_ids != {record["uid"] for record in target_records}:
                raise AxiError("an explicitly linked target is not on a readable timeline track", fix="name a linked item that is on the timeline")

            target_track_successors = [
                (member, span) for kind, row, member, span in rows
                if kind == track_type and row == track_index
                and int(span["record_in"]) >= end]
            successor_groups = []
            claimed = set(target_ids)
            for member, span in sorted(target_track_successors,
                                       key=lambda pair: int(pair[1]["record_in"])):
                group = _linked_items(member)
                records = []
                for linked in group:
                    linked_uid = str(linked.GetUniqueId())
                    if linked_uid in claimed:
                        raise AxiError(
                            "two ripple items share a link group; refusing ambiguous movement", fix="split the ripple so no two items share a link group")
                    hits = [(kind, row, live, live_span)
                            for kind, row, live, live_span in rows
                            if str(live_span["uid"]) == linked_uid]
                    if len(hits) != 1:
                        raise AxiError(
                            f"linked item {linked_uid!r} is not uniquely present", fix="name the linked item by its unique id, then re-run")
                    kind, row, live, _ = hits[0]
                    records.append(_ripple_member_snapshot(live, kind, row))
                    claimed.add(linked_uid)
                successor_groups.append(records)
            moved_records = [record for group in successor_groups
                             for record in group]
            destination_claims = {
                (record["track_type"], record["track_index"],
                 record["record_in"] - duration,
                 record["record_out"] - duration)
                for record in moved_records}
            affected_ids = target_ids | {record["uid"] for record in moved_records}
            for kind, row, _live_item, span in rows:
                uid_here = str(span["uid"])
                if uid_here in affected_ids:
                    continue
                old_in, old_out = int(span["record_in"]), int(span["record_out"])
                if any(dest_kind == kind and dest_row == row
                       and dest_in < old_out and old_in < dest_out
                       for dest_kind, dest_row, dest_in, dest_out
                       in destination_claims):
                    raise AxiError(
                        f"track-scoped ripple would overlap unrelated {kind}{row} content", fix="narrow the ripple to the row you mean, or re-time the clip so nothing else is covered")

            if not args.apply:
                emit([kv_block("track_ripple_remove_plan", {
                    "timeline": timeline.GetName() + (" (current)" if is_current else ""),
                    "target": target_span["name"], "track": f"{track_type}{track_index}",
                    "record_span": f"[{start}, {end})", "shifted_groups": len(successor_groups),
                    "shift_frames": duration,
                    "linked_target_items": len(target_records) - 1,
                }), note, "dry run - pass --apply to journal and close the gap"])
                return 0
            _assert_cursor_on(resolve, timeline.GetName())
            before_rows = _track_ripple_inventory(timeline)
            with _journal_write(
                    args, timeline=timeline, project=project,
                    spec={"verb": "edit_track_ripple_remove",
                          "timeline": timeline.GetName(), "unique_id": uid,
                          "track": f"{track_type}{track_index}",
                          "record_in": start, "record_out": end,
                          "shifted_groups": len(successor_groups)},
                    gate_class="edit_track_ripple_remove") as (journal, state):
                if not timeline.DeleteClips(
                        [record["item"] for record in target_records], False):
                    raise AxiError("Resolve refused the local target and linked-item delete", fix="re-read the timeline and check whether the item is still there, then re-run")
                if any(_presence(timeline, record["uid"])
                       for record in target_records):
                    raise AxiError("the target or an explicitly linked item remained after delete", fix="re-read the timeline: the item is still there, so nothing was deleted")

                for group in successor_groups:
                    if not timeline.DeleteClips(
                            [record["item"] for record in group], False):
                        raise AxiError("Resolve refused to clear a shifted link group", fix="re-read the timeline and re-run; the link group is unchanged")
                    if any(_presence(timeline, record["uid"]) for record in group):
                        raise AxiError("a shifted original remained before re-placement", fix="re-read the timeline and re-run; nothing was moved")
                    replacements = []
                    for record in group:
                        media_type = {"video": 1, "audio": 2}.get(
                            record["track_type"])
                        if media_type is None:
                            raise AxiError(
                                f"linked {record['track_type']} items cannot be re-placed safely", fix="re-state the plan against what the linked items are now")
                        replacement, error = _replace_item(
                            timeline, project.GetMediaPool(), None,
                            record["source_in"], record["source_out"],
                            record["record_in"] - duration,
                            record["track_index"], media_type,
                            pool_item=record["pool_item"],
                            preservation_state=record["preservation_state"],
                            write_context={"project": project.GetName(),
                                           # The re-place carries the
                                           # approved transform, and a
                                           # transform write is anchored
                                           # to a project folder.
                                           "project_folder":
                                               args.project_folder or None,
                                           "timeline_id": timeline.GetUniqueId(),
                                           "timeline_name": timeline.GetName()})
                        if error:
                            raise AxiError(error, fix="re-run once the underlying error clears; nothing was written")
                        replacements.append(replacement)
                    if len(replacements) > 1:
                        if not timeline.SetClipsLinked(replacements, True):
                            raise AxiError("Resolve refused to restore an explicit link group", fix="re-read the timeline and re-run; the link group is unchanged")
                        replacement_ids = {str(item.GetUniqueId()) for item in replacements}
                        if any(replacement_ids - {str(item.GetUniqueId())}
                               - {str(linked.GetUniqueId()) for linked in
                                  (item.GetLinkedItems() or [])}
                               for item in replacements):
                            raise AxiError("replacement link group did not read back", fix="re-read the timeline and re-run; the replacement did not land")

                # Verify the requested track's complete post-removal sequence.
                final_items = list(timeline.GetItemListInTrack(
                    track_type, track_index) or [])
                final_spans = sorted((_item_span(item) for item in final_items),
                                     key=lambda span: int(span["record_in"]))
                actual_starts = [int(span["record_in"]) for span in final_spans]
                if any(record["record_in"] - duration not in actual_starts
                       for group in successor_groups for record in group
                       if record["track_type"] == track_type
                       and record["track_index"] == track_index):
                    raise AxiError("the downstream track sequence did not close the removed gap", fix="re-read the timeline: the gap is still open, so the ripple did not land")
                after_rows = _track_ripple_inventory(timeline)
                before_by_uid = {str(record["uid"]): record
                                 for record in before_rows}
                after_by_uid = {str(record["uid"]): record
                                for record in after_rows}
                expected_moves = moved_records
                for old_uid, old_record in before_by_uid.items():
                    if old_uid in target_ids:
                        if old_uid in after_by_uid:
                            raise AxiError("the target or its linked items remain", fix="re-read the timeline: the items are still there, so nothing was deleted")
                        continue
                    if old_uid in affected_ids:
                        if old_uid in after_by_uid:
                            raise AxiError("a shifted original item remained on the timeline", fix="re-read the timeline and re-run; the item did not move")
                        continue
                    if old_uid not in affected_ids:
                        new = after_by_uid.get(old_uid)
                        if (new is None or _trim_row_semantic_signature(new)
                                != _trim_row_semantic_signature(old_record)):
                            raise AxiError("an unrelated item changed during track ripple", fix="re-read the timeline: the ripple touched an item outside the plan")
                for record in expected_moves:
                    if _presence(timeline, record["uid"]):
                        raise AxiError("a shifted original item remained on the timeline", fix="re-read the timeline and re-run; the item did not move")
                    wanted_in = record["record_in"] - duration
                    wanted_out = record["record_out"] - duration
                    matches = [candidate for candidate in after_rows
                               if candidate["track_type"] == record["track_type"]
                               and candidate["track_index"] == record["track_index"]
                               and candidate["pool_uid"] == record["pool_uid"]
                               and candidate["source_in"] == record["source_in"]
                               and candidate["source_end_inclusive"]
                               == record["source_out"]
                               and candidate["record_in"] == wanted_in
                               and candidate["record_out"] == wanted_out]
                    if len(matches) != 1:
                        raise AxiError("a downstream item did not read back at its exact shifted span", fix="re-read the timeline and re-run; the shift did not land")
                if len(after_rows) != len(rows) - len(target_records):
                    raise AxiError("the timeline item count differs from the scoped ripple plan", fix="re-read the timeline and re-state the plan against what is there")
                state["success"] = True
            emit([kv_block("track_ripple_removed", {
                "timeline": timeline.GetName(), "target": target_span["name"],
                "target_absent": "yes", "track": f"{track_type}{track_index}",
                "shifted_groups": len(successor_groups), "shift_frames": duration,
                "journal_entry": journal["id"] if journal else "unavailable",
            }), _journal_block(journal), note])
            return 0
        except AxiError as exc:
            if journal:
                return fail(
                    f"{exc} Recovery is required from journal entry "
                    f"{journal['id']}; it records the full pre-edit timeline.",
                    f"ren undo {args.project} --list")
            return fail(str(exc), exc.fix or f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            if journal:
                return fail(
                    f"track-scoped ripple remove failed ({type(exc).__name__}: "
                    f"{exc}). Recovery is required from journal entry "
                    f"{journal['id']}; it records the full pre-edit timeline.",
                    f"ren undo {args.project} --list")
            return fail(f"track-scoped ripple remove failed ({type(exc).__name__}: {exc})",
                        f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        except Exception as exc:  # noqa: BLE001 - Resolve bridge errors vary by API call
            if journal:
                return fail(
                    f"track-scoped ripple remove failed ({type(exc).__name__}: "
                    f"{exc}). Recovery is required from journal entry "
                    f"{journal['id']}; it records the full pre-edit timeline.",
                    f"ren undo {args.project} --list")
            return fail(f"track-scoped ripple remove failed ({type(exc).__name__}: {exc})",
                        f"{TOOL} items --timeline \"{timeline.GetName()}\"")


def cmd_edit_delete(args) -> int:
    """Delete one item off the reel through a generation patch.

    A local `clip.delete` re-reads before its one measured retry.
    `--ripple` closes the gap and is never defaulted: it has a timeline
    wide temporal effect, outside the local EditPatch operation.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, uid = _edit_item_by_identity(
                timeline, args)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        else:
            emit([kv_block("delete_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "track": f"{track_type}{track_index}",
                      "index": args.index,
                      "name": span["name"],
                      "record_in": span["record_in"],
                      "record_out": span["record_out"],
                      "ripple": "yes - the gap closes" if args.ripple
                      else "no",
                  }),
                  note,
                  f"dry run - pass --apply to delete under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([
                      f"{TOOL} edit delete --timeline "
                      f"\"{timeline.GetName()}\" --track "
                      f"{track_type}{track_index} --index {args.index}"
                      f"{' --ripple' if args.ripple else ''} --apply"])])
            return 0
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "edit_delete", "timeline": timeline.GetName(),
                      "name": span["name"], "unique_id": uid,
                      "ripple": args.ripple},
                gate_class="edit_delete") as (journal, journal_state):
            if not args.ripple:
                if not uid:
                    return fail("the item has no unique id, so EditPatch "
                                "cannot address this local delete.",
                                f"{TOOL} items --timeline "
                                f"\"{timeline.GetName()}\"")
                from library.tools import edit_patch
                from library.tools.execution_router import route_axi_dispatch
                try:
                    axi_operations = [{"op": "clip.delete", "unique_id": uid}]
                    # VC-09: AXI dispatches share the routing contract.
                    axi_routing = route_axi_dispatch(
                        axi_operations,
                        context=_qualification_context(
                            resolve, project, timeline, axi_operations))
                    receipt = edit_patch.apply_live_patch(
                        project=project, timeline=timeline,
                        capability="reel.touchup",
                        operations=axi_operations,
                        conflict_domains=("timeline_structure",),
                        idempotency_key=(
                            f"resolve-axi.edit-delete:{uuid.uuid4().hex}"))
                except Exception as exc:
                    return fail(
                        f"EditPatch refused or failed the delete ({exc}).",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
                receipt["execution_routing"] = axi_routing
                if receipt["status"] != "committed":
                    return fail(
                        f"the local-delete EditPatch did not verify: "
                        f"{receipt}.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
            else:
                try:
                    first = bool(timeline.DeleteClips([item], True))
                except Exception as exc:
                    return fail(f"delete raised ({exc}) - verify by hand.",
                                f"{TOOL} items --timeline "
                                f"\"{timeline.GetName()}\"")
                if _presence(timeline, uid):
                    try:
                        second = bool(timeline.DeleteClips([item], True))
                    except Exception as exc:
                        return fail(
                            f"delete raised on retry ({exc}) and the item "
                            f"is still there - verify by hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
                    if _presence(timeline, uid):
                        return fail(
                            f"delete reports {second} and the item is still "
                            f"there - the write needs the Edit page open, "
                            f"and nothing was retried past once.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            journal_state["success"] = True
        emit([kv_block("deleted", {
                  "timeline": timeline.GetName(),
                  "name": span["name"],
                  "verified": "yes (unique id absent on re-read)"
                  if uid else "partially (the item carried no id - "
                  "the call reported "
                  f"{first})",
              }),
              note,
              _journal_block(journal),
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def cmd_edit_move(args) -> int:
    """Move one item to a new record frame: verified re-place, then
    the original is deleted. The place half verifies before the
    delete half runs, so a failed move leaves the original alone."""
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, uid = _edit_item(
                timeline, args.track, args.index)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        dest_track = args.track_to or f"{track_type}{track_index}"
        try:
            _dest_type, dest_index = _parse_track(dest_track)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        if args.to is None:
            return fail("move needs --to.",
                        f"{TOOL} edit move --help")
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        else:
            emit([kv_block("move_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "name": span["name"],
                      "from": (f"{track_type}{track_index}@"
                               f"{span['record_in']}"),
                      "to": f"{dest_track}@{args.to}",
                  }),
                  note,
                  f"dry run - pass --apply to re-place and delete "
                  f"under the Resolve lease",
                  help_block([
                      f"{TOOL} edit move --timeline "
                      f"\"{timeline.GetName()}\" --track "
                      f"{track_type}{track_index} --index {args.index} "
                      f"--to {args.to} --apply"])])
            return 0
        try:
            src_in = int(span["source_in"])
            src_out = int(span["source_out"])
            record_duration = (int(span["record_out"])
                               - int(span["record_in"]))
        except (TypeError, ValueError):
            return fail(
                f"{span['name']!r} would not report its source range "
                f"- the move cannot be re-placed faithfully.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        normalized = _inclusive_source_end(src_in, src_out, record_duration)
        if normalized is None:
            return fail(
                f"{span['name']!r} has a source-to-record duration mismatch; "
                "move refuses because a lossless restore cannot be proven.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        # Fresh AppendToTimeline placements report the exclusive source
        # endpoint; the re-place speaks inclusive bounds like the CLI.
        src_out = normalized
        refusal = _preflight_replace_delete(resolve, project, timeline, uid)
        if refusal:
            return fail(
                f"move refused before writing because its source delete is "
                f"not authorized ({refusal}).",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "edit_move", "timeline": timeline.GetName(),
                      "name": span["name"], "from": f"{track_type}{track_index}",
                      "to": dest_track, "record_frame": args.to},
                gate_class="edit_move") as (journal, journal_state):
            new_item, error = _replace_item(
                timeline, project.GetMediaPool(), item, src_in, src_out,
                args.to, dest_index,
                media_type=1 if track_type == "video" else 2,
                write_context={
                    "project": project.GetName(),
                    "project_folder": args.project_folder or None,
                    "timeline_id": timeline.GetUniqueId(),
                    "timeline_name": timeline.GetName(),
                })
            if error:
                return fail(error, f"{TOOL} items --timeline "
                                   f"\"{timeline.GetName()}\"")
            drop_error = _drop_original(resolve, project, timeline, uid)
            if drop_error:
                return fail(drop_error, f"{TOOL} items --timeline "
                                         f"\"{timeline.GetName()}\"")
            journal_state["success"] = True
        new_span = _item_span(new_item)
        emit([kv_block("moved", {
                  "timeline": timeline.GetName(),
                  "name": span["name"],
                  "record_in": new_span["record_in"],
                  "record_out": new_span["record_out"],
                  "verified": "yes (re-place present, original "
                  "absent)",
              }),
              note,
              _journal_block(journal),
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def cmd_edit_trim(args) -> int:
    """Narrow an item's source range in place: verified re-place at
    the shifted record frame, then the original is deleted. The API
    offers no trim, so this is the trim - and the dry run says so."""
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, uid = _edit_item_by_identity(
                timeline, args)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        if args.source_in is None and args.source_out is None:
            return fail("trim needs --source-in and/or --source-out.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        try:
            orig_in = int(span["source_in"])
            orig_out = int(span["source_out"])
            orig_rec = int(span["record_in"])
            orig_rec_out = int(span["record_out"])
        except (TypeError, ValueError):
            return fail(
                f"{span['name']!r} would not report its spans - the "
                f"trim cannot be re-placed faithfully.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        record_duration = orig_rec_out - orig_rec
        normalized = _inclusive_source_end(orig_in, orig_out, record_duration)
        if normalized is None:
            return fail(
                f"{span['name']!r} has a source-to-record duration mismatch; "
                "trim refuses because a lossless restore cannot be proven.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        # Fresh AppendToTimeline placements report the exclusive source
        # endpoint (measured: the requested endFrame echoes back); the CLI
        # speaks inclusive bounds, so narrow from the normalized bound.
        orig_out_inclusive = normalized
        new_in = orig_in if args.source_in is None else args.source_in
        new_out = (orig_out_inclusive if args.source_out is None
                   else args.source_out)
        if not orig_in <= new_in <= new_out <= orig_out_inclusive:
            return fail(
                f"[{new_in}, {new_out}] reaches past the handles "
                f"[{orig_in}, {orig_out_inclusive}] - trim only narrows.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        new_rec = orig_rec + (new_in - orig_in)
        # THE COMPOSITION the clip carries, if it carries one.  A
        # re-place gives the item a new identity and a composition is
        # keyed to the item it was written for, so the composition is
        # RE-DERIVED from the reel's own record after the write
        # (`library/tools/comp_carry.py`).  The record is read BEFORE
        # the write, because a record that does not describe this reel
        # is a refusal, not a guess.
        carry = None
        comp_count = 0
        try:
            comp_count = int(item.GetFusionCompCount() or 0)
        except Exception as exc:  # noqa: BLE001 - an unreadable count is 0
            comp_count = 0
        if comp_count:
            try:
                carry = _plan_comp_carry(
                    args, project, timeline, item, track_type, track_index,
                    new_in, new_out, comp_count)
            except AxiError as exc:
                return fail(str(exc), exc.fix)
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        else:
            emit([kv_block("trim_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "name": span["name"],
                      "source": f"[{orig_in}, {orig_out}] -> "
                      f"[{new_in}, {new_out}]",
                      "record_in": f"{orig_rec} -> {new_rec}",
                  }),
                  note,
                  f"dry run - pass --apply to re-place and delete "
                  f"under the Resolve lease",
                  help_block([
                      f"{TOOL} edit trim --timeline "
                      f"\"{timeline.GetName()}\" --track "
                      f"{track_type}{track_index} --index {args.index} "
                      f"--source-in {new_in} --source-out {new_out} "
                      f"--apply"])])
            return 0
        refusal = _preflight_replace_delete(resolve, project, timeline, uid)
        if refusal:
            return fail(
                f"trim refused before writing because its source delete is "
                f"not authorized ({refusal}).",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "edit_trim", "timeline": timeline.GetName(),
                      "name": span["name"], "source_in": new_in,
                      "source_out": new_out, "record_in": new_rec},
                gate_class="edit_trim") as (journal, journal_state):
            pool = project.GetMediaPool()
            media_type = 1 if track_type == "video" else 2
            try:
                pool_item = item.GetMediaPoolItem()
            except Exception as exc:
                return fail(
                    f"the item would not name its pool clip ({exc}) "
                    "- nothing was changed.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            if pool_item is None:
                return fail(
                    "the item carries no pool clip (a generator or title?) "
                    "- nothing was changed.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            try:
                before_snapshot = _timeline_trim_snapshot(timeline)
            except AxiError as exc:
                return fail(
                    f"trim refused before writing: {exc}",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            drop_error = _drop_original(resolve, project, timeline, uid)
            if drop_error:
                return fail(drop_error, f"{TOOL} items --timeline "
                                   f"\"{timeline.GetName()}\"")
            new_item, error = _replace_item(
                timeline, pool, item, new_in, new_out,
                new_rec, track_index, media_type, pool_item=pool_item,
                comps_rederived=carry is not None,
                preservation_state=next(
                    row["preservation_state"] for row in before_snapshot
                    if row["uid"] == uid),
                write_context={
                    "project": project.GetName(),
                    "project_folder": args.project_folder or None,
                    "timeline_id": timeline.GetUniqueId(),
                    "timeline_name": timeline.GetName(),
                })
            if error:
                restored, restore_error = _restore_trim_snapshot(
                    timeline, pool, before_snapshot, uid,
                    write_context={
                        "project": project.GetName(),
                        "project_folder": args.project_folder or None,
                        "timeline_id": timeline.GetUniqueId(),
                        "timeline_name": timeline.GetName(),
                    })
                if restore_error:
                    return fail(
                        f"trim failed ({error}); full timeline restoration "
                        f"could not be proven ({restore_error})",
                        f"{TOOL} items --timeline \"{timeline.GetName()}\"")
                return fail(f"trim failed ({error}); the original full "
                            f"timeline snapshot was restored and verified",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            # THE COMPOSITION, re-derived onto the re-placed clip.  The
            # clip is written; the treatment it carried is not, and a
            # clip whose composition was dropped renders the picture the
            # treatment used to draw over.  So the comp pass writes it
            # again, keyed to the length the clip NOW plays, and the
            # write is proved: the comp is counted and its media window
            # is read back against the frames the item plays.
            carry_receipt = None
            if carry is not None:
                placed_span = _item_span(new_item)
                try:
                    from library.tools import comp_carry
                    comp_carry.apply_carry(
                        carry,
                        project_folder=carry.project_folder,
                        resolve_project_name=project.GetName(),
                        timeline_name=timeline.GetName(),
                        item=new_item)
                    carry_receipt = comp_carry.verify_carry(
                        new_item, int(placed_span["record_out"])
                        - int(placed_span["record_in"]))
                except Exception as exc:  # noqa: BLE001 - the write is undone
                    restored, restore_error = _restore_trim_snapshot(
                        timeline, pool, before_snapshot, uid,
                        write_context={
                            "project": project.GetName(),
                            "project_folder": args.project_folder or None,
                            "timeline_id": timeline.GetUniqueId(),
                            "timeline_name": timeline.GetName(),
                        })
                    if restore_error:
                        return fail(
                            f"trim failed ({exc}); full timeline restoration "
                            f"could not be proven ({restore_error})",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
                    return fail(
                        f"trim failed ({exc}); the original full timeline "
                        "snapshot was restored and verified",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
                if not carry_receipt.get("carried"):
                    restored, restore_error = _restore_trim_snapshot(
                        timeline, pool, before_snapshot, uid,
                        write_context={
                            "project": project.GetName(),
                            "project_folder": args.project_folder or None,
                            "timeline_id": timeline.GetUniqueId(),
                            "timeline_name": timeline.GetName(),
                        })
                    if restore_error:
                        return fail(
                            f"the composition did not read back "
                            f"({carry_receipt}); full timeline restoration "
                            f"could not be proven ({restore_error})",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
                    return fail(
                        f"the composition did not read back "
                        f"({carry_receipt}); the original full timeline "
                        "snapshot was restored and verified",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
            journal_state["success"] = True
        new_span = _item_span(new_item)
        emit([kv_block("trimmed", {
                  "timeline": timeline.GetName(),
                  "name": span["name"],
                  "source_in": new_span["source_in"],
                  "source_out": new_span["source_out"],
                  "record_in": new_span["record_in"],
                  "verified": "yes (re-place present, original "
                  "absent)",
              }),
              note,
              _journal_block(journal),
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def cmd_edit_split(args) -> int:
    """Cut ONE picture item at an exact source frame; the audio is not cut.

    The cut is addressed in SOURCE frames, because a source frame is
    what an event boundary is measured in; the record frame it lands on
    is computed and reported.  The linked audio beside the picture is
    read before and after the write and must come back unchanged: same
    item, same in/out, same gain, same fades.

    The route is COMPOSED: the installed Resolve 21.1 exposes no split
    method (measured on the live Resolve), so the picture is replaced by
    two picture-only spans.  The audio item is never touched; its LINK
    to the picture is reported as measured, because the picture's
    identity is what changed.
    """
    from library.tools import picture_split

    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        try:
            item, track_type, track_index, uid = _edit_item_by_identity(
                timeline, args)
        except AxiError as exc:
            return fail(str(exc), exc.fix)
        if track_type != "video":
            return fail(
                f"split addresses a PICTURE item, and {track_type}"
                f"{track_index} is not a video track.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        at_source_frame = int(args.at_source_frame)
        try:
            record_frame = picture_split.record_frame_for_source_frame(
                item, at_source_frame)
        except picture_split.PictureSplitRefused as exc:
            return fail(str(exc), f"{TOOL} items --timeline "
                               f"\"{timeline.GetName()}\"")
        span = _item_span(item)
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix)
        else:
            emit([kv_block("split_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "track": f"{track_type}{track_index}",
                      "index": args.index,
                      "name": span["name"],
                      "at_source_frame": at_source_frame,
                      "at_record_frame": record_frame,
                      "record_in": span["record_in"],
                      "record_out": span["record_out"],
                      "audio": "the linked audio is not cut",
                  }),
                  note,
                  f"dry run - pass --apply to split under the Resolve lease",
                  help_block([
                      f"{TOOL} edit split --timeline "
                      f"\"{timeline.GetName()}\" --track "
                      f"{track_type}{track_index} --index {args.index} "
                      f"--at-source-frame {at_source_frame} --apply"])])
            return 0
        audio_before = picture_split.audio_track_states(timeline)
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "edit_split", "timeline": timeline.GetName(),
                      "name": span["name"], "unique_id": uid,
                      "at_source_frame": at_source_frame,
                      "at_record_frame": record_frame},
                gate_class="edit_split") as (journal, journal_state):
            try:
                receipt = picture_split.composed_picture_split(
                    item, at_source_frame, timeline=timeline,
                    pool=project.GetMediaPool())
            except picture_split.PictureSplitRefused as exc:
                return fail(str(exc), f"{TOOL} items --timeline "
                                   f"\"{timeline.GetName()}\"")
            after_items = picture_split.track_item_states(
                timeline, track_type, track_index)
            picture_in = int(receipt["before"]["record_in"])
            picture_out = int(receipt["before"]["record_out"])
            halves = [state for state in after_items
                      if _overlaps_span(state, picture_in, picture_out)]
            verdict = picture_split.verify_split(
                {"before": receipt["before"],
                 "record_frame": receipt["record_frame"],
                 "audio": audio_before},
                {"halves": halves,
                 "audio": picture_split.audio_track_states(timeline)},
                at_source_frame)
            if verdict["result"] != "passed":
                failed = [row["check"] for row in verdict["checks"]
                          if row["result"] != "passed"]
                return fail(
                    f"the split did not verify ({', '.join(failed)}): "
                    f"the cut must land on the exact frame and the linked "
                    f"audio must read back unchanged.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            journal_state["success"] = True
        emit([kv_block("split", {
                  "timeline": timeline.GetName(),
                  "name": span["name"],
                  "at_source_frame": at_source_frame,
                  "at_record_frame": record_frame,
                  "halves": len(halves),
                  "route": receipt["split_called_with"],
                  "audio_unchanged": "yes (the linked audio item reads back "
                  "unchanged)",
                  "audio_link": "preserved" if verdict["link_preserved"]
                  else "the picture was replaced, so the audio's partner "
                  "changed with it (measured)",
                  "verified": "yes (exact frame placement and an unchanged "
                  "linked audio item)",
              }),
              note,
              _journal_block(journal),
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def _overlaps_span(state, record_in: int, record_out: int) -> bool:
    """Whether a read-back item's record span overlaps a picture's."""
    try:
        start = int(state["record_in"])
        end = int(state["record_out"])
    except (KeyError, TypeError, ValueError):
        return False
    return start < int(record_out) and end > int(record_in)
def _plan_comp_carry(args, project, timeline, item, track_type, track_index,
                     new_in, new_out, comp_count):
    """The reel's own record of this clip's treatment, for the re-derivation.

    The record is the reel's Fusion manifest: it says what each clip's
    treatment IS, and the comp pass maps a clip spec to an item by
    source path and window.  So the record has to describe THIS reel -
    a manifest from another cut would write treatments onto the wrong
    clips, and the carry refuses rather than guess.
    """
    from library.tools import comp_carry, reel_read, reel_touchup

    project_folder = str(getattr(args, "project_folder", "") or "")
    if not project_folder:
        raise AxiError(
            "this clip carries a Fusion composition, and carrying it needs "
            "the reel's own record of its treatments - which lives in the "
            "project folder.",
            f"{TOOL} edit trim --timeline \"{timeline.GetName()}\" "
            "--project-folder <the project folder>")
    try:
        tracks = reel_read.read_tracks(timeline)
    except Exception as exc:  # noqa: BLE001 - an unreadable reel is a refusal
        raise AxiError(
            f"the reel's rows could not be read ({exc}), so the "
            "composition cannot be carried",
            f"{TOOL} items --timeline \"{timeline.GetName()}\"") from exc
    manifest = reel_touchup.recorded_fusion_manifest(
        project_folder, timeline.GetName(), tracks)
    items = list(timeline.GetItemListInTrack(track_type, track_index) or [])
    index = next((position for position, candidate in enumerate(items)
                  if str(candidate.GetUniqueId() or "")
                  == str(item.GetUniqueId() or "")), None)
    if index is None:
        raise AxiError(
            "the clip could not be located on its row, so its composition "
            "cannot be carried",
            f"{TOOL} items --timeline \"{timeline.GetName()}\"")
    pool_item = item.GetMediaPoolItem()
    source_file = ""
    source_fps = 0.0
    if pool_item is not None:
        try:
            source_file = str(pool_item.GetClipProperty("File Path") or "")
        except Exception:  # noqa: BLE001 - an unreadable path is empty
            source_file = ""
        try:
            source_fps = float(pool_item.GetClipProperty("FPS") or 0)
        except Exception:  # noqa: BLE001 - an unreadable fps is 0
            source_fps = 0.0
    row = f"{'V' if track_type == 'video' else 'A'}{track_index}"
    try:
        return comp_carry.plan_carry(
            project_folder=project_folder,
            timeline_name=timeline.GetName(),
            manifest=manifest, row=row, item_index=index, items=items,
            source_file=source_file,
            source_in_frame=int(new_in), source_out_frame=int(new_out),
            played_frames=int(new_out) - int(new_in) + 1,
            source_fps=source_fps)
    except comp_carry.CompCarryRefused as refused:
        raise AxiError(str(refused), str(
            getattr(refused, "fix", "")
            or f"{TOOL} items --timeline \"{timeline.GetName()}\"")) from refused


def cmd_edit_switch_camera(args) -> int:
    """Replace one speaker turn with an assigned camera's synced range.

    This is an exact-range replacement: the record span and track stay
    fixed, while the source pool item and source frames change together.
    """
    import os

    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    camera_file = os.path.abspath(args.camera_source_file)
    camera_path = os.path.realpath(camera_file)
    if not os.path.isfile(camera_path):
        return fail(f"assigned camera file does not exist: {camera_path}",
                    f"{TOOL} edit switch-camera --help")
    if not args.speaker_id.strip() or not args.sync_evidence_id.strip():
        return fail("speaker and sync evidence ids must be non-empty",
                    f"{TOOL} edit switch-camera --help")
    if args.assignment_basis not in {
            "project_declaration", "diarized_speaker_identity"}:
        return fail("assignment basis must name a project declaration or "
                    "diarized speaker identity",
                    f"{TOOL} edit switch-camera --help")
    if args.source_in < 0 or args.source_out < args.source_in:
        return fail("synced source frames must be a non-negative inclusive range",
                    f"{TOOL} edit switch-camera --help")
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
            item, track_type, track_index, uid = _edit_item_by_identity(
                timeline, args)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        if track_type != "video":
            return fail("multicam camera selection requires a video item",
                        f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        span = _item_span(item)
        record_in, record_out = int(span["record_in"]), int(span["record_out"])
        duration = record_out - record_in
        if args.source_out - args.source_in + 1 != duration:
            return fail(
                "synced camera source range does not match the turn's "
                "record duration; preserving edit points would be impossible",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        if not args.apply:
            emit([kv_block("switch_camera_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "target": uid, "camera_source": camera_path,
                      "source_frames": f"{args.source_in}..{args.source_out}",
                      "record_frames": f"{record_in}..{record_out}",
                      "track": f"video{track_index}",
                  }), note,
                  "dry run - pass --apply to replace under the Resolve lease",
                  help_block([f"{TOOL} items --timeline \"{timeline.GetName()}\""])])
            return 0
        try:
            _assert_cursor_on(resolve, timeline.GetName())
            pool = project.GetMediaPool()
            matches, scanned, unreadable = _relink_candidates(
                pool, camera_file, getattr(args, "timeout", None))
        except Exception as exc:
            return fail(f"camera source lookup failed ({exc})",
                        f"{TOOL} pool")
        if len(matches) != 1 or unreadable:
            return fail(
                f"assigned camera source resolves to {len(matches)} media-pool "
                f"items (scanned {scanned}, unreadable {unreadable}); refusing "
                "an ambiguous or absent source",
                f"{TOOL} pool")
        camera_pool_item = matches[0][0]
        try:
            before = _timeline_trim_snapshot(timeline)
        except AxiError as exc:
            return fail(f"camera switch refused before writing: {exc}",
                        f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        try:
            target_state = next(row["preservation_state"] for row in before
                                if row["uid"] == uid)
        except StopIteration:
            return fail("camera switch refused: target is absent from the "
                        "preservation snapshot",
                        f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        from library.tools import edit_state_preservation
        try:
            edit_state_preservation.preflight_source(
                target_state, camera_pool_item)
        except edit_state_preservation.StatePreservationError as exc:
            return fail(f"camera switch refused before writing: {exc}",
                        f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        refusal = _preflight_replace_delete(resolve, project, timeline, uid)
        if refusal:
            return fail(f"camera switch refused before writing: {refusal}",
                        f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "multicam_select_angle",
                      "timeline": timeline.GetName(), "target": uid,
                      "speaker_id": args.speaker_id,
                      "assignment_basis": args.assignment_basis,
                      "sync_evidence_id": args.sync_evidence_id,
                      "camera_source": camera_path,
                      "source_in": args.source_in,
                      "source_out": args.source_out,
                      "record_in": record_in, "record_out": record_out},
                gate_class="multicam_select_angle") as (journal, journal_state):
            drop_error = _drop_original(resolve, project, timeline, uid)
            if drop_error:
                return fail(drop_error, f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            # The re-place carries the approved transform, and a
            # transform write is anchored to a project folder: without
            # one the switch refuses ("cannot find the project folder for
            # transform write", measured 2026-10-20).
            multicam_context = {
                "project": project.GetName(),
                "project_folder": args.project_folder or None,
                "timeline_id": timeline.GetUniqueId(),
                "timeline_name": timeline.GetName(),
            }
            replacement, error = _replace_item(
                timeline, pool, None, args.source_in, args.source_out,
                record_in, track_index, 1, pool_item=camera_pool_item,
                preservation_state=target_state,
                write_context=multicam_context)
            if error:
                restored, restore_error = _restore_trim_snapshot(
                    timeline, pool, before, uid,
                    write_context=multicam_context)
                suffix = (f"; original timeline restored ({restored is not None})"
                          if not restore_error else
                          f"; restoration could not be proven ({restore_error})")
                return fail(f"camera switch failed ({error}){suffix}",
                            f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            new_span = _item_span(replacement)
            try:
                placed_pool_item = replacement.GetMediaPoolItem()
                placed_path = placed_pool_item.GetClipProperty("File Path")
                source_range_matches = (
                    int(new_span["source_in"]) == args.source_in
                    and _inclusive_source_end(
                        int(new_span["source_in"]),
                        int(new_span["source_out"]), duration)
                    == args.source_out)
                verified = (
                    int(new_span["record_in"]) == record_in
                    and int(new_span["record_out"]) - record_in == duration
                    and source_range_matches
                    and os.path.realpath(str(placed_path)) == camera_path)
            except Exception:
                verified = False
            if not verified:
                restored, restore_error = _restore_trim_snapshot(
                    timeline, pool, before, uid)
                return fail(
                    "camera replacement failed source or record-span "
                    f"read-back (restored={restored is not None}, "
                    f"restore_error={restore_error})",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            journal_state["success"] = True
        emit([kv_block("camera_selected", {
                  "timeline": timeline.GetName(), "source": camera_path,
                  "source_frames": f"{args.source_in}..{args.source_out}",
                  "record_frames": f"{record_in}..{record_out}",
                  "verified": "yes (source range and edit points re-read)"}),
              note, _journal_block(journal)])
        return 0


def _title_current(item) -> str:
    for key in _TITLE_KEYS:
        try:
            value = item.GetProperty(key)
        except Exception:
            continue
        if value:
            return str(value)
    return ""


def cmd_edit_title(args) -> int:
    """Set a title card's text, verified by read-back.

    Tries the known title keys plain-first; a build that takes none
    is refused with the Fusion-comp route named instead of guessed
    at - the same text often lives on the item's TextPlus input.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, _uid = _edit_item(
                timeline, args.track, args.index)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        current = _title_current(item)
        if not args.text:
            return fail("title needs --text.",
                        f"{TOOL} edit title --help")
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        else:
            emit([kv_block("title_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "name": span["name"],
                      "current": preview(current, False),
                      "text": preview(args.text, False),
                  }),
                  note,
                  f"dry run - pass --apply to write under the "
                  f"Resolve lease",
                  help_block([
                      f"{TOOL} edit title --timeline "
                      f"\"{timeline.GetName()}\" --track "
                      f"{track_type}{track_index} --index {args.index} "
                      f"--text \"...\" --apply"])])
            return 0
        attempts = []
        for key in _TITLE_KEYS:
            try:
                ok = bool(item.SetProperty(key, args.text))
            except Exception as exc:
                attempts.append(f"{key}: raised {exc}")
                continue
            if not ok:
                attempts.append(f"{key}: refused")
                continue
            try:
                back = item.GetProperty(key)
            except Exception as exc:
                attempts.append(f"{key}: wrote, re-read raised {exc}")
                continue
            if back == args.text:
                emit([kv_block("titled", {
                          "timeline": timeline.GetName(),
                          "name": span["name"],
                          "key": key,
                          "verified": "yes (read back equal)",
                      }),
                      note,
                      help_block([f"{TOOL} items --timeline "
                                  f"\"{timeline.GetName()}\""])])
                return 0
            attempts.append(f"{key}: wrote, re-read differs")
        return fail(
            f"no title key took the text ({'; '.join(attempts)}) - "
            f"the text may live on the item's Fusion TextPlus input, "
            f"which this verb does not write; set it in the "
            f"pipeline build.",
            f"{TOOL} items --timeline \"{timeline.GetName()}\"")


def cmd_edit_speed(args) -> int:
    """Set one item's constant playback speed, verified by re-read.

    `SetSpeed` takes a constant percentage only (0.0 = freeze frame):
    the 21.1 stub carries no speed-curve API, so a ramp request is
    refused rather than rounded to a constant - a ramp reaches the
    timeline as stepped constant-speed segments, one `SetSpeed` per
    step, planned as `speed_ramp` with `params.segments` (step
    4.03) and applied by the build's native applicator
    (`library/tools/native_ops_apply.py`). `--freeze` spells the
    0.0 case out loud; `--percent 0` is refused so a freeze is never
    a typo. The read-back is `GetSpeed`'s Percentage, compared with
    a 1e-6 tolerance - a disagreeing re-read fails, and a build
    without `GetSpeed` fails naming it rather than claiming the
    write off the True alone. Measured 2026-09-24: 40% re-reads
    40.0 with the timeline rippled, and 0.0 re-reads 0.0 on a fresh
    item; 0.0 after a rippled speed change re-reads 100.0 (the
    pixels still freeze), and that case refuses here.

    The linked dialogue rides the same write (finding 17): after the
    video item verifies, every same-span audio item from the SAME
    source file is retimed to the same Percentage and judged by its
    own re-read - a retimed talking shot without its dialogue loses
    sync. Same-span audio from another source (the bed) is left at
    full speed AND named; an unreadable source refuses rather than
    guesses; an audio refusal fails the verb and puts the video item
    back to its pre-op speed. The dry run names the audio it would
    touch.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, _uid = _edit_item(
                timeline, args.track, args.index)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        percent = getattr(args, "percent", None)
        freeze = bool(getattr(args, "freeze", False))
        if freeze and percent is not None:
            return fail("pass --freeze or --percent, not both.",
                        f"{TOOL} edit speed --help")
        if freeze:
            percent = 0.0
        if percent is None:
            return fail("speed needs --percent <pct> or --freeze.",
                        f"{TOOL} edit speed --help")
        try:
            percent = float(percent)
        except (TypeError, ValueError):
            return fail(f"bad --percent {percent!r}: a number.",
                        f"{TOOL} edit speed --help")
        if percent <= 0.0 and not freeze:
            return fail(f"--percent {percent} would freeze: pass "
                        f"--freeze to say so out loud.",
                        f"{TOOL} edit speed --help")
        ripple = bool(getattr(args, "ripple", False))
        try:
            current_opts = item.GetSpeed()
            current = (current_opts or {}).get("Percentage", "")
        except AttributeError:
            current = "    (GetSpeed absent on this build)"
        except Exception:
            current = ""
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        else:
            _dry_linked, _dry_skipped, _dry_refused, _dry_unchecked = \
                _linked_audio_for_speed(timeline, item, span)
            if _dry_refused:
                _dry_audio = f"would refuse - {_dry_refused}"
            elif _dry_unchecked:
                _dry_audio = f"unchecked - {_dry_unchecked}"
            elif _dry_linked:
                _dry_names = []
                for _a in _dry_linked:
                    try:
                        _dry_names.append(_a.GetName())
                    except Exception:
                        _dry_names.append("?")
                _dry_audio = "would also retime: " + ", ".join(_dry_names)
                if _dry_skipped:
                    _dry_audio += (" (left at full speed: "
                                   + ", ".join(_dry_skipped) + ")")
            elif _dry_skipped:
                _dry_audio = ("video only for the retime - left at full "
                              "speed: " + ", ".join(_dry_skipped))
            else:
                _dry_audio = "video only - no same-span audio"
            emit([kv_block("speed_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "name": span["name"],
                      "current_percent": current,
                      "percent": percent,
                      "ripple": "yes" if ripple else "no",
                      "audio": _dry_audio,
                      "note": ("constant speed only - the 21.1 stub "
                               "carries no speed-curve API"),
                  }),
                  note,
                  f"dry run - pass --apply to write under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([
                      f"{TOOL} edit speed --timeline "
                      f"\"{timeline.GetName()}\" --track "
                      f"{track_type}{track_index} --index {args.index} "
                      f"--percent {percent} --apply"])])
            return 0
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "edit_speed", "timeline": timeline.GetName(),
                      "name": span["name"], "percent": percent,
                      "ripple": ripple},
                gate_class="edit_speed") as (journal, journal_state):
            try:
                wrote = bool(item.SetSpeed({"Percentage": percent,
                                            "RippleTimeline": ripple}))
            except Exception as exc:
                return fail(f"SetSpeed raised ({exc}) - verify by hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            if not wrote:
                return fail(
                    f"SetSpeed({percent}%) answered False on "
                    f"{span['name']!r} - nothing was claimed.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            try:
                back_opts = item.GetSpeed()
            except AttributeError:
                return fail(
                    f"SetSpeed answered True but this build has no "
                    f"GetSpeed to re-read - refusing to claim it.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            except Exception as exc:
                return fail(f"the speed re-read raised ({exc}) - verify "
                            f"by hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            try:
                back = float((back_opts or {}).get("Percentage"))
            except (TypeError, ValueError):
                return fail(
                    f"SetSpeed answered True but GetSpeed re-reads "
                    f"{back_opts!r} - refusing to claim it.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            if abs(back - percent) > 1e-6:
                return fail(
                    f"SetSpeed reports True and re-reads {back} for "
                    f"{percent} - refusing to claim it.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            # Finding 17: a retimed talking shot without its dialogue
            # loses sync, so the linked audio rides the same write -
            # selected by `_linked_audio_for_speed` (same span, same
            # source), each judged by its own `GetSpeed` re-read. An
            # audio refusal fails the verb AND puts the video item back
            # to its pre-op speed rather than leave the pair split.
            _linked, _skipped, _refused, _unchecked = \
                _linked_audio_for_speed(timeline, item, span)
            if _refused:
                return fail(
                    f"SetSpeed({percent}%) on {span['name']!r} verified, "
                    f"but {_refused}.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            if _unchecked:
                audio_note = f"linkage unchecked - {_unchecked}"
            elif _linked:
                try:
                    _fps = float(timeline.GetSetting("timelineFrameRate"))
                except Exception:
                    return fail(
                        f"SetSpeed({percent}%) on {span['name']!r} "
                        f"verified, but the timeline rate will not "
                        f"read and {len(_linked)} linked audio "
                        f"item(s) need retiming - refusing to "
                        f"judge them.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
                from library.tools import native_ops_apply as _apply
                _audio_ok, _audio_bad = _apply.retime_linked_audio(
                    item, span["name"], percent, _fps, _linked, set())
                if _audio_bad is not None:
                    _restored = ""
                    try:
                        _pre = float(current)
                        if bool(item.SetSpeed({"Percentage": _pre,
                                               "RippleTimeline": ripple})):
                            _restored = (f" the video item was put "
                                         f"back to {_pre:g}%")
                    except Exception:
                        pass
                    return fail(
                        f"SetSpeed({percent}%) on {span['name']!r} "
                        f"verified, but its linked audio refused - "
                        f"{_audio_bad['what']}."
                        f"{_restored or ' the video item could not be put back - verify the pair by hand'}",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
                audio_note = ("retimed with the picture: "
                              + ", ".join(r["item"] for r in _audio_ok))
                if _skipped:
                    audio_note += (" (left at full speed, different "
                                   "source: " + ", ".join(_skipped) + ")")
            elif _skipped:
                audio_note = ("left at full speed, different source: "
                              + ", ".join(_skipped))
            else:
                audio_note = "video only - no same-span audio"
            journal_state["success"] = True
        emit([kv_block("speed", {
                  "timeline": timeline.GetName(),
                  "name": span["name"],
                  "percent": percent,
                  "ripple": "yes" if ripple else "no",
                  "verified": "yes (GetSpeed re-reads equal)",
                  "audio": audio_note,
              }),
              note,
              _journal_block(journal),
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def cmd_edit_transition(args) -> int:
    """Native transitions at a V1 cut, verified by the returned item.

    Without `--apply` this reports every cut that can carry one,
    and with `--index` it plans the exact `AddTransition` payload.
    With `--apply` it places Resolve's OWN transition (simple,
    fusion, ofx or audio - measured 2026-09-24: Cross Dissolve in
    simple and fusion, Slide and Smooth Cut in simple, Spin in
    fusion, and Cross Fade +3 dB in audio read back; Dip to Color
    Dissolve, Push, Whip Pan, Blur Dissolve and Cross Fade -3 dB
    answered empty) at the indexed clip's edge and judges the write
    by what comes back: `AddTransition` returns the transition item
    or None, so an empty answer refuses naming the type and category
    tried, and a returned item whose span will not read refuses
    naming that. The drawn Fusion-comp transitions (fade_to_black
    and friends) still reach timelines through the pipeline build
    (compile_manifest + apply_fusion_comps), never through here.
    The granted native transitions also reach timelines from the
    plan: step 4.02 admits them by name, compile_manifest carries
    them as `native_transitions`, and the build's native applicator
    (`library/tools/native_ops_apply.py`) places each through this
    same call with the same read-back. A measured-refused name
    (Whip Pan, Dip, Push, Blur Dissolve) refuses at plan time
    rather than here - it never arrives as a downgraded hard cut.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=bool(getattr(args, "apply", False))):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            clips = (timeline.GetItemListInTrack("video", 1) or [])
        except Exception as exc:
            return fail(f"timeline {timeline.GetName()!r} would not "
                        f"read V1 ({exc}).",
                        f"{TOOL} timeline list")
        rows = []
        for first, second in zip(clips, clips[1:]):
            try:
                cut = first.GetEnd()
                outgoing = first.GetName()
                incoming = second.GetName()
            except Exception:
                continue
            rows.append({"cut": cut, "outgoing": outgoing,
                         "incoming": incoming})
        index = getattr(args, "index", None)
        want_type = getattr(args, "type", None) or "Cross Dissolve"
        category = getattr(args, "category", None) or "simple"
        position = getattr(args, "position", None) or "start"
        alignment = getattr(args, "alignment", None) or "center"
        duration = getattr(args, "duration", None)
        apply = bool(getattr(args, "apply", False))
        if category not in ("simple", "fusion", "ofx", "audio"):
            return fail(
                f"bad category {category!r}: simple|fusion|ofx|audio.",
                f"{TOOL} edit transition --help")
        if position not in ("start", "end"):
            return fail(f"bad position {position!r}: start|end.",
                        f"{TOOL} edit transition --help")
        if alignment not in ("left", "center", "right"):
            return fail(f"bad alignment {alignment!r}: "
                        f"left|center|right.",
                        f"{TOOL} edit transition --help")
        if duration is not None and duration < 1:
            return fail(f"bad duration {duration!r}: frames, >= 1.",
                        f"{TOOL} edit transition --help")
        if index is None and not apply:
            emit([kv_block("carriers", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "cuts": len(rows),
                  }),
                  note,
                  table("cuts", rows, ["cut", "outgoing", "incoming"]),
                  help_block([f"{TOOL} items --timeline "
                              f"\"{timeline.GetName()}\""])])
            return 0
        if index is None:
            return fail(
                f"{len(rows)} carrier cut(s) found - name the incoming "
                f"V1 clip with --index (0-based) so the write cannot "
                f"land on the wrong cut.",
                f"{TOOL} edit transition --timeline "
                f"\"{timeline.GetName()}\"")
        if index < 0 or index >= len(clips):
            return fail(
                f"V1 holds {len(clips)} item(s) - index {index} names "
                f"nothing.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        if position == "start" and index < 1:
            return fail(
                f"a transition at the START of V1 item 0 joins "
                f"nothing - index 1 or later carries the cut.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        if position == "end" and index > len(clips) - 2:
            return fail(
                f"a transition at the END of the last V1 item trails "
                f"onto nothing.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        try:
            item = clips[index]
            item_name = item.GetName()
            neighbour = (clips[index - 1].GetName()
                         if position == "start"
                         else clips[index + 1].GetName())
            cut = (clips[index - 1].GetEnd()
                   if position == "start" else item.GetEnd())
        except Exception as exc:
            return fail(f"the cut would not read ({exc}) - nothing "
                        f"was written.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        payload = {"type": want_type, "category": category,
                   "position": position, "alignment": alignment}
        if duration is not None:
            payload["duration"] = duration
        if not apply:
            emit([kv_block("transition_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "cut": cut,
                      "outgoing": (neighbour if position == "start"
                                   else item_name),
                      "incoming": (item_name if position == "start"
                                   else neighbour),
                      "type": want_type,
                      "category": category,
                      "position": position,
                      "alignment": alignment,
                      "duration": (duration if duration is not None
                                   else "(Resolve default)"),
                  }),
                  note,
                  f"dry run - pass --apply to place under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([
                      f"{TOOL} edit transition --timeline "
                      f"\"{timeline.GetName()}\" --index {index} "
                      f"--type \"{want_type}\" --category {category} "
                      f"--apply"])])
            return 0
        try:
            _assert_cursor_on(resolve, timeline.GetName())
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "edit_transition",
                      "timeline": timeline.GetName(), "cut": cut,
                      "type": want_type, "category": category,
                      "position": position, "alignment": alignment,
                      "duration": duration},
                gate_class="edit_transition") as (journal, journal_state):
            try:
                from library.tools.resolve_api_capabilities import callable_method
                add_transition = callable_method(item, "AddTransition")
                if add_transition is None:
                    return fail(
                        "Resolve does not expose TimelineItem.AddTransition",
                        "use a transition supported by this Resolve version")
                placed = add_transition(dict(payload))
            except Exception as exc:
                return fail(f"AddTransition raised ({exc}) - verify by "
                            f"hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            if placed is None or placed is False:
                return fail(
                    f"AddTransition({want_type!r}, {category!r}) answered "
                    f"empty on {item_name!r} - the type or category took "
                    f"nothing on this build (Cross Dissolve in simple and "
                    f"fusion is the proven pair; a clip starting at "
                    f"source 0 has no head handles for a centered "
                    f"transition).",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            try:
                tr_span = {"name": placed.GetName(),
                           "record_in": placed.GetStart(),
                           "record_out": placed.GetEnd(),
                           "duration": placed.GetDuration()}
            except Exception as exc:
                return fail(
                    f"AddTransition answered, but the transition item "
                    f"would not read its span ({exc}) - refusing to "
                    f"claim it.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            journal_state["success"] = True
        emit([kv_block("transition", {
                  "timeline": timeline.GetName(),
                  "cut": cut,
                  "type": want_type,
                  "category": category,
                  "transition": tr_span["name"],
                  "duration": tr_span["duration"],
                  "verified": "yes (returned a transition item "
                  "whose span reads)",
              }),
              note,
              _journal_block(journal),
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def _current_folder_name(pool) -> str:
    try:
        folder = pool.GetCurrentFolder()
    except Exception:
        return "(unknown folder)"
    try:
        return folder.GetName() or "(unknown folder)"
    except Exception:
        return "(unknown folder)"


def cmd_ingest(args) -> int:
    """Import files into the pool's current folder, verified by name.

    `ImportMedia` lands in the CURRENT pool folder, so the dry run
    names it - and `--bin` is refused rather than moving the folder
    there and back around the write. Missing local files refuse
    before any Resolve contact.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            pool = project.GetMediaPool()
        except Exception as exc:
            return fail(f"project {project.GetName()!r} would not open "
                        f"its media pool ({exc}).",
                        f"{TOOL} cursor")
        folder_name = _current_folder_name(pool)
        missing = [p for p in args.paths
                   if not os.path.exists(p)]
        if missing:
            return fail(
                f"{len(missing)} path(s) are not on disk: {missing} - "
                f"nothing was imported.",
                f"{TOOL} ingest <existing-paths...>")
        before = _cursor_name(resolve)
        if not args.apply:
            emit([kv_block("ingest_plan", {
                      "project": project.GetName(),
                      "folder": folder_name,
                      "files": len(args.paths),
                  }),
                  table("files", [{"file": p} for p in args.paths],
                        ["file"]),
                  f"dry run - pass --apply to import under the "
                  f"Resolve lease",
                  help_block([f"{TOOL} ingest {' '.join(args.paths)} "
                              f"--apply"])])
            return 0
        try:
            imported = pool.ImportMedia(list(args.paths)) or []
        except Exception as exc:
            return fail(f"import raised ({exc}) - verify by hand.",
                        f"{TOOL} pool")
        names = []
        for item in imported:
            try:
                names.append(item.GetName())
            except Exception:
                continue
        after = _cursor_name(resolve)
        if len(imported) != len(args.paths):
            return fail(
                f"import returned {len(imported)} item(s) for "
                f"{len(args.paths)} path(s) ({names}) - partial, "
                f"verify by hand.",
                f"{TOOL} pool")
        emit([kv_block("ingested", {
                  "project": project.GetName(),
                  "folder": folder_name,
                  "files": len(names),
                  "cursor_before": before,
                  "cursor_after": after,
                  "verified": "yes (one pool item per path, named)",
              }),
              table("imported", [{"file": n} for n in names], ["file"]),
              help_block([f"{TOOL} pool"])])
        return 0


def _relink_candidates(pool, old_path: str, deadline: float | None) -> tuple:
    """Pool items whose File Path is `old_path`, with their bins.

    Returns `(candidates, scanned, unreadable)` where each candidate is
    `(clip, name, bin, file)`. Every Resolve call runs under
    `resolve_deadline.call_with_deadline` naming the exact call, so a
    hang stops with the call named instead of waiting indefinitely.
    Judged by what each call RETURNS, never by `hasattr` (AGENTS.md 5).
    """
    from library.tools.resolve_deadline import call_with_deadline
    try:
        root = call_with_deadline(
            "media pool GetRootFolder", pool.GetRootFolder,
            timeout_s=deadline)
    except Exception as exc:
        raise AxiError(
            f"the media pool would not open its root folder ({exc}).",
            f"{TOOL} cursor") from exc
    candidates, scanned, unreadable = [], 0, 0
    stack = [(root, "")]
    while stack:
        folder, bin_path = stack.pop()
        try:
            clips = call_with_deadline(
                f"bin {bin_path or '/'} GetClipList",
                folder.GetClipList, timeout_s=deadline) or []
        except Exception:
            unreadable += 1
            clips = []
        for clip in clips:
            scanned += 1
            try:
                name = call_with_deadline(
                    "clip GetName", clip.GetName,
                    timeout_s=deadline)
            except Exception:
                unreadable += 1
                continue
            try:
                props = call_with_deadline(
                    f"clip {name!r} GetClipProperty",
                    clip.GetClipProperty, timeout_s=deadline) or {}
            except Exception:
                unreadable += 1
                continue
            if not isinstance(props, dict):
                unreadable += 1
                continue
            file_path = props.get("File Path") or ""
            if file_path == old_path:
                candidates.append((clip, name, bin_path or "/ (root)",
                                   file_path))
        try:
            subs = call_with_deadline(
                f"bin {bin_path or '/'} GetSubFolderList",
                folder.GetSubFolderList, timeout_s=deadline) or []
        except Exception:
            continue
        for sub in subs:
            try:
                sub_name = call_with_deadline(
                    "bin GetName", sub.GetName,
                    timeout_s=deadline)
            except Exception:
                unreadable += 1
                continue
            stack.append((sub, f"{bin_path}/{sub_name}" if bin_path
                         else sub_name))
    return candidates, scanned, unreadable


def cmd_relink(args) -> int:
    """Re-point pool clips at moved media, verified by re-read.

    When ingested media moves on disk - or a proxy file is created the
    pool should point at - `ReplaceClip` re-points each pool item whose
    `File Path` is `--from`, and the verdict is the re-read `File Path`,
    never the return alone. Dry-run by default; `--apply` writes under
    the exclusive lease. Missing `--to` files refuse before any Resolve
    contact, the way `ingest` refuses missing sources.
    """
    import os as _os
    from library.tools.resolve_deadline import (
        ResolveCallTimeout,
        call_with_deadline,
    )
    old_path = getattr(args, "old_path", "") or ""
    new_path = getattr(args, "new_path", "") or ""
    if not old_path or not new_path:
        return fail("relink needs --from <old-path> --to <new-path>.",
                    f"{TOOL} relink --from <old> --to <new>")
    if not _os.path.exists(new_path):
        return fail(f"{new_path!r} is not on disk - nothing was relinked.",
                    f"{TOOL} relink --from {old_path} --to <existing-path>")
    deadline = getattr(args, "timeout", None) or None
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    try:
        with _lease(exclusive=args.apply):
            project = _project(resolve, args.project)
            try:
                pool = call_with_deadline(
                    "project GetMediaPool", project.GetMediaPool,
                    timeout_s=deadline)
            except (AxiError, ResolveCallTimeout):
                raise
            except Exception as exc:
                return fail(f"project {project.GetName()!r} would not open "
                            f"its media pool ({exc}).",
                            f"{TOOL} cursor")
            try:
                candidates, scanned, unreadable = _relink_candidates(
                    pool, old_path, deadline)
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
            except ResolveCallTimeout as exc:
                return fail(f"Resolve stopped answering: {exc}",
                            f"{TOOL} pool")
            wanted_clip = getattr(args, "clip", "") or ""
            if wanted_clip:
                candidates = [c for c in candidates if c[1] == wanted_clip]
            before = _cursor_name(resolve)
            if not candidates:
                emit([kv_block("relink_plan", {
                          "project": project.GetName(),
                          "from": old_path,
                          "to": new_path,
                          "scanned": scanned,
                          "unreadable": unreadable,
                          "matched": 0,
                      }),
                      f"no pool item points at {old_path!r} "
                      f"({scanned} scanned) - nothing to relink.",
                      help_block([f"{TOOL} pool"])])
                return 1
            if not args.apply:
                emit([kv_block("relink_plan", {
                          "project": project.GetName(),
                          "from": old_path,
                          "to": new_path,
                          "scanned": scanned,
                          "unreadable": unreadable,
                          "matched": len(candidates),
                      }),
                      table("matched", [{
                          "name": name, "bin": bin_path, "file": file_path,
                      } for _, name, bin_path, file_path in candidates],
                      ["name", "bin", "file"]),
                      f"dry run - pass --apply to re-point under the "
                      f"Resolve lease",
                      help_block([f"{TOOL} relink --from {old_path} "
                                  f"--to {new_path} --apply"])])
                return 0
            relinked, failed = [], []
            for clip, name, bin_path, file_path in candidates:
                try:
                    ok = call_with_deadline(
                        f"clip {name!r} ReplaceClip",
                        clip.ReplaceClip, new_path,
                        timeout_s=deadline)
                except ResolveCallTimeout as exc:
                    failed.append({"name": name, "bin": bin_path,
                                   "reason": str(exc)})
                    continue
                except Exception as exc:
                    failed.append({"name": name, "bin": bin_path,
                                   "reason": f"ReplaceClip raised ({exc})"})
                    continue
                if not ok:
                    failed.append({"name": name, "bin": bin_path,
                                   "reason": "ReplaceClip returned false"})
                    continue
                try:
                    back = call_with_deadline(
                        f"clip {name!r} GetClipProperty File Path",
                        clip.GetClipProperty, "File Path",
                        timeout_s=deadline)
                except ResolveCallTimeout as exc:
                    failed.append({"name": name, "bin": bin_path,
                                   "reason": f"re-read timed out: {exc}"})
                    continue
                except Exception as exc:
                    failed.append({"name": name, "bin": bin_path,
                                   "reason": f"re-read raised ({exc})"})
                    continue
                if back != new_path:
                    failed.append({"name": name, "bin": bin_path,
                                   "reason": f"reads back {back!r}, "
                                             f"not {new_path!r}"})
                    continue
                relinked.append({"name": name, "bin": bin_path,
                                 "file": back})
            after = _cursor_name(resolve)
            emit([kv_block("relinked", {
                      "project": project.GetName(),
                      "from": old_path,
                      "to": new_path,
                      "matched": len(candidates),
                      "relinked": len(relinked),
                      "failed": len(failed),
                      "cursor_before": before,
                      "cursor_after": after,
                      "verified": "yes (File Path re-read per clip)",
                  }),
                  table("relinked", relinked,
                        ["name", "bin", "file"]) if relinked else "",
                  table("failed", failed,
                        ["name", "bin", "reason"]) if failed else "",
                  help_block([f"{TOOL} pool"])])
            return 0 if not failed else 1
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    except ResolveCallTimeout as exc:
        return fail(f"Resolve stopped answering: {exc}",
                    f"{TOOL} pool")


def _render_job_rows(project, ids=None) -> list:
    try:
        jobs = project.GetRenderJobList() or []
    except Exception:
        return []
    rows = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if ids is not None and job.get("JobId") not in ids:
            continue
        job_id = job.get("JobId") or ""
        try:
            status = project.GetRenderJobStatus(job_id) or {}
        except Exception:
            status = {}
        if not isinstance(status, dict):
            status = {}
        rows.append({
            "job": job.get("RenderJobName") or job_id,
            "id": job_id,
            "timeline": job.get("TimelineName") or "",
            "state": _render_state(status),
            "output": "/".join([
                str(job.get("TargetDir") or "").rstrip("/"),
                str(job.get("OutputFilename") or "")]).strip("/"),
        })
    return rows


def _wait_for_render_completion(project) -> bool:
    """Keep the caller's exclusive lease until Resolve reads back idle.

    A failed read is not evidence of completion. Retry it while retaining
    the lease so another client cannot enter during an uncertain render.
    Returns whether this command observed rendering in progress.
    """
    saw_rendering = False
    last_notice = 0.0
    while True:
        try:
            rendering = bool(project.IsRenderingInProgress())
        # An unreadable state cannot be treated as completion.
        except Exception as exc:  # noqa: BLE001
            now = _time.monotonic()
            if now - last_notice >= RENDER_READ_ERROR_NOTICE_SECONDS:
                print("waiting: Resolve would not report render state "
                      f"({exc}); keeping the instance lease",
                      file=sys.stderr)
                last_notice = now
        else:
            if not rendering:
                return saw_rendering
            if not saw_rendering:
                print("waiting: render is in progress; keeping the "
                      "instance lease until completion is read back",
                      file=sys.stderr)
            saw_rendering = True
        _time.sleep(RENDER_POLL_SECONDS)


def cmd_render_queue(args) -> int:
    """Queue the reel for render under a preset, verified by JobId.

    `AddRenderJob` renders the CURRENT timeline from the CURRENT
    settings, so the cursor is asserted and the preset pinned first:
    without `--preset` the dry run says plainly that today's settings
    go. The read-back names the real output path off the queued row.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        if args.preset:
            try:
                presets = project.GetRenderPresetList() or []
            except Exception as exc:
                return fail(
                    f"project {project.GetName()!r} would not list "
                    f"render presets ({exc}).",
                    f"{TOOL} project")
            if args.preset not in list(presets):
                return fail(
                    f"no render preset {args.preset!r}.",
                    f"{TOOL} project")
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        else:
            emit([kv_block("queue_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "preset": args.preset or
                      "(current settings, unchanged - pass --preset "
                      "to pin them)",
                  }),
                  note,
                  f"dry run - pass --apply to queue under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([
                      f"{TOOL} render queue --timeline "
                      f"\"{timeline.GetName()}\""
                      f"{' --preset ' + args.preset if args.preset else ''} "
                      f"--apply"])])
            return 0
        if args.preset:
            try:
                loaded = bool(project.LoadRenderPreset(args.preset))
            except Exception as exc:
                return fail(f"preset {args.preset!r} would not load "
                            f"({exc}) - nothing queued.",
                            f"{TOOL} project")
            if not loaded:
                return fail(f"preset {args.preset!r} would not load - "
                            f"nothing queued.",
                            f"{TOOL} project")
        try:
            job_id = project.AddRenderJob()
        except Exception as exc:
            return fail(f"queueing raised ({exc}) - verify by hand.",
                        f"{TOOL} renders")
        if not job_id:
            return fail("queueing answered empty - nothing queued.",
                        f"{TOOL} renders")
        rows = _render_job_rows(project, ids=[job_id])
        if not rows:
            return fail(
                f"job {job_id!r} is not in the queue on re-read - "
                f"verify by hand.",
                f"{TOOL} renders")
        row = rows[0]
        if row["timeline"] != timeline.GetName():
            return fail(
                f"queued job {job_id!r} names {row['timeline']!r}, not "
                f"{timeline.GetName()!r} - the cursor moved mid-flight. "
                f"Remove it by hand.",
                f"{TOOL} renders")
        emit([kv_block("queued", {
                  "timeline": timeline.GetName(),
                  "job": row["job"],
                  "output": row["output"],
                  "verified": "yes (JobId in the queue, timeline "
                  "matches)",
              }),
              note,
              help_block([f"{TOOL} renders",
                          f"{TOOL} render start --job {job_id}"])])
        return 0


def cmd_render_start(args) -> int:
    """Start render jobs and wait under the exclusive lease to completion.

    Resolve starts rendering asynchronously, so returning after the first
    progress read would let another leased client enter during the render.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        rows = _render_job_rows(project)
        if not rows:
            return fail("the render queue is empty - queue first.",
                        f"{TOOL} render queue --timeline \"<name>\" "
                        f"--apply")
        if args.all:
            ids = [r["id"] for r in rows if r["id"]]
            wanted = rows
        else:
            if not args.job:
                return fail("name --job <id> (repeatable), or --all.",
                            f"{TOOL} renders")
            ids = [j for j in args.job if j]
            wanted = [r for r in rows if r["id"] in ids]
            missing = [j for j in ids
                       if j not in {r["id"] for r in rows}]
            if missing:
                return fail(f"no queued job(s): {missing}.",
                            f"{TOOL} renders")
        before = _cursor_name(resolve)
        if not args.apply:
            emit([kv_block("start_plan", {
                      "jobs": len(wanted),
                      "cursor": before,
                  }),
                  table("jobs", wanted,
                        ["job", "id", "timeline", "state"]),
                  f"dry run - pass --apply to start rendering (this "
                  f"spends real machine time)",
                  help_block([f"{TOOL} renders"])])
            return 0
        start_error = None
        try:
            started = bool(project.StartRendering(ids, False))
        except Exception as exc:
            # Start may have taken effect before its call raised. Keep the
            # lease until Resolve can establish that no render remains.
            start_error = exc
            started = False
        saw_rendering = _wait_for_render_completion(project)
        after = _cursor_name(resolve)
        if start_error is not None:
            return fail(f"starting raised ({start_error}); Resolve now "
                        "reports no render in progress.",
                        f"{TOOL} renders")
        if not started and not saw_rendering:
            return fail("start reported "
                        f"{started} and nothing is rendering.",
                        f"{TOOL} renders")
        verified = ("yes (already rendering, then idle on completion "
                    "read-back)" if not started else
                    "yes (idle on completion read-back)")
        emit([kv_block("completed", {
                  "jobs": len(wanted),
                  "cursor_before": before,
                  "cursor_after": after,
                  "verified": verified,
              }),
              table("jobs", _render_job_rows(project,
                                             ids=set(ids)),
                    ["job", "id", "timeline", "state"]),
              help_block([f"{TOOL} renders",
                          f"{TOOL} render stop"])])
        return 0


def cmd_render_stop(args) -> int:
    """Stop whatever is rendering. Read back to stopped, or failed."""
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            rendering = bool(project.IsRenderingInProgress())
        except Exception:
            rendering = False
        before = _cursor_name(resolve)
        if not args.apply:
            emit([kv_block("stop_plan", {
                      "project": project.GetName(),
                      "rendering": "yes" if rendering else "no",
                      "cursor": before,
                  }),
                  f"dry run - pass --apply to stop",
                  help_block([f"{TOOL} renders"])])
            return 0
        try:
            project.StopRendering()
        except Exception as exc:
            return fail(f"stopping raised ({exc}) - verify by hand.",
                        f"{TOOL} renders")
        try:
            rendering = bool(project.IsRenderingInProgress())
        except Exception:
            rendering = True
        after = _cursor_name(resolve)
        if rendering:
            return fail("stop answered but rendering continues.",
                        f"{TOOL} renders")
        emit([kv_block("stopped", {
                  "project": project.GetName(),
                  "cursor_before": before,
                  "cursor_after": after,
                  "verified": "yes (not rendering on re-read)",
              }),
              help_block([f"{TOOL} renders"])])
        return 0


def cmd_render_inspect(args) -> int:
    """Render the OPEN timeline to a scratch file, for inspection.

    The one render that changes nothing in Resolve: it writes a file,
    restores the render format and codec it borrowed, and deletes the
    render job it queued. No timeline, no clip and no media-pool item
    is touched. That is what makes it broker-eligible
    (`jobs._BROKER_ALLOWLIST`): the review loop has to LOOK at a
    timeline, and a render the caller drives itself is not a job the
    broker can queue, observe or coalesce with every other Resolve job.

    It renders the OPEN timeline on purpose - the caller is looking at
    the screen - and the cursor is not moved to do it. The render is
    the ONE implementation (`execution.resolve_render`), so a file made
    here is the same file `ren deliver` and `ren preview` make.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    if not args.out:
        return fail("render inspect needs --out <directory>.",
                    f"{TOOL} render inspect --out <directory> --apply")
    out_dir = os.path.abspath(args.out)
    render = None
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, _is_current, _note = _target_timeline(project, "",
                                                            resolve=resolve)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        if not args.apply:
            emit([kv_block("inspect_plan", {
                      "project": project.GetName(),
                      "timeline": timeline.GetName(),
                      "out": out_dir,
                  }),
                  "dry run - pass --apply to render the open timeline "
                  "for inspection (this spends real machine time)",
                  help_block([f"{TOOL} render inspect --out {out_dir} "
                              "--apply"])])
            return 0
        from library.tools.execution import resolve_render
        os.makedirs(out_dir, exist_ok=True)
        render = resolve_render.render_timeline_under_existing_lease(
            timeline_name="",
            output_dir=out_dir,
            output_name=(args.name or timeline.GetName()),
            fmt="mp4", codec="H264",
            resolve=resolve)
    # The file is checked AFTER the lease is released: the broker owns
    # Resolve, and an ffprobe is not Resolve work.
    report = resolve_render.validate_render_output(render)
    # What this route produces is a PRE-MASTER render: the file is on
    # disk and carries audio, and it has NOT been brought to the
    # delivery loudness. The old wording - "verified: yes" - read as
    # though the file were finished, and the section-12 demo shipped
    # exactly this file: 9.18 dB under the delivery target with
    # nothing on record that said so. The record beside the file now
    # says what the file is, and the payload says it out loud.
    from library.tools import delivery_identity
    record = delivery_identity.write_record(
        report["output_path"],
        timeline_name=report["timeline_name"],
        render=report,
        delivered=False,
        stage=delivery_identity.STAGE_PRE_MASTER,
        source="resolve-axi render inspect")
    payload = {"project": project.GetName(),
               "timeline": report["timeline_name"],
               "output_path": report["output_path"],
               "size_bytes": report["size_bytes"],
               "stage": delivery_identity.STAGE_PRE_MASTER,
               "pre_master": True,
               "mastered": False,
               "delivered": False,
               "record_path": record["record_path"],
               "written": "yes (the file is on disk and carries audio)",
               "note": "this is a PRE-MASTER render, not a deliverable: "
                       "it has not been brought to the delivery loudness. "
                       "Deliver it with `ren deliver <project> --timeline "
                       "<name>`, which masters it and writes the delivery "
                       "record."}
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        return 0
    emit([kv_block("rendered", payload),
          help_block([f"{TOOL} renders"])])
    return 0


def cmd_project_set(args) -> int:
    """Set one project setting, verified by read-back.

    The old value is read first and shown in the dry run; the write
    tries the plural `SetSettings` and falls back to the singular
    `SetSetting` only when the plural is absent (same key, same
    value - the fallback cannot double-apply anything). A read-back
    that disagrees fails: a silent setting is worse than a refused
    one.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        if not args.key:
            return fail("project set needs --key.",
                        f"{TOOL} project")
        try:
            old = project.GetSetting(args.key)
        except Exception:
            old = ""
        before = _cursor_name(resolve)
        if not args.apply:
            emit([kv_block("setting_plan", {
                      "project": project.GetName(),
                      "key": args.key,
                      "old": old,
                      "new": args.value,
                      "cursor": before,
                  }),
                  f"dry run - pass --apply to write under the "
                  f"Resolve lease",
                  help_block([f"{TOOL} project set --key {args.key} "
                              f"--value \"{args.value}\" --apply"])])
            return 0
        wrote, how = False, ""
        try:
            wrote = bool(project.SetSettings({args.key: args.value}))
            how = "SetSettings"
        except AttributeError:
            try:
                wrote = bool(project.SetSetting(args.key, args.value))
                how = "SetSetting"
            except Exception as exc:
                return fail(f"setting {args.key!r} raised ({exc}) - "
                            f"verify by hand.",
                            f"{TOOL} project")
        except Exception as exc:
            return fail(f"setting {args.key!r} raised ({exc}) - verify "
                        f"by hand.",
                        f"{TOOL} project")
        try:
            back = project.GetSetting(args.key)
        except Exception:
            back = None
        after = _cursor_name(resolve)
        if not wrote or (back is not None
                         and str(back) != str(args.value)):
            return fail(
                f"setting {args.key!r} reports {wrote} and re-reads "
                f"{back!r} for {args.value!r} - refusing to claim it.",
                f"{TOOL} project")
        emit([kv_block("setting", {
                  "project": project.GetName(),
                  "key": args.key,
                  "old": old,
                  "new": args.value,
                  "via": how,
                  "cursor_before": before,
                  "cursor_after": after,
                  "verified": "yes (read back equal)",
              }),
              help_block([f"{TOOL} project"])])
        return 0


def cmd_project_checkpoint(args) -> int:
    """Create or restore a Resolve project checkpoint through the store."""
    if not args.apply:
        return fail("checkpoint operations write an artifact or import a project; pass --apply only after reviewing the target.",
                    f"{TOOL} project checkpoint --help")
    try:
        resolve = _connect()
        manager = resolve.GetProjectManager()
        from library.tools.versions import checkpoints
        if args.restore:
            result = checkpoints.restore_project(
                manager, args.project_folder, args.checkpoint_id, args.new_name)
        else:
            project = _project(resolve, args.project)
            result = checkpoints.project_checkpoint(
                manager, project, args.project_folder, args.checkpoint_id)
        emit([kv_block("project_checkpoint", result)])
        return 0
    except Exception as exc:  # noqa: BLE001 - report a bounded refusal
        return fail(f"project checkpoint failed: {exc}", f"{TOOL} project")


def _scratch_apply(args, scratch) -> dict:
    """One scratch lifecycle operation, under the exclusive lease."""
    with _lease(exclusive=True):
        resolve = _connect()
        manager = resolve.GetProjectManager()
        if manager is None:
            raise AxiError("Resolve returned no live Project Manager", fix="open a project in Resolve, then re-run")
        if args.scratch_command == "create":
            return scratch.create(
                resolve, manager, args.name,
                fps=getattr(args, "fps", None),
                width=getattr(args, "width", None),
                height=getattr(args, "height", None))
        if args.scratch_command == "import":
            return scratch.import_project(resolve, manager,
                                          args.source, args.name,
                                          fps=getattr(args, "fps", None),
                                          width=getattr(args, "width", None),
                                          height=getattr(args, "height", None))
        if args.scratch_command == "open":
            return scratch.open_project(resolve, manager, args.name)
        if args.scratch_command == "restore":
            return scratch.restore_recorded(
                resolve, manager,
                timeline_drts=getattr(args, "timeline_drt", ()) or ())
        if args.scratch_command == "collect":
            if args.apply:
                if not args.manifest:
                    raise AxiError("scratch collection apply needs --manifest", fix="pass --manifest <file>, then re-run")
                return scratch.apply_collection(resolve, manager, args.manifest)
            manifest = scratch.plan_collection(manager)
            return {"planned": True, "manifest": str(manifest),
                    "note": "review and select exact project rows before --apply"}
        if args.scratch_command == "delete":
            return scratch.delete(resolve, manager, args.name)
        raise AxiError("choose a scratch lifecycle operation", fix="name one of the scratch lifecycle operations, then re-run")


def cmd_project_scratch(args) -> int:
    """Operate on Ren-owned scratch projects under the exclusive lease."""
    if not getattr(args, "apply", False) and args.scratch_command != "collect":
        return fail(
            "scratch project lifecycle changes the Resolve session; pass --apply",
            f"{TOOL} project scratch --help")
    try:
        from library.tools import resolve_scratch_projects as scratch
        if args.scratch_command in ("create", "import", "open", "delete"):
            # The scratch origin is ONE machine resource, so a verb that
            # moves the machine QUEUES for it the way the instance lease
            # queues - it does not refuse on the first sight.
            #
            # The queue runs in the CALLER, before the broker's turn is
            # taken, and never here: the holder of the slot is inside
            # Resolve and needs that turn to restore the origin and
            # release the slot, so a wait inside a turn holds the very
            # turn the holder needs. This command is run INSIDE the
            # broker's turn when the broker is serving, so it refuses
            # FAST, with its own exit code, and the caller waits for
            # the slot and runs it again (measured 2026-10-21: three
            # `project scratch open` jobs held the machine's turn for
            # the whole 900 s lease timeout each, and every other lane
            # waited behind them).
            try:
                result = _scratch_apply(args, scratch)
            except scratch.ScratchOriginHeld as held:
                emit([f"error: {held}",
                      f"help: {TOOL} project scratch --help"])
                return scratch.SLOT_HELD_EXIT_CODE
        else:
            result = _scratch_apply(args, scratch)
        emit([kv_block("project_scratch", result),
              help_block([f"{TOOL} project scratch restore --apply",
                          f"{TOOL} project scratch delete <scratch-name> --apply"])])
        return 0
    except Exception as exc:  # noqa: BLE001 - lifecycle refusal is user-facing
        return fail(f"scratch project operation failed: {exc}",
                    f"{TOOL} project scratch --help")


def cmd_project_export_timeline(args) -> int:
    """Copy one named timeline out of ANY project, into a scratch project.

    The source project is read only: it is never saved and never
    written, and the project, timeline and page that were open when the
    command started are restored and read back afterwards.
    """
    if not getattr(args, "apply", False):
        return fail(
            "the export copies a reel out of a project and creates a "
            "scratch project; pass --apply once the source and the target "
            "are right",
            f"{TOOL} project export-timeline --help")
    try:
        from library.tools import reel_export
        with _lease(exclusive=True):
            resolve = _connect()
            manager = resolve.GetProjectManager()
            if manager is None:
                raise AxiError("Resolve returned no live Project Manager", fix="open a project in Resolve, then re-run")
            result = reel_export.export_into_scratch(
                resolve, manager,
                source_project=args.source_project,
                timeline_name=args.timeline,
                scratch_project=getattr(args, "scratch", "") or "",
                fps=getattr(args, "fps", None),
                width=getattr(args, "width", None),
                height=getattr(args, "height", None),
                source_folder=getattr(args, "source_folder", "") or "",
                archive_dir=getattr(args, "archive", "") or None)
        emit([kv_block("reel_export", result)])
        return 0
    except Exception as exc:  # noqa: BLE001 - lifecycle refusal is user-facing
        return fail(f"the reel export failed: {exc}",
                    f"{TOOL} project export-timeline --help")


def cmd_timeline_checkpoint(args) -> int:
    """Duplicate and export a timeline checkpoint under the Resolve lease."""
    if not args.apply:
        return fail("timeline checkpoint writes a duplicate and a .drt; pass --apply only after reviewing the target.",
                    f"{TOOL} timeline list")
    try:
        resolve = _connect()
        project = _project(resolve, args.project)
        timeline, _is_current, _note = _target_timeline(project, args.timeline)
        from library.tools.versions import checkpoints
        result = checkpoints.timeline_checkpoint(
            project, timeline, args.project_folder, args.checkpoint_id, resolve)
        emit([kv_block("timeline_checkpoint", result)])
        return 0
    except Exception as exc:  # noqa: BLE001 - report a bounded refusal
        return fail(f"timeline checkpoint failed: {exc}", f"{TOOL} timeline list")


def cmd_timeline_retire_owned(args) -> int:
    """Retire one recorded qualification timeline through guarded AXI."""
    if not args.apply:
        return fail("owned timeline retirement requires --apply; no mutation ran",
                    f"{TOOL} timeline delete --artifact-id <id> "
                    "--project-folder <path> --apply")
    try:
        with _lease(exclusive=True):
            resolve = _connect()
            manager = resolve.GetProjectManager()
            if manager is None:
                raise AxiError("Resolve returned no live Project Manager", fix="open a project in Resolve, then re-run")
            from library.tools import reel_retirement
            result = [reel_retirement.retire_owned_qualification_timeline(
                resolve, manager, args.project_folder, artifact_id)
                for artifact_id in args.artifact_id]
        emit([kv_block("timeline_retirement", result),
              help_block([f"{TOOL} timeline list"])])
        return 0
    except Exception as exc:  # noqa: BLE001 - lifecycle refusal is user-facing
        return fail(f"owned timeline retirement refused: {exc}",
                    f"{TOOL} timeline list")


def cmd_timeline_duplicate(args) -> int:
    """Version a reel: duplicate the timeline under a new exact name.

    The name is refused when taken - exact names only, here as
    everywhere. Verified by the name listing afterwards.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, _is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        names = _timeline_names(project)
        if not args.name:
            return fail("duplicate needs --name.",
                        f"{TOOL} timeline duplicate --timeline "
                        f"\"{timeline.GetName()}\" --name \"<new>\"")
        if args.name in names:
            return fail(
                f"a timeline named {args.name!r} already exists - "
                f"exact names only, refusing rather than doubling.",
                f"{TOOL} timeline list")
        before = _cursor_name(resolve)
        if not args.apply:
            emit([kv_block("duplicate_plan", {
                      "timeline": timeline.GetName(),
                      "name": args.name,
                      "cursor": before,
                  }),
                  note,
                  f"dry run - pass --apply to duplicate under the "
                  f"Resolve lease",
                  help_block([
                      f"{TOOL} timeline duplicate --timeline "
                      f"\"{timeline.GetName()}\" --name \"{args.name}\" "
                      f"--apply"])])
            return 0
        try:
            created = timeline.DuplicateTimeline(args.name)
        except Exception as exc:
            return fail(f"duplicating raised ({exc}) - verify by hand.",
                        f"{TOOL} timeline list")
        names = _timeline_names(project)
        after = _cursor_name(resolve)
        if args.name not in names:
            return fail(
                f"duplicate answered "
                f"{'with a timeline' if created else 'empty'} but "
                f"{args.name!r} is not listed - verify by hand.",
                f"{TOOL} timeline list")
        emit([kv_block("duplicated", {
                  "timeline": timeline.GetName(),
                  "name": args.name,
                  "cursor_before": before,
                  "cursor_after": after,
                  "verified": "yes (listed on re-read)",
              }),
              note,
              help_block([f"{TOOL} timeline list"])])
        return 0


# ── native Resolve 21.1 verbs: multicam, color, voice isolation ──
#
# Each op the fidelity probe ranked as reading back is a verb here
# in the same discipline as the edit writes: dry-run default,
# `--apply` writes, timeline-scoped writes assert the cursor and
# hold the lease exclusive, and the read-back decides. An op with
# no measured read-back (`multicam sync`) plans but refuses
# `--apply`, naming the missing measurement instead of claiming a
# write off the return value alone.


#: `--sync` names onto the resolve.* angle-sync constants. The enum
#: lives on the live Resolve object, so a build without it refuses
#: naming the constant rather than sending a guessed value.
_MULTICAM_SYNCS = {
    "audio": "MULTICAM_ANGLE_SYNC_AUDIO",
    "timecode": "MULTICAM_ANGLE_SYNC_TIMECODE",
    "in": "MULTICAM_ANGLE_SYNC_IN",
    "out": "MULTICAM_ANGLE_SYNC_OUT",
    "marker": "MULTICAM_ANGLE_SYNC_MARKER",
}

#: `--mode` names onto the resolve.* audio-sync constants, same
#: shape as the multicam sync modes above.
_AUDIO_SYNC_MODES = {
    "waveform": "AUDIO_SYNC_WAVEFORM",
    "timecode": "AUDIO_SYNC_TIMECODE",
}


def _pool_names(pool) -> list:
    """Every clip name in the pool, bins walked. Best effort per
    folder: a folder that will not read is skipped, never fatal."""
    names: list = []

    def visit(folder) -> None:
        try:
            clips = folder.GetClipList() or []
        except Exception:
            clips = []
        for clip in clips:
            try:
                names.append(clip.GetName())
            except Exception:
                continue
        try:
            subs = folder.GetSubFolderList() or []
        except Exception:
            subs = []
        for sub in subs:
            visit(sub)

    try:
        visit(pool.GetRootFolder())
    except Exception:
        pass
    return names


def cmd_multicam_build(args) -> int:
    """Build a multicam clip from pool clips, verified by listing.

    `CreateMulticamClip` answers the new pool items; the write is
    claimed only when the named clip lists on re-read. The sync
    mode is an explicit `--sync` (audio sync is the probe's ground
    truth); the enum is read off live Resolve, and a build without
    it refuses naming the constant. This is project-scoped, so the
    cursor is reported before and after rather than asserted.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        wanted = [c for c in (args.clip or []) if c]
        if len(wanted) < 2:
            return fail("multicam build needs at least two --clip "
                        "names (repeatable).",
                        f"{TOOL} multicam build --help")
        if not args.name:
            return fail("multicam build needs --name (the read-back "
                        "is the name listing on re-read).",
                        f"{TOOL} multicam build --help")
        sync = args.sync or "timecode"
        if sync not in _MULTICAM_SYNCS:
            return fail(
                f"bad --sync {sync!r}: "
                f"{'|'.join(sorted(_MULTICAM_SYNCS))}.",
                f"{TOOL} multicam build --help")
        try:
            pool = project.GetMediaPool()
        except Exception as exc:
            return fail(f"project {project.GetName()!r} would not open "
                        f"its media pool ({exc}).",
                        f"{TOOL} cursor")
        items = []
        for name in wanted:
            try:
                clip, _row = _pool_find_clip(project, name,
                                             args.bin or "")
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
            items.append(clip)
        const_name = _MULTICAM_SYNCS[sync]
        try:
            sync_const = getattr(resolve, const_name)
        except AttributeError:
            return fail(
                f"this Resolve build has no {const_name} - refusing "
                f"to send a guessed sync mode.",
                f"{TOOL} multicam build --help")
        options = {"name": args.name, "angleSyncMode": sync_const}
        before = _cursor_name(resolve)
        if not args.apply:
            emit([kv_block("multicam_plan", {
                      "project": project.GetName(),
                      "name": args.name,
                      "sync": sync,
                      "clips": len(items),
                      "cursor": before,
                  }),
                  table("sources", [{"clip": n} for n in wanted],
                        ["clip"]),
                  f"dry run - pass --apply to build under the "
                  f"Resolve lease",
                  help_block([
                      f"{TOOL} multicam build "
                      f"{' '.join('--clip ' + n for n in wanted)} "
                      f"--name \"{args.name}\" --sync {sync} "
                      f"--apply"])])
            return 0
        try:
            created = pool.CreateMulticamClip(items, options) or []
        except Exception as exc:
            return fail(f"CreateMulticamClip raised ({exc}) - verify "
                        f"by hand.",
                        f"{TOOL} pool")
        made = []
        for entry in created:
            try:
                made.append(entry.GetName())
            except Exception:
                continue
        listed = _pool_names(pool)
        after = _cursor_name(resolve)
        if args.name not in listed:
            return fail(
                f"CreateMulticamClip returned {len(made)} item(s) "
                f"({made}) but {args.name!r} is not in the pool on "
                f"re-read - verify by hand.",
                f"{TOOL} pool")
        emit([kv_block("multicam", {
                  "project": project.GetName(),
                  "name": args.name,
                  "sync": sync,
                  "cursor_before": before,
                  "cursor_after": after,
                  "verified": "yes (named clip listed on re-read; "
                  "sync quality is judged on pixels, not here)",
              }),
              table("created", [{"clip": n} for n in made], ["clip"]),
              help_block([f"{TOOL} pool"])])
        return 0


def cmd_multicam_sync(args) -> int:
    """Plan an audio-sync pass; `--apply` is refused on purpose.

    `AutoSyncAudio` returns a bare bool with no property the lane
    has measured to re-read, so claiming a sync off True would be
    the True-that-did-nothing shape this tool refuses everywhere
    else. The dry run plans the exact call (clips plus mode); the
    write waits on the hands probe ranking a read-back for it.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=False):
        project = _project(resolve, args.project)
        wanted = [c for c in (args.clip or []) if c]
        if len(wanted) < 2:
            return fail("multicam sync needs at least two --clip "
                        "names (repeatable: video plus audio).",
                        f"{TOOL} multicam sync --help")
        mode = args.mode or "waveform"
        if mode not in _AUDIO_SYNC_MODES:
            return fail(f"bad --mode {mode!r}: waveform|timecode.",
                        f"{TOOL} multicam sync --help")
        try:
            pool = project.GetMediaPool()
        except Exception as exc:
            return fail(f"project {project.GetName()!r} would not open "
                        f"its media pool ({exc}).",
                        f"{TOOL} cursor")
        items = []
        for name in wanted:
            try:
                clip, _row = _pool_find_clip(project, name,
                                             args.bin or "")
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
            items.append(clip)
        const_name = _AUDIO_SYNC_MODES[mode]
        try:
            getattr(resolve, const_name)
        except AttributeError:
            return fail(
                f"this Resolve build has no {const_name} - refusing "
                f"to plan against a guessed sync mode.",
                f"{TOOL} multicam sync --help")
    if args.apply:
        return fail(
            f"{len(items)} clip(s) planned for AutoSyncAudio "
            f"({mode}), none synced: the call returns a bare bool "
            f"with no measured property to re-read, so --apply "
            f"waits on the hands probe ranking a read-back.",
            f"{TOOL} pool")
    emit([kv_block("sync_plan", {
              "project": project.GetName(),
              "mode": mode,
              "clips": len(items),
          }),
          table("sources", [{"clip": n} for n in wanted], ["clip"]),
          "dry run - --apply is refused until the hands probe "
          "measures a read-back for AutoSyncAudio",
          help_block([f"{TOOL} pool"])])
    return 0


def _ledger_block(args, row: dict, why_missing: str = "") -> str:
    """Record a verified hands write as an edit-ledger row, as well as
    acting - so the next build replays it instead of painting over it
    (K3). Returns the `ledger` TOON block: `recorded` with the row's
    name, or `not-recorded` naming why. A ledger failure never fails
    the verb: the ACT is what the exit judges (it already verified),
    and the block judges the durability - but it says so loudly,
    because an unledgered hands edit WILL be painted over by the next
    build and nothing else will say that."""
    from library.tools import edit_ledger as _ledger

    project_folder = getattr(args, "project_folder", "") or ""
    if not project_folder:
        return kv_block("ledger", {
            "row": why_missing or "not-recorded",
            "why": ("no --project-folder: acted, not carried - the "
                    "next build paints this over. Re-run with "
                    "--project-folder <pipeline project> to ledger it."),
        })
    if why_missing:
        return kv_block("ledger", {
            "row": "not-recorded",
            "why": why_missing,
        })
    try:
        _ledger.record_row(project_folder, row)
    except Exception as exc:
        return kv_block("ledger", {
            "row": "not-recorded",
            "why": (f"{exc} - acted, not carried: the next build "
                    f"paints this over until it is ledgered."),
        })
    return kv_block("ledger", {
        "row": "recorded",
        "name": _ledger._row_name(row),
    })


def _reel_number(name: str) -> int:
    """The reel number in a timeline name like 'Reel 29 - salvage', or 0."""
    import re
    match = re.match(r"Reel\s+(\d+)", name)
    return int(match.group(1)) if match else 0


@contextlib.contextmanager
def _journal_write(args, *, timeline, project, spec, gate_class):
    """Journal a direct resolve-axi write through the undo journal.

    Opens an entry BEFORE the write (capturing the before-tracks) and
    closes it AFTER (capturing the after-tracks, recording a version and
    a history entry). The caller sets ``state["success"] = True`` once the
    write has verified; an unverified write leaves the entry FAILED, never
    applied. A journal failure never fails the verb: the ACT is what the
    exit judges (it already verified), and the block judges the
    durability - but it says so loudly, because an unjournaled hands edit
    is invisible to `ren undo` and the next build's carry.
    """
    from library.tools import undo_journal as _journal

    project_folder = getattr(args, "project_folder", "") or ""
    if not project_folder:
        if getattr(args, "require_recovery", False):
            raise AxiError(
                "this semantic write requires a durable recovery journal",
                "supply --project-folder <pipeline project>")
        yield None, {"success": False}
        return
    entry = _journal.open_entry(
        project_folder,
        final=timeline.GetName(),
        reel=_reel_number(timeline.GetName()),
        resolve_project=project.GetName(),
        spec=spec,
        gate_class=gate_class,
        source_timeline=timeline,
        removals=[],
        project=project,
    )
    state = {"success": False}
    try:
        yield entry, state
    except Exception as exc:
        _journal.fail_entry(project_folder, entry, repr(exc))
        raise
    if state["success"]:
        _journal.close_entry(
            project_folder, entry, after_timeline=timeline, rows={},
            project=project)
    else:
        _journal.fail_entry(project_folder, entry,
                             "the write did not verify")


def _journal_block(entry) -> str:
    """The `journal` TOON block for a completed journal entry."""
    if entry is None:
        return kv_block("journal", {
            "entry": "not-journaled",
            "why": ("no --project-folder: acted, not undone - `ren undo` "
                    "cannot reverse it. Re-run with --project-folder "
                    "<pipeline project> to journal it."),
        })
    return kv_block("journal", {
        "entry": entry["id"],
        "status": entry.get("status", ""),
    })


def cmd_color_lut(args) -> int:
    """Set a node LUT on one item, verified by re-read.

    The LUT path is Resolve's own shelf path (e.g. "Film Looks/..."),
    passed verbatim - this verb never guesses a local file onto it.
    `SetLUT` is judged by `GetLUT` on the same node: the getter
    answers the RELATIVE path, so equality or a trailing match
    verifies, and anything else fails naming both strings. A
    missing node graph, an out-of-range node, or a False answer
    each refuse naming what failed.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, _uid = _edit_item(
                timeline, args.track, args.index)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        if not args.lut:
            return fail("color lut needs --lut <Resolve LUT path>.",
                        f"{TOOL} color lut --help")
        node = args.node if args.node is not None else 1
        try:
            graph = item.GetNodeGraph()
        except AttributeError:
            return fail(
                f"{span['name']!r} exposes no node graph on this "
                f"build - nothing was written.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        except Exception as exc:
            return fail(f"the node graph would not open ({exc}) - "
                        f"nothing was written.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        if graph is None:
            return fail(f"{span['name']!r} has no node graph - "
                        f"nothing was written.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        try:
            count = graph.GetNumNodes()
        except Exception as exc:
            return fail(f"the node graph would not count its nodes "
                        f"({exc}) - nothing was written.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        if node < 1 or (isinstance(count, int) and node > count):
            return fail(f"node {node} is outside 1..{count} on "
                        f"{span['name']!r} - nothing was written.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        try:
            current = graph.GetLUT(node)
        except Exception:
            current = ""
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        else:
            emit([kv_block("lut_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "name": span["name"],
                      "node": node,
                      "current": current or "(none)",
                      "lut": args.lut,
                  }),
                  note,
                  f"dry run - pass --apply to write under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([
                      f"{TOOL} color lut --timeline "
                      f"\"{timeline.GetName()}\" --track "
                      f"{track_type}{track_index} --index {args.index} "
                      f"--lut \"{args.lut}\" --apply"])])
            return 0
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "color_lut", "timeline": timeline.GetName(),
                      "name": span["name"], "node": node, "lut": args.lut},
                gate_class="color_lut") as (journal, state):
            try:
                wrote = bool(graph.SetLUT(node, args.lut))
            except Exception as exc:
                return fail(f"SetLUT raised ({exc}) - verify by hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            if not wrote:
                return fail(f"SetLUT(node {node}, {args.lut!r}) answered "
                            f"False on {span['name']!r} - nothing was "
                            f"claimed.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            try:
                back = graph.GetLUT(node)
            except Exception as exc:
                return fail(f"the LUT re-read raised ({exc}) - verify by "
                            f"hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            if not back or (back != args.lut
                            and not args.lut.endswith("/" + str(back))):
                return fail(
                    f"SetLUT reports True and re-reads {back!r} for "
                    f"{args.lut!r} - refusing to claim it.",
                    f"{TOOL} items --timeline \"{timeline.GetName()}\"")
            state["success"] = True
        anchor = (getattr(args, "anchor", "") or "").strip()
        ledger_row = {
            "op": "clip_lut",
            "anchor": {"kind": "words", "phrase": anchor} if anchor
            else {"kind": "reel"},
            "params": {"lut": args.lut, "node": node},
            "stated_by": getattr(args, "stated_by", "captain") or
            "captain",
            "reason": (getattr(args, "reason", "") or
                       f"{TOOL} color lut on {timeline.GetName()}").strip(),
        }
        reel_scope = (getattr(args, "reel", "") or "").strip()
        ledger_row["reel"] = reel_scope or timeline.GetName()
        if not anchor:
            ledger_note = ("no --anchor: a LUT holds on the clips "
                           "speaking the anchor's words, which is what "
                           "survives a rebuild - acted, not carried. "
                           "Re-run with --anchor \"...\" and "
                           "--project-folder to ledger it.")
        else:
            ledger_note = ""
        emit([kv_block("lut", {
                  "timeline": timeline.GetName(),
                  "name": span["name"],
                  "node": node,
                  "lut": args.lut,
                  "verified": "yes (GetLUT re-reads the LUT)",
              }),
              note,
              _ledger_block(args, ledger_row, ledger_note),
              _journal_block(journal),
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def cmd_color_group(args) -> int:
    """Assign one item to a color group, verified by re-read.

    Without `--create` the group must already exist (listed by
    `GetColorGroupsList` and matched by exact name); with it the
    verb creates the group first. Either way the write is claimed
    only when the item's `GetColorGroup` re-reads the same name -
    a True that grouped nothing fails naming both.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, _uid = _edit_item(
                timeline, args.track, args.index)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        if not args.group:
            return fail("color group needs --group <name>.",
                        f"{TOOL} color group --help")
        try:
            current_group = item.GetColorGroup()
            current = (current_group.GetName()
                       if current_group is not None else "(none)")
        except Exception:
            current = ""
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        else:
            emit([kv_block("group_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "name": span["name"],
                      "current": current or "(none)",
                      "group": args.group,
                      "create": ("yes" if args.create else "no"),
                  }),
                  note,
                  f"dry run - pass --apply to write under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([
                      f"{TOOL} color group --timeline "
                      f"\"{timeline.GetName()}\" --track "
                      f"{track_type}{track_index} --index {args.index} "
                      f"--group \"{args.group}\""
                      f"{' --create' if args.create else ''} "
                      f"--apply"])])
            return 0
        group = None
        if args.create:
            try:
                group = project.AddColorGroup(args.group)
            except Exception as exc:
                return fail(f"AddColorGroup raised ({exc}) - nothing "
                            f"was written.",
                            f"{TOOL} cursor")
            if group is None or group is False:
                return fail(
                    f"AddColorGroup({args.group!r}) answered empty - "
                    f"nothing was written.",
                    f"{TOOL} cursor")
        else:
            try:
                groups = project.GetColorGroupsList() or []
            except AttributeError:
                return fail(
                    "this build lists no color groups - pass "
                    "--create to make one.",
                    f"{TOOL} color group --help")
            except Exception as exc:
                return fail(f"the group list would not read ({exc}) "
                            f"- nothing was written.",
                            f"{TOOL} cursor")
            for candidate in groups:
                try:
                    if candidate.GetName() == args.group:
                        group = candidate
                        break
                except Exception:
                    continue
            if group is None:
                return fail(
                    f"no color group {args.group!r} - pass --create "
                    f"to make it rather than grouping nowhere.",
                    f"{TOOL} color group --help")
        try:
            wrote = bool(item.AssignToColorGroup(group))
        except Exception as exc:
            return fail(f"AssignToColorGroup raised ({exc}) - verify "
                        f"by hand.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        if not wrote:
            return fail(
                f"AssignToColorGroup({args.group!r}) answered False "
                f"on {span['name']!r} - nothing was claimed.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        try:
            back_group = item.GetColorGroup()
            back = (back_group.GetName()
                    if back_group is not None else None)
        except Exception as exc:
            return fail(f"the group re-read raised ({exc}) - verify "
                        f"by hand.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        if back != args.group:
            return fail(
                f"AssignToColorGroup reports True and re-reads "
                f"{back!r} for {args.group!r} - refusing to claim it.",
                f"{TOOL} items --timeline \"{timeline.GetName()}\"")
        emit([kv_block("grouped", {
                  "timeline": timeline.GetName(),
                  "name": span["name"],
                  "group": args.group,
                  "verified": "yes (GetColorGroup re-reads the name)",
              }),
              note,
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def cmd_audio_isolate(args) -> int:
    """Set one audio track's voice isolation, verified by re-read.

    The state is `{isEnabled, amount}` with amount in 0..100;
    `--disable` writes isEnabled False. The write is claimed only
    when `GetVoiceIsolationState` re-reads the same enabled flag
    (and the same amount when enabled) - a True that isolated
    nothing fails naming both. The per-clip variant
    (`TimelineItem.SetVoiceIsolationState`) stays out: the probe
    ranks the track call first.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        track = args.track
        if track is None or track < 1:
            return fail("isolate needs --track <audio track number, "
                        "from 1>.",
                        f"{TOOL} audio --timeline "
                        f"\"{timeline.GetName()}\" --full")
        try:
            count = timeline.GetTrackCount("audio") or 0
        except Exception as exc:
            return fail(f"timeline {timeline.GetName()!r} would not "
                        f"report its audio tracks ({exc}).",
                        f"{TOOL} timeline list")
        if track > count:
            return fail(f"timeline {timeline.GetName()!r} holds "
                        f"{count} audio track(s) - track {track} "
                        f"names nothing.",
                        f"{TOOL} audio --timeline "
                        f"\"{timeline.GetName()}\" --full")
        amount = args.amount if args.amount is not None else 60
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return fail(f"bad --amount {args.amount!r}: 0..100.",
                        f"{TOOL} audio isolate --help")
        if amount < 0 or amount > 100:
            return fail(f"bad --amount {amount}: 0..100.",
                        f"{TOOL} audio isolate --help")
        if args.disable:
            state = {"isEnabled": False}
        else:
            state = {"isEnabled": True, "amount": amount}
        try:
            current = timeline.GetVoiceIsolationState(track)
        except Exception:
            current = None
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        else:
            emit([kv_block("isolate_plan", {
                      "timeline": timeline.GetName() +
                      (" (current)" if is_current else ""),
                      "track": f"audio{track}",
                      "current": _voice_state(current),
                      "state": ("off" if args.disable
                                else f"on {amount}"),
                  }),
                  note,
                  f"dry run - pass --apply to write under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([
                      f"{TOOL} audio isolate --timeline "
                      f"\"{timeline.GetName()}\" --track {track} "
                      f"--amount {amount} --apply"])])
            return 0
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "audio_isolate", "timeline": timeline.GetName(),
                      "track": track, "state": state},
                gate_class="audio_isolate") as (journal, state_journal):
            try:
                wrote = bool(timeline.SetVoiceIsolationState(
                    track, dict(state)))
            except Exception as exc:
                return fail(f"SetVoiceIsolationState raised ({exc}) - "
                            f"verify by hand.",
                            f"{TOOL} audio --timeline "
                            f"\"{timeline.GetName()}\" --full")
            if not wrote:
                return fail(
                    f"SetVoiceIsolationState(audio{track}) answered "
                    f"False - nothing was claimed.",
                    f"{TOOL} audio --timeline "
                    f"\"{timeline.GetName()}\" --full")
            try:
                back = timeline.GetVoiceIsolationState(track) or {}
            except Exception as exc:
                return fail(f"the isolation re-read raised ({exc}) - "
                            f"verify by hand.",
                            f"{TOOL} audio --timeline "
                            f"\"{timeline.GetName()}\" --full")
            want_enabled = bool(state.get("isEnabled"))
            if (bool(back.get("isEnabled")) != want_enabled
                    or (want_enabled
                        and back.get("amount") != state["amount"])):
                return fail(
                    f"SetVoiceIsolationState reports True and re-reads "
                    f"{back!r} for {state!r} - refusing to claim it.",
                    f"{TOOL} audio --timeline "
                    f"\"{timeline.GetName()}\" --full")
            state_journal["success"] = True
        ledger_row = {
            "op": "voice_isolation",
            "anchor": {"kind": "reel"},
            "params": {"track": track,
                       "amount": (0 if args.disable else amount)},
            "stated_by": getattr(args, "stated_by", "captain") or
            "captain",
            "reason": (getattr(args, "reason", "") or
                       f"{TOOL} audio isolate on "
                       f"{timeline.GetName()}").strip(),
        }
        reel_scope = (getattr(args, "reel", "") or "").strip()
        ledger_row["reel"] = reel_scope or timeline.GetName()
        if args.disable:
            ledger_note = ("isolation turned OFF: off is the absence "
                           "of a hold, not a hold - acted, not "
                           "carried. Record a voice_isolation row by "
                           "hand for an amount the build must keep.")
        else:
            ledger_note = ""
        emit([kv_block("isolated", {
                  "timeline": timeline.GetName(),
                  "track": f"audio{track}",
                  "state": ("off" if args.disable
                            else f"on {state['amount']}"),
                  "verified": "yes (re-reads equal)",
              }),
              note,
              _ledger_block(args, ledger_row, ledger_note),
              _journal_block(journal),
              help_block([f"{TOOL} audio --timeline "
                          f"\"{timeline.GetName()}\" --full"])])
        return 0


# ── senses: Resolve's own AI beside Ren's, judged by read-back ────


#: Seconds `sense transcribe --apply` waits for words to land before
#: refusing. Transcription is a model job: the call answers True at
#: once and the segments fill in later (or never, on clips Resolve
#: hears nothing in - measured 240 s empty on a 23 s phone clip).
_SENSE_TRANSCRIBE_WAIT_S = 240

#: What SmartReframe is judged by. The call answers True while moving
#: nothing: measured True with Pan/Tilt/ZoomX reading back 0/0/1.0 on
#: a clip with a held face, twice (rung 3a and rung 3c).
_REFRAME_KEYS = ("Pan", "Tilt", "ZoomX", "ZoomY")


def _sense_clip(project, clip_name: str, bin_path: str):
    """The one pool item a sense reads, or a refusal naming the fix."""
    if not clip_name:
        raise AxiError("a sense needs --clip <pool clip name>.",
                       f"{TOOL} pool")
    return _pool_find_clip(project, clip_name, bin_path or "")[0]


def _transcription_words(clip):
    """Resolve's transcription as (language, segments), or a refusal.

    Returns only what Resolve actually reports: segments carrying
    words with timing and speaker labels. A missing, empty or
    still-wordless answer is refused - never claimed as transcribed.
    """
    try:
        transcript = clip.GetTranscription() or {}
    except Exception as exc:
        raise AxiError(f"GetTranscription raised ({exc}) - verify "
                       f"by hand.",
                       f"{TOOL} pool") from exc
    if not isinstance(transcript, dict):
        raise AxiError("GetTranscription answered a non-mapping - "
                       "refusing rather than shaping it.",
                       f"{TOOL} pool")
    segments = transcript.get("segments") or []
    voiced = [s for s in segments
              if isinstance(s, dict) and (s.get("text") or "").strip()]
    if not voiced:
        raise AxiError(
            f"the transcription holds {len(segments)} segment(s) with "
            f"no words - TranscribeAudio may still be processing, or "
            f"Resolve heard nothing here. Re-read later, or pass "
            f"--apply to (re)start it.",
            f"{TOOL} sense transcribe --clip \"{clip.GetName()}\" "
            f"--apply")
    return transcript.get("language", ""), voiced


def _emit_transcription(clip_name: str, language, segments,
                        full: bool) -> None:
    seg_rows = []
    word_rows = []
    for number, seg in enumerate(segments):
        words = seg.get("words") or []
        seg_rows.append({
            "index": number,
            "start": seg.get("start", ""),
            "end": seg.get("end", ""),
            "speaker": ("" if seg.get("speaker") is None
                        else str(seg.get("speaker"))),
            "words": len(words),
            "text": preview(seg.get("text") or "", full),
        })
        for pos, word in enumerate(words):
            if not isinstance(word, dict):
                continue
            word_rows.append({
                "seg": number,
                "index": pos,
                "start": word.get("start", ""),
                "end": word.get("end", ""),
                "text": preview(word.get("text") or "", full),
            })
    speakers = sorted({r["speaker"] for r in seg_rows} - {""})
    emit([kv_block("transcription", {
                "clip": clip_name,
                "language": language or "",
                "segments": len(seg_rows),
                "words": len(word_rows),
                "speakers": ",".join(speakers) or "(none labelled)",
            }),
          table("segments", seg_rows,
                ["index", "start", "end", "speaker", "words", "text"]),
          table("words", word_rows,
                ["seg", "index", "start", "end", "text"])])
    return None


def cmd_sense_transcribe(args) -> int:
    """Read (or start) Resolve's own transcription with speakers.

    `TranscribeAudio(True)` answers True at once and the words land
    later, so `--apply` polls `GetTranscription` up to `--wait`
    seconds for segments carrying text. The verb returns only those
    segments - words with timing and speaker labels, verbatim - and
    refuses on an empty or wordless answer instead of claiming a
    transcription. Pool-scoped: the cursor is reported, never moved.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            clip = _sense_clip(project, args.clip, args.bin or "")
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            name = clip.GetName()
        except Exception:
            name = args.clip
        before = _cursor_name(resolve)
        if not args.apply:
            try:
                language, voiced = _transcription_words(clip)
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
            _emit_transcription(name, language, voiced, args.full)
            emit([f"cursor: {before} (reported, never moved)",
                  help_block([f"{TOOL} sense transcribe --clip "
                              f"\"{name}\" --full"])])
            return 0
        try:
            started = bool(clip.TranscribeAudio(True))
        except Exception as exc:
            return fail(f"TranscribeAudio raised ({exc}) - verify "
                        f"by hand.",
                        f"{TOOL} pool")
        if not started:
            return fail(f"TranscribeAudio answered False for "
                        f"{name!r} - nothing was started.",
                        f"{TOOL} pool")
        try:
            wait = float(args.wait if args.wait is not None else
                         _SENSE_TRANSCRIBE_WAIT_S)
        except (TypeError, ValueError):
            return fail(f"bad --wait {args.wait!r}: seconds.",
                        f"{TOOL} sense transcribe --help")
        import time as _time
        deadline = _time.monotonic() + max(0.0, wait)
        language, voiced = "", []
        while True:
            try:
                language, voiced = _transcription_words(clip)
                break
            except AxiError:
                voiced = []
                if _time.monotonic() >= deadline:
                    try:
                        held = clip.GetTranscription() or {}
                    except Exception:
                        held = {}
                    count = len((held or {}).get("segments") or [])
                    return fail(
                        f"TranscribeAudio started but the transcription "
                        f"holds no words after {wait:g}s "
                        f"({count} placeholder segment(s)) - refusing "
                        f"rather than claiming it. Re-read later.",
                        f"{TOOL} sense transcribe --clip \"{name}\"")
                _time.sleep(5)
        _emit_transcription(name, language, voiced, args.full)
        after = _cursor_name(resolve)
        emit([f"cursor: {before} -> {after} (reported, never moved)",
              help_block([f"{TOOL} sense transcribe --clip "
                          f"\"{name}\" --full"])])
        return 0


def _cut_frames(timeline) -> list:
    """Every V1 item's record start, sorted - the cuts Resolve drew."""
    try:
        items = (timeline.GetItemListInTrack("video", 1) or [])
    except Exception as exc:
        raise AxiError(
            f"timeline {timeline.GetName()!r} would not read V1 "
            f"({exc}).",
            f"{TOOL} items --timeline "
            f"\"{timeline.GetName()}\"") from exc
    starts = []
    for item in items:
        try:
            starts.append(int(item.GetStart()))
        except Exception:
            continue
    return sorted(starts)


def cmd_sense_cuts(args) -> int:
    """Detect scene cuts along a timeline, reporting the cut frames.

    `DetectSceneCuts` splits the timeline's items where Resolve sees
    a cut; the verb reports the record frames of every resulting V1
    item. A True that splits nothing is reported as zero cuts (a
    single continuous take - measured), never as cuts. False is a
    refusal. Timeline-scoped: `--apply` asserts the cursor.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        if not args.apply:
            starts = _cut_frames(timeline)
            emit([kv_block("cuts_plan", {
                        "timeline": timeline.GetName() +
                        (" (current)" if is_current else ""),
                        "v1_items": len(starts),
                    }),
                  table("carriers",
                        [{"index": n, "record_in": s}
                         for n, s in enumerate(starts)],
                        ["index", "record_in"]),
                  note,
                  f"dry run - pass --apply to detect under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([f"{TOOL} sense cuts --timeline "
                              f"\"{timeline.GetName()}\" --apply"])])
            return 0
        try:
            _assert_cursor_on(resolve, timeline.GetName())
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        before = _cut_frames(timeline)
        try:
            detected = bool(timeline.DetectSceneCuts())
        except Exception as exc:
            return fail(f"DetectSceneCuts raised ({exc}) - verify "
                        f"by hand.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        if not detected:
            return fail(f"DetectSceneCuts answered False on "
                        f"{timeline.GetName()!r} - nothing was "
                        f"claimed.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        after = _cut_frames(timeline)
        fresh = [s for s in after if s not in set(before)]
        emit([kv_block("cuts", {
                    "timeline": timeline.GetName(),
                    "items_before": len(before),
                    "items_after": len(after),
                    "cuts": len(fresh),
                }),
              table("cut_frames", [{"frame": s} for s in fresh],
                    ["frame"]) if fresh else
              "cut_frames: 0 new cuts - one continuous take",
              note,
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


def _classification_of(clip) -> dict:
    """The audio classes Resolve reports for a pool clip, or {}.

    `PerformAudioClassification` files its answer under the
    `Category` / `Subcategory` metadata keys (measured): whatever
    holds them - metadata or clip properties - is the read-back.
    """
    found: dict = {}
    try:
        meta = clip.GetMetadata()
    except Exception:
        meta = None
    if isinstance(meta, dict):
        for key in ("Category", "Subcategory"):
            if meta.get(key):
                found[key] = meta[key]
    if not found:
        try:
            props = clip.GetClipProperty() or {}
        except Exception:
            props = {}
        if isinstance(props, dict):
            for key in ("Category", "Subcategory"):
                if props.get(key):
                    found[key] = props[key]
    return found


def cmd_sense_classify(args) -> int:
    """Read (or run) Resolve's audio classification for a pool clip.

    The classes read back as `Category` / `Subcategory` metadata -
    the verb reports them verbatim and refuses when they are absent,
    including a True that files nothing. Pool-scoped: the cursor is
    reported, never moved.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            clip = _sense_clip(project, args.clip, args.bin or "")
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            name = clip.GetName()
        except Exception:
            name = args.clip
        before = _cursor_name(resolve)
        if not args.apply:
            found = _classification_of(clip)
            if not found:
                return fail(
                    f"{name!r} carries no Category/Subcategory - "
                    f"pass --apply to run PerformAudioClassification.",
                    f"{TOOL} sense classify --clip \"{name}\" --apply")
            emit([kv_block("classification", {
                        "clip": name, **found,
                    }),
                  f"cursor: {before} (reported, never moved)"])
            return 0
        try:
            ran = bool(clip.PerformAudioClassification())
        except Exception as exc:
            return fail(f"PerformAudioClassification raised ({exc}) - "
                        f"verify by hand.",
                        f"{TOOL} pool")
        if not ran:
            return fail(f"PerformAudioClassification answered False "
                        f"for {name!r} - nothing was claimed.",
                        f"{TOOL} pool")
        found = _classification_of(clip)
        if not found:
            return fail(
                f"PerformAudioClassification reports True but no "
                f"Category/Subcategory reads back on {name!r} - "
                f"refusing to claim it.",
                f"{TOOL} pool")
        after = _cursor_name(resolve)
        emit([kv_block("classification", {
                    "clip": name, **found,
                    "verified": "yes (read back after True)",
                }),
              f"cursor: {before} -> {after} (reported, never moved)"])
        return 0


def cmd_sense_intellisearch(args) -> int:
    """Run Resolve's IntelliSearch analysis on a pool clip.

    The scripting call exists (`AnalyzeForIntellisearch`) but on this
    machine Resolve itself refuses it - the `AI Intellisearch -
    Faster` package is not installed (measured). The verb attempts
    the call and reports Resolve's own refusal verbatim; a True with
    no readable metadata change is reported as unverified, never as
    analysed. Nothing here is faked.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            clip = _sense_clip(project, args.clip, args.bin or "")
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            name = clip.GetName()
        except Exception:
            name = args.clip
        mode = "Better" if args.better else "Faster"
        if not args.apply:
            emit([kv_block("intellisearch_plan", {
                        "clip": name,
                        "mode": mode,
                        "faces": "yes" if args.faces else "no",
                    }),
                  f"dry run - pass --apply to analyse under the "
                  f"Resolve lease",
                  help_block([f"{TOOL} sense intellisearch --clip "
                              f"\"{name}\" --apply"])])
            return 0
        try:
            before_meta = clip.GetMetadata()
        except Exception:
            before_meta = None
        try:
            analysed = bool(clip.AnalyzeForIntellisearch(
                bool(args.faces), bool(args.better)))
        except Exception as exc:
            return fail(f"AnalyzeForIntellisearch refused "
                        f"({exc}).",
                        f"{TOOL} sense intellisearch --clip "
                        f"\"{name}\"")
        if not analysed:
            return fail(f"AnalyzeForIntellisearch answered False for "
                        f"{name!r} - nothing was claimed.",
                        f"{TOOL} pool")
        try:
            after_meta = clip.GetMetadata()
        except Exception:
            after_meta = None
        if (isinstance(before_meta, dict)
                and isinstance(after_meta, dict)):
            fresh = {k: v for k, v in after_meta.items()
                     if before_meta.get(k) != v}
        else:
            fresh = {}
        if not fresh:
            return fail(
                f"AnalyzeForIntellisearch reports True but no "
                f"metadata change reads back on {name!r} - "
                f"unverified, refusing to claim it.",
                f"{TOOL} pool")
        emit([kv_block("intellisearch", {
                    "clip": name,
                    "mode": mode,
                    "verified": "yes (metadata changed)",
                }),
              table("new_metadata",
                    [{"key": k, "value": v}
                     for k, v in sorted(fresh.items())],
                    ["key", "value"])])
        return 0


def _reframe_state(item) -> dict:
    try:
        props = item.GetProperty() or {}
    except Exception:
        props = {}
    return {k: (props or {}).get(k) for k in _REFRAME_KEYS}


def cmd_sense_reframe(args) -> int:
    """Run Smart Reframe on one timeline item, judged by its transform.

    The call answers True while moving nothing (measured twice: True
    with Pan/Tilt/ZoomX reading back unchanged, on a clip holding a
    face). The verb claims the reframe only when the re-read differs;
    an unchanged re-read is a refusal with the measured reason, not a
    success. Timeline-scoped: `--apply` asserts the cursor.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, uid = _edit_item(
                timeline, args.track, args.index)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        if not args.apply:
            emit([kv_block("reframe_plan", {
                        "timeline": timeline.GetName() +
                        (" (current)" if is_current else ""),
                        "item": (f"{track_type}{track_index}"
                                 f"[{args.index}] {span['name']}"),
                        **_reframe_state(item),
                    }),
                  note,
                  f"dry run - pass --apply to reframe under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([f"{TOOL} sense reframe --timeline "
                              f"\"{timeline.GetName()}\" --track "
                              f"{track_type}{track_index} --index "
                              f"{args.index} --apply"])])
            return 0
        try:
            _assert_cursor_on(resolve, timeline.GetName())
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        before = _reframe_state(item)
        if not uid:
            return fail("the item has no unique id, so EditPatch cannot "
                        "address Smart Reframe.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "sense_reframe", "timeline": timeline.GetName(),
                      "name": span["name"], "unique_id": uid,
                      "keys": list(_REFRAME_KEYS)},
                gate_class="sense_reframe") as (journal, journal_state):
            from library.tools import edit_patch
            from library.tools.execution_router import route_axi_dispatch
            try:
                axi_operations = [{
                    "op": "clip.smart_reframe", "unique_id": uid,
                    "keys": list(_REFRAME_KEYS), "before": before,
                }]
                # VC-09: AXI dispatches share the routing contract.
                axi_routing = route_axi_dispatch(
                    axi_operations,
                    context=_qualification_context(
                        resolve, project, timeline, axi_operations))
                receipt = edit_patch.apply_live_patch(
                    project=project, timeline=timeline,
                    capability="reel.touchup",
                    operations=axi_operations,
                    conflict_domains=("picture_transform",),
                    idempotency_key=(
                        f"resolve-axi.sense-reframe:{uuid.uuid4().hex}"),
                    preconditions=[{
                        "kind": "clip_transform_equals",
                        "unique_id": uid, "properties": before,
                    }])
            except Exception as exc:
                return fail(f"Smart Reframe EditPatch failed ({exc}).",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\" --transforms")
            receipt["execution_routing"] = axi_routing
            if receipt["status"] != "committed":
                return fail(
                    f"Smart Reframe did not verify from its EditPatch: "
                    f"{receipt} - no verifiable effect; judged on pixels, "
                    "not here.",
                    f"{TOOL} items --timeline "
                    f"\"{timeline.GetName()}\" --transforms")
            journal_state["success"] = True
        after = _reframe_state(item)
        emit([kv_block("reframed", {
                    "timeline": timeline.GetName(),
                    "item": span["name"],
                    "verified": "yes (transform moved)",
                }),
              table("transform",
                    [{"key": k, "before": before[k], "after": after[k]}
                     for k in _REFRAME_KEYS],
                    ["key", "before", "after"]),
              note,
              _journal_block(journal)])
        return 0


def cmd_edit_transform(args) -> int:
    """Translate an item by output pixels in one verified EditPatch."""
    if args.move_x == 0 and args.move_y == 0:
        return fail("transform needs a non-zero --move-x or --move-y.",
                    f"{TOOL} edit transform --help")
    if args.apply and not args.verify:
        return fail("--apply requires --verify for a transform edit.",
                    f"{TOOL} edit transform --item <id> --move-x <px> "
                    "--verify --apply")
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        matches = []
        for track_type in ("video",):
            try:
                track_count = int(timeline.GetTrackCount(track_type) or 0)
                for track_index in range(1, track_count + 1):
                    for item in timeline.GetItemListInTrack(
                            track_type, track_index) or ():
                        if str(item.GetUniqueId() or "") == args.item:
                            matches.append(item)
            except Exception as exc:
                return fail(f"cannot resolve item identity on the timeline "
                            f"({exc}).", f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\" --transforms")
        if len(matches) != 1:
            return fail(f"item id {args.item!r} resolves to {len(matches)} "
                        "timeline items; exactly one is required.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\" --transforms")
        item = matches[0]
        pool_item = item.GetMediaPoolItem()
        if pool_item is None:
            return fail("the item has no media-pool source dimensions.",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\" --transforms")
        try:
            raw_resolution = str(
                pool_item.GetClipProperty("Resolution") or "")
            dimensions = _re.findall(
                r"(\d{2,5})\s*x\s*(\d{2,5})", raw_resolution.lower())
            if not dimensions:
                raise ValueError(
                    f"media source has no measured Resolution: "
                    f"{raw_resolution!r}")
            clip_w, clip_h = (int(part) for part in dimensions[-1])
            frame_w = int(timeline.GetSetting("timelineResolutionWidth"))
            frame_h = int(timeline.GetSetting("timelineResolutionHeight"))
            before = item.GetProperty() or {}
            pan, tilt = float(before.get("Pan", 0.0)), float(
                before.get("Tilt", 0.0))
            scaling = before.get("Scaling", 0)
            from library.tools import resolve_transform
            if scaling in (1, "1", True):
                base_scale = resolve_transform.NATIVE_BASE_SCALE
            else:
                behavior = project.GetSetting(
                    "timelineInputResMismatchBehavior") or ""
                if behavior != "scaleToFit":
                    raise ValueError(
                        f"unqualified project mismatch behavior {behavior!r}")
                base_scale = resolve_transform.fit_base_scale(
                    clip_w, clip_h, frame_w, frame_h)
            rejection = resolve_transform.native_conversion_refusal(
                before, "scaleToFit")
            if rejection:
                raise ValueError(rejection)
            draw_gain = resolve_transform.FALLBACK_DRAW_GAIN
            new_pan = pan + (args.move_x * frame_w /
                             (clip_w * base_scale * draw_gain))
            new_tilt = tilt - (args.move_y * frame_h /
                               (clip_h * base_scale * draw_gain))
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            return fail(f"cannot compile a qualified output-pixel move "
                        f"({exc}).", f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\" --transforms")
        span = _item_span(item)
        if args.apply:
            try:
                _assert_cursor_on(resolve, timeline.GetName())
            except AxiError as exc:
                return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
            from library.tools import edit_patch
            from library.tools.execution_router import route_axi_dispatch
            try:
                axi_operations = [
                    {"op": "clip.set_property", "unique_id": args.item,
                     "key": "Pan", "value": new_pan},
                    {"op": "clip.set_property", "unique_id": args.item,
                     "key": "Tilt", "value": new_tilt},
                ]
                # VC-09: AXI dispatches share the routing contract.
                axi_routing = route_axi_dispatch(
                    axi_operations,
                    context=_qualification_context(
                        resolve, project, timeline, axi_operations))
                receipt = edit_patch.apply_live_patch(
                    project=project, timeline=timeline,
                    capability="reel.touchup",
                    operations=axi_operations,
                    conflict_domains=("picture_transform",),
                    idempotency_key=f"resolve-axi.transform:{uuid.uuid4().hex}",
                    project_folder=args.project_folder or None,
                    preconditions=[{
                        "kind": "clip_transform_equals", "unique_id": args.item,
                        "properties": {key: before[key]
                                       for key in ("Pan", "Tilt")
                                       if key in before},
                    }])
            except Exception as exc:
                return fail(f"transform EditPatch failed ({exc}).",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\" --transforms")
            receipt["execution_routing"] = axi_routing
            if receipt["status"] != "committed":
                return fail(f"transform did not verify from read-back: "
                            f"{receipt}.", f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\" --transforms")
            emit([kv_block("transform", {
                        "timeline": timeline.GetName(), "item": span["name"],
                        "item_id": args.item, "move_x_px": args.move_x,
                        "move_y_px": args.move_y, "pan": new_pan,
                        "tilt": new_tilt, "receipt": receipt["patch_id"],
                        "verified": "yes (native properties read back)"}),
                  note])
            return 0
        emit([kv_block("transform_plan", {
                    "timeline": timeline.GetName() +
                    (" (current)" if is_current else ""),
                    "item": span["name"], "item_id": args.item,
                    "move_x_px": args.move_x, "move_y_px": args.move_y,
                    "pan_before": pan, "pan_after": new_pan,
                    "tilt_before": tilt, "tilt_after": new_tilt,
                    "verification": "native Pan/Tilt read-back",
                }), note,
              "dry run - pass --verify --apply to commit this EditPatch",
              help_block([f"{TOOL} edit transform --item {args.item} "
                          f"--move-x {args.move_x:g} --move-y "
                          f"{args.move_y:g} --verify --apply"])])
        return 0


def cmd_sense_mask(args) -> int:
    """Start a Magic Mask on one timeline item, judged by its nodes.

    Measured: `CreateMagicMask` answers False with the node count
    unchanged. The verb claims the mask only when the call answers
    True AND the node graph gains a node; anything else is a refusal
    with the measured reason. Timeline-scoped.
    """
    if (args.mode or "") not in ("F", "B", "BI"):
        return fail(f"bad --mode {args.mode!r}: F|B|BI.",
                    f"{TOOL} sense mask --help")
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, _uid = _edit_item(
                timeline, args.track, args.index)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        from library.tools import magic_mask
        try:
            source_frame_range = magic_mask.source_frame_range_for_item(
                item, getattr(args, "source_frame_range", None))
        except ValueError as exc:
            return fail(f"Magic Mask range refused ({exc}).",
                        f"{TOOL} edit trim --timeline "
                        f"\"{timeline.GetName()}\" --track "
                        f"{track_type}{track_index} --index {args.index}")
        if not args.apply:
            emit([kv_block("mask_plan", {
                            "timeline": timeline.GetName() +
                            (" (current)" if is_current else ""),
                            "item": (f"{track_type}{track_index}"
                                     f"[{args.index}] {span['name']}"),
                            "mode": args.mode,
                            "source_frame_range": (
                                source_frame_range.label()),
                        }),
                      note,
                      f"dry run - pass --apply to mask under the "
                      f"Resolve lease (cursor must sit on the reel)",
                      help_block([f"{TOOL} sense mask --timeline "
                                  f"\"{timeline.GetName()}\" --track "
                                  f"{track_type}{track_index} --index "
                                  f"{args.index} --mode {args.mode} "
                                  f"--source-frame-range "
                                  f"{source_frame_range.start}:"
                                  f"{source_frame_range.end} "
                                  f"--apply"])])
            return 0
        try:
            _assert_cursor_on(resolve, timeline.GetName())
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            graph = item.GetNodeGraph()
            nodes_before = (graph.GetNumNodes() if graph is not None
                            else None)
        except Exception:
            nodes_before = None
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "sense_mask", "timeline": timeline.GetName(),
                      "name": span["name"], "mode": args.mode,
                      "source_frame_range": source_frame_range.to_receipt()},
                gate_class="sense_mask") as (journal, journal_state):
            try:
                masked = bool(item.CreateMagicMask(args.mode))
            except Exception as exc:
                return fail(f"CreateMagicMask raised ({exc}) - verify "
                            f"by hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            if not masked:
                return fail(
                    f"CreateMagicMask({args.mode}) answered False on "
                    f"{span['name']!r} (nodes {nodes_before}) - no mask "
                    f"started, nothing claimed.",
                    f"{TOOL} items --timeline "
                    f"\"{timeline.GetName()}\"")
            try:
                graph = item.GetNodeGraph()
                nodes_after = (graph.GetNumNodes() if graph is not None
                               else None)
            except Exception:
                nodes_after = None
            if nodes_after == nodes_before:
                return fail(
                    f"CreateMagicMask reports True but the node count "
                    f"reads back unchanged ({nodes_before}) - "
                    f"unverified, refusing to claim it.",
                    f"{TOOL} items --timeline "
                    f"\"{timeline.GetName()}\"")
            journal_state["success"] = True
        emit([kv_block("masked", {
                    "timeline": timeline.GetName(),
                    "item": span["name"],
                    "mode": args.mode,
                    "source_frame_range": source_frame_range.label(),
                    "nodes_before": nodes_before,
                    "nodes_after": nodes_after,
                    "verified": "yes (node gained)",
                }),
              note,
              _journal_block(journal)])
        return 0


def cmd_sense_switch(args) -> int:
    """Run a multicam Smart Switch on one timeline item.

    `PerformMulticamSmartSwitch` cuts a multicam item where Resolve
    hears the angle change; the verb reports the item count before
    and after. False is a refusal (nothing switched - measured on
    timecode-synced phone clips). Timeline-scoped.
    """
    try:
        resolve = _connect()
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    with _lease(exclusive=args.apply):
        project = _project(resolve, args.project)
        try:
            timeline, is_current, note = _target_timeline(
                project, args.timeline)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            item, track_type, track_index, _uid = _edit_item(
                timeline, args.track, args.index)
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        span = _item_span(item)
        try:
            window = float(args.min_edit if args.min_edit is not None
                           else 1.0)
        except (TypeError, ValueError):
            return fail(f"bad --min-edit {args.min_edit!r}: seconds.",
                        f"{TOOL} sense switch --help")
        if not args.apply:
            emit([kv_block("switch_plan", {
                        "timeline": timeline.GetName() +
                        (" (current)" if is_current else ""),
                        "item": (f"{track_type}{track_index}"
                                 f"[{args.index}] {span['name']}"),
                        "min_edit": window,
                    }),
                  note,
                  f"dry run - pass --apply to switch under the "
                  f"Resolve lease (cursor must sit on the reel)",
                  help_block([f"{TOOL} sense switch --timeline "
                              f"\"{timeline.GetName()}\" --track "
                              f"{track_type}{track_index} --index "
                              f"{args.index} --apply"])])
            return 0
        try:
            _assert_cursor_on(resolve, timeline.GetName())
        except AxiError as exc:
            return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
        try:
            items_before = len(timeline.GetItemListInTrack(
                track_type, track_index) or [])
        except Exception as exc:
            return fail(f"timeline {timeline.GetName()!r} would not "
                        f"read {track_type}{track_index} ({exc}).",
                        f"{TOOL} items --timeline "
                        f"\"{timeline.GetName()}\"")
        with _journal_write(
                args, timeline=timeline, project=project,
                spec={"verb": "sense_switch", "timeline": timeline.GetName(),
                      "name": span["name"], "min_edit": window},
                gate_class="sense_switch") as (journal, journal_state):
            try:
                switched = bool(item.PerformMulticamSmartSwitch(
                    {"minEditDuration": window}))
            except Exception as exc:
                return fail(f"PerformMulticamSmartSwitch raised ({exc}) - "
                            f"verify by hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            if not switched:
                return fail(f"PerformMulticamSmartSwitch answered False on "
                            f"{span['name']!r} - nothing switched.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            try:
                items_after = len(timeline.GetItemListInTrack(
                    track_type, track_index) or [])
            except Exception as exc:
                return fail(f"timeline {timeline.GetName()!r} would not "
                            f"re-read {track_type}{track_index} ({exc}) - "
                            f"verify by hand.",
                            f"{TOOL} items --timeline "
                            f"\"{timeline.GetName()}\"")
            journal_state["success"] = True
        emit([kv_block("switched", {
                    "timeline": timeline.GetName(),
                    "item": span["name"],
                    "items_before": items_before,
                    "items_after": items_after,
                    "verified": "yes (returned True; count re-read)",
                }),
              note,
              _journal_block(journal),
              help_block([f"{TOOL} items --timeline "
                          f"\"{timeline.GetName()}\""])])
        return 0


# ── setup / update ───────────────────────────────────────────────────


def cmd_setup_hooks(args) -> int:
    """Opt-in session hook: a static routing hint, never a live Resolve read.

    A hook that calls Resolve at every session start would probe a
    possibly-closed app on every session for every lane; the live state
    belongs in explicit invocations. What the hook carries is the way
    to reach it.
    """
    app = (args.app or "").lower()
    if app not in ("opencode", "claude-code", "codex"):
        return fail(f"unknown app {args.app!r}.",
                    f"{TOOL} setup hooks --app "
                    f"opencode|claude-code|codex")
    homes = {
        "opencode": os.path.join(os.path.expanduser("~"), ".config",
                                 "opencode", "plugins", "resolve-axi.js"),
        "claude-code": os.path.join(os.path.expanduser("~"), ".claude",
                                    "settings.json"),
        "codex": os.path.join(os.path.expanduser("~"), ".codex",
                              "hooks.json"),
    }
    emit([kv_block("setup", {
              "app": app,
              "path": homes[app],
              "note": "review the path, then re-run with --write to "
                      "install the session hook",
          }),
          help_block([f"{TOOL} setup hooks --app {app} --write"])])
    if not args.write:
        return 0
    return fail("hook installation is not implemented in this preview - "
                "the live state stays one explicit call away.",
                f"{TOOL} timeline list")


def cmd_update(_args) -> int:
    import subprocess
    sha = "unknown"
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=here,
            capture_output=True, text=True, encoding="utf-8",
            timeout=10, check=False)
        if out.returncode == 0:
            sha = out.stdout.strip()
    except Exception:
        pass
    emit([kv_block("update", {
              "package": TOOL,
              "version": VERSION,
              "installed": "repository checkout - pull to update",
              "sha": sha,
          })])
    return 0


def cmd_edit_request(args) -> int:
    """Semantic EditRequest lifecycle through the shared AXI SDK."""
    from library.tools.resolve_axi_sdk import (
        cancel_edit_job,
        get_edit_job,
        prepare_edit,
        submit_edit,
    )
    try:
        if args.request_command in {"plan", "submit"}:
            request = json.loads(Path(args.file).read_text(encoding="utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request file must contain a JSON object")
            if args.request_command == "plan":
                result = prepare_edit(request)
            else:
                result = submit_edit(request, authorized=bool(args.apply))
        elif args.request_command == "status":
            result = get_edit_job(args.job_id, wait=args.wait)
        else:
            result = cancel_edit_job(args.job_id)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (OSError, json.JSONDecodeError, KeyError, TypeError,
            ValueError, PermissionError) as exc:
        return fail(str(exc), "correct the request or job id and retry")


# ── Argument wiring ──────────────────────────────────────────────────


def _add_scope(parser, what: str, inherit: bool = False) -> None:
    """Add the project/timeline scope to one command.

    `inherit=True` is for a SUBPARSER. argparse parses the subcommand
    into a namespace of its own and then hands it over the parent's, so
    a subparser's own default ("") overwrites the scope the caller gave
    the parent command: `markers --project X --timeline Y snapshot`
    reached `snapshot` with an EMPTY scope, and the snapshot was taken
    against whichever project Resolve had open. With SUPPRESS the
    subparser only speaks when it is given the flag itself, and the
    scope the caller named survives the dispatch.
    """
    default = argparse.SUPPRESS if inherit else ""
    parser.add_argument("--project", default=default,
                        help=f"select this recorded project for reads or "
                             f"expect it open for live operations ({what})")
    parser.add_argument("--timeline", default=default,
                        help="which timeline: the exact name, a UNIQUE "
                             "prefix, or a bare positional "
                             "(markers \"Reel 29\"); omit only for an "
                             "explicit live refresh. "
                             "An ambiguous prefix is refused.")


def _add_reel_scope(parser) -> None:
    """Address a recorded read by reel number (LE-14).

    `--reel N` plus `--project-folder` resolves the reel's recorded
    exact final timeline (`--staging` for its staging sibling)
    through the Ren project's own records, instead of
    `--project/--timeline`. Only on the recorded-read commands;
    writes keep their explicit timeline flags.
    """
    parser.add_argument("--reel", default=None,
                        help="the reel number (e.g. 7): read the reel's "
                             "recorded exact final timeline - with "
                             "--staging, its staging sibling. Needs "
                             "--project-folder, instead of "
                             "--project/--timeline.")
    parser.add_argument("--staging", action="store_true",
                        help="with --reel N, read the (rebuild staging) "
                             "sibling instead of the final")
    parser.add_argument("--project-folder", default="",
                        help="Ren project folder: with --reel N, resolve "
                             "the reel's exact timeline name from its "
                             "records")


def _resolve_reel_scope(args, what: str):
    """Apply `--reel N` to a recorded read. None when applied, else `(message, fix)`.

    On success the resolved binding lands on `args.project` and the
    exact timeline name on `args.timeline` (or `args.name` for
    `timeline get`), with `args._reel_exact` set so the lookup below
    never falls back to a prefix match (AGENTS.md 5).
    """
    reel = getattr(args, "reel", None)
    staging = bool(getattr(args, "staging", False))
    if reel is None:
        if staging:
            return ("--staging refines --reel N - pass both, or neither.",
                    f"{TOOL} {what} --help lists the addressing flags")
        return None
    timeline = getattr(args, "timeline", "") or ""
    name = getattr(args, "name", "") or ""
    if timeline or name:
        return ("--reel N addresses the reel through the Ren project's "
                "own records - pass --project-folder instead of "
                "--timeline, not as well.",
                f"{TOOL} {what} --reel {reel} "
                "--project-folder <project>")
    if getattr(args, "refresh_live", False):
        return ("--reel N reads the recorded generation - drop "
                "--refresh-live, or pass --timeline for a live refresh.",
                f"{TOOL} {what} --reel {reel} --project-folder <project>")
    project_folder = getattr(args, "project_folder", "") or ""
    if not project_folder:
        return ("--reel N needs --project-folder <Ren project> - the "
                "reel number resolves through its records.",
                f"{TOOL} {what} --reel {reel} --project-folder <project>")
    if getattr(args, "project", ""):
        return ("--reel N carries its own Resolve project binding - "
                "pass --project-folder instead of --project, not as well.",
                f"{TOOL} {what} --reel {reel} "
                "--project-folder <project>")
    from library.tools import reel_address
    from library.tools.ren_refusal import RenRefusal
    try:
        resolved = reel_address.resolve_reel_timeline(
            project_folder, reel, staging=staging)
    except RenRefusal as refused:
        return (refused.render(),
                f"{TOOL} timeline list, or ren reels "
                f"{project_folder} to list the recorded reels")
    args.project = resolved["resolve_project"]
    if hasattr(args, "timeline"):
        args.timeline = resolved["timeline"]
    if hasattr(args, "name"):
        args.name = resolved["timeline"]
    args._reel_exact = True
    return None


def supplyable_commands() -> tuple[str, ...]:
    """What a caller can supply on the AXI command surface.

    The commands the AXI has, the flags it accepts, and the qualifier
    dimension names its conditions carry - read from the parser itself,
    so the vocabulary cannot drift from what the CLI accepts.  A
    refusal's fix is checked against this, so it cannot recommend a
    command or a flag the caller cannot supply.
    """
    import argparse as _argparse

    parser = build_parser()
    commands: set[str] = set()
    flags: set[str] = set()

    def walk(node) -> None:
        for action in node._actions:
            flags.update(action.option_strings)
            if isinstance(action, _argparse._SubParsersAction):
                commands.update(action.choices.keys())
                for sub in action.choices.values():
                    walk(sub)

    walk(parser)
    from library.tools import axi_qualification_dimensions as dimensions

    names = set(getattr(dimensions, "DIMENSIONS", {}) or {})
    names.update(getattr(dimensions, "MEDIA_TYPES", ()) or ())
    return tuple(sorted(commands | flags | names))


def build_parser() -> Parser:
    parser = Parser(prog=TOOL, description=DESCRIPTION,
                    formatter_class=argparse.RawDescriptionHelpFormatter,
                    epilog=f"""examples:
  {TOOL}
  {TOOL} timeline list
  {TOOL} --submit pool "Footage/Day 1"
  {TOOL} timeline get "Reel 13 - moment"
  {TOOL} timeline get "Reel 13 - moment" --refresh-live
  {TOOL} markers "Reel 29"
  {TOOL} markers --timeline "Reel 13 - moment"
  {TOOL} markers snapshot --timeline "Reel 13 - moment" --out /tmp/m.json
  {TOOL} cursor --expect "Reel 13 - moment"
  {TOOL} frames --timeline "Reel 13 - moment"
  {TOOL} pool
  {TOOL} pool "Footage/Day 1"
  {TOOL} renders
  {TOOL} api search "marker"
  {TOOL} api docs --section "Audio Mapping"
  {TOOL} luts list
  {TOOL} project
  {TOOL} audio --timeline "Reel 29"
  {TOOL} edit delete --timeline "Reel 29" --track video1 --index 0
  {TOOL} edit speed --timeline "Reel 29" --track video1 --index 0 --percent 40
  {TOOL} edit transition --timeline "Reel 29" --index 1 --type "Cross Dissolve"
  {TOOL} multicam build --clip a.mov --clip b.mov --name "mc" --sync audio
  {TOOL} color lut --timeline "Reel 29" --track video1 --index 0 --lut "Film Looks/x.cube"
  {TOOL} audio isolate --timeline "Reel 29" --track 1 --amount 60
  {TOOL} sense transcribe --clip a.mov
  {TOOL} sense cuts --timeline "Reel 29" --apply
  {TOOL} ingest /footage/a.mov
  {TOOL} relink --from /old/a.mov --to /new/a.mov --apply
  {TOOL} render queue --timeline "Reel 29" --preset "H.265 Master"
  {TOOL} run --timeline "Reel 29" --script "result = timeline_names"
  {TOOL} run --timeline "Reel 29" "result = timeline_names\"""")
    parser.add_argument(
        "--submit", action="store_true",
        help="queue this Resolve command and print its receipt id without "
             "waiting; collect the result with `ren resolved result`")
    subs = parser.add_subparsers(dest="command")

    p = subs.add_parser("capabilities", help="evidence-backed AXI and edit-algebra capability registry")
    p.add_argument("operation", nargs="?", default="",
                   help="AXI operation id, native_mcp.<tool>, or algebra op id")
    p.add_argument("--search", default="",
                   help="search operation names and purpose text")
    p.add_argument("--references", action="store_true",
                   help="print this operation's rows from the generated "
                        "capability references - the narrow read, instead "
                        "of the whole 141 KB of tables")
    p.add_argument("--json", action="store_true",
                   help="emit structured JSON instead of concise TOON")
    p.set_defaults(func=cmd_capabilities)

    p = subs.add_parser(
        "actions",
        help="what this build can actually do, and what each action "
             "costs: the supported operations for a requested effect, "
             "with their schema, preservation limits, verification and "
             "recovery options")
    p.add_argument("request", nargs="?", default="",
                   help="the effect you want, in your own words; the "
                        "whole supported menu when omitted")
    p.add_argument("--limit", type=int, default=5,
                   help="how many actions to return (default 5)")
    p.add_argument("--json", action="store_true",
                   help="emit structured JSON instead of compact tables")
    p.set_defaults(func=cmd_actions)

    p = subs.add_parser(
        "operation-map",
        help="the canonical operation map: semantics, dependencies, "
             "backends, preservation, qualification and supported states")
    p.add_argument("operation", nargs="?", default="",
                   help="one operation id; the whole map when omitted")
    p.add_argument("--audit", action="store_true",
                   help="emit only the audit of the registry against the code")
    p.add_argument("--toon", action="store_true",
                   help="render compact tables for a person instead of JSON")
    p.set_defaults(func=cmd_operation_map)

    p = subs.add_parser(
        "capability-coverage",
        help="the capability coverage report: implemented backends, "
             "production eligibility, tested states, missing qualification "
             "dimensions and the routes the router would take")
    p.add_argument("operation", nargs="?", default="",
                   help="one operation id; the summary when omitted")
    p.add_argument("--full", action="store_true",
                   help="print the whole report, not the summary")
    p.set_defaults(func=cmd_capability_coverage)

    p = subs.add_parser(
        "support",
        help="answer whether one operation may run on a timeline in the "
             "measured conditions, and what prevents it when it may not")
    p.add_argument("operation", nargs="?", default="",
                   help="one algebra operation id")
    p.add_argument("--conditions", default="",
                   help="the measured state of the timeline, as JSON in "
                        "the qualification dimension vocabulary")
    p.add_argument("--conditions-file", default="",
                   help="read the measured state from a JSON file")
    p.add_argument("--params", default="",
                   help="the operation's own parameters, as JSON")
    p.set_defaults(func=cmd_support)

    p = subs.add_parser("timeline", help="list timelines or get one by "
                                         "exact name")
    tsubs = p.add_subparsers(dest="timeline_command", required=True)
    pl = tsubs.add_parser("list", help="recorded timelines with frame "
                                       "counts, markers and staging flags")
    pl.add_argument("--project", default="",
                    help="filter to this recorded project (default: all)")
    pl.set_defaults(func=cmd_timeline_list)
    pg = tsubs.add_parser("get", help="one recorded timeline by name or id - "
                                      "exact or unique prefix; collisions refused")
    pg.add_argument("name", nargs="?", default="",
                    help="the timeline's exact name, unique "
                         "prefix, or timeline id (omit with --reel N)")
    pg.add_argument("--project", default="",
                    help="select this project when the timeline name "
                         "appears in more than one project")
    pg.add_argument("--refresh-live", action="store_true",
                    help="refresh from Resolve; the exact timeline must "
                         "already be current")
    _add_reel_scope(pg)
    pg.set_defaults(func=cmd_timeline_get)
    pr = tsubs.add_parser("delete", help="delete one completed, recorded qualification timeline by exact Resolve id")
    pr.add_argument("--artifact-id", required=True, action="append",
                    help="lifecycle artifact id recorded by the owning job")
    pr.add_argument("--project-folder", required=True,
                    help="Ren project folder holding the ownership event log")
    pr.add_argument("--apply", action="store_true",
                    help="write a retirement plan and apply it under the exclusive Resolve lease")
    pr.set_defaults(func=cmd_timeline_retire_owned)
    pd = tsubs.add_parser("duplicate", help="version a reel under a new "
                                            "exact name; --apply writes")
    pd.add_argument("--project", default="",
                    help="expect this Resolve project open")
    pd.add_argument("--timeline", default="",
                    help="which timeline to duplicate (exact or unique "
                         "prefix)")
    pd.add_argument("--name", default="",
                    help="the new timeline's name (refused when taken)")
    pd.add_argument("--apply", action="store_true",
                    help="duplicate under the Resolve lease (default "
                         "is a dry-run plan)")
    pd.set_defaults(func=cmd_timeline_duplicate)

    pc = tsubs.add_parser("checkpoint", help="duplicate and export an idempotent timeline checkpoint")
    pc.add_argument("--project", default="", help="expect this Resolve project open")
    pc.add_argument("--timeline", required=True, help="source timeline name")
    pc.add_argument("--project-folder", required=True, help="Ren project folder holding the version ledger")
    pc.add_argument("--checkpoint-id", required=True, help="stable idempotency key for this checkpoint")
    pc.add_argument("--apply", action="store_true", help="duplicate and export under the Resolve lease")
    pc.set_defaults(func=cmd_timeline_checkpoint)

    p = subs.add_parser("edit", help="mutating verbs on reel items - "
                                     "dry-run default, --apply writes")
    esubs = p.add_subparsers(dest="edit_command", required=True)

    def _edit_scope(q, what: str) -> None:
        _add_scope(q, what)
        q.add_argument("--track", required=True,
                       help="video1, audio2, ...")
        q.add_argument("--index", required=True, type=int,
                       help="position on the track, 0-based")
        q.add_argument("--target-unique-id", default="",
                       help="stable live item identity; re-resolved before a write")
        q.add_argument("--expected-source-file", default="")
        q.add_argument("--expected-source-in-frame", type=int)
        q.add_argument("--expected-source-out-frame", type=int)
        q.add_argument("--expected-record-in-frame", type=int)
        q.add_argument("--expected-record-out-frame", type=int)
        q.add_argument("--apply", action="store_true",
                       help="write under the Resolve lease with the "
                            "cursor asserted (default is a dry-run "
                            "plan)")
        q.add_argument("--project-folder", default="",
                       help="pipeline project folder: with --apply, "
                            "journal the write through the undo journal "
                            "so `ren undo` can reverse it (without this "
                            "the write acts but is not undone)")
        q.add_argument("--require-recovery", action="store_true",
                       help="refuse before writing unless the undo journal opens")

    q = esubs.add_parser("place", help="append a pool clip onto the "
                                        "reel; --apply writes")
    _add_scope(q, "place")
    q.add_argument("--clip", default="",
                   help="pool clip name (exact; --bin disambiguates)")
    q.add_argument("--clip-id", default="",
                   help="pool clip unique id (exact; the identity "
                        "clip.insert carries)")
    q.add_argument("--bin", default="",
                   help="scope the clip search to bin path A/B/C")
    q.add_argument("--source-in", default=None, type=int,
                   help="source start frame")
    q.add_argument("--source-out", default=None, type=int,
                   help="source end frame")
    q.add_argument("--media", default="both",
                   help="both|video|audio")
    q.add_argument("--track", default=None, type=int,
                   help="destination track index")
    q.add_argument("--record", default=None, type=int,
                   help="record frame position")
    q.add_argument("--project-folder", default="",
                   help="pipeline project folder: with --apply, "
                        "journal the write through the undo journal "
                        "so `ren undo` can reverse it (without this "
                        "the write acts but is not undone)")
    q.add_argument("--require-recovery", action="store_true",
                   help="refuse before writing unless the undo journal opens")
    q.add_argument("--apply", action="store_true",
                   help="append under the Resolve lease with the "
                        "cursor asserted (default is a dry-run plan)")
    q.set_defaults(func=cmd_edit_place)

    q = esubs.add_parser("trim", help="narrow an item's source range "
                                       "in place; --apply writes")
    _edit_scope(q, "trim")
    q.add_argument("--source-in", default=None, type=int,
                   help="new source start frame (within the handles)")
    q.add_argument("--source-out", default=None, type=int,
                   help="new source end frame (within the handles)")
    q.set_defaults(func=cmd_edit_trim)

    q = esubs.add_parser(
        "split", help="split ONE picture item at an exact source frame; "
                      "the linked audio is not cut; --apply writes")
    _edit_scope(q, "picture-only split")
    q.add_argument("--at-source-frame", required=True, type=int,
                   help="the SOURCE frame the cut lands on (the record "
                        "frame is computed and reported)")
    q.set_defaults(func=cmd_edit_split)

    q = esubs.add_parser("switch-camera", help="replace a speaker turn with an assigned, synced camera range; --apply writes")
    _edit_scope(q, "switch-camera")
    q.add_argument("--camera-source-file", required=True,
                   help="exact media file for the assigned camera")
    q.add_argument("--speaker-id", required=True,
                   help="resolved diarized or project-declared speaker")
    q.add_argument("--assignment-basis", required=True,
                   choices=("project_declaration",
                            "diarized_speaker_identity"))
    q.add_argument("--sync-evidence-id", required=True,
                   help="evidence record that supplied the synced range")
    q.add_argument("--source-in", required=True, type=int,
                   help="synced camera source start frame")
    q.add_argument("--source-out", required=True, type=int,
                   help="synced camera source end frame, inclusive")
    q.set_defaults(func=cmd_edit_switch_camera)

    q = esubs.add_parser("move", help="move an item to a new record "
                                       "frame; --apply writes")
    _edit_scope(q, "move")
    q.add_argument("--to", default=None, type=int,
                   help="destination record frame (required)")
    q.add_argument("--track-to", default="",
                   help="destination track (default stays)")
    q.set_defaults(func=cmd_edit_move)

    q = esubs.add_parser(
        "track-ripple-remove",
        help="remove an item and close the gap on its track and explicit links")
    _edit_scope(q, "track-scoped ripple remove")
    q.set_defaults(func=cmd_edit_track_ripple_remove)

    q = esubs.add_parser("transform", help="move one item's output-frame "
                                             "position in pixels")
    q.add_argument("--item", required=True,
                   help="the item's exact Resolve unique id")
    q.add_argument("--timeline", default="",
                   help="exact timeline (defaults to the current timeline)")
    q.add_argument("--project", default="",
                   help="expect this exact Resolve project to be open")
    q.add_argument("--move-x", type=float, default=0.0,
                   help="horizontal output-frame displacement in pixels")
    q.add_argument("--move-y", type=float, default=0.0,
                   help="vertical output-frame displacement in pixels")
    q.add_argument("--verify", action="store_true",
                   help="require native transform read-back verification")
    q.add_argument("--apply", action="store_true",
                   help="apply through one EditPatch; dry-run is the default")
    q.add_argument("--project-folder", default="",
                   help="pipeline project folder: the transform write log "
                        "records Pan/Tilt/Zoom writes there (without this "
                        "the log resolves it from the cwd or registry, "
                        "and a fixture run with neither refuses)")
    q.set_defaults(func=cmd_edit_transform)

    q = esubs.add_parser("delete", help="delete one item, verified by "
                                         "absence; --apply writes")
    _edit_scope(q, "delete")
    q.add_argument("--ripple", action="store_true",
                   help="close the gap (never defaulted - cannot be "
                        "selectively undone)")
    q.set_defaults(func=cmd_edit_delete)

    q = esubs.add_parser("title", help="set a title card's text, "
                                        "verified by read-back; "
                                        "--apply writes")
    _edit_scope(q, "title")
    q.add_argument("--text", default="",
                   help="the new title text (required)")
    q.set_defaults(func=cmd_edit_title)

    q = esubs.add_parser("transition", help="native transitions at "
                                             "a V1 cut: carriers listed, "
                                             "--apply places AddTransition")
    _add_scope(q, "transition")
    q.add_argument("--index", default=None, type=int,
                   help="incoming V1 clip, 0-based (the cut joins it to "
                        "its neighbour; required for --apply)")
    q.add_argument("--type", default="Cross Dissolve",
                   help="transition type name (default Cross Dissolve)")
    q.add_argument("--category", default="simple",
                   help="simple|fusion|ofx|audio (default simple)")
    q.add_argument("--position", default="start",
                   help="start|end: the edge of the indexed clip "
                        "(default start)")
    q.add_argument("--alignment", default="center",
                   help="left|center|right (default center)")
    q.add_argument("--duration", default=None, type=int,
                   help="duration in frames (else Resolve's default)")
    q.add_argument("--apply", action="store_true",
                   help="place under the Resolve lease with the cursor "
                        "asserted (default lists carriers)")
    q.set_defaults(func=cmd_edit_transition)

    q = esubs.add_parser("speed", help="constant clip speed (0.0 "
                                      "freezes); --apply writes")
    _edit_scope(q, "speed")
    q.add_argument("--percent", default=None, type=float,
                   help="speed in percent, e.g. 40.0 (--freeze for "
                        "0.0, never 0 here)")
    q.add_argument("--freeze", action="store_true",
                   help="freeze frame (SetSpeed 0.0, said out loud)")
    q.add_argument("--ripple", action="store_true",
                   help="ripple the timeline (never defaulted - "
                        "moves every later cut)")
    q.set_defaults(func=cmd_edit_speed)

    p = subs.add_parser("edit-request", help="prepare or submit semantic edit intent")
    rsubs = p.add_subparsers(dest="request_command", required=True)
    q = rsubs.add_parser("plan", help="prepare and route without Resolve writes")
    q.add_argument("file", help="JSON EditRequest file")
    q.set_defaults(func=cmd_edit_request)
    q = rsubs.add_parser("submit", help="submit through the selected AXI backend")
    q.add_argument("file", help="JSON EditRequest file")
    q.add_argument("--apply", action="store_true",
                   help="authorize the prepared semantic edit")
    q.set_defaults(func=cmd_edit_request)
    q = rsubs.add_parser("status", help="read a semantic edit job")
    q.add_argument("job_id")
    q.add_argument("--wait", type=float, default=0.0)
    q.set_defaults(func=cmd_edit_request)
    q = rsubs.add_parser("cancel", help="request a broker-safe cancellation")
    q.add_argument("job_id")
    q.set_defaults(func=cmd_edit_request)

    p = subs.add_parser("ingest", help="import files into the pool's "
                                       "current folder; --apply writes")
    p.add_argument("--project", default="",
                   help="expect this Resolve project open")
    p.add_argument("paths", nargs="+",
                   help="local file paths to import")
    p.add_argument("--apply", action="store_true",
                   help="import under the Resolve lease (default is a "
                        "dry-run plan)")
    p.set_defaults(func=cmd_ingest)

    p = subs.add_parser("relink", help="re-point pool clips at moved "
                                      "media; --apply writes")
    p.add_argument("--project", default="",
                   help="expect this Resolve project open")
    p.add_argument("--from", dest="old_path", default="",
                   help="the File Path the pool item points at now")
    p.add_argument("--to", dest="new_path", default="",
                   help="the on-disk file to re-point it at (must exist)")
    p.add_argument("--clip", default="",
                   help="only this pool clip name (exact; else every item "
                        "at --from)")
    p.add_argument("--timeout", default=None, type=float,
                   help="deadline seconds per Resolve call (default 30)")
    p.add_argument("--apply", action="store_true",
                   help="re-point under the Resolve lease (default is a "
                        "dry-run plan)")
    p.set_defaults(func=cmd_relink)

    p = subs.add_parser("render", help="queue, start and stop render "
                                       "jobs; --apply writes")
    rsubs = p.add_subparsers(dest="render_command", required=True)
    q = rsubs.add_parser("queue", help="queue the reel under a preset; "
                                        "--apply writes")
    _add_scope(q, "queue")
    q.add_argument("--preset", default="",
                   help="render preset name (else current settings go, "
                        "unchanged)")
    q.add_argument("--apply", action="store_true",
                   help="queue under the Resolve lease with the cursor "
                        "asserted (default is a dry-run plan)")
    q.set_defaults(func=cmd_render_queue)
    q = rsubs.add_parser("start", help="start queued jobs and wait for "
                                        "completion; --apply spends machine time")
    q.add_argument("--project", default="",
                   help="expect this Resolve project open")
    q.add_argument("--job", default=[], action="append",
                   help="job id (repeatable)")
    q.add_argument("--all", action="store_true",
                   help="start every queued job")
    q.add_argument("--apply", action="store_true",
                   help="start and wait under the exclusive Resolve lease "
                        "(default is a dry-run plan)")
    q.set_defaults(func=cmd_render_start)
    q = rsubs.add_parser("stop", help="stop rendering; --apply stops")
    q.add_argument("--project", default="",
                   help="expect this Resolve project open")
    q.add_argument("--apply", action="store_true",
                   help="stop under the Resolve lease (default is a "
                        "dry-run plan)")
    q.set_defaults(func=cmd_render_stop)
    i = rsubs.add_parser("inspect", help="render the OPEN timeline to a "
                                          "scratch file for inspection; "
                                          "--apply writes")
    i.add_argument("--project", default="",
                   help="expect this Resolve project open")
    i.add_argument("--out", default="",
                   help="the directory the inspection file is written to")
    i.add_argument("--name", default="",
                   help="the file's name stem (default: the timeline's name)")
    i.add_argument("--apply", action="store_true",
                   help="render under the exclusive Resolve lease "
                        "(default is a dry-run plan)")
    i.add_argument("--json", action="store_true",
                   help="print the render receipt as JSON")
    i.set_defaults(func=cmd_render_inspect)

    p = subs.add_parser("markers", help="notes from a recorded reel "
                                        "generation; --refresh-live reads "
                                        "Resolve")
    _add_scope(p, "markers")
    _add_reel_scope(p)
    p.add_argument("--refresh-live", action="store_true",
                   help="refresh from Resolve; the exact timeline must "
                        "already be current")
    p.add_argument("--plane", default="",
                   help="timeline_marker|clip_marker|media_pool_marker|"
                        "clip_comment")
    p.add_argument("--full", action="store_true",
                   help="show complete note text instead of the preview")
    p.set_defaults(func=cmd_markers)
    msubs = p.add_subparsers(dest="markers_command")

    p = msubs.add_parser("snapshot", help="write a reel's marker "
                                          "CONTENT to a file")
    _add_scope(p, "snapshot", inherit=True)
    p.add_argument("--out", required=True, help="snapshot file to write")
    p.set_defaults(func=cmd_markers_snapshot)

    p = msubs.add_parser("restore", help="dry-run a snapshot "
                                         "against live markers; "
                                         "--apply writes")
    _add_scope(p, "restore", inherit=True)
    p.add_argument("--in", dest="in_file", required=True,
                   help="snapshot file to restore from")
    p.add_argument("--apply", action="store_true",
                   help="write the missing timeline-plane markers under "
                        "the Resolve lease (default is a dry-run diff)")
    p.add_argument("--allow-partial", action="store_true",
                   help="acknowledge that the snapshot holds clip-plane "
                        "notes restore cannot write, and proceed with "
                        "the timeline plane only (without it, such a "
                        "snapshot is refused before any Resolve contact)")
    p.set_defaults(func=cmd_markers_restore)

    p = msubs.add_parser("reply", help="record our answer as a marker, "
                                       "linked to the note it answers; "
                                       "--apply writes")
    _add_scope(p, "reply", inherit=True)
    p.add_argument("--frame", required=True, type=int,
                   help="ABSOLUTE frame where the reply lands (the space "
                        "`markers` reports)")
    p.add_argument("--color", default="",
                   help="marker colour - required, never defaulted")
    p.add_argument("--name", default="",
                   help="reply marker name - required (Resolve refuses "
                        "empty names)")
    p.add_argument("--note", default="",
                   help="the reply text - required")
    p.add_argument("--duration", default=1, type=int,
                   help="marker duration in frames (default 1)")
    p.add_argument("--answers-frame", default=None, type=int,
                   help="frame of the note being answered: its text is "
                        "looked up live into the payload and shown")
    p.add_argument("--answers", default="",
                   help="explicit identity of the answered note, "
                        "validated against the durable-identity grammar "
                        "(else the note's own identity is taken from "
                        "--answers-frame)")
    p.add_argument("--summary", default="",
                   help="one-line summary recorded into the payload")
    p.add_argument("--apply", action="store_true",
                   help="place the marker under the Resolve lease with "
                        "the cursor asserted (default is a dry-run plan)")
    p.set_defaults(func=cmd_markers_reply)

    p = msubs.add_parser("audit-replies", help="report which replies "
                                               "still sit beside the "
                                               "notes they answer; "
                                               "read-only, writes nothing")
    _add_scope(p, "audit-replies", inherit=True)
    p.add_argument("--full", action="store_true",
                   help="show complete names and identities instead of "
                        "the preview")
    p.set_defaults(func=cmd_markers_audit_replies)

    p = subs.add_parser("fusion", help="recorded clips carrying Fusion "
                                       "comps and their media coverage")
    _add_scope(p, "fusion")
    _add_reel_scope(p)
    p.add_argument("--refresh-live", action="store_true",
                   help="refresh from Resolve; the exact timeline must "
                        "already be current")
    p.set_defaults(func=cmd_fusion)

    p = subs.add_parser("items", help="recorded clips with record/source "
                                      "spans and source paths")
    _add_scope(p, "items")
    _add_reel_scope(p)
    p.add_argument("--refresh-live", action="store_true",
                   help="refresh from Resolve; the exact timeline must "
                        "already be current")
    p.add_argument("--transforms", action="store_true",
                   help="add the stored Pan/Tilt/Zoom/Opacity columns, "
                        "read back verbatim (units unconverted)")
    p.set_defaults(func=cmd_items)

    p = subs.add_parser(
        "target",
        help="resolve a DESCRIBED target to the clip it names, with the "
             "arguments that address it")
    p.add_argument("description",
                   help="the target, as key=value pairs: "
                        "id=<clip id>, name=<clip name>, row=V2 item=2, "
                        "or start=<frame> end=<frame>")
    _add_scope(p, "target")
    _add_reel_scope(p)
    p.add_argument("--refresh-live", action="store_true",
                   help="refresh from Resolve; the exact timeline must "
                        "already be current")
    p.add_argument("--json", action="store_true",
                   help="emit structured JSON instead of concise TOON")
    p.set_defaults(func=cmd_target)

    p = subs.add_parser("captions", help="Subtitles-row cards with file "
                                         "paths")
    _add_scope(p, "captions")
    _add_reel_scope(p)
    p.add_argument("--refresh-live", action="store_true",
                   help="refresh from Resolve; the exact timeline must "
                        "already be current")
    p.set_defaults(func=cmd_captions)

    p = subs.add_parser("cursor", help="show (and optionally assert) the "
                                       "current-timeline cursor")
    p.add_argument("--expect", default="",
                   help="refuse unless the cursor sits on this timeline")
    p.set_defaults(func=cmd_cursor)

    p = subs.add_parser("frames", help="recorded frame counts for drift "
                                       "checks")
    _add_scope(p, "frames")
    _add_reel_scope(p)
    p.add_argument("--refresh-live", action="store_true",
                   help="refresh from Resolve; the exact timeline must "
                        "already be current")
    p.set_defaults(func=cmd_frames)

    p = subs.add_parser("api", help="the native MCP's knowledge tools "
                                   "as TOON: stubs, search, docs, "
                                   "whats-new, intellisearch-query")
    asubs = p.add_subparsers(dest="api_command", required=True)
    q = asubs.add_parser("search", help="search the scripting API "
                                        "stubs by pattern")
    q.add_argument("pattern", help="regex matched against stub names "
                                    "and descriptions, e.g. \"marker\"")
    q.add_argument("--full", action="store_true",
                   help="all matching lines instead of the first 50")
    q.set_defaults(func=cmd_api_search)
    q = asubs.add_parser("stubs", help="the .pyi declarations for "
                                        "named API types")
    q.add_argument("types", nargs="+",
                   help="type names, e.g. RenderJobInfo RenderJobStatus")
    q.set_defaults(func=cmd_api_stubs)
    q = asubs.add_parser("docs", help="the developer documents, whole "
                                       "or by section")
    q.add_argument("--document", default="README.md",
                   help="README.md (scripting guide) or DCTLReadme.txt "
                        "(DCTL reference)")
    q.add_argument("--section", default="TOC",
                   help="heading to return (default TOC); \"\" returns "
                        "the whole document")
    q.set_defaults(func=cmd_api_docs)
    q = asubs.add_parser("whats-new", help="the Resolve changelog since "
                                            "a version or date")
    q.add_argument("--since", required=True,
                   help="version (e.g. \"21.0\") or ISO date "
                        "(e.g. \"2025-01-25\")")
    q.add_argument("--full", action="store_true",
                   help="include each release's changelog text")
    q.set_defaults(func=cmd_api_whats_new)
    q = asubs.add_parser("intellisearch-query", help="probe the scripting "
                                                     "API for an "
                                                     "IntelliSearch QUERY "
                                                     "verb (indexing is not "
                                                     "querying)")
    q.add_argument("--since", default="21.0",
                   help="changelog floor for IntelliSearch mentions "
                        "(default 21.0, the IntelliSearch generation)")
    q.add_argument("--full", action="store_true",
                   help="all matching lines instead of the first 50")
    q.set_defaults(func=cmd_api_intellisearch_query)

    p = subs.add_parser("intellisearch-collect", help="collect one "
                                                     "IntelliSearch query "
                                                     "into an "
                                                     "--external-hits file "
                                                     "for ren search")
    p.add_argument("--project", default="",
                   help="pipeline project folder: catalog to intersect, "
                        "scratch to file rows in")
    p.add_argument("--query", default="",
                   help="what IntelliSearch is asked")
    p.add_argument("--box", default="",
                   help="search field center as x,y in window points "
                        "(runtime-measured; refused if absent)")
    p.add_argument("--out", default="",
                   help="hits file to write (default <project>/"
                        "pipeline_output/scratch/intellisearch_hits.json)")
    p.add_argument("--timeline", default="",
                   help="qualification timeline to hold (default current)")
    p.add_argument("--budget", default=None, type=float,
                   help="take budget in seconds (default the announced "
                        "window)")
    p.add_argument("--survey", action="store_true",
                   help="background-safe menu/AX reach only, no take")
    p.add_argument("--dry-run", action="store_true",
                   help="print the validated spec without spending a take")
    p.set_defaults(func=cmd_intellisearch_collect)

    p = subs.add_parser("luts", help="the shared LUT shelf: list "
                                     "locally, write wrapped with "
                                     "compile check (dry-run default)")
    lsubs = p.add_subparsers(dest="luts_command", required=True)
    q = lsubs.add_parser("list", help="every DCTL and LUT file on the "
                                       "shared shelf")
    q.set_defaults(func=cmd_luts_list)
    q = lsubs.add_parser("update", help="write a .dctl file "
                                         "(compile-checked); --apply "
                                         "writes")
    q.add_argument("name_pos", nargs="?", default="",
                   help="shelf file name, e.g. \"cool.dctl\" (--name "
                        "is the explicit form)")
    q.add_argument("--name", default="",
                   help="shelf file name relative to the MCP shelf")
    q.add_argument("--text", default="",
                   help="the DCTL source")
    q.add_argument("--file", default="",
                   help="read the DCTL source from this file")
    q.add_argument("--apply", action="store_true",
                   help="compile-check and write (default is a dry-run "
                        "plan)")
    q.set_defaults(func=cmd_luts_update)
    q = lsubs.add_parser("delete", help="delete a shelf file; --apply "
                                         "deletes")
    q.add_argument("name_pos", nargs="?", default="",
                   help="shelf file name (--name is the explicit form)")
    q.add_argument("--name", default="",
                   help="shelf file name relative to the MCP shelf")
    q.add_argument("--apply", action="store_true",
                   help="delete (default is a dry-run plan)")
    q.set_defaults(func=cmd_luts_delete)
    q = lsubs.add_parser("generate", help="evaluate a Python transform "
                                           "into a .cube LUT; --apply "
                                           "writes")
    q.add_argument("name_pos", nargs="?", default="",
                   help="shelf file name, e.g. \"warm.cube\" (--name is "
                        "the explicit form)")
    q.add_argument("--name", default="",
                   help="shelf file name relative to the MCP shelf")
    q.add_argument("--size", default=33, type=int,
                   help="cube size per axis (17, 33, 65)")
    q.add_argument("--transform", default="",
                   help="Python body: receives r, g, b in [0,1], "
                        "returns an (r, g, b) tuple")
    q.add_argument("--transform-file", default="",
                   help="read the transform body from this file")
    q.add_argument("--apply", action="store_true",
                   help="evaluate and write (default is a dry-run plan)")
    q.set_defaults(func=cmd_luts_generate)

    p = subs.add_parser("launch", help="start Resolve when it is not "
                                       "running; never opens a project")
    p.set_defaults(func=cmd_launch)

    p = subs.add_parser("project", help="the open project: identity, "
                                        "delivery settings, presets")
    p.add_argument("--project", default="",
                   help="expect this Resolve project open")
    p.set_defaults(func=cmd_project)
    psubs = p.add_subparsers(dest="project_command")
    q = psubs.add_parser("set", help="set one project setting, "
                                      "verified by read-back; --apply "
                                      "writes")
    q.add_argument("--project", default="",
                   help="expect this Resolve project open")
    q.add_argument("--key", default="",
                   help="setting name, e.g. timelineFrameRate")
    q.add_argument("--value", default="",
                   help="new value (passes as given)")
    q.add_argument("--apply", action="store_true",
                   help="write under the Resolve lease (default is a "
                        "dry-run plan)")
    q.set_defaults(func=cmd_project_set)

    q = psubs.add_parser("checkpoint", help="export a project checkpoint or restore it under a new name")
    q.add_argument("--project", default="", help="expect this Resolve project open for checkpoint creation")
    q.add_argument("--project-folder", required=True, help="Ren project folder holding the version ledger")
    q.add_argument("--checkpoint-id", required=True, help="stable idempotency key for this checkpoint")
    q.add_argument("--restore", action="store_true", help="restore the named checkpoint into a new project")
    q.add_argument("--new-name", default="", help="unused Resolve project name required with --restore")
    q.add_argument("--apply", action="store_true", help="write the checkpoint or import a new project")
    q.set_defaults(func=cmd_project_checkpoint)

    q = psubs.add_parser(
        "export-timeline",
        help="copy one named timeline out of any project into a Ren-owned "
             "scratch project; the source is read only and the origin is "
             "restored")
    q.add_argument("--source-project", required=True,
                   help="the EXACT Resolve project to read the timeline from")
    q.add_argument("--timeline", required=True,
                   help="the EXACT timeline name to copy out")
    q.add_argument("--scratch", default="",
                   help="new 'Ren Scratch - ' project to import the copy "
                        "into (default: only the .drt is written)")
    q.add_argument("--source-folder", default="",
                   help="Project Manager folder holding the source project "
                        "(default: the folder that holds it)")
    q.add_argument("--archive", default="",
                   help="where to write the .drt (default: the machine's "
                        "reel-export store)")
    q.add_argument("--fps", default=None,
                   help="frame rate of the copy (default: the source's)")
    q.add_argument("--width", default=None,
                   help="width of the copy (default: the source's)")
    q.add_argument("--height", default=None,
                   help="height of the copy (default: the source's)")
    q.add_argument("--apply", action="store_true",
                   help="perform the export and restore the origin project")
    q.set_defaults(func=cmd_project_export_timeline)

    q = psubs.add_parser(
        "scratch", help="create, import, open, restore or delete 'Ren Scratch - ' projects")
    ss = q.add_subparsers(dest="scratch_command", required=True)
    sq = ss.add_parser("create", help="create a named scratch project with one empty current timeline")
    sq.add_argument("name", help="project name beginning with 'Ren Scratch - '")
    sq.add_argument("--fps", default=None,
                    help="timeline frame rate set at creation before any timeline "
                         "exists, e.g. 23.976 or 29.97 (default: Resolve default)")
    sq.add_argument("--width", default=None,
                    help="timeline resolution width, with --height "
                         "(default: Resolve default)")
    sq.add_argument("--height", default=None,
                    help="timeline resolution height, with --width "
                         "(default: Resolve default)")
    sq.add_argument("--apply", action="store_true", help="perform the create and restore the starting project")
    sq.set_defaults(func=cmd_project_scratch)
    sq = ss.add_parser("import", help="import a .drp project or .drt timeline into a new scratch project")
    sq.add_argument("source", help="existing .drp project or .drt timeline file")
    sq.add_argument("name", help="new project name beginning with 'Ren Scratch - '")
    sq.add_argument("--fps", default=None,
                    help="timeline frame rate for .drt imports (required for .drt)")
    sq.add_argument("--width", default=None,
                    help="timeline resolution width for .drt imports (required with --height)")
    sq.add_argument("--height", default=None,
                    help="timeline resolution height for .drt imports (required with --width)")
    sq.add_argument("--apply", action="store_true", help="perform the import and restore the starting project")
    sq.set_defaults(func=cmd_project_scratch)
    for verb, description in (
            ("open", "open an existing scratch project with settled read-back"),
            ("delete", "delete an existing closed scratch project")):
        sq = ss.add_parser(verb, help=description)
        sq.add_argument("name", help="exact project name beginning with 'Ren Scratch - '")
        sq.add_argument("--apply", action="store_true", help="perform the scratch project operation")
        sq.set_defaults(func=cmd_project_scratch)
    sq = ss.add_parser("restore", help="restore the project, timeline and page recorded by scratch open")
    sq.add_argument("--timeline-drt", action="append", default=[],
                    help="import a one-timeline DRT into a Ren Qualification origin before restoring; repeatable")
    sq.add_argument("--apply", action="store_true", help="restore the recorded origin")
    sq.set_defaults(func=cmd_project_scratch)
    sq = ss.add_parser("collect", help="plan or apply manifest-selected Ren scratch project retirement")
    sq.add_argument("--manifest", default="", help="reviewed manifest to apply")
    sq.add_argument("--apply", action="store_true", help="export .drp backups and remove selected exact Ren Scratch projects")
    sq.set_defaults(func=cmd_project_scratch)

    p = subs.add_parser(
        "ownership",
        help="adopt existing reel timelines by exact Resolve id; "
             "changes the local ownership ledger, never the timelines")
    p.add_argument("project", help="Ren project slug or project directory")
    p.add_argument("--who", required=True,
                   help="person authorizing this one-time adoption")
    p.set_defaults(func=cmd_ownership_adopt)

    p = subs.add_parser("audio", help="audio tracks: enable state and "
                                      "clip counts (read-only); isolate "
                                      "writes voice isolation")
    _add_scope(p, "audio")
    p.add_argument("--full", action="store_true",
                   help="add sub-type, lock and voice-isolation columns")
    p.set_defaults(func=cmd_audio)
    asubs = p.add_subparsers(dest="audio_command")
    q = asubs.add_parser("isolate", help="set a track's voice "
                                        "isolation, verified by "
                                        "re-read; --apply writes")
    _add_scope(q, "isolate")
    q.add_argument("--track", default=None, type=int,
                   help="audio track number from 1 (required)")
    q.add_argument("--amount", default=60, type=int,
                   help="isolation amount 0..100 (default 60)")
    q.add_argument("--disable", action="store_true",
                   help="turn isolation off instead")
    q.add_argument("--apply", action="store_true",
                   help="write under the Resolve lease with the "
                        "cursor asserted (default is a dry-run plan)")
    q.add_argument("--project-folder", default="",
                   help="pipeline project folder: with --apply, record "
                        "the isolation as an edit-ledger row so the "
                        "next build replays it instead of painting over "
                        "it (without this the write acts but is not "
                        "carried)")
    q.add_argument("--reel", default="",
                   help="ledger scope: the reel timeline name (default "
                        "is the timeline just written)")
    q.add_argument("--stated-by", default="captain",
                   choices=["captain", "requester", "model"],
                   help="who stated this value (the ledger taste seam)")
    q.add_argument("--reason", default="",
                   help="whose words decided this, for the ledger row")
    q.set_defaults(func=cmd_audio_isolate)

    p = subs.add_parser("multicam", help="multicam clips from pool "
                                       "clips; build --apply writes, "
                                       "sync plans only")
    msubs = p.add_subparsers(dest="multicam_command", required=True)
    q = msubs.add_parser("build", help="build a multicam clip, "
                                      "verified by the pool listing; "
                                      "--apply writes")
    q.add_argument("--project", default="",
                   help="expect this Resolve project open")
    q.add_argument("--clip", default=[], action="append",
                   help="source clip name, exact (repeatable, "
                        "at least two)")
    q.add_argument("--bin", default="",
                   help="scope the clip search to bin path A/B/C")
    q.add_argument("--name", default="",
                   help="the multicam clip's name (required: the "
                        "read-back is this name listing)")
    q.add_argument("--sync", default="timecode",
                   help="audio|timecode|in|out|marker (default "
                        "timecode)")
    q.add_argument("--apply", action="store_true",
                   help="build under the Resolve lease (default is a "
                        "dry-run plan)")
    q.set_defaults(func=cmd_multicam_build)
    q = msubs.add_parser("sync", help="plan an AutoSyncAudio pass "
                                    "(--apply refused: no measured "
                                    "read-back yet)")
    q.add_argument("--project", default="",
                   help="expect this Resolve project open")
    q.add_argument("--clip", default=[], action="append",
                   help="clip name, exact (repeatable: video plus "
                        "audio, at least two)")
    q.add_argument("--bin", default="",
                   help="scope the clip search to bin path A/B/C")
    q.add_argument("--mode", default="waveform",
                   help="waveform|timecode (default waveform)")
    q.add_argument("--apply", action="store_true",
                   help="refused with the missing read-back named")
    q.set_defaults(func=cmd_multicam_sync)

    p = subs.add_parser("color", help="node LUTs and color groups, "
                                     "verified by re-read; --apply "
                                     "writes")
    csubs = p.add_subparsers(dest="color_command", required=True)
    q = csubs.add_parser("lut", help="set a node LUT on one item, "
                                    "verified by re-read; --apply "
                                    "writes")
    _add_scope(q, "lut")
    q.add_argument("--track", required=True,
                   help="video1, audio2, ...")
    q.add_argument("--index", required=True, type=int,
                   help="position on the track, 0-based")
    q.add_argument("--lut", default="",
                   help="Resolve LUT path, e.g. \"Film Looks/...\" "
                        "(required)")
    q.add_argument("--node", default=1, type=int,
                   help="node index from 1 (default 1)")
    q.add_argument("--apply", action="store_true",
                   help="write under the Resolve lease with the "
                        "cursor asserted (default is a dry-run plan)")
    q.add_argument("--project-folder", default="",
                   help="pipeline project folder: with --apply, record "
                        "the LUT as an edit-ledger row so the next "
                        "build replays it instead of painting over it "
                        "(without this the write acts but is not "
                        "carried)")
    q.add_argument("--anchor", default="",
                   help="words the graded clip speaks: the ledger "
                        "anchor, which is what survives a rebuild "
                        "(required to ledger a LUT)")
    q.add_argument("--reel", default="",
                   help="ledger scope: the reel timeline name (default "
                        "is the timeline just written)")
    q.add_argument("--stated-by", default="captain",
                   choices=["captain", "requester", "model"],
                   help="who stated this value (the ledger taste seam)")
    q.add_argument("--reason", default="",
                   help="whose words decided this, for the ledger row")
    q.set_defaults(func=cmd_color_lut)
    q = csubs.add_parser("group", help="assign one item to a color "
                                      "group, verified by re-read; "
                                      "--apply writes")
    _add_scope(q, "group")
    q.add_argument("--track", required=True,
                   help="video1, audio2, ...")
    q.add_argument("--index", required=True, type=int,
                   help="position on the track, 0-based")
    q.add_argument("--group", default="",
                   help="group name, exact (required)")
    q.add_argument("--create", action="store_true",
                   help="create the group first (else it must exist)")
    q.add_argument("--apply", action="store_true",
                   help="write under the Resolve lease with the "
                        "cursor asserted (default is a dry-run plan)")
    q.set_defaults(func=cmd_color_group)

    p = subs.add_parser("sense", help="Resolve's own AI beside Ren's - "
                                     "each judged by read-back, never "
                                     "wired into pipeline decisions")
    ssubs = p.add_subparsers(dest="sense_command", required=True)

    def _sense_pool(q, what: str) -> None:
        q.add_argument("--project", default="",
                       help="expect this Resolve project open")
        q.add_argument("--clip", required=True,
                       help="pool clip name, exact (--bin "
                            "disambiguates)")
        q.add_argument("--bin", default="",
                       help="scope the clip search to bin path A/B/C")
        q.add_argument("--apply", action="store_true",
                       help=f"run the analysis under the Resolve lease "
                       f"(default reads what {what} already holds)")

    def _sense_item(q) -> None:
        _add_scope(q, "sense")
        q.add_argument("--track", required=True,
                       help="video1, audio2, ...")
        q.add_argument("--index", required=True, type=int,
                       help="position on the track, 0-based")
        q.add_argument("--apply", action="store_true",
                       help="write under the Resolve lease with the "
                            "cursor asserted (default is a dry-run "
                            "plan)")
        q.add_argument("--project-folder", default="",
                       help="pipeline project folder: with --apply, "
                            "journal the write through the undo journal "
                            "so `ren undo` can reverse it (without this "
                            "the write acts but is not undone)")

    q = ssubs.add_parser("transcribe", help="Resolve's transcription "
                                            "with speakers, words with "
                                            "timing; --apply starts it")
    _sense_pool(q, "Resolve")
    q.add_argument("--wait", default=None, type=float,
                   help="seconds to wait for words after --apply "
                        "(default 240; 0 reports what Resolve holds "
                        "now)")
    q.add_argument("--full", action="store_true",
                   help="show complete segment text instead of the "
                        "preview")
    q.set_defaults(func=cmd_sense_transcribe)

    q = ssubs.add_parser("cuts", help="scene cuts along a timeline, "
                                      "as cut frames; --apply detects")
    _add_scope(q, "cuts")
    q.add_argument("--apply", action="store_true",
                   help="detect under the Resolve lease with the "
                        "cursor asserted (default lists carriers)")
    q.set_defaults(func=cmd_sense_cuts)

    q = ssubs.add_parser("classify", help="audio classes for a pool "
                                          "clip; --apply runs it")
    _sense_pool(q, "Resolve")
    q.set_defaults(func=cmd_sense_classify)

    q = ssubs.add_parser("intellisearch", help="IntelliSearch analysis "
                                               "for a pool clip; "
                                               "--apply runs it")
    _sense_pool(q, "Resolve")
    q.add_argument("--faces", action="store_true",
                   help="identify faces as well")
    q.add_argument("--better", action="store_true",
                   help="Better mode (default Faster)")
    q.set_defaults(func=cmd_sense_intellisearch)

    q = ssubs.add_parser("reframe", help="Smart Reframe one item, "
                                         "judged by its transform; "
                                         "--apply writes")
    _sense_item(q)
    q.set_defaults(func=cmd_sense_reframe)

    q = ssubs.add_parser("mask", help="Magic Mask one item, judged by "
                                      "its nodes; --apply writes")
    _sense_item(q)
    q.add_argument("--mode", default="F",
                   help="F|B|BI: forward, backward, bidirectional "
                        "(default F)")
    q.add_argument("--source-frame-range", default=None,
                   help="inclusive START:END source frames; defaults to "
                        "the selected item's measured trim, capped at "
                        "120 frames")
    q.set_defaults(func=cmd_sense_mask)

    q = ssubs.add_parser("switch", help="multicam Smart Switch one "
                                        "item; --apply writes")
    _sense_item(q)
    q.add_argument("--min-edit", default=None, type=float,
                   help="minimum edit duration in seconds (default "
                        "1.0)")
    q.set_defaults(func=cmd_sense_switch)

    p = subs.add_parser("pool", help="every clip in the media pool, "
                                    "with the bin it sits in")
    p.add_argument("--project", default="",
                   help="expect this Resolve project open")
    p.add_argument("--bin", default="",
                   help="bin path A/B/C from the pool root (or a bare "
                        "positional); omit for the whole pool")
    p.set_defaults(func=cmd_pool)

    p = subs.add_parser("renders", help="the render queue: which "
                                       "timeline, how far, what failed")
    p.add_argument("--project", default="",
                   help="expect this Resolve project open")
    p.add_argument("--full", action="store_true",
                   help="add the raw localized status and output "
                        "path columns")
    p.set_defaults(func=cmd_renders)

    p = subs.add_parser(
        "run",
        help="run a custom script; --purpose records it in Ren",
        description=(
            "Run a caller script with ready Resolve names in scope. "
            "A short --purpose writes the exact script and outcome to "
            "Ren's project or user record and makes recurring work "
            "eligible for explicit workflow promotion. A script without "
            "--purpose remains available in developer shells with "
            "RESOLVE_AXI_ALLOW_RUN=1. The script runs with "
            "exactly these names in scope - resolve, manager, project, "
            "project_name, timeline, timeline_name, is_current, "
            "timeline_names, by_index, read_notes, open_project - and "
            "whatever it "
            "leaves in `result` renders as TOON rows under the same "
            "truncation discipline as every other command. A list of "
            "dicts renders as a typed table; --full and --json escape "
            "the truncation. Without --unsafe the script is AST-scanned "
            "for Resolve writers and refused when one appears. Recorded "
            "scripts take the exclusive lease; writes also require "
            "--unsafe, a per-run .drt backup, and inventory comparison. "
            "--borrow (with --unsafe) is only for the developer path and saves "
            "and restores the captain's project around a project-"
            "switching one-off; inside, switch with open_project(name), "
            "which settles and reads back. Submitted as a broker job "
            "(`ren resolved submit resolve_axi '{\"argv\": [\"run\", "
            "...]}'`), the same script runs on the broker's connection "
            "instead of opening a second scripting client."))
    _add_scope(p, "run")
    p.add_argument("script_pos", nargs="?", default="",
                   help="the script itself (positional); sets `result = ...`")
    p.add_argument("--script", default="",
                   help="inline script; set `result = ...`")
    p.add_argument("--file", default="",
                   help="read the script from this file")
    p.add_argument("--purpose", default="",
                   help="short reason; records the script and its outcome")
    p.add_argument("--scope", choices=("auto", "project", "user"),
                   default="auto",
                   help="project when a Ren project is found, otherwise user")
    p.add_argument("--project-dir", default="",
                   help="Ren project folder for records and write backups")
    p.add_argument("--full", action="store_true",
                   help="no per-cell truncation")
    p.add_argument("--json", action="store_true",
                   help="emit result as JSON instead of TOON")
    p.add_argument("--unsafe", action="store_true",
                   help="allow Resolve writers; holds the exclusive "
                        "lease and reports cursor before/after")
    p.add_argument("--borrow", action="store_true",
                   help="save and restore the captain's project around "
                        "a project-switching one-off (needs --unsafe; "
                        "omit --project/--timeline and switch inside "
                        "with open_project(name))")
    p.add_argument("--acknowledge-copy-grades", action="store_true",
                   help="the script names CopyGrades out loud: it "
                        "replaces the target's whole grade with no "
                        "version to go back to")
    p.set_defaults(func=cmd_run)

    p = subs.add_parser("workflow", help="review and run saved custom scripts")
    workflow_subs = p.add_subparsers(dest="workflow_command", required=True)
    for command, handler, help_text in (
            ("list", cmd_workflow_list, "list promoted named workflows"),
            ("candidates", cmd_workflow_candidates,
             "show recurring scripts that could become workflows")):
        q = workflow_subs.add_parser(command, help=help_text)
        q.add_argument("--scope", choices=("auto", "project", "user"),
                       default="auto")
        q.add_argument("--project-dir", default="")
        q.set_defaults(func=handler)

    q = workflow_subs.add_parser("promote",
                                 help="promote one candidate run by choice")
    q.add_argument("candidate_id")
    q.add_argument("run_id")
    q.add_argument("name")
    q.add_argument("--scope", choices=("auto", "project", "user"),
                   default="auto")
    q.add_argument("--project-dir", default="")
    q.set_defaults(func=cmd_workflow_promote)

    q = workflow_subs.add_parser("run", help="execute a saved workflow by name")
    q.add_argument("workflow_name")
    _add_scope(q, "run")
    q.add_argument("--scope", choices=("auto", "project", "user"),
                   default="auto")
    q.add_argument("--project-dir", default="")
    q.add_argument("--full", action="store_true")
    q.add_argument("--json", action="store_true")
    q.add_argument("--unsafe", action="store_true",
                   help="allow writes after creating a .drt backup")
    q.add_argument("--acknowledge-copy-grades", action="store_true")
    q.set_defaults(func=cmd_run, script_pos="", script="", file="",
                   purpose="")

    p = subs.add_parser("setup", help="session integrations")
    ssubs = p.add_subparsers(dest="setup_command", required=True)
    ph = ssubs.add_parser("hooks", help="install the opt-in session hook")
    ph.add_argument("--app", default="",
                    help="opencode|claude-code|codex")
    ph.add_argument("--write", action="store_true",
                    help="write the hook (default only shows the plan)")
    ph.set_defaults(func=cmd_setup_hooks)

    p = subs.add_parser("update", help="report the installed version")
    p.set_defaults(func=cmd_update)
    return parser


#: Commands whose first job is one timeline: a bare name there is the
#: obvious shape (`markers "Reel 29"`), so it is accepted.
_BARE_TIMELINE_COMMANDS = ("markers", "items", "captions", "frames",
                           "fusion")

#: Commands whose first job is one pool bin: a bare path there is the
#: obvious shape (`pool "Footage/Day 1"`), so it is accepted onto
#: `--bin` the same way a bare reel routes onto `--timeline`.
_BARE_BIN_COMMANDS = ("pool",)

#: First-position tokens these commands own: `markers snapshot ...`,
#: `markers restore ...` and `markers reply ...` must keep routing to
#: their subcommand.
_OWN_SUBCOMMANDS = ("snapshot", "restore", "reply")


def _normalize(argv: list) -> list:
    """Rewrite a bare timeline name onto `--timeline`, before parsing.

    `markers "Reel 29 - x"` fails at argparse's subcommand dispatch
    (the name collides with the `snapshot`/`restore` position), and
    `items "Reel 29 - x"` fails as an unrecognized argument - three
    attempts plus a `--help` round trip before the first real read,
    which is the cost this tool removes. The rewrite is deliberately
    narrow: only the token right after the command, only when it is
    not a flag and not one of the command's own subcommands. Flags
    (`--timeline`, `--plane`, `--full`) keep working exactly as before.
    """
    if argv and argv[0] == "--submit":
        return ["--submit"] + _normalize(argv[1:])
    if (len(argv) >= 2 and argv[0] in _BARE_TIMELINE_COMMANDS
            and not argv[1].startswith("-")
            and argv[1] not in _OWN_SUBCOMMANDS):
        return [argv[0], "--timeline", argv[1]] + argv[2:]
    if (len(argv) >= 2 and argv[0] in _BARE_BIN_COMMANDS
            and not argv[1].startswith("-")):
        return [argv[0], "--bin", argv[1]] + argv[2:]
    return argv


def _through_broker(client, argv: list, keep_the_slot: bool = False) -> int:
    """Run this command line inside ren-resolved, and print what it did.

    Where the broker is serving it owns Resolve, so the command queues
    with every other Resolve job, and an identical read already in
    flight from another agent is joined rather than repeated.

    `keep_the_slot` is the scratch-lifecycle path: the job moves the
    machine into (or out of) a scratch project, so it must not START
    while another writer holds the scratch-origin slot. A job that
    starts against a held slot waits for it INSIDE the broker's turn,
    and a wait inside a turn holds the one Resolve against every other
    lane (measured 2026-10-21: three `project scratch open` jobs held
    the machine's turn for the whole 900 s lease timeout each). So
    while the job is still QUEUED the caller watches the slot, and a
    slot another live writer has taken CANCELS the queued job - the
    broker drops a queued job without running it - and the caller goes
    back to waiting for the slot outside the turn.
    """
    from library.tools.resolve_lock import default_owner
    submitted = None
    try:
        submitted = client.submit(
            "resolve_axi", {"argv": list(argv), "cwd": os.getcwd(),
                            "caller": default_owner()},
            owner=default_owner())
        job = client.result(submitted["id"], wait=0.0)
        while job.get("state") in ("queued", "running"):
            watching = keep_the_slot and job.get("state") == "queued"
            if watching and _slot_taken():
                client.cancel(submitted["id"])
                return _slot_held_code()
            job = client.result(submitted["id"],
                                wait=5.0 if watching else 60.0)
    except (ConnectionError, client.BrokerError) as exc:
        # The refusal names the JOB and the broker's last error: a lane
        # cannot tell a transient refusal from a broker that died, and
        # one line that says only "closed the connection" told it
        # neither.
        job_id = str((submitted or {}).get("id") or "")
        named = f" (job {job_id})" if job_id else ""
        return fail(f"ren-resolved refused this command{named}: {exc}",
                    "ren resolved list, then the broker's log beside its "
                    "socket")
    if job["state"] != "done":
        return fail(f"ren-resolved {job['state']} job {job['id']}: "
                    f"{job['error']}", f"ren resolved result {job['id']}")
    result = job.get("result")
    # A job the broker refused carries its refusal, not the command's
    # output. Reading `result["stdout"]` anyway raised KeyError and
    # crashed the CLI, so the reason the command was refused never
    # reached the operator.
    if not isinstance(result, dict) or "stdout" not in result:
        return fail(
            f"ren-resolved job {job['id']} finished without command output: "
            f"{job.get('error') or result}",
            f"ren resolved result {job['id']}")
    sys.stdout.write(result["stdout"])
    sys.stderr.write(result.get("stderr", ""))
    return result.get("exit_code", 1)


def _submit_through_broker(client, argv: list) -> int:
    """Queue one Resolve command and return its receipt key immediately."""
    from library.tools.resolve_lock import default_owner

    job_argv = list(argv)
    if job_argv and job_argv[0] == "--submit":
        job_argv.pop(0)
    try:
        submitted = client.submit(
            "resolve_axi", {"argv": job_argv, "cwd": os.getcwd(),
                            "caller": default_owner()},
            owner=default_owner())
    except (ConnectionError, client.BrokerError) as exc:
        return fail(f"cannot submit to ren-resolved: {exc}",
                    "start or restore the broker with `ren resolved serve`, "
                    "then re-run this command")
    sys.stdout.write(json.dumps(submitted) + "\n")
    return 0


#: The scratch lifecycle verbs that need the machine's scratch-origin
#: slot: each of them moves the machine into (or out of) a Ren-owned
#: scratch project, and the slot is the route back.
_SCRATCH_SLOT_COMMANDS = ("create", "import", "open", "delete")


def _scratch_slot_needed(name: str, args) -> bool:
    """Whether this command has to wait for the scratch-origin slot."""
    return (name == "cmd_project_scratch"
            and getattr(args, "scratch_command", "") in _SCRATCH_SLOT_COMMANDS)


def _slot_taken() -> bool:
    """Whether another live writer holds the machine's scratch-origin slot.

    The ledger is a file, so this reads it without touching Resolve and
    without holding anything.
    """
    from library.tools import resolve_scratch_projects as scratch
    try:
        return scratch.slot_holder() is not None
    except Exception:  # noqa: BLE001 - an unreadable ledger is not a block
        return False


def _slot_held_code() -> int:
    """The exit code a held slot answers with."""
    from library.tools import resolve_scratch_projects as scratch
    return scratch.SLOT_HELD_EXIT_CODE


def _holds_the_broker_turn() -> bool:
    """Whether this process already HOLDS the one Resolve's turn.

    Inside the broker's executor the turn is held: the job is running
    now and every other lane waits behind it. A session holds it too,
    for the whole operation.

    And a wait for the scratch-origin slot in EITHER of those places
    holds the very turn the holder of the slot needs: the holder is
    inside Resolve and needs this turn to restore the origin and
    release the slot (measured 2026-10-21: three `project scratch
    open` jobs held the machine's turn for the whole 900 s lease
    timeout each, and every other lane waited behind them). So the
    wait belongs to the caller, outside the turn, and the command
    inside the turn refuses FAST - with its own exit code, which the
    caller retries on.
    """
    from library.tools.resolved import client
    if client.in_broker():
        return True
    from library.tools import resolve_session
    try:
        return bool(resolve_session.claim_held())
    except Exception:  # noqa: BLE001 - an unreadable claim is not a block
        return False


def _wait_for_the_scratch_slot(deadline: float) -> dict:
    """Wait for the machine's scratch-origin slot, HERE.

    The wait belongs to the caller: the writer that holds the slot is
    inside Resolve and needs the broker's turn to restore the origin
    and release it. So the wait runs where no turn is held - before
    the command is queued - and never inside the command, which the
    broker runs while holding the turn (measured 2026-10-21: three
    `project scratch open` jobs held the machine's turn for the whole
    900 s lease timeout each, and every other lane waited behind them).

    The answer says whether the slot is free. A slot that is still held
    when the wait ends is NOT run against: the command would queue
    inside the turn again, which is the defect.
    """
    from library.tools import resolve_scratch_projects as scratch
    return scratch.wait_for_origin_slot(
        timeout=max(0.0, deadline - _time.time()))


def _run_with_the_scratch_slot(run, deadline: float) -> int:
    """Run `run()` once the scratch-origin slot is free, and retry once.

    The command refuses FAST (with its own exit code) when the slot is
    held, so the retry happens out here, where the wait costs the
    machine nothing.
    """
    from library.tools import resolve_scratch_projects as scratch
    while True:
        state = _wait_for_the_scratch_slot(deadline)
        if state.get("timed_out"):
            # The slot is still somebody else's. The command is NOT
            # run: it would wait inside the turn, which is the defect
            # this whole path exists to remove.
            holder = state.get("holder") or "another writer"
            emit([f"error: the scratch origin is held by {holder}; it is "
                  "still held, so nothing was moved",
                  f"help: {TOOL} project scratch --help"])
            return scratch.SLOT_HELD_EXIT_CODE
        code = run()
        if code != scratch.SLOT_HELD_EXIT_CODE:
            return code
        if _time.time() >= deadline:
            return code


def _main_impl(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 1 and argv[0] in ("-v", "-V", "--version"):
        sys.stdout.write(f"{VERSION}\n")
        return 0
    parser = build_parser()
    if not argv:
        args = argparse.Namespace()
        func = cmd_home
    else:
        args = parser.parse_args(_normalize(argv))
        func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stdout)
        return 2
    submit = bool(getattr(args, "submit", False))
    if submit and getattr(func, "__name__", "") in (
            _LOCAL_COMMANDS | _NO_BROKER_COMMANDS):
        return fail("--submit queues a job on the broker, and this command "
                    "runs locally", "remove --submit")
    from library.tools.resolve_lock import ResolveBusy
    name = getattr(func, "__name__", "")
    needs_resolve = (name not in _LOCAL_COMMANDS
                     and name not in _NO_BROKER_COMMANDS
                     and not (
                         name in _SHADOW_READ_COMMANDS
                         and not getattr(args, "refresh_live", False)))
    scratch_slot = _scratch_slot_needed(name, args)
    deadline = 0.0
    if scratch_slot:
        from library.tools import resolve_scratch_projects as scratch
        deadline = _time.time() + scratch.slot_timeout()
    if needs_resolve:
        from library.tools.resolved import client
        client.ensure()
        if submit and not client.in_broker():
            return _submit_through_broker(client, argv)
        if client.serving():
            if scratch_slot and not _holds_the_broker_turn():
                return _run_with_the_scratch_slot(
                    lambda: _through_broker(client, argv,
                                            keep_the_slot=True),
                    deadline)
            return _through_broker(client, argv)
    try:
        if scratch_slot and not _holds_the_broker_turn():
            return _run_with_the_scratch_slot(
                lambda: _dispatch(func, args), deadline)
        return _dispatch(func, args)
    except AxiError as exc:
        return fail(str(exc), exc.fix, exc.blocker, exc.next_action)
    except ResolveBusy as exc:
        return fail(str(exc),
                    "wait for the named Resolve holder to finish, "
                    "then re-run this command")


def main(argv=None) -> int:
    """CLI entry point over the shared AXI execution core."""
    from library.tools.resolve_axi_core import invoke_cli
    result = invoke_cli(argv)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.exit_code


def cli_boundary(main_impl, argv=None) -> int:
    """The ONE place this verb renders a refusal.

    A typed refusal leaves as exit 4 with its what/why/fix; an
    unexpected error keeps its traceback and exit 1, because a fix
    invented for a bug would be a lie.

    AXI-42: a routing refusal is a `RenRefusal`, so it lands here as a
    REFUSAL with a next action. It used to be a bare `RuntimeError`, so
    every routing refusal that escaped a command reached the caller as
    an unexpected error - a traceback and exit 1 - and the qualification
    gap that caused it was never stated.
    """
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)

    try:
        return int(main_impl(argv))
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        return REFUSAL_EXIT_CODE
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        return report_unexpected(exc)


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line
    # (LE-03). The verb's own `fail()` vocabulary (exit 1) is
    # untouched: only a `RenRefusal` escaping `main` and an uncaught
    # error land here.
    _HERE = os.path.abspath(__file__)
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    sys.exit(cli_boundary(main))
