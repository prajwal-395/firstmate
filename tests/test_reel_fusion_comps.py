"""A reel build records the Fusion comps it had - as plain text, per reel.

`TimelineItem.ExportFusionComp` returns plain Lua text, which is what
makes reels diffable and mergeable at the layer where most of the
visual work lives. This pins the RECORD half of that: a build writes
one file per comp per reel under
`pipeline_output/steps/7_01_build_reels/fusion_comps/`, verbatim, and
a build with no comps records none.

Built against fakes, never a live Resolve session: the fakes below
are timelines the way `test_reel_build_gate_keeps_good_reel.py` builds
them - names that really resolve, rows that really list - with items
whose exporter writes caller-supplied Lua text. What is asserted is
what the build recorded on disk, not that the exporter can be called.
"""

from library.tools import reel_fusion_comps as comps
from tests.resolve_double import (
    FakeProject,
    FakeTimeline,
    TimelineItemSpec,
)

REEL = "Reel 01 - moment-1"
LUA_A = "-- Fusion comp A\nComposition {\n\tTools = {}\n}\n"
LUA_B = "-- Fusion comp B\nComposition {\n\tTools = { Blur1 = Blur {} }\n}\n"


def _item(*texts):
    return TimelineItemSpec("clip", 0, 1, fusion_comps=texts)


def test_build_records_the_comps_it_had(tmp_path):
    timeline = FakeTimeline(
        REEL,
        video={1: [_item(LUA_A, LUA_B)], 2: [_item(LUA_A)]},
    )
    project = FakeProject(timelines=[timeline])
    out = comps.export_built_reels(project, [REEL], str(tmp_path))

    want_dir = (
        tmp_path / "pipeline_output" / "steps" / "7_01_build_reels" / "fusion_comps"
    )
    assert want_dir.is_dir()
    files = sorted(p.name for p in want_dir.iterdir())
    assert len(files) == 3
    # Per-reel granularity: every file carries its reel, so one reel
    # reverts without touching another reel's files.
    stem = comps.safe_stem(REEL)
    assert all(name.startswith(stem + "__") for name in files)
    assert all(name.endswith(".comp") for name in files)
    # Verbatim: the bytes on disk are exactly what the exporter wrote -
    # never compressed, re-encoded, summarised or normalised.
    bodies = sorted((want_dir / name).read_bytes() for name in files)
    assert bodies == sorted(
        [LUA_A.encode("utf-8"), LUA_A.encode("utf-8"), LUA_B.encode("utf-8")]
    )
    report = out["reels"][REEL]
    assert report["comp_count"] == 3
    assert report["items_with_comps"] == 2
    assert report["errors"] == []
    assert sorted(out["files"]) == sorted(str(want_dir / name) for name in files)


def test_build_with_none_records_none(tmp_path):
    timeline = FakeTimeline(REEL, video={1: [_item()], 2: []})
    project = FakeProject(timelines=[timeline])
    # A ghost from a previous build carrying comps: a rebuild with
    # none must not leave it reading as still live.
    stale = comps.fusion_comps_dir(str(tmp_path)) / (
        comps.safe_stem(REEL) + "__V01_item000_c1.comp"
    )
    stale.write_text(LUA_A, encoding="utf-8")

    out = comps.export_built_reels(project, [REEL], str(tmp_path))

    remaining = list(comps.fusion_comps_dir(str(tmp_path)).iterdir())
    assert remaining == []
    report = out["reels"][REEL]
    assert report["comp_count"] == 0
    assert report["files"] == []
    assert out["files"] == []
