"""The runner of the `reels` PROCESS: its capabilities, in requirement order.

`edit_video` has had a runner of its own (`run_pipeline.py`) from the
start; `reels` ran as a loop inside `manage_project.cmd_build_reels`.
This is that loop, moved beside the process it runs, so each process
owns its runner and `build-reels` (and `ren build`, which execs it) is
a CALLER, holding no path of its own.

It used to be `reel_build.rebuild_reels_in_project` called directly,
and that was the only way reels could be made: you had to know which
subcommand to run, and nothing checked a single prerequisite before
connecting to Resolve and deleting the existing reel timelines. The
captain's stop condition was that reels be created *"using the pipeline
and not any standalone scripts"*, and a command that reaches past the
pipeline into a tool is that gap however thin it is.

So the run is the process's CAPABILITIES (`capabilities.run_order`):
each runs after the capabilities producing what it requires, registry
order breaking ties, and each runs through the operation registry,
which checks its DERIVED requirements first and REFUSES naming what is
missing. No build path lives here - the operation resolves to the
step's own body, and the step's body calls `reel_build`. One
implementation. Each result is recorded under its capability id
(`capability_outputs`); nothing here is keyed by a DAG node.

State persistence is borrowed, not copied: `save_pipeline_state` is the
edit_video runner's own writer because it owns the backup rule
(AGENTS.md 8), and that rule is not process-specific.
"""

from __future__ import annotations

import contextlib
import copy
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def add_arguments(parser) -> None:
    """The run's options, registered on the caller's parser.

    One definition, so `build-reels --help` and this runner cannot
    disagree about what a flag means.
    """
    parser.add_argument(
        "project", help="The project to rebuild reels for")
    parser.add_argument(
        "--skip-captions", action="store_true", help="Skip rendering subtitles (saves CPU)")
    parser.add_argument(
        "--only-reel", type=int, action="append", default=[], metavar="N",
        help="Build only this reel number; repeatable. Default: every "
             "approved moment. The build deletes only the timelines it is "
             "about to place, so this touches one timeline.")
    parser.add_argument(
        "--name-suffix", default="", metavar="TEXT",
        help="Label the temporary Resolve timeline and its caption files. "
             "After conformance passes, the build promotes to the plan's "
             "approved name; a failed verification leaves it untouched. "
             "Default: use the plan name for the temporary timeline too.")
    parser.add_argument(
        "--allow-drop", dest="allow_drop", action="append", default=[],
        metavar="SPEC",
        help="A row the replace guard may let shrink, by ROW never by "
             "blanket (issue #925): `ROW` (e.g. `video:Semantic`) for "
             "every reel this run promotes, or `FINAL::ROW` for one reel "
             "only. Repeatable. Absent means any row that loses items, "
             "or vanishes, refuses the promotion.")
    parser.add_argument(
        "--supersede", dest="supersede", action="append", default=[],
        metavar="REEL",
        help="A reel whose durable captain SIGN-OFF this build may "
             "replace (library/tools/reel_signoff.py). Repeatable. "
             "Absent means a promotion over a signed-off reel refuses "
             "by name and prints this flag. The sign-off is recorded "
             "as superseded, never deleted, and the timeline it "
             "covered is retired to the archive bin.")
    parser.add_argument(
        "--accept-editor-changes", action="append", default=[],
        metavar="REEL",
        help="Explicitly supersede detected manual edits on this reel. "
             "An uncarried edit otherwise refuses promotion with its "
             "source and record ranges. Repeatable.")
    parser.add_argument(
        "--retain", dest="retain", action="append", default=[],
        metavar="REEL",
        help="A reel whose superseded generation the promotion may "
             "RETIRE into the archive rather than delete "
             "(library/tools/reel_retirement.py). Repeatable. Absent - "
             "the default - means one timeline per reel and an empty "
             "archive; a reel carrying a sign-off retires whatever "
             "this says.")
    parser.add_argument(
        "--rebuild-all", dest="rebuild_all", action="store_true",
        help="Place every reel this run names, whatever the state "
             "says. By default a reel nothing changed about is LEFT "
             "ALONE (library/tools/reel_rebuild_need.py): a reel's "
             "Resolve pass is 19.4-67.1s of which the Fusion comp "
             "pass is fixed overhead, so placing an unchanged reel "
             "again costs that and produces the same frames. The "
             "decision is printed per reel with its reason either "
             "way. Use this to re-place onto drift-free state, or to "
             "measure what the pass costs.")
    parser.add_argument(
        "--placement", dest="placement_mode", default="auto",
        choices=("append", "otio", "auto"),
        help="How each reel timeline is placed. `append` places item "
             "by item. `otio` lands the whole timeline with "
             "ONE OTIO import and restores what the import drops "
             "(library/tools/reel_otio_placement.py): measured 1.26s of "
             "Resolve hold against 13.35s on Reel 09. A reel carrying an "
             "image-sequence caption, a transition element or a program "
             "channel other than 1 refuses by name. `auto` (the default) runs a FREE "
             "compatibility check and uses append for those shapes, or "
             "when the project resolution differs from the reel. Only "
             "explicit `otio` opts into the project-default resolution "
             "switch for a mismatch.")
    parser.add_argument(
        "--json", dest="json", action="store_true",
        help="Print the end-of-build per-reel digest as JSON - the last "
             "stdout object - instead of one line per reel. The digest "
             "is the LE-07 rows for the reels this build touched: "
             "outcome, final name, verify verdict and owed answers, "
             "plus the refusal code if any and the full log's path.")


