"""The live AXI qualification matrix.

Each candidate primitive runs against each disposable configuration. The
run BUILDS the configuration, MEASURES the runtime state, executes the
operation through its production route, and COMPARES the state before
and after. What the run measured becomes the coverage the registry
imports, and what it changed besides the request is what keeps it from
being evidence.

The walk is three phases, because the operation itself must run through
the production route - the route the qualification is about - and that
route owns the broker and its lease:

1. BUILD: the configuration is created, measured and snapshotted.
2. OPERATE: the operation runs through `resolve-axi edit-request
   submit`, which routes it, authorizes it and verifies it.
3. COMPARE: the timeline is snapshotted again and the two snapshots are
   compared.

Every phase runs under the qualification session, in the DISPOSABLE
qualification project. Nothing here touches a captain's project.

The report the walk writes is the v2 evidence format
(`library/tools/axi_qualification.py`), and the registry imports it with:

    python3 -m library.tools.axi_qualification --import-result result.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REN = ROOT / "bin" / "ren"
REPORT_DIR = Path(__file__).resolve().parent / "reports" / "matrix"

#: The qualification project the walk builds its configurations in.
PROJECT = "Ren Qualification"

#: The pool sources the configurations place from. The qualification
#: media is rendered by `library/tools/qualification_project.py`; the
#: still and the caption cards are the live lane's fixtures.
QUALIFICATION_MEDIA = ("qual_a.mp4", "qual_b.mp4", "qual_c.mp4")
STILL_SOURCE = "fm-qual-logo-180x64.png"
CAPTION_SOURCES = ("axc-caption-1.mov", "axc-caption-2.mov")

#: The script that builds one configuration, measures it and snapshots
#: it. It runs INSIDE Resolve, with `project` and `resolve` in scope.
BUILD_SCRIPT = """
import json

from library.tools import axi_qualification_configurations as configurations
from library.tools import axi_qualification_snapshot as snapshots
from library.tools import axi_qualification_state as qualification_state
from library.tools import reel_read

configuration = configurations.configuration_by_id({configuration_id!r})
output = {output!r}

pool = project.GetMediaPool()
available = {{}}
for item in (pool.GetRootFolder().GetClipList() or []):
    available[str(item.GetName())] = item
pool_items = {{}}
for _kind, source in configuration.media:
    if source not in pool_items:
        if source not in available:
            raise RuntimeError(
                "the pool holds no item called " + source)
        pool_items[source] = available[source]

built = configurations.build(project, configuration, pool_items=pool_items)

built_timeline = None
for index in range(1, int(project.GetTimelineCount() or 0) + 1):
    candidate = project.GetTimelineByIndex(index)
    if (candidate is not None
            and candidate.GetName() == configuration.timeline_name):
        built_timeline = candidate
        break
if built_timeline is None:
    raise RuntimeError(
        "the configuration timeline did not appear in the project")
if not project.SetCurrentTimeline(built_timeline):
    raise RuntimeError("the configuration timeline did not become current")

measured = qualification_state.inspect_handles(
    resolve=resolve, resolve_project=project,
    timeline=built_timeline, operations=[])
declared = configurations.shape_of(configuration)
differences = {{field: [declared[field], measured.get("state", {{}}).get(field)]
                for field in declared
                if measured.get("state", {{}}).get(field) != declared[field]}}

tracks = reel_read.read_tracks(built_timeline)
settings = {{key: built_timeline.GetSetting(key) for key in
            ("timelineFrameRate", "timelineResolutionWidth",
             "timelineResolutionHeight")}}
snapshot = snapshots.snapshot(tracks, settings)
items = []
for track in tracks:
    for clip in track.get("clips") or ():
        items.append({{"unique_id": clip.get("unique_id"),
                      "name": clip.get("name"),
                      "track_type": track.get("type"),
                      "track_index": track.get("index"),
                      "media_type": clip.get("media_type"),
                      "record_in": clip.get("record_in"),
                      "record_out": clip.get("record_out")}})

with open(output, "w", encoding="utf-8") as handle:
    json.dump({{"configuration": configuration.id,
              "built": built,
              "declared": declared,
              "measured": measured,
              "shape_differences": differences,
              "snapshot": snapshot,
              "items": items}}, handle)
result = {{"configuration": configuration.id,
          "shape_differences": differences,
          "items": len(items)}}
"""

#: The script that snapshots the timeline again, after the operation.
SNAPSHOT_SCRIPT = """
import json

