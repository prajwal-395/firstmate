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

from library.tools.music_search import (  # noqa: E402
    DECLARATION_KEY,
    DEFAULT_FETCH_LIMIT,
    DEFAULT_RESULTS_PER_QUERY,
    MusicSearchError,
    derive_queries_from_creative_direction,
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


# ── Default-on: search runs unless the project declines ───────────────

def test_a_project_that_declares_nothing_gets_default_on_search(tmp_path):
    """Captain's ruling 2026-09-02: search runs by default."""
    declaration = search_declaration(str(_project(tmp_path)))
    assert declaration.requested is True
    assert declaration.derive_from_direction is True
    assert declaration.queries == ()  # to be derived by the bridge
    assert declaration.results_per_query == DEFAULT_RESULTS_PER_QUERY
    assert declaration.fetch_limit == DEFAULT_FETCH_LIMIT


# ── Explicit declarations must state their own bounds ─────────────────

def test_a_declaration_that_does_not_state_its_bounds_is_refused_by_name():
    """Missing, non-positive and misspelled bounds each refuse, naming it."""
    rows = [
        ({"queries": ["x"], "fetch_limit": 2}, "results_per_query"),
        ({"queries": ["x"], "results_per_query": 0, "fetch_limit": 2},
         "results_per_query"),
        # a bound that is silently not there is a bound that is not there
        ({"queries": ["x"], "results_per_query": 3,
          "fetch_limi": 2, "fetch_limit": 2}, "fetch_limi"),
    ]
    for declared, match in rows:
        with pytest.raises(MusicSearchError, match=match):
            parse_declaration(declared)


# ── Bytes are only spent on tracks that could be chosen ───────────────

CEILING = 600.0
TARGET = 60.0
SLACK = 0.5


def test_duration_is_judged_before_download_and_unstated_is_kept():
    """Too long and too short are dropped on the stated duration; an
    absent measurement is not a measurement of unsuitability."""
    for seconds, ok_expected, word in ((15102.0, False, "TOO LONG"),
                                       (31.0, False, "TOO SHORT"),
                                       (None, True, "unstated"),
                                       (0, True, "unstated"),
                                       ("3:45", True, "unstated")):
        ok, note = within_duration({"duration_seconds": seconds},
                                   TARGET, CEILING, SLACK)
        assert ok is ok_expected, (seconds, note)
        assert word in note


# ── Queries are derived from creative_direction by default ────────────

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


def test_derive_queries_from_empty_direction_returns_nothing():
    """An empty creative_direction is a loud gap, not a silent one."""
    assert derive_queries_from_creative_direction({}) == ()
    assert derive_queries_from_creative_direction(None) == ()
    assert derive_queries_from_creative_direction(
        {"target_mood": "", "narrative_theme": ""}) == ()


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


# ── The bridge searches by default, and declines loudly ───────────────

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