def run(project_folder: str, args) -> int:
    """Run every capability of the reels process. 0, or 4 on a refusal.

    `args` carries the options `add_arguments` registers. A refusal
    stops the run where it happened, names the operation and what it
    refused on, and makes no closing commit: nothing after it ran.
    4 is the refusal code (`library/tools/ren_refusal.py`).

    The run always ends with the per-reel digest on stdout - the
    LE-07 rows for the reels this build touched (outcome, final
    name, verify verdict, owed answers), the refusal code if any and
    the full log's path. `--json` prints the same as the last stdout
    object, so an agent parses one shape instead of mining the run's
    own output the way the 50,863-byte log plus 4 JSON reads did.
    The digest never fails the build: a reader that refuses correct
    output is worse than no reader (AGENTS.md 10.4).
    """
    code = _run(project_folder, args)
    try:
        print_digest(project_folder, args, code)
    except Exception as exc:  # noqa: BLE001 - the digest never fails a build
        print(f"build digest unavailable ({exc!r}) - the reels this "
              f"run touched are in `ren reels {project_folder}`",
              file=sys.stderr)
    return code


def _run(project_folder: str, args) -> int:
    # The format gate, before any capability reads project state: a
    # project outside the supported range refuses here, and an older
    # supported one migrates with a backup.  This runner takes a raw
    # folder, never `get_project`, so it asks for the gate itself.
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result)
    try:
        from library.tools import project_format
        project_format.ensure_project_format(project_folder)
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        return REFUSAL_EXIT_CODE
    # The assignment this build belongs to (library/tools/
    # assignment_spine.py): D2 asks before approved work is replaced,
    # and the state moves only when the build completes - a refusal or
    # a crash leaves the assignment where it was.
    from library.tools import assignment_spine
    try:
        act = assignment_spine.begin_build(
            project_folder, args.only_reel or None)
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        return REFUSAL_EXIT_CODE
    try:
        code = _run_capabilities(project_folder, args)
    except KeyboardInterrupt:
        assignment_spine.end_act(act, ok=False, status="interrupted")
        raise
    except BaseException as crashed:
        assignment_spine.end_act(act, ok=False, status="failed",
                                 detail=repr(crashed))
        raise
    assignment_spine.end_act(
        act, ok=code == 0, detail="" if code == 0 else f"exit {code}")
    return code


