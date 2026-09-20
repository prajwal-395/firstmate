"""Grade the reels that were BUILT against the plan they were built from.

The `verify_reels` node of the `reels` process
(`library/processes/reels/dag.json`).

Why this is a node and not a tail call
--------------------------------------
`rebuild_reels_in_project` has always ended by running the conformance
verifier, so a defective build raised from inside the build.  That is
correct for a direct caller and wrong for a process: "the timelines were
placed" and "the timelines conform" are two facts, they fail for
different reasons, and a ledger that carries one entry for both cannot
say which happened.  So the process has two nodes, and the edge between
them carries the build's own record - `reel_build` - rather than letting
this half re-derive what was placed.

It owns no verification logic
-----------------------------
`library/tools/reel_conformance_verifier.py` is the verifier and stays
the verifier; `reel_build.verify_built_reels` is the one caller that
turns its exit code into a raise.  This body reads the merged input dict,
refuses what it cannot do, and calls that.
"""
from __future__ import annotations


class ReelVerifyRefused(RuntimeError):
    """Verification cannot start, and this says which input is missing."""


def verify_reels(data: dict) -> dict:
    """Run the conformance verifier over what `build_reels` placed.

    Raises when the verifier finds an error - the gate is
    `reel_build.verify_built_reels`, not a second reading of the report
    here.  Returns the record of a PASS, so a run can say which plan and
    which timelines were graded rather than only that nothing raised.
    """
    project_folder = (data or {}).get("project_folder") or ""
    if not project_folder:
        raise ReelVerifyRefused(
            "verify_reels has no project_folder, so there is nowhere to "
            "read the plan from and nowhere to write the report.")

    build = (data or {}).get("reel_build")
    if not build:
        raise ReelVerifyRefused(
            "verify_reels has no reel_build record, so nothing says which "
            "timelines were placed or which plan they came from. Run "
            "build_reels first - it is the producing node of this edge.")

    transcript = (data or {}).get("timeline_transcript")
    if not transcript:
        raise ReelVerifyRefused(
            "verify_reels has no timeline_transcript, and the verifier "
            "grades the placed speech against it. NO STEP MAKES ONE - see "
            "`python3 -m library.tools.timeline_transcript <project> "
            "--write`.")

    from library.tools.reel_build import verify_built_reels
    from library.tools.timeline_transcript import transcript_path

    # Every address comes off the BUILD'S OWN RECORD, never re-derived
    # from project.yaml. The build wrote into a named project and a named
    # timeline; those are what must be graded, and a project.yaml edited
    # between the two nodes would otherwise send the verifier somewhere
    # else. It is the same reason `plan_path` is carried rather than
    # re-found: a verifier that re-derives can grade against a different
    # answer than the one the build wrote.
    resolve_project_name = build.get("resolve_project_name") or ""
    master_timeline_name = build.get("master_timeline_name") or ""
    plan_path = build.get("plan_path") or ""
    absent = [name for name, value in (
        ("resolve_project_name", resolve_project_name),
        ("master_timeline_name", master_timeline_name),
        ("plan_path", plan_path)) if not value]
    if absent:
        raise ReelVerifyRefused(
            f"the reel_build record names no {', no '.join(absent)}, so "
            f"the verifier cannot address what it must read or the plan it "
            f"must grade against. Re-run build_reels: the record is what "
            f"it writes.")

    # Scoped to what the build PLACED, read off its own record. This
    # node used to grade every `Reel *` timeline in the project, so a
    # build of one reel paid for all fifty and failed on findings from
    # timelines it never touched - while its own return record below
    # claimed it had verified only `timelines_built`. The record was
    # the promise; the code is now kept to it.
    timelines_built = list(build.get("timelines_built") or ())
    left_alone = list(build.get("reels_left_alone") or ())
    if not timelines_built and left_alone:
        # The build placed nothing because nothing needed placing
        # (`library/tools/reel_rebuild_need.py`). That is not a build
        # with no record - it is a build whose record says every reel
        # it named already carries what it would have given it. There
        # is no staging to grade and nothing to promote, and calling
        # the gate with an empty scope is refused on purpose: a gate
        # that passes having graded nothing reads as coverage
        # (AGENTS.md 10.4).
        return {"reel_verification": {
            "plan_path": plan_path,
            "resolve_project_name": resolve_project_name,
            "master_timeline_name": master_timeline_name,
            "timelines_verified": [],
            "reels_left_alone": left_alone,
            "nothing_to_grade": (
                f"all {len(left_alone)} reel(s) needed no Resolve pass, "
                f"so nothing was staged and nothing was promoted - the "
                f"approved timelines are untouched"),
        }}
    if not timelines_built:
        raise ReelVerifyRefused(
            "the reel_build record names no timelines_built, so there "
            "is nothing this node may grade. Grading every reel timeline "
            "instead would re-grade work this build never touched; "
            "re-run build_reels.")
    # Staging/final container -> reel number, read off the plan the
    # build itself recorded (never parsed out of a timeline name), so
    # the phase log names which reel each line belongs to. Unknown is
    # 0 with the full timeline name - an honest absence, not a guess.
    _staging_numbers: dict = {}
    _final_numbers: dict = {}
    try:
        from library.tools.reel_build import built_name
        from library.tools.reel_proposal import read_proposal
        _suffix = str(build.get("name_suffix") or "")
        for _m in read_proposal(plan_path):
            try:
                _final_numbers[built_name(_m, _suffix)] = int(_m.number)
            except Exception:
                pass
    except Exception:
        pass
    # `staged_timelines` maps final -> staging while anything is
    # staged. It is absent on records written before staging existed -
    # those timelines are already final and promote to nothing, so
    # grading them is the whole job and there is no second half.
    staged = dict(build.get("staged_timelines") or {})
    for _final, _staging in staged.items():
        if _final in _final_numbers:
            _staging_numbers[_staging] = _final_numbers[_final]
    # The gain the BUILD calibrated and placed with, off its own
    # record - the verifier grades stored transforms, so on a run
    # whose renderer drew another gain the fallback default would
    # misjudge every placement. Absent on records written before the
    # probe existed, which grade under the fallback as before.
    from library.tools.resolve_transform import FALLBACK_DRAW_GAIN
    run_gain = ((build.get("draw_gain_calibration") or {}).get("gain")
                or FALLBACK_DRAW_GAIN)
    try:
        verify_built_reels(
            project_folder=project_folder,
            resolve_project_name=resolve_project_name,
            master_timeline_name=master_timeline_name,
            plan_path=plan_path,
            draw_gain=run_gain,
            # ONE spelling of where the transcript lives, owned by the
            # module that writes it (tests/test_operations.py pins that
            # it is not composed by hand outside reel_build.py).
            transcript_path=str(transcript_path(project_folder)),
            only_reels=timelines_built)
    except Exception as gate_refused:
        # The gate refused: the staging containers and their baselines
        # go before the refusal propagates, or the next build would
        # refuse on this run's debris. The approved timelines were
        # never named and are still in the project. A discard that
        # itself fails is said, never silent - but it never stops the
        # gate's own refusal, which is the verdict that matters.
        try:
            from library.tools import reel_phase_log as _phase_log
            for _staged in timelines_built:
                _phase_log.log_wait(
                    project_folder, _staging_numbers.get(_staged, 0),
                    _staged,
                    f"verification refused: {gate_refused}")
        except Exception:
            pass
        if staged:
            from library.tools.reel_build import discard_staged_record
            try:
                discard_staged_record(
                    project_folder, resolve_project_name,
                    list(staged.values()), master_timeline_name)
            except Exception as cleanup_failed:
                import sys as _sys
                print(f"verify_reels: gate refused AND staging cleanup "
                      f"failed ({cleanup_failed}) - clear the staging "
                      f"timelines {sorted(staged.values())} in Resolve "
                      f"before re-running", file=_sys.stderr)
        raise

    # The gate passed: when verification ran, per reel, at the moment
    # it ran - a pure read that would otherwise leave no trace, which
    # is exactly the first hypothesis the 2026-09-18 stall could not
    # exclude (`reel_phase_log`).
    try:
        from library.tools import reel_phase_log as _phase_log
        for _staged in timelines_built:
            _phase_log.log_event(
                project_folder, _staging_numbers.get(_staged, 0),
                _staged, _phase_log.VERIFIED,
                detail="conformance gate passed over the staging")
    except Exception:
        pass

    # Promotion is the build's deferred second half, and it runs HERE -
    # after this gate passed, never before. The build node stages into
    # separate containers and grades nothing; this node grades the
    # staging and only then moves it onto the final names, retiring
    # each approved original to a backup first
    # (`reel_build.promote_staged_reels`).
    #
    # The replace guard's declaration comes off the BUILD'S OWN RECORD
    # (`allow_drops`, normalised per final by the build node), never
    # re-derived - for the same reason the binding and the plan come
    # off it. A declaration supplied alongside this node (the
    # `--allow-drop` flag reaches both nodes) is the fallback for a
    # record written before declarations existed.
    organised = None
    if staged:
        from library.tools import reel_replace_guard as _guard
        from library.tools.reel_build import promote_staged_reels
        recorded = build.get("allow_drops")
        if recorded is None:
            recorded = _guard.parse_specs(
                (data or {}).get("allow_drops"), list(staged))
        # The same rule for the sign-off declaration: the build's own
        # record first, this node's input only as the fallback for a
        # record written before declarations existed
        # (`library/tools/reel_signoff.py`).
        superseding = build.get("supersede")
        if superseding is None:
            superseding = (data or {}).get("supersede")
        # The same rule for the retain declaration: the build's own
        # record first, this node's input only as the fallback
        # (`library/tools/reel_retirement.py`).
        retaining = build.get("retain")
        if retaining is None:
            retaining = (data or {}).get("retain")
        promoted = promote_staged_reels(
            project_folder, resolve_project_name, master_timeline_name,
            staged, allow_drops=recorded, supersede=superseding,
            retain=retaining)
        organised = promoted["organised"]
        timelines_verified = list(promoted["promoted"])
        # Consolidation ran, per reel, at the moment it ran: the staging
        # is now the final name (`reel_phase_log`).
        try:
            from library.tools import reel_phase_log as _phase_log
            for _final in timelines_verified:
                _phase_log.log_event(
                    project_folder, _final_numbers.get(_final, 0),
                    _final, _phase_log.CONSOLIDATED,
                    detail=f"promoted {staged.get(_final, '')} "
                           f"onto {_final}")
        except Exception:
            pass
        # The 6.01 hook never fired for reels, so no reel build ever
        # committed its baseline (measured 2026-09-11). Snapshot each
        # promoted timeline beside the declaration it was built from
        # and commit. Never fails the gate.
        import sys as _sys_vc
        try:
            from library.tools import build_version_control as _bvc
            _vc = _bvc.record_reel_promotion(
                project_folder, resolve_project_name,
                list(promoted["promoted"]))
            if _vc.get("committed"):
                print(f"── Version control: committed {_vc['commit']} "
                      f"({len(_vc.get('files', []))} file(s)) ──",
                      file=_sys_vc.stderr)
            else:
                print(f"  version-control record not committed: "
                      f"{_vc.get('reason', 'unknown')}", file=_sys_vc.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"  version-control record failed: {exc!r} - "
                  f"the reels are promoted and unaffected",
                  file=_sys_vc.stderr)
        # The detection half of the conformance sweep, over the
        # PROMOTED project - every reel timeline, not just the ones
        # this node graded. The refusing gate above stays scoped (a
        # whole-project gate failed clean single-reel builds on
        # timelines they never touched); this reports whether the
        # round disturbed a reel it did not touch, and never refuses
        # (`reel_build.sweep_all_reels_informational`).
        from library.tools.reel_build import sweep_all_reels_informational
        sweep_all_reels_informational(
            project_folder=project_folder,
            resolve_project_name=resolve_project_name,
            master_timeline_name=master_timeline_name,
            plan_path=plan_path,
            transcript_path=str(transcript_path(project_folder)))
        # A promotion that leaves other stagings pending says so: the
        # holds file is the pending-promotion record, and a build that
        # stages but never promotes otherwise sits protected and
        # invisible (measured on Reel 16, 2026-09-19). Reported, never
        # a gate - the reels above already landed.
        try:
            from library.tools import staging_holds as _holds
            _pending_report = _holds.report_pending(project_folder)
        except Exception:
            _pending_report = ""
        if _pending_report:
            import sys as _sys_pending
            print(f"  {_pending_report}", file=_sys_pending.stderr,
                  flush=True)
    else:
        timelines_verified = list(timelines_built)

    return {"reel_verification": {
        "passed": True,
        "plan_path": plan_path,
        "timelines_verified": timelines_verified,
        "resolve_project_name": resolve_project_name,
        "master_timeline_name": master_timeline_name,
        "organised": organised,
    }}


def main():
    import json
    import sys

    data = json.loads(sys.stdin.read())
    try:
        json.dump(verify_reels(data), sys.stdout, indent=2)
    except ReelVerifyRefused as refused:
        print(f"verify_reels REFUSED: {refused}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
