"""Project lookup guidance points outside-root projects at a live command."""

import pytest

from library.tools.project_registry import get_project


def test_missing_slug_shows_a_valid_path_based_command(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    with pytest.raises(FileNotFoundError) as missing:
        get_project("outside-project", root=projects_root)

    message = str(missing.value)
    assert "python3 manage_project.py info /path/to/outside-project" in message
    assert "manage_project.py dashboard" not in message
