"""One reel build holds the exclusive Resolve lease for its WHOLE duration.

The defect this file answers (captain, 2026-10-05): three workers ran
live reel builds in the captain's Resolve project at the same time. One
worker's builds died mid-build with Resolve returning None
("GetTimelineCount None in the freeze post-Fusion check", then "Timeline
race: expected None, but got None. Mutator changed it"), and another
worker's Reel 28 staging timeline vanished (timeline count 35 -> 34).

Where the lease was taken and released during a build
------------------------------------------------------
`rebuild_reels_in_project` took the exclusive instance lease in short
PER-SECTION holds and released it between them - the instance was FREE
in the gaps, and a second build's holds interleaved with the first's:

  1. "build reels connect" (EXCLUSIVE): the scripting handshake.
  2. "build reels survey" (SHARED): the master read-back.
  3. "draw-gain probe" (EXCLUSIVE): the renderer calibration scratch.
  4. "build reels drift baseline" (EXCLUSIVE): per-reel transform reads.
  5. "build reels debris check" (SHARED): two enumerations.
  6. "reclaim refused staging" (EXCLUSIVE): only when reclaiming.
  7. "build reels master digest" (EXCLUSIVE): the carried master digest.
  8. per reel "decide {name}" (EXCLUSIVE): the carried self-read.
  9. per reel "place {name}" (EXCLUSIVE): the placement.
 10. "apply fusion comps" (EXCLUSIVE): the batch Fusion pass.
 11. "verify built reels" (SHARED): the conformance gate.
 12. "discard refused staging" (EXCLUSIVE): the gate-refusal path.
 13. "promote staged reels" (EXCLUSIVE): the rename onto final names.
 14. "sweep all reels" (SHARED): the informational sweep.
 15. "build reels drift end" (EXCLUSIVE): the closing bracket.
 16. "pending promotions census" (SHARED): the holds reconciliation.

So: per phase and per reel, never for the build. Between those holds a
concurrent build moved the cursor, deleted a staging timeline out from
under a read, and read a timeline count that no longer matched the
project - the per-write cursor check makes each write land where it was
aimed, but it cannot protect a READ from a concurrent deletion, and it
cannot stop one build's discard path from deleting another's staging.

The fix
-------
The build takes ONE exclusive lease at its first line and holds it to
its last - connect, derivation, placement, gate, promotion, sweep. Every
inner hold becomes a reentrant no-op under it. A second build waits at
its own first acquisition (the connect, which IS the whole build now)
and refuses with a named reason after the bounded wait if the first
build never finishes. The per-section holds stay in the code: they
document each section's critical section, and they are correct if the
outer hold is ever removed.

Reads by the shadow watcher go through the broker, which serializes
them behind the build's exclusive grant - they wait, which is the safe
direction. The other long-span writers already hold their whole
operation: `qualification_project.reset()`/qualify()` and the smoke
scripts (`populate_calibration.py`, `probe_render_control_plane.py`,
`probe_fusion_tool_inputs.py`) each take one `@under_lease` for their
whole body. The reel build was the outlier.

The two tests
-------------
`test_the_build_holds_one_exclusive_lease_for_whole_body` pins the new
shape deterministically in-process. `test_two_concurrent_builds_never_
interleave` is the regression test: two subprocesses run the REAL build
against fake Resolves sharing one lock dir, and the second build's body
must never start before the first build's body ends. Both fail on the
per-section shape and pass on the whole-build hold.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from library.tools import resolve_lock
from library.tools import reel_build as rb
from library.tools.resolve_lock import resolve_lease

REPO_ROOT = Path(__file__).resolve().parents[3]

MASTER = "GEO Podcast - Synced"

#: How long the mocked placement plan sleeps. It runs under NO hold on
#: the old shape, so a second build's connect slips in during it - that
#: window is what makes the interleaving observable, and 3s is far wider
#: than the sub-second the second build needs to reach its own plan.
_PREPARE_SECONDS = 3.0

#: How long the mocked placement sleeps - long enough that the two
#: builds' placements cannot be mistaken for simultaneous.
_PLACE_SECONDS = 0.5


class _StagingTimeline:
    """A timeline whose name really changes when renamed."""

    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetUniqueId(self):
        if not getattr(self, "_uid", None):
            type(self)._seq = getattr(type(self), "_seq", 0) + 1
            self._uid = f"{type(self).__name__}-{type(self)._seq}"
        return self._uid


class _ResolveProject:
    """A Resolve project whose pool really creates, renames and deletes."""

    def __init__(self, names):
        self.timelines = [_StagingTimeline(name) for name in names]
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        pool.CreateEmptyTimeline.side_effect = self._create
        self.appends = []
        pool.AppendToTimeline.side_effect = self._append
        self._pool = pool

    def _delete(self, timelines):
        for timeline in timelines:
            self.timelines.remove(timeline)
        return True

    def _create(self, name):
        timeline = _StagingTimeline(name)
        self.timelines.append(timeline)
        return timeline

    def _append(self, items):
        current = self.GetCurrentTimeline()
        self.appends.append(
            (current.GetName() if current is not None else None,
             list(items)))
        return [MagicMock()]

    def GetName(self):
        return "Mock Project"

    def GetMediaPool(self):
        return self._pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def names(self):
        return [t.GetName() for t in self.timelines]

    def GetCurrentTimeline(self):
        return getattr(self, "_current", None)

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


def _moment(number=7, name="Reel 01"):
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = number
    moment.timeline_name = name
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


def _write_project(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.yaml").write_text(
        'resolve: {project_name: "Mock Project", '
        f'timeline_name: "{MASTER}"}}', encoding="utf-8")
    review_dir = project_dir / "pipeline_output" / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch_dir = (project_dir / "pipeline_output" / "scratch"
                   / "timeline_transcript")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    (scratch_dir / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")


# The real functions the spies call through to, captured before patching.
_real_subtitles = rb.reel_subtitle_segments
_real_prepare = rb.prepare_reel_timeline


def _install_build_mocks(project, on_derive=None, on_prepare=None,
                         on_place=None):
    """The mock set that drives one full offline build (see reel_build_runner)."""
    from library.tools import draw_gain_probe as probe_mod
    from library.tools import reel_replace_guard
    from library.tools.resolve_transform import FALLBACK_DRAW_GAIN

    def fake_snapshot(timeline, _project, _project_folder=None):
        return {
            "timeline": {
                "name": timeline.GetName(),
                "unique_id": str(timeline.GetUniqueId()),
                "settings": {},
                "start_frame": 0,
                "end_frame": 0,
            },
            "items": [],
            "markers": [],
        }

    def measured_gain(resolve, project, frame_wh, workdir="",
                      project_folder=None):
        pool = project.GetMediaPool()
        scratch = pool.CreateEmptyTimeline(probe_mod.PROBE_TIMELINE_NAME)
        pool.DeleteTimelines([scratch])
        return {
            "gain": FALLBACK_DRAW_GAIN,
            "source": "measured",
            "disagrees_with_fallback": False,
            "warnings": [],
            "probe": {},
        }

    def verify_side_effect(**kwargs):
        report_path = kwargs.get("json_path")
        if report_path:
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump({"has_errors": False, "findings": []}, handle)
        return 0

    def spy_subtitles(*args, **kwargs):
        if on_derive:
            on_derive()
        return _real_subtitles(*args, **kwargs)

    def spy_prepare(*args, **kwargs):
        if on_prepare:
            on_prepare()
        return _real_prepare(*args, **kwargs)

    def spy_place(**kwargs):
        if on_place:
            on_place()
        name = kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        project.GetMediaPool().CreateEmptyTimeline(name)
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}

    return (
        patch("library.tools.resolve_locale.scriptapp_preserving_locale"),
        patch("library.tools.reel_build.resolve_project_exactly",
              return_value=project),
        patch("library.tools.reel_proposal.read_proposal",
              return_value=[_moment()]),
        patch("library.tools.timeline_ingest.snapshot_timeline"),
        patch("library.tools.subtitle_style.resolve_subtitle_style"),
        patch("library.tools.reel_conformance_verifier.run_verification",
              side_effect=verify_side_effect),
        patch.object(reel_replace_guard, "full_timeline_snapshot", fake_snapshot),
        patch.object(probe_mod, "calibrate", measured_gain),
        patch.object(rb, "reel_subtitle_segments", side_effect=spy_subtitles),
        patch.object(rb, "prepare_reel_timeline", side_effect=spy_prepare),
        patch.object(rb, "build_reel_timeline", side_effect=spy_place),
    )


_BUILD_WORKER = textwrap.dedent("""
    import json
    import os
    import sys
    import time
    from contextlib import contextmanager
    from unittest.mock import MagicMock, patch

    lock_dir, log_path, project_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    os.environ["PIPELINE_RESOLVE_LOCK_DIR"] = lock_dir
    os.environ["PIPELINE_RESOLVE_LEASE_TIMEOUT"] = "60"

    sys.path.insert(0, __REPO__)

    # Never touch live Resolve: the bindings are a double, and the
    # connection itself is stubbed below.
    sys.modules["DaVinciResolveScript"] = MagicMock()

    from library.tools import resolve_lock
    from library.tools import reel_build as rb

    @contextmanager
    def no_broker_turn(*args, **kwargs):
        yield None
    resolve_lock._broker_turn = no_broker_turn

    def log(event):
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write("%.3f %s\\n" % (time.time(), event))

    class _StagingTimeline:
        def __init__(self, name):
            self._name = name
        def GetName(self):
            return self._name
        def SetName(self, name):
            self._name = name
            return True
        def GetUniqueId(self):
            if not getattr(self, "_uid", None):
                type(self)._seq = getattr(type(self), "_seq", 0) + 1
                self._uid = "%s-%d" % (type(self).__name__,
                                       type(self)._seq)
            return self._uid

    class _ResolveProject:
        def __init__(self, names):
            self.timelines = [_StagingTimeline(n) for n in names]
            pool = MagicMock()
            pool.DeleteTimelines.side_effect = self._delete
            pool.CreateEmptyTimeline.side_effect = self._create
            self.appends = []
            pool.AppendToTimeline.side_effect = self._append
            self._pool = pool
        def _delete(self, timelines):
            for t in timelines:
                self.timelines.remove(t)
            return True
        def _create(self, name):
            t = _StagingTimeline(name)
            self.timelines.append(t)
            return t
        def _append(self, items):
            cur = self.GetCurrentTimeline()
            self.appends.append(
                (cur.GetName() if cur is not None else None, list(items)))
            return [MagicMock()]
        def GetName(self):
            return "Mock Project"
        def GetMediaPool(self):
            return self._pool
        def GetTimelineCount(self):
            return len(self.timelines)
        def GetTimelineByIndex(self, index):
            return self.timelines[index - 1]
        def names(self):
            return [t.GetName() for t in self.timelines]
        def GetCurrentTimeline(self):
            return getattr(self, "_current", None)
        def SetCurrentTimeline(self, timeline):
            self._current = timeline
            return True

    def _moment(number=7, name="Reel 01"):
        m = MagicMock()
        m.approval = "approved"
        m.number = number
        m.timeline_name = name
        m.timeline_start = 0.0
        m.timeline_end = 10.0
        return m

    from pathlib import Path
    project_path = Path(project_dir)
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "project.yaml").write_text(
        'resolve: {project_name: "Mock Project", timeline_name: __MASTER_YAML__}',
        encoding="utf-8")
    review = project_path / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = (project_path / "pipeline_output" / "scratch"
               / "timeline_transcript")
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")

    project = _ResolveProject([__MASTER__])

    from library.tools import draw_gain_probe as probe_mod
    from library.tools import reel_replace_guard
    from library.tools.resolve_transform import FALLBACK_DRAW_GAIN

    def fake_snapshot(timeline, _project, _project_folder=None):
        return {
            "timeline": {"name": timeline.GetName(),
                         "unique_id": str(timeline.GetUniqueId()),
                         "settings": {}, "start_frame": 0, "end_frame": 0},
            "items": [], "markers": [],
        }

    def measured_gain(resolve, project, frame_wh, workdir="",
                      project_folder=None):
        pool = project.GetMediaPool()
        scratch = pool.CreateEmptyTimeline(probe_mod.PROBE_TIMELINE_NAME)
        pool.DeleteTimelines([scratch])
        return {"gain": FALLBACK_DRAW_GAIN, "source": "measured",
                "disagrees_with_fallback": False, "warnings": [],
                "probe": {}}

    def verify_side_effect(**kwargs):
        report_path = kwargs.get("json_path")
        if report_path:
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump({"has_errors": False, "findings": []}, handle)
        return 0

    _real_subtitles = rb.reel_subtitle_segments
    _real_prepare = rb.prepare_reel_timeline

    def spy_subtitles(*args, **kwargs):
        log("derive")
        return _real_subtitles(*args, **kwargs)

    def spy_prepare(*args, **kwargs):
        log("prepare-start")
        time.sleep(__PREPARE_SECONDS__)
        return _real_prepare(*args, **kwargs)

    def spy_place(**kwargs):
        log("place")
        time.sleep(__PLACE_SECONDS__)
        name = kwargs.get("timeline_name")
        project.GetMediaPool().CreateEmptyTimeline(name)
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \\
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=project), \\
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment()]), \\
            patch("library.tools.timeline_ingest.snapshot_timeline"), \\
            patch("library.tools.subtitle_style.resolve_subtitle_style"), \\
            patch("library.tools.reel_conformance_verifier.run_verification",
                  side_effect=verify_side_effect), \\
            patch.object(reel_replace_guard, "full_timeline_snapshot",
                         fake_snapshot), \\
            patch.object(probe_mod, "calibrate", measured_gain), \\
            patch.object(rb, "reel_subtitle_segments",
                         side_effect=spy_subtitles), \\
            patch.object(rb, "prepare_reel_timeline",
                         side_effect=spy_prepare), \\
            patch.object(rb, "build_reel_timeline", side_effect=spy_place):
        log("enter")
        try:
            rb.rebuild_reels_in_project(project_dir, organise=False)
            log("exit")
        except Exception as exc:
            log("failed: %r" % (exc,))
            raise
