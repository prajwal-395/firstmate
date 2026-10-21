"""ONE deliver and verify: a committed edit becomes a delivered file.

Why this module exists
-----------------------
Three routes produced a file before this one:

* `reel_deliver.deliver_reel` - a reel built by the reels process;
* step 6.01 -> 6.02 - a full pipeline run with an `assembly_manifest`;
* the AXI render route (`resolve-axi render inspect`) - whatever the
  operator had open, for inspection.

Only the first two brought the file to the delivery loudness, and only
the first wrote the delivery identity record. A committed edit on an
ARBITRARY timeline - the master timeline, a timeline a revision
patched, a timeline nobody proposed as a reel - therefore had no
engine path to a delivered file: the run had to hand-roll one (the
section-12 demo wrote `/tmp/bench/render_export.sh` by hand), and
every hand-rolled route skipped the mastering, the declarations and
the records. That is how a file 9.18 dB under target shipped with
nothing on record that said so.

So this module is the ONE path, and every route that produces a
deliverable comes through it:

    render (pre-master)  ->  master (delivery loudness)  ->  verify
                        ->  record (the identity record + the ledger)

What it will not do
-------------------
Render unasked. Nothing here is a DAG node, an operation, or reachable
from `run`, `build-reels` or either runner; the callers are the verbs
(`ren deliver`, `ren revise --deliver`) and the pipeline's own render
step. The captain's standing ruling (2026-09-09, 2026-09-10) is that a
render is the expensive thing they must ask for.

The record
----------
Every file this module delivers carries its delivery identity record
(`delivery_identity`): the frame rate, the range, the length, the
mastering report, the verification verdict, and the file's own digest.
A file that was never mastered says so in its own record, and the
delivery ledger names the file per timeline.
"""

from __future__ import annotations

import os

from library.tools.ren_refusal import RenRefusal


class DeliverRefused(RenRefusal):
    """The edit will not be delivered, and this says why."""


def _refusal(what: str, why: str, fix: str) -> DeliverRefused:
    return DeliverRefused(what, why, fix)


def deliver_timeline(project_folder: str, *, timeline_name: str,
                     timeline_id: str = "",
                     expected_project_name: str = "",
                     expected_seconds: float = 0.0,
                     settings: dict | None = None,
                     dest_dir: str = "",
                     output_name: str = "",
                     reel: int | None = None,
                     timeout_seconds: int = 1800,
                     source: str = "",
                     fps: float | None = None,
                     start_frame: int | None = None,
                     end_frame: int | None = None,
                     assignment_reel: dict | None = None,
                     assignment_id: str | None = None,
                     qc_passed: bool | None = None,
                     write_ledger: bool = True) -> dict:
    """Render, master, verify and RECORD one timeline. The core.

    The timeline is named; nothing is selected, moved or written on it.
    The render lands in the project's scratch as a PRE-MASTER file, the
    mastering pass brings it to the delivery spec's loudness target and
    true-peak ceiling, the verification measures the file that is
    handed over, and the identity record is written beside it.

    `source` names the route that asked for the delivery, and it goes
    into the record: a file delivered by `ren revise --deliver` says
    so, and one delivered by `ren deliver` says so.

    Returns the report every caller reads: `render`, `verification`,
    `delivered`, `report_path`.
    """
    from library.tools import reel_deliver
    from library.tools.execution import resolve_render
    from library.tools.master_loudness import master_render_report
    from library.tools.project_layout import Area, ProjectLayout

    # The render gate, read through `reel_deliver` so there is ONE gate:
    # a worker that cannot render says so here rather than half way
    # through the machine's time. An `unknown:` entry reports and never
    # gates - only a measured absence refuses.
    blocking = [entry for entry in reel_deliver.check_render()
                if reel_deliver.is_refusal(entry)]
    if blocking:
        raise _refusal(
            "this worker cannot render the delivery",
            "; ".join(blocking),
            "run `ren worker manifest` to see this worker's "
            "advertisement, and `ren setup` to repair the missing needs")

    project_folder = os.path.abspath(project_folder)
    settings = settings or reel_deliver.resolve_deliver_settings(
        project_folder)
    layout = ProjectLayout(project_folder)
    dest_dir = dest_dir or str(layout.write_dir(Area.EXPORTS))
    os.makedirs(dest_dir, exist_ok=True)
    if not output_name:
        output_name = reel_deliver.render_file_name(
            timeline_name, settings["naming"], settings["extension"])

    from library.tools.delivery_spec import resolve_delivery_spec_for_project
    delivery_spec = resolve_delivery_spec_for_project(project_folder)

    raw_dir = str(layout.write_dir(Area.SCRATCH, step="deliver"))
    stem = os.path.splitext(os.path.basename(output_name))[0]
    report = resolve_render.render_timeline(
        timeline_name=timeline_name,
        timeline_id=timeline_id,
        expected_project_name=expected_project_name,
        output_dir=raw_dir,
        output_name=stem + "_pre_master",
        fmt=settings["format"],
        codec=settings["codec"],
        timeout_seconds=timeout_seconds,
    )
    extension = (os.path.splitext(report["output_path"])[1]
                 or settings["extension"])
    final_path = os.path.join(dest_dir, stem + extension)
    # The delivery spec the project's named platform selects: the
    # master is normalized to THAT platform's loudness target and
    # true-peak ceiling, not the bare default - the same resolved spec
    # the plan and the gates read.
    report = master_render_report(
        report, final_path,
        target_lufs=delivery_spec.loudness_target_lufs,
        true_peak_ceiling=delivery_spec.true_peak_ceiling_dbtp)
    # Positional: fakes of verify_deliverable in the suite take
    # (*args), so a keyword here would break every one of them. The
    # spec goes with it, so the verdict measures the loudness the
    # master was normalized to - the verdict's own loudness leg is the
    # second ear on a route that skipped or weakened the master.
    verdict = reel_deliver.verify_deliverable(
        report["output_path"], expected_seconds,
        settings["width"], settings["height"],
        delivery_spec.codec, delivery_spec)

    full = {
        "timeline_name": timeline_name,
        "reel": reel,
        "timeline_fps": fps,
        "timeline_start_frame": start_frame,
        "timeline_end_frame": end_frame,
        "timeline_seconds": expected_seconds,
        "settings": settings,
        "render": report,
        "verification": verdict,
        "delivered": bool(verdict["passed"]),
        "source": source,
    }
    record = _write_identity(
        project_folder, full, source=source,
        assignment_reel=assignment_reel, assignment_id=assignment_id,
        qc_passed=qc_passed, write_ledger=write_ledger)
    full["record_path"] = record["record_path"]
    full["report_path"] = record["record_path"]
    if not verdict["passed"]:
        loudness = verdict.get("loudness") or {}
        raise _refusal(
            f"rendered {report['output_path']} but verification FAILED",
            f"duration={verdict['duration']['passed']}, "
            f"resolution={verdict['resolution']['passed']}, "
            f"audio={verdict['audio']['passed']}, "
            f"loudness={loudness.get('passed')}, "
            f"codec={verdict['codec']['passed']}, "
            f"content_present={verdict['content_present']}. "
            f"Report: {record['record_path']}",
            "read the record for the failing check, fix the timeline or "
            "the render settings, then deliver again - the file stays "
            "in exports but is not recorded as delivered")
    return full


