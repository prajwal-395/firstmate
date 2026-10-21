"""Measure adaptive temporal inspection against the incumbent.

Two fixtures, one question: does the dense tier buy recall, and what
does it cost?

**The retrieval held-out set** (the editorial held-out set over the
Ren-owned scratch project).  The BEFORE arm is the frame index as the
incumbent builds it: a uniform grid, one frame every ten seconds.  The
AFTER arm is the same index built adaptively: the grid retained, plus
the frames inside the intervals the source's own measurements say
changed.  Both arms are scored by the SHIPPED scorer
(`library.tools.retrieval_eval.run`), so the numbers are the ones the
engine itself reports: region-capped recall, the wrong seconds the
editor would take, and the false-positive windows and seconds.

**The M0 fixture** is measured by
`docs/benchmarks/m0_adaptive_inspection_bench.py`, which runs the
same engine code against the frozen occurrence corpus.

Both arms run on the engine's own code, in the foreground, and report
what they measured.  Nothing here gates anything.

    bin/vep docs/benchmarks/adaptive_inspection_bench.py \\
        --project <scratch project> --out-json <report.json>
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import adaptive_inspection
from library.tools.analysis import footage_frames


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 2)


def _build_index(project: str, index_dir: Path, sampling: str) -> dict:
    """Build a frame index into `index_dir` with the given sampling."""
    started = time.perf_counter()
    stats = footage_frames.build_frame_index(project, index_dir=index_dir,
                                             sampling=sampling)
    stats["wall_seconds"] = round(time.perf_counter() - started, 1)
    stats["peak_rss_mb"] = _peak_rss_mb()
    stats["frames_indexed"] = stats.get("frame_count")
    return stats


def _scored_arm(project: str, heldout_set: str, index_dir: Path,
                label: str) -> dict:
    """One arm of the retrieval measurement, scored by the shipped scorer.

    The scorer is the engine's own `retrieval_eval.run`, pointed at an
    ALTERNATIVE frame index.  Nothing is re-implemented here: the
    numbers this arm reports are the numbers the engine reports.
    """
    from library.tools import retrieval_eval

    eval_set = json.loads(Path(heldout_set).read_text(encoding="utf-8"))
    started = time.perf_counter()
    result = retrieval_eval.run(project, eval_set, ["editorial"],
                                 frame_index_dir=str(index_dir))
    return {
        "label": label,
        "index_dir": str(index_dir),
        "wall_seconds": round(time.perf_counter() - started, 1),
        "summary": result.get("summary"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",
                        help="the scratch project to measure the retrieval "
                             "lane on")
    parser.add_argument("--heldout-set",
                        default="library/tools/editorial_retrieval_heldout_set.json")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--work-dir", default=None,
                        help="where the two frame indexes are built "
                             "(default: a temp dir)")
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "policy_version": adaptive_inspection.POLICY_VERSION,
        "code_revision": _git_head(),
    }

    if args.project:
        work = Path(args.work_dir or "/tmp/ren-vi005-bench")
        work.mkdir(parents=True, exist_ok=True)
        before_dir = work / "index_uniform"
        after_dir = work / "index_adaptive"
        report["retrieval"] = {
            "before": _build_index(args.project, before_dir, "uniform"),
            "after": _build_index(args.project, after_dir, "adaptive"),
        }
        report["retrieval"]["scored"] = {
            "before": _scored_arm(args.project, args.heldout_set,
                                  before_dir, "uniform"),
            "after": _scored_arm(args.project, args.heldout_set,
                                 after_dir, "adaptive"),
        }

    Path(args.out_json).write_text(json.dumps(report, indent=1) + "\n",
                                    encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0


def _git_head() -> str | None:
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, encoding="utf-8",
                             check=False)
        return out.stdout.strip() or None
    except OSError:
        return None


if __name__ == "__main__":
    sys.exit(main())