""").replace("__REPO__", repr(str(REPO_ROOT))).replace(
    "__MASTER__", repr(MASTER)).replace(
    "__MASTER_YAML__", '"' + MASTER + '"').replace(
    "__PREPARE_SECONDS__", str(_PREPARE_SECONDS)).replace(
    "__PLACE_SECONDS__", str(_PLACE_SECONDS))


def _start_worker(lock_dir: Path, log: Path, project_dir: Path):
    env = dict(os.environ)
    env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
    env.pop(resolve_lock.INHERIT_ENV, None)
    return subprocess.Popen(
        [sys.executable, "-c", _BUILD_WORKER,
         str(lock_dir), str(log), str(project_dir)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")


def _wait_for_log(log: Path, event: str, timeout: float) -> float:
    """The timestamp of the first `event` line, or raise."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                if line.split(" ", 1)[-1] == event:
                    return float(line.split(" ", 1)[0])
        time.sleep(0.05)
    raise AssertionError(
        f"the log never recorded {event!r} within {timeout:g}s: "
        f"{log.read_text(encoding='utf-8') if log.exists() else '<no log>'}")


def _read_log(log: Path) -> list:
    if not log.exists():
        return []
    return [line.split(" ", 1) for line in
            log.read_text(encoding="utf-8").splitlines()]


