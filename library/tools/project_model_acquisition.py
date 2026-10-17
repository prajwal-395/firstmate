"""Production VI-12 adapter for bounded, query-directed M4 acquisition."""

from __future__ import annotations

import copy
import resource
import sys
import time
from pathlib import Path
from typing import Any

from library.tools import (
    model_evidence_provenance,
    model_registry,
    project_model_evidence,
    project_model_store,
    source_memory,
)
from library.tools.query_directed_acquisition import query_with_acquisition

WINDOW_SECONDS = 10.0
PRODUCER_VERSION = "gemma-4-12b-it-4bit:m4-scene-window-v1"
MODEL_IMPLEMENTATION_ID = "gemma.vision_4_12b_it_4bit"
M4_WINDOW_COST_SECONDS = 90.0


def _m4_window_evidence_id(digest: str, start_s: float | None,
                           end_s: float | None) -> str:
    if start_s is None or end_s is None:
        return ""
    return f"m4-window:{digest}:{round(float(start_s) * 1000)}:{round(float(end_s) * 1000)}"


def filter_m4_by_reuse_policy(model: dict[str, Any], project: Path, *,
                              edition: str, purpose: str,
                              registry: dict[str, Any] | None = None
                              ) -> dict[str, Any]:
    """Keep M4 observations only when their producer record permits reuse."""
    registry = registry or model_registry.load_registry()
    filtered = copy.deepcopy(model)
    observations = []
    audit_path = (project / project_model_store.STORE_DIR /
                  model_evidence_provenance.AUDIT_FILE)
    for row in filtered["observations"]:
        if not row.get("provenance_id", "").endswith("-m4"):
            observations.append(row)
            continue
        payload = row["payload"]
        record = payload.get("model_provenance")
        source = payload.get("model_provenance_source")
        if (not isinstance(record, dict) or not isinstance(source, dict)
                or not record.get("evidence_ids")):
            continue
        try:
            decision = model_evidence_provenance.reuse_evidence(
                [{"id": record["evidence_ids"][0]}], [record],
                source_project_id=source["project_id"],
                target_project_id=model["id"], edition=edition,
                purpose=purpose, source_project_root=source["project_root"],
                target_project_root=project, audit_path=audit_path,
                registry=registry,
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if decision["reused"]:
            observations.append(row)
    filtered["observations"] = observations
    return filtered


def _m4_rows(
    model: dict[str, Any],
    digest: str,
    interval: dict[str, float],
    *,
    asset_id: str,
    clock_id: str,
    duration: float,
    memory_root: Path | None,
) -> tuple[list[dict], list[dict]]:
    builder = project_model_evidence._Builder(model["id"])
    project_model_evidence._migrate_scenes(
        builder, digest, asset_id, clock_id, duration, memory_root
    )
    left, right = (round(interval["start_s"] * 1000), round(interval["end_s"] * 1000))
    observations = [
        row
        for row in builder.model["observations"]
        if row.get("provenance_id") == f"prov-{digest}-m4"
        and row.get("interval", {}).get("start", 0) < right
        and row.get("interval", {}).get("end", 0) > left
    ]
    if observations:
        return observations, builder.model["provenance"]
    return [], []


def query_project(
    project: str | Path,
    model: dict[str, Any],
    plan: dict[str, Any],
    *,
    source_digest: str,
    source_file: str,
    duration_seconds: float,
    max_cost_seconds: float,
    workload_purpose: str = "commercial_client",
    memory_root: str | Path | None = None,
    window_analyzer: Any = None,
) -> dict[str, Any]:
    """Query Project Model and optionally acquire only the needed M4 windows.

    ``window_analyzer`` replaces the model-call boundary for deterministic
    integration tests. The default path runs the production VLM.
    """
    project = Path(project)
    source_revision, authoritative_model = project_model_store.load_project_model(project)
    if authoritative_model != model:
        raise RuntimeError(
            f"stale Project Model input; supplied evidence is not revision {source_revision}"
        )
    model = authoritative_model
    root = Path(memory_root).expanduser() if memory_root else None
    registry = model_registry.load_registry()
    target_edition = ("commercial" if workload_purpose == "commercial_client"
                      else "personal")
    if workload_purpose not in model_registry.PURPOSES:
        raise ValueError(f"unknown workload purpose {workload_purpose!r}")
    asset = next(
        (row for row in model["assets"] if row.get("content_digest") == source_digest),
        None,
    )
    if asset is None:
        raise ValueError("source digest is not an asset in the Project Model")
    clock_id = (plan.get("scope") or {}).get("clock_id")
    clock = next(
        row
        for row in model["clocks"]
        if row["id"] == clock_id and row["domain"].get("asset_id") == asset["id"]
    )
    asset_id, clock_id = asset["id"], clock["id"]

    def cached(*, lane: str, start_s: float, end_s: float):
        if lane != "m4":
            return None
        memory = source_memory.read_scenes(source_digest, root)
        for row in (memory or {}).get("window_coverage", []):
            evidence_id = _m4_window_evidence_id(
                source_digest, row.get("start_s"), row.get("end_s"))
            producer_id = row.get("producer_project_id")
            producer_root = row.get("producer_project_root")
            if not producer_id or not producer_root or not row.get("model_provenance"):
                continue
            if (
                row.get("status") == "complete"
                and row.get("source_digest") == source_digest
                and row.get("start_s") <= start_s
                and row.get("end_s") >= end_s
            ):
                reuse = model_evidence_provenance.reuse_evidence(
                    [{"id": evidence_id}], [row["model_provenance"]],
                    source_project_id=producer_id,
                    target_project_id=model["id"],
                    edition=target_edition, purpose=workload_purpose,
                    source_project_root=producer_root,
                    target_project_root=project,
                    audit_path=(project / project_model_store.STORE_DIR /
                                model_evidence_provenance.AUDIT_FILE),
                    registry=registry,
                )
                if not reuse["reused"]:
                    continue
                return {
                    "sufficient": True,
                    "coverage": "complete",
                    "producer_version": row["producer_version"],
                }
        return None

    def produce(
        *,
        lane: str,
        start_s: float,
        end_s: float,
        budget_seconds: float,
        max_peak_rss_gb: float,
    ):
        if lane != "m4":
            return {
                "measured": False,
                "cost_seconds": 0,
                "peak_rss_gb": 0,
                "producer_version": PRODUCER_VERSION,
            }
        generated_provenance = None
        if window_analyzer is None:
            generated_provenance = model_evidence_provenance.record_for_generation(
                [_m4_window_evidence_id(source_digest, start_s, end_s)],
                MODEL_IMPLEMENTATION_ID, workload_purpose, registry=registry)
            if generated_provenance["output_reuse"]["reuse_scope"] == "benchmark_only":
                raise ValueError("benchmark-only model output cannot enter M4 evidence")
            if generated_provenance["output_reuse"]["class"] == "personal_noncommercial":
                cache_root = (root or source_memory.memory_root()).resolve()
                if not cache_root.is_relative_to(project.resolve()):
                    raise ValueError(
                        "personal model output requires a project-private Source Memory root")
        if window_analyzer is None:
            source_path = Path(source_file)
            if not source_path.is_file():
                raise ValueError(f"source media is unavailable: {source_file}")
            from library.tools.footage_identity import fingerprint

            live_digest = fingerprint(str(source_path))["content_digest"]
            if live_digest != source_digest:
                raise ValueError("source media changed since its Project Model digest")
        import tempfile

        from library.tools.analysis import vision_pipeline_v3 as vision

        started = time.perf_counter()
        temp = Path(tempfile.mkdtemp(prefix="vi12-m4-"))
        try:
            if window_analyzer is not None:
                measured_result = window_analyzer(
                    start_s=start_s, end_s=end_s, source_file=source_file
                )
                measured = bool(measured_result.get("measured"))
                scenes = measured_result.get("scenes") or []
            else:
                clips = vision.extract_video_clips(
                    source_file,
                    duration_seconds,
                    temp,
                    window_s=WINDOW_SECONDS,
                    window_starts_s=[start_s],
                    with_audio=True,
                )
                if not clips:
                    measured, scenes = False, []
                else:
                    index = vision.load_temporal_index(source_file)
                    with vision._LazyAnalyzer(None, str(project)) as analyzer:
                        result = vision.analyze_windows(
                            analyzer,
                            clips,
                            duration_seconds,
                            index,
                            "",
                            request_prefix="vi12",
                        )
                    row = result[0]
                    measured = bool(row.get("scene_measured")) and not row.get(
                        "parse_error"
                    )
                    scenes = row.get("scene") or []
            cost = time.perf_counter() - started
            peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_gb = peak_rss / (1024**3 if sys.platform == "darwin" else 1024**2)
            if cost > budget_seconds or rss_gb > max_peak_rss_gb:
                measured = False
            if measured:
                source_memory.merge_scene_window(
                    source_digest,
                    source_file,
                    start_s=start_s,
                    end_s=min(start_s + WINDOW_SECONDS, duration_seconds),
                    scenes=scenes,
                    producer_version=PRODUCER_VERSION,
                    source_digest=source_digest,
                    measured=True,
                    model_provenance=generated_provenance,
                    producer_project_id=model["id"],
                    producer_project_root=str(project.resolve()),
                    root=root,
                )
            return {
                "measured": measured,
                "cost_seconds": cost,
                "peak_rss_gb": rss_gb,
                "producer_version": PRODUCER_VERSION,
                "samples": len(scenes),
                "coverage": "complete" if measured else "incomplete",
            }
        finally:
            import shutil

            shutil.rmtree(temp, ignore_errors=True)

    def refresh(_old_model):
        interval = plan.get("scope") or {"start_s": 0.0, "end_s": duration_seconds}
        if interval.get("start_s") is None or interval.get("end_s") is None:
            interval = {"start_s": 0.0, "end_s": duration_seconds}
        for attempt in range(3):
            revision, current = project_model_store.load_project_model(project)
            rows, provenance = _m4_rows(
                current,
                source_digest,
                interval,
                asset_id=asset_id,
                clock_id=clock_id,
                duration=duration_seconds,
                memory_root=root,
            )
            try:
                project_model_store.merge_evidence_records(
                    project,
                    observations=rows,
                    provenance=provenance,
                    expected_revision=revision,
                )
                break
            except RuntimeError as exc:
                if "stale project model revision" not in str(exc) or attempt == 2:
                    raise
        refreshed = project_model_store.load_project_model(project)[1]
        if window_analyzer is None:
            return filter_m4_by_reuse_policy(
                refreshed, project, edition=target_edition,
                purpose=workload_purpose, registry=registry)
        return refreshed

    query_model = model
    if window_analyzer is None:
        query_model = filter_m4_by_reuse_policy(
            model, project, edition=target_edition,
            purpose=workload_purpose, registry=registry)
    result = query_with_acquisition(
        query_model,
        plan,
        source_digest=source_digest,
        duration_seconds=duration_seconds,
        cached=cached,
        producers={"m4": produce},
        refresh_model=refresh,
        max_cost_seconds=max_cost_seconds,
        available_lanes=("m4",),
        producer_budgets={"m4": M4_WINDOW_COST_SECONDS},
        window_seconds_by_lane={"m4": WINDOW_SECONDS},
        producer_cost_per_window_seconds={"m4": M4_WINDOW_COST_SECONDS},
    )
    result["source_revision"] = source_revision
    result["result_revision"] = project_model_store.load_project_model(project)[0]
    return result


def verify_action_candidates(
    source_digest: str,
    candidates: list[dict[str, Any]],
    *,
    max_candidates: int = 8,
    memory_root: str | Path | None = None,
    model: Any = None,
) -> list[dict[str, Any]]:
    """Send only the highest ranked action intervals to the existing Gemma M8.

    M8 writes a separate cached verdict in source memory; the measured
    Project Model observations and candidate Event records are untouched.
    The caller can direct writes to a scratch memory root for evaluations.
    """
    if max_candidates < 0:
        raise ValueError("max_candidates must be non-negative")
    if max_candidates == 0:
        return []
    from library.tools import person_measurements, span_verification

    root = Path(memory_root).expanduser() if memory_root else None
    m3 = person_measurements.read_m3(source_digest, root)
    if m3 is None:
        return [{"candidate_id": row["id"], "status": "not_yet_analysed"}
                for row in candidates[:max_candidates]]
    ordered = sorted(candidates,
                     key=lambda row: (-len(row["evidence_ids"]),
                                      row["interval"]["start"], row["id"]))
    frames = m3["frames"]
    spans_by_kind: dict[str, list[dict[str, Any]]] = {}
    ids_by_kind: dict[str, list[str]] = {}
    deferred = []
    for candidate in ordered[:max_candidates]:
        interval = candidate["interval"]
        start_s, end_s = interval["start"] / 1000, interval["end"] / 1000
        selected = [frame for frame in frames
                    if start_s <= float(frame["t"]) <= end_s]
        if len(selected) < 2:
            deferred.append({"candidate_id": candidate["id"],
                             "status": "not_yet_analysed"})
            continue
        kind = candidate["kind"]
        spans_by_kind.setdefault(kind, []).append(
            {"start": start_s, "end": end_s,
             "frames": [{"t": float(frame["t"]), "d": 0.0,
                         "box": None} for frame in selected]})
        ids_by_kind.setdefault(kind, []).append(candidate["id"])
    if not spans_by_kind:
        return deferred
    statements = {
        "drink_from_cup": "drink from a cup",
        "pick_up_put_down": "pick up an object and put it back down",
        "hand_to_face": "touches their face or hair with a hand",
    }
    results = list(deferred)
    for kind, spans in spans_by_kind.items():
        statement = statements.get(kind)
        if statement is None:
            raise ValueError(f"no verifier statement for action {kind!r}")
        verdicts = span_verification.verify_spans(
            source_digest, spans, statement, root=root, model=model)
        results.extend(
            {"candidate_id": candidate_id, "status": verdict["answer"],
             "verdict": verdict}
            for candidate_id, verdict in zip(ids_by_kind[kind], verdicts))
    return results
