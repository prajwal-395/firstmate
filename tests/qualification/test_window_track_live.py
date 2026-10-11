"""Window tracking closed on pixels, live in Ren Qualification.

Presses `Color > Tracker > Forward` on Q Spine's qual_b - which carries
a circular-window node from the window-create proof - and judges the
sampled stills with `verify_window_coverage`: the boosted window must
still be there on every frame. The node is unboosted before restore,
so no lane inherits a grade.

Setup dependency, stated plainly: qual_b must hold a window node
(count >= 2 - the create slice's proof leaves exactly that). A fresh
project rebuild wipes it; the test then fails naming the setup
(`script --recipe window-create` on qual_b) instead of judging an
unwindowed clip.

Drives the live Resolve: skipped unless `REN_LIVE_RESOLVE=1` inside a
granted borrow-resolve window (`resolve_session` holds the whole file's
lease, one live take at a time across lanes). Menu presses and gallery
stills are background routes - no focus is taken.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.resolve_live

NEUTRAL = {
    "Slope": "1.0000 1.0000 1.0000",
    "Offset": "0.0000 0.0000 0.0000",
    "Power": "1.0000 1.0000 1.0000",
    "Saturation": "1.0000",
}


def _tc(fr, fps=24):
    return (f"{fr // (fps * 3600):02d}:{(fr // (fps * 60)) % 60:02d}:"
            f"{(fr // fps) % 60:02d}:{fr % fps:02d}")


@pytest.mark.serial(reason="the tracker press holds the instance lease")
def test_a_tracked_window_stays_covered_on_sampled_stills(
        resolve_session, tmp_path):
    """The track's loop closed on pixels: boost, track forward, sample
    three frames, and the coverage verdict passes with the window
    present on every frame."""
    from pathlib import Path

    from library.tools.computer_use.client import DriverClient
    from library.tools.computer_use.scripts import run_script
    from library.tools.computer_use.window_track import (
        build_window_track_script,
        verify_window_coverage,
    )
    from library.tools.marker_capture import grab_still as grab
    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed

    resolve = connect_resolve()
    with borrowed(resolve):
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
        count = int(qual_b.GetNumNodes())
        assert count >= 2, (
            f"qual_b holds {count} node(s) - no window node. Run "
            f"`script --recipe window-create` on qual_b first, then retry")
        node = count
        # The five grabs below must put the gallery back: read the
        # album count back, or repeated runs leave the captain a
        # gallery to tidy (2026-10-10: five discard-stills).
        album = project.GetGallery().GetCurrentStillAlbum()
        stills_before = len(album.GetStills() or [])
        try:
            project.SetCurrentTimeline(timeline)
            start = int(qual_b.GetStart())
            dur = int(qual_b.GetDuration())
            frames = [start + dur // 4, start + dur // 2,
                      start + 3 * dur // 4]
            timeline.SetCurrentTimecode(_tc(frames[1]))
            base = tmp_path / "base.png"
            regrab = tmp_path / "regrab.png"
            grab(timeline, project, base)
            grab(timeline, project, regrab)
            boosted = dict(NEUTRAL, **{"NodeIndex": str(node),
                                       "Slope": "1.5000 1.5000 1.5000"})
            assert bool(qual_b.SetCDL(boosted)), "SetCDL refused the boost"
            with DriverClient() as client:
                result = run_script(
                    client, build_window_track_script("forward"))
                assert result.outcome == "done", (
                    f"tracker press ended {result.outcome}")
                shots = []
                for n, fr in enumerate(frames):
                    timeline.SetCurrentTimecode(_tc(fr))
                    dest = tmp_path / f"s{n}.png"
                    grab(timeline, project, dest)
                    shots.append(str(dest))
            verdict = verify_window_coverage(
                baseline=str(base), regrab=str(regrab), windowed=shots)
            assert verdict["passed"], (
                f"coverage failed: {verdict['reason']} "
                f"counts={verdict['counts']}")
            assert all(c > 0 for c in verdict["counts"]), (
                f"window vanished mid-track: {verdict['counts']}")
        finally:
            qual_b.SetCDL(dict(NEUTRAL, NodeIndex=str(node)))
            assert len(album.GetStills() or []) == stills_before, (
                "the sampled stills left stills in the gallery - "
                "repeated runs would leave the captain a gallery to tidy")
