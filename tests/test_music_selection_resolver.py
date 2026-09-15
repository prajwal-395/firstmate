"""The main selection resolves title-plus-source to a catalogue path.

At HEAD the model had to hand-copy `audio_path` verbatim because nothing
resolved the track TITLE it chose to the catalogue PATH the verdict
demands - the only title matching in the path was `_shortlist_track`,
which serves shortlist sections, not the main selection.  So a title the
model named and a path it retyped could disagree, and the step could only
refuse the disagreement after the fact.

`resolve_audio_path` is the missing resolver: exact on title AND source,
and a refusal - never a guess - when the title is absent or ambiguous.
An ambiguous title resolved by picking the first match would let sorted
order decide what the viewer hears, which is the defect family AGENTS.md
10.5 has been closing.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
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


def test_source_scopes_the_match():
    """The same title held by the project is not the library's track."""
    candidates = [_candidate("rise", "project", "/proj/music/rise.wav")]
    path, errors = resolve_audio_path("rise", "library", candidates)
    assert path is None
    assert any("rise" in e and "library" in e for e in errors)


# ── ...and refuses rather than guessing ──────────────────────────────


def test_an_absent_title_is_refused_actionably():
    candidates = [_candidate("rise", "library", "/music/rise.mp3")]
    path, errors = resolve_audio_path("something else", "library", candidates)
    assert path is None
    assert errors, "an unknown title must be refused, not resolved to nothing"
    joined = " ".join(errors)
    assert "something else" in joined
    assert "rise" in joined, (
        "the refusal must say what IS held, so the answer can be corrected"
    )


def test_an_ambiguous_title_is_refused_not_first_matched():
    """Two files, one name: sorted order must not decide the bed."""
    first = _candidate("rise", "library", "/music/a-rise.mp3")
    second = _candidate("rise", "library", "/music/z-rise.mp3")
    path, errors = resolve_audio_path(
        "rise", "library", [second, first])
    assert path is None
    assert errors, "an ambiguous title must be refused, never picked"
    joined = " ".join(errors)
    assert "/music/a-rise.mp3" in joined
    assert "/music/z-rise.mp3" in joined
    assert path != "/music/a-rise.mp3", (
        "the sorted-first file must not become the answer by position"
    )


def test_an_empty_title_is_refused():
    path, errors = resolve_audio_path(
        "", "library", [_candidate("rise", "library", "/music/rise.mp3")])
    assert path is None
    assert errors


def test_external_has_no_catalogue_path_to_resolve():
    path, errors = resolve_audio_path("anything", "external", [])
    assert path is None
    assert errors, "external tracks are fetched by URL, not resolved by title"


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