def _run_capabilities(project_folder: str, args) -> int:
    """The capability loop of `run`, inside its assignment act."""
    from library.tools.ren_refusal import REFUSAL_EXIT_CODE
    # ONE HOLD for the whole run: build, ask, the gate and promotion.
    # `rebuild_reels_in_project` holds the exclusive instance lease for
    # its own body, and that body ends where `reel.build` returns -
    # the gate (`reel.verify`) and the promotion inside it are later
    # capabilities of this loop. Measured 2026-10-05: a 6-reel build's
    # hold released at 20:24:01.307, a queued caption smoke was granted
    # the instance 2ms later, and the verify that followed read the
    # master and all six stagings changed under it ("timelines were
    # MODIFIED during verification", the cursor on Reel 01 where the
    # gate's first read had found the master). The inner holds are
    # reentrant no-ops under this one, and a child inherits it.
    from library.tools import (capabilities, event_log, operations, processes,
                               provenance, resolve_lock)
    with contextlib.ExitStack() as held:
        # The run's lease events belong to this project: without this
        # the instance lease's hook never fires here, the same gap the
        # edit runner closed per step. Restored on every exit - a
        # refusal returns through the stack like any other.
        held.enter_context(event_log.scoped_log(project_folder))
        try:
            held.enter_context(
                resolve_lock.resolve_lease("build reels", exclusive=True))
        except resolve_lock.ResolveBusy as busy:
            from library.tools.ren_refusal import emit_refusal_result
            print(busy.render(), file=sys.stderr)
            emit_refusal_result(busy)
            return REFUSAL_EXIT_CODE
        for spec in capabilities.run_order(processes.REELS):
            op = operations.get(spec.id)
            if op.caller_supplied:
                # Caller-supplied operations take arguments only a caller
                # supplies - the touchup's change spec, the stills grab's
                # reel label and frames - handed in by the fix or gate that
                # computed them. This loop is not such a caller, so it does
                # not drive them: as coded it walked every owned op, and on a
                # ready project `reel.build` completed and the loop then
                # REFUSED at `reel.touchup` (`spec` unbound) before
                # `reel.ask` ever ran. Skipped, never executed, and nothing
                # is recorded for one. (The dry run's old-path walk in
                # `library/tools/ren_dry_run.py` reads the same flag.)
                print(f"{op.name}: skipped (caller-supplied)", file=sys.stderr)
                continue
            # The execution receipt, keyed by capability id: before this a
            # reel build wrote its files and provenance recorded nothing.
            from library.tools.ren_refusal import RenRefusal
            try:
                with provenance.observing_operation(project_folder, op.name):
                    result = op.execute(
                        project_folder,
                        skip_captions=args.skip_captions,
                        only_reels=args.only_reel or None,
                        timeline_name_suffix=args.name_suffix,
                        allow_drops=args.allow_drop or None,
                        accept_editor_changes=getattr(
                            args, "accept_editor_changes", None) or None,
                        supersede=args.supersede or None,
                        retain=args.retain or None,
                        rebuild_all=bool(getattr(args, "rebuild_all", False)),
                        placement_mode=getattr(args, "placement_mode", "auto"))
            except RenRefusal as refused:
                # A step-level refusal with a known next step (the replace
                # guard's `--allow-drop` declaration): no traceback, exit 4,
                # nothing after it ran and no closing commit.
                from library.tools.ren_refusal import (
                    REFUSAL_EXIT_CODE, emit_refusal_result)
                print(f"REFUSED: {op.name}", file=sys.stderr)
                print(refused.render(), file=sys.stderr)
                emit_refusal_result(refused)
                return REFUSAL_EXIT_CODE
            if result.refused:
                from library.tools.ren_refusal import REFUSAL_EXIT_CODE
                print(f"REFUSED: {op.name}", file=sys.stderr)
                print(result.error, file=sys.stderr)
                return REFUSAL_EXIT_CODE

            # RECORD the capability's result the way a run records one, so
            # the next capability's gathered inputs can carry it and so the
            # build is readable afterwards by everything that reads the
            # records - the traceback, `status`, the deliverer.
            record_project_output(project_folder, op.name, result.payload,
                                  only_reels=args.only_reel or None)
            print(f"{op.name}: {result.status}", file=sys.stderr)
            report_rebuild_need(result.payload)
            report_reel_verification(result.payload)

    # ══════════════════════════════════════════════════════════════
    # THE CLOSING COMMIT (library/tools/versions/store.py)
    # ══════════════════════════════════════════════════════════════
    # The per-build commit fires INSIDE the promoting capability, and
    # this loop writes that capability's own output to
    # pipeline_data.json AFTER it returns - so the last record could
    # never be in the commit it belongs to. Measured 2026-09-11 on the
    # captain's project: HEAD was "reels build: Reel 13" and the working
    # tree was dirty with exactly the verify record's
    # `reel_verification.organised`, the bin organisation the commit
    # ran too early to see. A store that is dirty after every build
    # teaches a reader to ignore its dirtiness, which is how a hand
    # edit goes missing.
    #
    # So the run closes its own record, here, where the state write
    # it completes lives. `clean` is the ordinary answer once the
    # capability's commit already covered everything.
    commit_run_tail(project_folder)
    return 0


