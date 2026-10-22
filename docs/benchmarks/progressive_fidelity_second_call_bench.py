"""Progressive fidelity: the SECOND call, before and after.

WHAT THIS MEASURES
-------------------
A caller asks the cheap tier first - "where is the laptop on the
desk?" - and the verified tier second - "which shot actually shows
it?".  The second call used to pay for the RANKING again, because the
coarse answer was printed and gone: the lane had no way to find the
ranking the first call had already made, so the second call ranked
the index from scratch and then verified the candidates.

The coarse answer is now kept beside the index, and the second call
computes the detailed tier from it.  So the question this bench asks
is the one a caller cares about: **how long does the second call
take, and does it answer the same thing?**

THE ARMS
--------
* **before** - the code as it was: the tree at the base commit.  The
  second call ranks the index and verifies the candidates.
* **after** - the code as it is: the second call reads the coarse
  answer the first call kept, and verifies ITS candidates.

Each arm runs in its own process, so each pays its own model load, and
each arm is given the same verdict store, so the two are compared on
the SAME evidence: a difference in the answers is the LADDER's, and a
difference in the seconds is the RANKING's.

TWO STORES, TWO CLAIMS
----------------------
The verification verdicts are recorded per source and per statement,
so a second run of the same question is served the recorded verdicts
instead of asking the model again.

* the TIMING is measured on a WARM store: the store is filled first, so
  both arms pay for the ranking and the verdict LOOKUPS, and the
  difference between them is the ranking the after-arm does not pay.
* the first-question cost is measured on a COLD store as well: each
  arm gets its own empty store and pays for the verdicts it needs.  A
  model's yes/no is not a deterministic function of the footage, so
  the cold answers are compared and any difference is REPORTED, not
  hidden.

    bin/vep docs/benchmarks/progressive_fidelity_second_call_bench.py \
        --project <project> --queries "a laptop on a desk;..." \
        --before-tree /tmp/base --after-tree .
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(__file__).resolve()


# ── the arms ──────────────────────────────────────────────────────────────

def arm_coarse(args) -> dict:
    """The first call: the cheap tier, which keeps its answer."""
    from library.tools.analysis.footage_frames import FrameIndex

    index = FrameIndex(args.project, index_dir=args.index_dir)
    started = time.perf_counter()
    report = index.answer_at_tier(args.query, tier="coarse",
                                  top_ranges=args.top_k)
    elapsed = time.perf_counter() - started
    return {"arm": "coarse", "query": args.query,
            "seconds": round(elapsed, 3),
            "tier": (report.get("fidelity") or {}).get("tier"),
            "windows": _windows(report),
            "coarse_report": report.get("coarse_report")}


def arm_second(args) -> dict:
    """The second call: the verified tier, the way THIS tree does it.

    The tree that keeps coarse answers escalates from the stored one;
    the tree that does not rank the index again.  Nothing else about
    the two differs, which is what makes the comparison a measurement
    of the ladder and not of the weather.
    """
    from library.tools.analysis.footage_frames import FrameIndex

    index = FrameIndex(args.project, index_dir=args.index_dir)
    started = time.perf_counter()
    try:
        from library.tools import coarse_report  # noqa: F401
    except ImportError:
        report = index.search_ranges(args.query, top_ranges=args.top_k,
                                     temporal=True)
        reused = None
    else:
        report = index.answer_at_tier(args.query, tier="detailed",
                                      top_ranges=args.top_k)
        reused = (report.get("coarse_report") or {}).get("status")
    elapsed = time.perf_counter() - started
    return {"arm": "second", "query": args.query,
            "seconds": round(elapsed, 3),
            "tier": (report.get("fidelity") or {}).get("tier"),
            "windows": _windows(report),
            "coarse_report_status": reused,
            "cost": (report.get("temporal_pass") or {}).get("cost")}


ARMS = {"coarse": arm_coarse, "second": arm_second}


def _windows(report: dict) -> list:
    """The answer, in the form two arms can be compared on.

    The window's IDENTITY is the camera and the span; its VERDICT is
    the model's answer and the reason.  Both travel, because an answer
    that kept the windows but lost the verdicts would be a different
    answer wearing the same windows.
    """
    out = []
    for row in report.get("ranges") or []:
        justification = row.get("justification") or {}
        out.append({
            "clip_id": row.get("clip_id"),
            "start": row.get("start"),
            "end": row.get("end"),
            "best_score": row.get("best_score"),
            "verified": bool(row.get("verified")),
            "verdict": justification.get("verdict"),
            "reason": justification.get("reason"),
            "statement": justification.get("statement"),
        })
    return out


# ── the scratch stores ───────────────────────────────────────────────────

def _frame_index_digests(index_dir: Path) -> list[str]:
    """The source digests the frame index was built from."""
    payload = json.loads((index_dir / "frame_index.json").read_text(
        encoding="utf-8"))
    digests = set()
    for frame in payload.get("frames") or []:
        marker = "/source_memory/"
        path = str(frame.get("file") or "")
        if marker in path:
            digests.add(path.split(marker, 1)[1].split("/", 1)[0])
    return sorted(digests)


def scratch_memory_root(index_dir: Path, root: Path, *,
                        verdicts: str = "none") -> Path:
    """A verdict store with the real frames and the recorded verdicts.

    The frames are symlinked, so they are the real measured frames of
    the real footage.  The verdict file is either left out (a COLD
    store: the pass has to ask the model again, which is what a first
    question costs) or COPIED from the machine's own store (a WARM
    store: the question has been asked before and the recorded verdicts
    answer it without a model).  The copy is a copy: the machine's
    store is never written through it.
    """
    from library.tools import source_memory

    shared = source_memory.machine_memory_root()
    root.mkdir(parents=True, exist_ok=True)
    for digest in _frame_index_digests(index_dir):
        source = shared / digest
        if not source.is_dir():
            continue
        target = root / digest
        target.mkdir(parents=True, exist_ok=True)
        for entry in source.iterdir():
            if entry.name == "verdicts.json":
                if verdicts == "copy" and entry.exists():
                    shutil.copy2(entry, target / entry.name)
                continue
            link = target / entry.name
            if not link.exists():
                link.symlink_to(entry)
    return root


def scratch_index_dir(index_dir: Path, root: Path) -> Path:
    """A COPY of the frame index, so the run writes nothing to the
    project it was pointed at.

    The coarse answers live beside the index, so a run that keeps them
    must not keep them in the captain's project.
    """
    target = root / "frame_index"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in ("frame_index.json", "frame_embeddings.npy"):
        source = index_dir / name
        if source.exists():
            shutil.copy2(source, target / name)
    return target


# ── the run ──────────────────────────────────────────────────────────────

def _run_arm(arm: str, args, tree: Path, memory_root: Path) -> dict:
    """One arm, in its own process, in the given tree, on the given
    verdict store."""
    command = [sys.executable, str(BENCH),
               "--project", str(args.project), "--query", args.query,
               "--index-dir", str(args.index_dir), "--top-k", str(args.top_k),
               "--arm", arm]
    environment = dict(os.environ)
    environment["PIPELINE_SOURCE_MEMORY_ROOT"] = str(memory_root)
    environment["PYTHONPATH"] = str(tree)
    completed = subprocess.run(command, capture_output=True, text=True,
                               encoding="utf-8", cwd=str(tree), env=environment,
                               check=False)
    if completed.returncode != 0:
        # The child's stderr is the diagnosis: keep it whole in the
        # scratch area instead of truncating it into the exception, so
        # a failed arm can be retried and inspected rather than guessed
        # at.
        dump = Path(args.scratch) / f"arm-{arm}-failure.log"
        dump.write_text(f"tree: {tree}\nquery: {args.query}\n"
                        f"returncode: {completed.returncode}\n"
                        f"stdout:\n{completed.stdout[-2000:]}\n"
                        f"stderr:\n{completed.stderr}",
                        encoding="utf-8")
        raise RuntimeError(f"the {arm} arm failed in {tree}: see {dump} "
                           f"(last stderr: {completed.stderr[-500:]})")
    payload = completed.stdout.strip().splitlines()
    for line in reversed(payload):
        try:
            return json.loads(line)
        except ValueError:
            continue
    raise RuntimeError(f"the {arm} arm printed no result:\n"
                       f"{completed.stdout[-2000:]}")


def _identity(windows: list) -> list:
    """The windows without the model's verdicts."""
    return [{key: row.get(key) for key in ("clip_id", "start", "end")}
            for row in windows]


