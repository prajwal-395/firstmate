"""`ren deliver <project>`: every approved reel, one receipt, QC, the file in front of you.

Plan item T16 of the carefree-workflow plan. With no reel number,
`deliver-reel` delivers every approved (signed-off) reel instead of
refusing for a missing number:

* the platform comes from the series or the project -
  `delivery_spec.delivery_platform_name`, the precedence that module
  already owns - with `--platform` extras for the run;
* `deliverable_set` is planned only when the requested specs differ.
  pmk posts one 1080x1920 master to three platforms whose specs agree
  on every render-governing field, so the three are ONE job and each
  reel renders once; when the specs differ, each further job is
  reframed off the rendered master picture (`deliver_variants`) into
  its own variant file instead of a named-missing receipt row;
* every produced file runs `deliverable_qc` against its own spec;
* every owning assignment gets
  `assignment_artifacts.publish_delivery_receipt`;
* the exports folder is revealed in Finder (`open -R`, opt-out), and
  the run prints where each file is.

What is NOT here
----------------
The render itself stays `reel_deliver.deliver_reel` - the explicit
verb, the ONLY path that renders a reel. This module is a batch over
it, called only from `manage_project.cmd_deliver_reel` when no reel
number is given. Nothing in `build-reels`, `run`, either DAG or the
operation registry reaches this module either: a batch that started
unasked is the same defect as a single render that started unasked.

A second invocation with nothing changed re-renders nothing. The
check is fail-closed in the `reel_rebuild_need` style: a reel is
skipped only on a positive match of the delivery signature AND the
file on disk; every answer that is not a match renders. The explicit
single-reel verb always renders - an explicit ask is not "nothing
changed" - but it records the ledger, so the batch sees it.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from library.tools.reel_deliver import DeliverRefused
from library.tools.stable_json import dumps_stable

DELIVER_LEDGER_FILENAME = "deliver_ledger.json"
"""`exports/deliver_ledger.json`: the last delivery signature per reel.