def digest_rows(project_folder: str, only_reels=None) -> list:
    """The LE-07 rows for the reels this build touched, in reel order.

    Read-only: one `reel_state.reels_state` read, filtered to the
    `--only-reel` numbers where the build was scoped. A project with
    no recorded reels digests to `[]`, never a refusal - the closing
    commit above already said what ran.
    """
    from library.tools import reel_state

    try:
        state = reel_state.reels_state(str(project_folder))
        rows = list(state.get("reels") or ())
    except Exception:
        return []
    wanted = _digest_wanted(only_reels)
    if wanted is None:
        return rows
    return [row for row in rows
            if isinstance(row.get("reel"), int) and row["reel"] in wanted]


def _digest_wanted(only_reels) -> set | None:
    """The `--only-reel` numbers as ints, or None for a whole-project run."""
    if not only_reels:
        return None
    if isinstance(only_reels, (str, int)):
        only_reels = [only_reels]
    try:
        return {int(reel) for reel in only_reels}
    except (TypeError, ValueError):
        return None


def build_digest(project_folder: str, only_reels=None,
                 exit_code: int = 0) -> dict:
    """The end-of-build digest as data: one entry per touched reel.

    Per reel: outcome, final name, verify verdict (plus the raw
    verify record), owed model answers and approval. Plus the
    refusal code where the run refused, and the path of the full
    log (`pipeline_output/logs/event_log.jsonl`) for the run's own
    account of itself.
    """
    from library.tools import event_log, reel_state

    reels = []
    for row in digest_rows(project_folder, only_reels):
        if not isinstance(row, dict):
            continue
        reels.append({
            "reel": row.get("reel"),
            "outcome": row.get("outcome"),
            "final": row.get("final") or row.get("timeline_name"),
            "verify": row.get("verify"),
            "verify_verdict": reel_state._verify_word(row.get("verify")),
            "owed_answers": list(row.get("owed_answers") or ()),
            "approval": row.get("approval"),
        })
    try:
        log = str(event_log.log_path(str(project_folder)))
    except Exception:
        log = ""
    return {
        "command": "build",
        "project": str(project_folder),
        "exit": exit_code,
        "refusal_code": exit_code if exit_code else None,
        "log": log,
        "reel_count": len(reels),
        "reels": reels,
    }


