"""Creative QA after delivery, with one bounded repair (plan item T17).

Runs after `ren deliver` (T16): `creative_qa.from_deliverable` on each
delivered file, fed with the 6.01 render review's subjective observations
read off the pipeline state. Then D4: one reversible repair for a
measurable presentation defect through `ren revise` (T10), then re-QC
with the same check. Editorial calls never enter the patch path: each
failing subjective observation becomes a question on the review page
(T13), and no edit is made for it.

Readers (AGENTS.md 10.4): a deterministic finding's reader is the repair
receipt below (or the block it leaves when nothing repairable remains);
a subjective finding's reader is the review-page question; a clean
render's reader is its own receipt. Nothing measured here goes quiet.

Only `lufs` joins the caption metrics in `AUTO_REPAIRABLE`, because only
those three have a declared target and a mechanical fix: the delivery
spec's loudness target, the measured safe area. Duration and black
frames stay creator-routed - an over-long render asks what to cut and a
black stretch asks which beat declared it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import sys
from pathlib import Path

FORMAT = "post_delivery_qa/1"
QA_FILENAME = "creative_qa.json"

STATUS_CLEAN = "clean"
STATUS_REPAIRED = "repaired"
STATUS_REPAIR_FAILED = "repair_failed"
STATUS_BLOCKED = "blocked"
STATUS_CREATOR_REVIEW = "creator_review"


class PostDeliveryQARefused(ValueError):
    """Post-delivery QA cannot run on this deliverable as asked."""


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _root(project) -> str:
    return str(Path(project).expanduser().resolve())


def store_path(project: str, *, write: bool = False) -> Path:
    """The QA receipt store: `pipeline_output/review/creative_qa.json`."""
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(_root(project))
    return (layout.write_path if write else layout.read_path)(Area.REVIEW, QA_FILENAME)


def read_store(project: str) -> dict:
    """Every QA receipt recorded for this project, oldest first."""
    try:
        doc = json.loads(store_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"format": FORMAT, "receipts": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("receipts"), list):
        return {"format": FORMAT, "receipts": []}
    return doc


def _append_receipt(project: str, receipt: dict) -> Path:
    doc = read_store(project)
    doc["receipts"].append(receipt)
    path = store_path(project, write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path


def render_review_from_state(project: str) -> dict:
    """The 6.01 render review off the pipeline state, or {}.

    Absent state is not a refusal: there are simply no subjective
    observations to feed in, and the receipt says so.
    """
    from library.tools import capability_outputs
    from library.tools.project_layout import ProjectLayout

    try:
        state = json.loads(
            ProjectLayout(_root(project)).pipeline_data_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    review = capability_outputs.value(state, "render.build", "render_review")
    return review if isinstance(review, dict) else {}


def default_intent_for(spec) -> dict:
    """The intent each deliverable check is judged against.

    Every key names the delivery spec row that declares the target - the
    provenance is the declaration, never an invented creative target.
    Captions are absent here: when subtitle data reaches the driver its
    caller extends this map with the brief's caption intent.
    """
    platform = getattr(spec, "platform", "?")
    provenance = f"delivery spec {platform}"
    return {
        "duration": {"key": "delivery.duration", "provenance": provenance},
        "black_frames": {
            "key": "delivery.no_undeclared_black",
            "provenance": provenance,
        },
        "lufs": {"key": "delivery.loudness", "provenance": provenance},
        "grade": {"key": "picture.grade_delivery", "provenance": provenance},
    }


def mechanical_note(finding, *, platform: str = "") -> str:
    """The revision note a repairable finding becomes.

    Fully specified from the finding's own measurement - its metric and
    measured detail, which name the value and the spec target - so the
    translation has nothing to ask. A note that needs a question is not
    mechanical and never reaches here.
    """
    where = f" for the {platform} delivery" if platform else ""
    return (
        f"Creative QA after delivery{where}: {finding.metric} measures "
        f"{finding.detail}. Normalize it to the delivery spec target; "
        f"change nothing else about the reel."
    )


def revise_routed_repair(project: str, timeline: str):
    """The default repair: the defect travels T10's patch path.

    Files the mechanical note as a `ren revise` revision and carries it
    as far as it goes now. The receipt names the revision; the inverse
    is that revision undone (`ren undo`), which is what makes the repair
    reversible. A revision the host has not translated yet stops at
    `awaiting_translation` - the receipt says so, and the re-measure
    below judges what actually changed, never what was asked for.
    """
    from library.tools import revise

    project = _root(project)

    def repair(finding) -> dict:
        note = mechanical_note(finding, platform=_platform_hint(project, timeline))
        result = revise.start(project, note, reel=timeline)
        if result.get("status") not in revise.LANDED:
            raise PostDeliveryQARefused(
                "the QA correction is not an AXI-verified timeline edit "
                f"(ren revise status={result.get('status')!r}); "
                "it remains a request for translation or review")
        return {
            "receipt": {
                "route": "ren revise",
                "revise_id": result.get("revise_id"),
                "status": result.get("status"),
                "reply": result.get("reply"),
                "note": note,
            },
            "inverse": {
                "revise_id": result.get("revise_id"),
                "undo": (f"ren undo {_root(project)} --act {result.get('revise_id')}"),
            },
        }

    return repair


def _platform_hint(project: str, timeline: str) -> str:
    """The platform this timeline was delivered for, or "".

    A hint for the note's prose only - the repair target always comes
    from the delivery spec at revise time, never from this string.
    """
    try:
        from library.tools import deliver_all

        return str(deliver_all.resolve_platforms(project)[0])
    except Exception:  # noqa: BLE001 - a hint is never worth a refusal
        return ""


def _recheck(video_path: str, spec, expected_duration: float, metric: str):
    """Re-run every deliverable detector after repair, not just its target."""
    from library.tools.deliverable_qc import run_deliverable_qc

    verdict = run_deliverable_qc(video_path, spec, expected_duration)
    results = [verdict.duration, verdict.black_frames, verdict.loudness,
               verdict.grade, *(verdict.captions or [])]
    by_metric = {row.metric: row for row in results}
    if metric not in by_metric:
        raise PostDeliveryQARefused(
            f"re-measure found no {metric!r} check in deliverable QC"
        )
    return results


def open_questions(project: str) -> list[dict]:
    """The QA questions the review page still waits on the creator for.

    One entry per failing subjective observation in the latest receipt
    for its timeline: a newer QA run supersedes the older questions for
    the same reel, so answered-by-a-new-render questions do not linger.
    """
    latest: dict = {}
    for receipt in read_store(project)["receipts"]:
        timeline = receipt.get("timeline") or ""
        stamp = receipt.get("at") or ""
        if timeline and stamp >= str(latest.get(timeline, {}).get("at", "")):
            latest[timeline] = receipt
    questions = []
    for timeline in sorted(latest):
        for entry in latest[timeline].get("questions") or []:
            questions.append({"timeline": timeline, **entry})
    return questions


def _already_posted(
    project: str, timeline: str, render_id: str, finding_id: str
) -> bool:
    for receipt in read_store(project)["receipts"]:
        if (receipt.get("timeline"), receipt.get("render_id")) != (timeline, render_id):
            continue
        if finding_id in {
            entry.get("finding_id") for entry in receipt.get("questions") or []
        }:
            return True
    return False


def _question_for(project: str, timeline: str, finding) -> dict:
    quoted = shlex.quote(_root(project))
    return {
        "finding_id": finding.id,
        "metric": finding.metric,
        "question": f"{finding.detail} - what should change?",
        "span": dict(finding.span or {}),
        "intent": dict(finding.intent or {}),
        "command": (
            f'ren revise {quoted} --reel {shlex.quote(timeline)} "<what to change>"'
        ),
    }


def run_for_deliverable(
    project,
    *,
    timeline: str,
    video_path: str,
    spec,
    expected_duration: float,
    assignment_id: str = "",
    render_id: str = "",
    intent_by_metric: dict | None = None,
    spans_by_metric: dict | None = None,
    grade_spans=None,
    subtitle_data=None,
    spine_blocks=None,
    declared_black_beats=None,
    render_review: dict | None = None,
    repair=None,
    repair_budget: int | None = None,
    reexport=None,
) -> dict:
    """QA one delivered file: report, at most one repair, questions.

    `repair` is the `creative_qa.attempt_repair` callable's repair half -
    `repair(finding)` returning `{"receipt", "inverse"}`; the default
    routes through `ren revise`. A landed edit must be followed by
    `reexport(attempt)`, returning a fresh `video_path` and optional
    `expected_duration`, before any detector is allowed to close it.
    Both seams are injectable, so tests need no Resolve or render.
    """
    from library.tools import creative_qa

    project = _root(project)
    if not timeline:
        raise PostDeliveryQARefused("no timeline was named")
    if render_review is None:
        render_review = render_review_from_state(project)
    intent = (
        dict(intent_by_metric)
        if intent_by_metric is not None
        else default_intent_for(spec)
    )
    if spec is None and intent_by_metric is None:
        raise PostDeliveryQARefused(
            "no delivery spec and no declared intent: QA judges a "
            "deliverable against its own spec, never a default"
        )

    report = creative_qa.from_deliverable(
        assignment_id or "unassigned",
        render_id or "unrendered",
        video_path,
        spec,
        expected_duration=float(expected_duration),
        intent_by_metric=intent,
        spans_by_metric=spans_by_metric,
        grade_spans=grade_spans,
        subtitle_data=subtitle_data,
        spine_blocks=spine_blocks,
        declared_black_beats=declared_black_beats,
        render_review=render_review or {},
        repair_budget=(
            creative_qa.DEFAULT_REPAIR_BUDGET
            if repair_budget is None
            else repair_budget
        ),
    )

    repair_record: dict = {}
    if not report.passed:
        repair_record = _repair_once(
            project,
            timeline,
            report,
            video_path,
            spec,
            float(expected_duration),
            repair=repair,
            reexport=reexport,
        )

    questions = []
    if report.needs_creator:
        render_id_value = render_id or "unrendered"
        for finding in report.subjective_observations:
            if finding.passed:
                continue
            if _already_posted(project, timeline, render_id_value, finding.id):
                continue
            questions.append(_question_for(project, timeline, finding))

    verified_export = (repair_record.get("reexport") or {}).get("video_path")
    if verified_export:
        video_path = verified_export

    if repair_record.get("attempt") and repair_record.get("verified"):
        regressions = repair_record["verified"].get("regressions") or []
        status = (STATUS_CLEAN if report.passed and not regressions else
                  STATUS_REPAIR_FAILED if (
                      report.blocks_delivery
                      or regressions) else
                  STATUS_CREATOR_REVIEW if report.needs_creator else
                  STATUS_REPAIRED)
    elif repair_record.get("error"):
        status = STATUS_REPAIR_FAILED
    elif report.blocks_delivery:
        status = STATUS_BLOCKED
    elif report.needs_creator:
        status = STATUS_CREATOR_REVIEW
    else:
        status = STATUS_CLEAN

    receipt = {
        "format": FORMAT,
        "at": _now(),
        "project": project,
        "timeline": timeline,
        "render_id": render_id or "unrendered",
        "assignment_id": assignment_id or "unassigned",
        "video_path": video_path,
        "render_review_present": bool(render_review),
        "report": report.as_dict(),
        "repair": repair_record,
        "questions": questions,
        "status": status,
    }
    receipt_path = _append_receipt(project, receipt)
    return {
        "status": status,
        "timeline": timeline,
        "render_id": receipt["render_id"],
        "report": report.as_dict(),
        "repair": repair_record,
        "questions": questions,
        "receipt_path": str(receipt_path),
    }


def _repair_once(
    project: str,
    timeline: str,
    report,
    video_path: str,
    spec,
    expected_duration: float,
    repair=None,
    reexport=None,
) -> dict:
    """The single bounded repair: first `repair_once` finding, then re-QC."""
    from library.tools import creative_qa

    repairable = [
        finding for finding in report.findings if finding.action == "repair_once"
    ]
    if not repairable:
        return {}
    finding = repairable[0]
    repair = repair or revise_routed_repair(project, timeline)
    try:
        attempt = creative_qa.attempt_repair(report, finding.id, repair)
    except Exception as exc:  # noqa: BLE001 - a refused repair is reported
        return {
            "finding_id": finding.id,
            "repair_class": finding.repair_class,
            "error": f"{type(exc).__name__}: {exc}",
        }
    fresh_path = ""
    fresh_duration = expected_duration
    try:
        if reexport is None:
            raise PostDeliveryQARefused(
                "the AXI correction has no fresh re-export callback; "
                "the prior file cannot verify a timeline change")
        export = reexport(attempt)
        if not isinstance(export, dict) or not export.get("video_path"):
            raise PostDeliveryQARefused(
                "the correction re-export did not identify a fresh deliverable")
        fresh_path = str(export["video_path"])
        fresh_duration = float(export.get("expected_duration", expected_duration))
        fresh = _recheck(fresh_path, spec, fresh_duration, finding.metric)
    except Exception as exc:  # noqa: BLE001 - an unmeasured repair is reported
        return {
            "finding_id": finding.id,
            "repair_class": finding.repair_class,
            "attempt": attempt,
            **({"reexport": {"video_path": fresh_path,
                              "expected_duration": fresh_duration}}
               if fresh_path else {}),
            "error": f"re-measure failed: {type(exc).__name__}: {exc}",
        }
    try:
        verification = creative_qa.record_repair_measurements(
            report, finding.id, fresh)
    except Exception as exc:  # noqa: BLE001 - incomplete evidence stays open
        return {
            "finding_id": finding.id,
            "repair_class": finding.repair_class,
            "attempt": attempt,
            "reexport": {"video_path": fresh_path,
                         "expected_duration": fresh_duration},
            "error": f"re-measure evidence is incomplete: {type(exc).__name__}: {exc}",
        }
    try:
        report.render_id = f"{timeline}@{os.stat(fresh_path).st_mtime_ns}"
    except OSError:
        report.render_id = f"{timeline}@{Path(fresh_path).name}"
    attempt = next(
        entry for entry in report.repair_attempts if entry["finding_id"] == finding.id
    )
    return {
        "finding_id": finding.id,
        "repair_class": finding.repair_class,
        "attempt": attempt,
        "reexport": {"video_path": fresh_path,
                     "expected_duration": fresh_duration},
        "verified": {
            "passed": bool(verification["target"].passed),
            "detail": verification["target"].detail,
            "all_criteria_passed": verification["all_criteria_passed"],
            "regressions": verification["regressions"],
        },
    }


def run_for_project(project, *, reel: int | None = None) -> dict:
    """QA every delivered reel of this project (or one `--reel`).

    Reads the delivery ledger T16 wrote: the file on disk, its sidecar
    `.deliver.json` for the expected duration, and the spec row the
    delivery served. A reel with no delivery on record is listed, never
    guessed.
    """
    from library.tools import deliver_all

    project = _root(project)
    wanted = deliver_all.resolve_platforms(project)
    jobs = deliver_all.delivery_jobs(
        wanted, tracks=deliver_all.manifest_tracks(project)
    )
    spec = jobs[0]["spec"]
    ledger = deliver_all.read_ledger(project)
    if reel is not None:
        wanted_timelines = [
            name
            for name, entry in ledger.items()
            if int((entry or {}).get("reel") or -1) == reel
        ]
        if not wanted_timelines:
            raise PostDeliveryQARefused(f"reel {reel} has no delivery on record")
    else:
        wanted_timelines = sorted(ledger)
    if not wanted_timelines:
        raise PostDeliveryQARefused(
            f"nothing delivered yet - creative QA judges a delivered "
            f"file; run `ren deliver {shlex.quote(project)}` first"
        )

    review = render_review_from_state(project)
    ran, skipped = [], []
    for timeline in wanted_timelines:
        entry = ledger.get(timeline) or {}
        output = entry.get("output_path") or ""
        if not output or not os.path.isfile(output):
            skipped.append(
                {"timeline": timeline, "reason": "the recorded file is gone"}
            )
            continue
        sidecar = os.path.join(
            os.path.dirname(output),
            os.path.splitext(os.path.basename(output))[0] + ".deliver.json",
        )
        try:
            expected = float(
                json.loads(Path(sidecar).read_text(encoding="utf-8"))[
                    "timeline_seconds"
                ]
            )
        except (OSError, ValueError, KeyError, TypeError):
            skipped.append(
                {"timeline": timeline, "reason": f"no expected duration in {sidecar}"}
            )
            continue
        try:
            stat = os.stat(output)
            render_id = f"{timeline}@{stat.st_mtime_ns}"
        except OSError:
            render_id = f"{timeline}@{Path(output).name}"

        reel_number = entry.get("reel")
        recorded_output = output

        def reexport(_attempt, reel=reel_number,
                     output_path=recorded_output):
            if reel is None:
                raise PostDeliveryQARefused(
                    "the delivery ledger does not name the reel number; "
                    "cannot re-export the corrected timeline")
            from library.tools.reel_deliver import deliver_reel

            delivered = deliver_reel(
                project, int(reel), output_dir=os.path.dirname(output_path),
                file_name=os.path.basename(output_path))
            return {
                "video_path": delivered["render"]["output_path"],
                "expected_duration": delivered["timeline_seconds"],
                "delivery": delivered,
            }

        ran.append(
            run_for_deliverable(
                project,
                timeline=timeline,
                video_path=output,
                spec=spec,
                expected_duration=expected,
                assignment_id=str(entry.get("assignment_id") or ""),
                render_id=render_id,
                render_review=review,
                reexport=reexport,
            )
        )
    return {"platforms": wanted, "deliverables": ran, "skipped": skipped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren qa",
        description="Creative QA after delivery: run the deliverable "
        "checks against the spec, attempt one reversible "
        "repair through `ren revise`, and carry editorial "
        "calls to the review page as questions.",
    )
    parser.add_argument("project", help="project folder path")
    parser.add_argument(
        "--reel", type=int, default=None, help="QA only this delivered reel number"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the full QA record as JSON"
    )
    args = parser.parse_args(argv)
    try:
        result = run_for_project(args.project, reel=args.reel)
    except PostDeliveryQARefused as refused:
        print(f"ren qa: {refused}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    for entry in result["deliverables"]:
        line = f"{entry['timeline']}: {entry['status']}"
        if entry["repair"].get("attempt"):
            line += (
                f" (one {entry['repair'].get('repair_class')} repair, "
                f"re-measured {'pass' if (entry['repair'].get('verified') or {}).get('passed') else 'fail'})"
            )
        if entry["questions"]:
            line += f" ({len(entry['questions'])} question(s) for the review page)"
        print(line)
    for skipped in result["skipped"]:
        print(f"{skipped['timeline']}: skipped ({skipped['reason']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
