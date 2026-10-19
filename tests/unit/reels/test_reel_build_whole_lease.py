"""Reel preparation leaves Resolve free, then each placement is atomic.

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
A separate build turn serializes builds without holding Resolve during
preparation. Graphics and placement-plan work completes before one
exclusive per-reel lease spans the live read, decision and placement.
The batch Fusion pass, verification and promotion retain their own
leases.

Reads by the shadow watcher go through the broker, which serializes
them behind the build's exclusive grant - they wait, which is the safe
direction. The other long-span writers already hold their whole
operation: `qualification_project.reset()`/qualify()` and the smoke
scripts (`populate_calibration.py`, `probe_render_control_plane.py`,
`probe_fusion_tool_inputs.py`) each take one `@under_lease` for their
whole body. The reel build was the outlier.

The acceptance tests
--------------------
`test_graphics_prepare_runs_without_resolve_lease` blocks a fake graphics
renderer while a second Resolve reader acquires the instance, then proves
the decision and placement share one exclusive lease. The subprocess race
test ensures two builds still do not interleave.
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

from library.tools import reel_build as rb
from library.tools import resolve_lock

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
    env.pop(resolve_lock.INHERIT_MODE_ENV, None)
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

    The first build's placement plan sleeps 3s while its Resolve lease
    is free. The main test starts the second build during that pause.
    The separate build-turn lock keeps the second build's derivation
    from overlapping the first build's sidecar writes.
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
    """Record every Resolve lease taken through either module reference.

    Patches both names used by the build so tests can inspect the active
    cursor-sensitive sequence at each fake Resolve operation.
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


