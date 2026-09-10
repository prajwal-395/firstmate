"""Pin the per-project version-control contract (AGENTS.md 3).

The allow-list test names a binary directory the module never names:
if a future binary area is added to the engine, this is the test that
catches it being swallowed into the repo.
"""

import json
import subprocess

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
