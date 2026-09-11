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
    RENDER_LEDGER_NAME,
    LedgerUnreadable,
    SweepRefused,
    collect_pipeline_roots,
    collect_resolve_roots,
    enumerate_assets,
    ledger_path_for,
    mark,
    reconcile_render_ledger,
    record_rendered_segments,
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


def test_box_sidecar_is_a_sibling_of_its_mov(tmp_path):
    """The tight box sidecar lives while its mov lives.

    Measured 2026-09-10: the sweep quarantined Reel 26's thirteen live
    captions' `_box.json` sidecars as `unknown` - the suffix set knew
    the previous carriage (`_tight_box.json`) but not the current one -
    and the next build's reuse fell through to a full measured
    re-render per caption. A sidecar whose mov is live is live; one
    whose mov is orphaned follows it; a lone one is an orphan.
    """
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live_mov = os.path.join(asset_dir, _mov_names()[2])
    live_box = live_mov[:-4] + "_box.json"
    _write(live_box, size=100)
    orphan_mov = os.path.join(asset_dir, _mov_names()[0])
    orphan_box = orphan_mov[:-4] + "_box.json"
    _write(orphan_box, size=100)
    lone_box = os.path.join(
        asset_dir, "sub_tl_akshita_9_90000-92000_dddddddd_box.json")
    _write(lone_box, size=100)
    roots = [_ok_root("resolve:test", {live_mov})]
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[live_box].status == LIVE
    assert by_path[live_box].kind == "sibling"
    assert by_path[orphan_box].status == ORPHAN
    assert by_path[lone_box].status == ORPHAN
    assert by_path[lone_box].kind == "lone-sibling"


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

    Two renders of one card with genuinely different pixels (a text
    correction: same provenance stem, new content digest) leave the
    older mov superseded: named on the newer entry, without waiting
    for a sweep. Reachability still rules the mark - this only names
    the candidate at the moment it becomes one.
    """
    from library.steps.step_4_05_render_subtitles.step import (
        RENDERED,
        render_one_segment,
    )
    from tests.test_subtitle_overlay_modes import _props, _StubRenderer

    out_dir = str(tmp_path)
    first = render_one_segment(_props(), out_dir, "tl",
                               remotion_dir="/none",
                               renderer=_StubRenderer(),
                               overlay_geometry="full")
    assert first["provenance"] == RENDERED
    assert first["superseded"] == []

    changed = _props()
    changed["subtitles"] = [dict(changed["subtitles"][0],
                                 text="and so my very CHANGED")]
    second = render_one_segment(changed, out_dir, "tl",
                                remotion_dir="/none",
                                renderer=_StubRenderer(),
                                overlay_geometry="full")
    assert second["provenance"] == RENDERED
    assert second["overlay_path"] != first["overlay_path"]
    assert second["superseded"] == [first["overlay_path"]]
    # The old file is still on disk: orphaned, not deleted.
    assert os.path.isfile(first["overlay_path"])


def test_a_sub_pixel_source_shift_is_the_same_artefact(tmp_path):
    """A source-span shift inside the millisecond the filename carries
    changes no pixel: same provenance stem, same drawing digest, same
    file. The re-render overwrites itself - which is correct - and
    names nothing superseded, because there is no older generation."""
    from library.steps.step_4_05_render_subtitles.step import (
        RENDERED,
        render_one_segment,
    )
    from tests.test_subtitle_overlay_modes import _props, _StubRenderer

    out_dir = str(tmp_path)
    first = render_one_segment(_props(), out_dir, "tl",
                               remotion_dir="/none",
                               renderer=_StubRenderer(),
                               overlay_geometry="full")
    assert first["provenance"] == RENDERED

    changed = _props()
    changed["_source_start"] = 10.0004  # same ms token, same pixels
    second = render_one_segment(changed, out_dir, "tl",
                                remotion_dir="/none",
                                renderer=_StubRenderer(),
                                overlay_geometry="full")
    assert second["provenance"] == RENDERED
    assert second["overlay_path"] == first["overlay_path"]
    assert second["superseded"] == []


# --------------------------------------------- the render ledger


def _recorded_ids(asset_dir):
    with open(ledger_path_for(asset_dir), encoding="utf-8") as handle:
        data = json.load(handle)
    return [s["segment_id"]
            for s in data["subtitle_overlay"]["segments"]]


def test_render_one_segment_records_the_ledger(tmp_path):
    """A render outside any pipeline run still vouches for its pixels.

    The whole chain in one test: render one card straight into a
    project's step directory, and the pipeline root resolves it with no
    Resolve anywhere near it.
    """
    from library.steps.step_4_05_render_subtitles.step import (
        RENDERED,
        render_one_segment,
    )
    from tests.test_subtitle_overlay_modes import _props, _StubRenderer

    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    os.makedirs(asset_dir, exist_ok=True)
    produced = render_one_segment(_props(), asset_dir, "tl",
                                   remotion_dir="/none",
                                   renderer=_StubRenderer(),
                                   overlay_geometry="full")
    assert produced["provenance"] == RENDERED
    ledger = ledger_path_for(asset_dir)
    assert os.path.isfile(ledger)
    assert produced["segment_id"] in _recorded_ids(asset_dir)
    roots = collect_pipeline_roots(project)
    render_root = next(r for r in roots
                       if r.name == "pipeline:render_subtitles")
    assert render_root.status == "ok"
    assert produced["overlay_path"] in render_root.paths
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[produced["overlay_path"]].status == LIVE
    assert by_path[produced["overlay_path"]].saved_by == \
        "pipeline:render_subtitles"


def test_ledger_merge_never_narrows(tmp_path):
    """One step directory holds several timelines' batches, and a pass
    covers one plan: recording batch B must not unprotect batch A."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    first = os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov")
    second = os.path.join(asset_dir, "sub_tl_b_1_1-2_bbbbbbbb.mov")
    _write(first)
    _write(second)
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_aaaaaaaa",
        "overlay_path": first, "provenance": "rendered",
        "superseded": []}])
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_b_1_1-2_bbbbbbbb",
        "overlay_path": second, "provenance": "rendered",
        "superseded": []}])
    assert sorted(_recorded_ids(asset_dir)) == sorted(
        ["sub_tl_a_1_1-2_aaaaaaaa", "sub_tl_b_1_1-2_bbbbbbbb"])


