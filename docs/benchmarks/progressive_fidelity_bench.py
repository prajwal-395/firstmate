"""Progressive fidelity: the before/after on one real reel.

WHAT THIS MEASURES
-------------------
A planning question about the picture - "where is the laptop on the
desk?" - asked of a real reel's real footage index.  Two arms:

* **before** - the lane as it was: the answer comes from the VERIFIED
  tier, so the caller pays the local vision model's verdict on every
  candidate before it hears anything at all.  One call, one answer.
* **after** - the ladder: the COARSE tier answers first (the ranked
  candidates, which say where to look), and the ESCALATION - the
  detailed tier, computed from those same candidates - answers with the
  verified windows.

The two arms must end on the SAME answer.  That is the whole claim:
the after-arm reaches the verified answer through the coarse tier and
does not reach a different one.  If the answers differ, the bench
exits non-zero - a fidelity regression is a revert, not a finding.

TWO STORES, TWO CLAIMS
----------------------
The verification verdicts are recorded per source and per statement,
so a second run of the same question is served the recorded verdicts
instead of asking the model again.  That makes a warm answer
DETERMINISTIC - the same verdicts, the same windows - and a cold
answer a MODEL JUDGEMENT, which is not the same twice.

So the bench measures two things, and keeps them apart:

* the TIMING is measured on COLD stores: each arm gets its own store
  with no verdicts in it, so each pays for the verdicts it needs.
* the EQUALITY is measured on a SHARED store: the before-arm records
  the verdicts and the after-arm reads them, so the two answers are
  compared on the same evidence and any difference is the LADDER's,
  not the model's.

The cold pair is compared too, and its differences are reported: a
model's yes/no is not a deterministic function of the footage, and the
bench says so rather than hiding it.

THE ARMS ARE SEPARATE PROCESSES
--------------------------------
A model load is a cost of the first measurement on this machine, not
of the request, so each arm runs in its own process and pays its own
load.  The scratch stores are built by symlinking the real frames and
leaving the verdict file out, so the frames are the real ones and the
verdicts are cold.

    bin/vep docs/benchmarks/progressive_fidelity_bench.py \
        --project <project> --queries "a laptop on a desk;..."
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


# ── the arms ──────────────────────────────────────────────────────────────

def arm_before(args) -> dict:
    """The lane as it was: the verified tier, one call, one answer."""
    from library.tools.analysis.footage_frames import FrameIndex

    started = time.perf_counter()
    index = FrameIndex(args.project)
    report = index.search_ranges(args.query, top_ranges=args.top_k,
                                 temporal=True)
    elapsed = time.perf_counter() - started
    return {"arm": "before", "query": args.query,
            "seconds_first": round(elapsed, 3),
            "seconds_full": round(elapsed, 3),
            "tier": (report.get("fidelity") or {}).get("tier"),
            "windows": _windows(report),
            "cost": (report.get("temporal_pass") or {}).get("cost")}


def arm_after(args) -> dict:
    """The ladder: the coarse tier answers, then the escalation."""
    from library.tools.analysis.footage_frames import FrameIndex

    index = FrameIndex(args.project)
    started = time.perf_counter()
    coarse = index.search_ranges(args.query, top_ranges=args.top_k,
                                 temporal=False)
    first = time.perf_counter() - started
    detailed = index.verify_ranges(args.query, coarse)
    full = time.perf_counter() - started
    return {"arm": "after", "query": args.query,
            "seconds_first": round(first, 3),
            "seconds_full": round(full, 3),
            "tier": (detailed.get("fidelity") or {}).get("tier"),
            "windows": _windows(detailed),
            "cost": (detailed.get("temporal_pass") or {}).get("cost"),
            "coarse_windows": _windows(coarse),
            "escalation": coarse.get("escalation")}


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


ARMS = {"before": arm_before, "after": arm_after}


# ── the scratch verdict stores ───────────────────────────────────────────

def _frame_index_digests(project: Path) -> list[str]:
    """The source digests the project's frame index was built from."""
    from library.tools.analysis.footage_query import index_dir_for

    index_dir = index_dir_for(project)
    payload = json.loads((index_dir / "frame_index.json").read_text(
        encoding="utf-8"))
    digests = set()
    for frame in payload.get("frames") or []:
        marker = "/source_memory/"
        path = str(frame.get("file") or "")
        if marker in path:
            digests.add(path.split(marker, 1)[1].split("/", 1)[0])
    return sorted(digests)


