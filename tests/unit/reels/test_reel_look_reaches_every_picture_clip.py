"""The DECLARED look reaches every picture clip, and the captain's hand
edit becomes a version.

Two defects, two tests:

1. A picture clip that carries a comp built on the WRONG canvas plays
   flatter and cooler than the clip beside it. Reel 26's closing shot
   carried a Fusion comp whose canvas was the SOURCE frame
   (3840x2160) while every other comp in the reel was authored on the
   DELIVERY frame (1080x1920) - so its vignette and glow landed on a
   different picture. The look is a DECLARATION, and this applies the
   declaration to the clips that need it, by the build's own comp pass.

2. The captain's hand edit - the corner logo's timing, corrected by
   hand - was nowhere Ren knew about, so "the state the captain
   accepted" was a story rather than a version. An ADOPTED version
   records it: the live generation by its Resolve identity, the
   immutable archive of that generation and the hashed asset manifest.
"""

from __future__ import annotations

import json

from library.tools import reel_look
from library.tools.versions import reel_versions


def test_the_look_manifest_describes_the_window_the_item_plays():
    """A comp lands on the item it names, by the window it plays."""
    clip = {
        "record_in": 793,
        "record_out": 1013,
        "source_file": "/media/LCATL0013.MXF",
        "source_in_frame": 11537,
        "source_out_frame": 11758,
        "source_fps": 23.976,
    }
    manifest = reel_look._look_manifest(
        "/project", "Reel 26 - x", "a0c4856c", [(2, clip)], 23.976,
        {"grade_contrast": 0.12, "vignette": True})

    spec = manifest["tracks"]["V2"]["clips"][0]
    assert spec["source_file"] == "/media/LCATL0013.MXF"
    # The comp pass joins a planned clip to a placed item by the source
    # window, so the window has to be the one the item PLAYS.
    assert round(spec["source_in"] * 23.976) == 11537
    assert round(spec["source_out"] * 23.976) == 11758
    assert manifest["fusion_effects"]["per_clip"][spec["label"]] == {
        "grade_contrast": 0.12, "vignette": True}


def test_the_look_manifest_names_the_resolve_identity():
    """A name cannot say which generation; the id can."""
    clip = {"record_in": 0, "record_out": 10,
            "source_file": "/media/a.mxf", "source_in_frame": 0,
            "source_out_frame": 10, "source_fps": 24.0}

    manifest = reel_look._look_manifest(
        "/project", "Reel 26 - x", "a0c4856c", [(1, clip)], 24.0,
        {"vignette": True})

    assert manifest["expected_timeline_id"] == "a0c4856c"


def test_an_adopted_hand_edit_is_a_version_and_not_an_act(tmp_path):
    """The captain's hand is recorded, and `ren undo` does not reverse it."""
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)

    entry = reel_versions.record(
        str(project), "Reel 26 - x", kind=reel_versions.KIND_ADOPT,
        rows={}, reason="the corner logo timing was corrected by hand",
        by="captain", timeline_id="a0c4856c")

    assert entry["version"] == 1
    assert entry["kind"] == reel_versions.KIND_ADOPT
    assert entry["reason"] == "the corner logo timing was corrected by hand"
    assert entry["timeline_id"] == "a0c4856c"
    # An ACT is something `ren undo` reverses. The captain's hand is not.
    assert reel_versions.KIND_ADOPT not in reel_versions.ACTS


def test_the_adopted_version_is_written_to_the_ledger(tmp_path):
    """It is a version in the ledger, not a note beside it."""
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)

    reel_versions.record(
        str(project), "Reel 26 - x", kind=reel_versions.KIND_ADOPT,
        rows={}, reason="hand edit", timeline_id="a0c4856c")

    history = reel_versions.versions_of(str(project), "Reel 26 - x")
    assert len(history) == 1
    assert history[0]["kind"] == reel_versions.KIND_ADOPT
    path = (project / "pipeline_output" / "review" / "reel_versions.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["reels"]["Reel 26 - x"][0]["timeline_id"] == "a0c4856c"
