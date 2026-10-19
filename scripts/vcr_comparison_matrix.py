#!/usr/bin/env python3
"""Compare direct, staged and full rebuild routes on a disposable reel.

The adapter owns production operations and writes one JSON receipt per
route. This script owns fixture isolation, the control matrix, receipt
validation and comparison. No adapter is included yet: the live adapter
must route mutations through the Resolve broker and production edit paths.

Example, once a broker-backed adapter is supplied:
    bin/vep scripts/vcr_comparison_matrix.py \
      --runner bin/vep scripts/vcr_live_route.py \
      --output /tmp/vcr-comparison.json

The runner receives --fixture-dir, --control, --route and --receipt. Each
invocation gets a fresh copy of the representative fixture. Receipt fields
are defined by ``RECEIPT_FIELDS`` below; no measurements are inferred from
the process exit status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "vcr_representative_reel"
ROUTES = ("direct", "staged", "full_rebuild")
REQUIRED_KINDS = {
    "video_with_linked_audio", "graphic_overlay", "caption",
    "fusion_comp", "grade",
}
RECEIPT_FIELDS = {
    "schema", "control", "route", "final_state_sha256", "render_sha256",
    "rerendered_components", "changed_placements", "resolve_lease_seconds",
    "engine_revision", "verification",
}


class MatrixError(RuntimeError):
    """A fixture, route or measurement receipt failed its contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): _sha256(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != "route-receipt.json"
    }


def _load_fixture() -> dict:
    manifest = json.loads((FIXTURE / "fixture.json").read_text(
        encoding="utf-8"))
    if manifest["schema"] != "vcr_representative_reel/1":
        raise MatrixError(f"unsupported fixture schema {manifest['schema']!r}")
    kinds = {asset["kind"] for asset in manifest["assets"]}
    missing = sorted(REQUIRED_KINDS - kinds)
    if missing:
        raise MatrixError(f"representative fixture is missing {missing}")
    return manifest


def _receipt(path: Path, control: str, route: str) -> dict:
    if not path.is_file():
        raise MatrixError(f"route driver did not write receipt {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(RECEIPT_FIELDS - value.keys())
    if missing:
        raise MatrixError(f"{control}/{route} receipt lacks {missing}")
    if value["schema"] != "vcr_route_receipt/1":
        raise MatrixError(f"{control}/{route} has unsupported receipt schema")
    if value["control"] != control or value["route"] != route:
        raise MatrixError(f"{control}/{route} receipt identifies another run")
    if value["verification"] != "passed":
        raise MatrixError(f"{control}/{route} verification failed")
    if not isinstance(value["rerendered_components"], list):
        raise MatrixError(f"{control}/{route} rerender list is not a list")
    if not isinstance(value["changed_placements"], list):
        raise MatrixError(f"{control}/{route} placement list is not a list")
    if float(value["resolve_lease_seconds"]) < 0:
        raise MatrixError(f"{control}/{route} reports negative lease time")
    return value


def run_matrix(runner: list[str], output: Path) -> dict:
    fixture = _load_fixture()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results = []
    with tempfile.TemporaryDirectory(prefix="vcr-matrix-") as temporary:
        root = Path(temporary)
        for control in fixture["controls"]:
            control_id = control["id"]
            receipts = {}
            for route in ROUTES:
                run_dir = root / control_id / route
                shutil.copytree(FIXTURE, run_dir)
                receipt_path = run_dir / "route-receipt.json"
                command = [*runner, "--fixture-dir", str(run_dir),
                           "--control", control_id, "--route", route,
                           "--receipt", str(receipt_path)]
                started = time.perf_counter()
                completed = subprocess.run(
                    command, cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", check=False)
                elapsed = time.perf_counter() - started
                if completed.returncode:
                    detail = completed.stderr.strip() or completed.stdout.strip()
                    raise MatrixError(
                        f"{control_id}/{route} driver exited "
                        f"{completed.returncode}: {detail}")
                result = _receipt(receipt_path, control_id, route)
                result["driver_wall_seconds"] = round(elapsed, 6)
                result["fixture_assets_sha256"] = _asset_hashes(run_dir)
                receipts[route] = result
            full = receipts["full_rebuild"]
            for route in ("direct", "staged"):
                current = receipts[route]
                if current["final_state_sha256"] != full["final_state_sha256"]:
                    raise MatrixError(
                        f"{control_id}/{route} final timeline differs from full rebuild")
                if current["render_sha256"] != full["render_sha256"]:
                    raise MatrixError(
                        f"{control_id}/{route} rendered output differs from full rebuild")
                current["rerenders_avoided"] = len(
                    set(full["rerendered_components"])
                    - set(current["rerendered_components"]))
                current["placements_avoided"] = len(
                    set(full["changed_placements"])
                    - set(current["changed_placements"]))
                current["resolve_lease_seconds_avoided"] = round(
                    float(full["resolve_lease_seconds"])
                    - float(current["resolve_lease_seconds"]), 6)
            results.append({"control": control_id, "routes": receipts})

    report = {
        "schema": "vcr_comparison_matrix/1",
        "created_at": stamp,
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "fixture_sha256": _sha256(FIXTURE / "fixture.json"),
        "fixture_assets_sha256": _asset_hashes(FIXTURE),
        "fixture_profile": fixture["profile"],
        "controls": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runner", nargs=argparse.REMAINDER, required=True,
                        help="route-driver command and arguments")
    args = parser.parse_args()
    if not args.runner:
        parser.error("--runner needs a command")
    try:
        report = run_matrix(args.runner, args.output)
    except (OSError, ValueError, MatrixError) as exc:
        print(f"VCR matrix refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(args.output),
                      "controls": len(report["controls"]),
                      "routes_per_control": len(ROUTES)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