def _write_identity(project_folder: str, full: dict, *, source: str,
                    assignment_reel: dict | None,
                    assignment_id: str | None,
                    qc_passed: bool | None,
                    write_ledger: bool = True) -> dict:
    """Write the delivery identity record, and the ledger's entry.

    The ledger (the project's record of WHAT was delivered) and the
    sidecar (the file's record of HOW) are written together: a
    delivery that has one and not the other is half a delivery, and the
    identity criterion reads both.
    """
    from library.tools import delivery_identity

    output_path = (full.get("verification") or {}).get("output_path") or ""
    if not output_path:
        # A verdict that names no file records no file. Writing a
        # record beside no file would put `.deliver.json` in whatever
        # directory the process happened to be in.
        return {"record_path": ""}
    record = delivery_identity.write_record(
        output_path,
        timeline_name=full.get("timeline_name") or "",
        reel=full.get("reel"),
        fps=full.get("timeline_fps"),
        seconds=full.get("timeline_seconds"),
        start_frame=full.get("timeline_start_frame"),
        end_frame=full.get("timeline_end_frame"),
        settings=full.get("settings"),
        render=full.get("render"),
        verification=full.get("verification"),
        delivered=bool(full.get("delivered")),
        stage=delivery_identity.STAGE_MASTERED,
        source=source,
    )
    try:
        if not write_ledger:
            return record
        from library.tools import deliver_all
        from library.tools.delivery_spec import (
            resolve_delivery_spec_for_project,
        )
        signature = deliver_all.delivery_signature(
            timeline_name=full.get("timeline_name") or "",
            settings=full.get("settings") or {},
            spec=deliver_all.spec_summary(
                resolve_delivery_spec_for_project(project_folder)),
            proposal_digest=deliver_all.proposal_sha(project_folder),
            assignment_reel=assignment_reel)
        deliver_all.record_ledger_entry(
            project_folder,
            timeline_name=full.get("timeline_name") or "",
            signature=signature,
            output_path=output_path,
            reel=int(full.get("reel") or 0),
            assignment_id=assignment_id,
            qc_passed=qc_passed)
    except Exception as failed:  # noqa: BLE001 - the delivery stands
        # The ledger is the batch's skip-check; the record is the
        # identity. A store that cannot be written must not sink a
        # delivery that already rendered and verified - the next
        # delivery re-renders rather than reading a ledger that lies.
        record["ledger_error"] = repr(failed)
    return record


