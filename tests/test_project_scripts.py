"""The readiness check SEES standalone scripts beside the footage.

The gap, recorded 2026-09-06: three standalone scripts lived in the
captain's project folder (`place_subtitles.py`,
`generate_podcast_subtitles.py`, `export_audio.py`) - re-implementing
steps 4.01/4.05 and placement outside the pipeline - and every
repository-side guard (the Ruling-1 no-second-implementation test, the
import greps, the operation registry) looks at the REPOSITORY, so none
of them could see a script living next to the footage. A later look
found a FOURTH (`render_subtitle_segments.py`), which is why the finder
below matches the general shape - any loose `*.py` - and never the
three names the record happened to observe.

The check REPORTS them; it does not refuse them. Refusing would dictate
how the captain works in their own directories, and that call is the
captain's (the record holds it for them). What the pipeline owes is
visibility: a run beside an unseen parallel implementation must say so.

Sweep, 2026-09-08: the bare identifiers appear nowhere else executable.
`library/tools/timeline_transcript.py` and
`docs/FIELD_TEST_PODCAST_FINDINGS.md` name the scripts in comments and
prose to explain what was carried across from them (interpolation of
untimed WhisperX words) - references, not routes. No other file type in
the repo names them.
"""

from __future__ import annotations

import os

from library.tools.project_layout import Area, layout_for


def _project(tmp_path):
    """A bare project folder under tmp_path - never a real one."""
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return project


def _plant(project, *relpaths):
    for rel in relpaths:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# standalone\n", encoding="utf-8")
    from library.tools.project_scripts import find_standalone_scripts
    return find_standalone_scripts(str(project))


def test_the_three_record_scripts_are_found(tmp_path):
    """The observed evidence, by name: all three must read as findings."""
    from library.tools.project_scripts import find_standalone_scripts
    project = _project(tmp_path)
    names = ("place_subtitles.py",
             "generate_podcast_subtitles.py",
             "export_audio.py")
    found = _plant(project, *names)
    expected = {os.path.join(str(project), name) for name in names}
    assert set(found) == expected
    assert found == sorted(found), "findings arrive in a stable order"
    assert find_standalone_scripts(str(project)) == found


def test_the_finder_is_not_a_list_of_three_names(tmp_path):
    """The fourth script the record did not name, plus a nested one.

    A finder matching only the observed names would pass the test above
    and miss the next script. `render_subtitle_segments.py` is the real
    fourth script from the live project folder.
    """
    project = _project(tmp_path)
    found = _plant(project, "render_subtitle_segments.py",
                   os.path.join("subtitle_plans", "helper.py"))
    expected = {os.path.join(str(project), "render_subtitle_segments.py"),
                os.path.join(str(project), "subtitle_plans", "helper.py")}
    assert set(found) == expected


def test_pipeline_output_is_the_pipeline_not_a_finding(tmp_path):
    """Scripts under the pipeline's own output area are not reported.

    The exclusion is derived from the layout's own read route
    (`read_dir`, which never creates) - not from a copied `"..."`.
    """
    project = _project(tmp_path)
    out_root = layout_for(str(project)).read_dir(Area.OUTPUT_ROOT)
    rel = os.path.join(os.path.basename(str(out_root)), "scratch", "x.py")
    found = _plant(project, "place_subtitles.py", rel)
    assert set(found) == {os.path.join(str(project), "place_subtitles.py")}
    assert not any(os.path.realpath(p).startswith(os.path.realpath(out_root))
                   for p in found)


def test_non_python_files_are_not_scripts(tmp_path):
    project = _project(tmp_path)
    found = _plant(project, "project.yaml", "notes.md",
                   os.path.join("raw", "LC4930.MXF"))
    assert found == []


# ── The readiness check is the reader ─────────────────────────────

