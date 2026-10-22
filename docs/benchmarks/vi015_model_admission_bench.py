"""VI-015 bench: what the machine admits, and what a multi-model run costs.

Two measurements, both on the machine as it is:

1. **The admission probe.**  A fixed set of resident-model requests is
   submitted to the scheduler, one at a time and then concurrently, and
   each is recorded as ADMITTED or REFUSED with the reason and the
   latency.  The request's demand is the PROFILE's declared demand, so
   this measures what the pipeline asks for, not what the bench decides.
2. **The fixed multi-model analysis run.**  The same three resident
   models are asked to work on the same scratch media: the VLM's vision
   pass, SAM 2's segmentation, and the HTDemucs stem separation.  The
   run's wall time, the peak resident memory of each pass, and a digest
   of what each pass produced are recorded, so a run that changed its
   OUTPUT shows up as a changed digest.

The bench writes only under `--out` (a scratch directory) and reads only
scratch media; the source footage is never written.

Usage::

    ~/.local/share/vep/venv-py312/bin/python3 \\
        docs/benchmarks/vi015_model_admission_bench.py \\
        --clips-dir /tmp/vi015-scratch/media --out /tmp/vi015-scratch/out
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: The resident models the probe asks for, and the profile each one
#: asks under. The DEMAND comes from the profile, so the before and
#: after trees are measured through the same door.
REQUESTS = (
    ("gemma", "semantics.analyse:model_memory"),
    ("sam2", "objects.segment:model_memory"),
    ("stems", "music.analyse:model_memory"),
)

PROBE_TIMEOUT_S = 5.0


def _digest(path: Path) -> str:
    """The digest of the SAMPLES a pass produced, not of its file bytes.

    A WAV carries a timestamp in its metadata chunk, so two byte-wise
    identical runs of the same separation differ in their bytes while
    producing the same audio. The analysis output is the audio.
    """
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:  # the bench runs without the ML deps too
        return hashlib.sha256(path.read_bytes()).hexdigest()
    samples, _rate = sf.read(str(path), dtype="float32")
    return hashlib.sha256(
        np.ascontiguousarray(samples).tobytes()).hexdigest()


def _machine_reading() -> dict:
    """The LIVE reading of the machine, with the test seam removed.

    `VEP_SCHEDULER_MEMORY_GB` is a documented test seam: it changes
    where the admission probe reads, and nothing else. The bench asks
    for a fixed reading so its before/after comparison is reproducible
    (and so a run cannot load a 15 GB model on a busy box); the live
    reading is still reported beside it, measured without the seam.
    """
    from library.tools import perf_resources

    environment = {key: value for key, value in os.environ.items()
                   if key != "VEP_SCHEDULER_MEMORY_GB"}
    script = (
        "import json, sys;"
        f"sys.path.insert(0, {str(REPO_ROOT)!r});"
        "from library.tools import resource_scheduler as r;"
        "print(json.dumps({'available': r._memory_available_gb(),"
        "'swap': r._swap_state_gb()}))")
    try:
        completed = subprocess.run([sys.executable, "-c", script],
                                   capture_output=True, text=True,
                                   encoding="utf-8", env=environment,
                                   check=False, timeout=30)
        reading = json.loads(completed.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        reading = {"available": None, "swap": None}
    available = reading.get("available")
    swap = reading.get("swap")
    return {
        "available_gb": None if not available else round(available[0], 2),
        "total_gb": None if not available else round(available[1], 2),
        "swap_used_gb": None if not swap or swap[0] is None else round(swap[0], 2),
        "swap_total_gb": None if not swap or swap[1] is None else round(swap[1], 2),
        "pressure": perf_resources.memory_pressure(),
    }


def probe(requests=REQUESTS, timeout_s=PROBE_TIMEOUT_S) -> dict:
    """Ask for each resident model in turn; record what the machine did.

    The request's demand is the PROFILE's declared demand, so this
    measures what the pipeline asks for, not what the bench decides.
    """
    from library.tools import heavy_work_lock, resource_scheduler

    scheduler = resource_scheduler.Scheduler(heavy_work_lock.HEAVY_LOCK_DIR)
    rows = []
    for name, profile in requests:
        demand = resource_scheduler.demand_for(profile)
        started = time.monotonic()
        try:
            token = scheduler.acquire(f"vi015-probe-{name}", demand,
                                      announce=lambda line: None,
                                      timeout=timeout_s)
        except (resource_scheduler.MemoryUnavailable,
                resource_scheduler.Cancelled) as exc:
            rows.append({"request": name, "profile": profile,
                         "demand": demand,
                         "admitted": False, "reason": str(exc),
                         "latency_s": round(time.monotonic() - started, 3)})
            continue
        except Exception as exc:  # noqa: BLE001 - a refusal of any kind
            rows.append({"request": name, "profile": profile,
                         "demand": demand,
                         "admitted": False,
                         "reason": f"{type(exc).__name__}: {exc}",
                         "latency_s": round(time.monotonic() - started, 3)})
            continue
        rows.append({"request": name, "profile": profile, "demand": demand,
                     "admitted": True,
                     "latency_s": round(time.monotonic() - started, 3)})
        scheduler.release(token)
    return {"requests": rows,
            "admitted": sum(1 for row in rows if row["admitted"]),
            "refused": sum(1 for row in rows if not row["admitted"]),
            "total": len(rows)}


def probe_concurrently(holder: str, waiter: str, timeout_s=PROBE_TIMEOUT_S
                       ) -> dict:
    """Hold one resident model and ask for a second one beside it.

    This is the competing-pass measurement: two GPU-intensive passes
    that cannot coexist on this machine must not both be admitted. The
    two are asked through the scheduler as two JOBS, not as two nested
    locks in one process: a nested lock would re-enter the first job's
    grant and never reach an admission decision at all.
    """
    from library.tools import heavy_work_lock, resource_scheduler

    profiles = dict(REQUESTS)
    scheduler = resource_scheduler.Scheduler(heavy_work_lock.HEAVY_LOCK_DIR)
    holder_demand = resource_scheduler.demand_for(profiles[holder])
    waiter_demand = resource_scheduler.demand_for(profiles[waiter])
    result = {"holder": holder, "waiter": waiter,
              "holder_demand": holder_demand,
              "waiter_demand": waiter_demand}
    try:
        token = scheduler.acquire(f"vi015-probe-{holder}", holder_demand,
                                  announce=lambda line: None,
                                  timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        result["holder_admitted"] = False
        result["reason"] = f"{type(exc).__name__}: {exc}"
        result["waiter_admitted"] = False
        return result
    result["holder_admitted"] = True
    started = time.monotonic()
    try:
        waiter_token = scheduler.acquire(f"vi015-probe-{waiter}",
                                         waiter_demand,
                                         announce=lambda line: None,
                                         timeout=timeout_s)
    except (resource_scheduler.MemoryUnavailable,
            resource_scheduler.Cancelled) as exc:
        result["waiter_admitted"] = False
        result["reason"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        result["waiter_admitted"] = False
        result["reason"] = f"{type(exc).__name__}: {exc}"
    else:
        result["waiter_admitted"] = True
        scheduler.release(waiter_token)
    result["waiter_latency_s"] = round(time.monotonic() - started, 3)
    scheduler.release(token)
    return result


def _grant_peak(owner: str) -> dict:
    """What the machine's own grant history says a pass really used."""
    from library.tools import heavy_work_lock

    grants = heavy_work_lock._scheduler().history(time.time() - 3600)
    mine = [grant for grant in grants if grant.get("owner") == owner]
    if not mine:
        return {}
    last = mine[-1]
    return {
        "owner": owner,
        "demand": last.get("demand"),
        "peak_footprint_gb": last.get("peak_footprint_gb"),
        "peak_rss_gb": last.get("peak_rss_gb"),
        "wall_s": round(float(last.get("finished_at", 0))
                        - float(last.get("started_at", 0)), 3),
        "samples": last.get("samples"),
    }


