"""Music analysis cache keys include the audio file's content."""

import json
from pathlib import Path
from types import SimpleNamespace

from library.steps.step_2_06_music_analysis import step as music_step
from library.tools.project_layout import Area, ProjectLayout


def test_replaced_audio_at_same_path_invalidates_analysis_cache(
        tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    track = project / "bed.wav"
    track.write_bytes(b"audio version one")
    output_dir = ProjectLayout(str(project)).write_dir(
        Area.MUSIC_ANALYSIS, step="music_analysis")
    analysis_path = Path(output_dir) / "music_analysis.json"
    analysis_path.write_text(json.dumps({
        "file": str(track),
        "method_hash": music_step._method_hash(),
        "audio_content_digest": music_step._audio_content_digest(str(track)),
        "available": True,
        "sentinel": "analysis of version one",
    }), encoding="utf-8")

    track.write_bytes(b"audio version two, still the same path")
    calls = []

    def no_output(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(music_step.subprocess, "run", no_output)
    result = music_step.analyse_music(
        {"audio_path": str(track)}, project_folder=str(project))

    assert calls, "changed audio bytes must invoke the analyzer again"
    assert result["music_analysis"]["available"] is False
    assert "sentinel" not in result["music_analysis"]
    assert not analysis_path.exists(), (
        "the stale file must be removed before an analyzer that writes no output")


def test_unreadable_audio_fingerprint_never_hits_cache(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    track = project / "bed.wav"
    track.write_bytes(b"audio")
    output_dir = ProjectLayout(str(project)).write_dir(
        Area.MUSIC_ANALYSIS, step="music_analysis")
    analysis_path = Path(output_dir) / "music_analysis.json"
    analysis_path.write_text(json.dumps({
        "file": str(track),
        "method_hash": music_step._method_hash(),
        "audio_content_digest": "",
        "available": True,
    }), encoding="utf-8")

    calls = []
    monkeypatch.setattr(music_step, "_audio_content_digest", lambda _path: "")
    monkeypatch.setattr(
        music_step.subprocess, "run",
        lambda *a, **k: calls.append((a, k)) or SimpleNamespace(
            returncode=0, stdout="", stderr=""))

    music_step.analyse_music({"audio_path": str(track)},
                             project_folder=str(project))

    assert calls, "an empty content digest cannot authorize a reuse hit"
