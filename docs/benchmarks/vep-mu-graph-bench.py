#!/usr/bin/env python3
"""DATA-051: benchmark the current JSON-sidecar storage model.

Generates a synthetic Project Model at scale (no footage, no media, no
model passes) and measures what the punchlist asks for before any index
infrastructure is evaluated: sidecar load time, linear-scan query cost
versus the EvidenceIndex, contradiction-scan cost, revision-history scan
cost, and peak memory. Prints a JSON report; the scorecard in
vep-mu-graph-scorecard-2026-10-16.md records the numbers and the
no-database decision they support.

Usage: python3 docs/benchmarks/vep-mu-graph-bench.py [--obs N]
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


def main() -> None:
    n_obs = int(sys.argv[sys.argv.index("--obs") + 1]) if "--obs" in sys.argv else 20000
    print(json.dumps(bench(n_obs), indent=2))


if __name__ == "__main__":
    main()
