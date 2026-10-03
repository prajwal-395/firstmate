"""Golden conversation, live: the offline scenario's stages on real Resolve.

`tests/scenarios/test_golden_conversation.py` drives the GEO podcast's
shape on the canonical double; this drives the same recipe through a real
Resolve, in a disposable scratch project (`eval_harness`'s bracket) that is
deleted afterwards while the project that was open is restored. Only the
transcriber and Remotion are answered (`golden.install_model_seams`); the
build, the gate, promotion, the touch and the render are Resolve's own.

Runs only inside a granted borrow-resolve window (`REN_LIVE_RESOLVE=1`,
`resolve_session`), with Resolve open on the Qualification project.
"""

from __future__ import annotations

import os
import shutil
import uuid

import pytest

from library.tools.eval_harness import (
    SCRATCH_PREFIX,
    resolve_bracket_end,
    resolve_bracket_start,
)
from tests.scenarios import golden

pytestmark = [
    pytest.mark.resolve_live,
    pytest.mark.skipif(shutil.which("ffmpeg") is None,
                       reason="golden media is rendered with ffmpeg"),
]


@pytest.fixture
def live(resolve_session, tmp_path, monkeypatch):
    """An empty scratch project holding the golden master; the project
    that was open is restored and the scratch deleted, also on failure."""
    from library.tools.marker_feedback import connect_resolve

    media = golden.ensure_media(golden.CONVERSATION)
    scratch = (f"{SCRATCH_PREFIX}golden-conversation-{os.getpid()}-"
               f"{uuid.uuid4().hex[:8]}")
    saved = resolve_bracket_start(scratch)
    try:
        project = connect_resolve().GetProjectManager().GetCurrentProject()
        assert project.GetName() == scratch
        golden.live_conversation_master(project, golden.CONVERSATION, media)
        renderer = golden.install_model_seams(monkeypatch,
                                              golden.CONVERSATION)
        folder = golden.make_project_folder(
            tmp_path, golden.CONVERSATION, resolve_name=scratch)
        yield folder, project, renderer
    finally:
        restored = resolve_bracket_end(scratch, saved)
        assert restored.get("project_restored"), restored
        assert restored.get("timeline_restored"), restored
        assert restored.get("scratch_deleted") is not False, restored


def test_conversation_live_build_touch_rebuild_deliver(live):
    folder, project, renderer = live
    recipe = golden.CONVERSATION

    transcript = golden.analyze(folder)
    assert [(s["speaker"], s["text"]) for s in transcript["segments"]] == [
        (line.speaker, line.text) for line in recipe.lines]

    proposal = golden.plan(folder, golden.CONVERSATION_ANSWER)
    golden.rule(proposal)
    assert golden.build(folder) == 0
    assert sorted(golden.timeline_names(project)) == sorted(
        [recipe.master, golden.REEL])
    reel = golden.timeline(project, golden.REEL)
    built = golden.rows(reel)
    assert [name for kind, name in built if kind == "video"][:2] == [
        "SpeakerOne", "SpeakerTwo"]
    assert ("audio", "SpeakerOne CH1") in built
    assert ("audio", "SpeakerTwo CH1") in built
    assert len(built[("video", "Subtitles")]) == len(renderer.rendered) // 2

    from library.tools import reel_touchup
    reel_touchup.apply_touchup(str(folder), {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V2", "item": 0,
         "properties": {"ZoomX": 1.25, "ZoomY": 1.25}}]})
    assert golden.build(folder) == 0
    reel = golden.timeline(project, golden.REEL)
    assert golden.rows(reel) == built
    assert reel.GetItemListInTrack("video", 2)[0].GetProperty(
        "ZoomX") == pytest.approx(1.25)

    from library.tools import reel_deliver
    delivered = reel_deliver.deliver_reel(str(folder), 1)
    assert delivered["delivered"] is True, delivered["verification"]