def test_ledger_drops_superseded_generation(tmp_path):
    """A re-render unpins the generation it replaced: the old file
    becomes an orphan candidate naming its replacement, while a
    timeline-placed file would still read LIVE by reachability."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    old = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    new = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_bbbbbbbb.mov"))
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_aaaaaaaa",
        "overlay_path": old, "provenance": "rendered",
        "superseded": []}])
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_bbbbbbbb",
        "overlay_path": new, "provenance": "rendered",
        "superseded": [old]}])
    assert _recorded_ids(asset_dir) == ["sub_tl_a_1_1-2_bbbbbbbb"]
    roots = collect_pipeline_roots(project)
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[new].status == LIVE
    assert by_path[old].status == ORPHAN
    assert by_path[old].superseded_by == new


def test_ledger_carries_tight_fallback(tmp_path):
    """A verify-gate fallback keeps its reason in the only record a
    staging render leaves.

    The step records WHY a card is full canvas on a tight project on
    the entry (`tight_fallback`), and a staging render merges straight
    into the ledger with no step output beside it. Dropping the key at
    the merge reads exactly like a render that never tried tight, which
    is what made three full-canvas 09-09 renders on a tight project
    unexplorable."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    full = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_aaaaaaaa",
        "overlay_path": full, "provenance": "rendered",
        "superseded": [], "geometry": "full", "container": "video",
        "tight_box": None,
        "tight_fallback": "tight output is not the probe crop: "
        "max channel diff 255 (allows 4)"}])
    with open(ledger_path_for(asset_dir), encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data["subtitle_overlay"]["segments"]
    assert len(entries) == 1
    assert entries[0]["geometry"] == "full"
    assert "probe crop" in entries[0]["tight_fallback"]


def test_empty_run_records_an_empty_set(tmp_path):
    """No assets produced is a valid empty record - `ok` with no paths,
    never an unreadable root."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    record_rendered_segments(asset_dir, [])
    roots = collect_pipeline_roots(project)
    render_root = next(r for r in roots
                       if r.name == "pipeline:render_subtitles")
    assert render_root.status == "ok"
    assert render_root.paths == set()


def test_corrupt_ledger_reads_unreadable_and_refuses_sweep(tmp_path):
    """The failure direction: a record that could not be written (here
    a torn write, simulated as garbage bytes) reads UNREADABLE - never
    as an empty root - and the sweep moves nothing."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    ledger = ledger_path_for(asset_dir)
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_aaaaaaaa",
        "overlay_path": os.path.join(asset_dir, _mov_names()[0]),
        "provenance": "rendered", "superseded": []}])
    with open(ledger, "wb") as handle:
        handle.write(b"{torn write, not json")
    roots = collect_pipeline_roots(project)
    render_root = next(r for r in roots
                       if r.name == "pipeline:render_subtitles")
    assert render_root.status == "unreadable"
    assert render_root.paths == set()
    result = mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    with pytest.raises(SweepRefused) as refused:
        sweep(mark_path, project_folder=project, fresh_roots=roots)
    assert "unreadable" in str(refused.value).lower()
    assert sorted(os.listdir(asset_dir)) == sorted(
        _mov_names()
        + [n[:-4] + "_props.json" for n in _mov_names()]
        + [n[:-4] + "_reuse_key.txt" for n in _mov_names()]
        + ["sub_tl_akshita_9_90000-92000_dddddddd_props.json",
            RENDER_LEDGER_NAME])


