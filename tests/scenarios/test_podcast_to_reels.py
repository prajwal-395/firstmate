"""T14: a podcast file becomes reel proposals with no Resolve timeline.

"3 reels about pricing" on a short fixture podcast (`golden_podcast.py`:
the golden conversation's shape as ONE file), run through
`run_pipeline.run_pipeline` exactly as `ren edit <p> --reels 3 --about
pricing` execs it. Resolve is never reachable: every route to it raises.

The transcriber, the diarizer and the model are answered at one seam each
(`golden_podcast.install`). Everything else is the real code: the footage
enumeration, M0/M1 in the per-source memory, the source transcript, the
requirement, the scope, 3.04's bridge and post-bridge, and `ren propose`'s
writer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.scenarios import golden_edit, golden_podcast
from tests.scenarios.golden_podcast import TOPIC


@pytest.fixture
def podcast(tmp_path):
    folder = tmp_path / "podcast"
    folder.mkdir()
    (folder / "project.yaml").write_text(yaml.safe_dump({
        "name": "Fixture Podcast", "slug": "podcast",
        "source": {"type": "mov"}}), encoding="utf-8")
    golden_podcast.media(folder / "raw")
    return folder


def _run(folder: Path, **extra) -> dict:
    from library.processes.edit_video import run_pipeline

    return run_pipeline.run_pipeline(
        str(folder), full_auto="agent", llm_timeout=5,
        reels=3, about=TOPIC, **extra)


def test_three_reels_about_a_topic_from_a_podcast_with_no_timeline(
        podcast, monkeypatch):
    from library.tools import reel_proposal, timeline_transcript

    golden_edit.in_process_steps(monkeypatch)
    calls = golden_podcast.install(monkeypatch, podcast)

    summary = _run(podcast)
    # A scoped run reports PARTIAL - SUCCESS means the whole DAG - and
    # the request scoped it to reel selection.
    assert summary["status"] == "PARTIAL", summary
    assert summary["completed"] == ["select_reels", "judge_reels"]
    assert summary["failed"] == []
    assert calls["asked"] == {"select_reels": 1, "judge_reels": 1}

    path = timeline_transcript.transcript_path(podcast)
    transcript = json.loads(path.read_text(encoding="utf-8"))
    assert transcript["derived_from"]["kind"] == "source"
    assert transcript["derived_from"]["timeline"] is None
    assert sorted(transcript["speakers"]) == ["speaker_01", "speaker_02"]

    proposal = reel_proposal.write_from_step_output(str(podcast))
    document = json.loads(Path(proposal).read_text(encoding="utf-8"))
    moments = document["moments"]
    assert len(moments) == 3, moments
    for moment in moments:
        said = golden_podcast.said_between(transcript, moment["timeline_start"],
                             moment["timeline_end"])
        assert TOPIC in said, (moment, said)

    # The second run reuses the transcript: reel selection runs again,
    # nothing is heard or diarized again, and the file is not rewritten.
    heard, diarized = calls["heard"], calls["diarized"]
    assert (heard, diarized) == (1, 1)
    written = path.stat().st_mtime_ns
    summary = _run(podcast, rerun=["select_reels"])
    assert summary["failed"] == [], summary
    assert "select_reels" in summary["completed"]
    assert calls["asked"]["select_reels"] == 2
    assert (calls["heard"], calls["diarized"]) == (1, 1)
    assert path.stat().st_mtime_ns == written


def test_a_lost_transcript_is_rebuilt_from_memory_without_hearing(
        podcast, monkeypatch):
    """The per-source memory is the second layer of reuse: with the
    document gone, it is rebuilt from M1 and the recorded voices of a
    previous document are not needed - only the diarizer runs again."""
    from library.tools import source_transcript, timeline_transcript

    calls = golden_podcast.install(monkeypatch, podcast)
    first = source_transcript.ensure(str(podcast))
    timeline_transcript.transcript_path(podcast).unlink()
    second = source_transcript.ensure(str(podcast))
    assert calls["heard"] == 1
    assert [s["text"] for s in second["segments"]] == [
        s["text"] for s in first["segments"]]


def test_interrupted_edit_reuses_unchanged_work_and_invalidates_source_drift(
        podcast, monkeypatch):
    """A host interruption keeps valid work, while changed evidence
    invalidates the reel plan on resume.

    The runner's worker seam simulates the host disappearing after reel
    selection committed but while judging is starting. No project outside
    ``tmp_path`` is involved, and the golden model doubles count inference
    requests so reuse is observable rather than inferred from timestamps.
    """
    from library.processes.edit_video import run_pipeline

    calls = golden_podcast.install(monkeypatch, podcast)
    monkeypatch.setattr(run_pipeline, "parallel_workers_enabled",
                        lambda *_args, **_kwargs: False)
    from library.tools import step_ledger
    state = run_pipeline.load_pipeline_state(str(podcast))
    state[step_ledger.SOURCE_FINGERPRINTS_KEY] = \
        run_pipeline._current_source_fingerprints(str(podcast))
    run_pipeline.save_pipeline_state(str(podcast), state)
    real_work = run_pipeline._execute_step_once
    interrupted = {"once": False}

    def interrupt_after_selection(impl, inputs, node_id, *args, **kwargs):
        if node_id == "judge_reels" and not interrupted["once"]:
            interrupted["once"] = True
            raise RuntimeError("simulated agent-host interruption")
        return real_work(impl, inputs, node_id, *args, **kwargs)

    monkeypatch.setattr(run_pipeline, "_execute_step_once",
                        interrupt_after_selection)

    first = _run(podcast)
    assert first["status"] == "FAILED", first
    assert first["failed"] == ["judge_reels"]
    assert calls["asked"].get("select_reels") == 1
    assert calls["asked"].get("judge_reels", 0) == 0

    # A same-evidence resume must reuse the already committed selection.
    monkeypatch.setattr(run_pipeline, "_execute_step_once", real_work)
    resumed = _run(podcast, resume_mode=True)
    assert resumed["failed"] == [], resumed
    assert calls["asked"]["select_reels"] == 1
    assert calls["asked"]["judge_reels"] == 1

    # A changed source fingerprint invalidates the evidence-dependent plan;
    # both the selection and its dependent judgment must be recomputed.
    source = podcast / "raw" / "episode.mov"
    with source.open("ab") as handle:
        handle.write(b"source evidence changed")
    changed = _run(podcast, resume_mode=True)
    assert changed["failed"] == [], changed
    assert calls["asked"]["select_reels"] == 2
    assert calls["asked"]["judge_reels"] == 2
