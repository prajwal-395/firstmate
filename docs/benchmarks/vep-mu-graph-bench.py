#!/usr/bin/env python3
"""DATA-051: benchmark the current JSON-sidecar storage model.

Generates a synthetic Project Model at scale (no footage, no media, no
model passes) and measures what the punchlist asks for before any index
infrastructure is evaluated: sidecar load time, linear-scan query cost
versus the EvidenceIndex, contradiction-scan cost, revision-history scan
cost, and peak memory. Prints a JSON report; the scorecard in
vep-mu-graph-scorecard-2026-10-16.md records the numbers and the
no-database decision they support.

With ``--model`` it ALSO measures a real Project Model - the snapshot
of a Ren-owned scratch project - and publishes the numbers beside the
synthetic ones: the index build, the contradiction scan and the
multi-hop traversal over the whole model, and what the B-16 trace of
one named entity returns. The snapshot is READ, never written, and no
project is touched.

Usage: python3 docs/benchmarks/vep-mu-graph-bench.py [--obs N]
       [--model PATH] [--entity REF] [--hops N] [--real-only]
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from library.tools.evidence_graph import EvidenceIndex, find_contradictions  # noqa: E402
from library.tools import project_model_store  # noqa: E402
from library.tools.evidence_lifecycle import (  # noqa: E402
    collection_history, normalize_envelope_preprocessing, typed_confidence)
from library.tools.evidence_license import stamp_license  # noqa: E402


def build_model(n_obs: int) -> dict:
    clocks = [{"id": "c1", "ticks_per_second": 1000}]
    entities = [{"id": f"speaker-{i % 8}", "kind": "speaker",
                 "label": f"Speaker {i % 8}"} for i in range(8)]
    observations = []
    for i in range(n_obs):
        start = (i * 37) % 3_600_000
        end = start + 500 + (i % 5) * 250
        payload: dict = {"voice_track": f"v{i % 64}",
                         "text": f"utterance number {i} about revenue"}
        if i % 3 == 0:
            payload.update({"object_id": f"obj-{i % 50}",
                            "box": [0.1, 0.1, 0.3, 0.3]})
        observations.append({
            "id": f"obs-{i}", "asset_id": "asset-1",
            "interval": {"clock_id": "c1", "start": start, "end": end},
            "modality": "voice_turn" if i % 2 else "object.detection",
            "payload": payload, "provenance_id": "prov-d-m1",
            "certainty": "measured", "confidence": None})
    links = [{"id": f"l{i}", "entity_id": f"speaker-{i % 8}",
              "observation_id": f"obs-{i}", "method": "diarization",
              "status": "candidate", "supporting_observation_ids": [],
              "contradicting_observation_ids": []} for i in range(n_obs)]
    return {"id": "bench", "schema_version": 1, "clocks": clocks,
            "entities": entities, "observations": observations,
            "identity_links": links, "events": [], "relations": [],
            "tracks": []}


def linear_overlapping(model: dict, start: int, end: int) -> list:
    return [obs for obs in model["observations"]
            if obs["interval"]["start"] < end
            and obs["interval"]["end"] > start]


def bench(n_obs: int) -> dict:
    model = build_model(n_obs)
    with tempfile.TemporaryDirectory() as tmp:
        sidecar = Path(tmp) / "snapshot.json"
        tick = time.perf_counter()
        sidecar.write_text(json.dumps(model, ensure_ascii=False),
                           encoding="utf-8")
        dump_s = time.perf_counter() - tick
        size_mb = sidecar.stat().st_size / 1_000_000
        tick = time.perf_counter()
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
        load_s = time.perf_counter() - tick

    windows = [(i * 997) % 3_600_000 for i in range(200)]
    tick = time.perf_counter()
    for start in windows:
        linear_overlapping(loaded, start, start + 2000)
    linear_s = time.perf_counter() - tick

    tracemalloc.start()
    tick = time.perf_counter()
    index = EvidenceIndex(loaded)
    build_s = time.perf_counter() - tick
    tick = time.perf_counter()
    for start in windows:
        index.observations_overlapping("c1", start, start + 2000)
    indexed_s = time.perf_counter() - tick
    tick = time.perf_counter()
    for tick_mark in range(0, 3_600_000, 360_000):
        index.observations_at("c1", tick_mark)
    point_s = time.perf_counter() - tick
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    tick = time.perf_counter()
    contradictions = find_contradictions(index)
    contra_s = time.perf_counter() - tick

    # Parity: indexed windows return exactly the linear-scan row sets.
    for start in windows[:20]:
        assert {o["id"] for o in
                index.observations_overlapping("c1", start, start + 2000)} == {
            o["id"] for o in linear_overlapping(loaded, start, start + 2000)}

    cold = subprocess.run(
        [sys.executable, "-c",
         "import time; t=time.perf_counter(); "
         "from library.tools.evidence_graph import EvidenceIndex; "
         "print(time.perf_counter()-t)"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        check=True)

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        seed = json.loads((ROOT / "native/ren-protocol/fixtures"
                           / "project-model.json").read_text(encoding="utf-8"))
        project_model_store.commit_project_model(
            project, seed, expected_revision=0, reason="bench seed")
        revisions = 20
        tick = time.perf_counter()
        for i in range(revisions):
            project_model_store.merge_evidence_records(
                project, observations=[{
                    "id": "obs-bench", "asset_id": "asset-source",
                    "interval": {"clock_id": "clock-source",
                                "start": 0, "end": 24},
                    "modality": "scene", "payload": {"note": i},
                    "provenance_id": "prov-bench",
                    "confidence": None}],
                expected_revision=1 + i)
        merge_s = time.perf_counter() - tick
        tick = time.perf_counter()
        chain = collection_history(project, "observations", "obs-bench")
        history_s = time.perf_counter() - tick
        assert len(chain) == revisions
        assert chain[0]["change"] == "added"
        tick = time.perf_counter()
        envelope = normalize_envelope_preprocessing({
            "producer": "bench", "producer_version": "1",
            "source_digest": "sha256:bench",
            "sampling_method": "bench-sampling",
            "coverage_intervals": [], "coverage_status": "sampled"})
        license_rows = stamp_license(
            [{"id": "o1"}], "minilm.all_minilm_l6_v2", "commercial_client")
        provenance_s = time.perf_counter() - tick
        assert envelope["derivation_digest"].startswith("sha256:")
        assert license_rows[0]["license_context"]["approval_status"] == "approved"
        assert typed_confidence(0.5, "rank", "bench")["kind"] == "rank"

    return {"n_observations": n_obs, "sidecar_mb": round(size_mb, 2),
            "sidecar_dump_s": round(dump_s, 4),
            "sidecar_load_s": round(load_s, 4),
            "linear_200_windows_s": round(linear_s, 4),
            "index_build_s": round(build_s, 4),
            "index_200_windows_s": round(indexed_s, 4),
            "index_10_point_queries_s": round(point_s, 4),
            "contradiction_scan_s": round(contra_s, 4),
            "contradictions_found": len(contradictions),
            "index_peak_mb": round(peak / 1_000_000, 2),
            "cold_import_s": round(float(cold.stdout.strip()), 4),
            "history_revisions": revisions,
            "history_20_merges_s": round(merge_s, 4),
            "history_chain_scan_s": round(history_s, 4),
            "provenance_helpers_s": round(provenance_s, 4)}


def _pick_entity(model: dict, wanted: str | None) -> str:
    """The entity the real-model trace follows.

    With no name given, the busiest entity wins: the one with the most
    identity links, then the most relations, then the id - so the
    benchmark picks the same entity on every run.
    """
    if wanted:
        return wanted
    links: dict[str, int] = {}
    for link in model.get("identity_links") or []:
        if isinstance(link, dict) and isinstance(link.get("entity_id"), str):
            links[link["entity_id"]] = links.get(link["entity_id"], 0) + 1
    relations: dict[str, int] = {}
    for relation in model.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        for key in ("subject_id", "object_id"):
            value = relation.get(key)
            if isinstance(value, str):
                relations[value] = relations.get(value, 0) + 1
    entities = [row.get("id") for row in model.get("entities") or []
                if isinstance(row, dict) and isinstance(row.get("id"), str)]
    if not entities:
        raise SystemExit("the model holds no entity to trace")
    return sorted(entities, key=lambda entity: (
        -links.get(entity, 0), -relations.get(entity, 0), entity))[0]


def bench_real_model(snapshot_path: Path, entity_ref: str | None,
                     hops: int) -> dict:
    """Measure the graph layer over a REAL Project Model.

    The snapshot is read and never written: no project is touched, and
    the measurement is reproducible from the model alone.
    """
    from library.tools import evidence_trace

    tick = time.perf_counter()
    document = json.loads(snapshot_path.read_text(encoding="utf-8"))
    load_s = time.perf_counter() - tick
    model = document.get("model", document)

    tick = time.perf_counter()
    index = EvidenceIndex(model)
    index_build_s = time.perf_counter() - tick

    entity = _pick_entity(model, entity_ref)
    tick = time.perf_counter()
    trace = evidence_trace.trace(model, entity, hops=hops)
    trace_s = time.perf_counter() - tick

    # The answer is reproducible from the model alone: the same model
    # gives the same answer, byte for byte.
    again = evidence_trace.trace(model, entity, hops=hops)
    reproducible = (json.dumps(trace, sort_keys=True, default=str)
                    == json.dumps(again, sort_keys=True, default=str))

    tick = time.perf_counter()
    contradictions = find_contradictions(index)
    contra_s = time.perf_counter() - tick

    tick = time.perf_counter()
    traversal = index.traverse(entity, hops=hops)
    traversal_s = time.perf_counter() - tick

    edges = traversal.get("edges") or []
    relation_edges = [edge for edge in edges if edge.get("kind") == "relation"]
    link_edges = [edge for edge in edges
                  if edge.get("kind") == "identity_link"]
    leaves = trace.get("leaf_observation_ids") or []
    sources = trace.get("sources") or []
    placed = [row for row in sources if row.get("status") == "resolved"]
    with_file = [row for row in placed if row.get("source_file")]
    with_seconds = [row for row in placed
                    if isinstance(row.get("start_seconds"), (int, float))
                    and isinstance(row.get("end_seconds"), (int, float))]

    # The gate: the traversal reaches an identity link and a relation
    # edge per hop, every leaf observation names its file and its exact
    # seconds, and the answer is reproducible.
    assert link_edges, f"the {hops}-hop traversal of {entity} reached no identity link"
    assert relation_edges, f"the {hops}-hop traversal of {entity} reached no relation edge"
    assert len(leaves) == len(sources), (
        "a leaf observation was dropped from the answer")
    assert len(with_file) == len(leaves), (
        "a leaf observation carries no source file")
    assert len(with_seconds) == len(leaves), (
        "a leaf observation carries no exact seconds")
    assert reproducible, "the same model gave two different answers"

    return {
        "snapshot": str(snapshot_path),
        "observations": len(model.get("observations") or []),
        "entities": len(model.get("entities") or []),
        "identity_links": len(model.get("identity_links") or []),
        "relations": len(model.get("relations") or []),
        "events": len(model.get("events") or []),
        "interpretations": len(model.get("interpretations") or []),
        "tracks": len(model.get("tracks") or []),
        "snapshot_load_s": round(load_s, 3),
        "index_build_s": round(index_build_s, 3),
        "trace_s": round(trace_s, 3),
        "traversal_s": round(traversal_s, 3),
        "contradiction_scan_s": round(contra_s, 3),
        "contradictions_found": len(contradictions),
        "entity": entity,
        "hops": hops,
        "traversal": {
            "nodes": len(traversal.get("nodes") or []),
            "edges": len(edges),
            "relation_edges": len(relation_edges),
            "identity_link_edges": len(link_edges),
        },
        "trace": {
            "observations": len(trace.get("observations") or []),
            "identity_links": len(trace.get("identity_links") or []),
            "relations": len(trace.get("relations") or []),
            "events": len(trace.get("events") or []),
            "interpretations": len(trace.get("interpretations") or []),
            "leaf_observations": len(leaves),
            "leaves_with_source_file": len(with_file),
            "leaves_with_exact_seconds": len(with_seconds),
            "reproducible": reproducible,
        },
    }


def main() -> None:
    args = sys.argv[1:]
    n_obs = int(args[args.index("--obs") + 1]) if "--obs" in args else 20000
    report = {} if "--real-only" in args else bench(n_obs)
    if "--model" in args:
        snapshot = Path(args[args.index("--model") + 1]).expanduser()
        entity = (args[args.index("--entity") + 1]
                  if "--entity" in args else None)
        hops = int(args[args.index("--hops") + 1]) if "--hops" in args else 2
        report["real_model"] = bench_real_model(snapshot, entity, hops)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
