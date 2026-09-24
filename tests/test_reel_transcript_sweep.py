"""The reel sweep proposes sounds, never words - and records nothing unasked.

`library/tools/reel_transcript_sweep.py` (the captain's Reel 17 note):
deterministic filler/fragment shapes over the words APPROVED reels
actually play, grouped across reels, recorded PENDING for the captain
to promote or retire. These tests prove the boundary (a real word
stays however awkward), the reel scoping (a stray sound no reel plays
is not proposed), the propose-and-accept shape (preview records
nothing; apply holds PENDING, never enforced), and the guards (no
double-proposals, no global single letters, spelling owns its tokens).
"""

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from library.tools import reel_transcript_sweep as sw


def _project(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "learned_context"), exist_ok=True)
    with open(os.path.join(root, "learned_context", "learnings.json"),
              "w", encoding="utf-8") as handle:
        json.dump([], handle)
    return root


def _word(word, start, end):
    return {"word": word, "start": start, "end": end, "timed": True}


def _doc():
    return {"segments": [
        {"speaker": "Craig",
         "text": "so um we launched the thing",
         "timeline_start": 10.0, "timeline_end": 14.0,
         "words": [_word("so", 10.0, 10.2),
                   _word("um", 10.2, 10.5),
                   _word("we", 10.5, 10.7),
                   _word("launched", 10.7, 11.1),
                   _word("the", 11.1, 11.3),
                   _word("thing", 11.3, 11.7)]},
        {"speaker": "Akshita",
         "text": "yeah s exactly what happened",
         "timeline_start": 20.0, "timeline_end": 24.0,
         "words": [_word("yeah", 20.0, 20.4),
                   _word("s", 20.4, 20.5),
                   _word("exactly", 20.5, 21.0),
                   _word("what", 21.0, 21.2),
                   _word("happened", 21.2, 21.7)]},
        {"speaker": "Craig",
         "text": "nowhere near any reel uh oh",
         "timeline_start": 100.0, "timeline_end": 104.0,
         "words": [_word("nowhere", 100.0, 100.5),
                   _word("near", 100.5, 100.8),
                   _word("any", 100.8, 101.0),
                   _word("reel", 101.0, 101.3),
                   _word("uh", 101.3, 101.5),
                   _word("oh", 101.5, 101.8)]},
    ]}


def _moment(number, start, end):
    return SimpleNamespace(number=number, slug=f"moment-{number}",
                           timeline_start=float(start),
                           timeline_end=float(end),
                           call_to_action=None)


def test_classify_token_names_sounds_never_words():
    assert sw.classify_token("um", "um") == "filler"
    assert sw.classify_token("uh", "uh") == "filler"
    assert sw.classify_token("s", "s") == "fragment"
    assert sw.classify_token("goin", "goin-") == "truncated"
    # Real words stay: short, awkward, emphatic - all None.
    assert sw.classify_token("so", "so") is None
    assert sw.classify_token("oh", "oh") is None
    assert sw.classify_token("yeah", "yeah") is None
    assert sw.classify_token("qu", "qu") is None
    # Dictionary singletons are words, never fragments.
    assert sw.classify_token("a", "a") is None
    assert sw.classify_token("I", "I") is None


def test_sweep_covers_played_words_only(tmp_path):
    project = _project(tmp_path)
    report = sw.sweep_moments([_moment(17, 10.0, 24.0)], _doc(), project)
    assert report["reels_swept"] == 1
    heard = {(c["shape"], c["heard"]) for c in report["candidates"]}
    # The played "um" (global filler) and the played stray "s"
    # (anchored fragment) propose; the unplayed "uh" at 100s does not.
    assert ("filler", "um") in heard
    assert ("fragment", "s") in heard
    assert not [c for c in report["candidates"]
                if c["heard"] == "uh"]
    # Scopes follow the practice: fillers global, fragments anchored.
    by_heard = {c["heard"]: c for c in report["candidates"]}
    assert by_heard["um"]["scope"] is None
    assert by_heard["s"]["scope"] == {"speaker": "Akshita",
                                      "surface": "s",
                                      "prev": "yeah", "next": "exactly"}


def test_apply_holds_pending_never_enforced(tmp_path):
    from library.tools import learned_context as lc
    from library.tools import transcript_corrections as tc

    project = _project(tmp_path)
    report = sw.scan(project, moments=[_moment(17, 10.0, 24.0)],
                     transcript=_doc(), apply=True)
    assert len(report["recorded"]) == 2
    # Held, never enforced: no active suppression, words stand.
    assert tc.suppressions(project) == []
    assert len(lc.pending(project)) == 2
    doc = _doc()
    tc.apply_to_document(doc, project)
    assert "um" in doc["segments"][0]["text"].split()
    # Honest attribution: the pipeline wears its own name.
    assert {l["kind"] for l in lc.pending(project)} == {"mistake_fix"}
    # Promotion enforces from the next run.
    for held_id in report["recorded"]:
        lc.promote(project, held_id, reason="Captain: yes.")
    assert sorted(s["heard"] for s in tc.suppressions(project)) == [
        "s", "um"]


def test_second_sweep_proposes_nothing_recorded(tmp_path):
    project = _project(tmp_path)
    first = sw.scan(project, moments=[_moment(17, 10.0, 24.0)],
                    transcript=_doc(), apply=True)
    assert first["recorded"]
    second = sw.scan(project, moments=[_moment(17, 10.0, 24.0)],
                     transcript=_doc(), apply=True)
    assert second["candidates"] == []
    assert second["recorded"] == []
    assert second["skipped"].get("already recorded (active or pending)") \
        == 2


def test_sentence_edge_fragment_is_held_never_global(tmp_path):
    project = _project(tmp_path)
    doc = {"segments": [
        {"speaker": "Craig", "text": "f same story",
         "timeline_start": 10.0, "timeline_end": 12.0,
         "words": [_word("f", 10.0, 10.2),
                   _word("same", 10.2, 10.5),
                   _word("story", 10.5, 10.9)]}]}
    report = sw.scan(project, moments=[_moment(17, 10.0, 12.0)],
                     transcript=doc, apply=True)
    assert report["candidates"] == []
    assert len(report["held_for_captain"]) == 1
    assert report["recorded"] == []


def test_spelling_owned_token_is_not_proposed(tmp_path):
    from library.tools import transcript_corrections as tc

    project = _project(tmp_path)
    tc.record_spelling(project, "s", "see",
                       reason="Captain: she said see.")
    report = sw.sweep_moments([_moment(17, 10.0, 24.0)], _doc(), project)
    assert not [c for c in report["candidates"] if c["heard"] == "s"]
    assert report["skipped"].get("owned by an active respell") == 1


def test_scan_refuses_without_approved_moments(tmp_path):
    import pytest

    project = _project(tmp_path)
    with pytest.raises(sw.SweepRefused):
        sw.scan(project, moments=[], transcript=_doc(), apply=False)
