"""5.01 sees colour, stills and the camera match - rung 4c.

The colourist step used to see mean luma only and was shown no frames,
so a cast between two angles covering one set had no number and no
picture. The bridge now measures per-shot chroma
(`library/tools/shot_colour.py`), draws one still per shot, asks the
still router what it sees (`library/tools/still_vision.py`), and derives
the measured camera-match proposal (`library/tools/camera_match.py`).

The vision half is stubbed here: what the router answers is the
router's own business (tests/unit/resolve/test_still_vision.py). What this step owns
is that the stills exist where the block says, the notes record WHO
answered, and the match proposal is stated, never applied.
"""
import json
import pathlib
import shutil
import subprocess
from unittest.mock import patch

import pytest

from library.steps.step_5_01_color_grade import bridge as bridge_501
from library.steps.step_5_01_color_grade.grade import (
    collect_entries,
    measure_clips,
)

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _clip(path, color):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=160x120:d=12",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


def _inputs(project, warm, cool):
    return {
        "project_folder": str(project),
        "a_roll_assignments": [
            {"clip_id": "clip_warm", "timeline_start": 0.0,
             "source_file": str(warm)},
            {"clip_id": "clip_cool", "timeline_start": 5.0,
             "source_file": str(cool)},
        ],
        "b_roll_assignments": [],
        "creative_direction": {},
        "clip_catalog": [],
        "semantic_analysis_documents": [],
    }


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_rows_carry_the_chroma_half(tmp_path):
    """Each measured row carries per-channel means and the neutral
    balance - the numbers the cast-match grounds in."""
    warm = tmp_path / "warm.mp4"
    cool = tmp_path / "cool.mp4"
    _clip(warm, "0x787673")
    _clip(cool, "0x74767A")
    data = _inputs(tmp_path / "proj", warm, cool)
    rows = bridge_501.attach_sources(
        measure_clips(collect_entries(data), ""),
        collect_entries(data), "")
    by_clip = {row["clip_id"]: row for row in rows}
    assert by_clip["clip_warm"]["neutral_rb"] is not None
    assert by_clip["clip_warm"]["neutral_rb"] > 1.0
    assert by_clip["clip_cool"]["neutral_rb"] is not None
    assert by_clip["clip_cool"]["neutral_rb"] < 1.0
    assert by_clip["clip_warm"]["mean_rgb"][0] > \
        by_clip["clip_warm"]["mean_rgb"][2]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_bridge_draws_stills_and_derives_the_match(tmp_path, monkeypatch):
    """Stills land where the block says, the notes record who answered,
    and the match proposes one slope - stated, never applied."""
    monkeypatch.delenv("PIPELINE_HOST_HARNESS", raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    warm = tmp_path / "warm.mp4"
    cool = tmp_path / "cool.mp4"
    _clip(warm, "0x787673")
    _clip(cool, "0x74767A")
    data = _inputs(project, warm, cool)
    entries = collect_entries(data)
    rows = bridge_501.attach_sources(
        measure_clips(entries, str(project)), entries, str(project))

    block, paths = bridge_501.draw_shot_stills(rows, str(project))
    assert len(paths) == 2
    assert "clip_warm" in block and "clip_cool" in block
    for clip in ("clip_warm", "clip_cool"):
        still = project / "pipeline_output" / "steps" / "5_01_color_grade" \
            / "shot_stills" / f"{clip}__shot.jpg"
        assert still.stat().st_size > 0

    with patch.object(bridge_501.still_router, "inspect_stills",
                      return_value="warm cast on the wall") as seen:
        notes = bridge_501.observe_shot_stills(paths, str(project))
    seen.assert_called_once()
    assert notes["text"] == "warm cast on the wall"
    assert notes["observed_by"] != "none"

    match = bridge_501.derive_camera_match(rows)
    assert match["reference"] is not None
    assert len(match["matches"]) == 1
    slope = match["matches"][0]["slope"]
    assert len(slope) == 3
    after = match["matches"][0]["neutral_after"]
    assert after["rb"] == pytest.approx(
        match["reference"] and match["matches"][0]
        ["reference_neutral"]["rb"], abs=1e-3)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_vision_failure_is_a_stated_absence(tmp_path):
    """A router that raises does not fail the colour step: the absence
    is recorded with the reason, and the stills stay in the block for
    the driver to open."""
    project = tmp_path / "proj"
    project.mkdir()
    warm = tmp_path / "warm.mp4"
    _clip(warm, "0x787673")
    data = _inputs(project, warm, tmp_path / "missing.mp4")
    entries = collect_entries(data)
    rows = bridge_501.attach_sources(
        measure_clips(entries, str(project)), entries, str(project))
    block, paths = bridge_501.draw_shot_stills(rows, str(project))
    assert "NOT DRAWN" in block

    with patch.object(bridge_501.still_router, "inspect_stills",
                      side_effect=RuntimeError("host silent")):
        notes = bridge_501.observe_shot_stills(paths, str(project))
    assert notes["observed_by"] == "none"
    assert "host silent" in notes["reason"]
    assert notes["text"] == ""


def test_empty_inputs_emit_only_declared_keys():
    """The gate-test path: no clips, no project, no vision call - every
    emitted key is in the manifest, so validate_step_output stays
    quiet (tests/test_bridge_outputs_are_declared.py)."""
    manifest = json.loads(
        pathlib.Path(bridge_501.__file__).parent.joinpath(
            "manifest.json").read_text(encoding="utf-8"))
    declared = {o["name"] for o in manifest["interface"]["outputs"]}
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):
        rows = measure_clips([], "")
    assert rows == []
    match = bridge_501.derive_camera_match(rows)
    assert match["matches"] == []
    notes = bridge_501.observe_shot_stills([], "")
    assert notes["observed_by"] == "none"
    for key in ("clip_exposure", "cut_adjacency", "declared_look",
                "shot_stills", "still_colour_notes", "camera_match",
                "grade_terms_legend"):
        assert key in declared, f"bridge emits {key!r} no manifest declares"
