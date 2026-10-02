"""The readiness check SEES standalone scripts beside the footage.

Repository-side guards cannot see a script living next to the footage, so
the finder matches the general shape (any loose `*.py`), never the names
one record observed. It REPORTS, never refuses.
History: docs/evidence/project_scripts.md.
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


def test_any_loose_script_is_found_not_a_list_of_names(tmp_path):
    """The three record scripts by name, the fourth the record did not
    name (`render_subtitle_segments.py`, real, from the live folder) and
    a nested one: a finder matching only observed names would miss the
    next script."""
    from library.tools.project_scripts import find_standalone_scripts
    project = _project(tmp_path)
    names = ("place_subtitles.py",
             "generate_podcast_subtitles.py",
             "export_audio.py",
             "render_subtitle_segments.py",
             os.path.join("subtitle_plans", "helper.py"))
    found = _plant(project, *names)
    assert set(found) == {os.path.join(str(project), n) for n in names}
    assert found == sorted(found), "findings arrive in a stable order"
    assert find_standalone_scripts(str(project)) == found


def test_pipeline_output_and_non_python_files_are_not_findings(tmp_path):
    """Scripts under the pipeline's own output area are not reported,
    and neither is anything that is not Python.

    The exclusion is derived from the layout's own read route
    (`read_dir`, which never creates) - not from a copied `"..."`.
    """
    project = _project(tmp_path)
    out_root = layout_for(str(project)).read_dir(Area.OUTPUT_ROOT)
    rel = os.path.join(os.path.basename(str(out_root)), "scratch", "x.py")
    found = _plant(project, "place_subtitles.py", rel, "project.yaml",
                   "notes.md", os.path.join("raw", "LC4930.MXF"))
    assert set(found) == {os.path.join(str(project), "place_subtitles.py")}
    assert not any(os.path.realpath(p).startswith(os.path.realpath(out_root))
                   for p in found)
