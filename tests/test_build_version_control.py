"""Pin the per-project version-control contract (AGENTS.md 3).

The allow-list test names a binary directory the module never names:
if a future binary area is added to the engine, this is the test that
catches it being swallowed into the repo.
"""

import json
import subprocess
import sys

import pytest

from library.tools import build_version_control as bvc


def _git(project, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(project), capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _write(project, rel, content="x"):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


TEXT_FILES = [
    "project.yaml",
    "pipeline_data.json",
    "pipeline_run.json",
    "external/captain_edits.json",
    "context/look.md",
    "profiles/tight.json",
    "pipeline_output/steps/4_05_render_subtitles/output.json",
    "pipeline_output/steps/4_05_render_subtitles/summary.md",
    "pipeline_output/steps/5_04_compile_manifest/assembly_manifest.json",
    "pipeline_output/steps/6_01_render/fusion_comps/a.comp",
    "pipeline_output/steps/6_01_render/otio/cut.build.otio",
    "pipeline_output/steps/6_01_render/otio/cut.timeline.json",
    "pipeline_output/steps/6_01_render/otio/cut.BUILD-RECORD.md",
    "pipeline_output/llm_requests/r.json",
    "pipeline_output/llm_responses/r.json",
    "pipeline_output/gates/g.json",
    "pipeline_output/review/channel.json",
    "pipeline_output/review/reel_variants.json",
    "marker_feedback/pull.json",
    "timeline_captures/reel13-live-20260911/reel13_live_state.json",
    "pipeline_output/provenance/p.json",
    "pipeline_output/quarantine/mark_20240101.json",
    "pipeline_output/quarantine/mark_20240101.md",
    "pipeline_output/quarantine/sweep_20240101_manifest.md",
    "pipeline_output/RUN-TRACEBACK.md",
    "pipeline_output/ARTIFACTS.md",
]

# Binaries that must NEVER land in the repo - including
# 9_99_newthing/, a step directory this module never names, which is
# the regression shape for "the next new binary directory somebody
# adds".
BINARY_DECOYS = [
    "pipeline_output/steps/4_05_render_subtitles/seg_0001.mov",
    "pipeline_output/steps/9_99_newthing/big.bin",
    "pipeline_output/scratch/work.tmp",
    "pipeline_output/quarantine/moved_aside.mov",
    "pipeline_output/thumbnails/thumb.jpg",
    "pipeline_output/steps/7_01_build_reels/frame_overlays/f1.png",
    "exports/final.mp4",
    "audio/a.wav",
    "subtitle_overlays/o.mov",
]


def test_allow_list_versions_text_only(tmp_path):
    for rel in TEXT_FILES:
        _write(tmp_path, rel, f"text of {rel}\n")
    for rel in BINARY_DECOYS:
        _write(tmp_path, rel, b"\x00\x01binary\xff" * 100)
    assert bvc.init_project_repo(str(tmp_path))["initialised"] is True
    result = bvc.commit_build(str(tmp_path), message="first commit\n")
    assert result["committed"] is True
    tracked = set(_git(tmp_path, "ls-files").splitlines())
    for rel in TEXT_FILES:
        assert rel in tracked, f"allow-listed {rel} missing from the repo"
    for rel in BINARY_DECOYS:
        assert rel not in tracked, f"binary {rel} swallowed into the repo"
    assert ".gitignore" in tracked


def test_commit_message_renders_what_the_run_knows(tmp_path):
    _write(tmp_path, "pipeline_run.json", json.dumps({
        "mode": "single-step judge_reels, full-auto agy, no breakpoints",
        "argv": ["--project", "/p", "--step", "judge_reels"],
        "profile": {"name": ""},
        "status": "partial",
        "steps_to_run": ["judge_reels"],
        "last_completed_step": "judge_reels",
        "restart": {"basis": "after_failure", "previous_status": "failed"},
    }))
    _write(tmp_path, "pipeline_data.json", json.dumps({
        "edit_completed": {"judge_reels": {}, "select_reels": {}},
        "failed_steps": [],
    }))
    msg = bvc.render_commit_message(str(tmp_path))
    assert msg.splitlines()[0] == "build judge_reels (partial)"
    assert "mode: single-step judge_reels" in msg
    assert "argv: --project /p --step judge_reels" in msg
    assert "steps run: judge_reels" in msg
    assert "completed (2): judge_reels, select_reels" in msg
    assert "restart: after_failure (after failed)" in msg
    # No profile name declared, no provenance record: both lines are
    # omitted rather than invented.
    assert "profile:" not in msg
    assert "\nrun:" not in msg


def test_noop_build_produces_no_commit(tmp_path):
    bvc.init_project_repo(str(tmp_path))
    _write(tmp_path, "project.yaml", "name: demo\n")
    first = bvc.commit_build(str(tmp_path), message="first\n")
    assert first["committed"] is True
    second = bvc.commit_build(str(tmp_path))
    assert second == {"committed": False, "reason": "clean"}
    assert _git(tmp_path, "rev-list", "--count", "HEAD").strip() == "1"


def test_committed_bytes_are_verbatim(tmp_path):
    # The absolute-path decision: machine-local paths are committed
    # exactly as the pipeline wrote them, so a checkout rebuilds.
    content = '{"source_file": "/Users/prajwal/footage/clip_001.mp4"}\n'
    bvc.init_project_repo(str(tmp_path))
    _write(tmp_path, "pipeline_data.json", content)
    assert bvc.commit_build(str(tmp_path), message="paths\n")["committed"]
    shown = _git(tmp_path, "show", "HEAD:pipeline_data.json")
    assert shown == content


def test_build_record_names_the_blind_spot(tmp_path):
    written = bvc.write_build_record(
        str(tmp_path), "cut_01", "OTIO-body", {"schema_version": "1.0"})
    record = (tmp_path / f"pipeline_output/steps/6_01_render/otio/"
              "cut_01.BUILD-RECORD.md").read_text(encoding="utf-8")
    assert "Fusion comps empty" in record
    assert "CDL grades absent" in record
    assert "fusion_comps" in record
    assert written["otio"].endswith("cut_01.build.otio")
    assert written["timeline_json"].endswith("cut_01.timeline.json")


def test_record_without_repo_declines(tmp_path):
    class _Timeline:
        def Export(self, path, flag):
            raise AssertionError("must not reach Resolve without a repo")

    report = bvc.record_finished_timeline(
        object(), _Timeline(), str(tmp_path), "cut_01")
    assert report == {"committed": False, "reason": "no-repo", "files": []}


def test_record_without_project_folder_declines():
    report = bvc.record_finished_timeline(object(), object(), "", "cut_01")
    assert report["committed"] is False
    assert "project_folder" in report["reason"]


# ── The reels promotion record ─────────────────────────────────────
#
# The 6.01 hook never fired for reels, so no reel build committed its
# baseline (measured 2026-09-11). `record_reel_promotion` closes that:
# one snapshot per promoted timeline beside the declaration, then the
# commit. These pin the decline paths and the snapshot-then-commit
# shape; a Resolve that is absent or on another project records the
# reason instead of raising.


class _FakeTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name


class _FakeProject:
    def __init__(self, name, timelines):
        self._name = name
        self._timelines = list(timelines)
        self._current = self._timelines[0]

    def GetName(self):
        return self._name

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]

    def GetCurrentTimeline(self):
        return self._current

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


