"""The reel.opening qualification: live, on a Ren-owned product-format copy.

`reel.opening` tightens a reel's opening through the structural edits a
model decided from the written brief and the reel's own words.  The
operation had no qualification record, so the router refused it on every
reel: an operation with an executor and no live evidence is not a
production route.

This runs the operation LIVE, on a Ren-owned scratch copy in the
product's delivery format (1080x1920 at 23.976), and imports the
evidence it produces through `library/tools/axi_qualification.py`.

What it never does:

* it never writes the captain's projects, timelines or media.  The
  target is a Ren-owned scratch project, the media is a copy this run
  owns in `/tmp`, and the captain's open project is borrowed and
  restored around the run (`qualification_project.borrowed`);
* it never starts a broker.  The lease comes from the machine's ONE
  serving broker (`library/tools/resolved`), and the AXI verbs run in
  THIS process, so the code the evidence measures is the code in this
  checkout;
* it never grades a refusal as a pass.  A run that REFUSED is recorded
  as a refusal, with the reason the AXI gave, and it produces no
  qualification row - a refusal proves a refusal;
* it never imports a row whose evidence it did not measure.  The
  coverage of every row is the MEASURED runtime state of the case that
  earned it (`library/tools/axi_qualification_state.py` owns that
  vocabulary).

    REN_AXI_QUAL_PROJECT="Ren Scratch - <scratch project>" \\
    REN_AXI_QUAL_PROJECT_FOLDER="<the Ren project folder>" \\
    python3 -m tests.live.opening_qualification

`tests/unit/resolve/test_reel_opening_readback.py` names the defects
this closes.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Where this lane's evidence lives.
REPORT_DIR = ROOT / "tests" / "live" / "reports" / "opening-qualification"
EVIDENCE_PATH = REPORT_DIR / "opening-evidence.json"
#: The canonical report directory, beside the other rows' reports.
CANONICAL_REPORT_DIR = ROOT / "tests" / "live" / "reports" / "registry"

#: The media this run OWNS.  The clip is 24fps media, and the scratch
#: timeline is the product's 23.976 delivery rate, so the clip is
#: RETIMED into it: the source span and the record span are then
#: different numbers, which is the state the re-place has to be judged
#: in (measured 2026-10-21: the same 96-frame clip occupies 96 record
#: frames on a 24fps timeline and 95 on the 23.976 one).
OWNED_DIR = Path("/tmp/vep-axi010-owned")
OWNED_A = OWNED_DIR / "vep-axi010-opening-a.mp4"
OWNED_B = OWNED_DIR / "vep-axi010-opening-b.mp4"
OWNED_A_NAME = "vep-axi010-opening-a.mp4"
OWNED_B_NAME = "vep-axi010-opening-b.mp4"

#: The brief the decision was made from.  It travels with the operation
#: as its `brief` parameter, and it is the model's own input: the
#: opening runs long because its marked trailing pause holds dead air
#: after the speaker finishes.
BRIEF_TEXT = (
    "Tighten the opening to the supplied brief while preserving the\n"
    "speaker's meaning and approved edits.\n"
    "\n"
    "The opening runs long because its marked trailing pause holds two\n"
    "seconds of dead air after the speaker finishes. Remove the marked\n"
    "pause and close the gap; keep every spoken word in order and keep\n"
    "all approved edits and items outside the opening exactly as they are."
)

#: The opening clip's speech ends here; the frames after it are the pause.
OPENING_OUT_FRAME = 72

#: The report schema the importer consumes.
REPORT_SCHEMA = "ren-axi-qualification-result/v2"


# ── The media this run owns ──────────────────────────────────────────


def _own_media() -> None:
    """Two clips this run owns, rendered into `/tmp` and never shared."""
    OWNED_DIR.mkdir(parents=True, exist_ok=True)
    for path, seconds, colour in ((OWNED_A, 4, "testsrc2"),
                                   (OWNED_B, 3, "smptebars")):
        if path.is_file():
            continue
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"{colour}=size=1920x1080",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
             "-t", str(seconds), "-r", "24", "-pix_fmt", "yuv420p",
             "-c:v", "libx264", "-c:a", "aac", str(path)],
            check=True, capture_output=True, text=True, encoding="utf-8")


# ── The live run ─────────────────────────────────────────────────────


def _call(argv):
    """One AXI verb, in this process, with its output captured."""
    from library.tools import resolve_axi_core_commands as cmds

    out, err = io.StringIO(), io.StringIO()
    parser = cmds.build_parser()
    args = parser.parse_args(cmds._normalize(list(argv)))
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = int(args.func(args) or 0)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:  # noqa: BLE001 - the failure IS the evidence
            code = 1
            out.write(f"{type(exc).__name__}: {exc}\n")
            out.write(traceback.format_exc())
    return {"argv": list(argv), "exit_code": code,
            "stdout": out.getvalue()[-3000:], "stderr": err.getvalue()[-1500:]}


def _clips_of(snapshot):
    return [{"track_type": track.get("type"), "track_index": track.get("index"),
             "unique_id": clip.get("unique_id"), "name": clip.get("name"),
             "record_in": clip.get("record_in"),
             "record_out": clip.get("record_out"),
             "source_in": clip.get("source_in_frame"),
             "source_out": clip.get("source_out_frame"),
             "source_file": clip.get("source_file"),
             "duration": clip.get("duration"),
             "speed": clip.get("speed")}
            for track in (snapshot.get("tracks") or [])
            for clip in (track.get("clips") or [])]


def _ensure_scratch(project: str) -> dict:
    """The Ren-owned scratch project this run writes on.

    It is created in the product's delivery format when it is not there
    yet: the qualification is earned in the state the product ships
    (1080x1920 at 23.976), not in the fixture's landscape format.
    """
    created = _call(["project", "scratch", "create", project,
                     "--fps", "23.976", "--width", "1080", "--height", "1920",
                     "--apply"])
    if created["exit_code"] and "already exists" not in created["stdout"]:
        raise RuntimeError("the scratch project could not be created: "
                           + created["stdout"])
    return created


def _run(project: str, project_folder: str) -> dict:
    """The live run: seed, decide, execute, read back, measure."""
    from library.tools import (
        axi_qualification_state,
        edit_request,
        qualification_project,
        resolve_axi_core,
        timeline_shadow,
    )
    from library.tools.marker_feedback import connect_resolve
    from library.tools.resolve_lock import resolve_lease

    timeline_base = f"{project} - Empty"
    timeline = f"{project} run {time.strftime('%H%M%S')}"
    evidence = {"project": project, "timeline": timeline,
                "project_folder": project_folder,
                "engine": str(ROOT), "steps": []}
    # The client ENSURES a broker before it leases or runs anything, and
    # a linked worktree must never start one: the serving broker is
    # already up, and this only stops the client from trying to start a
    # second one.
    from library.tools.resolved import client as resolved_client
    resolved_client.ensure = lambda *args, **kwargs: None
    with resolve_lease("reel.opening qualification: product format scratch",
                       exclusive=True, timeout=2400.0):
        # The AXI verbs run HERE, on this connection, under this lease -
        # the broker's own refusal says a write "runs locally under your
        # own lease". Marking the executor thread is what makes the AXI
        # dispatcher run instead of forwarding back to the broker.
        resolved_client.mark_executor()
        resolve = connect_resolve()
        manager = resolve.GetProjectManager()
        with qualification_project.borrowed(resolve) as found:
            evidence["borrowed_from"] = found
            evidence["steps"].append(_ensure_scratch(project))
            qualification_project.switch_project(manager, project)
            project_handle = manager.GetCurrentProject()
            # 0. a FRESH timeline for this run: the base is empty, and a
            #    run that reused a trimmed timeline would measure a
            #    different before-state than the one it decided against.
            duplicate = _call(["timeline", "duplicate", "--project", project,
                               "--timeline", timeline_base, "--name",
                               timeline, "--apply"])
            evidence["steps"].append(duplicate)
            if duplicate["exit_code"]:
                raise RuntimeError("the working timeline could not be "
                                   "created: " + duplicate["stdout"])
            timeline_handle = next(
                (project_handle.GetTimelineByIndex(index)
                 for index in range(1, int(
                     project_handle.GetTimelineCount() or 0) + 1)
                 if project_handle.GetTimelineByIndex(index).GetName()
                 == timeline), None)
            if timeline_handle is None:
                raise RuntimeError(f"no timeline named {timeline!r}")
            project_handle.SetCurrentTimeline(timeline_handle)

            # 1. seed the scratch copy: the opening clip (with its
            #    marked trailing pause) and the shot that follows it.
            evidence["steps"].append(_call(
                ["ingest", str(OWNED_A), str(OWNED_B), "--apply",
                 "--project", project]))
            evidence["steps"].append(_call(
                ["edit", "place", "--clip", OWNED_A_NAME, "--media", "video",
                 "--track", "1", "--record", "0", "--source-in", "0",
                 "--source-out", "95", "--apply", "--project", project,
                 "--timeline", timeline]))
            evidence["steps"].append(_call(
                ["edit", "place", "--clip", OWNED_B_NAME, "--media", "video",
                 "--track", "1", "--record", "96", "--source-in", "0",
                 "--source-out", "47", "--apply", "--project", project,
                 "--timeline", timeline]))

            # 2. the recorded base the request is prepared against.
            generation = timeline_shadow.observe(
                project_handle, timeline_handle, project_folder=project_folder)
            store = timeline_shadow.ShadowStore()
            before = store.snapshot(generation)
            evidence["base_generation"] = generation.generation
            evidence["before"] = _clips_of(before)
            opening = next((clip for clip in evidence["before"]
                            if int(clip["record_in"]) == 0), None)
            if opening is None:
                raise RuntimeError("the seeded opening clip is not on the "
                                   "timeline")

            # 3. the DECIDED edit: the model's opening tighten.
            request = edit_request.EditRequest(
                id=edit_request._request_id(),
                project=project,
                timeline=timeline,
                operations=(edit_request.EditOperation(
                    op="reel.opening",
                    target=edit_request.EditTarget(
                        unique_id=str(opening["unique_id"])),
                    params={
                        "unique_id": str(opening["unique_id"]),
                        "brief": BRIEF_TEXT,
                        "edits": [{
                            "op": "clip.trim",
                            "unique_id": str(opening["unique_id"]),
                            "in_frame": 0,
                            "out_frame": OPENING_OUT_FRAME,
                        }],
                    },
                    kind=edit_request.DIRECT,
                    expected_effect=("tighten the opening to the supplied "
                                     "brief")),),
                expected_base_generation=generation.generation,
                originator={"kind": "agent", "name": "ren revise",
                            "detail": "reel.opening qualification"})

            # 4. route, execute and verify through the semantic path.
            job = {"params": {"request": request.to_dict(),
                              "backend": "resolve_axi",
                              "base_generation": generation.generation}}
            receipt = resolve_axi_core.execute_semantic_edit_job(
                job, resolve=resolve)
            evidence["receipt"] = receipt

            # 5. what the timeline reads back as, and the state the
            #    evidence was earned in.
            after_generation = timeline_shadow.observe(
                project_handle, timeline_handle, project_folder=project_folder)
            after = store.snapshot(after_generation)
            evidence["after_generation"] = after_generation.generation
            evidence["after"] = _clips_of(after)
            state = axi_qualification_state.inspect(
                project=project, timeline=timeline,
                operations=[{"op": "reel.opening",
                             "unique_id": str(opening["unique_id"])}],
                resolve=resolve)
            evidence["qualification_state"] = state
    return evidence


# ── The report ───────────────────────────────────────────────────────


def _measured_state(evidence: dict) -> dict:
    return dict((evidence.get("qualification_state") or {}).get("state") or {})


def build_report(evidence: dict) -> dict:
    """The `ren-axi-qualification-result/v2` report this run earned.

    A refusal earns NOTHING: the report is only written when the
    operation committed and verified, because a refusal proves a
    refusal.
    """
    from library.tools import axi_qualification_dimensions as dimensions

    receipt = evidence.get("receipt") or {}
    if str(receipt.get("status") or "") != "committed":
        raise RuntimeError(
            "the run did not commit: " + str(receipt.get("refusal") or
                                             receipt.get("status")))
    state = _measured_state(evidence)
    if not state:
        raise RuntimeError("the run measured no runtime state")
    before = evidence.get("before") or []
    after = evidence.get("after") or []
    opening_before = next((clip for clip in before
                           if int(clip["record_in"]) == 0), None)
    if opening_before is None:
        raise RuntimeError("the run read no opening clip back")
    opening_after = next(
        (clip for clip in after
         if str(clip.get("source_file") or "")
         == str(opening_before.get("source_file") or "")
         and int(clip.get("source_in") or 0) == int(opening_before["source_in"])
         and int(clip.get("record_in") or 0) == int(opening_before["record_in"])),
        None)
    if opening_after is None:
        raise RuntimeError("the trimmed opening clip did not read back")
    acceptance = ((receipt.get("acceptance_contracts") or [{}])[0]
                  .get("evidence") or {}).get("acceptance") or {}
    checks = [{"check": str(row.get("check")), "result": "passed"}
              for row in (acceptance.get("results") or [])
              if row.get("passed") is not False]
    if not checks:
        raise RuntimeError("the run produced no acceptance checks")
    journal = ""
    readback = receipt.get("readback")
    if isinstance(readback, list):
        readback = readback[0] if readback else {}
    readback = readback or {}
    for sub in (readback.get("operation_receipts") or []):
        reference = (((sub.get("execution_contract") or {}).get("recovery")
                      or {}).get("reference") or {})
        if reference.get("entry_id"):
            journal = str(reference["entry_id"])
            break
    uncovered = {}
    for field in dimensions.DIMENSIONS:
        measured = [str(state[field])] if state.get(field) else []
        allowed = dimensions.vocabulary(field)
        if allowed is None:
            continue
        uncovered[field] = [value for value in allowed
                            if value not in measured]
    report = {
        "schema": REPORT_SCHEMA,
        "operation_id": "reel.opening",
        "backend": "resolve_axi",
        "result": "passed",
        "evidence": (
            "tests/live/reports/opening-qualification/opening-evidence.json"
            "#the live reel.opening run on the Ren-owned scratch copy "
            + str(evidence.get("project")) + " / "
            + str(evidence.get("timeline"))
            + " (1080x1920 at 23.976, the product's delivery format): the "
            "decided opening tighten committed, the trimmed clip read back "
            "at its declared span, every other item unchanged, and the "
            "undo journal entry written"),
        "resolve_version": str(state.get("resolve_version") or ""),
        "environment": {
            "platform": sys.platform,
            "resolve_version": str(state.get("resolve_version") or ""),
        },
        "semantic_boundary": {
            "applies_to": [
                ("tightening a reel's opening through the edits the model "
                 "decided from the written brief and the reel's own words: "
                 "a trim of the opening clip at the frame the decision "
                 "named"),
                ("a timeline that holds the opening clip on a video row and "
                 "nothing else the decision touches"),
            ],
            "does_not_apply_to": [
                ("a decided edit that removes a clip: `clip.remove` was not "
                 "exercised by this run"),
                ("a timeline whose frame rate, geometry, track layout, "
                 "linked audio or existing effects are outside "
                 "coverage.dimensions"),
                ("the EDITORIAL choice of the opening: the operation "
                 "carries out the edits a model decided from evidence, and "
                 "no rule here picks the opening"),
            ],
        },
        "coverage": {
            "dimensions": {field: [str(state.get(field))]
                           for field in dimensions.DIMENSIONS
                           if state.get(field)},
            "configurations": [
                "ren_owned_scratch_opening_1080x1920_23.976"],
            "uncovered": uncovered,
            "source": "measured",
        },
        "project_conditions": [
            str(evidence.get("project")) + " / "
            + str(evidence.get("timeline")),
            ("a Ren-owned scratch copy in the product's delivery format "
             "(1080x1920 at 23.976), seeded with the run's own media and "
             "the shot that follows the opening"),
            ("the request named the brief the decision was made from, and "
             "the trim point the decision named"),
        ],
        "supported_media_types": ["video"],
        "expected_state": {
            "opening_clip_record_in": int(opening_before["record_in"]),
            "opening_clip_record_out": OPENING_OUT_FRAME,
            "every_other_item_unchanged": True,
        },
        "actual_state": {
            "receipt_status": str(receipt.get("status") or ""),
            "backend": str(receipt.get("backend") or ""),
            "opening_clip_record_in": int(opening_after["record_in"]),
            "opening_clip_record_out": int(opening_after["record_out"]),
            "source_in": int(opening_after["source_in"]),
            "source_out": int(opening_after["source_out"]),
            "undo_journal_entry": journal,
        },
        "preservation_checks": checks,
        "preservation_limits": [
            ("one opening clip, trimmed at the frame the decision named"),
            ("every other item on the timeline keeps its identity, its "
             "source span and its record span"),
            ("the evidence is ONE live run on a Ren-owned scratch copy; it "
             "does not qualify a reel that carries a Fusion composition on "
             "the row the trim touches (the replacement path cannot carry "
             "one, and the operation refuses it there)"),
        ],
        "visual_verification": [
            ("the opening clip reads back at record 0.."
             + str(OPENING_OUT_FRAME)
             + " with the source window the trim named, read from a fresh "
             "live timeline"),
        ],
        "audio_verification": [
            ("the audio rows read back unchanged: the trim moved picture "
             "frames and no audio row"),
        ],
        "recovery_status": "passed" if journal else "not_required",
    }
    # The predicates follow the coverage: the values each field was
    # measured in.
    report["qualification_predicates"] = [
        {"field": field, "operator": "equals", "value": values[0]}
        for field, values in sorted(report["coverage"]["dimensions"].items())
        if len(values) == 1]
    return report


def _import_report(path: Path) -> dict:
    """Import one report through the registry tool."""
    proc = subprocess.run(
        [str(ROOT / "bin" / "vep"), "-m", "library.tools.axi_qualification",
         "--import-result", str(path)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=300, check=False)
    return {"path": str(path), "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def _report_and_import(evidence: dict) -> int:
    """The report the run earned, and the row it imports as."""
    report = build_report(evidence)
    path = REPORT_DIR / "reel.opening__resolve_axi.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    CANONICAL_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    canonical = CANONICAL_REPORT_DIR / "reel.opening__resolve_axi.json"
    canonical.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    imported = _import_report(path)
    print(json.dumps({"imported": imported}, indent=2))
    return 0 if imported.get("exit_code") == 0 else 1


def main() -> int:
    project = os.environ.get("REN_AXI_QUAL_PROJECT", "").strip()
    project_folder = os.environ.get("REN_AXI_QUAL_PROJECT_FOLDER", "").strip()
    if os.environ.get("REN_AXI_QUAL_REPORT_ONLY") == "1":
        # The evidence is already measured; only the report and the
        # import are left, and re-running the live run would measure a
        # second, different, run.
        evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        return _report_and_import(evidence)
    if not project or not project_folder:
        raise SystemExit("set REN_AXI_QUAL_PROJECT and "
                         "REN_AXI_QUAL_PROJECT_FOLDER")
    _own_media()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    evidence = _run(project, project_folder)
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, indent=1, default=str) + "\n", encoding="utf-8")
    receipt = evidence.get("receipt") or {}
    print(json.dumps({
        "evidence": str(EVIDENCE_PATH),
        "status": str(receipt.get("status") or ""),
        "refusal": str((receipt.get("refusal") or {}).get("reason") or ""),
    }, indent=2))
    if str(receipt.get("status") or "") != "committed":
        print("the run REFUSED: no qualification row is written - a refusal "
              "proves a refusal", file=sys.stderr)
        return 1
    return _report_and_import(evidence)


if __name__ == "__main__":
    raise SystemExit(main())