def deliver_committed_edit(project_folder: str, *, timeline_name: str = "",
                           timeline_id: str = "",
                           reel: int | None = None,
                           output_dir: str = "",
                           file_name: str = "",
                           timeout_seconds: int = 1800,
                           source: str = "") -> dict:
    """Deliver a committed edit that no reel plan names.

    The edit is a live Resolve timeline; this finds it by its exact
    name (or its Resolve id), reads its clock, and delivers it through
    the one path. Nothing about the timeline is changed.

    A timeline that cannot be found, or that is ambiguous, is refused
    by name: delivering the wrong timeline is worse than delivering
    nothing.
    """
    from library.tools import reel_build, reel_deliver
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.resolve_lock import resolve_lease
    from library.tools.timeline_addressing import (
        AddressingError,
        resolve_timeline,
    )

    if not timeline_name and not timeline_id:
        raise _refusal(
            "no timeline named",
            "a delivery renders ONE timeline, and this one was not named",
            "name the timeline, e.g. `ren deliver <project> --timeline "
            "'Reel 03 - hook'`")

    project_folder = os.path.abspath(project_folder)
    settings = reel_deliver.resolve_deliver_settings(project_folder)
    with open(os.path.join(project_folder, "project.yaml"),
              encoding="utf-8") as handle:
        import yaml as _yaml
        resolve_name = ((_yaml.safe_load(handle).get("resolve") or {}).get(
            "project_name", os.path.basename(project_folder)))
    # The Resolve connection is refused outside the instance lease, so
    # the lookup below takes one short exclusive hold. The render that
    # follows selects under its own lease.
    with resolve_lease("deliver committed edit connect", exclusive=True):
        project = reel_build._connect_resolve_project(resolve_name)
        try:
            timeline = resolve_timeline(
                project, project_name=resolve_name,
                timeline_id=timeline_id or None,
                timeline_name=timeline_name or None)
        except AddressingError as exc:
            raise _refusal(
                f"no unique live timeline matches {timeline_name or timeline_id!r}",
                str(exc),
                "name the timeline exactly as Resolve holds it, or pass "
                "its Resolve id, then deliver again") from None
        live_name = timeline.GetName()
        fps, start, end = reel_deliver._timeline_clock(timeline)
        expected_seconds = reel_deliver._timeline_expected_seconds(timeline)
        from library.tools.timeline_addressing import timeline_address
        live_identity = timeline_address(timeline, resolve_name)

    # The project's declared maximum length, checked before a render
    # spends the machine on a file the platform brief already rules
    # out. One frame of allowance: a cut quantised to frames may end
    # on the frame just past it.
    from library.tools.delivery_spec import (
        duration_refusal,
        resolve_delivery_spec_for_project,
    )
    too_long = duration_refusal(
        resolve_delivery_spec_for_project(project_folder), expected_seconds,
        frame_seconds=1.0 / float(fps))
    if too_long:
        raise _refusal(
            "the edit is longer than the project allows",
            too_long,
            "trim the timeline to the declared maximum, or change "
            "pipeline.max_duration_seconds in project.yaml if this video "
            "may run longer")

    out_name = (file_name.strip() if file_name and file_name.strip()
                else reel_deliver.render_file_name(
                    live_name, settings["naming"], settings["extension"]))
    full = deliver_timeline(
        project_folder,
        timeline_name=live_name,
        timeline_id=live_identity.timeline_id,
        expected_project_name=resolve_name,
        expected_seconds=expected_seconds,
        settings=settings,
        dest_dir=output_dir or str(
            ProjectLayout(project_folder).write_dir(Area.EXPORTS)),
        output_name=out_name,
        reel=reel,
        timeout_seconds=timeout_seconds,
        source=source,
        fps=fps,
        start_frame=start,
        end_frame=end,
    )
    _queue_retention(project_folder, full)
    return full


def _queue_retention(project_folder: str, full: dict) -> str:
    """Queue the reclamation the delivery act implies, fail-soft.

    A delivery is an ACT, so the retention decision belongs with it -
    the same per-user policy a sign-off obeys. The job is queued, not
    run: the worker starts once nothing is rendering.
    """
    try:
        from library.tools import retention
        summary = retention.after_delivery(
            project_folder,
            lifecycle_event_id=retention.delivery_event_id(
                project_folder, [full["render"]["output_path"]]),
            outputs=[full["render"]["output_path"]],
            start_worker=False)
        return summary or ""
    except Exception:  # noqa: BLE001 - the delivery stands
        return ""


__all__ = [
    "DeliverRefused",
    "deliver_committed_edit",
    "deliver_timeline",
]
