"""The archive a promotion keeps IS the generation it replaced.

Defect (Reel 26 audit §2.3): the promotion-time archive was not the
version being replaced. The `.drt` on disk at overwrite time was not
v22 - its semantic rows matched v23 - because the archive was taken by
NAME through a crowd of similarly named containers, and a name cannot
prove which generation an export came from.

The receipt therefore has to carry the identity of what was archived -
the Resolve unique id, the version's row digest, the exact Resolve
PROJECT and the moment - and the archive's own contents have to be
checked against the timeline that is retiring before the staged
timeline is allowed to take the final name.
"""

from __future__ import annotations

from library.tools.reel_build import _archive_inventory_against_snapshot


def _archive(items, project="Podcast (field test)"):
    return {
        "project": project,
        "timelines": [{"id": "t1", "tracks": [
            {"type": "video", "name": "Craig", "items": items}]}],
    }


def _snapshot(items):
    return {"items": items}


def test_the_archive_must_be_the_retiring_timeline():
    """An archive of a DIFFERENT generation is refused."""
    archive = _archive([
        {"name": "LCATL0013.MXF", "media_path": "/media/LCATL0013.MXF"},
        {"name": "LCATL0014.MXF", "media_path": "/media/LCATL0014.MXF"},
    ])
    timeline = _snapshot([
        {"name": "LCATL0014.MXF", "source_file": "/media/LCATL0014.MXF"},
        {"name": "LCATL0013.MXF", "source_file": "/media/LCATL0013.MXF"},
        {"name": "logo_bulb_lines_safe_23976.mov",
         "source_file": "/media/logo.mov"},
    ])

    verdict = _archive_inventory_against_snapshot(archive, timeline)

    assert verdict["matches"] is False
    assert "/media/logo.mov" in verdict["reason"]


def test_an_archive_of_the_retiring_timeline_matches():
    """The same generation, read in a different order, still matches."""
    archive = _archive([
        {"name": "LCATL0014.MXF", "media_path": "/media/LCATL0014.MXF"},
        {"name": "LCATL0013.MXF", "media_path": "/media/LCATL0013.MXF"},
    ])
    timeline = _snapshot([
        {"name": "LCATL0013.MXF", "source_file": "/media/LCATL0013.MXF"},
        {"name": "LCATL0014.MXF", "source_file": "/media/LCATL0014.MXF"},
    ])

    verdict = _archive_inventory_against_snapshot(archive, timeline)

    assert verdict["matches"] is True
    assert verdict["reason"] == ""


def test_a_renamed_item_is_the_same_generation():
    """The MEDIA is the identity; a name is a label Resolve re-spells."""
    archive = _archive([
        {"name": "LCATL0013.MXF", "media_path": "/media/LCATL0013.MXF"},
    ])
    timeline = _snapshot([
        {"name": "LCATL0013_v2.MXF", "source_file": "/media/LCATL0013.MXF"},
    ])

    verdict = _archive_inventory_against_snapshot(archive, timeline)

    assert verdict["matches"] is True


def test_different_media_under_the_same_name_is_a_difference():
    """Same name, different footage: a different generation."""
    archive = _archive([
        {"name": "LCATL0013.MXF", "media_path": "/media/LCATL0013.MXF"},
    ])
    timeline = _snapshot([
        {"name": "LCATL0013.MXF", "source_file": "/media/LCATL0014.MXF"},
    ])

    verdict = _archive_inventory_against_snapshot(archive, timeline)

    assert verdict["matches"] is False
