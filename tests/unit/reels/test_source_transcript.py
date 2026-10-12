"""The source transcript and the reels request: the defects each guards.

The whole path, footage to proposals with no Resolve, is
`tests/scenarios/test_podcast_to_reels.py`. These pin the decisions that
path does not exercise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import (
    briefing_chat,
    reel_request,
    requirements,
    source_memory,
    source_transcript,
    timeline_transcript,
)


def _m1(digest: str, words: list, offset: float) -> None:
    """An M1 transcript of `words`, one per 0.4 s, starting at `offset`."""
    timed = [{"word": w, "start": round(offset + i * 0.4, 3),
              "end": round(offset + i * 0.4 + 0.3, 3)}
             for i, w in enumerate(words)]
    source_memory.write_json(
        source_memory.source_dir(digest) / source_memory.SLOT_TRANSCRIPT,
        {"content_digest": digest, "status": "transcribed",
         "utterances": [{"start": timed[0]["start"], "end": timed[-1]["end"],
                         "text": " ".join(words), "words": timed}],
         "word_count": len(timed)})


def test_a_synced_multicam_pair_is_one_conversation_not_two():
    """Two angles of one podcast each heard both voices; laid end to end
    they would offer every reel twice. The conversation clock groups them
    and only the angle with the most words is placed."""
    words = [f"w{i}" for i in range(80)]
    _m1("a" * 64, words, offset=0.0)
    _m1("b" * 64, words[:70], offset=2.0)
    _m1("c" * 64, [f"other{i}" for i in range(30)], offset=0.0)
    sources = [{"clip_id": "clip_001", "source_file": "/x/a.mov",
                "content_digest": "a" * 64},
               {"clip_id": "clip_002", "source_file": "/x/b.mov",
                "content_digest": "b" * 64},
               {"clip_id": "clip_003", "source_file": "/x/c.mov",
                "content_digest": "c" * 64}]
    placed = source_transcript._place(sources)
    assert [s["clip_id"] for s in placed] == ["clip_001", "clip_003"]
    assert sources[1]["angle_of"] == "a" * 64
    assert "another angle" in sources[1]["why_not_placed"]


def test_a_row_is_cut_where_the_voice_changes():
    """M1 has no speakers; one utterance can hold both voices. Each run of
    one voice's words becomes its own row, on the virtual timeline."""
    utterance = {"text": "yes. no way",
                 "words": [{"word": "yes", "start": 1.0, "end": 1.3},
                           {"word": "no", "start": 2.0, "end": 2.2},
                           {"word": "way", "start": 2.3, "end": 2.6}]}
    voices = [{"label": "speaker_01", "spans": [[0.5, 1.6]]},
              {"label": "speaker_02", "spans": [[1.9, 3.0]]}]
    rows = source_transcript._rows_by_voice([utterance], voices, offset=10.0)
    assert [r["text"] for r in rows["speaker_01"]] == ["yes"]
    assert [r["text"] for r in rows["speaker_02"]] == ["no way"]
    assert rows["speaker_02"][0]["start"] == 12.0


def _project(tmp_path: Path, files=("episode.mov",)) -> Path:
    folder = tmp_path / "p"
    (folder / "raw").mkdir(parents=True)
    for name in files:
        (folder / "raw" / name).write_bytes(name.encode() * 64)
    return folder


def test_a_transcript_of_the_cut_is_never_replaced(tmp_path):
    """The captain's timeline is the authoritative clock: a document a
    Resolve timeline produced is returned as it is, never re-heard."""
    folder = _project(tmp_path)
    path = timeline_transcript.transcript_path(folder)
    path.parent.mkdir(parents=True)
    cut = {"derived_from": {"timeline": "Master"}, "segments": []}
    path.write_text(json.dumps(cut), encoding="utf-8")
    assert source_transcript.ensure(str(folder)) == cut


def test_a_changed_file_makes_the_source_transcript_stale(tmp_path):
    """The words of a replaced file must not be served as the new one's."""
    folder = _project(tmp_path)
    sources = source_transcript.footage(str(folder))
    document = {"derived_from": {"kind": "source", "sources": sources}}
    assert source_transcript.is_current(document, str(folder))
    (folder / "raw" / "episode.mov").write_bytes(b"re-recorded" * 64)
    assert not source_transcript.is_current(document, str(folder))


def test_no_footage_refuses_with_a_fix(tmp_path):
    folder = tmp_path / "empty"
    (folder / "raw").mkdir(parents=True)
    with pytest.raises(source_transcript.SourceTranscriptRefused) as refused:
        source_transcript.ensure(str(folder))
    assert "raw/" in refused.value.fix


def test_the_requirement_is_met_by_footage_and_still_refuses_without(
        tmp_path):
    """Selection no longer needs a hand-made transcript, but a project
    with neither a transcript nor footage is still refused before the
    run starts - the gate can fail."""
    check = requirements._timeline_transcript_on_file
    with_footage = requirements.Context(project_folder=str(_project(tmp_path)))
    empty = tmp_path / "empty"
    (empty / "raw").mkdir(parents=True)
    assert check(with_footage).is_satisfied
    assert check(requirements.Context(project_folder=str(empty))).is_unsatisfied


def test_a_build_refuses_reels_chosen_on_the_source_clock():
    """Source seconds are not a master timeline's: placing them would cut
    the wrong picture."""
    source_transcript.refuse_build_from_source(
        {"derived_from": {"timeline": "Master"}})
    with pytest.raises(source_transcript.BuildNeedsATimeline):
        source_transcript.refuse_build_from_source(
            {"derived_from": {"kind": "source"}})


def test_a_reels_request_scopes_the_run_unless_the_person_named_one():
    request = reel_request.ReelRequest(count=3, about="pricing")
    assert reel_request.scope(request) == list(reel_request.REELS_STEPS)
    assert reel_request.scope(request, only=["catalog"]) == ["catalog"]
    assert reel_request.scope(request, target="rough_cut_subtitles") == []
    assert reel_request.scope(reel_request.ReelRequest()) == []


def test_the_request_reaches_the_brief_as_answers_before_the_interview(
        tmp_path):
    from library.tools.project_layout import ProjectLayout

    folder = tmp_path / "p"
    folder.mkdir()
    state = {"brief_answers": [{"question": "Who is it for?",
                                "answer": "founders"}]}
    reel_request.record(state, reel_request.ReelRequest(3, "pricing"))
    Path(ProjectLayout(str(folder)).pipeline_data_path).write_text(
        json.dumps(state), encoding="utf-8")
    answers = briefing_chat.answers_for(str(folder))
    assert answers == [
        {"question": reel_request.COUNT_QUESTION, "answer": "3"},
        {"question": reel_request.TOPIC_QUESTION, "answer": "pricing"},
        {"question": "Who is it for?", "answer": "founders"}]