def print_digest(project_folder: str, args, exit_code: int) -> None:
    """End the build with the digest on stdout: text, or `--json`.

    Text is one LE-07 line per touched reel plus one closing line
    carrying the exit code and the full log's path. `--json` prints
    the digest as the last stdout object: everything else this run
    prints goes to stderr, so the tail parses. Read-only and never
    Resolve: the digest that needs the machine would defeat its
    own purpose.
    """
    only_reels = getattr(args, "only_reel", None)
    if getattr(args, "json", False):
        print(json.dumps(
            build_digest(project_folder, only_reels,
                         exit_code=exit_code),
            ensure_ascii=False, default=str))
        return
    from library.tools import event_log, reel_state

    for row in digest_rows(project_folder, only_reels):
        if isinstance(row, dict):
            print(reel_state.format_line(row))
    try:
        log = str(event_log.log_path(str(project_folder)))
    except Exception:
        log = "(unknown)"
    touched = len(digest_rows(project_folder, only_reels))
    print(f"build: {touched} reel(s), exit {exit_code}, log {log}")


def record_project_output(project_folder: str, capability_id: str, payload,
                          only_reels=None) -> None:
    """Record one capability's result, preserving concurrent reel-lane writes.

    Read, merged and written under the project-file lock, so two
    `--only-reel` lanes finishing together each keep their own reel's
    entries (`merge_record`).
    """
    from library.tools import capability_outputs
    from library.tools.project_file_lock import lock_project_file
    from library.tools.project_layout import ProjectLayout

    path = ProjectLayout(project_folder).pipeline_data_path
    with lock_project_file(path):
        state = (json.loads(Path(path).read_text(encoding="utf-8"))
                 if Path(path).is_file() else {})
        capability_outputs.migrate(state)
        state["project_folder"] = project_folder
        merge_record(state.setdefault(capability_outputs.KEY, {}),
                     capability_id, copy.deepcopy(payload),
                     only_reels=only_reels)
        _edit_video_runner().save_pipeline_state(project_folder, state)


def merge_record(records: dict, capability_id: str, payload,
                 only_reels=None) -> None:
    """Merge one capability's payload into its recorded output.

    A whole-project run updates the record key by key; a single-reel
    run (`only_reels`) replaces only that reel's entries in each
    per-reel list and map, so concurrent lanes and an earlier
    whole-project record survive it. Non-dict payloads overwrite.
    """
    prior = records.get(capability_id)
    if isinstance(prior, dict) and isinstance(payload, dict):
        wanted = _requested_reel_numbers(only_reels)
        if wanted is None:
            prior.update(payload)
        else:
            records[capability_id] = _merge_payload(
                prior, payload, wanted)
    else:
        records[capability_id] = payload


_PER_REEL_LIST_KEYS = {
    "reel_asks", "skipped_by_exclusion", "skipped_out_of_window",
    "picture_motion", "rebuild_need", "reels_left_alone",
    "timelines_built", "timelines_verified", "supersede", "retain",
    "accept_editor_changes",
    "reels", "stills",
}
_SCOPED_PROJECT_LIST_KEYS = {"pending_promotions", "reels_requested"}
_PER_REEL_MAP_KEYS = {
    # `caption_hashes` is retired from new build records (Lane E3,
    # R2/R9) but stays here so `only_reels` runs still scope records
    # written before the retirement.
    "caption_hashes", "closer_fit", "track_plans", "reel_placements",
    "transition_overlays", "staged_timelines", "allow_drops",
    "staged_timeline_ids", "reels", "notes",
}


def _requested_reel_numbers(only_reels) -> set[int] | None:
    if only_reels is None:
        return None
    if isinstance(only_reels, (str, int)):
        only_reels = [only_reels]
    try:
        return {int(reel) for reel in only_reels}
    except (TypeError, ValueError):
        return None


def _record_reel_number(value):
    import re

    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.match(r"^Reel\s+(\d+)(?:\s|$)", str(value))
        return int(match.group(1)) if match else None


def _row_reel_number(row):
    if isinstance(row, int):
        return row
    if isinstance(row, dict):
        for key in ("number", "reel", "reel_name", "staging", "awaiting"):
            if row.get(key) is not None:
                number = _record_reel_number(row[key])
                if number is not None:
                    return number
    if isinstance(row, str):
        return _record_reel_number(row)
    return None


