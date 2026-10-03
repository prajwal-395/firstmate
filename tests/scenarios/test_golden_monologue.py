"""Golden monologue: a phone walk-and-talk with B-roll, raw footage to file.

Project 001's shape (`tests/scenarios/golden_edit.py`, `MONOLOGUE`): one
portrait talking head, two landscape cutaways and a music bed, through the
`edit_video` runner to the `rough_cut_subtitles` target - every step from
scan to the Resolve build and render - on the canonical Resolve double.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.scenarios import golden, golden_edit

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="golden media is rendered with ffmpeg")

FPS = 30


@pytest.fixture
def world(tmp_path, monkeypatch, stub_resolve_script):
    recipe = golden_edit.MONOLOGUE
    media = golden_edit.ensure_media(recipe)
    folder = golden_edit.make_project_folder(tmp_path, recipe, media)
    golden_edit.in_process_steps(monkeypatch)
    asked = golden_edit.install_seams(monkeypatch, recipe, folder)
    project = golden_edit.resolve_world(monkeypatch, recipe)
    return folder, project, asked


def test_monologue_raw_footage_to_delivered_file(world):
    folder, project, asked = world
    recipe = golden_edit.MONOLOGUE

    state = golden_edit.edit(folder)
    assert not state.get("failed_steps"), state.get("step_errors")
    # Every judgement the run needed was asked once, and answered first time.
    assert asked == dict.fromkeys(
        ("creative_direction", "music_selection", "speech_sequence",
         "mesh_spine", "select_broll", "audio_mix", "review_rough_cut",
         "render"), 1)

    # ── the built timeline ──
    (name,) = project.names()
    assert name.startswith(recipe.name)
    timeline = project.timelines[0]
    rows = golden.rows(timeline)
    assert [row for kind, row in rows if kind == "video"] == [
        "A-Roll", "B-Roll", "Subtitles"]
    assert [row for kind, row in rows if kind == "audio"] == [
        "Speech", "Music"]

    # A-roll plays the three passages, in order, cut where their words are.
    a_roll = timeline.GetItemListInTrack("video", 1)
    assert [item.GetName() for item in a_roll] == ["talk.mov"] * 3
    first_words = [round(start * FPS) for start, _text in recipe.lines]
    assert [item.GetLeftOffset() for item in a_roll] == first_words
    assert rows[("audio", "Speech")] == rows[("video", "A-Roll")], (
        "the speech row plays exactly what the picture row cuts")

    # The cutaways: the planner over the line that names it, the
    # whiteboard covering the breath before the close.
    b_roll = timeline.GetItemListInTrack("video", 2)
    assert [item.GetName() for item in b_roll] == ["broll_1.mov",
                                                    "broll_2.mov"]
    end = timeline.GetEndFrame()
    shown = set()
    for row in ("A-Roll", "B-Roll"):
        for start, stop, _enabled in rows[("video", row)]:
            shown.update(range(start, stop))
    assert shown == set(range(timeline.GetStartFrame(), end)), (
        "every frame of the timeline shows a clip")

    # The bed runs under the whole piece; every caption sits on speech.
    assert [(s, e) for s, e, _ in rows[("audio", "Music")]] == [(0, end)]
    speech = [(s, e) for s, e, _ in rows[("audio", "Speech")]]
    captions = rows[("video", "Subtitles")]
    assert captions
    for start, stop, _enabled in captions:
        assert any(s <= start and stop <= e for s, e in speech), (
            start, stop, speech)

    # ── the delivered file ──
    export = folder / "exports" / f"{name}.mp4"
    assert export.is_file()
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height:format=duration", "-of", "json",
         str(export)], capture_output=True, encoding="utf-8",
        check=True).stdout)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert float(probe["format"]["duration"]) == pytest.approx(
        (end - timeline.GetStartFrame()) / FPS, abs=0.1)
