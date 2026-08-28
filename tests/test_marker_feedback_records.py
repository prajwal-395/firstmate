"""The disk half of `marker_feedback` - no Resolve, and no fake of one.

Everything here runs against real files in `tmp_path`. The Resolve half
is proved against a real running Resolve in
`tests/test_marker_feedback_against_resolve.py`; nothing in this file
stands in for it, because a fake that returns what the test just told it
is what left the module's four wrong assumptions green for months.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.marker_feedback import (  # noqa: E402
    PULL_FILE_SUFFIX,
    MarkerNote,
    frames_to_timecode,
    note_identity,
    pulled_files,
)
from library.tools.project_layout import (  # noqa: E402
    AREAS, Area, Kind, ProjectLayout, ProjectLayoutViolation, WRITABLE_KINDS,
)


def _note(**kw) -> MarkerNote:
    base = dict(source="timeline_marker", name="Q", note="n", text="Q\n\nn",
                frame=10, timecode="00:00:00:10", frame_in_timeline_space=10)
    base.update(kw)
    return MarkerNote(**base)


# ── Timecode ────────────────────────────────────────────────────────


@pytest.mark.parametrize("frame,fps,expected", [
    (0, 30.0, "00:00:00:00"),
    (29, 30.0, "00:00:00:29"),
    (30, 30.0, "00:00:01:00"),
    (108000, 30.0, "01:00:00:00"),   # Resolve's default start timecode
    (108007, 30.0, "01:00:00:07"),
    (1440, 24.0, "00:01:00:00"),
])
def test_timecode_of_an_absolute_frame(frame, fps, expected):
    assert frames_to_timecode(frame, fps) == expected


def test_timecode_is_absent_rather_than_guessed():
    """A timecode nobody can scrub to is worse than an admitted absence."""
    assert frames_to_timecode(None, 30.0) is None
    assert frames_to_timecode(10, 0.0) is None


# ── Identity, which is what the guard compares ──────────────────────


def test_identity_is_the_text_and_where_it_was_typed():
    a = _note()
    assert note_identity(a) == note_identity(_note(frame=999, timecode="x"))
    assert note_identity(a) != note_identity(_note(note="something else"))
    assert note_identity(a) != note_identity(_note(name="different"))
    assert note_identity(a) != note_identity(_note(source="clip_marker"))


def test_colour_is_not_part_of_identity():
    """The captain chose typed notes over colour codes: recolouring a
    marker must not make it look like a new note."""
    assert note_identity(_note(color="Blue")) == note_identity(
        _note(color="Red"))


# ── Where the record lives ──────────────────────────────────────────


def test_the_area_is_outside_pipeline_output_and_writable():
    spec = AREAS[Area.MARKER_FEEDBACK]
    assert not spec.relpath.startswith("pipeline_output"), (
        "everything under pipeline_output/ is Kind.OUTPUT - reproducible by "
        "a re-run. A typed note is destroyed by one."
    )
    assert spec.kind is Kind.CAPTURED
    assert spec.kind in WRITABLE_KINDS


def test_a_re_run_area_and_the_captured_area_are_different_places(tmp_path):
    layout = ProjectLayout(tmp_path)
    captured = layout.write_dir(Area.MARKER_FEEDBACK)
    assert layout.read_dir(Area.OUTPUT_ROOT) not in captured.parents
    assert captured.parent == layout.root


def test_the_layout_still_refuses_the_bare_project_root(tmp_path):
    layout = ProjectLayout(tmp_path)
    with pytest.raises(ProjectLayoutViolation):
        layout.assert_writable(tmp_path / "loose.json")
    assert layout.assert_writable(tmp_path / "marker_feedback" / "a.json")


def test_the_readme_states_that_a_re_run_must_not_delete_it(tmp_path):
    text = ProjectLayout(tmp_path).describe()
    assert "marker_feedback/" in text
    assert "re-run" in text


# ── Reading pull files back ─────────────────────────────────────────


def _write_pull(layout, name, timeline, notes):
    path = layout.write_path(Area.MARKER_FEEDBACK, name + PULL_FILE_SUFFIX)
    path.write_text(json.dumps({
        "format": "marker_feedback/1", "timeline": timeline,
        "note_count": len(notes), "notes": notes,
    }), encoding="utf-8")
    return path


def test_pull_files_are_listed_oldest_first_and_filtered_by_timeline(tmp_path):
    layout = ProjectLayout(tmp_path)
    _write_pull(layout, "Edit.20260828T090000Z", "Edit", [{"note": "a"}])
    _write_pull(layout, "Edit.20260828T100000Z", "Edit", [{"note": "b"}])
    _write_pull(layout, "Other.20260828T110000Z", "Other", [{"note": "c"}])

    every = pulled_files(str(tmp_path))
    assert [p.name for p, _ in every] == [
        "Edit.20260828T090000Z.markers.json",
        "Edit.20260828T100000Z.markers.json",
        "Other.20260828T110000Z.markers.json",
    ]
    assert [payload["timeline"] for _, payload in
            pulled_files(str(tmp_path), "Edit")] == ["Edit", "Edit"]


def test_an_unreadable_pull_file_is_skipped_not_fatal(tmp_path):
    layout = ProjectLayout(tmp_path)
    _write_pull(layout, "Edit.20260828T090000Z", "Edit", [{"note": "a"}])
    layout.write_path(
        Area.MARKER_FEEDBACK, "Edit.broken" + PULL_FILE_SUFFIX
    ).write_text("{not json", encoding="utf-8")
    assert len(pulled_files(str(tmp_path), "Edit")) == 1


def test_no_pull_directory_is_not_an_error(tmp_path):
    assert pulled_files(str(tmp_path)) == []
