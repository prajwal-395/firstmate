"""A repeatable, real-footage workflow qualification for Ren.

The source selection is read from a prebuilt footage index. Source files are
copied into the qualification media store, analyzed in a disposable collection,
and placed only into the exact ``Ren Qualification`` Resolve project through
the broker. Each run writes a dated, stage-by-stage evidence report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path

from library.tools import perf_ledger
from library.tools import qualification_project as qp

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_TIMEOUT_SECONDS = 8 * 60 * 60
RENDER_TIMEOUT_SECONDS = 600
MAX_SOURCE_SECONDS = 300.0
DEFAULT_QUERY = "marketing directors talking about marketing"


class WorkflowQualificationError(RuntimeError):
    """The real-footage workflow did not pass every required stage."""


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(report, indent=2, default=str) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _stage(report: dict, name: str, *, passed: bool, evidence) -> None:
    report["stages"].append({"name": name,
                             "status": "passed" if passed else "failed",
                             "evidence": evidence})


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_analysis(copy_path: Path, run_dir: Path, run_id: str) -> dict:
    projects = run_dir / "projects"
    memory = run_dir / "source-memory"
    slug = f"qualification-{run_id.lower().replace('_', '-')[:28]}"
    command = [str(REPO_ROOT / "bin" / "ren"), "analyze",
               str(copy_path.parent), "--into", str(projects),
               "--slug", slug, "--memory-root", str(memory), "--json",
               # The editing DAG's temporal-index operation currently
               # refuses its declared semantic document input when reached
               # through `ren analyze`. This qualification needs semantic
               # analysis plus the independently built text index; do not
               # turn that known wiring defect into a false workflow pass.
               "--skip", "temporal.index", "--skip", "prosody.analyse"]
    with perf_ledger.span("ren_analysis_cli", subprocesses=1, calls=1):
        result = subprocess.run(
            command, cwd=REPO_ROOT, capture_output=True, text=True,
            encoding="utf-8", timeout=ANALYSIS_TIMEOUT_SECONDS, check=False)
    evidence = {"command": command, "exit_code": result.returncode,
                "stdout_tail": result.stdout[-12000:],
                "stderr_tail": result.stderr[-12000:]}
    if result.returncode != 0:
        raise WorkflowQualificationError(
            f"real-footage analysis failed with exit {result.returncode}: "
            f"{result.stderr[-1500:]}" )
    try:
        record = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowQualificationError(
            "ren analyze did not return its JSON run record") from exc
    if record["status"] != "complete":
        raise WorkflowQualificationError(
            f"ren analyze recorded status {record['status']!r}")
    project_folder = Path(record["project_folder"]).resolve()
    index_file = project_folder / "pipeline_output" / "scratch" \
        / "footage_index" / "footage_index.json"
    if not index_file.is_file():
        raise WorkflowQualificationError(
            f"analysis did not build its footage index: {index_file}")
    evidence.update({"run_record": record, "analysis_project": str(project_folder),
                     "index": str(index_file)})
    return {"project_folder": project_folder,
            "index_file": index_file, "evidence": evidence}


def _analyzed_hit(index_file: Path, query: str) -> dict:
    from library.tools.analysis.footage_query import FootageIndex

    payload = json.loads(index_file.read_text(encoding="utf-8"))
    index = FootageIndex(payload["project_folder"],
                         index_dir=index_file.parent)
    result = index.search_report(query, top_k=20, mode="hybrid")
    if result.get("error") or not result["results"]:
        raise WorkflowQualificationError(
            f"fresh analysis index found no usable speech for {query!r}: "
            f"{result.get('error') or 'no hits'}")
    for hit in result["results"]:
        if hit["start"] >= 0 and hit["end"] > hit["start"]:
            return {"hit": {key: hit[key] for key in
                             ("clip_id", "start", "end", "score", "dense")
                             if key in hit}, "query": query,
                    "index": str(index_file),
                    "index_project": payload["project_folder"]}
    raise WorkflowQualificationError("analysis index returned no timed span")


def _check_project_standard(project) -> dict:
    """Read the Qualification project standard back; the workflow never
    mutates project settings under existing timelines. The engine refuses
    that write, and the reset fixture owns the 24fps 1080p standard."""
    try:
        rate_raw = project.GetSetting("timelineFrameRate")
        width_raw = project.GetSetting("timelineResolutionWidth")
        height_raw = project.GetSetting("timelineResolutionHeight")
    except (AttributeError, TypeError, ValueError) as exc:
        raise WorkflowQualificationError(
            f"could not read the Qualification project standard: {exc}"
        ) from exc
    try:
        rate, width, height = (float(rate_raw), int(width_raw),
                               int(height_raw))
    except (TypeError, ValueError):
        rate = width = height = None
    if (rate is None or abs(rate - 24.0) > 0.0001
            or width != 1920 or height != 1080):
        raise WorkflowQualificationError(
            "Ren Qualification project standard drifted: frame rate "
            f"{rate_raw!r}, {width_raw!r}x{height_raw!r} (want 24, "
            "1920x1080); run `ren qualification reset` first")
    return {"timelineFrameRate": rate, "timelineResolutionWidth": width,
            "timelineResolutionHeight": height}


def _check_timeline_inherits_standard(timeline) -> dict:
    """Read the new timeline's inherited shape back and require the
    project standard. Three consecutive live runs refused a custom
    timeline rate (menu label and full precision alike) while the
    timeline kept the project rate, so the cut rides the 24fps project
    standard instead: source frames are indexed at the media rate, record
    extents match 1:1, and the 0.1% rate drift sits a hundredfold inside
    render QA's duration tolerance."""
    try:
        rate_raw = timeline.GetSetting("timelineFrameRate")
        width_raw = timeline.GetSetting("timelineResolutionWidth")
        height_raw = timeline.GetSetting("timelineResolutionHeight")
    except (AttributeError, TypeError, ValueError) as exc:
        raise WorkflowQualificationError(
            f"could not read the new timeline shape: {exc}") from exc
    try:
        rate, width, height = (float(rate_raw), int(width_raw),
                               int(height_raw))
    except (TypeError, ValueError):
        rate = width = height = None
    if (rate is None or abs(rate - 24.0) > 0.0001
            or width != 1920 or height != 1080):
        raise WorkflowQualificationError(
            "new timeline did not inherit the project standard: frame "
            f"rate {rate_raw!r}, {width_raw!r}x{height_raw!r} (want 24, "
            "1920x1080)")
    return {"timelineFrameRate": rate, "timelineResolutionWidth": width,
            "timelineResolutionHeight": height}