def test_ledger_refuses_to_overwrite_itself_corrupt(tmp_path):
    """Recording onto a corrupt ledger raises instead of converting an
    UNREADABLE root into a freshly valid one naming only the latest
    pass - the overwrite that would silently unprotect everything the
    old record vouched for."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    os.makedirs(asset_dir, exist_ok=True)
    ledger = ledger_path_for(asset_dir)
    with open(ledger, "w", encoding="utf-8") as handle:
        handle.write("{torn write, not json")
    with pytest.raises(LedgerUnreadable):
        record_rendered_segments(asset_dir, [{
            "segment_id": "sub_tl_a_1_1-2_bbbbbbbb",
            "overlay_path": os.path.join(asset_dir, "sub_tl_new.mov"),
            "provenance": "rendered", "superseded": []}])
    assert open(ledger, encoding="utf-8").read() == \
        "{torn write, not json"


def test_ledger_prunes_files_gone_from_disk(tmp_path):
    """An entry whose file is gone protects nothing, so the next record
    drops it rather than pinning a path that can never match."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    gone = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    kept = _write(os.path.join(asset_dir, "sub_tl_b_1_1-2_bbbbbbbb.mov"))
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_aaaaaaaa",
        "overlay_path": gone, "provenance": "rendered",
        "superseded": []}])
    os.unlink(gone)
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_b_1_1-2_bbbbbbbb",
        "overlay_path": kept, "provenance": "rendered",
        "superseded": []}])
    assert _recorded_ids(asset_dir) == ["sub_tl_b_1_1-2_bbbbbbbb"]