class _FakeManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class _FakeResolve:
    def __init__(self, project):
        self._project = project

    def GetProjectManager(self):
        return _FakeManager(self._project)


class _FakeDvr:
    def __init__(self, resolve):
        self._resolve = resolve

    def scriptapp(self, name):
        return self._resolve


def _promotion_project(name="Podcast (field test)"):
    project = _FakeProject(name, [
        _FakeTimeline("GEO Podcast - Synced"),
        _FakeTimeline("Reel 28 - the-nail-salon-query-google-cant-answer"),
    ])
    return project


def test_reel_promotion_without_repo_declines(tmp_path):
    report = bvc.record_reel_promotion(
        str(tmp_path), "Podcast (field test)", ["Reel 28"])
    assert report["committed"] is False
    assert report["reason"] == "no-repo"


def test_reel_promotion_without_names_declines(tmp_path):
    bvc.init_project_repo(str(tmp_path))
    report = bvc.record_reel_promotion(
        str(tmp_path), "Podcast (field test)", [])
    assert report["committed"] is False
    assert "no promoted timelines" in report["reason"]


def test_reel_promotion_on_wrong_project_records_and_never_raises(
        tmp_path, monkeypatch):
    bvc.init_project_repo(str(tmp_path))
    project = _promotion_project(name="Something else")
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        _FakeDvr(_FakeResolve(project)))
    report = bvc.record_reel_promotion(
        str(tmp_path), "Podcast (field test)", ["Reel 28"])
    assert report["committed"] is False
    assert "Something else" in report["reason"]
    assert report["snapshots"] == []


def test_reel_promotion_snapshots_each_timeline_then_commits(
        tmp_path, monkeypatch):
    bvc.init_project_repo(str(tmp_path))
    project = _promotion_project()
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        _FakeDvr(_FakeResolve(project)))
    import library.tools.timeline_serializer as ser
    seen = []

    def _fake_serialize(resolve_mock=None):
        seen.append(resolve_mock.GetProjectManager()
                    .GetCurrentProject().GetCurrentTimeline().GetName())
        return {"schema_version": "1.0", "timeline": seen[-1]}

    monkeypatch.setattr(ser, "serialize_timeline_state", _fake_serialize)
    report = bvc.record_reel_promotion(
        str(tmp_path), "Podcast (field test)",
        ["Reel 28 - the-nail-salon-query-google-cant-answer", "Nope"])
    assert report["committed"] is True
    assert report["missing"] == ["Nope"]
    assert seen == ["Reel 28 - the-nail-salon-query-google-cant-answer"]
    snapshot = (tmp_path / "pipeline_output" / "review" /
                "Reel_28_-_the-nail-salon-query-google-cant-answer"
                ".timeline.json")
    assert snapshot.is_file()
    assert json.loads(snapshot.read_text(encoding="utf-8"))["timeline"] == \
        "Reel 28 - the-nail-salon-query-google-cant-answer"
    # The commit holds the snapshot, and the open timeline is restored.
    assert "pipeline_output/review/Reel_28" in "\n".join(report["files"])
    assert project.GetCurrentTimeline().GetName() == "GEO Podcast - Synced"
    log = _git(tmp_path, "log", "--oneline")
    assert log.strip() != ""