def _probe_source_frames(source: Path) -> tuple:
    """Media rate and total frames from the local copy, never from the
    pool item: Resolve's scripting methods differ by media type (the MXF
    essence item exposes no GetLeftOffset), while ffprobe reads the file
    the SHA-256 already covers."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=avg_frame_rate,codec_type", "-of", "json",
         str(source)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        check=False)
    if probe.returncode != 0:
        raise WorkflowQualificationError(
            f"ffprobe could not read the qualification copy: {source.name}")
    try:
        measured = json.loads(probe.stdout)
        duration = float(measured["format"]["duration"])
        rate_text = next(row["avg_frame_rate"]
                         for row in measured.get("streams", [])
                         if row.get("codec_type") == "video")
        numerator, _, denominator = rate_text.partition("/")
        fps = float(numerator) / float(denominator or 1)
    except (KeyError, TypeError, ValueError, StopIteration,
            json.JSONDecodeError) as exc:
        raise WorkflowQualificationError(
            f"ffprobe gave no usable rate/duration for {source.name}") from exc
    if not 0 < duration or not fps > 0:
        raise WorkflowQualificationError(
            f"ffprobe gave no usable rate/duration for {source.name}")
    return fps, max(1, round(duration * fps))


def _broker_call(kind: str, params: dict, *, idempotency_key: str,
                 wait_seconds: int) -> dict:
    from library.tools.resolved import client

    with perf_ledger.span(
            "qualification_broker_job", backend="ren-resolved",
            calls=1, external_engine="resolve_qualification_job"):
        submitted = client.submit(kind, params, qualification=True,
                                  idempotency_key=idempotency_key)
        receipt = client.result(submitted["id"], wait=wait_seconds)
    if receipt["state"] != "done":
        failure = {"state": receipt["state"], "error": receipt.get("error"),
                   **(receipt.get("result") or {})}
        raise WorkflowQualificationError(json.dumps(failure, default=str))
    return receipt


def prepare_broker_job(params: dict) -> dict:
    """Validate the broker's one fixed write/export workflow."""
    from library.tools.resolved.jobs import QUALIFICATION_PROJECT, JobRefused

    allowed = {"project", "source_copy", "timeline", "source_start",
               "source_end", "output_dir", "output_name",
               "timeout_seconds", "source_sha256"}
    if set(params) != allowed:
        raise JobRefused("qualification.workflow accepts exactly "
                         f"{sorted(allowed)}")
    if params["project"] != QUALIFICATION_PROJECT:
        raise JobRefused("qualification.workflow only accepts "
                         f"{QUALIFICATION_PROJECT!r}")
    source = Path(params["source_copy"]).expanduser().resolve()
    if not source.is_relative_to(qp.real_media_dir().resolve()) or not source.is_file():
        raise JobRefused("source_copy must be an existing qualification media copy")
    output_dir = Path(params["output_dir"]).expanduser().resolve()
    allowed_output = (qp.media_dir() / "workflow-runs").resolve()
    if not output_dir.is_relative_to(allowed_output):
        raise JobRefused("output_dir must stay under qualification workflow-runs")
    start, end = params["source_start"], params["source_end"]
    if (not isinstance(start, (int, float)) or isinstance(start, bool)
            or not isinstance(end, (int, float)) or isinstance(end, bool)
            or start < 0 or end <= start or end - start > 45):
        raise JobRefused("source range must be positive and at most 45 seconds")
    timeout = params["timeout_seconds"]
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= RENDER_TIMEOUT_SECONDS:
        raise JobRefused("render timeout must be between 1 and 600 seconds")
    for key in ("timeline", "output_name", "source_sha256"):
        if not isinstance(params[key], str) or not params[key]:
            raise JobRefused(f"qualification.workflow needs {key!r}")
    if len(params["source_sha256"]) != 64 or any(
            char not in "0123456789abcdef" for char in params["source_sha256"]):
        raise JobRefused("source_sha256 must be a lowercase SHA-256 hex digest")
    if Path(params["output_name"]).name != params["output_name"]:
        raise JobRefused("output_name must be a single filename stem")
    if len(params["timeline"]) > 64:
        raise JobRefused("timeline name must be at most 64 characters")
    return {"mode": "exclusive", "priority": "qa_render",
            "executed": True, "project": QUALIFICATION_PROJECT,
            "timeline": params["timeline"], "locality": "timeline",
            "coalesce_key": None, "qualification": True}


