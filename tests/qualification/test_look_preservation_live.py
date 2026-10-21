"""QA-005 live: the canvas mismatch, caught on a scratch copy.

The defect this reproduces is Reel 26's closing shot, 2026-10-20: its
Fusion comp was authored on the SOURCE canvas while every other comp in
the reel was authored on the DELIVERY canvas, so its vignette and glow
landed on a different picture and the shot read flatter and cooler than
the shot beside it.  PR 3009 re-applied the declared look, the shot
moved, and PR 3079 had to restore the version the captain preferred.

The scratch copy is a timeline of this run's own in the REN
QUALIFICATION project - the one project a live test may touch - named
for the run and deleted afterwards.  Nothing touches a timeline that
was already there, and nothing touches the captain's project.

The scenario, in the engine's own words:

- the reel DELIVERS 1080x1920;
- the shot's comp is authored on the DELIVERY canvas - the shot as it
  was approved;
- the revision re-authors that comp on the SOURCE canvas - which is
  what the engine's own comp pass does, because Fusion composites over
  the SOURCE frame (`library/tools/fusion/comp_builder.py`);
- the check must CATCH that, and a revision that changed nothing must
  PASS.

The comp is imported in a SEPARATE PROCESS, because Resolve refuses a
timeline created and imported into in the same one
(`library/tools/execution/apply_fusion_comps.py`).

`run_scenario` is the whole scenario, so the same steps can be run from
a driver that borrows the open project - the `resolve_session` fixture
requires the qualification project to be open already, and skips where
another lane's project is open instead.  The
`qualification_resolve_session` fixture borrows it instead, so the
test runs whatever Resolve has open.

Drives the live Resolve: skipped unless `REN_LIVE_RESOLVE=1` inside a
granted borrow-resolve window.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.resolve_live

SCRATCH_PREFIX = "Q LookPreservation "
DELIVERY = (1080, 1920)

#: The subprocess that imports the comp. It is written out rather than
#: imported, because it must be a DIFFERENT PROCESS from the one that
#: created the timeline.
_IMPORTER = '''
import json
import sys
import time

from library.tools.marker_feedback import connect_resolve
from library.tools.resolve_lock import resolve_lease

timeline_name = sys.argv[1]
comp_path = sys.argv[2]

resolve = None
with resolve_lease("QA-005 live: import a comp onto a scratch copy",
                   exclusive=True):
    resolve = connect_resolve()
    manager = resolve.GetProjectManager()
    # Resolve's project switch settles asynchronously: the scratch
    # timeline can be a moment behind the open project. The wait is
    # bounded and it says what it could not find.
    deadline = time.time() + 30.0
    target = None
    project = None
    while target is None and time.time() < deadline:
        project = manager.GetCurrentProject()
        if project is not None:
            for index in range(1, int(project.GetTimelineCount() or 0) + 1):
                candidate = project.GetTimelineByIndex(index)
                if candidate is not None and candidate.GetName() == timeline_name:
                    target = candidate
                    break
        if target is None:
            time.sleep(0.5)
    if target is None:
        raise SystemExit(
            "no timeline named %r in %r; the project holds %r" % (
                timeline_name,
                project.GetName() if project is not None else "no project",
                [project.GetTimelineByIndex(i + 1).GetName()
                 for i in range(int(project.GetTimelineCount() or 0))]
                if project is not None else []))
    items = target.GetItemListInTrack("video", 1) or []
    if not items:
        raise SystemExit("the scratch timeline holds no picture clip")
    item = items[0]
    for comp in list(item.GetFusionCompNameList() or []):
        item.DeleteFusionCompByName(comp)
    item.ImportFusionComp(comp_path)
    print(json.dumps({"ok": True,
                      "comps": list(item.GetFusionCompNameList() or [])}))
'''


def _pool_clip(pool, stem):
    root = pool.GetRootFolder()
    for item in root.GetClipList() or []:
        name = item.GetName() or ""
        if name == stem or name.rsplit(".", 1)[0] == stem:
            return item
    return None


def _pool_clips(pool):
    root = pool.GetRootFolder()
    return [item for item in (root.GetClipList() or [])
            if item is not None and (item.GetName() or "")]


def _comp_file(directory: Path, canvas, frames: int = 48) -> str:
    """A Fusion comp whose solid image is drawn at `canvas`.

    The engine's own comp builder writes the canvas on the Background
    node's Width and Height, and that is where the reader reads it
    back - so the comp here is the engine's, not a hand-written one.
    """
    from library.tools.fusion.comp_builder import build_effect_comp

    content = build_effect_comp(
        {"vignette": True, "vignette_soft": 0.35, "vignette_blend": 0.25},
        frames, tuple(canvas))
    path = directory / f"comp_{canvas[0]}x{canvas[1]}.comp"
    path.write_text(content, encoding="utf-8")
    return str(path)


def _import_comp(directory: Path, timeline_name: str, comp_path: str) -> dict:
    """Import one comp onto the scratch timeline, in another process."""
    script = directory / "import_comp.py"
    script.write_text(_IMPORTER, encoding="utf-8")
    environment = dict(os.environ)
    root = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(
        [root, environment.get("PYTHONPATH", "")]).strip(os.pathsep)
    finished = subprocess.run(
        [sys.executable, str(script), timeline_name, comp_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, cwd=root, env=environment, timeout=300)
    if finished.returncode != 0:
        raise AssertionError(
            f"the comp import failed: "
            f"{(finished.stderr or '').strip()[-400:]}")
    return json.loads((finished.stdout or "{}").strip().splitlines()[-1])


def _capture(project, timeline) -> dict:
    """The recorded state of the scratch timeline, through the one reader.

    The read is retried: another lane switching projects mid-read
    invalidates the handle, and a live test that fails because the
    machine was busy is not a measurement.  The scratch timeline is
    made current again before each attempt, because the read-back
    demands the CURRENT timeline (AGENTS.md 5).
    """
    import time

    from library.tools import reel_read

    last = None
    for _attempt in range(3):
        try:
            project.SetCurrentTimeline(timeline)
            return reel_read.read_reel(timeline, project.GetName(),
                                       mode=reel_read.QUICK,
                                       resolve_project=project)
        except Exception as exc:  # noqa: BLE001 - retried below
            last = exc
            time.sleep(2)
    raise AssertionError(
        f"the scratch timeline could not be read back: {last}")


def run_scenario(project, work_dir: Path, media: str = "qual_b") -> dict:
    """The whole QA-005 live scenario, on a scratch copy of the reel.

    Returns the two vericts: the one that must FAIL (the canvas
    mismatch) and the one that must PASS (a revision that changed
    nothing about the look).
    """
    from library.tools import look_preservation, preservation_invariant

    pool = project.GetMediaPool()
    clip = _pool_clip(pool, media)
    if clip is None:
        clip = next((candidate for candidate in _pool_clips(pool)
                     if candidate.GetClipProperty("Resolution")), None)
    assert clip is not None, (
        "the qualification pool holds no clip with a resolution")
    source_resolution = clip.GetClipProperty("Resolution") or ""
    assert "x" in source_resolution, (
        "the scratch clip states no resolution, so no canvas can be read")
    source_canvas = tuple(int(part)
                           for part in source_resolution.split("x"))

    name = SCRATCH_PREFIX + uuid.uuid4().hex[:8]
    timeline = pool.CreateEmptyTimeline(name)
    assert timeline is not None, f"could not create {name!r}"
    try:
        project.SetCurrentTimeline(timeline)
        # Resolve refuses a resolution change until the timeline says it
        # carries custom settings - the same order the engine's own
        # qualification builder uses (`axi_qualification_configurations`).
        assert timeline.SetSetting("useCustomSettings", "1"), (
            "Resolve refused the custom settings the delivery canvas needs")
        for key, value in (("timelineResolutionWidth", str(DELIVERY[0])),
                           ("timelineResolutionHeight", str(DELIVERY[1]))):
            assert timeline.SetSetting(key, value), (
                f"Resolve refused {key}={value}")
        assert pool.AppendToTimeline([clip]), "could not place the clip"
        items = timeline.GetItemListInTrack("video", 1) or []
        assert len(items) == 1, f"the scratch holds {len(items)} clips"

        # The shot as it was APPROVED: its comp is authored on the
        # canvas the reel delivers.
        _import_comp(work_dir, name, _comp_file(work_dir, DELIVERY))
        before = _capture(project, timeline)

        # The revision re-authors that comp on the SOURCE canvas - what
        # the engine's own comp pass does.
        _import_comp(work_dir, name, _comp_file(work_dir, source_canvas))
        after = _capture(project, timeline)

        scope = preservation_invariant.scope_for(
            [{"op": "clip.trim", "unique_id": items[0].GetUniqueId()}])

        caught = look_preservation.verify_look_preservation(
            before=before, after=after, scope=scope)
        report = look_preservation.canvas_report(
            look_preservation.capture_look(after))
        clean = look_preservation.verify_look_preservation(
            before=after, after=after, scope=scope)
        return {
            "caught": caught,
            "canvas_report": report,
            "clean": clean,
            "source_canvas": list(source_canvas),
            "delivery": list(DELIVERY),
            "before_look": look_preservation.capture_look(before),
            "after_look": look_preservation.capture_look(after),
        }
    finally:
        pool.DeleteTimelines([timeline])


@pytest.mark.serial(reason="the comp import holds the instance lease")
def test_the_canvas_mismatch_is_caught_and_a_clean_revision_passes(
        qualification_resolve_session, tmp_path):
    """The check catches the canvas change and passes the clean case."""
    project = qualification_resolve_session
    outcome = run_scenario(project, tmp_path)

    caught = outcome["caught"]
    assert caught["status"] == "failed", caught
    assert any("canvas" in failure or "fusion" in failure
               for failure in caught["failures"]), caught["failures"]
    assert outcome["canvas_report"], "the canvas mismatch was not reported"
    assert any(row["kind"] == "canvas_differs_from_delivery"
               for row in outcome["canvas_report"]), outcome["canvas_report"]
    assert outcome["clean"]["status"] == "verified", outcome["clean"]
