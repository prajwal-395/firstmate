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


def record_uncarried_notes(project_folder: str, markers) -> dict:
    """File the promotion's marker losses as obligations. Never raises.

    `markers` is `promoted["markers"]` - the structured record of what
    the promotion carried and what it dropped, per reel
    (`reel_build.promote_staged_reels`). Reading two sibling keys off
    it and dropping the third is how a reel's blue went unanswered for
    forty minutes (2026-09-20): `marker_carry.report` had named it on
    stderr, and nothing durable survived.

    RECORD-AND-CONTINUE: an uncarried note is often the correct outcome
    (a note pinned to a removed take cannot carry anywhere), so a
    filing that fails is said on stderr - where the promotion's own
    report still names every dropped note with the captain's words -
    and the gate's verdict stands. What is filed becomes an obligation
    (`library/tools/uncarried_notes.py`): it names the reel, quotes the
    captain's words verbatim, and blocks that reel's sign-off until an
    explicit discharge says what happened to the note.
    """
    import sys as _sys

    try:
        from library.tools import uncarried_notes as _owed
        report = _owed.record(project_folder, markers)
    except Exception as record_failed:                  # noqa: BLE001
        print(f"  uncarried-note obligations not filed ({record_failed}) "
              f"- the promotion's report above still names every dropped "
              f"note with the captain's words",
              file=_sys.stderr, flush=True)
        return {"filed": [], "reopened": [], "open": {},
                "path": None, "error": f"{record_failed}"}
    filed = report.get("filed") or []
    reopened = report.get("reopened") or []
    if filed or reopened:
        print(f"  Uncarried notes filed as obligations: {len(filed)} new, "
              f"{len(reopened)} reopened - `manage_project.py "
              f"discharge-uncarried {project_folder}` lists what each "
              f"reel owes", file=_sys.stderr, flush=True)
    return report


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

    from library.tools.reel_build import (
        ReelVerificationRefused, verify_built_reels)
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
            "uncarried_notes": None,
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
    # grading them is the whole job and there is no second half. The
    # gate above already ran over `timelines_built` and
    # `verify_built_reels` refuses a pass whose report did not land,
    # so reaching here means the verdict is both run and recorded.
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
    except ReelVerificationRefused as gate_refused:
        # The gate refused NAMED reels: exactly those staging
        # containers and their baselines go before the refusal
        # propagates, or the next build would refuse on this run's
        # debris - never the batch. One refusing reel used to discard
        # every staging in flight (measured 2026-09-20); the discard
        # below is scoped to the refused names intersected with what
        # this run staged. Surviving siblings stay staged with their
        # holds. The approved timelines were never named and are still
        # in the project. A discard that itself fails is said, never
        # silent - but it never stops the gate's own refusal, which is
        # the verdict that matters.
        _staged_set = set(staged.values())
        _failed = [name for name in gate_refused.failed_reels
                   if name in _staged_set]
        _surviving = [name for name in staged.values()
                      if name not in set(_failed)]
        try:
            from library.tools import reel_phase_log as _phase_log
            for _staged in _failed:
                _phase_log.log_wait(
                    project_folder, _staging_numbers.get(_staged, 0),
                    _staged,
                    f"verification refused: {gate_refused}")
        except Exception:
            pass
        if _failed:
            from library.tools.reel_build import discard_staged_record
            try:
                discard_staged_record(
                    project_folder, resolve_project_name,
                    _failed, master_timeline_name)
            except Exception as cleanup_failed:
                import sys as _sys
                print(f"verify_reels: gate refused AND staging cleanup "
                      f"failed ({cleanup_failed}) - clear the staging "
                      f"timelines {sorted(_failed)} in Resolve "
                      f"before re-running", file=_sys.stderr)
        else:
            import sys as _sys
            print(f"verify_reels: gate refused but named no reel this "
                  f"run staged - discarding nothing; "
                  f"{len(_surviving)} staging(s) stay with their "
                  f"holds: {sorted(_surviving)}", file=_sys.stderr)
        if _surviving:
            raise ReelVerificationRefused(
                f"{gate_refused}\nRefused staging(s) discarded: "
                f"{sorted(_failed)}. Still staged with holds, "
                f"untouched by this refusal: {sorted(_surviving)}.",
                failed_reels=list(_failed))
        raise
    except Exception as gate_broken:
        # Verification never graded - a connect failure, a fatal the
        # gate itself raised (a scoped staging deleted outside this
        # run), a missing transcript. There is no verdict, so there is
        # nothing to discard: every staging stays with its hold and
        # the failure propagates naming that. Discarding here is what
        # turned an infra flake into destroyed siblings.
        import sys as _sys_broken
        print(f"verify_reels: verification never graded "
              f"({gate_broken}) - discarding nothing; "
              f"{len(list(staged.values()))} staging(s) stay with "
              f"their holds: {sorted(staged.values())}",
              file=_sys_broken.stderr)
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
    uncarried_report = None
    if staged:
        from library.tools import reel_replace_guard as _guard
        from library.tools.reel_build import (
            ReelBuildError as _PromoteError, promote_staged_reels)
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
        try:
            promoted = promote_staged_reels(
                project_folder, resolve_project_name, master_timeline_name,
                staged, allow_drops=recorded, supersede=superseding,
                retain=retaining)
        except _PromoteError as partial:
            # A partial promotion raises AFTER its passing reels fully
            # promoted - and their marker losses ride on the exception
            # (`.markers`), because there is no return record on this
            # path. Filed before the refusal propagates: the reels
            # already landed, and their drops are owed owners either
            # way. Never masks the refusal.
            record_uncarried_notes(
                project_folder, getattr(partial, "markers", None))
            raise
        uncarried_report = record_uncarried_notes(
            project_folder, promoted.get("markers"))
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
        "uncarried_notes": uncarried_report,
    }}