def run_broker_job(job: dict, resolve) -> dict:
    """Build, revise, export and read back one timeline on Resolve."""
    from library.tools.resolved.jobs import JobRefused

    stages = []
    current = {"name": "project borrow"}
    try:
        params = job["params"]
        output_dir = Path(params["output_dir"]).expanduser().resolve()
        workflow_root = output_dir.parent
        ledger_path = workflow_root / "perf_ledger.jsonl"
        with perf_ledger.recording(
                ledger_path, workflow_root.name, "qualification_workflow"):
            result = _run_broker_job(job, resolve, current, stages)
    except Exception as exc:
        raise JobRefused(
            f"{current['name']} failed: {exc}",
            result={"broker_stages": stages,
                    "failed_stage": current["name"],
                    "detail": str(exc),
                    "traceback": traceback.format_exc()}) from exc
    result["broker_stages"] = stages
    return result


def _run_broker_job(job: dict, resolve, current: dict,
                    stages: list) -> dict:
    """Internal workflow body; records each completed broker stage."""
    from library.tools import edit_patch, timeline_shadow
    from library.tools.resolve_deadline import DeadlineProxy
    from library.tools.resolved.jobs import QUALIFICATION_PROJECT
    from library.tools.transform_write_log import write_scope

    params = job["params"]
    source = Path(params["source_copy"]).resolve()
    if not source.is_relative_to(qp.real_media_dir().resolve()):
        raise WorkflowQualificationError("broker source escaped qualification media")
    if _file_sha256(source) != params["source_sha256"]:
        raise WorkflowQualificationError(
            "qualification source copy no longer matches its recorded SHA-256")
    if not isinstance(resolve, DeadlineProxy):
        raise WorkflowQualificationError(
            "qualification Resolve handle is missing per-call deadlines")
    current["name"] = "edit construction: switch project"
    with qp.borrowed(resolve) as borrowed_from:
        manager = resolve.GetProjectManager()
        project = qp.switch_project(manager, QUALIFICATION_PROJECT)
        if project.GetName() != QUALIFICATION_PROJECT:
            raise WorkflowQualificationError(
                f"opened {project.GetName()!r}, not {QUALIFICATION_PROJECT!r}")
        if any(project.GetTimelineByIndex(index).GetName() == params["timeline"]
               for index in range(1, int(project.GetTimelineCount() or 0) + 1)):
            raise WorkflowQualificationError(
                f"timeline already exists: {params['timeline']}")
        _check_project_standard(project)
        current["name"] = "edit construction: import media"
        pool = project.GetMediaPool()
        media_item = None
        for item in pool.GetRootFolder().GetClipList() or []:
            properties = item.GetClipProperty() or {}
            if Path(str(properties.get("File Path", ""))).resolve() == source:
                media_item = item
                break
        imported = False
        if media_item is None:
            imported_items = pool.ImportMedia([str(source)]) or []
            if len(imported_items) != 1:
                raise WorkflowQualificationError(
                    f"Resolve imported {len(imported_items)} source items")
            media_item = imported_items[0]
            imported = True
        media_fps, total_frames = _probe_source_frames(source)
        # Pool clips start at their own zero; the offset is a timeline
        # notion, and some essence items expose no offset method at all.
        left = 0
        source_in = left + round(float(params["source_start"]) * media_fps)
        source_count = max(1, round(
            (float(params["source_end"]) - float(params["source_start"])) * media_fps))
        source_out = min(left + total_frames,
                         source_in + source_count)
        if source_out <= source_in:
            raise WorkflowQualificationError("analysis selected no source frames")

        timeline = pool.CreateEmptyTimeline(params["timeline"])
        if not timeline:
            raise WorkflowQualificationError("Resolve could not create timeline")
        from library.tools.resolve_lock import assert_current_timeline
        assert_current_timeline(project, timeline)
        if project.GetCurrentTimeline().GetName() != params["timeline"]:
            raise WorkflowQualificationError("qualification timeline did not read back")
        current["name"] = "edit construction: timeline shape"
        timeline_shape = _check_timeline_inherits_standard(timeline)
        timeline_fps = float(timeline_shape["timelineFrameRate"])
        if timeline.GetTrackCount("audio") == 0 and not timeline.AddTrack("audio"):
            raise WorkflowQualificationError("Resolve could not create A1")
        for media_type, name in (("video", "A-Roll"), ("audio", "Production Audio")):
            if not timeline.SetTrackName(media_type, 1, name):
                raise WorkflowQualificationError(
                    f"could not name {media_type}1 {name!r}")
        store = timeline_shadow.ShadowStore()
        current["name"] = "edit construction: place clips"
        timeline_shadow.observe(project, timeline, store)
        build = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.build",
            operations=[
                {"op": "clip.insert", "unique_id": f"{job['id']}-build-v1",
                 "track_type": "video", "track_index": 1,
                 "record_in": int(timeline.GetStartFrame()),
                 "record_out": int(timeline.GetStartFrame()) + source_out - source_in,
                 "media_pool_item": media_item.GetUniqueId(),
                 "source_in": source_in, "source_out": source_out},
                {"op": "clip.insert", "unique_id": f"{job['id']}-build-a1",
                 "track_type": "audio", "track_index": 1,
                 "record_in": int(timeline.GetStartFrame()),
                 "record_out": int(timeline.GetStartFrame()) + source_out - source_in,
                 "media_pool_item": media_item.GetUniqueId(),
                 "source_in": source_in, "source_out": source_out},
            ], conflict_domains=("timeline_structure",),
            idempotency_key=f"qualification-build:{job['id']}", store=store)
        video = timeline.GetItemListInTrack("video", 1) or []
        audio = timeline.GetItemListInTrack("audio", 1) or []
        if build["status"] != "committed" or len(video) != 1 or len(audio) != 1:
            raise WorkflowQualificationError(
                f"edit construction read back video={len(video)} audio={len(audio)} "
                f"receipt={build}")
        stages.append({"name": "edit construction", "status": "passed",
                       "evidence": {"build_receipt": build,
                                    "video_clips": len(video),
                                    "audio_clips": len(audio),
                                    "timeline_shape": timeline_shape}})
        current["name"] = "scripted human change"
        human_frame = int(timeline.GetStartFrame()) + round(timeline_fps)
        manual = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.touchup",
            operations=[{"op": "marker.add", "frame": human_frame,
                         "color": "Blue", "name": "Human change: keep this moment",
                         "note": "Scripted stand-in for the editor's retained change."}],
            conflict_domains=("markers",),
            idempotency_key=f"qualification-human:{job['id']}",
            preconditions=({"kind": "marker_absent", "frame": human_frame},),
            store=store)
        if manual["status"] != "committed":
            raise WorkflowQualificationError(f"manual change did not commit: {manual}")
        stages.append({"name": "scripted human change", "status": "passed",
                       "evidence": manual})

        current["name"] = "Ren revision preservation"
        record_in = int(timeline.GetEndFrame())
        revision_count = max(1, round(timeline_fps))
        revision_source_in = min(source_out, source_in + round(2 * media_fps))
        revision_source_out = min(source_out, revision_source_in + revision_count)
        if revision_source_out <= revision_source_in:
            revision_source_in, revision_source_out = source_in, source_out
        revision = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.build",
            operations=[
                {"op": "clip.insert", "unique_id": f"{job['id']}-v1",
                 "track_type": "video", "track_index": 1,
                 "record_in": record_in,
                 "record_out": record_in + revision_source_out - revision_source_in,
                 "media_pool_item": media_item.GetUniqueId(),
                 "source_in": revision_source_in,
                 "source_out": revision_source_out},
                {"op": "clip.insert", "unique_id": f"{job['id']}-a1",
                 "track_type": "audio", "track_index": 1,
                 "record_in": record_in,
                 "record_out": record_in + revision_source_out - revision_source_in,
                 "media_pool_item": media_item.GetUniqueId(),
                 "source_in": revision_source_in,
                 "source_out": revision_source_out},
            ], conflict_domains=("timeline_structure",),
            idempotency_key=f"qualification-revision:{job['id']}",
            store=store)
        if revision["status"] != "committed":
            raise WorkflowQualificationError(
                f"Ren revision did not commit: {revision}")
        markers = timeline.GetMarkers() or {}
        readback_marker = (markers.get(human_frame)
                           or markers.get(str(human_frame)))
        video_after = timeline.GetItemListInTrack("video", 1) or []
        audio_after = timeline.GetItemListInTrack("audio", 1) or []
        if (not readback_marker
                or readback_marker.get("name") != "Human change: keep this moment"
                or len(video_after) != 2 or len(audio_after) != 2):
            raise WorkflowQualificationError(
                "Ren revision lost the scripted human change or placement: "
                f"marker={readback_marker}, video={len(video_after)}, "
                f"audio={len(audio_after)}")
        stages.append({"name": "Ren revision preservation",
                       "status": "passed",
                       "evidence": {"revision": revision,
                                    "marker_frame": human_frame,
                                    "marker_name": readback_marker["name"],
                                    "video_clips": len(video_after),
                                    "audio_clips": len(audio_after)}})
        with write_scope(project=QUALIFICATION_PROJECT,
                         timeline_name=params["timeline"],
                         run_id=job["id"]):
            if not manager.SaveProject():
                raise WorkflowQualificationError(
                    "Resolve refused to save the Qualification edit")

        current["name"] = "export"
        from library.tools.execution import resolve_render
        render = resolve_render.render_timeline_under_existing_lease(
            timeline_name=params["timeline"],
            output_dir=params["output_dir"],
            output_name=params["output_name"], fmt="mp4", codec="H264",
            timeout_seconds=params["timeout_seconds"],
            resolve=resolve)
        stages.append({"name": "Resolve render", "status": "passed",
                       "evidence": render})
        duration_frames = int(timeline.GetEndFrame() - timeline.GetStartFrame())
        return {"project": QUALIFICATION_PROJECT,
                "timeline": params["timeline"], "fps": timeline_fps,
                "duration_seconds": duration_frames / timeline_fps,
                "media_imported": imported,
                "media_path": str(source),
                "media_unique_id": media_item.GetUniqueId(),
                "selected_source_span": [params["source_start"], params["source_end"]],
                "edit_construction": {"video_clips": 1, "audio_clips": 1,
                                      "selected_from_fresh_analysis": True,
                                      "build_receipt": build,
                                      "readback": "passed"},
                "manual_change": manual,
                "ren_revision": revision,
                "preservation_readback": {
                    "marker_frame": human_frame,
                    "marker_name": readback_marker["name"],
                    "video_clips": len(video_after),
                    "audio_clips": len(audio_after),
                    "passed": True},
                "render": render,
                "borrowed_from": borrowed_from}


