"""A garbage collector for rendered caption assets, built on reachability.

The defect this closes
----------------------
`pipeline_output/steps/4_05_render_subtitles/` on the field project held
13 GB across ~5,700 files: superseded generations of re-rendered cards
and older title slugs no run cleans up, because no run owns the
directory's lifetime. The tempting collector - by name, slug or age - is
WRONG: an older-named asset may still be placed on a timeline the
captain keeps, and deleting a referenced asset silently breaks it.

The only safe test is REACHABILITY: an asset is garbage when NOTHING
references it. The roots are every timeline in the captain's Resolve
project (read through a COPY of the database, never the live file), the
pipeline's own current step records and manifests, and anything a later
step consumes downstream. An asset reachable from any root is LIVE.
Everything else is a candidate, and a candidate is not a deletion until
the captain or firstmate says so - the sweep MOVES to quarantine and
never deletes.

Three parts: a read-only `mark`, a `sweep` that refuses on a stale mark
or an unreadable root, and a retention rule that orphans a superseded
generation at re-render time.
"""
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.caption_asset_gc import (  # noqa: E402
    LIVE,
    ORPHAN,
    SweepRefused,
    collect_pipeline_roots,
    collect_resolve_roots,
    enumerate_assets,
    mark,
    sweep,
)


def _write(path, size=100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)
    return path


def _asset_dir(root):
    return os.path.join(
        root, "pipeline_output", "steps", "4_05_render_subtitles")


def _mov_names():
    return [
        "sub_tl_akshita_1_10000-12000_aaaaaaaa.mov",
        "sub_tl_akshita_1_10000-12000_bbbbbbbb.mov",
        "sub_tl_craig_2_20000-22000_cccccccc.mov",
    ]


def _populate(asset_dir):
    """Three movs (two generations of one card), each with siblings."""
    for name in _mov_names():
        stem = name[:-4]
        _write(os.path.join(asset_dir, name), size=1000)
        _write(os.path.join(asset_dir, stem + "_props.json"), size=100)
        _write(os.path.join(asset_dir, stem + "_reuse_key.txt"), size=10)
    # A lone sibling: props whose mov never rendered (failed render).
    _write(os.path.join(
        asset_dir, "sub_tl_akshita_9_90000-92000_dddddddd_props.json"),
        size=100)
    return asset_dir


# --------------------------------------- the unreadable-root refusal


def test_unreadable_root_refuses_sweep(tmp_path):
    """A root that could not be read refuses the sweep, never widens it.

    An unreadable database returns nothing, which reads exactly like a
    project with nothing on any timeline - and would mark every file
    deletable, including the captain's live captions. That failure
    direction deletes work, so the sweep stops instead. This is the
    behaviour the whole module is built around, and it is tested first.
    """
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    db_path = os.path.join(project, "no-such-database", "Project.db")
    roots = collect_resolve_roots([db_path])
    assert roots[0].status == "unreadable"
    result = mark(project, asset_dir, roots)
    orphans = [a for a in result.assets if a.status == ORPHAN]
    assert orphans, "an unreadable root must not read as 'everything live'"
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    with pytest.raises(SweepRefused) as refused:
        sweep(mark_path, project_folder=project)
    assert "unreadable" in str(refused.value).lower()
    # Nothing moved: every file is still where it was.
    assert sorted(os.listdir(asset_dir)) == sorted(_mov_names()
        + [n[:-4] + "_props.json" for n in _mov_names()]
        + [n[:-4] + "_reuse_key.txt" for n in _mov_names()]
        + ["sub_tl_akshita_9_90000-92000_dddddddd_props.json"])


def test_sweep_refuses_when_a_root_became_unreadable_since_mark(tmp_path):
    """    The mark was fine, but the database is gone at sweep time.

    The mark is a claim about a moment and the move happens in a later
    one, so readability is re-proved then, not carried over.
    """
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live = os.path.join(asset_dir, _mov_names()[0])
    roots = [_ok_root("resolve:test", {live})]
    result = mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    gone = os.path.join(project, "moved-away.db")
    with pytest.raises(SweepRefused):
        sweep(mark_path, project_folder=project,
              db_paths=[gone])


def _ok_root(name, paths):
    from library.tools.caption_asset_gc import RootResult
    return RootResult(name=name, status="ok", paths=set(paths), detail="")


