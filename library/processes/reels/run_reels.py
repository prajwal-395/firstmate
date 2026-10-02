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
        help="Append this to the Resolve timeline name each reel is built "
             "into, and to its caption filenames. Default: the plan's own "
             "name, which REPLACES the timeline already called that.")
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


def run(project_folder: str, args) -> int:
    """Run every capability of the reels process. 0, or 4 on a refusal.

    `args` carries the options `add_arguments` registers. A refusal
    stops the run where it happened, names the operation and what it
    refused on, and makes no closing commit: nothing after it ran.
    4 is the refusal code (`library/tools/ren_refusal.py`).
    """
    from library.tools import capabilities, operations, processes, provenance

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
                rebuild_all=bool(getattr(args, "rebuild_all", False)))
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
    "caption_hashes", "closer_fit", "track_plans",
    "transition_overlays", "staged_timelines", "allow_drops",
    "reels", "notes",
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
