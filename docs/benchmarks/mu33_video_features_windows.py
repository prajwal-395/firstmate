"""Calibration window fixtures for TMP-033..041 reusable video features.

Calibration-only windows over the EVAL-01 collections (podcast MXF +
Apple 001 takes, read-only). Motion windows come from the agent action
consensus labels (calibration split); static windows are intervals with
NO agent-labeled action - they are not certified motion-free, and the
fixture records that distinction. Podcast windows carry no motion label
and serve cost and cross-signal correlation only, never separation.

Source of truth for splits: docs/benchmarks/2026-10-15-editorial-eval-corpus.json.
Source of truth for labels: docs/benchmarks/evaluator-only/2026-10-15-agent-action-consensus.json.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_VERSION = "mu-features-windows-v1"
CORPUS_MANIFEST = "docs/benchmarks/2026-10-15-editorial-eval-corpus.json"
ACTION_CONSENSUS = ("docs/benchmarks/evaluator-only/"
                    "2026-10-15-agent-action-consensus.json")

MAX_MOTION_WINDOW_S = 8.0
STATIC_WINDOW_S = 4.0
MIN_WINDOW_S = 3.0
STATIC_MARGIN_S = 1.0
MAX_STATIC_PER_TAKE = 3


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_calibration_actions() -> list[dict]:
    """Agent action consensus rows on calibration takes, sorted by take/start."""
    root = _repo_root()
    rows = json.loads((root / ACTION_CONSENSUS).read_text(encoding="utf-8"))["labels"]
    actions = [row for row in rows
               if row.get("split") == "calibration" and row.get("kind") == "action"]
    actions.sort(key=lambda row: (row["take"], row["start"], row["end"]))
    return actions


def load_source_durations() -> dict[str, float]:
    """ffprobe durations for every calibration source (seconds)."""
    import subprocess

    root = _repo_root()
    manifest = json.loads((root / CORPUS_MANIFEST).read_text(encoding="utf-8"))
    home_documents = Path.home() / "Documents"
    durations: dict[str, float] = {}
    for source in manifest["sources"]:
        if source.get("split") != "calibration":
            continue
        path = home_documents / source["path"]
        stem = path.stem
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed for calibration source {path}")
        durations[stem] = float(result.stdout.strip())
    return durations


def build_windows() -> dict:
    """Deterministic calibration window list with label provenance."""
    actions = load_calibration_actions()
    durations = load_source_durations()
    by_take: dict[str, list[dict]] = {}
    for row in actions:
        by_take.setdefault(row["take"], []).append(row)

    windows: list[dict] = []
    for row in actions:
        start = float(row["start"])
        end = min(float(row["end"]), start + MAX_MOTION_WINDOW_S)
        windows.append({
            "window_id": f"motion-{row['take']}-{start:.2f}-{end:.2f}",
            "take": row["take"],
            "start": round(start, 3),
            "end": round(end, 3),
            "label": "motion",
            "label_basis": "agent-action-consensus calibration "
                           f"{row.get('verb') or row.get('name')}; "
                           f"truncated_to_{MAX_MOTION_WINDOW_S}s" if float(row["end"]) - start > MAX_MOTION_WINDOW_S else "agent-action-consensus calibration "
                           f"{row.get('verb') or row.get('name')}",
            "truncated": float(row["end"]) - start > MAX_MOTION_WINDOW_S,
        })

    for take, duration in sorted(durations.items()):
        occupied = [(float(row["start"]), float(row["end"]))
                    for row in by_take.get(take, [])]
        free: list[list[float]] = [[0.0, duration]]
        for start, end in occupied:
            block = [max(0.0, start - STATIC_MARGIN_S),
                     min(duration, end + STATIC_MARGIN_S)]
            updated = []
            for left, right in free:
                if block[1] <= left or block[0] >= right:
                    updated.append([left, right])
                    continue
                if block[0] > left:
                    updated.append([left, block[0]])
                if block[1] < right:
                    updated.append([block[1], right])
            free = updated
        made = 0
        # Podcast takes have no action labels: spread windows for cost and
        # correlation only; they never join motion/static separation.
        kind = "unlabeled-cost-only" if take.startswith(("LCAT", "LC49")) else "static"
        budget = 6 if take.startswith(("LCAT", "LC49")) else MAX_STATIC_PER_TAKE
        if take == "IMG_1816":
            # Long unlabeled take: spread across its full duration.
            step = duration / budget
            for index in range(budget):
                start = round(index * step, 3)
                end = round(min(duration, start + STATIC_WINDOW_S), 3)
                if end - start < MIN_WINDOW_S:
                    continue
                made += 1
                windows.append({
                    "window_id": f"static-{take}-{start:.2f}-{end:.2f}",
                    "take": take, "start": start, "end": end,
                    "label": "static",
                    "label_basis": "no agent-labeled action in take; "
                                   "not certified motion-free",
                    "truncated": False,
                })
            continue
        for left, right in free:
            while right - left >= MIN_WINDOW_S and made < budget:
                span = min(STATIC_WINDOW_S, right - left)
                start, end = round(left, 3), round(left + span, 3)
                made += 1
                windows.append({
                    "window_id": f"{kind}-{take}-{start:.2f}-{end:.2f}",
                    "take": take, "start": start, "end": end,
                    "label": kind,
                    "label_basis": ("no action labels on podcast take; cost and "
                                    "correlation only" if kind == "unlabeled-cost-only"
                                    else "no agent-labeled action in interval; "
                                         "not certified motion-free"),
                    "truncated": False,
                })
                left = end
    windows.sort(key=lambda row: (row["take"], row["start"]))
    return {
        "fixture_version": FIXTURE_VERSION,
        "corpus_manifest": CORPUS_MANIFEST,
        "action_consensus": ACTION_CONSENSUS,
        "label_type": "agent",
        "split": "calibration",
        "counts": {label: sum(1 for row in windows if row["label"] == label)
                   for label in ("motion", "static", "unlabeled-cost-only")},
        "windows": windows,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    fixture = build_windows()
    args.out.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"{len(fixture['windows'])} windows {fixture['counts']}")


if __name__ == "__main__":
    main()