# ------------------------------------------------------------- the mark


def test_mark_reports_live_with_saving_root_and_orphan(tmp_path):
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live_mov = os.path.join(asset_dir, _mov_names()[2])
    roots = [_ok_root("resolve:test", {live_mov})]
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[live_mov].status == LIVE
    assert by_path[live_mov].saved_by == "resolve:test"
    orphan_mov = os.path.join(asset_dir, _mov_names()[0])
    assert by_path[orphan_mov].status == ORPHAN
    assert by_path[orphan_mov].saved_by == ""


def test_siblings_follow_their_mov(tmp_path):
    """A .json/.txt sibling has no lifetime of its own.

    It lives when its mov lives and goes when its mov goes, because
    nothing references a props file - timelines place the mov. A sibling
    whose mov is absent (a render that failed after writing props) is an
    orphan on its own.
    """
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live_mov = os.path.join(asset_dir, _mov_names()[2])
    roots = [_ok_root("resolve:test", {live_mov})]
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    live_stem = live_mov[:-4]
    assert by_path[live_stem + "_props.json"].status == LIVE
    assert by_path[live_stem + "_reuse_key.txt"].status == LIVE
    orphan_mov = os.path.join(asset_dir, _mov_names()[0])
    assert by_path[orphan_mov[:-4] + "_props.json"].status == ORPHAN
    lone = os.path.join(
        asset_dir, "sub_tl_akshita_9_90000-92000_dddddddd_props.json")
    assert by_path[lone].status == ORPHAN
    assert "mov" in by_path[lone].reason.lower()


def test_reachability_beats_name_and_age(tmp_path):
    """An old, oddly-named asset in the reference set stays LIVE.

    This pins the design insight: no collector by name, slug or age.
    The asset below looks stale by every heuristic and is placed on a
    timeline, so it lives.
    """
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    old = _write(os.path.join(
        asset_dir, "sub_reel-23_keyword-stuffing-is-hurting-your-ai-visi"
        "_akshita_3_460413-461208_7c2c1df7.mov"), size=500)
    ancient = os.path.getmtime(old) - 90 * 86400
    os.utime(old, (ancient, ancient))
    roots = [_ok_root("resolve:test", {old})]
    result = mark(project, asset_dir, roots)
    assert {a.path: a for a in result.assets}[old].status == LIVE


def test_mark_is_read_only(tmp_path):
    """The mark never writes into the asset directory: safe any time."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    before = {}
    for dirpath, _dirnames, filenames in os.walk(asset_dir):
        for name in filenames:
            path = os.path.join(dirpath, name)
            before[path] = (os.path.getsize(path),
                            os.stat(path).st_mtime_ns)
    roots = [_ok_root("resolve:test", set())]
    mark(project, asset_dir, roots)
    after = {}
    for dirpath, _dirnames, filenames in os.walk(asset_dir):
        for name in filenames:
            path = os.path.join(dirpath, name)
            after[path] = (os.path.getsize(path),
                           os.stat(path).st_mtime_ns)
    assert before == after


def test_stale_mark_refuses_sweep(tmp_path):
    """A file landing after the mark makes the mark stale: refuse."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    roots = [_ok_root("resolve:test", set())]
    result = mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    _write(os.path.join(asset_dir, "sub_tl_new_1_1-2_eeeeeeee.mov"))
    with pytest.raises(SweepRefused) as refused:
        sweep(mark_path, project_folder=project)
    assert "stale" in str(refused.value).lower()


# ------------------------------------------------------------ the sweep


