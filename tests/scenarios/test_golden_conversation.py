"""Golden conversation: a synced two-camera podcast, cut into a reel.

The GEO podcast's shape (`tests/scenarios/golden.py`, `CONVERSATION`),
driven through every stage on the canonical Resolve double: analyze, plan,
build, touch, rebuild, the captain's own hand edits, and deliver.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import sys

import pytest

from library.tools.ren_refusal import REFUSAL_EXIT_CODE
from tests.scenarios import golden


def run_build_cli(monkeypatch, folder, command):
    """Run the actual build-reels parser and dispatcher on this test's double."""
    import manage_project

    monkeypatch.setattr(
        sys, "argv",
        ["manage_project.py", "build-reels", *command, str(folder)])
    try:
        manage_project.main()
    except SystemExit as exited:
        return exited.code
    return 0

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="golden media is rendered with ffmpeg")


@pytest.fixture(params=[golden.CONVERSATION, golden.CONVERSATION_24FPS],
                ids=["23.976-fps", "24-fps"])
def world(request, tmp_path, monkeypatch, stub_resolve_script):
    recipe = request.param
    media = golden.ensure_media(recipe)
    resolve, project = golden.conversation_world(recipe, media)
    renderer = golden.install_offline_seams(
        monkeypatch, resolve, recipe)
    folder = golden.make_project_folder(tmp_path, recipe)
    return folder, project, renderer, recipe


