"""Search reaches the model as RESULTS, not as an invitation.

`search_youtube.py` sat in step 2.04's directory with no caller anywhere
in the repository and `yt-dlp` in neither `requirements.txt` nor the venv,
so the model was told it could name a URL with no results in front of it.

These tests hold what replaced it, and none of them touch the network:

  * a project that declares nothing searches nothing, and says so;
  * a declaration must state its own bounds, or it is refused by name;
  * a result that cannot cover the edit is dropped BEFORE anything is
    downloaded, on the duration YouTube states for free;
  * nothing composes a query out of the creative direction;
  * licence is recorded and gates nothing.
"""
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STEP = REPO / "library" / "steps" / "step_2_04_music_selection"

from library.tools import music_search  # noqa: E402
from library.tools.music_search import (  # noqa: E402
    DECLARATION_KEY,
    MusicSearchError,
    parse_declaration,
    provenance,
    search_declaration,
    within_duration,
)


def _project(tmp_path, pipeline_block=None):
    """A project under tmp_path. Never the captain's - AGENTS.md section 8."""
    folder = tmp_path / "proj"
    (folder / "music").mkdir(parents=True)
    config = {"name": "t", "slug": "t", "target_duration_seconds": 60}
    if pipeline_block is not None:
        config["pipeline"] = pipeline_block
    import yaml
    (folder / "project.yaml").write_text(
        yaml.safe_dump(config), encoding="utf-8")
    return folder


# ── A project can decline, and declining is the default ───────────────

def test_a_project_that_declares_nothing_searches_nothing(tmp_path):
    declaration = search_declaration(str(_project(tmp_path)))
    assert declaration.requested is False
    assert declaration.queries == ()
    # Stated, not silent: a run that quietly did not search reads exactly
    # like a run whose search found nothing.
    assert DECLARATION_KEY in declaration.reason
    assert "searches nothing" in declaration.reason


def test_a_project_can_decline_explicitly(tmp_path):
    declaration = search_declaration(
        str(_project(tmp_path, {DECLARATION_KEY: False})))
    assert declaration.requested is False
    assert "declined search explicitly" in declaration.reason


def test_no_project_folder_is_not_a_search(tmp_path):
    assert search_declaration("").requested is False
    assert search_declaration(None).requested is False


def test_a_declaration_is_read_off_the_project(tmp_path):
    folder = _project(tmp_path, {DECLARATION_KEY: {
        "queries": ["sparse piano instrumental"],
        "results_per_query": 4,
        "fetch_limit": 2,
    }})
    declaration = search_declaration(str(folder))
    assert declaration.requested is True
    assert declaration.queries == ("sparse piano instrumental",)
    assert declaration.results_per_query == 4
    assert declaration.fetch_limit == 2
    assert "4 result(s)" in declaration.describe()


# ── The bounds are the project's, and there is no default ─────────────

@pytest.mark.parametrize("bound", ["results_per_query", "fetch_limit"])
def test_a_declaration_without_its_bounds_is_refused(bound):
    declared = {"queries": ["x"], "results_per_query": 3, "fetch_limit": 2}
    del declared[bound]
    with pytest.raises(MusicSearchError, match=bound):
        parse_declaration(declared)


@pytest.mark.parametrize("value", [0, -1, "3", 2.5, True, None])
def test_a_bound_that_is_not_a_positive_integer_is_refused(value):
    with pytest.raises(MusicSearchError, match="results_per_query"):
        parse_declaration({"queries": ["x"], "results_per_query": value,
                           "fetch_limit": 2})


def test_a_declaration_with_no_query_is_refused():
    for queries in ([], "", None, [""], [3]):
        with pytest.raises(MusicSearchError, match="queries"):
            parse_declaration({"queries": queries, "results_per_query": 3,
                               "fetch_limit": 2})


def test_a_misspelled_key_is_refused_by_name():
    """A bound that is silently not there is a bound that is not there."""
    with pytest.raises(MusicSearchError, match="fetch_limi"):
        parse_declaration({"queries": ["x"], "results_per_query": 3,
                           "fetch_limi": 2, "fetch_limit": 2})


def test_a_declaration_that_is_not_a_mapping_is_refused():
    with pytest.raises(MusicSearchError):
        parse_declaration(["a query"])


def test_one_query_may_be_written_as_a_string():
    assert parse_declaration({"queries": "one query",
                              "results_per_query": 1,
                              "fetch_limit": 1}).queries == ("one query",)


# ── Bytes are only spent on tracks that could be chosen ───────────────

CEILING = 600.0
TARGET = 60.0
SLACK = 0.5


def test_a_compilation_is_rejected_before_it_is_downloaded():
    ok, note = within_duration({"duration_seconds": 15102.0},
                               TARGET, CEILING, SLACK)
    assert ok is False
    assert "TOO LONG" in note


def test_a_track_shorter_than_the_edit_is_rejected_before_download():
    ok, note = within_duration({"duration_seconds": 31.0},
                               TARGET, CEILING, SLACK)
    assert ok is False
    assert "TOO SHORT" in note


