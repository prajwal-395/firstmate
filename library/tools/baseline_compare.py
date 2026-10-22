"""Compare a new measurement against the M04 performance baselines.

The baselines live in one committed file
(`docs/benchmarks/m04-performance-baselines.json`): each names its
source PR/report, the workload it was measured on, its denominators,
and its quality and speed metrics. This module re-reads a NEW
measurement and prints the quality and speed delta against each
baseline - the automated comparison the M04 acceptance asks for.

A measurement bundle is JSON mapping a baseline id to either a native
artifact path (re-read by a per-baseline extractor) or inline values
with a provenance note naming where the numbers came from:

    {"m04-targeted-query": {"artifact": "path/to/targeted-reasoning.json"},
     "m04-shared-decode": {"metrics": {"shared_decode_s": 25.1, ...},
                           "provenance": "bench_shared_decode --json, 2026-10-23"}}

Quality metrics compare exact (any quality regression fails, because a
regression reverts). Speed metrics compare within a relative
tolerance - the source reports document machine-load variance, so a
speed delta inside tolerance is reported, not failed. Nothing here
invents a measurement: a metric the new measurement does not carry is
reported as unmeasured, never defaulted.

Usage:

    python3 -m library.tools.baseline_compare --measurement new.json
    python3 -m library.tools.baseline_compare --measurement new.json \\
        --baseline docs/benchmarks/m04-performance-baselines.json \\
        --speed-tolerance 0.25 --json-out deltas.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: Exit codes: 0 means every compared metric held (or improved);
#: 2 means a quality metric regressed, a speed metric left tolerance,
#: or a measurement was missing where the baseline promises one.
EXIT_OK = 0
EXIT_MISMATCH = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = (REPO_ROOT / "docs" / "benchmarks"
                    / "m04-performance-baselines.json")


def _read_json(path: str | Path, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BaselineCompareError(f"{what} not found: {path}")
    except json.JSONDecodeError as exc:
        raise BaselineCompareError(f"{what} is not JSON: {path} ({exc})")
    except OSError as exc:
        raise BaselineCompareError(f"{what} unreadable: {path} ({exc})")


class BaselineCompareError(Exception):
    """A usage or input defect: unknown id, unreadable file, missing key."""


def _fraction(text: str, where: str) -> float:
    """Parse "32/32" into a rate. Anything else is refused, not guessed."""
    parts = str(text).strip().split("/")
    if len(parts) != 2:
        raise BaselineCompareError(f"{where} is not a fraction: {text!r}")
    try:
        num, den = float(parts[0]), float(parts[1])
    except ValueError:
        raise BaselineCompareError(f"{where} is not a fraction: {text!r}")
    if den == 0:
        raise BaselineCompareError(f"{where} divides by zero: {text!r}")
    return num / den


def _need(mapping: Any, key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise BaselineCompareError(f"{where} carries no {key!r}")
    return mapping[key]


def extract_targeted_query(artifact: Any) -> dict[str, Any]:
    """Read a targeted-reasoning.py JSON output back into metrics."""
    routes = _need(artifact, "routes", "targeted-reasoning output")
    targeted = _need(routes, "targeted", "targeted-reasoning routes")
    whole = _need(routes, "whole_view", "targeted-reasoning routes")
    reel = _need(artifact, "reel_plan", "targeted-reasoning output")
    return {
        "answer_rate": _fraction(_need(targeted, "answered",
                                       "targeted route"), "answered"),
        "correct_rate": _fraction(_need(targeted, "correct",
                                        "targeted route"), "correct"),
        "context_bytes_per_call": float(_need(targeted,
                                              "context_bytes_per_call",
                                              "targeted route")),
        "whole_view_bytes_per_call": float(_need(whole,
                                                 "context_bytes_per_call",
                                                 "whole_view route")),
        "context_tokens_per_call": float(_need(targeted,
                                               "o200k_tokens_per_call",
                                               "targeted route")),
        "reel_speaker_set_match": _fraction(_need(reel, "speaker_set_match",
                                                  "reel plan"),
                                             "speaker_set_match"),
        "reel_speaker_share_within_0.10": _fraction(
            _need(reel, "speaker_share_within_0.10", "reel plan"),
            "speaker_share_within_0.10"),
    }


def _median(values: list[float], where: str) -> float:
    if not values:
        raise BaselineCompareError(f"{where} has no rows to take a median of")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def extract_progressive_retrieval(artifact: Any) -> dict[str, Any]:
    """Read a progressive_fidelity_bench.py --json report into metrics."""
    rows = _need(artifact, "rows", "progressive-fidelity report")
    if not isinstance(rows, list) or not rows:
        raise BaselineCompareError("progressive-fidelity report has no rows")
    first, before_first, full, identical = [], [], [], 0
    for index, row in enumerate(rows):
        where = f"progressive-fidelity row {index}"
        cold = _need(row, "cold", where)
        shared = _need(row, "shared", where)
        before = _need(cold, "before", where)
        after = _need(cold, "after", where)
        first.append(float(_need(after, "seconds_first", where)))
        before_first.append(float(_need(before, "seconds_full", where)))
        full.append(float(_need(after, "seconds_full", where)))
        if _need(shared, "answers_identical", where) is True:
            identical += 1
    return {
        "first_answer_s": _median(first, "coarse first answers"),
        "before_first_answer_s": _median(before_first, "before answers"),
        "full_fidelity_s": _median(full, "full-fidelity answers"),
        "answers_identical_rate": identical / len(rows),
    }


def extract_shared_decode(artifact: Any) -> dict[str, Any]:
    """Read a bench_shared_decode --json report into metrics."""
    where = "shared-decode report"
    return {
        "independent_decode_s": float(_need(artifact,
                                            "independent_decode_seconds",
                                            where)),
        "shared_decode_s": float(_need(artifact, "shared_decode_seconds",
                                       where)),
        "frames_byte_identical": _need(artifact, "frames_byte_identical",
                                       where) is True,
        "picture_output_identical": _need(artifact, "picture_output_identical",
                                          where) is True,
        "defects_output_identical": _need(artifact, "defects_output_identical",
                                          where) is True,
        "cache_bytes": int(_need(artifact, "cache_bytes", where)),
    }


def extract_coarse_waves(artifact: Any) -> dict[str, Any]:
    """Read ledger-derived wave walls (see the baseline workload) into metrics."""
    where = "coarse-waves measurement"
    return {
        "serial_wall_s": float(_need(artifact, "serial_wall_s", where)),
        "waved_wall_s": float(_need(artifact, "waved_wall_s", where)),
        "frames_byte_identical": _need(artifact, "frames_byte_identical",
                                       where) is True,
    }


def extract_demand_cache(artifact: Any) -> dict[str, Any]:
    """Read a bench_demand_analysis --json report into metrics."""
    where = "demand-analysis report"
    analysis = _need(artifact, "analysis_seconds", where)
    frames = _need(artifact, "frames", where)
    return {
        "analysis_before_s": float(_need(analysis, "before", where)),
        "analysis_after_s": float(_need(analysis, "after", where)),
        "frames_measured_after": int(_need(frames, "after", where)),
        "frames_reused_after": int(_need(frames, "reused", where)),
        "identical_answer": _need(artifact, "identical_answer",
                                  where) is True,
        "identical_rows": _need(artifact, "identical_rows", where) is True,
    }


def extract_visual_recall(artifact: Any) -> dict[str, Any]:
    """Read a retrieval_eval --out-json report (held-out set) into metrics."""
    where = "retrieval-eval report"
    eval_set = _need(artifact, "eval_set", where)
    if "held-out" not in str(eval_set) and "heldout" not in str(eval_set):
        raise BaselineCompareError(
            f"visual-recall baseline is the held-out set, not {eval_set!r}")
    summary = _need(artifact, "summary", where)
    editorial = _need(summary, "editorial", where)
    visual = _need(_need(editorial, "by_lane", where), "visual", where)
    consequence = _need(editorial, "false_positive_consequence", where)
    return {
        "visual_macro_recall_capped": float(_need(visual, "macro_recall",
                                                  where)),
        "visual_macro_recall_uncapped": float(_need(
            visual, "macro_recall_uncredited", where)),
        "visual_macro_precision": float(_need(visual, "macro_precision",
                                               where)),
        "wrong_seconds_selected": float(_need(consequence,
                                              "wrong_seconds_selected",
                                              where)),
        "false_positive_seconds": float(_need(consequence,
                                              "false_positive_seconds",
                                              where)),
    }


#: Native-artifact readers, one per baseline id.
EXTRACTORS = {
    "m04-targeted-query": extract_targeted_query,
    "m04-progressive-retrieval": extract_progressive_retrieval,
    "m04-shared-decode": extract_shared_decode,
    "m04-coarse-waves": extract_coarse_waves,
    "m04-demand-cache": extract_demand_cache,
    "m04-visual-recall": extract_visual_recall,
}


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _verdict(kind: str, direction: str, baseline: Any, measured: Any,
             tolerance: float) -> str:
    """OK when the metric held, IMPROVED/REGRESSED when it moved."""
    if isinstance(baseline, bool) or isinstance(measured, bool):
        if measured == baseline:
            return "OK"
        better = (measured is True) == (direction == "higher")
        return "IMPROVED" if better else "REGRESSED"
    delta = float(measured) - float(baseline)
    if delta == 0:
        return "OK"
    better = (delta > 0) == (direction == "higher")
    allowed = abs(tolerance * float(baseline)) if baseline != 0 else 0.0
    if abs(delta) <= allowed:
        return "OK"
    return "IMPROVED" if better else "REGRESSED"


def compare(baseline_path: str | Path, bundle: dict[str, Any],
            speed_tolerance: float) -> dict[str, Any]:
    """Compare every baseline id in the bundle; refuse what is missing."""
    stored = _read_json(baseline_path, "baseline file")
    wanted = _need(stored, "baselines", "baseline file")
    by_id = {entry["id"]: entry for entry in wanted}
    lines: list[str] = []
    results: list[dict[str, Any]] = []
    failures = 0
    for baseline_id, entry in bundle.items():
        if baseline_id not in by_id:
            raise BaselineCompareError(f"unknown baseline id: {baseline_id!r}")
        spec = by_id[baseline_id]
        if not isinstance(entry, dict):
            raise BaselineCompareError(
                f"measurement for {baseline_id!r} is not an object")
        if "artifact" in entry:
            if baseline_id not in EXTRACTORS:
                raise BaselineCompareError(
                    f"no native reader for {baseline_id!r}")
            artifact = _read_json(entry["artifact"],
                                  f"{baseline_id} artifact")
            measured = EXTRACTORS[baseline_id](artifact)
            provenance = f"artifact {entry['artifact']}"
        elif "metrics" in entry:
            measured = entry["metrics"]
            provenance = str(entry.get("provenance", "inline (unstated)"))
            if not isinstance(measured, dict):
                raise BaselineCompareError(
                    f"inline metrics for {baseline_id!r} are not an object")
        else:
            raise BaselineCompareError(
                f"measurement for {baseline_id!r} names neither "
                "'artifact' nor 'metrics'")
        lines.append(f"== {baseline_id}: {spec['title']}")
        lines.append(f"   baseline: {spec['source']['report']} "
                     f"(PR #{spec['source']['pr']})")
        lines.append(f"   measured: {provenance}")
        row_results: list[dict[str, Any]] = []
        for metric in spec["metrics"]:
            name = metric["name"]
            if name not in measured:
                lines.append(f"   {name}: UNMEASURED "
                             f"(baseline {_format_value(metric['baseline'])} "
                             f"{metric['unit']}) - not defaulted")
                row_results.append({"metric": name, "kind": metric["kind"],
                                    "verdict": "UNMEASURED",
                                    "baseline": metric["baseline"],
                                    "measured": None})
                failures += 1
                continue
            got = measured[name]
            if "tolerance" in metric:
                tolerance = float(metric["tolerance"])
            elif metric["kind"] == "speed":
                tolerance = speed_tolerance
            else:
                tolerance = 0.0
            verdict = _verdict(metric["kind"], metric["direction"],
                               metric["baseline"], got, tolerance)
            if isinstance(metric["baseline"], bool):
                delta_text = ""
            else:
                delta = float(got) - float(metric["baseline"])
                rel = (delta / float(metric["baseline"]) * 100.0
                       if metric["baseline"] != 0 else 0.0)
                delta_text = f" (delta {delta:+.4g}, {rel:+.2f}%)"
            lines.append(f"   [{verdict}] {metric['kind']}/{name}: "
                         f"baseline {_format_value(metric['baseline'])} -> "
                         f"measured {_format_value(got)} "
                         f"{metric['unit']}{delta_text}")
            row_results.append({"metric": name, "kind": metric["kind"],
                                "verdict": verdict,
                                "baseline": metric["baseline"],
                                "measured": got})
            if verdict in ("REGRESSED", "UNMEASURED"):
                failures += 1
        results.append({"baseline": baseline_id, "metrics": row_results})
    return {"lines": lines, "results": results, "failures": failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Compare a new measurement against the M04 "
                     "performance baselines."))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                        help="the committed baseline file")
    parser.add_argument("--measurement", required=True,
                        help="JSON bundle: baseline id -> artifact or metrics")
    parser.add_argument("--speed-tolerance", type=float, default=0.25,
                        help="relative tolerance for speed metrics")
    parser.add_argument("--json-out", default=None,
                        help="write the machine-readable verdict here")
    args = parser.parse_args(argv)

    try:
        bundle = _read_json(args.measurement, "measurement bundle")
        if not isinstance(bundle, dict) or not bundle:
            raise BaselineCompareError(
                "measurement bundle is not a non-empty object")
        report = compare(args.baseline, bundle, args.speed_tolerance)
    except BaselineCompareError as exc:
        print(f"baseline_compare: {exc}", file=sys.stderr)
        return 1
    print("\n".join(report["lines"]))
    quality = sum(1 for row in report["results"]
                  for item in row["metrics"]
                  if item["kind"] == "quality"
                  and item["verdict"] in ("REGRESSED", "UNMEASURED"))
    speed = sum(1 for row in report["results"]
                for item in row["metrics"]
                if item["kind"] == "speed"
                and item["verdict"] in ("REGRESSED", "UNMEASURED"))
    print(f"\ncompared {len(report['results'])} baselines: "
          f"{quality} quality regressions, {speed} speed regressions "
          f"or unmeasured metrics")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report["results"], indent=2) + "\n",
            encoding="utf-8")
    if report["failures"]:
        return EXIT_MISMATCH
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
