"""A duplicate take is analyzed once, not twice.

The defect this closes, measured on a fixture:

* Two copies of one take under different names (a card copied to a
  backup, or the same take imported twice) are the same bytes.  The
  semantic analysis skipped a clip only when its file STEM already
  had a profile, so identical bytes under different names each cost
  a full vision pass - the analysis was paid twice for identical
  footage.

The skip is now keyed by content fingerprint: the second copy is
recognized as the same take and never reaches the vision pipeline.
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def test_duplicate_footage_is_analyzed_once(tmp_path, monkeypatch):
    from library.steps.step_1_03_semantic_analysis import step as sem_step

    first = tmp_path / "take_a.mp4"
    first.write_bytes(b"fixture-bytes")
    second = tmp_path / "take_b.mp4"
    second.write_bytes(b"fixture-bytes")

    analyzed = []

    def fake_run(cmd, *args, **kwargs):
        if "--clip" in cmd:
            analyzed.extend(cmd[cmd.index("--clip") + 1:])
        return 0

    monkeypatch.setattr(sem_step, "_run_clip_vision", fake_run)
    sem_step.analyse_semantics(
        raw_footage_files=[str(first), str(second)],
        project_folder=str(tmp_path),
    )
    assert len(analyzed) == 1


def test_different_takes_are_each_analyzed(tmp_path, monkeypatch):
    """The skip is by content, not by name: two different takes are
    both analyzed."""
    from library.steps.step_1_03_semantic_analysis import step as sem_step

    first = tmp_path / "take_a.mp4"
    first.write_bytes(b"fixture-bytes-a")
    second = tmp_path / "take_b.mp4"
    second.write_bytes(b"fixture-bytes-b-different")

    analyzed = []

    def fake_run(cmd, *args, **kwargs):
        if "--clip" in cmd:
            analyzed.extend(cmd[cmd.index("--clip") + 1:])
        return 0

    monkeypatch.setattr(sem_step, "_run_clip_vision", fake_run)
    sem_step.analyse_semantics(
        raw_footage_files=[str(first), str(second)],
        project_folder=str(tmp_path),
    )
    assert len(analyzed) == 2


def test_duplicate_profile_resolves_under_both_clip_ids(tmp_path, monkeypatch):
    """Skipping the duplicate's vision analysis must not leave its clip_id
    with NO semantic profile.

    The defect the PR 1863 review named: the skip keyed by content
    fingerprint meant the duplicate never reached the vision pipeline, so
    every consumer looking the clip up by its own clip_id (picture plan,
    broll, vfx detection, project graph) saw an unanalyzed clip. The
    duplicate now gets the original's analysis, so lookups by either id
    resolve to the same analysis - while the vision pass is still paid
    only once.
    """
    from library.steps.step_1_03_semantic_analysis import step as sem_step
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.semantic_index import build_semantic_lookup

    first = tmp_path / "take_a.mp4"
    first.write_bytes(b"fixture-bytes")
    second = tmp_path / "take_b.mp4"
    second.write_bytes(b"fixture-bytes")

    layout = ProjectLayout(str(tmp_path))
    analysis_dir = str(layout.write_dir(
        Area.VISION_ANALYSIS, step="semantic_analysis"))

    analyzed = []

    def fake_run(cmd, *args, **kwargs):
        if "--clip" in cmd:
            paths = cmd[cmd.index("--clip") + 1:]
            analyzed.extend(paths)
            for path in paths:
                stem = os.path.splitext(os.path.basename(path))[0]
                profile = {
                    "file_path": os.path.realpath(path),
                    "assessment": {"content_type": "interview"},
                    "marker": "original-analysis",
                }
                with open(os.path.join(
                        analysis_dir,
                        f"clip_profile_{stem}_v3.json"),
                        "w", encoding="utf-8") as handle:
                    json.dump(profile, handle)
        return 0

    monkeypatch.setattr(sem_step, "_run_clip_vision", fake_run)
    result = sem_step.analyse_semantics(
        raw_footage_files=[str(first), str(second)],
        project_folder=str(tmp_path),
    )
    assert len(analyzed) == 1

    catalog = [
        {"clip_id": "clip_001", "path": str(first)},
        {"clip_id": "clip_002", "path": str(second)},
    ]
    lookup = build_semantic_lookup(
        result["semantic_analysis_documents"], catalog)
    assert lookup["clip_001"]["marker"] == "original-analysis"
    assert lookup["clip_002"]["marker"] == "original-analysis"
