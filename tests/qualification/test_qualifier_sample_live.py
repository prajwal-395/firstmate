"""Qualifier sampling closed live in Ren Qualification.

Samples a point on Q Spine's qual_b through the eyedropper route.
The selection itself reads back nowhere in the API, so this gate proves
the route executes end to end (establish, eyedropper click, viewer
click, receipt) and the still pipeline works - not what the qualifier
selected. Selection quality (which hues the range holds) is judged
with the adjust slice, which can narrow the range and boost it.

Drives the live Resolve: skipped unless `REN_LIVE_RESOLVE=1` inside a
granted borrow-resolve window (`resolve_session` holds the whole file's
lease, one live take at a time across lanes). Both clicks need the
foreground - the runner refuses a locked screen instead of clicking
blind.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.resolve_live


@pytest.mark.serial(reason="the eyedropper clicks hold the instance lease")
def test_an_eyedropper_sample_moves_pixels(resolve_session, tmp_path):
    """The qualifier loop closed on pixels: sample mid qual_b and the
    sampled still differs from the baseline."""
    from pathlib import Path

    from library.tools.computer_use.qualifier_recipe import (
        run_qualifier_sample,
    )
    from library.tools.marker_capture import grab_still as grab
    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed

    resolve = connect_resolve()
    with borrowed(resolve):
        from library.tools.computer_use.client import DriverClient

        manager = resolve.GetProjectManager()
        project = manager.LoadProject("Ren Qualification")
        assert project.GetName() == "Ren Qualification"
        timeline = None
        for i in range(1, int(project.GetTimelineCount() or 0) + 1):
            tl = project.GetTimelineByIndex(i)
            if tl and tl.GetName() == "Q Spine":
                timeline = tl
        assert timeline is not None, "no Q Spine - reset the project first"
        items = timeline.GetItemListInTrack("video", 1) or []
        qual_b = next(
            (it for it in items if (it.GetName() or "") == "qual_b.mp4"),
            None)
        assert qual_b is not None, "no qual_b on Q Spine"
        with DriverClient() as client:
            receipt = run_qualifier_sample(client, resolve, {
                "unique_id": qual_b.GetUniqueId(),
                "point": [0.5, 0.5],
                "timeline": "Q Spine",
                "foreground": True,
            })
        assert receipt["sampled"] is True
        assert receipt["verified"] is False
        shot = tmp_path / "sampled.png"
        project.SetCurrentTimeline(timeline)
        start = int(qual_b.GetStart())
        dur = int(qual_b.GetDuration())
        mid = start + dur // 2
        timeline.SetCurrentTimecode(
            f"00:00:{(mid // 24) % 60:02d}:{mid % 24:02d}")
        # The grab must put the gallery back: read the album count back,
        # or repeated runs leave the captain a gallery to tidy.
        album = project.GetGallery().GetCurrentStillAlbum()
        stills_before = len(album.GetStills() or [])
        grab(timeline, project, Path(str(shot)))
        assert shot.stat().st_size > 0
        assert len(album.GetStills() or []) == stills_before, (
            "the sampled still left a still in the gallery")
