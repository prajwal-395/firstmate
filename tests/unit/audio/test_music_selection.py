"""Music selection has a real schema, and the library is really consulted.

The LLM gets a non-empty output schema, `PIPELINE_MUSIC_LIBRARY` is
catalogued, outside tracks stay allowed, and the shipped 3914-second
compilation is rejected on the recorded reasoning. History:
docs/evidence/music_tests.md#music-selection-contract.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from library.tools.music_duplicates import (  # noqa: E402
    DB_TOLERANCE,
    DECLINED_SIGNALS,
    distinct_count,
    mark_duplicates,
    same_recording,
)


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools.music_selection_contract import (  # noqa: E402
    validate_selection,
)

STEP = REPO / "library" / "steps" / "step_2_04_music_selection"


def _good_selection(audio_path: str, duration: float = 200.0) -> dict:
    return {
        "title": "Infected (Instrumental)",
        "source": "library",
        "audio_path": audio_path,
        "source_url": "",
        "duration_seconds": duration,
        "bpm": 90,
        "key": "F#m",
        "direction_justification": {
            "direction_mood": "honest and self-deprecating, quietly determined",
            "why_it_fits": "sparse and unresolved; it sits under the voice",
            "forbidden_registers": ["triumphant", "motivational"],
            "why_not_forbidden": {
                "triumphant": "no lift, no swell, no resolution",
                "motivational": "no drive, no build to a payoff",
            },
        },
        "candidates_evaluated": [],
        "splices": [],
    }


# ── 1. The schema is not empty ────────────────────────────────────────


def test_manifest_declares_llm_outputs():
    """Without `llm_outputs` the injected schema is empty.

    `interface.outputs` minus what the bridge supplies was the whole
    schema, and the bridge supplied `music_selection` - the only output.
    An explicit `llm_outputs` is what makes the ask real.
    """
    manifest = json.loads((STEP / "manifest.json").read_text(encoding="utf-8"))
    llm_outputs = manifest["interface"].get("llm_outputs")
    assert llm_outputs, "step 2.04 declares no llm_outputs - schema is empty again"
    names = {o["name"] for o in llm_outputs}
    assert "music_selection" in names

    description = next(
        o["description"] for o in llm_outputs if o["name"] == "music_selection"
    )
    for required in (
        "source",
        "audio_path",
        "source_url",
        "duration_seconds",
        "direction_justification",
        "forbidden_registers",
    ):
        assert required in description, (
            f"the schema never asks for {required!r}, so nothing constrains it"
        )


# ── 2. The library is consulted ───────────────────────────────────────


def test_bridge_lists_the_library_and_picks_nothing(tmp_path, monkeypatch):
    """The bridge must catalogue both sources and select none of them."""
    library = tmp_path / "shared_music"
    library.mkdir()
    (library / "house_track.mp3").write_bytes(b"\x00" * 32)

    project = tmp_path / "proj"
    (project / "music").mkdir(parents=True)
    (project / "music" / "local_track.wav").write_bytes(b"\x00" * 32)
    (project / "project.yaml").write_text(
        "target_duration_seconds: 45\n", encoding="utf-8")

    env = dict(os.environ)
    env["PIPELINE_MUSIC_LIBRARY"] = str(library)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, str(STEP / "bridge.py")],
        input=json.dumps({"project_folder": str(project),
                          "creative_direction": {}}),
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)

    assert "music_selection" not in out, (
        "the bridge chose a track. It must catalogue and hand over, not "
        "select - sorted(...)[0] is what put a 65-minute compilation in the "
        "shipped edit."
    )
    catalogue = out["music_candidates"]
    assert catalogue["target_duration_seconds"] == 45.0, (
        "target duration must come off the project's own project.yaml"
    )
    by_source = {c["source"] for c in catalogue["candidates"]}
    assert by_source == {"library", "project"}, (
        f"the library was not consulted: sources seen were {by_source}"
    )
    searched = {s["source"]: s for s in catalogue["searched"]}
    assert searched["library"]["directory"] == str(library)


# ── 3. Outside the library stays allowed, and a valid pick passes ────


def test_a_valid_library_or_external_selection_passes(tmp_path):
    """The captain did NOT restrict selection to the library."""
    track = tmp_path / "t.wav"
    track.write_bytes(b"\x00" * 16)
    candidates = [{"audio_path": str(track), "source": "library"}]
    assert validate_selection(_good_selection(str(track)), candidates,
                              60.0) == []

    external = _good_selection("", duration=180.0)
    external["source"] = "external"
    external["source_url"] = "https://example.com/track"
    assert validate_selection(external, [], 60.0) == []


# ── What the schema must make impossible ──────────────────────────────


def test_the_shipped_failure_is_now_rejected(tmp_path):
    """3914 seconds of "Inspirational Motivational" for a 60s edit.

    Two independent reasons, so removing either one still catches it.
    """
    track = tmp_path / "Inspirational Motivational Music Video.wav"
    track.write_bytes(b"\x00" * 16)
    candidates = [{
        "audio_path": str(track),
        "title": track.stem,
        "source": "project",
        "duration_seconds": 3914.652,
    }]
    selection = {
        "title": "Inspirational Motivational Music Video _ Work Background Music",
        "source": "project",
        "audio_path": str(track),
        "duration_seconds": 3914.652,
        "direction_justification": {
            "direction_mood": "honest and self-deprecating, quietly determined",
            "why_it_fits": "it is uplifting",
            "forbidden_registers": ["triumphant", "motivational"],
            "why_not_forbidden": {
                "triumphant": "it is only mildly so",
                "motivational": "it is background music",
            },
        },
    }
    errors = validate_selection(selection, candidates, 60.0)
    joined = " ".join(errors)
    assert "compilation" in joined, f"duration sanity did not fire: {errors}"
    assert "forbidden_registers" in joined, (
        f"the title/forbidden-register clash did not fire: {errors}"
    )


def test_each_unacceptable_selection_names_its_reason(tmp_path):
    track = tmp_path / "t.wav"
    track.write_bytes(b"\x00" * 16)
    uncatalogued = tmp_path / "not_catalogued.wav"
    uncatalogued.write_bytes(b"\x00" * 16)
    library = [{"audio_path": str(track), "source": "library"}]
    project = [{"audio_path": str(track), "source": "project"}]

    too_short = dict(_good_selection(str(track), duration=1.0),
                     source="project")
    no_url = dict(_good_selection("", duration=180.0),
                  source="external", source_url="")
    no_justification = _good_selection(str(track))
    del no_justification["direction_justification"]
    unanswered = _good_selection(str(track))
    unanswered["direction_justification"]["why_not_forbidden"].pop(
        "triumphant")

    rows = [
        (too_short, project, "silent"),
        (_good_selection(str(uncatalogued)), [], "catalogued candidates"),
        (no_url, [], "source_url"),
        (no_justification, library, "direction_justification"),
        (unanswered, library, "triumphant"),
    ]
    for selection, candidates, reason in rows:
        errors = validate_selection(selection, candidates, 60.0)
        assert any(reason in e for e in errors), (reason, errors)


# --------------------------------------------------------------------------
# From test_music_selection_resolver.py
#
# The main selection resolves title-plus-source to a catalogue path:
# exact on title AND source, and a refusal - never a guess - when the title
# is absent or ambiguous (AGENTS.md 10.5). History:
# docs/evidence/music_tests.md#music-selection-resolver.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.music_selection_contract import (  # noqa: E402
    resolve_audio_path,
)

STEP_DIR = REPO / "library" / "steps" / "step_2_04_music_selection"


def _load_post_bridge():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "step_2_04_post_bridge_resolver_under_test",
        STEP_DIR / "post_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(title, source, audio_path):
    return {"title": title, "source": source, "audio_path": audio_path}


def _selection(**overrides):
    base = {
        "title": "rise",
        "source": "library",
        "duration_seconds": 180.0,
        "direction_justification": {
            "direction_mood": "hopeful",
            "why_it_fits": "it lifts without swelling",
            "forbidden_registers": ["sombre"],
            "why_not_forbidden": {"sombre": "it never sits in a minor key"},
        },
    }
    base.update(overrides)
    return base


# ── The resolver finds the right entry ────────────────────────────────


def test_exact_title_and_source_resolve_to_the_catalogue_path():
    candidates = [
        _candidate("rise", "library", "/music/rise.mp3"),
        _candidate("rise", "project", "/proj/music/rise.wav"),
        _candidate("fall", "library", "/music/fall.mp3"),
    ]
    path, errors = resolve_audio_path("rise", "library", candidates)
    assert errors == []
    assert path == "/music/rise.mp3"


def test_a_title_that_does_not_name_exactly_one_entry_is_refused():
    """Never guessed: scoped by source, absent, or ambiguous - each refuses
    and says what IS held, so the answer can be corrected."""
    project_only = [_candidate("rise", "project", "/proj/music/rise.wav")]
    path, errors = resolve_audio_path("rise", "library", project_only)
    assert path is None
    assert any("rise" in e and "library" in e for e in errors)

    held = [_candidate("rise", "library", "/music/rise.mp3")]
    path, errors = resolve_audio_path("something else", "library", held)
    assert path is None
    joined = " ".join(errors)
    assert "something else" in joined and "rise" in joined

    # two files, one name: sorted order must not decide the bed
    first = _candidate("rise", "library", "/music/a-rise.mp3")
    second = _candidate("rise", "library", "/music/z-rise.mp3")
    path, errors = resolve_audio_path("rise", "library", [second, first])
    assert path is None
    joined = " ".join(errors)
    assert "/music/a-rise.mp3" in joined and "/music/z-rise.mp3" in joined


# ── The post-bridge uses it ───────────────────────────────────────────


def test_post_bridge_fills_a_missing_audio_path_from_title(tmp_path):
    """The gate this change exists for: title-only passes now, failed before."""
    audio = tmp_path / "rise.mp3"
    audio.write_bytes(b"not really audio, but it is on disk")
    post_bridge = _load_post_bridge()
    resolved = post_bridge.resolve_selection(
        selection=_selection(audio_path=""),
        candidates=[_candidate("rise", "library", str(audio))],
        target_duration=60.0,
        project_folder=str(tmp_path),
    )
    assert resolved["audio_path"] == str(audio)


def test_post_bridge_refuses_an_unresolvable_title(tmp_path):
    import pytest

    post_bridge = _load_post_bridge()
    with pytest.raises(ValueError, match="no resolvable track"):
        post_bridge.resolve_selection(
            selection=_selection(audio_path="", title="never catalogued"),
            candidates=[_candidate("rise", "library", "/music/rise.mp3")],
            target_duration=60.0,
            project_folder=str(tmp_path),
        )


def test_post_bridge_leaves_a_supplied_path_for_the_verdict(tmp_path):
    """The verbatim path still goes to the verdict untouched - old shape kept."""
    import pytest

    audio = tmp_path / "rise.mp3"
    audio.write_bytes(b"not really audio, but it is on disk")
    post_bridge = _load_post_bridge()
    with pytest.raises(ValueError, match="not one of"):
        post_bridge.resolve_selection(
            selection=_selection(audio_path="/music/somewhere-else.mp3"),
            candidates=[_candidate("rise", "library", str(audio))],
            target_duration=60.0,
            project_folder=str(tmp_path),
        )


# --------------------------------------------------------------------------
# From test_music_search.py
#
# Search reaches the model as RESULTS, not as an invitation.
#
# `search_youtube.py` sat in step 2.04's directory with no caller anywhere
# in the repository and `yt-dlp` in neither `requirements.txt` nor the venv,
# so the model was told it could name a URL with no results in front of it.
#
# These tests hold what replaced it, and none of them touch the network:
#
#   * search is default-on; a project that declares nothing gets queries
#     derived from creative_direction;
#   * a project can decline explicitly with `pipeline.music_search: false`;
#   * an explicit declaration must state its own bounds, or it is refused;
#   * a result that cannot cover the edit is dropped BEFORE anything is
#     downloaded, on the duration YouTube states for free;
#   * when search does not run, the reason is stated loudly;
#   * licence is recorded and gates nothing.

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


# --------------------------------------------------------------------------
# From test_music_duplicates.py
#
# Two candidates that are one recording, established from measurements.
#
# 001's catalogue held seven files, four survived the duration check, and
# two of those four were the same recording under two filenames - so the
# model was told it had four things to choose between and it had three.
#
# The numbers in these fixtures are the ones measured on 001 and on
# re-encodes of its own tracks, 2026-08-28; `library/tools/music_duplicates.py`
# carries the table. Nothing here is a filename comparison.

def candidate(title, path, duration, lufs, lra, tp, spread, wspread,
              speech_band, envelope, measured=True):
    return {
        "title": title, "audio_path": path, "duration_seconds": duration,
        "measured": measured, "integrated_lufs": lufs,
        "loudness_range_lu": lra, "true_peak_dbtp": tp,
        "rms_spread_db": spread, "window_spread_db": wspread,
        "speech_band_ratio_db": speech_band,
        "window_envelope_dbfs": envelope,
    }


# 001's own four surviving candidates, measured 2026-08-28.
SICKICK_ENVELOPE = [-23.1, -19.2, -20.7, -18.7, -17.5, -14.7,
                    -15.8, -11.7, -12.8, -13.2, -15.2, -15.3]

LYRICS_MP3 = candidate(
    "Sickick - Infected (lyrics)", "/library/Sickick - Infected (lyrics).mp3",
    201.886, -13.78, 7.40, 0.42, 14.79, 17.33, -6.39, SICKICK_ENVELOPE)
LYRICS_WAV = candidate(
    "Sickick - Infected _lyrics_", "/001/Sickick - Infected _lyrics_.wav",
    201.886, -13.78, 7.40, 0.09, 14.35, 17.33, -6.41, SICKICK_ENVELOPE)
INSTRUMENTAL = candidate(
    "Sickick- _Infected_ _Instrumental_", "/001/instrumental.wav",
    198.600, -15.17, 9.80, 0.00, 30.58, 31.05, -8.60,
    [-42.7, -43.1, -30.7, -26.9, -20.1, -17.4,
     -18.0, -12.9, -14.1, -14.1, -16.5, -16.7])
RISE = candidate(
    "_background music_ rise", "/001/rise.wav",
    149.013, -13.94, 6.00, -0.86, 10.66, 2.26, -2.81,
    [-17.7, -14.9, -15.2, -14.8, -15.0, -15.4,
     -15.2, -15.8, -14.9, -15.1, -15.3, -14.9])

FOUR = [LYRICS_MP3, INSTRUMENTAL, LYRICS_WAV, RISE]


# ── The pair, and the pair that is not one ────────────────────────────

def test_the_two_encodes_of_one_recording_are_found():
    verdict, deltas = same_recording(LYRICS_MP3, LYRICS_WAV)
    assert verdict is True
    assert deltas["integrated_lufs"] == 0.0
    assert deltas["rms_spread_db"] == pytest.approx(0.44, abs=0.001)
    assert deltas["window_envelope_dbfs"] == 0.0


def test_the_instrumental_of_the_same_song_is_not_folded_in():
    """The hardest real pair available: same song, different recording."""
    for other in (LYRICS_MP3, LYRICS_WAV):
        verdict, deltas = same_recording(other, INSTRUMENTAL)
        assert verdict is False
    # and the margin is more than an order of magnitude
    assert abs(LYRICS_MP3["integrated_lufs"]
               - INSTRUMENTAL["integrated_lufs"]) > 10 * 0.0 + 1.0
    assert abs(SICKICK_ENVELOPE[0] - INSTRUMENTAL["window_envelope_dbfs"][0]) \
        > 10 * DB_TOLERANCE


def test_the_verdict_is_blind_to_the_name():
    """`(lyrics)` vs `_lyrics_` is no closer by string distance than
    either is to `_Instrumental_`, which is a different recording.

    So the names are made to disagree where the measurements agree, and
    to agree where they disagree, and neither verdict may move.
    """
    renamed_a = dict(LYRICS_MP3, title="Zebra", audio_path="/z.mp3")
    renamed_b = dict(LYRICS_WAV, title="Aardvark", audio_path="/a.wav")
    assert same_recording(renamed_a, renamed_b)[0] is True

    same_name_a = dict(LYRICS_MP3, title="Identical", audio_path="/x1.wav")
    same_name_b = dict(INSTRUMENTAL, title="Identical", audio_path="/x2.wav")
    assert same_recording(same_name_a, same_name_b)[0] is False

    assert "the filename" in DECLINED_SIGNALS


# ── An absent measurement is not evidence of sameness ─────────────────

def test_an_unmeasured_candidate_is_never_a_duplicate():
    hollow_a = {"title": "a", "audio_path": "/a.wav", "measured": False,
                "measurement_note": "already out on duration"}
    hollow_b = {"title": "b", "audio_path": "/b.wav", "measured": False,
                "measurement_note": "already out on duration"}
    assert same_recording(hollow_a, hollow_b)[0] is False
    assert same_recording(LYRICS_MP3, hollow_b)[0] is False
    marked = mark_duplicates([hollow_a, hollow_b, LYRICS_MP3, LYRICS_WAV])
    assert distinct_count(marked) == 3


def test_any_measured_disagreement_or_absence_is_a_different_recording():
    """Each row changes one thing about the WAV encode of the same song."""
    missing_column = {k: v for k, v in LYRICS_WAV.items()
                      if k != "speech_band_ratio_db"}
    short_envelope = dict(LYRICS_WAV,
                          window_envelope_dbfs=SICKICK_ENVELOPE[:6])
    louder = dict(LYRICS_WAV, integrated_lufs=(
        LYRICS_MP3["integrated_lufs"] + DB_TOLERANCE + 0.1))
    longer = dict(LYRICS_WAV,
                  duration_seconds=LYRICS_MP3["duration_seconds"] + 3.0)
    for other in (missing_column, short_envelope, louder, longer):
        assert same_recording(LYRICS_MP3, other)[0] is False


# --------------------------------------------------------------------------
# From test_music_audit_trail.py
#
# The music audit trail is out of the spines and kept whole in its own file.
#
# Absent from `audio_spine`; present and complete in
# `2_04_music_selection/music_audit_trail.json`; a missing or unparseable
# sidecar refuses. History: docs/evidence/music_tests.md#music-audit-trail.

REPO_2 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, REPO_2)

from library.steps.step_2_05_mesh_spine.post_bridge import (  # noqa: E402
    enrich_spine,
)
from library.tools import music_audit_trail as audit  # noqa: E402


def _speech():
    return {
        "body_sequence": [
            {
                "clip_id": "clip_001",
                "source_start": 0.0,
                "source_end": 4.0,
                "text": "First passage",
                "alignment_method": "whisperx",
                "word_timestamps": [
                    {"word": "First", "source_start": 0.0,
                     "source_end": 1.0}
                ],
            },
            {
                "clip_id": "clip_002",
                "source_start": 10.0,
                "source_end": 14.0,
                "text": "Second passage",
                "alignment_method": "whisperx",
                "word_timestamps": [
                    {"word": "Second", "source_start": 10.0,
                     "source_end": 11.0}
                ],
            },
        ]
    }


def _spine():
    return {
        "structure": [
            {"position": 1, "block_type": "speech",
             "content": {"passage_ref": 1}, "duration_seconds": 4.0,
             "music_behavior": "background"},
            {"position": 2, "block_type": "speech",
             "content": {"passage_ref": 2}, "duration_seconds": 4.0,
             "music_behavior": "background"},
        ]
    }


def _selection_2():
    """A resolved selection WITH the full audit record attached."""
    return {
        "title": "Bed",
        "source": "library",
        "audio_path": "/music/bed.wav",
        "duration_seconds": 160.0,
        "bpm": None,
        "key": None,
        "direction_justification": {
            "direction_mood": "measured, unhurried",
            "why_it_fits": "it settles rather than pushes",
            "forbidden_registers": ["triumphant"],
            "why_not_forbidden": {"triumphant": "no brass, no lift"},
        },
        "candidates_evaluated": [
            {"title": "Bed", "source": "library", "verdict": "chosen",
             "reason": "the only candidate measured"},
            {"title": "Anthem", "source": "library",
             "verdict": "rejected", "reason": "names the forbidden lift"},
        ],
        "splices": [
            {"intended_use": "the settled tail", "source_in": 100.0,
             "source_out": 160.0},
        ],
        "section": {"source_in": 60.0, "why": "the rising middle"},
        "tracks": [],
        "measurements": {"measured": False,
                         "measurement_note": "no readable file here"},
        "target_duration_seconds": 60.0,
        "catalogue_size": 2,
        "provenance": {"found_by": "catalogue_scan"},
    }


# ── Half one: absent from the edit data ─────────────────────────────

def test_enrich_spine_writes_no_music_selection_into_the_spine():
    result = enrich_spine(_spine(), _speech(), _selection_2(),
                          {"project_config":
                           {"target_duration_seconds": 8.0}})
    spine = result["audio_spine"]
    assert "music_selection" not in spine, (
        "the audit trail is back in the spine - the carrier move "
        "regressed")
    # The conducting survives the move: blocks still carry the bed's
    # behaviour words, which live on the structure rows, not in the
    # removed copy.
    assert [b["music_behavior"] for b in spine["structure"]] == [
        "background", "background"]


# ── Half two: present and complete in its own file ───────────────────

def test_the_audit_file_holds_the_whole_selection(tmp_path):
    selection = _selection_2()
    path = audit.write_audit_trail(str(tmp_path), selection)
    assert path.name == audit.AUDIT_FILENAME
    # In the chooser's own directory - where "why was this track
    # chosen" is looked up.
    assert path.parent.name == "2_04_music_selection"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == selection, (
        "the audit file is not the whole record - it was summarised, "
        "truncated or reshaped")
    # The audit keys in particular: the essays a prompt never needs
    # and a later question needs most.
    assert stored["candidates_evaluated"] == \
        selection["candidates_evaluated"]
    assert stored["direction_justification"] == \
        selection["direction_justification"]
    assert audit.read_audit_trail(str(tmp_path)) == selection


# ── The other half of 2.04's warn-and-continue write ──────────────
#
# The post-bridge only WARNS on a failed sidecar write; the pre-render
# check's `assert_audit_trail_present` is what refuses.

def test_a_missing_or_unparseable_sidecar_refuses(tmp_path):
    selection = _selection_2()
    with pytest.raises(audit.AuditTrailMissing,
                       match="audit sidecar is missing"):
        audit.assert_audit_trail_present(str(tmp_path), selection)

    path = audit.write_audit_trail(str(tmp_path), selection)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(audit.AuditTrailMissing, match="does not parse"):
        audit.assert_audit_trail_present(str(tmp_path), selection)