@pytest.mark.serial(
    reason="Two real build processes contend for one flock; keep child "
           "startup out of xdist saturation.")
def test_two_concurrent_builds_never_interleave(tmp_path):
    """Two REAL builds, one lock dir, and the second never starts its
    body before the first ends.

    The first build's placement plan sleeps 3s - a section that holds
    NO lease on the old shape. The main test waits for that section to
    begin, then starts the second build. On the per-section shape the
    second build's connect succeeds during the first's hold-free plan
    and its body overlaps the first's; on the whole-build hold the
    second build waits at its connect (which IS the whole build) until
    the first build's body has ended.
    """
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    log = tmp_path / "build.log"
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    _write_project(project_a)
    _write_project(project_b)

    first = _start_worker(lock_dir, log, project_a)
    try:
        # The first build is now inside its hold-free planning section.
        _wait_for_log(log, "prepare-start", 60)
        second = _start_worker(lock_dir, log, project_b)
        try:
            out_b, err_b = second.communicate(timeout=120)
        finally:
            if second.poll() is None:
                second.kill()
                second.communicate(timeout=10)
        out_a, err_a = first.communicate(timeout=120)
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate(timeout=10)

    assert first.returncode == 0, (out_a, err_a)
    assert second.returncode == 0, (out_b, err_b)

    events = _read_log(log)
    by_event = {}
    for stamp, event in events:
        by_event.setdefault(event.split(":")[0], []).append(float(stamp))
    assert "derive" in by_event, events
    assert "exit" in by_event, events

    # The first build to derive is the one that won the lease; the
    # second build's derive must not precede the first build's exit.
    first_exit = by_event["exit"][0]
    second_derive = by_event["derive"][1]
    assert second_derive >= first_exit, (
        f"the second build's body started at {second_derive:.3f}, before "
        f"the first build's body ended at {first_exit:.3f} - the two "
        f"builds interleaved their Resolve writes. Log: {events}")