One entry per timeline name: the signature, the file it produced and
the stat of that file. The skip check reads it; both deliver paths
write it after a verified delivery.
"""


# ── Which reels ──────────────────────────────────────────────────


def approved_moments(project_folder: str) -> list:
    """Proposal-APPROVED moments with a live sign-off, in reel order.

    "Approved" here is the assignment word: the captain signed the
    built reel off, not just the plan. A moment the plan approves but
    no sign-off covers is reported by `skipped_moments`, never
    delivered - delivering an unsigned build is the unasked render by
    another name.
    """
    from library.tools import reel_proposal, reel_signoff

    path = reel_proposal.proposal_path(project_folder)
    if not Path(path).is_file():
        raise DeliverRefused(
            f"no reel plan at {path}",
            "nothing has been proposed for this project, so there is "
            "no approved reel to deliver",
            "run `ren propose <project>` first, then deliver")
    try:
        moments = reel_proposal.read_proposal(path)
    except reel_proposal.ProposalError as exc:
        raise DeliverRefused(
            f"the reel plan cannot be read: {exc}",
            "delivering from an unreadable plan would guess which "
            "reels to render",
            "run `ren propose <project>` again to republish the plan") from None
    deliverable = []
    for moment in sorted(reel_proposal.approved_only(moments),
                         key=lambda m: int(m.number)):
        if reel_signoff.signoff_for(project_folder, moment.timeline_name) is None:
            continue
        deliverable.append(reel_proposal.ensure_moment_id(moment))
    return deliverable


def skipped_moments(project_folder: str) -> list[dict]:
    """Every moment the batch will NOT deliver, and why, in reel order."""
    from library.tools import reel_proposal, reel_signoff

    path = reel_proposal.proposal_path(project_folder)
    if not Path(path).is_file():
        return []
    try:
        moments = reel_proposal.read_proposal(path)
    except reel_proposal.ProposalError:
        return []
    approved_names = {m.timeline_name for m in approved_moments(project_folder)}
    rows = []
    for moment in sorted(moments, key=lambda m: int(m.number)):
        if moment.timeline_name in approved_names:
            continue
        if moment.approval is not reel_proposal.Approval.APPROVED:
            rows.append({"number": int(moment.number),
                         "name": moment.timeline_name,
                         "reason": f"the plan holds it at "
                                   f"{moment.approval.value}, not approved"})
        else:
            rows.append({"number": int(moment.number),
                         "name": moment.timeline_name,
                         "reason": "the plan approves it but no sign-off "
                                   "covers the built reel"})
    return rows


def moment_for_number(project_folder: str, reel: int):
    """The proposal moment for one reel number, or None."""
    from library.tools import reel_proposal

    path = reel_proposal.proposal_path(project_folder)
    if not Path(path).is_file():
        return None
    try:
        moments = reel_proposal.read_proposal(path)
    except reel_proposal.ProposalError:
        return None
    return next((reel_proposal.ensure_moment_id(m)
                 for m in moments if int(m.number) == int(reel)), None)


# ── Platforms and jobs ───────────────────────────────────────────


def resolve_platforms(project_folder: str,
                      extra: tuple = ()) -> list[str]:
    """The platforms this delivery serves, first-seen order, deduped.

    The head is always what the series or the project declares
    (`delivery_spec.delivery_platform_name`); `--platform` extras
    follow. An unknown name refuses at the declaration that named it -
    a silent fallback is how a master ships to the wrong spec.
    """
    from library.tools.delivery_spec import (
        delivery_platform_name,
        resolve_delivery_spec,
        spec_names,
    )

    names = [delivery_platform_name(project_folder)]
    names.extend(n.strip() for n in (extra or ()) if n and n.strip())
    platforms = list(dict.fromkeys(names))
    unknown = [name for name in platforms if _unknown(name)]
    if unknown:
        raise DeliverRefused(
            f"unknown delivery platform(s) {unknown}",
            "a silent fallback is how a master ships to the wrong "
            "platform's spec",
            f"name one of {spec_names()}: "
            f"`ren deliver <project> --platform <name>`")
    for name in platforms:
        resolve_delivery_spec(name)  # raise here, naming the platform
    return platforms


def _unknown(name: str) -> bool:
    from library.tools.delivery_spec import DELIVERY_SPECS

    return name not in DELIVERY_SPECS


def delivery_jobs(platforms: list[str],
                  tracks: dict | None = None) -> list[dict]:
    """One render job per distinct render the platforms require.

    `deliverable_set` is planned ONLY when the specs differ: platforms
    whose specs agree on every render-governing field share one job
    (pmk's three verticals are one master). Each job is
    `{"serves": (...), "spec": DeliverySpec, "needs_reframe": bool,
    "source": str}`. The first job's representative is the project's
    own platform - that is the file the batch renders; further jobs
    are reframed off it by `deliver_variants.render_variant`.
    """
    from library.tools.deliverable_matrix import render_key
    from library.tools.delivery_spec import resolve_delivery_spec

    specs = [(name, resolve_delivery_spec(name)) for name in platforms]
    keys = {render_key(spec) for _, spec in specs}
    if len(keys) == 1:
        name, spec = specs[0]
        return [{"serves": tuple(n for n, _ in specs), "spec": spec,
                 "needs_reframe": False, "source": "master_render",
                 "representative": name}]
    from library.tools.deliverable_set import plan_deliverable_set

    settled = plan_deliverable_set(platforms, tracks or {})
    jobs = []
    for job in settled.video.jobs:
        jobs.append({"serves": job.serves, "spec": job.spec,
                     "needs_reframe": job.needs_reframe,
                     "source": job.source,
                     "representative": job.serves[0]})
    return jobs


def manifest_tracks(project_folder: str) -> dict:
    """The manifest's mix-track mapping, or {} when there is none.

    Read best-effort off the compiled manifest for the stem half of
    `deliverable_set`. An absent mix plans no stems - that is a fact,
    not an error (`stem_plan.plan_stems` owns the rule).
    """
    from library.tools.project_layout import ProjectLayout

    path = ProjectLayout(project_folder).pipeline_data_path
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    manifest_path = None
    try:
        from library.tools import capability_outputs

        manifest_path = (capability_outputs.read(state, "compile_manifest")
                         .get("manifest_path"))
    except Exception:  # noqa: BLE001 - best effort, absence plans no stems
        return {}
    if not manifest_path:
        return {}
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    tracks = manifest.get("tracks")
    return tracks if isinstance(tracks, dict) else {}


def spec_summary(spec) -> dict:
    """A DeliverySpec as plain data for receipts and the ledger."""
    return {"platform": spec.platform, "width": spec.width,
            "height": spec.height, "fps": spec.fps, "codec": spec.codec,
            "container": spec.container,
            "loudness_target_lufs": spec.loudness_target_lufs,
            "caption_format": spec.caption_format}


# ── The fail-closed skip ─────────────────────────────────────────


def proposal_sha(project_folder: str) -> str:
    """sha256 of the proposal file's bytes, or a marker for absence.

    Absence digests as `missing:` - which cannot equal a recorded
    digest of real bytes, so a vanished plan renders rather than
    reading as unchanged.
    """
    from library.tools import reel_proposal

    try:
        data = Path(reel_proposal.proposal_path(project_folder)).read_bytes()
    except OSError:
        return "missing:proposal"
    return hashlib.sha256(data).hexdigest()


def owning_assignment_reel(project_folder: str,
                           moment_id: str) -> tuple[dict | None, dict | None]:
    """The assignment record and reel entry owning one moment, if any.

    The newest record holding the moment id wins - the same rule
    `assignment_spine` applies. None when no record owns it: untracked
    work always re-renders (fail-closed), never invents a history.
    """
    from library.tools import assignment_lifecycle

    root = str(Path(project_folder).expanduser().resolve())
    holding = [r for r in assignment_lifecycle.list_records(root)
               if moment_id in (r.get("reels") or {})]
    if not holding:
        return None, None
    record = max(holding,
                 key=lambda r: (r.get("updated_at") or "",
                                r.get("created_at") or ""))
    return record, (record.get("reels") or {}).get(moment_id)


def _settings_identity(settings: dict) -> dict:
    """The halves of the deliver settings that decide the file's bytes."""
    return {key: settings.get(key) for key in (
        "format", "codec", "width", "height", "naming", "extension",
        "output_transform", "preset_declared", "naming_declared")}


def _assignment_identity(entry: dict | None) -> dict | None:
    """The build-side identity of an assignment reel entry, or None.

    Only what the BUILD decided: number, name and build time. The
    delivery itself mutates the entry (`status` signed_off -> delivered,
    `delivered_at`) - a signature that carried those would differ after
    the delivery it records, so every second run re-rendered. A
    rebuild sets a new `built_at`, so it still renders.
    """
    if entry is None:
        return None
    return {"number": entry.get("number"), "name": entry.get("name"),
            "built_at": entry.get("built_at")}


def delivery_signature(*, timeline_name: str, settings: dict,
                       spec: dict, proposal_digest: str,
                       assignment_reel: dict | None) -> str:
    """What "nothing changed" means for one delivered reel.

    The exact timeline name, the byte-deciding deliver settings, the
    platform spec summary, the proposal bytes, and the assignment
    reel's build identity - a rebuild sets a new `built_at`, so it
    renders even where every other half agrees. `None` assignment
    reads as untracked, never as a match: it is still signed, but the
    skip needs an owner.
    """
    body = {
        "timeline_name": timeline_name,
        "settings": _settings_identity(settings),
        # The spec half arrives already summarised (see spec_summary):
        # what decides shared-vs-variant is the render-governing
        # fields, and those are what the summary carries.
        "spec": dict(spec),
        "proposal": proposal_digest,
        "assignment_reel": _assignment_identity(assignment_reel),
    }
    return hashlib.sha256(dumps_stable(body).encode("utf-8")).hexdigest()


def ledger_path(project_folder: str) -> Path:
    """`exports/deliver_ledger.json`, for reading. Nothing is created."""
    from library.tools.project_layout import Area, ProjectLayout

    return ProjectLayout(project_folder).read_path(
        Area.EXPORTS, DELIVER_LEDGER_FILENAME)


def read_ledger(project_folder: str) -> dict:
    """The last delivery signatures, or {} when there are none."""
    try:
        data = json.loads(ledger_path(project_folder).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_ledger(project_folder: str, ledger: dict) -> Path:
    """Persist the delivery signatures. One atomic write."""
    from library.tools.project_layout import Area, ProjectLayout

    path = ProjectLayout(project_folder).write_path(
        Area.EXPORTS, DELIVER_LEDGER_FILENAME)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps_stable(ledger) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def needs_render(project_folder: str, *, timeline_name: str,
                 settings: dict, spec: dict, proposal_digest: str,
                 assignment_reel: dict | None,
                 ledger: dict | None = None) -> tuple[bool, str]:
    """Whether this reel needs a Resolve pass. FAIL-CLOSED.

    Six ways to answer "render" and one to answer "up to date", and
    the reason always travels: a skip whose grounds are invisible is
    indistinguishable from a reel silently dropped.
    """
    ledger = read_ledger(project_folder) if ledger is None else ledger
    signature = delivery_signature(
        timeline_name=timeline_name, settings=settings, spec=spec,
        proposal_digest=proposal_digest, assignment_reel=assignment_reel)
    recorded = ledger.get(timeline_name)
    if assignment_reel is None:
        return True, ("no assignment owns this reel - untracked work "
                      "always re-renders rather than inventing a history")
    if not isinstance(recorded, dict) or not recorded.get("signature"):
        return True, ("no delivery signature on record for this reel - "
                      "nothing to compare, so it renders")
    if recorded.get("signature") != signature:
        return True, ("the delivery signature changed since the delivery "
                      "on record (settings, spec, plan or build)")
    output = recorded.get("output_path") or ""
    if not output or not os.path.isfile(output):
        return True, (f"the recorded file is gone ({output or 'no path'}) - "
                       "a missing deliverable is not an up-to-date one")
    try:
        stat = os.stat(output)
    except OSError:
        return True, (f"the recorded file cannot be statted ({output}) - "
                       "an unreadable deliverable is never an unchanged one")
    if (recorded.get("size_bytes") != stat.st_size
            or recorded.get("mtime_ns") != stat.st_mtime_ns):
        return True, ("the file on disk is not the one the signature was "
                      "taken for - it was replaced outside delivery")
    return False, ("signature and file both match the recorded delivery - "
                   "rendering again would produce the same bytes")


def record_ledger_entry(project_folder: str, *, timeline_name: str,
                        signature: str, output_path: str,
                        reel: int, assignment_id: str | None,
                        qc_passed: bool | None) -> dict:
    """Note a verified delivery in the ledger. Returns the ledger."""
    try:
        stat = os.stat(output_path)
        size, mtime = stat.st_size, stat.st_mtime_ns
    except OSError:
        size, mtime = None, None
    ledger = read_ledger(project_folder)
    ledger[timeline_name] = {
        "signature": signature, "output_path": output_path,
        "size_bytes": size, "mtime_ns": mtime, "reel": int(reel),
        "assignment_id": assignment_id,
        "qc_passed": qc_passed,
    }
    write_ledger(project_folder, ledger)
    return ledger


# ── QC ───────────────────────────────────────────────────────────


def _record(result) -> dict:
    return {"metric": result.metric, "passed": bool(result.passed),
            "value": result.value, "threshold": result.threshold,
            "severity": result.severity, "detail": result.detail}


def run_qc(output_path: str, spec, expected_seconds: float,
           *, declared_silence_spans=None) -> dict:
    """`deliverable_qc`'s verdict on one delivered file, as plain data.

    Duration is judged against what the delivery itself verified moments
    earlier - a stability re-read proving the file on disk still
    measures the same, not a second plan check (the timeline-exact
    duration gate already ran inside `deliver_reel`). Loudness is
    measured here too, by the same `deliverable_qc` check the
    delivery verdict now uses, so the two cannot grade the same number
    two ways. Grade reports, never gates.

    The AUDIO SPEC is measured here too - the same three legs, against
    the same declared silence, so the batch's receipt and the delivery
    verdict cannot disagree about a file's audio. `declared_silence_spans`
    are the seconds where silence under picture is DELIBERATE, read
    from the project's own declarations; the batch computes them per
    reel and passes them here.
    """
    from library.tools.deliverable_qc import run_deliverable_qc

    verdict = run_deliverable_qc(
        output_path, spec, float(expected_seconds),
        declared_silence_spans=declared_silence_spans)
    return {"platform": verdict.platform, "passed": bool(verdict.passed),
            "duration": _record(verdict.duration),
            "black_frames": _record(verdict.black_frames),
            "loudness": _record(verdict.loudness),
            "grade": _record(verdict.grade),
            "captions": ([_record(row) for row in verdict.captions]
                         if verdict.captions is not None else None),
            "audio": {
                "loudness": _record(verdict.audio.loudness),
                "silence": _record(verdict.audio.silence),
                "channels": _record(verdict.audio.channels),
                "passed": bool(verdict.audio.passed),
            }}


def _declared_silence(project_folder: str, timeline_name: str,
                      duration_seconds: float, fps: float) -> list:
    """Where this reel's silence is DELIBERATE, from its declarations.

    The same spans the delivery verdict measured against, so the batch's
    receipt and the delivery's own record grade one file one way.
    """
    from library.tools.declared_silence import declared_silence_spans

    return declared_silence_spans(
        project_folder, timeline_name,
        duration_seconds=duration_seconds, fps=fps)


# ── The batch ────────────────────────────────────────────────────


def _expected_seconds(deliver_report: dict, moment) -> float:
    """What QC judges duration against: the verified measure, else plan."""
    try:
        measured = float(deliver_report["verification"]["duration"]["value"])
        if measured > 0:
            return measured
    except (KeyError, TypeError, ValueError):
        pass
    return float(moment.total_duration)


def _batch_renderer(deliver_one):
    """The renderer the batch uses, with the reclamation left to the batch.

    The batch owns the reclaim for every render inside it, so one job
    covers the masters and the variants together. A stand-in renderer
    is left exactly as it is: a double is not the real renderer, and
    the real renderer's own contract is not the double's business.
    """
    from library.tools import reel_deliver

    if deliver_one is reel_deliver.deliver_reel:
        return functools.partial(reel_deliver.deliver_reel,
                                 retention_job=False)
    return deliver_one


def deliver_all_approved(
        project_folder: str, *,
        platforms: tuple = (),
        output_dir: str = "",
        timeout_seconds: int = 1800,
        deliver_one: Callable | None = None,
        qc_one: Callable | None = None,
        reframe_one: Callable | None = None,
        face_track_for: Callable | None = None) -> dict:
    """Deliver every approved (signed-off) reel. Returns the batch report.

    `deliver_one` defaults to `reel_deliver.deliver_reel`, `qc_one`
    to `run_qc` and `reframe_one` to
    `deliver_variants.render_variant` - the seams the suite fakes.
    `face_track_for`, when given, is called as
    `face_track_for(timeline_name, duration_seconds, sample_rate_hz)`
    and returns a face-centre track over the master seconds or None;
    without it every variant crop is centred and says so. A reel whose
    render is refused does not stop the batch: its error is recorded and the
    batch continues; when nothing at all was delivered the first
    refusal raises, so the CLI exits as a refusal rather than a quiet
    partial. Receipts cover what was delivered (rendered or already up
    to date); a run that delivered nothing writes none.
    """
    from library.tools import assignment_spine, reel_deliver
    from library.tools import deliver_variants as _variants
    from library.tools.project_layout import Area, ProjectLayout

    project_folder = os.path.abspath(project_folder)
    deliver_one = _batch_renderer(deliver_one or reel_deliver.deliver_reel)
    qc_one = qc_one or run_qc
    reframe_one = reframe_one or _variants.render_variant

    moments = approved_moments(project_folder)
    if not moments:
        raise DeliverRefused(
            "no approved reel to deliver",
            "every moment is unapproved, unsigned, or there is no plan",
            "approve (`ren approve`) and sign off (`ren sign-off`) a "
            "built reel first, then deliver")

    wanted = resolve_platforms(project_folder, extra=platforms)
    jobs = delivery_jobs(wanted, tracks=manifest_tracks(project_folder))
    master_job = jobs[0]
    master_spec = master_job["spec"]
    spec_data = spec_summary(master_spec)

    from library.tools.reel_deliver import resolve_deliver_settings

    settings = resolve_deliver_settings(project_folder)
    digest = proposal_sha(project_folder)
    ledger = read_ledger(project_folder)
    layout = ProjectLayout(project_folder)
    dest_dir = output_dir or str(layout.write_dir(Area.EXPORTS))

    delivered: list[dict] = []
    up_to_date: list[dict] = []
    failed: list[dict] = []
    first_error: Optional[Exception] = None
    # Per reel, what the master pass left behind: the rendered (or
    # recorded) master file, the full deliver report when one ran, and
    # the owning assignment halves the variant pass reuses. A reel the
    # master pass refused has no file and no entry here, so its
    # variants read as pending rather than as silent skips.
    master_outputs: dict[int, str] = {}
    master_fulls: dict[int, dict | None] = {}
    moment_state: dict[int, dict] = {}

    for moment in moments:
        number = int(moment.number)
        timeline = moment.timeline_name
        record, reel_entry = owning_assignment_reel(
            project_folder, moment.moment_id)
        render, why = needs_render(
            project_folder, timeline_name=timeline, settings=settings,
            spec=spec_data, proposal_digest=digest,
            assignment_reel=reel_entry, ledger=ledger)
        if not render:
            prior = ledger[timeline]
            up_to_date.append({
                "reel": number, "timeline_name": timeline,
                "output_path": prior.get("output_path"),
                "assignment_id": (prior.get("assignment_id")
                                  or (record or {}).get("assignment_id")),
                "qc": {"passed": prior.get("qc_passed"),
                       "from_prior_delivery": True},
                "reason": why,
            })
            master_outputs[number] = prior.get("output_path") or ""
            master_fulls[number] = None
            moment_state[number] = {
                "record": record, "reel_entry": reel_entry,
                "moment": moment}
            continue
        act = assignment_spine.begin_deliver(project_folder, number)
        try:
            full = deliver_one(
                project_folder, number, output_dir=output_dir,
                timeout_seconds=timeout_seconds)
        except DeliverRefused as refused:
            assignment_spine.end_act(act, ok=False, detail=refused.what)
            failed.append({"reel": number, "timeline_name": timeline,
                           "error": refused.what})
            if first_error is None:
                first_error = refused
            continue
        except KeyboardInterrupt:
            assignment_spine.end_act(act, ok=False, status="interrupted")
            raise
        except BaseException as crashed:  # noqa: BLE001 - ledgered, re-raised
            assignment_spine.end_act(act, ok=False, status="failed",
                                     detail=repr(crashed))
            raise
        assignment_spine.end_act(act, ok=True)
        verification = full["verification"]
        out_path = verification["output_path"]
        expected = _expected_seconds(full, moment)
        silence_spans = _declared_silence(
            project_folder, timeline, expected,
            float(full.get("timeline_fps") or 0.0))
        try:
            qc = qc_one(out_path, master_spec, expected,
                        declared_silence_spans=silence_spans)
            qc_passed = bool(qc.get("passed"))
        except Exception as exc:  # noqa: BLE001 - QC measures, never sinks
            qc = {"passed": False, "error": repr(exc)}
            qc_passed = False
        signature = delivery_signature(
            timeline_name=timeline, settings=settings, spec=spec_data,
            proposal_digest=digest, assignment_reel=reel_entry)
        assignment_id = (record or {}).get("assignment_id")
        ledger = record_ledger_entry(
            project_folder, timeline_name=timeline, signature=signature,
            output_path=out_path, reel=number,
            assignment_id=assignment_id, qc_passed=qc_passed)
        delivered.append({
            "reel": number, "timeline_name": timeline,
            "output_path": out_path, "report_path": full.get("report_path"),
            "assignment_id": assignment_id,
            "verification": {k: verification.get(k) for k in (
                "duration", "resolution", "audio", "content_present",
                "opens_on_black", "passed")},
            "qc": qc,
        })
        master_outputs[number] = out_path
        master_fulls[number] = full
        moment_state[number] = {
            "record": record, "reel_entry": reel_entry,
            "moment": moment}

    if not delivered and not up_to_date:
        assert first_error is not None
        raise first_error

    # Further jobs (specs differed): each is reframed off its reel's
    # rendered master picture into its own variant file - the
    # `deliverable_set` video plan made real. Variants skip and
    # re-render independently of the master and of each other: the
    # ledger key carries the served platforms, and the signature the
    # variant spec. A variant whose master is missing or whose reframe
    # fails is recorded as pending/failed, never as a quiet skip.
    variants: list[dict] = []
    variants_up_to_date: list[dict] = []
    variant_failures: list[dict] = []
    variant_notes: list[dict] = []
    variant_ready: list[dict] = []
    variant_missing: list[dict] = []
    scratch_dir = ""
    if len(jobs) > 1:
        scratch_dir = str(layout.write_dir(
            Area.SCRATCH, step="deliver-variants"))
    for job in jobs[1:]:
        serves = list(job["serves"])
        spec = job["spec"]
        variant_spec_data = spec_summary(spec)
        ext = _variants.variant_extension(spec, settings["extension"])
        for moment in moments:
            number = int(moment.number)
            timeline = moment.timeline_name
            state = moment_state.get(number, {})
            record = state.get("record")
            reel_entry = state.get("reel_entry")
            key = _variants.variant_ledger_key(timeline, serves)
            out_name = _variants.variant_file_name(
                timeline, settings["naming"], ext, serves)
            master_path = master_outputs.get(number) or ""
            if not master_path or not os.path.isfile(master_path):
                reason = ("the master render for this reel was refused "
                          "or its file is gone, so there is no picture "
                          "to reframe - deliver the master first")
                variant_notes.append({
                    "reel": number, "timeline_name": timeline,
                    "serves": serves, "reason": reason})
                variant_missing.append({
                    "reel": number, "timeline_name": timeline,
                    "serves": serves, "name": out_name,
                    "spec": variant_spec_data, "error": None,
                    "reason": reason})
                continue
            render, why = needs_render(
                project_folder, timeline_name=key, settings=settings,
                spec=variant_spec_data, proposal_digest=digest,
                assignment_reel=reel_entry, ledger=ledger)
            if not render:
                prior = ledger[key]
                row = {
                    "reel": number, "timeline_name": timeline,
                    "serves": serves,
                    "output_path": prior.get("output_path"),
                    "assignment_id": (prior.get("assignment_id")
                                      or (record or {}).get(
                                          "assignment_id")),
                    "qc": {"passed": prior.get("qc_passed"),
                           "from_prior_delivery": True},
                    "reason": why,
                }
                variants_up_to_date.append(row)
                variant_ready.append({**row, "spec": variant_spec_data})
                continue
            full = master_fulls.get(number)
            expected = (_expected_seconds(full, moment) if full
                        else float(moment.total_duration))
            out_path = os.path.join(dest_dir, out_name)
            try:
                track = (face_track_for(
                    timeline, expected,
                    _variants.VARIANT_FACE_RATE_HZ)
                    if face_track_for is not None else None)
                result = reframe_one(
                    master_path=master_path, spec=spec,
                    output_path=out_path, face_centers=track,
                    sample_rate_hz=_variants.VARIANT_FACE_RATE_HZ,
                    work_dir=scratch_dir)
            except Exception as exc:  # noqa: BLE001 - ledgered, batched on
                variant_failures.append({
                    "reel": number, "timeline_name": timeline,
                    "serves": serves, "error": repr(exc)})
                variant_missing.append({
                    "reel": number, "timeline_name": timeline,
                    "serves": serves, "name": out_name,
                    "spec": variant_spec_data, "error": repr(exc),
                    "reason": ("the reframe off the master picture "
                               "failed - see the failure row")})
                continue
            try:
                qc = qc_one(result["output_path"], spec, expected,
                            declared_silence_spans=_declared_silence(
                                project_folder, timeline, expected,
                                float((master_fulls.get(number) or {}).get(
                                    "timeline_fps") or 0.0)))
                qc_passed = bool(qc.get("passed"))
            except Exception as exc:  # noqa: BLE001 - QC measures, never sinks
                qc = {"passed": False, "error": repr(exc)}
                qc_passed = False
            signature = delivery_signature(
                timeline_name=key, settings=settings,
                spec=variant_spec_data, proposal_digest=digest,
                assignment_reel=reel_entry)
            assignment_id = (record or {}).get("assignment_id")
            ledger = record_ledger_entry(
                project_folder, timeline_name=key, signature=signature,
                output_path=result["output_path"], reel=number,
                assignment_id=assignment_id, qc_passed=qc_passed)
            row = {
                "reel": number, "timeline_name": timeline,
                "serves": serves, "output_path": result["output_path"],
                "assignment_id": assignment_id,
                "crop": result.get("crop"),
                "crop_reason": result.get("crop_reason"),
                "qc": qc,
            }
            variants.append(row)
            variant_ready.append({**row, "spec": variant_spec_data})

    receipts, untracked = _publish_receipts(
        project_folder, wanted, master_job, delivered, up_to_date,
        variant_ready, variant_missing, dest_dir, settings)

    # The batch is a delivery ACT, so the retention decision belongs
    # here and not in the CLI wrapper: a programmatic batch must obey the
    # same per-user policy a single delivery does. The job is queued,
    # not run - the worker starts once nothing is rendering, because the
    # reclaim would otherwise race the batch that is still reading its
    # own scratch.
    delivered_outputs = [row["output_path"] for row in delivered
                         if row.get("output_path")]
    delivered_outputs += [row["output_path"] for row in up_to_date
                          if row.get("output_path")]
    delivered_outputs += [row["output_path"] for row in variants
                          if row.get("output_path")]
    from library.tools import retention

    retention_summary = retention.after_delivery(
        project_folder,
        lifecycle_event_id=retention.delivery_event_id(
            project_folder, delivered_outputs),
        outputs=delivered_outputs,
        start_worker=False)

    return {"platforms": wanted,
            "jobs": [{"serves": list(job["serves"]),
                      "spec": spec_summary(job["spec"]),
                      "needs_reframe": job["needs_reframe"],
                      "source": job["source"]} for job in jobs],
            "settings": settings,
            "delivered": delivered, "up_to_date": up_to_date,
            "failed": failed, "unapproved": skipped_moments(project_folder),
            "untracked": untracked,
            "variants": variants,
            "variants_up_to_date": variants_up_to_date,
            "variant_failures": variant_failures,
            "variant_notes": variant_notes, "receipts": receipts,
            "retention_summary": retention_summary,
            "exports_dir": dest_dir}


def _file_entries(delivered: list[dict], up_to_date: list[dict],
                   serves: list[str], spec_data: dict,
                   variant_ready: list[dict], variant_missing: list[dict],
                   dest_dir: str) -> list[dict]:
    """Receipt `files` rows: master rows, rendered variants as ready,
    and only unrendered variants as missing.

    Each rendered (or up-to-date) variant row carries its own served
    platforms and its own spec - the row says what the file IS, not
    what the master is. A variant with no file names the reason it has
    none instead of reading as delivered.
    """
    platform = "+".join(serves)
    rows = []
    for row in delivered + up_to_date:
        out = row.get("output_path") or ""
        qc = row.get("qc")
        rows.append({
            "name": os.path.basename(out) or row["timeline_name"],
            "path": out, "platform": platform, "spec": spec_data,
            "exists": bool(out and os.path.isfile(out)),
            "status": ("ready" if out and os.path.isfile(out)
                       else "missing"),
            "qc": qc,
        })
    for row in variant_ready:
        out = row.get("output_path") or ""
        qc = row.get("qc")
        rows.append({
            "name": os.path.basename(out) or row["timeline_name"],
            "path": out, "platform": "+".join(row.get("serves") or ()),
            "spec": row.get("spec") or spec_data,
            "exists": bool(out and os.path.isfile(out)),
            "status": ("ready" if out and os.path.isfile(out)
                       else "missing"),
            "qc": qc,
        })
    for note in variant_missing:
        rows.append({
            "name": note.get("name") or note["timeline_name"],
            "path": os.path.join(dest_dir, note.get("name") or ""),
            "platform": "+".join(note.get("serves") or ()),
            "spec": note.get("spec") or spec_data,
            "exists": False, "status": "missing", "qc": None,
            "reason": note.get("error") or note.get("reason"),
        })
    return rows


def _publish_receipts(project_folder: str, platforms: list[str],
                      master_job: dict, delivered: list[dict],
                      up_to_date: list[dict], variant_ready: list[dict],
                      variant_missing: list[dict],
                      dest_dir: str, settings: dict
                      ) -> tuple[list[dict], list[dict]]:
    """One delivery receipt per owning assignment.

    Returns `(receipts, untracked)`: reels no assignment owns are
    delivered and QC'd but get no receipt - there is nothing to attach
    one to, and the spine leaves untracked work untracked rather than
    inventing it a history. Rendered variants join their reel's
    assignment receipt with their own QC; `selected_platforms` names
    every platform the batch served, not just the master's.
    """
    from library.tools import assignment_lifecycle, assignment_spine
    from library.tools.assignment_artifacts import publish_delivery_receipt

    covered = (delivered + up_to_date
               + [row for row in variant_ready if row.get("assignment_id")])
    by_assignment: dict[str, list[dict]] = {}
    untracked: list[dict] = []
    for row in covered:
        if row.get("assignment_id"):
            by_assignment.setdefault(row["assignment_id"], []).append(row)
        else:
            untracked.append(row)
    receipts = []
    for assignment_id, rows in sorted(by_assignment.items()):
        root = str(Path(project_folder).expanduser().resolve())
        try:
            record = assignment_lifecycle.load(root, assignment_id)
        except (KeyError, OSError, ValueError):
            continue
        numbers = sorted({row["reel"] for row in rows})
        files = _file_entries(
            [r for r in delivered + up_to_date
             if r.get("assignment_id") == assignment_id],
            [], list(master_job["serves"]),
            spec_summary(master_job["spec"]),
            [r for r in variant_ready
             if r.get("assignment_id") == assignment_id],
            [n for n in variant_missing
             if any(r["reel"] == n["reel"] for r in rows)],
            dest_dir)
        per_file = {}
        for entry in files:
            qc = entry.get("qc") or {}
            per_file[entry["name"]] = qc.get("passed")
        approved_version = {
            "status": "approved", "assignment_id": assignment_id,
            "state": record.get("state"),
            "reels": numbers,
            "deliver_settings_declared": {
                "preset": bool(settings.get("preset_declared")),
                "naming": bool(settings.get("naming_declared")),
            },
        }
        resume = assignment_spine.resume_point(
            project_folder,
            assignment_lifecycle.load(root, assignment_id))
        path, receipt = publish_delivery_receipt(
            project_folder, assignment_id=assignment_id,
            approved_version=approved_version, files=files,
            qc={"per_file": per_file, "platforms": platforms},
            resume_point=resume,
            selected_platforms=list(platforms))
        receipts.append({"assignment_id": assignment_id,
                         "path": str(path), "receipt": receipt})
    return receipts, untracked


# ── The file in front of you ─────────────────────────────────────


def reveal_in_finder(path: str) -> tuple[bool, str]:
    """Select `path` in Finder. `(revealed, reason)`.

    macOS only, opt-out via `REN_DELIVER_REVEAL=0`. Never raises: a
    reveal is a courtesy, and a failed courtesy must not fail a
    delivery. `encoding="utf-8"` on every captured pipe (AGENTS.md 9).
    """
    if os.environ.get("REN_DELIVER_REVEAL", "").strip() == "0":
        return False, "opted out (REN_DELIVER_REVEAL=0)"
    if sys.platform != "darwin":
        return False, f"no Finder on {sys.platform}"
    if not path:
        return False, "nothing to reveal"
    try:
        completed = subprocess.run(
            ["open", "-R", path], capture_output=True, encoding="utf-8",
            timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"`open -R` failed ({exc})"
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or "unknown error"
        return False, f"`open -R` exited {completed.returncode} ({detail})"
    return True, f"revealed {path} in Finder"


__all__ = [
    "DELIVER_LEDGER_FILENAME",
    "approved_moments",
    "delivery_jobs",
    "delivery_signature",
    "deliver_all_approved",
    "ledger_path",
    "manifest_tracks",
    "moment_for_number",
    "needs_render",
    "owning_assignment_reel",
    "proposal_sha",
    "read_ledger",
    "record_ledger_entry",
    "resolve_platforms",
    "reveal_in_finder",
    "run_qc",
    "skipped_moments",
    "spec_summary",
    "write_ledger",
]
