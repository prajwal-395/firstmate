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
    if not timelines_built:
        raise ReelVerifyRefused(
            "the reel_build record names no timelines_built, so there "
            "is nothing this node may grade. Grading every reel timeline "
            "instead would re-grade work this build never touched; "
            "re-run build_reels.")
    # `staged_timelines` maps final -> staging while anything is
    # staged. It is absent on records written before staging existed -
    # those timelines are already final and promote to nothing, so
    # grading them is the whole job and there is no second half.
    staged = dict(build.get("staged_timelines") or {})
    try:
        verify_built_reels(
            project_folder=project_folder,
            resolve_project_name=resolve_project_name,
            master_timeline_name=master_timeline_name,
            plan_path=plan_path,
            # ONE spelling of where the transcript lives, owned by the
            # module that writes it (tests/test_operations.py pins that
            # it is not composed by hand outside reel_build.py).
            transcript_path=str(transcript_path(project_folder)),
            only_reels=timelines_built)
    except Exception:
        # The gate refused: the staging containers and their baselines
        # go before the refusal propagates, or the next build would
        # refuse on this run's debris. The approved timelines were
        # never named and are still in the project. A discard that
        # itself fails is said, never silent - but it never stops the
        # gate's own refusal, which is the verdict that matters.
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

    # Promotion is the build's deferred second half, and it runs HERE -
    # after this gate passed, never before. The build node stages into
    # separate containers and grades nothing; this node grades the
    # staging and only then moves it onto the final names, retiring
    # each approved original to a backup first
    # (`reel_build.promote_staged_reels`).
    organised = None
    if staged:
        from library.tools.reel_build import promote_staged_reels
        promoted = promote_staged_reels(
            project_folder, resolve_project_name, master_timeline_name,
            staged)
        organised = promoted["organised"]
        timelines_verified = list(promoted["promoted"])
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
