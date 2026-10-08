"""End-to-end fixed-input FFrames carriage benchmark (run through vep-verify-run.sh)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "docs/fframes-evidence/phase2-side-by-side/repro"))

from props import props

from library.tools import fframes_render


def _render_and_measure(composition: str, declaration: dict, output: str, root: str) -> tuple[float, int]:
    """Sample aggregate RSS for this process and its descendants while rendering."""
    done = threading.Event()
    failure = []

    def render() -> None:
        try:
            fframes_render.render_one_card(
                composition, declaration, output, root, stream=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            failure.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=render)
    worker.start()
    started = time.perf_counter()
    peak_rss_kib = 0
    root_pid = os.getpid()
    while not done.wait(0.1):
        snapshot = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss=,comm="],
            capture_output=True, check=True, text=True, encoding="utf-8")
        rows = {}
        for line in snapshot.stdout.splitlines():
            fields = line.split(None, 3)
            if len(fields) == 4:
                rows[int(fields[0])] = (int(fields[1]), int(fields[2]), fields[3])
        descendants = {root_pid}
        changed = True
        while changed:
            changed = False
            for pid, (ppid, _, _) in rows.items():
                if pid not in descendants and ppid in descendants:
                    descendants.add(pid)
                    changed = True
        rss = sum(mem for pid, (_, mem, command) in rows.items()
                  if pid in descendants and not command.endswith("/ps"))
        peak_rss_kib = max(peak_rss_kib, rss)
    worker.join()
    if failure:
        raise failure[0]
    return time.perf_counter() - started, peak_rss_kib * 1024


def main() -> None:
    results = []
    with tempfile.TemporaryDirectory(prefix="fframes-native-bench-") as root:
        for composition in ("SubtitleOverlay", "FullFrameCard"):
            declaration = props(composition)
            output = os.path.join(root, f"{composition}.mov")
            elapsed, peak_rss = _render_and_measure(
                composition, declaration, output, root)
            count = int(declaration["durationInFrames"])
            results.append({
                "composition": composition,
                "frames": count,
                "wall_seconds": round(elapsed, 3),
                "frames_per_second": round(count / elapsed, 2),
                "encoded_bytes": os.path.getsize(output),
                "peak_aggregate_rss_bytes": peak_rss,
                "intermediate_frame_files": 0,
                "disk_bytes_in_final_output_and_scratch": sum(
                    os.path.getsize(Path(directory) / name)
                    for directory, _, names in os.walk(root)
                    for name in names),
            })
            os.unlink(output)
    print(json.dumps({
        "machine": os.uname().nodename,
        "platform": sys.platform,
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, check=True, text=True,
            encoding="utf-8").stdout.strip(),
        "warmth": "first composition cold, second composition warm",
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
