"""The cutaway cover check: unplaced source placed on the master clock.

Covers `verify_cover_clip` in `library/tools/reel_build.py` - the
external-input check (AGENTS.md 3) that lets a reaction cutaway show
listening picture the master never carried. The sync is DERIVED from
the nearest placed clip of the same file, never asserted, and every
other claim (bounds, disjointness, transcript silence, a locked
static shot) is checked too.

Media fixtures are generated into `tmp_path` with ffmpeg - no test
reaches a real project. The static shot is a `color=` source (a
locked camera, by construction); the moving shot is `testsrc2` (real
motion, by construction); the black shot is `color=black`.
"""

import shutil
import subprocess

import pytest

from library.tools.reel_build import OffsetRefused, verify_cover_clip
from library.tools.timeline_ingest import TimelineClip

FPS = 24000 / 1001

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason=(
        "ffmpeg/ffprobe is not available here, so the cover fixtures "
        "cannot be built or measured. Runs anywhere ffmpeg and "
        "ffprobe are on PATH - the CI runner installs them and "
        "AGENTS.md 9 requires them for any real run."
    ),
)


def _render(path, src, duration=4):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"{src}:d={duration}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


def _placed(source_file, source_in, source_out, master_start):
    return TimelineClip(
        resolve_item_id=f"placed-{source_in}",
        track_type="video", track_index=1, track_name="Akshita",
        speaker="Akshita", source_file=str(source_file),
        source_in=source_in, source_out=source_out,
        source_in_frame=int(source_in * 24),
        source_out_frame=int(source_out * 24),
        source_frames=int(4.0 * 24),
        timeline_start=master_start,
        timeline_end=master_start + (source_out - source_in),
        name="clip", transform={"ZoomX": 2.0})


def _transcript(words=()):
    return {"segments": [
        {"speaker": "Akshita", "timeline_start": 0.0,
         "words": [{"word": w[2], "start": w[0], "end": w[1]}
                   for w in words]}]}


def test_cover_derives_master_span_from_the_neighbour(tmp_path):
    """Slope-1 continuation: the placed clip ends src 2.0 at master
    100.0, so src 2.5-3.5 lands master 100.5-101.5 - derived, and the
    neighbour's row, name, speaker and framing travel with it."""
    media = tmp_path / "cam.mxf"
    _render(media, "color=c=0x808080:s=160x120:r=24")
    clip = verify_cover_clip(str(media), 2.5, 3.5,
                             [_placed(media, 0.0, 2.0, 98.0)],
                             _transcript(), FPS, require_face=False)
    assert clip.timeline_start == pytest.approx(100.5)
    assert clip.timeline_end == pytest.approx(101.5)
    assert (clip.track_index, clip.track_name, clip.speaker) == (
        1, "Akshita", "Akshita")
    assert clip.transform == {"ZoomX": 2.0}, \
        "the same camera keeps the same crop"
    assert clip.source_file == str(media)


def test_cover_refuses_every_claim_it_cannot_check(tmp_path):
    """Each refusal names its reason: no placed clip of the file to sync
    from (sync would be asserted), the speaker talking inside the derived
    span (a cutaway mid-sentence is no reaction), a moving camera, and -
    under the production default - no face reading on a flat grey card."""
    media = tmp_path / "cam.mxf"
    other = tmp_path / "other.mxf"
    moving = tmp_path / "moving.mp4"
    _render(media, "color=c=0x808080:s=160x120:r=24")
    _render(other, "color=c=0x808080")
    _render(moving, "testsrc2=s=160x120:r=24")
    cases = (
        (media, [_placed(other, 0.0, 2.0, 98.0)], _transcript(), {},
         "no sync basis"),
        (media, [_placed(media, 0.0, 2.0, 98.0)],
         _transcript(words=[(100.7, 101.0, "mm-hm")]), {}, "mid-sentence"),
        (moving, [_placed(moving, 0.0, 2.0, 98.0)], _transcript(),
         {"require_face": False}, "camera moved"),
        (media, [_placed(media, 0.0, 2.0, 98.0)], _transcript(), {},
         "no face (check|reads)"),
    )
    for source, placed, transcript, kwargs, match in cases:
        with pytest.raises(OffsetRefused, match=match):
            verify_cover_clip(str(source), 2.5, 3.5, placed, transcript,
                              FPS, **kwargs)