def test_graphics_prepare_runs_without_resolve_lease(
        tmp_path, stub_resolve_script, monkeypatch):
    """A shared reader enters during blocked graphics work, but not placement."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lock_dir))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)

    @contextmanager
    def no_broker(*args, **kwargs):
        yield None

    monkeypatch.setattr(resolve_lock, "_broker_turn", no_broker)
    monkeypatch.setenv("PIPELINE_RESOLVE_LEASE_TIMEOUT", "5")
    perf_path = tmp_path / "perf.jsonl"
    monkeypatch.setenv("REN_PERF_LEDGER", str(perf_path))
    monkeypatch.setenv("REN_PERF_RUN", "lease-split-test")
    monkeypatch.setenv("REN_PERF_CAPABILITY", "reel.build")

    project_dir = tmp_path / "project"
    _write_project(project_dir)
    project = _ResolveProject([MASTER])
    renderer_entered = []
    place_reader = []
    events = []
    markers = []
    place_snapshot = []
    graphics_marker = tmp_path / "graphics-reader.json"
    place_marker = tmp_path / "placement-reader.json"

    reader_script = textwrap.dedent("""        import json, os, sys, time
        from pathlib import Path
        from contextlib import contextmanager
        from library.tools import resolve_lock
        os.environ.pop(resolve_lock.INHERIT_ENV, None)
        os.environ.pop(resolve_lock.INHERIT_MODE_ENV, None)
        @contextmanager
        def no_broker(*args, **kwargs):
            yield None
        resolve_lock._broker_turn = no_broker
        marker = Path(sys.argv[1])
        marker.write_text(json.dumps({"state": "attempting"}), encoding="utf-8")
        with resolve_lock.resolve_lease(
                "acceptance shared reader", exclusive=False):
            marker.write_text(json.dumps({"state": "acquired"}), encoding="utf-8")
    """)

    def blocked_graphics_renderer():
        # This callback represents the renderer's blocked section. It does
        # not return until a separate process has actually acquired Resolve.
        proc = subprocess.run(
            [sys.executable, "-c", reader_script, str(graphics_marker)],
            cwd=REPO_ROOT,
            env={**os.environ, resolve_lock.LOCK_DIR_ENV: str(lock_dir)},
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            check=False)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert json.loads(
            graphics_marker.read_text(encoding="utf-8"))["state"] == "acquired"
        renderer_entered.append(True)
        markers.append("graphics")

    def placement_reader_attempt():
        proc = subprocess.Popen(
            [sys.executable, "-c", reader_script, str(place_marker)],
            cwd=REPO_ROOT,
            env={**os.environ, resolve_lock.LOCK_DIR_ENV: str(lock_dir)},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            encoding="utf-8")
        place_reader.append(proc)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if place_marker.exists():
                body = json.loads(place_marker.read_text(encoding="utf-8"))
                if body["state"] == "attempting":
                    break
            time.sleep(0.01)
        else:
            _, stderr = proc.communicate(timeout=5)
            raise AssertionError(
                f"reader did not reach its lease attempt; "
                f"returncode={proc.returncode}, stderr={stderr!r}")
        time.sleep(0.05)
        assert json.loads(
            place_marker.read_text(encoding="utf-8"))["state"] == "attempting"
        place_snapshot.extend(snapshot())

    def place():
        markers.append("place")
        placement_reader_attempt()

    patches = _install_build_mocks(
        project, on_derive=blocked_graphics_renderer,
        on_prepare=lambda: markers.append("prepare"), on_place=place)
    try:
        with _spy_outer_lease(events) as snapshot:
            for patcher in patches:
                patcher.start()
            try:
                result = rb.rebuild_reels_in_project(
                    str(project_dir), organise=False)
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
        assert renderer_entered == [True]
        assert any(name.startswith("place Reel 01")
                   for name in place_snapshot), place_snapshot
        assert markers == ["graphics", "prepare", "place"], markers
        assert "Reel 01" in project.names(), project.names()

        for proc in place_reader:
            _, stderr = proc.communicate(timeout=5)
            assert proc.returncode == 0, stderr
        assert json.loads(
            place_marker.read_text(encoding="utf-8"))["state"] == "acquired"

        rows = [json.loads(line) for line in
                perf_path.read_text(encoding="utf-8").splitlines()]
        phase_rows = [row for row in rows if row.get("kind") == "span"
                      and row.get("layer", "").startswith("reel_build_")]
        by_layer = {}
        for row in phase_rows:
            by_layer.setdefault(row["layer"], []).append(row)
        assert {"reel_build_survey", "reel_build_prepare",
                "reel_build_place", "reel_build_verify",
                "reel_build_promote"} <= set(by_layer)
        prep = by_layer["reel_build_prepare"][0]
        placement = by_layer["reel_build_place"][0]
        assert prep["wall_s"] >= 0
        assert prep["output_digest"] == placement["output_digest"]
        assert len(prep["output_digest"]["derivation"]) == 64
        from library.tools import perf_ledger as _perf
        edit_rows = [row for row in rows
                     if row.get("layer") == "reel_edit"]
        assert len(edit_rows) == 1
        edit_metrics = edit_rows[0]["measurements"]
        assert set(edit_metrics) == set(_perf.REEL_EDIT_METRICS)
        assert edit_metrics["attempts"] == 2
        assert edit_metrics["full_rebuilds"] == 1
        assert edit_metrics["items_touched"] >= 0
        assert edit_metrics["lease_queue_wait_s"] >= 0
        assert edit_metrics["lease_hold_s"] > 0
        holds = [row for row in rows if row.get("layer") == "resolve_hold"
                 and row.get("capability") == "reel.build"]

        def overlap_seconds(phase, hold):
            phase_start = float(phase["started_at"])
            phase_end = phase_start + float(phase["wall_s"])
            hold_start = float(hold["started_at"])
            hold_end = hold_start + float(hold.get("held_s", hold["wall_s"]))
            return max(0.0, min(phase_end, hold_end)
                       - max(phase_start, hold_start))

        # Only an EXCLUSIVE hold can move the cursor, so only one can
        # cover prepare: the shared acceptance readers this test stages
        # may land inside any phase's window (a saturated machine starts
        # their process late), and their sub-millisecond hold then reads
        # as a 1ms overlap - which is not a placement lease held across
        # prepare. A row without the key fails closed as exclusive.
        assert all(overlap_seconds(prep, hold) == 0
                   for hold in holds if hold.get("exclusive", True))
        assert any(overlap_seconds(placement, hold) > 0 for hold in holds)
        assert result["timelines_built"]
    finally:
        for proc in place_reader:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)


def test_synthetic_120_second_preparation_has_zero_lease_hold():
    """A profiled 120s preparation is outside Resolve, then placement holds."""
    from library.tools import perf_ledger

    rows = [
        {"kind": "capability", "run_id": "fixture",
         "capability": "reel.build", "started_at": 1000.0,
         "wall_s": 121.0, "cpu_s": 0.0},
        {"kind": "span", "run_id": "fixture",
         "capability": "reel.build", "layer": "reel_build_prepare",
         "started_at": 1000.0, "wall_s": 120.0},
        {"kind": "span", "run_id": "fixture",
         "capability": "reel.build", "layer": "resolve_hold",
         "started_at": 1120.0, "wall_s": 1.0, "held_s": 1.0,
         "exclusive": True, "purpose": "place Reel 01"},
    ]
    report = perf_ledger.profile(rows, "fixture")
    prepare = next(row for row in report["lines"]
                   if row["name"] == "reel_build_prepare")
    assert prepare["wall_s"] == 120.0
    assert report["resolve"]["exclusive_held_s"] == 1.0
    prep_interval = (1000.0, 1120.0)
    lease_interval = (1120.0, 1121.0)
    assert max(0.0, min(prep_interval[1], lease_interval[1])
               - max(prep_interval[0], lease_interval[0])) == 0.0



# ── The second interleaving: the gap between build and verify ───────
#
# 2026-10-05, after the whole-build hold above landed: a 6-reel
# `build-reels` run's `rebuild_reels_in_project` released its hold at
# 20:24:01.307 when `reel.build` returned, and a caption smoke queued
# on `resolve_lease("master caption live smoke", capability=
# "render.build", phase="apply", timeline=<master>)` was granted the
# instance 2ms later. `reel.ask` and `reel.verify` - the gate AND the
# promotion - are later capabilities of the same run, and they ran
# with only their own per-section holds: the verify read the master
# and all six stagings changed under it ("timelines were MODIFIED
# during verification", the cursor on Reel 01 where its first read had
# found the master) and nothing was promoted. The run now permits this
# gap; the verification refusal is the recovery behavior that keeps the
# ungraded staging from promotion.
#
# Both processes share one file-backed fake Resolve (current timeline
# and timeline inventory), so a write by one is a change the other's
# gate reads - the property the live run lost.

_RUN_WORKER = textwrap.dedent("""
    import json
    import os
    import sys
    import time
    from contextlib import contextmanager, nullcontext
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    role, lock_dir, log_path, state_path, project_dir = sys.argv[1:6]
    os.environ["PIPELINE_RESOLVE_LOCK_DIR"] = lock_dir
    os.environ["PIPELINE_RESOLVE_LEASE_TIMEOUT"] = "60"
    sys.path.insert(0, __REPO__)
    sys.modules["DaVinciResolveScript"] = MagicMock()

    from library.tools import resolve_lock

    @contextmanager
    def no_broker_turn(*args, **kwargs):
        yield None
    resolve_lock._broker_turn = no_broker_turn

    MASTER = __MASTER__
    STAGING = "Reel 01 (rebuild staging)"

    def log(event):
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write("%.3f %s %s\\n" % (time.time(), role, event))

    def has_event(expected):
        try:
            with open(log_path, encoding="utf-8") as handle:
                return any(line.rstrip().endswith(" " + expected)
                           for line in handle)
        except FileNotFoundError:
            return False

    def read_state():
        with open(state_path, encoding="utf-8") as handle:
            return json.load(handle)

    def write_state(state):
        temporary = state_path + ".%d" % os.getpid()
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(temporary, state_path)

    def set_current(name):
        state = read_state()
        state["current"] = name
        write_state(state)

    if role == "smoke":
        # The smoke's own lease, verbatim in shape: a timeline-scoped
        # exclusive grant that duplicates the master, moves the cursor
        # onto the copy and deletes it again.
        log("write-attempt")
        with resolve_lock.resolve_lease(
                "master caption live smoke", capability="render.build",
                phase="apply", project="Mock Project", timeline=MASTER):
            log("write-start")
            state = read_state()
            state["timelines"].append(MASTER + " (caption smoke)")
            state["current"] = MASTER + " (caption smoke)"
            write_state(state)
            time.sleep(0.3)
            state = read_state()
            state["timelines"].remove(MASTER + " (caption smoke)")
            state["current"] = "Reel 01"
            write_state(state)
            log("write-end")
        sys.exit(0)

    # role == "build": the real `run_reels.run` loop, whose capabilities
    # take the holds their real bodies take.
    def build(project_folder, **_):
        @resolve_lock.under_lease("build reels", exclusive=True)
        def body():
            log("build-start")
            state = read_state()
            state["timelines"].append(STAGING)
            state["current"] = STAGING
            write_state(state)
            # Hold placement open until the other process is ready to
            # contend. Otherwise a loaded machine can schedule this
            # process through the short hold before the smoke starts.
            deadline = time.time() + 10
            while not has_event("write-attempt"):
                if time.time() >= deadline:
                    raise RuntimeError("smoke never attempted its lease")
                time.sleep(0.01)
        body()
        return {}

    def ask(project_folder, **_):
        # The real ask derives for seconds before its read (2.8s
        # measured), holding nothing. Wait for the queued writer to finish
        # so the test always observes its write in the build/verify gap.
        deadline = time.time() + 10
        while not has_event("write-end"):
            if time.time() >= deadline:
                raise RuntimeError("smoke did not finish its write")
            time.sleep(0.01)
        with resolve_lock.resolve_lease(
                "read master timeline for reel asks", exclusive=False):
            read_state()
        return {}

    def verify(project_folder, **_):
        with resolve_lock.resolve_lease(
                "verify reel conformance", exclusive=False):
            before = read_state()
            time.sleep(1.0)
            after = read_state()
        if before != after or before["current"] != STAGING:
            log("verify-modified")
            raise RuntimeError(
                "timelines were MODIFIED during verification: the build "
                "left %r, the gate read %r then %r" % (
                    STAGING, before, after))
        with resolve_lock.resolve_lease("promote staged reels",
                                        exclusive=True):
            state = read_state()
            state["timelines"][state["timelines"].index(STAGING)] = "Reel 01"
            state["current"] = "Reel 01"
            write_state(state)
        log("verify-end")
        return {}

    bodies = {"reel.build": build, "reel.ask": ask, "reel.verify": verify}

    class FakeOperation:
        def __init__(self, name):
            self.name = name
            self.caller_supplied = name not in bodies

        def execute(self, project_folder, **kwargs):
            payload = bodies[self.name](project_folder, **kwargs)
            return SimpleNamespace(refused=False, error="",
                                   status="completed", payload=payload)

    from library.processes.reels import run_reels
    from library.tools import operations, project_format, provenance

    args = SimpleNamespace(skip_captions=False, only_reel=None,
                           name_suffix="", allow_drop=None,
                           supersede=None, retain=None)
    with patch.object(project_format, "ensure_project_format"), \\
            patch.object(operations, "get", FakeOperation), \\
            patch.object(provenance, "observing_operation",
                         lambda *a, **k: nullcontext()), \\
            patch.object(run_reels, "record_project_output"), \\
            patch.object(run_reels, "commit_run_tail"):
        code = run_reels.run(project_dir, args)
    log("run-end %d" % code)
    sys.exit(code)
