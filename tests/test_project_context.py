"""The project context folder: captain-owned input, map-not-body routing.

The captain asked for a context FOLDER the model reads - documents,
references, notes, links, images - generalising the rule
`library/tools/brief_reference.py` established for one document: the map
is always inline, a body is inline only when the map cannot stand in
for it. A folder of documents inlined would blow every prompt in the
pipeline, so the folder travels the same way the brief does: a step
declares `project_context`, the runner routes a MAP, and the step reads
a body with its shell when the map says it needs one.

Two areas, two owners: `context/` is the captain's INPUT (the pipeline
never writes there - the same law that guards `raw/`), and
`learned_context/` is pipeline-owned (what the run records back - see
`library/tools/learned_context.py`).
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools.project_layout import Area, ProjectLayout  # noqa: E402


def _project_with_context(tmp_path, files):
    (tmp_path / "context").mkdir()
    for name, body in files.items():
        p = tmp_path / "context" / name
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_a_step_cannot_write_into_the_captains_context(tmp_path):
    from library.tools.project_layout import ProjectLayoutViolation
    with pytest.raises(ProjectLayoutViolation):
        ProjectLayout(tmp_path).write_dir(Area.CONTEXT)
    assert not (tmp_path / "context").exists()


def test_the_map_names_every_file_and_inlines_no_big_body(tmp_path):
    from library.tools import project_context
    big = "Pace is patience. " * 500  # ~9 kB, far over the inline bar
    project = _project_with_context(tmp_path, {
        "style-guide.md": (
            "# Style Guide\n\nCut with intent.\n\n"
            "## Pacing\n\n" + big + "\n"),
        "note.txt": "The captain prefers dusk exteriors.\n",
    })
    full = sum((tmp_path / "context" / n).stat().st_size
               for n in ("style-guide.md", "note.txt"))
    built = project_context.build_context_map(project)
    assert "style-guide.md" in built
    assert "note.txt" in built
    # The small note travels whole; the big section travels as a map entry.
    assert "The captain prefers dusk exteriors." in built
    assert big not in built
    assert len(built.encode("utf-8")) < full


def test_a_markdown_body_is_reachable_through_the_map(tmp_path):
    """Reachability, the way test_brief_reference asserts it: follow the
    FILE line and the range with a shell, and require content the map
    never carried."""
    import subprocess
    from library.tools import project_context
    sentinel = "SENTINEL_PACING_LINE_41c9 the hold lasts four beats."
    big = "Pace is patience. " * 500
    project = _project_with_context(tmp_path, {
        "style-guide.md": (
            "# Style Guide\n\nCut with intent.\n\n"
            "## Pacing\n\n" + big + "\n" + sentinel + "\n"),
    })
    built = project_context.build_context_map(project)
    assert sentinel not in built
    path = project_context.map_path_for(built, "style-guide.md")
    assert path and Path(path).is_file()
    section_range = project_context.map_range_for(
        built, "style-guide.md", "Pacing")
    out = subprocess.run(
        ["sed", "-n", f"{section_range}p", path],
        capture_output=True, text=True, encoding="utf-8", check=True)
    assert sentinel in out.stdout


def test_an_image_is_listed_with_its_path_not_its_bytes(tmp_path):
    """An image has no section map. It travels as a listing - name, size,
    format, path - and the step opens it with an image-capable tool."""
    from library.tools import project_context
    # A minimal valid PNG (1x1). Bytes, not prose.
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
           b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde")
    project = _project_with_context(tmp_path, {"ref-frame.png": png})
    built = project_context.build_context_map(project)
    assert "ref-frame.png" in built
    assert "PNG" in built
    raw = png.decode("latin1")
    assert raw not in built


def test_a_declaring_step_is_routed_the_map_through_projection(tmp_path):
    """A step declaring `project_context` is routed the map, restored BY
    NAME like creative_brief: its allow-list neither has to list the
    captain's context nor can drop it."""
    from library.processes.edit_video.run_pipeline import gather_step_inputs
    project = _project_with_context(
        tmp_path, {"note.txt": "Dusk exteriors.\n"})
    dag = {"edges": []}
    manifest = {"interface": {"inputs": [
        {"name": "project_context", "type": "string", "required": False},
    ]}, "context_fields": ["creative_direction"]}
    inputs = gather_step_inputs(
        "music_selection", dag,
        {"project_folder": project, "creative_direction": {"a": 1}},
        manifest=manifest, step_type="llm_only")
    assert "note.txt" in inputs.get("project_context", "")
    assert "Dusk exteriors." in inputs["project_context"]

