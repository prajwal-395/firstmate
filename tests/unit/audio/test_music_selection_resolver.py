"""The main selection resolves title-plus-source to a catalogue path:
exact on title AND source, and a refusal - never a guess - when the title
is absent or ambiguous (AGENTS.md 10.5). History:
docs/evidence/music_tests.md#music-selection-resolver.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
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