def test_sweep_moves_to_quarantine_and_manifest_comes_first(tmp_path):
    """Move, never delete: the full manifest is on disk before anything
    moves, and every moved path - small records included - is in it."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live_mov = os.path.join(asset_dir, _mov_names()[2])
    roots = [_ok_root("resolve:test", {live_mov})]
    result = mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    record = sweep(mark_path, project_folder=project, fresh_roots=roots)
    assert os.path.isfile(record["manifest_path"])
    manifest = open(record["manifest_path"], encoding="utf-8").read()
    # Every moved path is named in full - no size-filtered listing.
    for path in record["moved"]:
        assert path in manifest
    # Small records are listed too: the manifest is the only way back.
    assert "_reuse_key.txt" in manifest
    assert "_props.json" in manifest
    # The live asset and its siblings never moved.
    assert os.path.isfile(live_mov)
    assert os.path.isfile(live_mov[:-4] + "_props.json")
    # The orphans are in quarantine, not deleted: bytes accounted.
    assert record["bytes_reclaimed"] == sum(
        a.size_bytes for a in result.assets if a.status == ORPHAN)
    for path in record["moved"]:
        assert not os.path.exists(path), path
    quarantine_hits = []
    for dirpath, _dirnames, filenames in os.walk(record["quarantine_dir"]):
        quarantine_hits.extend(filenames)
    assert len(quarantine_hits) == len(record["moved"])


def test_sweep_reproves_at_move_time(tmp_path):
    """A file that became referenced after the mark stops the sweep."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    roots = [_ok_root("resolve:test", set())]
    result = mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    # Fresh roots at sweep time now reference the first orphan.
    fresh = os.path.join(asset_dir, _mov_names()[0])
    with pytest.raises(SweepRefused):
        sweep(mark_path, project_folder=project,
              fresh_roots=[_ok_root("resolve:test", {fresh})])


# ---------------------------------------------------- pipeline roots


def test_pipeline_roots_read_step_records(tmp_path):
    """The pipeline's own records are a root: what the current step
    output and manifest name is reachable even with no Resolve."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    overlay = os.path.join(asset_dir, _mov_names()[0])
    step_dir = os.path.join(
        project, "pipeline_output", "steps", "4_05_render_subtitles")
    os.makedirs(step_dir, exist_ok=True)
    with open(os.path.join(step_dir, "output.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"subtitle_overlay": {"segments": [
            {"overlay_path": overlay},
        ]}}, handle)
    roots = collect_pipeline_roots(project)
    assert any(overlay in r.paths for r in roots if r.status == "ok")
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[overlay].status == LIVE
    assert by_path[overlay].saved_by.startswith("pipeline:")


def test_absent_pipeline_records_are_empty_not_unreadable(tmp_path):
    """A project that never ran the caption step has no record to read.

    That is an EMPTY root, not an unreadable one: refusing the sweep on
    a missing record would refuse it forever on exactly the projects
    whose garbage predates the records. Corrupt JSON, by contrast, is
    unreadable and refuses.
    """
    project = str(tmp_path)
    _populate(_asset_dir(project))
    roots = collect_pipeline_roots(project)
    assert roots, "the pipeline root set must exist even with no records"
    assert all(r.status == "no-record" for r in roots)
    assert all(r.paths == set() for r in roots)
    bad = os.path.join(project, "pipeline_data.json")
    with open(bad, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    roots = collect_pipeline_roots(project)
    assert any(r.status == "unreadable" for r in roots)


def test_collect_resolve_roots_marks_missing_database_unreadable(tmp_path):
    roots = collect_resolve_roots(
        [os.path.join(str(tmp_path), "missing", "Project.db")])
    assert len(roots) == 1
    assert roots[0].status == "unreadable"
    assert roots[0].paths == set()


# ------------------------------------------------------ the retention


def test_superseded_generation_orphaned_at_rerender_time(tmp_path):
    """Re-rendering a card orphans the generation it replaced, at once.

    Two renders of one card (same stem, different digest - e.g. a source
    span that shifted within the millisecond the filename carries) leave
    the older mov superseded: named on the newer entry, without waiting
    for a sweep. Reachability still rules the mark - this only names the
    candidate at the moment it becomes one.
    """
    from library.steps.step_4_05_render_subtitles.step import (
        RENDERED,
        render_one_segment,
    )
    from tests.test_subtitle_overlay_modes import _props, _StubRenderer

    out_dir = str(tmp_path)
    first = render_one_segment(_props(), out_dir, "tl",
                               remotion_dir="/none",
                               renderer=_StubRenderer())
    assert first["provenance"] == RENDERED
    assert first["superseded"] == []

    changed = _props()
    changed["_source_start"] = 10.0004  # same ms token, new digest
    second = render_one_segment(changed, out_dir, "tl",
                                remotion_dir="/none",
                                renderer=_StubRenderer())
    assert second["provenance"] == RENDERED
    assert second["superseded"] == [first["overlay_path"]]
    # The old file is still on disk: orphaned, not deleted.
    assert os.path.isfile(first["overlay_path"])