def _verdicts(windows: list) -> list:
    """The model's verdicts alone."""
    return [{"clip_id": row.get("clip_id"), "start": row.get("start"),
             "verdict": row.get("verdict")} for row in windows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="progressive_fidelity_second_call_bench",
        description=("The before/after of the SECOND call of the "
                     "progressive fidelity ladder."))
    parser.add_argument("--project", required=True)
    parser.add_argument("--query", help="one query (the --arm worker)")
    parser.add_argument("--queries", help="the queries, separated by ';'")
    parser.add_argument("--index-dir",
                        help="the frame index to read (the worker)")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--arm", choices=sorted(ARMS))
    parser.add_argument("--before-tree", default=str(REPO_ROOT),
                        help="the tree whose code is the BEFORE arm")
    parser.add_argument("--after-tree", default=str(REPO_ROOT),
                        help="the tree whose code is the AFTER arm")
    parser.add_argument("--scratch", default="/tmp/progressive-fidelity-second-call")
    parser.add_argument("--cold-queries", type=int, default=1,
                        help="how many questions get the COLD pair (each "
                             "arm pays for the verdicts it needs); the "
                             "cold pair is the real first-question cost "
                             "and it is the expensive one")
    parser.add_argument("--json", help="write the report here")
    args = parser.parse_args(argv)

    if args.arm:
        print(json.dumps(ARMS[args.arm](args)))
        return 0

    project = Path(args.project).expanduser().resolve()
    queries = [q.strip() for q in (args.queries or "").split(";") if q.strip()]
    if not queries:
        parser.error("--queries names at least one query")
    scratch = Path(args.scratch)
    before_tree = Path(args.before_tree).resolve()
    after_tree = Path(args.after_tree).resolve()

    from library.tools.analysis.footage_query import index_dir_for

    real_index = Path(args.index_dir) if args.index_dir else index_dir_for(project)
    index_dir = scratch_index_dir(real_index, scratch)
    args.index_dir = str(index_dir)

    rows = []
    for number, query in enumerate(queries):
        args.query = query
        # The first call: the coarse tier, which keeps its answer beside
        # the index.  It is timed too - it is what the caller pays to
        # LEARN where to look.
        coarse = _run_arm("coarse", args, after_tree,
                          scratch_memory_root(index_dir,
                                              scratch / f"warm-{number}"))
        # WARM: the store is filled FIRST - by an untimed run - so both
        # second-call arms pay for the ranking and the verdict lookups,
        # and the difference between them is the ranking the after-arm
        # does not pay.  The answers must be identical: the same
        # evidence.  The store carries the machine's RECORDED verdicts,
        # so the question has been asked before and the arms are
        # compared on that same evidence.
        warm = scratch_memory_root(index_dir, scratch / f"warm-{number}",
                                   verdicts="copy")
        _run_arm("second", args, after_tree, warm)
        before_warm = _run_arm("second", args, before_tree, warm)
        after_warm = _run_arm("second", args, after_tree, warm)
        # COLD: each arm gets its own empty store and pays for the
        # verdicts it needs.  This is the real first-question cost.
        if number < args.cold_queries:
            before_cold = _run_arm("second", args, before_tree,
                                   scratch_memory_root(
                                       index_dir, scratch / f"cold-before-{number}"))
            after_cold = _run_arm("second", args, after_tree,
                                  scratch_memory_root(
                                      index_dir, scratch / f"cold-after-{number}"))
            cold = {
                "before_seconds": before_cold["seconds"],
                "after_seconds": after_cold["seconds"],
                "windows": len(before_cold["windows"]),
                "before_cost": before_cold.get("cost"),
                "after_cost": after_cold.get("cost"),
                "windows_identical": _identity(before_cold["windows"])
                == _identity(after_cold["windows"]),
                "verdicts_identical": _verdicts(before_cold["windows"])
                == _verdicts(after_cold["windows"]),
                "answers_identical": before_cold["windows"]
                == after_cold["windows"],
            }
        else:
            cold = None
        rows.append({
            "query": query,
            "coarse": {"seconds": coarse["seconds"],
                       "tier": coarse["tier"],
                       "windows": len(coarse["windows"])},
            "warm": {
                "before_seconds": before_warm["seconds"],
                "after_seconds": after_warm["seconds"],
                "before_reused": before_warm.get("coarse_report_status"),
                "after_reused": after_warm.get("coarse_report_status"),
                "windows": len(before_warm["windows"]),
                "before_cost": before_warm.get("cost"),
                "after_cost": after_warm.get("cost"),
                "windows_identical": _identity(before_warm["windows"])
                == _identity(after_warm["windows"]),
                "verdicts_identical": _verdicts(before_warm["windows"])
                == _verdicts(after_warm["windows"]),
                "answers_identical": before_warm["windows"]
                == after_warm["windows"],
            },
            "cold": cold,
        })
        row = rows[-1]
        print(f"[{number + 1}/{len(queries)}] {query!r}: coarse "
              f"{row['coarse']['seconds']}s; warm second call "
              f"{row['warm']['before_seconds']}s -> "
              f"{row['warm']['after_seconds']}s "
              f"(answers identical={row['warm']['answers_identical']})"
              + (f"; cold second call {cold['before_seconds']}s -> "
                 f"{cold['after_seconds']}s "
                 f"(answers identical={cold['answers_identical']})"
                 if cold else "; cold pair skipped"),
              file=sys.stderr)

    supported = sum(1 for row in rows if row["warm"]["answers_identical"])
    report = {
        "project": str(project),
        "index_dir": str(index_dir),
        "before_tree": str(before_tree),
        "after_tree": str(after_tree),
        "queries": len(rows),
        "answers_identical": supported,
        "rows": rows,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2),
                                   encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if supported == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
