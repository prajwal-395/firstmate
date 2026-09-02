"""The panel traces a step's output without dumping it.

The captain's stated purpose for the panel: *trace all of the
intermediate outputs so we can see if everything is lining up.* 001's
`temporal_index` output is 2.89 MB, so the thing worth testing hardest is
that reading it costs a LEVEL and not the document - and that every bound
the view applies is said rather than silent.
"""

from __future__ import annotations

import json

import pytest

from library.tools import project_layout
from library.tools.panel import trace
from library.tools.panel.trace import PathError
from library.tools.project_layout import Area, ProjectLayout


def _project(tmp_path, state: dict):
    (tmp_path / "project.yaml").write_text("name: t\n", encoding="utf-8")
    ProjectLayout(tmp_path).pipeline_data_path.write_text(
        json.dumps(state), encoding="utf-8")
    return tmp_path


def _state(**outputs):
    return {
        "project_folder": "",
        "preflight_completed": {"scan": {}, "catalog": {}},
        "edit_completed": {},
        "failed_steps": [],
        "step_outputs": dict(outputs),
    }


# ── The step list is the pipeline, not the directory listing ─────────

def test_every_step_of_the_pipeline_is_a_row(tmp_path):
    """Driven by `project_layout.STEPS`, so a step that has never run is
    a row saying `pending` rather than being absent - the difference
    between "nothing ran it" and "it is not part of this pipeline"."""
    rows = trace.step_rows(_project(tmp_path, _state()))
    assert len(rows) == len(project_layout.STEPS)
    assert {row.node_id for row in rows} == {s.node_id
                                             for s in project_layout.STEPS}


def test_a_step_directory_resolves_through_the_one_translator(tmp_path):
    """`1_01_scan_project` is the node `scan` and no rule connects them.
    The scout's prototype guessed by longest match and gave three steps
    the wrong status; this asks `project_layout.node_id_for`."""
    rows = {row.dirname: row for row in
            trace.step_rows(_project(tmp_path, _state()))}
    assert rows["1_01_scan_project"].node_id == "scan"
    assert rows["1_02_catalog_footage"].node_id == "catalog"
    assert rows["6_02_validate_output"].node_id == "validate"
    for dirname, row in rows.items():
        assert row.node_id == project_layout.node_id_for(dirname)


def test_status_comes_from_the_two_ledgers(tmp_path):
    state = _state()
    state["failed_steps"] = ["temporal_index"]
    rows = {row.node_id: row for row in
            trace.step_rows(_project(tmp_path, state))}
    assert rows["scan"].status == "done"
    assert rows["scan"].ledger == "preflight"
    assert rows["temporal_index"].status == "failed"
    assert rows["render"].status == "pending"


def test_an_unwired_step_reads_unwired_not_pending(tmp_path):
    """`object_segmentation` is implemented and not in the DAG.
    Reporting it as `pending` says a run will get to it, which nothing
    will."""
    rows = {row.node_id: row for row in
            trace.step_rows(_project(tmp_path, _state()))}
    assert rows["object_segmentation"].status == "unwired"


def test_a_stranded_failure_is_named(tmp_path):
    """`failed_steps` is cleared when a step SUCCEEDS, so a step with no
    DAG node can never clear one - unwiring `object_segmentation` left
    exactly that. Counting it as `unwired` and moving on is the
    going-quiet AGENTS.md section 3 forbids."""
    state = _state()
    state["failed_steps"] = ["object_segmentation", "validate"]
    project = _project(tmp_path, state)
    rows = trace.step_rows(project, state)
    assert trace.stranded_failures(rows, state) == ["object_segmentation"]


# ── Reading a level costs a level ────────────────────────────────────

BIG = {"full_indices": [{"clip_id": "clip_%03d" % i,
                         "speech_regions": [{"text": "x" * 400}] * 40}
                        for i in range(17)],
       "total_indexed": 17,
       "source": "fresh"}


def test_the_top_level_of_a_large_output_is_four_lines(tmp_path):
    project = _project(tmp_path, _state(temporal_index=BIG))
    row = next(r for r in trace.step_rows(project) if r.node_id ==
               "temporal_index")
    result = trace.trace_step(project, row, trace.FROM_STATE, ())
    assert result.error == ""
    assert [e.key for e in result.entries] == ["full_indices",
                                               "total_indexed", "source"]
    assert result.leaf is None, "a level is not a leaf"


def test_a_preview_never_serialises_the_subtree():
    """A dict previews as its KEY NAMES and a list as the shape of its
    first item. Rendering the subtree here is what makes a 'summary' view
    cost the same as a dump."""
    preview = trace.preview(BIG["full_indices"])
    assert preview.startswith("[17 x {")
    assert "clip_id" in preview
    assert "x" * 100 not in preview
    assert len(preview) < 120


def test_descending_reaches_the_value_and_nothing_above_it(tmp_path):
    project = _project(tmp_path, _state(temporal_index=BIG))
    row = next(r for r in trace.step_rows(project) if r.node_id ==
               "temporal_index")
    result = trace.trace_step(project, row, trace.FROM_STATE,
                              ("full_indices", 3))
    assert result.error == ""
    assert {e.key for e in result.entries} == {"clip_id", "speech_regions"}


