"""Ren Qualification: the one Resolve project a live test may touch.

Live-Resolve tests used to run against whatever project was open - the
captain's. The broker now refuses a qualification job anywhere but
`QUALIFICATION_PROJECT` (`library/tools/resolved/jobs.py`); this module
is the other half: the project itself, built from synthetic media into
KNOWN timelines, so a test can assert exact frames and names.

    ren qualification media      # FREE: render the synthetic clips
    ren qualification reset      # borrow Resolve, rebuild the project
    ren qualification qualify    # reset, then qualify ren-resolved live

Synthetic media
---------------
`MEDIA` is the recipe: ffmpeg test sources with a tone, rendered once per
machine into a directory keyed by the recipe's hash
(`media_dir()`), never into a checkout or a project. Frame counts are
exact because the rate and length are.

Known timelines
---------------
`TIMELINES` declares each timeline's clips and markers; `verify` reads
the built project back against it and names every difference. A rebuild
is DELETE + CREATE, so a reset can never inherit a previous run's edits.

Borrowing Resolve
-----------------
The captain works in Resolve with his own project open. `borrowed`
records his open project and current timeline (by UNIQUE ID, since two
timelines may share a name), saves it, yields, and on the way out - also
when the body raised - reloads that project, makes that exact timeline
current, and READS BACK both. A restore that does not read back raises
`RestoreFailed` naming what to reopen by hand. Everything here runs
under the exclusive instance lease; nothing writes the captain's project
beyond the save.

`tests/unit/resolve/test_qualification_project.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from library.tools.resolve_lock import under_lease
from library.tools.resolved.jobs import QUALIFICATION_PROJECT

FPS = 24
WIDTH, HEIGHT = 1920, 1080

#: The synthetic clips: name, seconds, ffmpeg video source, tone (Hz).
MEDIA = (
    ("qual_a", 4, "testsrc2", 440),
    ("qual_b", 3, "smptebars", 660),
    ("qual_c", 2, "color=c=0x2050c0", 880),
)

#: The known timelines. Clips go onto V1 in order; marker frames are
#: relative to the timeline's start.
TIMELINES = (
    {"name": "Q Spine", "clips": ["qual_a", "qual_b", "qual_c"],
     "markers": []},
    {"name": "Q Markers", "clips": ["qual_b"],
     "markers": [{"frame": 0, "color": "Blue", "name": "start"},
                 {"frame": 48, "color": "Green", "name": "mid"}]},
)

MEDIA_ROOT_ENV = "REN_QUALIFICATION_MEDIA"


class QualificationError(RuntimeError):
    """The qualification environment could not be built or used."""


class RestoreFailed(QualificationError):
    """The captain's project or timeline did not read back as found."""


# ── FREE: the media ─────────────────────────────────────────────────

def recipe_key() -> str:
    recipe = {"fps": FPS, "size": [WIDTH, HEIGHT], "media": MEDIA}
    return hashlib.sha256(json.dumps(recipe).encode("utf-8")).hexdigest()[:12]


def media_dir() -> Path:
    root = os.environ.get(MEDIA_ROOT_ENV) or str(
        Path.home() / ".local" / "share" / "vep" / "qualification")
    return Path(root) / recipe_key()


def frames(clip: str) -> int:
    return next(seconds for name, seconds, _, _ in MEDIA
                if name == clip) * FPS


def ensure_media() -> dict:
    """Render any missing clip; returns {clip name: path}."""
    directory = media_dir()
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, seconds, source, tone in MEDIA:
        path = directory / f"{name}.mp4"
        if not path.exists():
            partial = directory / f".{name}.partial.mp4"
            command = [
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i",
                f"{source}{':' if '=' in source else '='}size={WIDTH}x"
                f"{HEIGHT}:rate={FPS}:duration={seconds}",
                "-f", "lavfi", "-i",
                f"sine=frequency={tone}:sample_rate=48000:duration={seconds}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
                "-c:a", "aac", "-shortest", str(partial)]
            done = subprocess.run(command, capture_output=True,
                                  encoding="utf-8", check=False)
            if done.returncode != 0:
                raise QualificationError(
                    f"ffmpeg could not render {name}: {done.stderr.strip()}")
            partial.rename(path)
        paths[name] = path
    return paths


# ── RESOLVE: borrowing it from the captain ──────────────────────────

def _timeline_by_id(project, unique_id: str):
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetUniqueId() == unique_id:
            return timeline
    return None