@contextmanager
def _spy_outer_lease(events):
    """Record every lease the build's own module and the decorator take.

    Patches BOTH names the build calls - `resolve_lock.resolve_lease`
    (which `under_lease`'s wrapper calls) and `reel_build.resolve_lease`
    (the inner per-section holds) - so the record shows the whole shape:
    the outer whole-build hold and the inner holds nested under it.
    """
    real = resolve_lock.resolve_lease
    open_stack = []

    @contextmanager
    def spy(purpose, exclusive=True, **kwargs):
        events.append(("lease-enter", purpose, exclusive))
        open_stack.append(purpose)
        with real(purpose, exclusive=exclusive, **kwargs):
            events.append(("lease-held", purpose, exclusive))
            try:
                yield
            finally:
                open_stack.pop()
        events.append(("lease-exit", purpose, exclusive))

    def snapshot():
        return list(open_stack)

    with patch.object(resolve_lock, "resolve_lease", spy), \
            patch.object(rb, "resolve_lease", spy):
        yield snapshot


def test_the_build_holds_one_exclusive_lease_for_whole_body(
        tmp_path, stub_resolve_script):
    """One exclusive hold, taken at the first line and held to the last.

    The outer hold is the build's whole body: it is taken before any
    derivation, still open during the placement and the gate, and
    released after the sweep. On the per-section shape the first hold is
    "build reels connect" and it closes before the body runs.
    """
    project_dir = tmp_path / "project"
    _write_project(project_dir)
    project = _ResolveProject([MASTER])

    events = []
    markers = []

    def derive():
        markers.append("derive")

    def prepare():
        markers.append("prepare")

    def place():
        markers.append("place")

    patches = _install_build_mocks(project, on_derive=derive,
                                  on_prepare=prepare, on_place=place)
    with _spy_outer_lease(events) as snapshot:
        for p in patches:
            p.start()
        try:
            rb.rebuild_reels_in_project(str(project_dir), organise=False)
        finally:
            for p in patches:
                p.stop()

    enters = [e for e in events if e[0] == "lease-enter"]
    assert enters, "the build took no lease at all"
    first_purpose, first_exclusive = enters[0][1], enters[0][2]
    assert first_purpose == "build reels", (
        f"the build's first lease was {first_purpose!r}, not the "
        f"whole-build hold 'build reels' - the per-section shape is back")
    assert first_exclusive is True, "the whole-build hold must be exclusive"

    # The outer hold is open across the whole body: derivation,
    # placement, gate and promotion all run under it.
    held_purposes = [e[1] for e in events if e[0] == "lease-held"]
    assert "build reels" in held_purposes
    exits = [e for e in events if e[0] == "lease-exit"]
    assert exits[-1][1] == "build reels", (
        f"the build's last lease release was {exits[-1][1]!r}, not the "
        f"whole-build hold - something outlived the build")

    # The body's markers all fired, and the build placed its reel.
    assert markers == ["derive", "prepare", "place"], markers
    assert "Reel 01" in project.names(), project.names()