def run_analysis(clips_dir: Path, out_dir: Path) -> dict:
    """The fixed multi-model analysis run: three models, one scratch set."""
    from library.tools.analysis import music_pipeline

    out_dir.mkdir(parents=True, exist_ok=True)
    audio = clips_dir / "clip_a.wav"
    video = clips_dir / "clip_a.mp4"
    result = {"stems": {}, "vision": {}, "segmentation": {}}

    # 1. The stem separation: the model this box CAN host.
    if audio.is_file():
        started = time.monotonic()
        try:
            separated = music_pipeline.separate_stems(
                str(audio), str(out_dir / "stems"))
        except Exception as exc:  # noqa: BLE001
            result["stems"] = {"ran": False,
                               "reason": f"{type(exc).__name__}: {exc}",
                               "wall_s": round(time.monotonic() - started, 3)}
        else:
            stems = sorted(Path(path) for path in
                            (separated.get("stems") or {}).values())
            result["stems"] = {
                "ran": True,
                "wall_s": round(time.monotonic() - started, 3),
                "method": separated.get("method"),
                "stems": [path.name for path in stems],
                "digest": hashlib.sha256(
                    "".join(_digest(path) for path in stems).encode()
                ).hexdigest(),
                "file_digest": hashlib.sha256(
                    "".join(hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in stems).encode()).hexdigest(),
                "grant": _grant_peak("music-analysis-stem-separation"),
            }

    # 2. The VLM's vision pass: the model this box CANNOT host.
    if video.is_file():
        analysis_dir = out_dir / "vision"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        command = [sys.executable,
                   str(REPO_ROOT / "library" / "tools" / "analysis"
                       / "vision_pipeline_v3.py"),
                   "--output-dir", str(analysis_dir),
                   "--clip", str(video)]
        completed = subprocess.run(command, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   cwd=str(REPO_ROOT), check=False)
        profiles = sorted(analysis_dir.glob("clip_profile_*.json"))
        result["vision"] = {
            "ran": bool(profiles),
            "returncode": completed.returncode,
            "wall_s": round(time.monotonic() - started, 3),
            "profiles": [path.name for path in profiles],
            "reason": _last_refusal(completed.stderr),
            "tail": (completed.stderr or "").strip().splitlines()[-3:],
        }

    # 3. SAM 2's segmentation: the other model this box cannot host.
    if video.is_file():
        from library.capabilities.objects import segment as seg

        started = time.monotonic()
        try:
            segmented = seg.run_step(
                raw_footage_files=[],
                clip_catalog=[{"clip_id": "clip-1", "path": str(video)}],
                output_dir=str(out_dir / "segmentation"),
                color_grade_spec={"subject_grades": [{"clip_id": "clip-1"}]},
                face_boxes_by_clip={"clip-1": [0.1, 0.1, 0.5, 0.5]})
        except Exception as exc:  # noqa: BLE001
            result["segmentation"] = {
                "ran": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "wall_s": round(time.monotonic() - started, 3)}
        else:
            trigger = segmented.get("matte_trigger") or {}
            result["segmentation"] = {
                "ran": bool(segmented.get("object_segmentation")),
                "wall_s": round(time.monotonic() - started, 3),
                "memory_refusal": trigger.get("memory_refusal"),
                "memory_refusal_kind": trigger.get("memory_refusal_kind"),
                "failures": trigger.get("failures"),
            }
    return result