def _timeline_by_name(project, name: str):
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() == name:
            return timeline
    raise QualificationError(f"no timeline {name!r} in "
                             f"{project.GetName()!r}")


@contextmanager
def borrowed(resolve):
    """Save the captain's project, lend Resolve, put it all back."""
    from library.tools.resolve_lock import exclusive_held
    if not exclusive_held():
        raise QualificationError("borrowing Resolve needs the EXCLUSIVE "
                                 "instance lease")
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    if not project:
        raise QualificationError("Resolve has no project open to return to")
    name = project.GetName()
    if name == QUALIFICATION_PROJECT:
        raise QualificationError(
            f"Resolve is already on {QUALIFICATION_PROJECT!r}, so the "
            f"captain's project is unknown - reopen it by hand first")
    timeline = project.GetCurrentTimeline()
    found = {"project": name,
             "timeline_id": timeline.GetUniqueId() if timeline else None,
             "timeline": timeline.GetName() if timeline else None}
    if not manager.SaveProject():
        raise QualificationError(f"could not save {name!r}; not borrowing")
    try:
        yield found
    finally:
        restore(resolve, found)


def restore(resolve, found: dict) -> None:
    """Reopen the found project and timeline, and read both back."""
    from library.tools.resolve_lock import assert_current_timeline
    manager = resolve.GetProjectManager()
    current = manager.GetCurrentProject()
    if current and current.GetName() == QUALIFICATION_PROJECT:
        manager.SaveProject()
    if not current or current.GetName() != found["project"]:
        current = manager.LoadProject(found["project"])
    reopen = (f"reopen {found['project']!r} on timeline "
              f"{found['timeline']!r} by hand")
    if not current or current.GetName() != found["project"]:
        raise RestoreFailed(f"{found['project']!r} did not reload; {reopen}")
    if found["timeline_id"] is None:
        return
    timeline = _timeline_by_id(current, found["timeline_id"])
    if timeline is None:
        raise RestoreFailed(f"timeline id {found['timeline_id']} is gone "
                            f"from {found['project']!r}; {reopen}")
    assert_current_timeline(current, timeline)
    back = manager.GetCurrentProject()
    now = back.GetCurrentTimeline() if back else None
    if (not back or back.GetName() != found["project"] or not now
            or now.GetUniqueId() != found["timeline_id"]):
        raise RestoreFailed(f"the restore did not read back; {reopen}")


# ── RESOLVE: building the project ───────────────────────────────────

def rebuild(resolve, media: dict):
    """DELETE + CREATE the qualification project; returns it, open.

    Called inside `borrowed`, while the captain's project is still the
    open one - Resolve cannot delete the project it has open.
    """
    from library.tools.resolve_lock import assert_current_timeline
    manager = resolve.GetProjectManager()
    if QUALIFICATION_PROJECT in (manager.GetProjectListInCurrentFolder()
                                 or []):
        if not manager.DeleteProject(QUALIFICATION_PROJECT):
            raise QualificationError(
                f"could not delete the old {QUALIFICATION_PROJECT!r}")
    project = manager.CreateProject(QUALIFICATION_PROJECT)
    if not project:
        raise QualificationError(f"could not create {QUALIFICATION_PROJECT!r}")
    for key, value in (("timelineFrameRate", str(FPS)),
                       ("timelineResolutionWidth", str(WIDTH)),
                       ("timelineResolutionHeight", str(HEIGHT))):
        if not project.SetSetting(key, value):
            raise QualificationError(f"could not set {key}={value}")
    pool = project.GetMediaPool()
    imported = pool.ImportMedia([str(media[name]) for name, *_ in MEDIA])
    by_name = {Path(item.GetName()).stem: item for item in imported or []}
    missing = [name for name, *_ in MEDIA if name not in by_name]
    if missing:
        raise QualificationError(f"Resolve did not import {missing}")
    for spec in TIMELINES:
        timeline = pool.CreateEmptyTimeline(spec["name"])
        if not timeline:
            raise QualificationError(f"could not create {spec['name']!r}")
        assert_current_timeline(project, timeline)
        if not pool.AppendToTimeline([by_name[c] for c in spec["clips"]]):
            raise QualificationError(f"could not place {spec['name']!r}")
        for marker in spec["markers"]:
            timeline.AddMarker(marker["frame"], marker["color"],
                               marker["name"], "", 1, "")
    if not manager.SaveProject():
        raise QualificationError(f"could not save {QUALIFICATION_PROJECT!r}")
    return project


