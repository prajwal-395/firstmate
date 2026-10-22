"""INFRA-006, lane C: the caption parity measurement, on real cards.

The caption layer draws through the selected graphics engine
(``library/tools/graphics_renderer.py``).  The native renderer
(``ren-graphics-fframes``) is the default, and the engine is part of the
caption render's reuse key: switching engines re-renders every card OVER
the artefact the project shipped.  This bench measures what that costs in
FIDELITY and in SECONDS, on the project's own shipped caption cards.

    python3 docs/benchmarks/2026-10-22-infra006-caption-parity.py \
        --project <project folder> --scratch <scratch folder> \
        --engine fframes --sample 96 --out /tmp/caption-parity.json

THE POPULATION.  The project's own shipped caption cards: the props file
step 4.05 wrote and the ``.mov`` artefact it shipped, under
``pipeline_output/steps/4_05_render_subtitles/``.  READ ONLY - the
project is never written to; every render lands in the scratch folder.

THE MEASURE.  The ink IoU of the rendered card against the artefact the
project shipped, frame by frame, by the alpha plane at the floor the
motion-graphics parity measurement used (alpha > 16), averaged over the
card's frames.  It is the measure ``library/tools/caption_parity_gate.py``
makes at publish time, so the bench and the gate measure the same thing.

THE SAMPLE.  Evenly spaced over the sorted population - every
``population // sample``-th card - so it spans the whole set (the cards
were shipped over five weeks by three different engines) rather than one
batch of it.  The population and the sample are reported separately: they
are different denominators and are never combined.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import caption_parity_gate as gate  # noqa: E402


def _cards_dir(project: Path) -> Path:
    return project / "pipeline_output/steps/4_05_render_subtitles"


def _sample(cards_dir: Path, sample: int) -> tuple[list[Path], int]:
    props = sorted(cards_dir.glob("*_props.json"))
    population = len(props)
    step = max(1, population // sample)
    return props[::step][:sample], population


def _render_fframes(props_path: Path, out_path: Path, project: Path,
                    scratch: Path) -> float:
    from library.tools import fframes_render as fr

    props = json.loads(props_path.read_text(encoding="utf-8"))
    started = time.perf_counter()
    fr.render_one_card("SubtitleOverlay", props, str(out_path),
                       str(scratch), str(project))
    return time.perf_counter() - started


def _render_remotion(props_path: Path, out_path: Path,
                     renderer) -> float:
    started = time.perf_counter()
    ok, error = renderer.render(str(props_path), str(out_path))
    if not ok:
        raise RuntimeError(f"the reference engine could not draw "
                           f"{props_path.name}: {error}")
    return time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--engine", default="fframes",
                        choices=["fframes", "remotion"])
    parser.add_argument("--sample", type=int, default=96)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    scratch = Path(args.scratch).expanduser().resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    cards_dir = _cards_dir(project)
    if not cards_dir.is_dir():
        print(f"no shipped caption cards under {cards_dir}", file=sys.stderr)
        return 2

    sample, population = _sample(cards_dir, args.sample)
    renderer = None
    if args.engine == "remotion":
        from library.tools.remotion_batch import PersistentRenderer

        renderer = PersistentRenderer(composition="SubtitleOverlay")
        renderer.start()

    records = []
    try:
        for props_path in sample:
            base = props_path.name[: -len("_props.json")]
            shipped = cards_dir / f"{base}.mov"
            if not shipped.is_file():
                continue
            out_path = scratch / f"{base}.mov"
            if args.engine == "fframes":
                seconds = _render_fframes(props_path, out_path, project,
                                          scratch)
            else:
                seconds = _render_remotion(props_path, out_path, renderer)
            result = gate.compare_artifacts(str(out_path), str(shipped))
            record = {
                "segment_id": base,
                "engine_seconds": round(seconds, 3),
                "frames": result.frames,
                "mean_iou": round(result.mean_iou, 4),
                "min_iou": round(result.min_iou, 4),
                "within_floor": result.within_floor,
            }
            records.append(record)
            print(json.dumps(record), flush=True)
    finally:
        if renderer is not None:
            renderer.close()

    within = sum(1 for record in records if record["within_floor"])
    seconds = [record["engine_seconds"] for record in records]
    summary = {
        "engine": args.engine,
        "population": population,
        "sample": len(records),
        "floor": gate.PARITY_FLOOR,
        "within_floor": within,
        "mean_iou": round(
            sum(record["mean_iou"] for record in records) / len(records), 4)
        if records else None,
        "mean_seconds": round(sum(seconds) / len(seconds), 3) if seconds else None,
        "median_seconds": round(sorted(seconds)[len(seconds) // 2], 3)
        if seconds else None,
        "records": records,
    }
    Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "records"},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
