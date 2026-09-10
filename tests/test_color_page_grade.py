"""A project's PowerGrade declaration: provenance-gated, refused when bare.

Covers `library/tools/color_page_grade.py`: an absent declaration applies
nothing, a malformed one raises before any Resolve call, and the apply
record says what happened rather than asserting it.
"""
import os

import pytest
import yaml

from library.tools.color_page_grade import (
    ColorPageGradeError,
    apply_power_grade,
    resolve_color_page_grade,
)


def _project(tmp_path, color_block) -> str:
    """A project dir whose project.yaml carries `color:` as given."""
    if color_block is None:
        (tmp_path / "project.yaml").write_text(
            yaml.safe_dump({"name": "t", "pipeline": {}}))
    else:
        (tmp_path / "project.yaml").write_text(
            yaml.safe_dump({"name": "t", "color": color_block}))
    return str(tmp_path)


def _drx(tmp_path, name="grade.drx") -> str:
    path = tmp_path / name
    path.write_bytes(b"DRX")
    return str(path)


PROVENANCE = {"source": "GUI-built", "authorised_by": "captain, 2026-09-10",
              "licence": "captain's own asset"}


def test_no_declaration_applies_nothing(tmp_path):
    assert resolve_color_page_grade(_project(tmp_path, None)) is None


def test_no_project_folder_applies_nothing():
    assert resolve_color_page_grade("") is None


def test_a_declared_grade_resolves_to_an_absolute_path(tmp_path):
    drx = _drx(tmp_path)
    resolved = resolve_color_page_grade(_project(
        tmp_path, {"power_grade_drx": {"path": drx,
                                       "provenance": dict(PROVENANCE)}}))
    assert resolved["path"] == drx
    assert resolved["provenance"]["authorised_by"] == "captain, 2026-09-10"


def test_a_relative_path_resolves_inside_the_project(tmp_path):
    (tmp_path / "brand_assets").mkdir()
    (tmp_path / "brand_assets" / "v04.drx").write_bytes(b"DRX")
    resolved = resolve_color_page_grade(_project(
        tmp_path, {"power_grade_drx": {
            "path": "brand_assets/v04.drx",
            "provenance": dict(PROVENANCE)}}))
    assert resolved["path"] == str(tmp_path / "brand_assets" / "v04.drx")


def test_a_path_with_no_provenance_is_refused(tmp_path):
    """The replacement for the withdrawn no-drx rule: the old test failed
    on any `.drx` anywhere; the new rule fails on a `.drx` nobody
    authorised."""
    drx = _drx(tmp_path)
    with pytest.raises(ColorPageGradeError) as excinfo:
        resolve_color_page_grade(_project(
            tmp_path, {"power_grade_drx": {"path": drx}}))
    assert "provenance" in str(excinfo.value)


@pytest.mark.parametrize("provenance", [
    {},
    {"source": "x", "authorised_by": "y"},
    {"source": "x", "licence": "z"},
    {"authorised_by": "y", "licence": "z"},
])
def test_provenance_with_a_hole_is_refused(tmp_path, provenance):
    drx = _drx(tmp_path)
    with pytest.raises(ColorPageGradeError) as excinfo:
        resolve_color_page_grade(_project(
            tmp_path, {"power_grade_drx": {"path": drx,
                                           "provenance": provenance}}))
    assert "provenance" in str(excinfo.value)


def test_a_missing_file_is_refused_not_rendered_ungraded(tmp_path):
    with pytest.raises(ColorPageGradeError) as excinfo:
        resolve_color_page_grade(_project(
            tmp_path, {"power_grade_drx": {
                "path": str(tmp_path / "absent.drx"),
                "provenance": dict(PROVENANCE)}}))
    assert "not on disk" in str(excinfo.value)


def test_a_non_drx_path_is_refused(tmp_path):
    cube = tmp_path / "grade.cube"
    cube.write_bytes(b"LUT")
    with pytest.raises(ColorPageGradeError) as excinfo:
        resolve_color_page_grade(_project(
            tmp_path, {"power_grade_drx": {
                "path": str(cube), "provenance": dict(PROVENANCE)}}))
    assert ".drx" in str(excinfo.value)


def test_unknown_keys_are_refused(tmp_path):
    drx = _drx(tmp_path)
    with pytest.raises(ColorPageGradeError) as excinfo:
        resolve_color_page_grade(_project(
            tmp_path, {"power_grade_drx": {
                "path": drx, "provenance": dict(PROVENANCE),
                "intensity": 0.5}}))
    assert "intensity" in str(excinfo.value)


# ── The apply record ─────────────────────────────────────────────


class _Graph:
    def __init__(self, applied=True):
        self._applied = applied

    def ApplyGradeFromDRX(self, path, mode):
        assert mode == 0
        self.calls = (path, mode)
        return self._applied


class _Item:
    def __init__(self, graph):
        self._graph = graph

    def GetNodeGraph(self):
        return self._graph


class _CountingItem(_Item):
    def __init__(self):
        super().__init__(_Graph())
        self.fetches = 0

    def GetNodeGraph(self):
        # The handle goes stale across the apply: the count must come
        # off a FRESH graph, so the renderer re-fetches. Two fetches
        # minimum - one to apply through, one to read back.
        self.fetches += 1
        if self.fetches == 1:
            return self._graph
        fresh = _Graph()
        fresh.GetNumNodes = lambda: 8
        return fresh


def test_apply_returns_the_read_back_node_count():
    record = apply_power_grade(_CountingItem(), "/grade/v04.drx")
    assert record == {"path": "/grade/v04.drx", "applied": True, "nodes": 8}


def test_a_false_return_is_reported_not_raised():
    record = apply_power_grade(_Item(_Graph(applied=False)),
                               "/grade/v04.drx")
    assert record["applied"] is False
    assert "reason" in record


def test_no_graph_is_reported_not_raised():
    record = apply_power_grade(_Item(None), "/grade/v04.drx")
    assert record["applied"] is False
    assert "reason" in record


def test_repo_wide_drx_files_need_recorded_provenance():
    """Any `.drx` IN THIS REPO must be named in AGENTS.md 11 with its
    source, authorisation and licence. The captain's own file proved the
    route from outside the repo; nothing ships one without the recording
    the captain's ruling still requires."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", ".venv")]
        found.extend(os.path.join(root, f) for f in files
                     if f.endswith(".drx"))
    if not found:
        return
    with open(os.path.join(repo_root, "AGENTS.md"),
              encoding="utf-8") as handle:
        agents = handle.read()
    for path in found:
        name = os.path.basename(path)
        assert name in agents, (
            f"{path} ships with no provenance recorded in AGENTS.md 11")