def verify(project) -> list:
    """Every way the open project differs from `TIMELINES`; [] is built."""
    problems = []
    for spec in TIMELINES:
        try:
            timeline = _timeline_by_name(project, spec["name"])
        except QualificationError as exc:
            problems.append(str(exc))
            continue
        items = timeline.GetItemListInTrack("video", 1) or []
        names = [Path(item.GetName()).stem for item in items]
        if names != spec["clips"]:
            problems.append(f"{spec['name']}: V1 holds {names}, "
                            f"declared {spec['clips']}")
        # GetEndFrame is EXCLUSIVE: measured live on Resolve 21.1
        # (2026-10-02), a 216-frame timeline read end - start = 216.
        length = timeline.GetEndFrame() - timeline.GetStartFrame()
        declared = sum(frames(clip) for clip in spec["clips"])
        if length != declared:
            problems.append(f"{spec['name']}: {length} frames, "
                            f"declared {declared}")
        marks = {int(frame): data for frame, data in
                 (timeline.GetMarkers() or {}).items()}
        wanted = {m["frame"]: m for m in spec["markers"]}
        if sorted(marks) != sorted(wanted) or any(
                marks[f].get("name") != wanted[f]["name"] for f in wanted
                if f in marks):
            problems.append(f"{spec['name']}: markers {sorted(marks)}, "
                            f"declared {sorted(wanted)}")
    return problems


@under_lease("ren qualification reset")
def reset() -> dict:
    """The one command: borrow Resolve, rebuild, verify, give it back."""
    from library.tools.marker_feedback import connect_resolve
    media = ensure_media()
    resolve = connect_resolve()
    with borrowed(resolve) as found:
        project = rebuild(resolve, media)
        problems = verify(project)
    if problems:
        raise QualificationError("the rebuilt project does not match its "
                                 "declaration: " + "; ".join(problems))
    return {"project": QUALIFICATION_PROJECT, "restored": found,
            "timelines": [spec["name"] for spec in TIMELINES]}


# ── RESOLVE: qualifying the broker ──────────────────────────────────

@contextmanager
def _broker(directory: Path):
    """A ren-resolved on its own socket, inheriting this process's lease."""
    from library.tools.resolved import client
    env = dict(os.environ, PIPELINE_RESOLVE_LOCK_DIR=str(directory),
               REN_SHADOW_DB=str(directory / "shadow.sqlite3"))
    process = subprocess.Popen(
        [sys.executable, "-m", "library.tools.resolved", "serve"], env=env,
        cwd=str(Path(__file__).resolve().parents[2]))
    path = directory / "ren-resolved.sock"
    try:
        deadline = time.time() + 30
        while client.ping(path) is None:
            if process.poll() is not None or time.time() > deadline:
                raise QualificationError("ren-resolved did not start")
            time.sleep(0.2)
        yield path
    finally:
        try:
            client.call({"op": "shutdown"}, path)
        except (ConnectionError, client.BrokerError, OSError):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()             # the exact process we started
            process.wait(timeout=10)


def _check(results: list, name: str, ok: bool, detail) -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})


