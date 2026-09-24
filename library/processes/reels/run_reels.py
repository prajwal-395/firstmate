"""The runner of the `reels` PROCESS: its nodes, in its own DAG's order.

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

So the node ORDER comes off this process's own dag.json rather than
being spelled here, and each node runs through the operation registry,
which checks the node's DERIVED requirements first and REFUSES naming
what is missing. No build path lives here - the operation resolves to
the step's own body, and the step's body calls `reel_build`. One
implementation.

State persistence is borrowed, not copied: `save_pipeline_state` is the
edit_video runner's own writer because it owns the backup rule
(AGENTS.md 8), and that rule is not process-specific.
"""

from __future__ import annotations

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
    """Run every node of the reels process. 0, or 1 on a refusal.

    `args` carries the options `add_arguments` registers. A refusal
    stops the run where it happened, names the operation and what it
    refused on, and makes no closing commit: nothing after it ran.
    """
    from library.tools import operations, processes
    from library.tools.project_layout import ProjectLayout

    for node_id in processes.execution_order(processes.REELS):
        for op in operations.by_node(node_id):
            if op.caller_supplied:
                # Caller-supplied operations take arguments only a
                # caller supplies - the touchup's change spec, the
                # stills grab's reel label and frames - handed in by
                # the fix or gate that computed them. This loop is not
                # such a caller, so it does not drive them: as coded
                # it walked every owned op, and on a ready project
                # `reel.build` completed and the loop then REFUSED at
                # `reel.touchup` (`spec` unbound) before `reel.ask`
                # ever ran. Skipped, never executed, and nothing is
                # recorded for one. (The dry run's old-path walk in
                # `library/tools/ren_dry_run.py` reads the same flag.)
                print(f"{op.name}: skipped (caller-supplied)",
                      file=sys.stderr)
                continue
            result = op.execute(
                project_folder,
                skip_captions=args.skip_captions,
                only_reels=args.only_reel or None,
                timeline_name_suffix=args.name_suffix,
                allow_drops=args.allow_drop or None,
                supersede=args.supersede or None,
                retain=args.retain or None,
                rebuild_all=bool(getattr(args, "rebuild_all", False)))
            if result.refused:
                print(f"REFUSED: {op.name}", file=sys.stderr)
                print(result.error, file=sys.stderr)
                return 1

            # RECORD the node's output the way a run records one, so the
            # edge to the next node can carry it and so the build is
            # readable afterwards by everything that reads
            # `step_outputs` - the traceback, the dashboard, `status`.
            path = ProjectLayout(project_folder).pipeline_data_path
            state = (json.loads(Path(path).read_text(encoding="utf-8"))
                     if Path(path).is_file() else {})
            state["project_folder"] = project_folder
            slot = state.setdefault("step_outputs", {})
            record_node_output(slot, node_id, result.payload)
            _edit_video_runner().save_pipeline_state(project_folder, state)
            print(f"{op.name}: {result.status}", file=sys.stderr)
            report_rebuild_need(result.payload)
            report_reel_verification(result.payload)

    # ══════════════════════════════════════════════════════════════
    # THE CLOSING COMMIT (library/tools/versions/store.py)
    # ══════════════════════════════════════════════════════════════
    # The per-build commit fires INSIDE the promoting node, and this
    # loop writes that node's own output to pipeline_data.json AFTER
    # the node returns - so the last node's record could never be in
    # the commit it belongs to. Measured 2026-09-11 on the captain's
    # project: HEAD was "reels build: Reel 13" and the working tree
    # was dirty with exactly `step_outputs.verify_reels.
    # reel_verification.organised`, the bin organisation the commit
    # ran too early to see. A store that is dirty after every build
    # teaches a reader to ignore its dirtiness, which is how a hand
    # edit goes missing.
    #
    # So the run closes its own record, here, where the state write
    # it completes lives. `clean` is the ordinary answer once the
    # node's commit already covered everything.
    commit_run_tail(project_folder)
    return 0


def record_node_output(slot: dict, node_id: str, payload) -> None:
    """Merge one op's payload into its node's recorded output.

    One node may own several ops (`build_reels` owns both `reel.build`
    and `reel.ask`), and a wholesale write lets the later op destroy
    the earlier's output. Measured 2026-09-19 on Reel 04: `reel.ask`
    overwrote `step_outputs.build_reels` with its ask-only payload, the
    `reel_build` record the build had just placed was lost, and
    `reel.verify` refused - a staged reel with no path to promotion. A
    node's record is the union of its ops; non-dict payloads keep the
    old overwrite behaviour.
    """
    prior = slot.get(node_id)
    if isinstance(prior, dict) and isinstance(payload, dict):
        prior.update(payload)
        slot[node_id] = prior
    else:
        slot[node_id] = payload


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
            "The per-build commit runs inside the promoting node, "
            "before the runner writes that node's own output to "
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
    """Say WHICH plan and WHICH timelines the verify node graded.

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