def _merge_scoped_list(prior: list, incoming: list,
                       wanted: set[int], *, incoming_scope_only=False) -> list:
    retained = [row for row in prior
                if _row_reel_number(row) not in wanted]
    # The operation was scoped before it produced this payload. Preserve
    # unknown rows in the old record, while replacing all rows for the
    # requested reel with the current operation's entries.
    additions = (list(incoming) if not incoming_scope_only else
                 [row for row in incoming
                  if _row_reel_number(row) in wanted])
    return retained + additions


def _merge_scoped_map(prior: dict, incoming: dict,
                      wanted: set[int]) -> dict:
    merged = {
        key: value for key, value in prior.items()
        if _record_reel_number(key) not in wanted
    }
    merged.update({key: value for key, value in incoming.items()
                   if _record_reel_number(key) in wanted})
    return merged


def _merge_payload(prior: dict, incoming: dict,
                        wanted: set[int] | None) -> dict:
    """Merge operation records, replacing only selected reel entries."""
    merged = dict(prior)
    for key, value in incoming.items():
        old = prior.get(key)
        if wanted is not None and key == "coherence_summary":
            # A single-reel run skips this project-wide witness. Preserve
            # the last whole-project reading, or leave the field absent,
            # instead of filing a project-wide "skipped" marker.
            continue
        if (wanted is not None and key == "divergence"
                and isinstance(old, dict) and isinstance(value, dict)):
            merged[key] = _merge_scoped_divergence(old, value, wanted)
        elif (wanted is not None and key == "staged_timeline_ids"
              and isinstance(value, dict)):
            # Identity records authorize promotion of staging containers
            # created by this run. Carrying an older reel's id forward
            # makes a scoped promotion inspect an identity it did not stage.
            # Holds remain durable in staging_holds.json for their own reel.
            merged[key] = {
                name: identity for name, identity in value.items()
                if _record_reel_number(name) in wanted
            }
        elif (wanted is not None and key in _PER_REEL_MAP_KEYS
              and isinstance(old, dict) and isinstance(value, dict)):
            merged[key] = _merge_scoped_map(old, value, wanted)
        elif isinstance(old, dict) and isinstance(value, dict):
            merged[key] = _merge_payload(old, value, wanted)
        elif (wanted is not None and key in _PER_REEL_LIST_KEYS
              and isinstance(old, list) and isinstance(value, list)):
            merged[key] = _merge_scoped_list(
                old, value, wanted, incoming_scope_only=True)
        elif (wanted is not None and key in _SCOPED_PROJECT_LIST_KEYS
              and isinstance(value, list)):
            scoped = _merge_scoped_list(
                old if isinstance(old, list) else [], value, wanted,
                incoming_scope_only=True)
            merged[key] = (sorted(set(scoped))
                           if key == "reels_requested" else scoped)
        else:
            merged[key] = value
    if (wanted is not None
            and isinstance(merged.get("reels"), list)
            and "outstanding_answers" in merged):
        merged["outstanding_answers"] = sum(
            len(row["layers"]) for row in merged["reels"])
    return merged


def _merge_scoped_divergence(prior: dict, incoming: dict,
                             wanted: set[int]) -> dict:
    """Keep each lane's reel rows in the shared divergence record."""
    merged = dict(prior)
    for key, value in incoming.items():
        old = prior.get(key)
        if key in {"reels", "notes"} and isinstance(value, dict):
            merged[key] = _merge_scoped_map(
                old if isinstance(old, dict) else {}, value, wanted)
        elif key in {"divergent", "undetermined"} and isinstance(value, dict):
            categories = dict(old) if isinstance(old, dict) else {}
            for category, reel_names in value.items():
                categories[category] = _merge_scoped_list(
                    categories.get(category, []), reel_names, wanted,
                    incoming_scope_only=True)
            merged[key] = categories
        elif key == "unavailable" and key in prior:
            continue
        else:
            merged[key] = value
    return merged