def test_reconcile_adopts_step_signature_outputs(tmp_path):
    """Pre-ledger renders are adopted from the render path's own write
    signature - a mov with a parseable props sibling. A mov without one
    is left for the other roots to judge, and the pre-reconcile mark is
    embedded beside the entries so the adoption is auditable."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    signed = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    with open(signed[:-4] + "_props.json", "w", encoding="utf-8") as handle:
        json.dump({"subtitles": []}, handle)
    unsigned = _write(os.path.join(
        asset_dir, "hand_placed_1_1-2_bbbbbbbb.mov"))
    adopted = reconcile_render_ledger(project)
    assert adopted["recorded"] == 1
    with open(adopted["ledger"], encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data["subtitle_overlay"]["segments"]
    assert [e["overlay_path"] for e in entries] == [signed]
    assert entries[0]["reconciled"] is True
    assert entries[0]["segment_id"] == "sub_tl_a_1_1-2_aaaaaaaa"
    assert data["pre_reconcile_mark"]["roots"][
        "pipeline:render_subtitles"]["paths"] == 0
    roots = collect_pipeline_roots(project)
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[signed].status == LIVE
    assert by_path[signed].saved_by == "pipeline:render_subtitles"
    assert by_path[unsigned].status == ORPHAN


def test_reconcile_never_overwrites_recorded_entries(tmp_path):
    """Genuinely recorded evidence (with its binding) is never replaced
    by reconstructed evidence for the same segment."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    mov = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    with open(mov[:-4] + "_props.json", "w", encoding="utf-8") as handle:
        json.dump({"subtitles": []}, handle)
    binding = {"timeline": "tl", "speaker": "akshita",
               "block_position": 1, "source_clip_id": "clip_001",
               "source_start": 10.0, "source_end": 12.0}
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_aaaaaaaa", "overlay_path": mov,
        "provenance": "rendered", "superseded": [], "binding": binding}])
    reconcile_render_ledger(project)
    with open(ledger_path_for(asset_dir), encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data["subtitle_overlay"]["segments"]
    assert len(entries) == 1
    assert entries[0].get("binding") == binding
    assert "reconciled" not in entries[0]


def test_empty_plan_stamps_an_empty_ledger(tmp_path):
    """The full pass over a caption-less plan records the empty set it
    produced: the root reads `ok` with no paths."""
    from library.steps.step_4_05_render_subtitles.step import (
        render_subtitle_overlays,
    )
    project = str(tmp_path)
    remotion = str(tmp_path / "remotion")
    os.makedirs(remotion, exist_ok=True)
    out = render_subtitle_overlays(
        {"subtitle_entries": []}, {"structure": []},
        project_folder=project, remotion_dir=remotion)
    assert out["subtitle_overlay"]["available"] is False
    roots = collect_pipeline_roots(project)
    render_root = next(r for r in roots
                       if r.name == "pipeline:render_subtitles")
    assert render_root.status == "ok"
    assert render_root.paths == set()


def test_sweep_manifests_carry_their_area_and_never_share_a_path(tmp_path):
    """Two areas swept in the same second used to share one manifest
    filename - the motion-graphics record overwrote the subtitle one
    (measured 2026-09-10: 82 subtitle files survived only as loose
    files in quarantine).  The tag separates areas; the suffix
    separates repeats.  The clock is frozen so the shared stamp is
    certain, not likely."""
    import datetime as datetime_mod
    from unittest import mock

    frozen = datetime_mod.datetime(2026, 9, 10, 22, 16, 28,
                                   tzinfo=datetime_mod.timezone.utc)

    class _Frozen(datetime_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live_mov = os.path.join(asset_dir, _mov_names()[2])
    roots = [_ok_root("resolve:test", {live_mov})]
    with mock.patch("library.tools.caption_asset_gc.datetime", _Frozen):
        first_mark = mark(project, asset_dir, roots)
        first_path = os.path.join(project, "mark_a.json")
        first_mark.write_json(first_path)
        sub = sweep(first_path, project_folder=project,
                    fresh_roots=roots, manifest_tag="subtitle_segments")
        assert "subtitle_segments" in os.path.basename(
            sub["manifest_path"])
        assert os.path.isfile(sub["manifest_path"])

        other_mark = mark(project, asset_dir, roots)
        other_path = os.path.join(project, "mark_b.json")
        other_mark.write_json(other_path)
        mg = sweep(other_path, project_folder=project,
                   fresh_roots=roots,
                   manifest_tag="motion_graphics_segments")
        assert mg["manifest_path"] != sub["manifest_path"]
        assert os.path.isfile(sub["manifest_path"]), \
            "the second area's manifest must not overwrite the first's"

        _write(os.path.join(
            asset_dir,
            "sub_tl_akshita_9_90000-92000_eeeeeeee.mov"), size=500)
        repeat_mark = mark(project, asset_dir, roots)
        repeat_path = os.path.join(project, "mark_c.json")
        repeat_mark.write_json(repeat_path)
        repeat = sweep(repeat_path, project_folder=project,
                       fresh_roots=roots,
                       manifest_tag="subtitle_segments")
        assert repeat["manifest_path"] != sub["manifest_path"]
        assert repeat["manifest_path"].endswith("_2_manifest.md")
        assert os.path.isfile(sub["manifest_path"])
        with open(repeat["manifest_path"], encoding="utf-8") as handle:
            repeat_manifest = handle.read()
        for path in repeat["moved"]:
            assert path in repeat_manifest
