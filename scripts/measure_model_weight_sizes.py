#!/usr/bin/env python3
"""Re-measure the weight sizes recorded in the model registry's admission records.

The admission record of every optional model states how many bytes a machine
downloads for the weights the runtime actually loads, and where that number
was measured. This script re-reads those publisher sources and compares them
with the recorded numbers, so a stale size is caught rather than trusted.

It reads METADATA only: publisher file listings and content lengths. It
never downloads a model, and it never loads one.

    python3 scripts/measure_model_weight_sizes.py
    python3 scripts/measure_model_weight_sizes.py --json

Exit status is 0 when every recorded size still matches its source, 1 when
one differs, and 2 when a source could not be reached (which is not a
mismatch: an unreachable publisher is reported, never scored as agreement).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "library" / "model_registry" / "registry.json"
TIMEOUT = 60


def _get(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "ren-model-admission/1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _head_length(url: str) -> int | None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "ren-model-admission/1"}, method="HEAD")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        length = response.headers.get("Content-Length")
    return int(length) if length is not None else None


def _sizes_for(part: dict[str, Any]) -> dict[str, int]:
    method = part["method"]
    source = part["source"]
    if method == "hub_api_blobs":
        payload = _get(source)
        return {row.get("rfilename"): row.get("size")
                for row in payload.get("siblings", [])}
    if method == "hub_api_tree":
        payload = _get(source)
        return {row.get("path"): row.get("size") for row in payload
                if isinstance(row, dict)}
    if method == "github_release_asset":
        payload = _get(source)
        return {row.get("name"): row.get("size") for row in payload.get("assets", [])}
    if method == "zenodo_record_files":
        payload = _get(source)
        return {row.get("key"): row.get("size") for row in payload.get("files", [])}
    if method == "http_content_length":
        length = _head_length(source)
        return {name: length for name in part.get("files", [])}
    raise ValueError(f"unknown size method {method!r}")


def measure(record: dict[str, Any]) -> dict[str, Any]:
    """Compare one admission record's sizes with their publisher sources."""
    size = record.get("size") or {}
    rows = []
    total = 0
    for part in size.get("parts", []):
        try:
            measured = _sizes_for(part)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            rows.append({"source": part.get("source"), "status": "unreachable",
                         "detail": str(exc)})
            continue
        part_total = 0
        detail = []
        for name in part.get("files", []):
            found = measured.get(name)
            detail.append({"file": name, "recorded": None, "measured": found})
            if found is None:
                continue
            part_total += found
        recorded = part.get("bytes")
        rows.append({
            "source": part.get("source"),
            "status": "match" if part_total == recorded else "DIFFERS",
            "recorded_bytes": recorded,
            "measured_bytes": part_total,
            "files": detail,
        })
        total += part_total
    return {"recorded_bytes": size.get("bytes"), "measured_bytes": total,
            "parts": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true",
                        help="print the full measurement as JSON")
    args = parser.parse_args()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    results = []
    for implementation in registry.get("implementations", []):
        record = implementation.get("admission")
        if not record:
            continue
        row = measure(record)
        row["implementation_id"] = implementation.get("id")
        results.append(row)
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for row in results:
            print(f"{row['implementation_id']}\t{row['recorded_bytes']}\t"
                  f"{row['measured_bytes']}\t"
                  + ", ".join(part["status"] for part in row["parts"]))
    differs = [row for row in results
               if any(part["status"] == "DIFFERS" for part in row["parts"])]
    unreachable = [row for row in results
                   if any(part["status"] == "unreachable" for part in row["parts"])]
    if differs:
        print(f"{len(differs)} recorded size(s) no longer match the publisher")
        return 1
    if unreachable:
        print(f"{len(unreachable)} publisher source(s) could not be reached")
        return 2
    print(f"every recorded size matches its publisher ({len(results)} models)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