class GateStillsRefused(RuntimeError):
    """The still run cannot start, and this says which input is missing."""


def _resolve_live_project(project_folder: str):
    """The open Resolve project, refused unless it is this project's own.

    Reads the expected name from the project's own `project.yaml`
    (`resolve.project_name`) and refuses when Resolve is not running,
    when nothing is open, or when what is open is another project -
    grabbing one project's reel stills off another's timelines is the
    failure this refusal exists to stop. Mirrors the connect-and-check
    in `library/steps/step_4_05_render_subtitles/step.py`.
    """
    import os as _os
    import sys as _sys
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        _os.environ.setdefault(
            "RESOLVE_SCRIPT_API",
            "/Library/Application Support/Blackmagic Design/"
            "DaVinci Resolve/Developer/Scripting/Modules")
        _sys.path.insert(0, _os.environ["RESOLVE_SCRIPT_API"])
        try:
            import DaVinciResolveScript as dvr
        except ImportError as exc:
            raise GateStillsRefused(
                "Resolve scripting is unavailable - open Resolve first, "
                "or run with no frames to check the refusal") from exc
    from library.tools.resolve_locale import scriptapp_preserving_locale
    app = scriptapp_preserving_locale(dvr, "Resolve")
    if app is None:
        raise GateStillsRefused(
            "Resolve is not running - open Resolve first, or run with "
            "no frames to check the refusal")
    try:
        project = app.GetProjectManager().GetCurrentProject()
        open_name = project.GetName() if project is not None else None
    except Exception as exc:  # noqa: BLE001 - unreadable is unusable
        raise GateStillsRefused(
            f"the open Resolve project cannot be read: {exc}") from exc
    import yaml
    from library.tools.project_layout import ProjectLayout as _Layout
    try:
        with open(_Layout(project_folder).project_config_path,
                  encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except OSError as exc:
        raise GateStillsRefused(
            f"cannot read {project_folder}/project.yaml: {exc}") from exc
    expected = ((config.get("resolve") or {}).get("project_name") or "")
    if not expected:
        raise GateStillsRefused(
            f"{project_folder}/project.yaml names no resolve.project_name "
            f"- refusing to guess which open project to grab off")
    if open_name != expected:
        raise GateStillsRefused(
            f"Resolve has {open_name!r} open, not {expected!r} - "
            f"refusing to grab another project's reel stills")
    return project


def _hold_awake():
    """Hold the display awake for the grab run, best effort.

    With the display asleep viewer stills come back missing upper-track
    overlays while lower tracks read correctly (measured 2026-09-17:
    captions pixel-perfect, motion graphics absent, on frames that
    verify exactly with the display held awake -
    `library/tools/marker_capture.py`). Returns the held process, or
    None when there is nothing to hold with: absent `caffeinate`
    (Linux, CI) is not a refusal, just an unheld run.
    """
    import shutil as _shutil
    import subprocess as _subprocess
    if _shutil.which("caffeinate") is None:
        return None
    try:
        return _subprocess.Popen(["caffeinate", "-d", "-i"])
    except OSError:
        return None


def grab_gate_stills(project_folder: str, reel_label: str,
                     timeline_name: str, frames: list) -> dict:
    """Grab gate stills at named reel-relative frames off one timeline.

    The entry point a visual gate calls instead of writing its own
    position-and-grab script (reached as `reel.gate_stills`, called as
    `operations.get(...).run(...)` the way `rerender_and_swap` is
    driven). `frames` are reel-relative: 0 is the timeline's first
    frame, the same numbering the lanes passed on the command line.
    Stills bank into the project's own `7_02_verify_reels/gate_stills/`
    directory - never `/tmp`, where the lanes' throwaway versions left
    evidence no later run could find.

    Malformed input RAISES (`ValueError`): no project folder, no reel
    label, no timeline name, no frames, or a frame that is not an int.
    A negative or past-the-end frame is not malformed - it is a frame
    the timeline does not have, so it is REPORTED per frame (Resolve
    grabs a black still past the end) while the rest of the set still
    grabs.

    When Resolve is unavailable, or the open project is not this
    project's own, nothing grabs and the failure RETURNS (`ok: False`
    with the reason) rather than raising: the gate reads the report,
    and a re-run grabs the same filenames. With no failures the report
    carries one record per still - reel frame, set and read-back
    timecodes, path, dimensions and bytes - for the gate to judge.
    """
    from library.tools import gate_stills as _stills
    from library.tools.project_layout import Area, ProjectLayout

    if not project_folder:
        raise ValueError(
            "grab_gate_stills needs a project_folder - stills bank into "
            "the project's step directory, never into the checkout")
    if not isinstance(reel_label, str) or not reel_label:
        raise ValueError(
            "grab_gate_stills needs a reel_label - it names the still "
            "files, and there is nothing to infer it from")
    if not isinstance(timeline_name, str) or not timeline_name:
        raise ValueError(
            "grab_gate_stills needs a timeline_name - the timeline is "
            "matched exactly, never by prefix, so guessing is refused")
    checked = list(frames or [])
    if not checked:
        raise ValueError(
            "grab_gate_stills needs at least one frame - a still run "
            "over no frames grabs nothing and proves nothing")
    for index, frame in enumerate(checked):
        if not isinstance(frame, int) or isinstance(frame, bool):
            raise ValueError(
                f"frame {index} is {frame!r}, not an int reel-relative "
                f"frame - refusing to guess which frame the gate meant")

    layout = ProjectLayout(project_folder)
    out_dir = layout.write_dir(Area.GATE_STILLS, step="verify_reels")

    try:
        project = _resolve_live_project(project_folder)
    except GateStillsRefused as exc:
        return {
            "ok": False,
            "reel_label": reel_label,
            "timeline": timeline_name,
            "stills": [],
            "failed": [{"reel_frame": frame, "reason": str(exc)}
                       for frame in checked],
            "restored": {"timeline": None, "ok": False},
            "error": str(exc),
        }
    held = _hold_awake()
    try:
        return _stills.grab_reel_stills(
            project, timeline_name, checked, out_dir,
            reel_label=reel_label)
    finally:
        if held is not None:
            try:
                held.terminate()
            except Exception:  # noqa: BLE001 - best effort, said nowhere
                pass


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