def scratch_memory_root(project: Path, root: Path) -> Path:
    """A verdict store with the real frames and NO verdicts.

    The frames are symlinked, so they are the real measured frames of
    the real footage; the verdict file is left out, so the verification
    pass has to ask the model again - which is what a first question
    costs.
    """
    from library.tools import source_memory

    shared = source_memory.machine_memory_root()
    root.mkdir(parents=True, exist_ok=True)
    for digest in _frame_index_digests(project):
        source = shared / digest
        if not source.is_dir():
            continue
        target = root / digest
        target.mkdir(parents=True, exist_ok=True)
        for entry in source.iterdir():
            if entry.name == "verdicts.json":
                continue
            link = target / entry.name
            if not link.exists():
                link.symlink_to(entry)
    return root


# ── the run ──────────────────────────────────────────────────────────────

def _run_arm(arm: str, args, memory_root: Path) -> dict:
    """One arm, in its own process, on the given verdict store."""
    command = [sys.executable,
               str(REPO_ROOT / "docs/benchmarks/progressive_fidelity_bench.py"),
               "--project", str(args.project), "--query", args.query,
               "--top-k", str(args.top_k), "--arm", arm]
    environment = dict(os.environ)
    environment["PIPELINE_SOURCE_MEMORY_ROOT"] = str(memory_root)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    completed = subprocess.run(command, capture_output=True, text=True,
                               encoding="utf-8", cwd=str(REPO_ROOT),
                               env=environment, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"the {arm} arm failed:\n{completed.stderr[-4000:]}")
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
        prog="progressive_fidelity_bench",
        description=("The before/after of progressive fidelity on one "
                     "reel's footage index."))
    parser.add_argument("--project", required=True)
    parser.add_argument("--query", help="one query (the --arm worker)")
    parser.add_argument("--queries",
                        help="the queries, separated by ';'")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--arm", choices=sorted(ARMS))
    parser.add_argument("--scratch", default="/tmp/progressive-fidelity-bench")
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
    rows = []
    for index, query in enumerate(queries):
        args.query = query
        # COLD: each arm gets its own empty verdict store, so each pays
        # for the verdicts it needs and the timing is the real one.
        before = _run_arm("before", args,
                          scratch_memory_root(project, scratch
                                              / f"cold-before-{index}"))
        after = _run_arm("after", args,
                         scratch_memory_root(project, scratch
                                             / f"cold-after-{index}"))
        # SHARED: the before-arm records the verdicts and the after-arm
        # reads them, so the two answers are compared on the SAME
        # evidence and the equality is the ladder's to answer for.
        shared = scratch_memory_root(project, scratch / f"shared-{index}")
        before_shared = _run_arm("before", args, shared)
        after_shared = _run_arm("after", args, shared)

        rows.append({
            "query": query,
            "cold": {
                "before": before,
                "after": after,
                "windows_identical": _identity(before["windows"])
                == _identity(after["windows"]),
                "verdicts_identical": _verdicts(before["windows"])
                == _verdicts(after["windows"]),
            },
            "shared": {
                "before": before_shared,
                "after": after_shared,
                "windows_identical": _identity(before_shared["windows"])
                == _identity(after_shared["windows"]),
                "verdicts_identical": _verdicts(before_shared["windows"])
                == _verdicts(after_shared["windows"]),
                "answers_identical": before_shared["windows"]
                == after_shared["windows"],
            },
        })
        cold = rows[-1]["cold"]
        shared_row = rows[-1]["shared"]
        print(f"[{index + 1}/{len(queries)}] {query!r}: "
              f"before {before['seconds_full']}s -> after "
              f"{after['seconds_first']}s first / {after['seconds_full']}s "
              f"full; cold windows identical={cold['windows_identical']}, "
              f"cold verdicts identical={cold['verdicts_identical']}; "
              f"shared answers identical={shared_row['answers_identical']}",
              file=sys.stderr)

    supported = sum(1 for row in rows if row["shared"]["answers_identical"])
    report = {
        "project": str(project),
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