def test_a_leaf_is_bounded_and_says_what_did_not_fit():
    """A truncation that reads as an end is worse than no view at all."""
    leaf = trace.render_leaf("y" * 40000, ("full_indices", 0, "text"),
                             budget=1000)
    assert len(leaf.text) == 1000
    assert leaf.truncated
    assert leaf.withheld_bytes == 39000
    assert "39,000 more bytes" in leaf.note()
    assert "full_indices/0/text" in leaf.note()


def test_a_leaf_that_fits_says_nothing():
    leaf = trace.render_leaf({"a": 1})
    assert not leaf.truncated
    assert leaf.note() == ""


def test_a_level_wider_than_the_limit_reports_the_remainder():
    entries, withheld = trace.entries({str(i): i for i in range(50)}, limit=10)
    assert len(entries) == 10
    assert withheld == 40


# ── A path that does not exist is refused BY NAME ────────────────────

def test_a_missing_key_names_the_path_and_what_is_there():
    """An empty view of a mistyped path reads exactly like a step that
    produced nothing, which is the confusion this surface exists to
    remove."""
    with pytest.raises(PathError) as exc:
        trace.walk({"full_indices": []}, ("full_indicies",))
    assert "full_indicies" in str(exc.value)
    assert "full_indices" in str(exc.value), "it says what IS there"


def test_an_index_outside_a_list_is_refused():
    with pytest.raises(PathError) as exc:
        trace.walk({"a": [1, 2]}, ("a", 9))
    assert "outside it" in str(exc.value)


def test_descending_into_a_scalar_is_refused():
    with pytest.raises(PathError) as exc:
        trace.walk({"a": 5}, ("a", "b"))
    assert "has no members" in str(exc.value)


def test_a_bad_path_comes_back_as_an_error_not_an_exception(tmp_path):
    """The view has to draw something, and "this is why there is nothing
    here" is the thing worth drawing."""
    project = _project(tmp_path, _state(scan={"a": 1}))
    row = next(r for r in trace.step_rows(project) if r.node_id == "scan")
    result = trace.trace_step(project, row, trace.FROM_STATE, ("nope",))
    assert "nope" in result.error
    assert result.entries == []


# ── Two places an output lives, and the panel says which ─────────────

def test_both_sources_are_offered_and_labelled(tmp_path):
    project = _project(tmp_path, _state(scan={"a": 1}))
    row = next(r for r in trace.step_rows(project) if r.node_id == "scan")
    sources = {s.name: s for s in trace.sources_for(project, row)}
    assert set(sources) == {trace.FROM_STATE, trace.FROM_EXPORT}
    assert sources[trace.FROM_STATE].available is True
    assert sources[trace.FROM_EXPORT].available is False


def test_the_export_and_the_state_are_read_separately(tmp_path):
    """The per-step export is best-effort and a missing one reads as {}
    downstream (AGENTS.md 10.1). Merging them would hide exactly the
    disagreement worth seeing."""
    project = _project(tmp_path, _state(scan={"from": "state"}))
    layout = ProjectLayout(project)
    directory = layout.write_dir(Area.STEPS_ROOT) / "1_01_scan_project"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / project_layout.STEP_OUTPUT_FILE).write_text(
        json.dumps({"from": "the export"}), encoding="utf-8")

    row = next(r for r in trace.step_rows(project) if r.node_id == "scan")
    assert trace.load_output(project, row, trace.FROM_STATE) == {
        "from": "state"}
    assert trace.load_output(project, row, trace.FROM_EXPORT) == {
        "from": "the export"}


def test_an_unknown_source_is_refused_by_name(tmp_path):
    project = _project(tmp_path, _state(scan={"a": 1}))
    row = next(r for r in trace.step_rows(project) if r.node_id == "scan")
    with pytest.raises(PathError) as exc:
        trace.load_output(project, row, "guess")
    assert "guess" in str(exc.value)


def test_a_step_with_no_recorded_output_says_so(tmp_path):
    project = _project(tmp_path, _state())
    row = next(r for r in trace.step_rows(project) if r.node_id == "render")
    result = trace.trace_step(project, row, trace.FROM_STATE, ())
    assert "has no entry under step_outputs" in result.error


# ── The other files a step wrote are part of the trace ───────────────

def test_the_step_directory_is_listed_beside_the_output(tmp_path):
    """A step's directory IS its product, so the 17 per-clip indices are
    the answer to "did the temporal index really run on everything"."""
    project = _project(tmp_path, _state(temporal_index={"a": 1}))
    layout = ProjectLayout(project)
    directory = layout.write_dir(Area.STEPS_ROOT) / "1_04_temporal_index"
    (directory / "index").mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (directory / "index" / ("clip_%03d.json" % i)).write_text("{}")
    (directory / "notes.txt").write_text("hi")

    row = next(r for r in trace.step_rows(project) if r.node_id ==
               "temporal_index")
    result = trace.trace_step(project, row, trace.FROM_STATE, ())
    assert any(f.startswith("index/") and "3 files" in f
               for f in result.other_files)
    assert any(f.startswith("notes.txt") for f in result.other_files)