""").replace("__REPO__", repr(str(REPO_ROOT))).replace(
    "__MASTER__", repr(MASTER))


def _start_run_worker(role, lock_dir, log, state, project_dir):
    env = dict(os.environ)
    env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
    env.pop(resolve_lock.INHERIT_ENV, None)
    env.pop(resolve_lock.INHERIT_MODE_ENV, None)
    return subprocess.Popen(
        [sys.executable, "-c", _RUN_WORKER, role, str(lock_dir), str(log),
         str(state), str(project_dir)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")


def _role_events(log: Path) -> list:
    if not log.exists():
        return []
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        stamp, role, event = line.split(" ", 2)
        rows.append((float(stamp), role, event))
    return rows


@pytest.mark.serial(
    reason="Two real processes contend for one flock; keep child "
           "startup out of xdist saturation.")
def test_writer_in_capability_gap_causes_safe_verify_refusal(
        tmp_path):
    """A writer may use the gap, but stale state cannot pass verification.

    The build-reels run starts first; the smoke starts once the build
    is inside its placement hold, so it queues until placement ends.
    It then moves the cursor before verify. Verify must refuse and leave
    the staging intact rather than promote content it did not grade.
    """
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    log = tmp_path / "run.log"
    state = tmp_path / "resolve_state.json"
    state.write_text(json.dumps({"current": MASTER,
                                 "timelines": [MASTER, "Reel 11"]}),
                     encoding="utf-8")
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    build = _start_run_worker("build", lock_dir, log, state, project_dir)
    try:
        deadline = time.time() + 60
        while not any(event == "build-start"
                      for _, _, event in _role_events(log)):
            assert time.time() < deadline, _role_events(log)
            assert build.poll() is None, build.communicate()
            time.sleep(0.05)
        smoke = _start_run_worker("smoke", lock_dir, log, state, project_dir)
        try:
            out_s, err_s = smoke.communicate(timeout=120)
        finally:
            if smoke.poll() is None:
                smoke.kill()
                smoke.communicate(timeout=10)
        out_b, err_b = build.communicate(timeout=120)
    finally:
        if build.poll() is None:
            build.kill()
            build.communicate(timeout=10)

    events = _role_events(log)
    assert smoke.returncode == 0, (out_s, err_s)
    assert build.returncode != 0, (out_b, err_b)
    assert any(role == "build" and event == "verify-modified"
               for _, role, event in events), events
    final_state = json.loads(state.read_text(encoding="utf-8"))
    assert "Reel 01 (rebuild staging)" in final_state["timelines"]
    assert "Reel 01" not in final_state["timelines"]


def test_placement_revalidation_accepts_fresh_wrappers_of_the_same_project():
    """Bridge proxies are per-call: the same open project must still place.

    Measured live 2026-10-17: three GetCurrentProject() calls returned
    three distinct wrappers for one open project, so the old `is`
    comparison refused every build. The exact-name lookup passes while
    a genuinely different open project still refuses inside it.
    Nothing here reaches Resolve.
    """
    from library.tools.timeline_ingest import (
        TimelineIngestError, resolve_project_exactly)

    class _Project:
        def __init__(self, name):
            self._name = name

        def GetName(self):
            return self._name

    class _Manager:
        def __init__(self, name):
            self._name = name

        def GetProjectListInCurrentFolder(self):
            return [self._name]

        def GetCurrentProject(self):
            return _Project(self._name)

    recorded = _Project("Ren Scratch - exec deep")
    current = resolve_project_exactly(
        _Manager("Ren Scratch - exec deep"), "Ren Scratch - exec deep")
    assert current.GetName() == recorded.GetName() == "Ren Scratch - exec deep"
    assert current is not recorded

    with pytest.raises(TimelineIngestError):
        resolve_project_exactly(
            _Manager("Podcast (field test)"), "Ren Scratch - exec deep")


def test_reacquire_current_project_and_timeline_uses_live_handles():
    from library.tools.reel_build import (
        _reacquire_current_project_timeline,
    )

    class _Timeline:
        def GetName(self):
            return "GEO Podcast - Synced"

        def GetUniqueId(self):
            return "master-id"

    class _Project:
        def GetName(self):
            return "Ren Scratch - exec deep"

        def GetTimelineCount(self):
            return 1

        def GetTimelineByIndex(self, index):
            return _Timeline() if index == 1 else None

    class _Manager:
        def GetProjectListInCurrentFolder(self):
            return ["Ren Scratch - exec deep"]

        def GetCurrentProject(self):
            return _Project()

    class _Resolve:
        def GetProjectManager(self):
            return _Manager()

    project, timeline = _reacquire_current_project_timeline(
        _Resolve(), "Ren Scratch - exec deep", "GEO Podcast - Synced",
        "master-id")
    assert project.GetName() == "Ren Scratch - exec deep"
    assert timeline.GetUniqueId() == "master-id"


def test_reacquire_current_project_refuses_a_different_active_project():
    from library.tools.reel_build import (
        ReelBuildError, _reacquire_current_project_timeline,
    )

    class _Project:
        def GetName(self):
            return "Ren Qualification"

    class _Manager:
        def GetProjectListInCurrentFolder(self):
            return ["Ren Qualification"]

        def GetCurrentProject(self):
            return _Project()

    class _Resolve:
        def GetProjectManager(self):
            return _Manager()

    with pytest.raises(ReelBuildError, match="active project changed"):
        _reacquire_current_project_timeline(
            _Resolve(), "Ren Scratch - exec deep", "GEO Podcast - Synced",
            "master-id")