def _last_refusal(stderr: str) -> str:
    for line in reversed((stderr or "").strip().splitlines()):
        if "could not be reserved" in line or "Refusing" in line:
            return line.strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips-dir", default="/tmp/vi015-scratch/media")
    parser.add_argument("--out", default="/tmp/vi015-scratch/out")
    parser.add_argument("--label", default="run")
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="probe only: do not run the fixed multi-model analysis run "
             "(use it with a calm reading, where the run would load a "
             "resident model this box cannot host)")
    parser.add_argument(
        "--memory",
        default="6.0,24.0",
        help="the FIXED memory reading the admission probe uses, as "
             "'<available>,<total>' gigabytes. It is the documented "
             "scheduler test seam (VEP_SCHEDULER_MEMORY_GB): it changes "
             "where the probe reads and nothing else, so the before and "
             "after runs are comparable and a run cannot load a 15 GB "
             "model on a busy box. The LIVE reading is reported beside it.")
    args = parser.parse_args(argv)

    if args.memory:
        os.environ["VEP_SCHEDULER_MEMORY_GB"] = args.memory

    clips_dir = Path(args.clips_dir)
    out_dir = Path(args.out) / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "label": args.label,
        "memory_reading_used": args.memory,
        "machine": _machine_reading(),
        "probe": probe(),
        "competing": probe_concurrently("gemma", "stems"),
    }
    if not args.skip_analysis:
        report["analysis"] = run_analysis(clips_dir, out_dir)
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
