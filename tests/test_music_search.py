"""Search reaches the model as RESULTS, not as an invitation.

`search_youtube.py` sat in step 2.04's directory with no caller anywhere
in the repository and `yt-dlp` in neither `requirements.txt` nor the venv,
so the model was told it could name a URL with no results in front of it.

These tests hold what replaced it, and none of them touch the network:

  * search is default-on; a project that declares nothing gets queries
    derived from creative_direction;
  * a project can decline explicitly with `pipeline.music_search: false`;
  * an explicit declaration must state its own bounds, or it is refused;
  * a result that cannot cover the edit is dropped BEFORE anything is
    downloaded, on the duration YouTube states for free;
  * when search does not run, the reason is stated loudly;
  * licence is recorded and gates nothing.
"""
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
    DEFAULT_FETCH_LIMIT,
    DEFAULT_RESULTS_PER_QUERY,
    MusicSearchError,
    derive_queries_from_creative_direction,
    parse_declaration,
    provenance,
    resolve_declaration,
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


# ── Default-on: search runs unless the project declines ───────────────

def test_a_project_that_declares_nothing_gets_default_on_search(tmp_path):
    """Captain's ruling 2026-09-02: search runs by default."""
    declaration = search_declaration(str(_project(tmp_path)))
    assert declaration.requested is True
    assert declaration.derive_from_direction is True
    assert declaration.queries == ()  # to be derived by the bridge
    assert declaration.results_per_query == DEFAULT_RESULTS_PER_QUERY
    assert declaration.fetch_limit == DEFAULT_FETCH_LIMIT


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


# ── Explicit declarations must state their own bounds ─────────────────

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


# ── Queries are derived from creative_direction by default ────────────

DIRECTION_FIELDS = ("target_mood", "emotional_landscape", "target_energy",
                    "energy_arc", "narrative_theme", "audience_emotion")


def test_derive_queries_from_creative_direction_uses_mood_and_theme():
    """The query is the model's own words, not a phrase this module composed."""
    direction = {
        "target_mood": "reflective and contemplative",
        "narrative_theme": "a personal journey through loss",
    }
    queries = derive_queries_from_creative_direction(direction)
    assert len(queries) == 1
    assert "reflective and contemplative" in queries[0]
    assert "a personal journey through loss" in queries[0]
    assert "background music" in queries[0]


def test_derive_queries_with_mood_only():
    queries = derive_queries_from_creative_direction(
        {"target_mood": "cinematic"})
    assert len(queries) == 1
    assert "cinematic" in queries[0]


def test_derive_queries_with_theme_only():
    queries = derive_queries_from_creative_direction(
        {"narrative_theme": "tech startup documentary"})
    assert len(queries) == 1
    assert "tech startup documentary" in queries[0]


def test_derive_queries_from_empty_direction_returns_nothing():
    """An empty creative_direction is a loud gap, not a silent one."""
    assert derive_queries_from_creative_direction({}) == ()
    assert derive_queries_from_creative_direction(None) == ()
    assert derive_queries_from_creative_direction(
        {"target_mood": "", "narrative_theme": ""}) == ()


def test_resolve_declaration_fills_in_derived_queries():
    """The resolve step turns a default-on declaration into a runnable one."""
    raw = search_declaration.__wrapped__ if hasattr(
        search_declaration, "__wrapped__") else None
    # Build a default-on declaration directly.
    from library.tools.music_search import SearchDeclaration
    declaration = SearchDeclaration(
        requested=True,
        queries=(),
        results_per_query=DEFAULT_RESULTS_PER_QUERY,
        fetch_limit=DEFAULT_FETCH_LIMIT,
        derive_from_direction=True,
    )
    direction = {"target_mood": "upbeat and energetic",
                 "narrative_theme": "fitness motivation"}
    resolved = resolve_declaration(declaration, direction)
    assert resolved.requested is True
    assert resolved.derive_from_direction is False
    assert len(resolved.queries) == 1
    assert "upbeat and energetic" in resolved.queries[0]
    assert "fitness motivation" in resolved.queries[0]


def test_resolve_declaration_loud_gap_when_direction_is_empty():
    """When creative_direction can't produce a query, the gap is LOUD."""
    from library.tools.music_search import SearchDeclaration
    declaration = SearchDeclaration(
        requested=True,
        queries=(),
        results_per_query=DEFAULT_RESULTS_PER_QUERY,
        fetch_limit=DEFAULT_FETCH_LIMIT,
        derive_from_direction=True,
    )
    resolved = resolve_declaration(declaration, {})
    assert resolved.requested is False
    assert "LIMITED TO WHAT IS ALREADY ON DISK" in resolved.reason
    assert "target_mood" in resolved.reason


def test_resolve_declaration_passes_through_explicit():
    """An explicit declaration is not touched by resolve_declaration."""
    declaration = parse_declaration({
        "queries": ["my custom query"],
        "results_per_query": 3,
        "fetch_limit": 2,
    })
    resolved = resolve_declaration(declaration, {"target_mood": "happy"})
    assert resolved.queries == ("my custom query",)
    assert resolved.results_per_query == 3


def test_resolve_declaration_passes_through_declined():
    """A declined declaration stays declined."""
    from library.tools.music_search import no_music_search
    declaration = no_music_search("project declined")
    resolved = resolve_declaration(declaration, {"target_mood": "happy"})
    assert resolved.requested is False


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


# ── The bridge searches by default, and declines loudly ───────────────

def test_the_bridge_reports_default_on_search_with_no_declaration(tmp_path):
    """A project with no declaration gets default-on search.

    We test the declaration pathway - not the full bridge subprocess,
    which would need network - to verify that the bridge would attempt
    search with queries derived from creative_direction.
    """
    folder = _project(tmp_path)
    declaration = search_declaration(str(folder))
    assert declaration.requested is True
    assert declaration.derive_from_direction is True
    # Resolve it with creative_direction to show the query.
    direction = {"target_mood": "chill lo-fi",
                 "narrative_theme": "late night coding"}
    resolved = resolve_declaration(declaration, direction)
    assert resolved.requested is True
    assert len(resolved.queries) == 1
    assert "chill lo-fi" in resolved.queries[0]
    assert "late night coding" in resolved.queries[0]


def test_the_bridge_reports_loud_gap_when_declined(tmp_path):
    """A project that explicitly declines search gets a loud message."""
    folder = _project(tmp_path, {DECLARATION_KEY: False})
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
    assert "declined search explicitly" in catalogue["search"]["reason"]
    # The loud gap must appear in stderr.
    assert "MUSIC SEARCH DID NOT RUN" in proc.stderr


def test_the_bridge_reports_loud_gap_when_direction_is_empty(tmp_path):
    """Without creative_direction fields, the gap is stated loudly."""
    folder = _project(tmp_path)
    library = tmp_path / "shared_music"
    library.mkdir()
    env = dict(os.environ, PIPELINE_MUSIC_LIBRARY=str(library))
    proc = subprocess.run(
        [sys.executable, str(STEP / "bridge.py")],
        input=json.dumps({
            "project_folder": str(folder),
            "creative_direction": {},
        }),
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert proc.returncode == 0, proc.stderr
    catalogue = json.loads(proc.stdout)["music_candidates"]
    assert catalogue["search"]["requested"] is False
    assert "LIMITED TO WHAT IS ALREADY ON DISK" in catalogue["search"]["reason"]
    assert "MUSIC SEARCH DID NOT RUN" in proc.stderr