def test_conversation_analyze_plan_build_touch_rebuild_deliver(
        world, monkeypatch, capsys):
    folder, project, renderer, recipe = world

    # ── analyze: every line heard, bound to its own speaker's footage ──
    transcript = golden.analyze(folder)
    said = [(s["speaker"], s["text"]) for s in transcript["segments"]]
    assert said == [(line.speaker, line.text) for line in recipe.lines]
    for segment in transcript["segments"]:
        assert segment["source_file"].endswith(
            f"{segment['speaker'].lower()}.mov")

    # ── plan: the model's moment, checked and published PROPOSED ──
    proposal = golden.plan(folder, golden.conversation_answer(recipe))
    (moment,) = json.loads(proposal.read_text(encoding="utf-8"))["moments"]
    assert moment["approval"] == "proposed"
    assert moment["speakers"] == ["Akshita", "Craig"]
    assert moment["call_to_action"]["speaker"] == "Akshita"
    assert moment["call_to_action"]["text"] == recipe.lines[4].text
    assert golden.build(folder) != 0, (
        "nothing is approved, so nothing may be built")
    assert golden.REEL not in golden.timeline_names(project)

    # ── build: approved, built, verified, promoted ──
    golden.rule(proposal)
    assert golden.build(folder) == 0
    assert golden.timeline_names(project) == [recipe.master, golden.REEL]
    reel = golden.timeline(project, golden.REEL)
    assert reel.GetSetting("timelineFrameRate") == recipe.resolve_rate
    built = golden.rows(reel)
    assert [name for kind, name in built if kind == "video"][:2] == [
        "Akshita", "Craig"]
    assert ("audio", "Akshita CH1") in built
    assert ("audio", "Craig CH1") in built
    captions = built[("video", "Subtitles")]
    assert len(captions) == len(renderer.rendered) // 2
    assert all(enabled for _s, _e, enabled in captions)

    # ── touch: one in-place property write, journaled, nothing re-placed ──
    from library.tools import reel_touchup

    receipt = reel_touchup.apply_touchup(str(folder), {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V2", "item": 1,
         "properties": {"ZoomX": 1.25, "ZoomY": 1.25}}]},
        connect=lambda _name: project)
    assert receipt["gate"]["class"] == "composed"
    reel = golden.timeline(project, golden.REEL)
    assert reel.GetItemListInTrack("video", 2)[1].GetProperty("ZoomX") == 1.25
    assert golden.rows(reel) == built, "a property touch moves nothing"

    # ── rebuild, nothing changed: the reel is left alone, touch and all ──
    assert golden.build(folder) == 0
    reel = golden.timeline(project, golden.REEL)
    assert reel.GetItemListInTrack("video", 2)[1].GetProperty("ZoomX") == 1.25
    assert golden.rows(reel) == built

    # ── the captain's own hands: edits made in Resolve, not through Ren ──
    project.SetCurrentTimeline(reel)
    by_hand_caption = reel.GetItemListInTrack("video", 3)[2]
    assert by_hand_caption.SetClipEnabled(False)
    by_hand_angle = reel.GetItemListInTrack("video", 2)[1]
    assert by_hand_angle.SetProperty("Pan", 40.0)
    hand_edited = golden.rows(reel)

    # ...then the plan changes: the body now ends on Akshita's line.
    golden.rule(proposal, timeline_end=recipe.lines[2].last)
    capsys.readouterr()
    assert run_build_cli(monkeypatch, folder, []) == REFUSAL_EXIT_CODE, (
        "the replace guard refuses: nothing is promoted")
    refused_err = capsys.readouterr().err
    assert ("--allow-drop 'audio:Craig CH1' --allow-drop 'video:Subtitles'"
            in refused_err), "the refusal names the rows that shrink"
    assert "Traceback" not in refused_err, "a refusal prints no traceback"
    reel = golden.timeline(project, golden.REEL)
    assert golden.rows(reel) == hand_edited, (
        "a refused promotion leaves the approved, hand-edited reel whole")
    assert reel.GetItemListInTrack("video", 2)[1].GetProperty("Pan") == 40.0

    # Read the exact command the refusal printed, then run its flags
    # through the CLI against the same Resolve double.
    fix_line = next(line for line in refused_err.splitlines()
                    if line.strip().startswith("fix:"))
    suggested = re.search(r"`(build-reels [^`]*)`", fix_line)
    assert suggested, f"the refusal prints a runnable command: {fix_line}"
    suggested_words = shlex.split(suggested.group(1))
    assert suggested_words[0] == "build-reels"

    # ...declared, it promotes - and carries what the captain did by hand.
    # The refused staging stays held in the project; the next build
    # reclaims it and re-stages, so the printed command works as
    # printed with no hand deletion in Resolve
    # (docs/GOLDEN_PROJECTS.md, finding 5).
    assert run_build_cli(monkeypatch, folder, suggested_words[1:]) == 0
    assert golden.timeline_names(project) == [recipe.master, golden.REEL]
    reel = golden.timeline(project, golden.REEL)
    rebuilt = golden.rows(reel)
    assert len(rebuilt[("video", "Subtitles")]) < len(captions)
    assert rebuilt[("video", "Subtitles")][2][2] is False, (
        "the caption the captain switched off stays off")
    assert reel.GetItemListInTrack("video", 2)[1].GetProperty("Pan") == 40.0
    assert reel.GetItemListInTrack("video", 2)[1].GetProperty("ZoomX") == 1.25
    assert reel.GetItemListInTrack("video", 2)[1].GetProperty("ZoomY") == 1.25, (
        "the ren touch property write is carried through the plan-change "
        "rebuild like the captain's own hand edits")

    # ── deliver: the approved reel rendered, mastered and verified ──
    from library.tools import reel_deliver

    delivered = reel_deliver.deliver_reel(str(folder), 1)
    assert delivered["delivered"] is True
    assert delivered["timeline_name"] == golden.REEL
    verdict = delivered["verification"]
    assert verdict["resolution"]["passed"] and verdict["audio"]["passed"]
    assert verdict["duration"]["passed"], verdict["duration"]
    assert len(project.started_renders) == 1
    assert (project.render_settings["MarkIn"],
            project.render_settings["MarkOut"]) == (
        reel.GetStartFrame(), reel.GetEndFrame() - 1)
    assert project.render_jobs == [], "the deliverer leaves no queued job"