def qualify_broker(resolve, project, socket: Path) -> list:
    """Each broker property, judged against the live qualification project."""
    from library.tools.resolve_lock import assert_current_timeline
    from library.tools.resolved import client
    from library.tools.resolved.jobs import JobRefused, prepare
    results = []
    spine = _timeline_by_name(project, "Q Spine")
    assert_current_timeline(project, spine)

    def submit(kind, params, **kw):
        return client.submit(kind, params, path=socket, **kw)

    def result(job_id):
        return client.result(job_id, wait=120, path=socket)

    # Coalescing: identical reads submitted while a grant holds Resolve.
    with client.grant({"purpose": "qualify: hold the queue"}, path=socket):
        snap = [submit("timeline.snapshot",
                       {"project": QUALIFICATION_PROJECT,
                        "timeline": "Q Markers"}) for _ in range(2)]
        axi = [submit("resolve_axi",
                      {"argv": ["items", "--timeline", "Q Spine",
                                "--refresh-live"],
                       "cwd": os.getcwd()}) for _ in range(2)]
    _check(results, "identical snapshots coalesce",
           snap[1] == {"id": snap[0]["id"], "coalesced": True}, snap)
    base = result(snap[0]["id"])
    _check(results, "snapshot records a generation",
           base["state"] == "done" and base["result"]["generation"] >= 1,
           base)
    if base["state"] != "done":
        return results          # every check below patches on this base
    axi_receipt = result(axi[0]["id"])
    _check(results, "identical resolve-axi reads coalesce",
           axi[1]["coalesced"] and axi_receipt["state"] == "done"
           and axi_receipt["result"]["exit_code"] == 0
           and "qual_a" in axi_receipt["result"]["stdout"],
           {"submits": axi, "receipt": axi_receipt})

    generation = base["result"]["generation"]
    patch = {"id": f"qualify-{int(time.time())}",
             "project": QUALIFICATION_PROJECT, "timeline": "Q Markers",
             "base_generation": generation, "capability": "reel.touchup",
             "affected_spans": [[24, 25]], "conflict_domains": ["markers"],
             "operations": [{"op": "marker.add", "frame": 24,
                             "color": "Yellow", "name": "qualify"}],
             "preconditions": [{"kind": "marker_absent", "frame": 24}],
             "postconditions": [{"kind": "duration_unchanged"}]}
    applied = result(submit("timeline.apply_patch", {"patch": patch})["id"])
    markers = _timeline_by_name(project, "Q Markers").GetMarkers() or {}
    _check(results, "a patch commits and reads back",
           applied["state"] == "done"
           and applied["result"]["status"] == "committed"
           and any(int(f) == 24 for f in markers),
           {"receipt": applied, "markers": sorted(int(f) for f in markers)})
    _check(results, "the broker puts the cursor back",
           project.GetCurrentTimeline().GetUniqueId() == spine.GetUniqueId(),
           project.GetCurrentTimeline().GetName())

    stale = dict(patch, id=patch["id"] + "-stale",
                 operations=[{"op": "marker.add", "frame": 24,
                              "color": "Red", "name": "stale"}])
    refused = result(submit("timeline.apply_patch", {"patch": stale})["id"])
    _check(results, "a stale patch is rejected with the head",
           refused["state"] == "rejected"
           and refused["result"]["refusal"] == "StalePatch",
           refused)

    try:
        with client.grant({"purpose": "qualify: a test section",
                           "project": QUALIFICATION_PROJECT},
                          qualification=True, wait=60, path=socket):
            granted = True
    except client.BrokerError as exc:
        granted = str(exc)
    _check(results, "a qualification grant runs on Ren Qualification",
           granted is True, granted)
    try:
        prepare("timeline.snapshot", {"project": "Podcast (field test)",
                                      "timeline": "Reel 01"},
                qualification=True)
        refused_user = False
    except JobRefused:
        refused_user = True
    _check(results, "a qualification job on a user project is refused",
           refused_user, None)

    receipts = client.call({"op": "list", "limit": 50}, socket)["jobs"]
    _check(results, "every finished job has a receipt with its costs",
           all(job["wait_seconds"] is not None
               and job["hold_seconds"] is not None for job in receipts
               if job["state"] in ("done", "rejected")),
           [{k: job[k] for k in ("kind", "state", "priority", "subscribers",
                                 "wait_seconds", "hold_seconds")}
            for job in receipts])
    return results


@under_lease("ren qualification qualify")
def qualify() -> dict:
    """Reset the project, then qualify ren-resolved against it, live."""
    from library.tools.marker_feedback import connect_resolve
    media = ensure_media()
    directory = Path(tempfile.mkdtemp(prefix="rq", dir="/tmp"))
    try:
        resolve = connect_resolve()
        with borrowed(resolve) as found:
            project = rebuild(resolve, media)
            problems = verify(project)
            results = []
            if not problems:
                with _broker(directory) as socket:
                    results = qualify_broker(resolve, project, socket)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    return {"restored": found, "build_problems": problems,
            "checks": results,
            "passed": not problems and bool(results)
            and all(r["ok"] for r in results)}


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="ren qualification",
        description=f"The {QUALIFICATION_PROJECT!r} project live tests use.")
    parser.add_argument("verb", choices=("media", "reset", "qualify"))
    args = parser.parse_args(argv)
    if args.verb == "media":
        report = {name: str(path) for name, path in ensure_media().items()}
    elif args.verb == "reset":
        report = reset()
    else:
        report = qualify()
    print(json.dumps(report, indent=2, default=str))
    return 0 if args.verb != "qualify" or report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