def commit_run_tail(project_folder: str) -> None:
    """Commit whatever the run's final state write left uncommitted.

    Never raises and never fails the build: a record that breaks a
    build is worse than no record, the rule the per-build hook already
    follows.
    """
    try:
        from library.tools.versions import store
        record = store.commit_build(
            project_folder,
            "reels build: closing record\n\n"
            "The per-build commit runs inside the promoting capability, "
            "before the runner writes that capability's own output to "
            "pipeline_data.json. This is the run closing its own "
            "record so the store is clean when it ends.")
        if record.get("committed"):
            print(f"── Version control: closing commit {record['commit']} "
                  f"({len(record.get('files', []))} file(s)) ──",
                  file=sys.stderr)
        elif record.get("reason") not in ("clean", "no-repo"):
            print(f"  closing version-control commit not made: "
                  f"{record.get('reason', 'unknown')}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"  closing version-control commit failed: {exc!r} - the "
              f"reels are built and unaffected", file=sys.stderr)


def report_rebuild_need(payload) -> None:
    """Say which reels needed a Resolve pass, and WHY - both answers.

    The build's own `rebuild_need` record
    (`library/tools/reel_rebuild_need.py`) is printed here, not only in
    the build's stdout, because the decision not to place a reel is the
    one a reader is most likely to want back afterwards: "why is Reel 23
    not in this round" has to be answerable from the run's own output.
    A record that only ever said what was placed would leave the answer
    to inference.
    """
    if not isinstance(payload, dict):
        return
    build = payload.get("reel_build")
    if not isinstance(build, dict):
        return
    decisions = build.get("rebuild_need")
    if not isinstance(decisions, list) or not decisions:
        return
    left = [d for d in decisions
            if isinstance(d, dict) and d.get("action") == "leave_alone"]
    print(f"  rebuild need: {len(decisions) - len(left)} reel(s) placed, "
          f"{len(left)} left alone", file=sys.stderr)
    for entry in decisions:
        if not isinstance(entry, dict):
            continue
        mark = ("left alone" if entry.get("action") == "leave_alone"
                else "placed")
        print(f"    - {entry.get('reel')}: {mark} - "
              f"{entry.get('reason')}", file=sys.stderr)


def report_reel_verification(payload) -> None:
    """Say WHICH plan and WHICH timelines `reel.verify` graded.

    `verify_reels` returns a terminal record - `reel_verification` - and
    for a while nothing read it: the loop above printed the operation's
    status, so a build reported "nothing raised" and never said what had
    been looked at.  A gate whose account of itself is unread reads as
    coverage (AGENTS.md 10.4), and the record exists precisely so a run
    can name the plan it graded against.

    The RAISE inside `reel_build.verify_built_reels` is still the gate.
    This does not re-judge it; it reports what passed.
    """
    if not isinstance(payload, dict):
        return
    record = payload.get("reel_verification")
    if not isinstance(record, dict):
        return
    timelines = list(record.get("timelines_verified") or ())
    print(
        f"  verified {len(timelines)} reel timeline(s) in Resolve project "
        f"{record.get('resolve_project_name') or '?'!r} against "
        f"{record.get('plan_path') or '(no plan named)'}",
        file=sys.stderr)
    for name in timelines:
        print(f"    - {name}", file=sys.stderr)
    if not timelines:
        print("    (the record names no timeline - the plan graded none)",
              file=sys.stderr)


def _edit_video_runner():
    """The edit_video runner module, imported the way `operations.Operation` does.

    Borrowed for `save_pipeline_state` only: state persistence and its
    backup rule are not process-specific, and a second copy would be a
    second backup rule.
    """
    process_dir = REPO_ROOT / "library" / "processes" / "edit_video"
    for entry in (str(REPO_ROOT), str(REPO_ROOT / "library"),
                  str(process_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    import run_pipeline
    return run_pipeline