def run(index_path: str | Path, *, query: str = DEFAULT_QUERY,
        runs: int = 2) -> dict:
    """Run analysis, real edit construction, manual change, revision, export,
    and final render QA two times by default.
    """
    if not 2 <= runs <= 2:
        raise ValueError("qualification requires exactly two clean runs")
    started = datetime.now(UTC)
    run_stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    root = qp.media_dir() / "workflow-runs" / run_stamp
    root.mkdir(parents=True, exist_ok=False)
    perf = perf_ledger.begin(
        root, "qualification_workflow", run_stamp,
        ledger_path_override=root / "perf_ledger.jsonl")
    if perf:
        perf_ledger.count_call("cli_starts")
    report_path = root / "qualification-report.json"
    report = {"schema": "ren-workflow-qualification.v1",
              "started_at": started.isoformat(), "project": "Ren Qualification",
              "source_selection": None, "runs": [], "stages": [],
              "passed": False}
    _write_report(report_path, report)
    try:
        try:
            selected = qp.copy_indexed_source(
                index_path, query, max_seconds=MAX_SOURCE_SECONDS)
        except Exception as exc:
            _stage(report, "source copy", passed=False,
                   evidence={"index": str(index_path), "query": query,
                             "error": str(exc)})
            _write_report(report_path, report)
            raise
        report["source_selection"] = selected
        _stage(report, "source copy", passed=True,
               evidence={k: selected[k] for k in (
                   "index", "query", "clip_id", "filename", "source_file",
                   "qualification_copy", "duration_seconds", "bytes", "sha256")})
        _write_report(report_path, report)
        copy_path = Path(selected["qualification_copy"])
        for ordinal in range(1, runs + 1):
            run_id = f"{ordinal}-{uuid.uuid4().hex[:8]}"
            run_dir = root / f"run-{ordinal}"
            run_dir.mkdir()
            one = {"ordinal": ordinal, "id": run_id, "stages": []}
            report["runs"].append(one)
            try:
                analysis = _run_analysis(copy_path, run_dir, run_id)
                child_rows = perf_ledger.read_rows(
                    analysis["project_folder"])
                child_run_ids = perf_ledger.run_ids(child_rows)
                if child_run_ids:
                    child_run_id = child_run_ids[-1]
                    spans = []
                    for child_row in child_rows:
                        if (child_row.get("kind") == perf_ledger.SPAN
                                and child_row.get("run_id") == child_run_id):
                            span_row = dict(child_row)
                            span_row["source_run_id"] = child_run_id
                            span_row["run_id"] = run_stamp
                            span_row["capability"] = "qualification_workflow"
                            spans.append(span_row)
                    perf_ledger.commit_to_path(
                        root / "perf_ledger.jsonl", spans)
                hit = _analyzed_hit(analysis["index_file"], query)
            except Exception as exc:
                _stage(report, f"run {ordinal}: analysis", passed=False,
                       evidence={"error": str(exc)})
                _write_report(report_path, report)
                raise
            _stage(report, f"run {ordinal}: analysis", passed=True,
                   evidence={**analysis["evidence"], "analysis_hit": hit})
            one["analysis_hit"] = hit
            _write_report(report_path, report)

            timeline_name = f"Q Workflow {run_stamp} {ordinal}"
            export_dir = root / "exports"
            export_dir.mkdir(exist_ok=True)
            params = {"project": "Ren Qualification",
                      "source_copy": str(copy_path),
                      "timeline": timeline_name,
                      "source_start": max(
                          0.0, float(hit["hit"]["start"]) - 5.0),
                      "source_end": min(
                          float(selected["duration_seconds"]),
                          max(float(hit["hit"]["end"]) + 5.0,
                              max(0.0, float(hit["hit"]["start"])-5.0)
                              + 15.0)),
                      "output_dir": str(export_dir),
                      "output_name": f"{timeline_name.replace(' ', '_')}",
                      "timeout_seconds": RENDER_TIMEOUT_SECONDS,
                      "source_sha256": selected["sha256"]}
            try:
                receipt = _broker_call(
                    "qualification.workflow", params,
                    idempotency_key=f"qualification-workflow:{run_id}",
                    wait_seconds=RENDER_TIMEOUT_SECONDS + 900)
            except WorkflowQualificationError as exc:
                try:
                    failure = json.loads(str(exc))
                except json.JSONDecodeError:
                    failure = {"error": str(exc)}
                for stage in failure.get("broker_stages", []):
                    _stage(report, f"run {ordinal}: {stage['name']}",
                           passed=stage["status"] == "passed",
                           evidence=stage.get("evidence"))
                failed_stage = failure.get("failed_stage")
                if failed_stage:
                    _stage(report, f"run {ordinal}: {failed_stage}",
                           passed=False, evidence=failure)
                _write_report(report_path, report)
                raise
            evidence = receipt["result"]
            one["resolve"] = evidence
            for stage in evidence["broker_stages"]:
                _stage(report, f"run {ordinal}: {stage['name']}",
                       passed=stage["status"] == "passed",
                       evidence=stage.get("evidence"))
            from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC
            from library.tools.execution import resolve_render
            from library.tools.master_loudness import master_render_report
            try:
                produced = resolve_render._files_written_since(
                    evidence["render"]["output_dir"],
                    evidence["render"]["output_name"],
                    evidence["render"]["render_started_at"])
                if not produced:
                    raise WorkflowQualificationError(
                        "Resolve completed without writing an export")
                raw_path = (Path(evidence["render"]["output_dir"])
                            / produced[-1])
                if raw_path.stat().st_size < resolve_render.MIN_PLAUSIBLE_BYTES:
                    raise WorkflowQualificationError(
                        f"Resolve export is implausibly small: {raw_path.stat().st_size} bytes")
                resolve_render._assert_has_audio(str(raw_path))
            except Exception as exc:
                _stage(report, f"run {ordinal}: export", passed=False,
                       evidence={"error": str(exc)})
                _write_report(report_path, report)
                raise
            master_path = str(export_dir / f"{params['output_name']}_master.mp4")
            try:
                mastered = master_render_report(
                    {"output_path": str(raw_path)}, master_path,
                    target_lufs=DEFAULT_DELIVERY_SPEC.loudness_target_lufs,
                    true_peak_ceiling=(
                        DEFAULT_DELIVERY_SPEC.true_peak_ceiling_dbtp))
            except Exception as exc:
                _stage(report, f"run {ordinal}: export", passed=False,
                       evidence={"error": str(exc)})
                _write_report(report_path, report)
                raise
            evidence["raw_output_path"] = str(raw_path)
            evidence["output_path"] = mastered["output_path"]
            evidence["mastering"] = mastered["mastering"]
            _stage(report, f"run {ordinal}: export", passed=True,
                   evidence={"render": evidence["render"],
                             "mastering": evidence["mastering"],
                             "output_path": evidence["output_path"]})
            _write_report(report_path, report)

            from library.skills.verify_render.skill import run as verify_render
            expected = float(evidence["duration_seconds"])
            qa = verify_render(
                evidence["output_path"], str(analysis["project_folder"]),
                f"qualification-{run_stamp}-{ordinal}",
                expected_duration=expected,
                expected_resolution=[1920, 1080],
                expected_fps=float(evidence["fps"]))
            one["render_qa"] = qa
            _stage(report, f"run {ordinal}: final render QA",
                   passed=bool(qa["passed"]), evidence=qa)
            _write_report(report_path, report)
            if not qa["passed"]:
                raise WorkflowQualificationError(
                    f"run {ordinal} final render QA failed: "
                    f"{'; '.join(qa['issues'])}")
        report["passed"] = (
            len(report["runs"]) == 2
            and all(stage["status"] == "passed" for stage in report["stages"])
            and all("resolve" in one and "render_qa" in one
                    and one["render_qa"]["passed"] for one in report["runs"]))
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        if not report["stages"] or report["stages"][-1]["status"] != "failed":
            _stage(report, "workflow", passed=False,
                   evidence=report["failure"])
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["report_path"] = str(report_path)
        _write_report(report_path, report)
        if perf:
            perf.row["status"] = "ok" if report["passed"] else "failed"
            perf.end()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren qualification workflow",
        description="Qualify real footage through two complete Ren revisions and render QA.")
    parser.add_argument("--index", required=True,
                        help="existing footage_index.json; read-only")
    parser.add_argument("--query", default=DEFAULT_QUERY,
                        help="search phrase used to select the real footage span")
    parser.add_argument("--runs", type=int, choices=(2,), default=2,
                        help="two consecutive complete runs are required")
    args = parser.parse_args(argv)
    try:
        report = run(args.index, query=args.query, runs=args.runs)
    except Exception as exc:  # noqa: BLE001 - command reports a durable failure
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2),
              file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["passed"] else 1