from library.tools import axi_qualification_snapshot as snapshots
from library.tools import reel_read

output = {output!r}
target = {timeline_name!r}

built_timeline = None
for index in range(1, int(project.GetTimelineCount() or 0) + 1):
    candidate = project.GetTimelineByIndex(index)
    if candidate is not None and candidate.GetName() == target:
        built_timeline = candidate
        break
if built_timeline is None:
    raise RuntimeError("the configuration timeline disappeared")

tracks = reel_read.read_tracks(built_timeline)
settings = {{key: built_timeline.GetSetting(key) for key in
            ("timelineFrameRate", "timelineResolutionWidth",
             "timelineResolutionHeight")}}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(snapshots.snapshot(tracks, settings), handle)
result = {{"timeline": target}}
"""


def build_script(configuration_id: str, output: Path) -> str:
    """The BUILD phase script for one configuration."""
    return BUILD_SCRIPT.format(configuration_id=configuration_id,
                                output=str(output))


def snapshot_script(timeline_name: str, output: Path) -> str:
    """The COMPARE phase script for one configuration."""
    return SNAPSHOT_SCRIPT.format(timeline_name=timeline_name,
                                   output=str(output))


def request_for(candidate, target: dict) -> dict:
    """The semantic EditRequest that runs one candidate operation.

    `target` names the item the operation acts on, by its exact unique
    id and the row it lives on.
    """
    return {
        "id": f"axi-matrix-{candidate.operation}-{target.get('unique_id', '')}",
        "project": PROJECT,
        "timeline": target.get("timeline", ""),
        "operations": [{
            "op": candidate.operation,
            "target": {"unique_id": str(target.get("unique_id") or ""),
                       "track_type": str(target.get("track_type") or ""),
                       "track_index": target.get("track_index")},
            "params": dict(target.get("params") or {}),
            "expected_effect": target.get("expected_effect") or
            f"{candidate.operation} lands on the named item",
        }],
    }


def _submit(steps: list[dict], *, wait_seconds: int = 600) -> dict:
    """Run steps inside one borrowed qualification session."""
    script = json.dumps({"project": PROJECT, "steps": steps})
    completed = subprocess.run(
        [str(REN), "resolved", "submit", "resolve.qualification_session",
         script, "--qualification", "--wait", str(wait_seconds)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=wait_seconds + 120, check=False)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    receipt = json.loads(completed.stdout)
    if receipt.get("state") != "done" or not receipt.get("result"):
        raise RuntimeError(
            f"the qualification session failed: {receipt.get('error', receipt)}")
    return receipt


def _run_script(script: str, *, purpose: str) -> dict:
    """Run one script inside Resolve, as a broker step."""
    path = Path("/tmp") / f"axi-matrix-{purpose.replace(' ', '-')}.py"
    path.write_text(script, encoding="utf-8")
    try:
        return _submit([{"resolve_axi": ["run", "--file", str(path),
                                         "--purpose", purpose,
                                         "--unsafe"]}])
    finally:
        path.unlink(missing_ok=True)


def build_configuration(configuration, output: Path) -> dict:
    """BUILD one configuration and return what the run measured."""
    script = build_script(configuration.id, output)
    _run_script(script, purpose=f"AXI matrix build {configuration.id}")
    return json.loads(output.read_text(encoding="utf-8"))


def compare_configuration(configuration, before: Path, after: Path) -> dict:
    """COMPARE the two snapshots of one configuration."""
    from library.tools import axi_qualification_snapshot as snapshots

    script = snapshot_script(configuration.timeline_name, after)
    _run_script(script, purpose=f"AXI matrix snapshot {configuration.id}")
    earlier = json.loads(before.read_text(encoding="utf-8"))
    later = json.loads(after.read_text(encoding="utf-8"))
    return snapshots.compare(earlier.get("snapshot") or {},
                             later.get("snapshot") or {}, [])


def main(argv: list[str] | None = None) -> int:
    """Walk the matrix, writing one result file per configuration."""
    from library.tools import axi_qualification_configurations as configs

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    selected = (argv or sys.argv[1:]) or [conf.id for conf in configs.CONFIGURATIONS]
    for identifier in selected:
        configuration = configs.configuration_by_id(identifier)
        output = REPORT_DIR / f"{configuration.id}.json"
        measured = build_configuration(configuration, output)
        print(json.dumps({"configuration": configuration.id,
                          "shape_differences":
                              measured.get("shape_differences")},
                         indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