def test_a_real_track_survives():
    ok, note = within_duration({"duration_seconds": 226.0},
                               TARGET, CEILING, SLACK)
    assert ok is True and note == ""


def test_an_unstated_duration_is_kept_and_said_to_be_unstated():
    """An absent measurement is not a measurement of unsuitability."""
    for value in (None, 0, "3:45"):
        ok, note = within_duration({"duration_seconds": value},
                                   TARGET, CEILING, SLACK)
        assert ok is True
        assert "unstated" in note


# ── The engine does not write the search terms ────────────────────────

DIRECTION_FIELDS = ("target_mood", "emotional_landscape", "target_energy",
                    "energy_arc", "narrative_theme", "audience_emotion")


def test_nothing_composes_a_query_out_of_the_creative_direction():
    """Composing a query in code is the engine choosing the search terms.

    The project states its own queries. A phrase pasted out of the
    creative direction and padded with words like "instrumental" or "no
    copyright" would be AGENTS.md 10.5's fabrication under another name.
    """
    source = (REPO / "library" / "tools" / "music_search.py").read_text(
        encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]
    for field in DIRECTION_FIELDS:
        assert field not in body, (
            f"music_search reads creative_direction.{field}. The project "
            f"declares its own queries; the engine does not write them.")
    assert "creative_direction" not in body


def test_the_bridge_does_not_compose_a_query_either():
    source = (STEP / "bridge.py").read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]
    for field in DIRECTION_FIELDS:
        assert field not in body, (
            f"step 2.04's bridge builds a query out of "
            f"creative_direction.{field}")


# ── Licence is provenance, and it gates nothing ───────────────────────

def test_licence_is_recorded_and_gates_nothing():
    declaration = parse_declaration({"queries": ["x"], "results_per_query": 1,
                                     "fetch_limit": 1})
    record = provenance({"source_url": "https://example/watch?v=a",
                         "channel": "Some Channel",
                         "search_query": "x"}, declaration)
    assert record["source_url"] == "https://example/watch?v=a"
    assert record["channel"] == "Some Channel"
    assert record["licence"] == "unstated by the platform"
    assert "nothing" in record["licence_gates"]


def test_no_module_refuses_a_track_on_rights():
    """The captain took licensing off the table on 2026-08-28.

    Nothing may read a licence to decide anything. `music_search` and the
    step that calls it may RECORD one; no code path may branch on it.
    """
    for path in (REPO / "library" / "tools" / "music_search.py",
                 REPO / "library" / "tools" / "music_selection_contract.py",
                 STEP / "bridge.py",
                 STEP / "post_bridge.py"):
        source = path.read_text(encoding="utf-8")
        body = source.split('"""', 2)[-1]
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "licence" in stripped or "license" in stripped:
                assert not stripped.startswith(("if ", "elif ", "assert ")), (
                    f"{path.name} branches on a licence: {stripped!r}")


# ── The tool, and how it is invoked ───────────────────────────────────

def test_yt_dlp_is_invoked_through_the_running_interpreter_when_it_can_be():
    """The `yt-dlp` on PATH may belong to another interpreter entirely.

    On this machine it did, and it was three versions stale. Same rule as
    AGENTS.md section 4's `sys.executable`.
    """
    command = music_search.yt_dlp_command()
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        assert command == ["yt-dlp"]
    else:
        assert command[0] == sys.executable
        assert command[1:] == ["-m", "yt_dlp"]


def test_yt_dlp_is_in_requirements_with_the_measured_floor():
    """It was in neither requirements.txt nor the venv, which is why the
    search script could never have run."""
    requirements = (REPO / "requirements.txt").read_text(encoding="utf-8")
    assert f"yt-dlp>={music_search.KNOWN_STALE_BEFORE}" in requirements


def test_the_dead_search_script_is_gone():
    """Replaced, not left beside its replacement."""
    assert not (STEP / "search_youtube.py").exists()


def test_search_refuses_a_non_positive_result_count():
    with pytest.raises(MusicSearchError):
        music_search.search("anything", 0)


# ── The bridge does not search unless asked ───────────────────────────

def test_the_bridge_reports_that_it_did_not_search(tmp_path, monkeypatch):
    """A project with no declaration produces a catalogue that says so."""
    folder = _project(tmp_path)
    library = tmp_path / "shared_music"
    library.mkdir()
    env = dict(os.environ, PIPELINE_MUSIC_LIBRARY=str(library))
    proc = subprocess.run(
        [sys.executable, str(STEP / "bridge.py")],
        input=json.dumps({"project_folder": str(folder)}),
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert proc.returncode == 0, proc.stderr
    catalogue = json.loads(proc.stdout)["music_candidates"]
    assert catalogue["search"]["requested"] is False
    assert catalogue["search"]["fetched"] == 0
    assert catalogue["search"]["cost"]["queries"] == 0
    assert catalogue["candidates"] == []
