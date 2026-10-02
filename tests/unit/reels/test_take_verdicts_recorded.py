"""The model's take verdicts stick: `takes_dropped` becomes exclusions.

Step 3.04's handoff has always asked the model for `takes_dropped` -
"any repeated take you are choosing not to play" - and the post-bridge
never read it, so every verdict evaporated with the run. The
post-bridge now records each valid verdict as a keep exclusion
(authored `model`, with the reel slug and the reason on the record),
refuses invalid ones loudly, and never breaks the batch over either.

All projects live under `tmp_path` (AGENTS.md 8).
"""

from library.steps.step_3_04_select_reels import post_bridge
from library.tools import transcript_corrections as tc


def _words(start, end):
    out = []
    cursor = float(start)
    n = 0
    while cursor < float(end) - 1e-9:
        stop = min(cursor + 1.0, float(end))
        out.append({"word": f"word{n}", "start": cursor, "end": stop,
                    "timed": True})
        cursor = stop
        n += 1
    return out


def _transcript():
    return {"segments": [
        {"speaker": "Akshita", "text": "ten seconds of speech here",
         "timeline_start": 10.0, "timeline_end": 20.0,
         "resolve_item_id": "a", "source_file": "LC4932.MXF",
         "source_start": 0.0, "source_end": 10.0,
         "words": _words(10.0, 20.0)},
    ]}


def _survivor(entry, start=10.0, end=20.0, slug="test-reel"):
    return [(dict(entry), start, end, slug)]


def test_a_valid_verdict_is_recorded_with_its_reason(tmp_path):
    recorded, refused = post_bridge.record_take_verdicts(
        _survivor({"takes_dropped": [
            {"start": 12.0, "end": 15.0,
             "reason": "the second telling is tighter"}]}),
        _transcript(), str(tmp_path), 100.0)
    assert refused == []
    assert len(recorded) == 1
    assert recorded[0]["id"].startswith("lc-")
    stored = tc.keep_exclusions(str(tmp_path))
    assert len(stored) == 1
    assert stored[0]["author"] == "model"
    assert "test-reel" in stored[0]["reason"]
    assert "the second telling is tighter" in stored[0]["reason"]
    # A string verdict parses to the same record shape.
    recorded, refused = post_bridge.record_take_verdicts(
        _survivor({"takes_dropped": ["16-18 - second telling is tighter"]}),
        _transcript(), str(tmp_path), 100.0)
    assert refused == []
    assert [(r["start"], r["end"]) for r in recorded] == [(16.0, 18.0)]
    _an_exact_duplicate_is_not_re_recorded(tmp_path / "second")


def test_an_invalid_verdict_is_refused_and_nothing_stored(tmp_path):
    """No reason; the whole moment (that rejects the reel - `considered`'s
    job, not a take drop's); an edge through a word (the build would
    play half a word and jump); outside the timeline."""
    cases = [
        ({"start": 12.0, "end": 15.0}, None),
        ({"start": 10.0, "end": 20.0, "reason": "hate it"}, None),
        ({"start": 12.5, "end": 15.0, "reason": "tighter"}, "word"),
        ({"start": 200.0, "end": 205.0, "reason": "tighter"}, None),
    ]
    for verdict, said in cases:
        recorded, refused = post_bridge.record_take_verdicts(
            _survivor({"takes_dropped": [verdict]}),
            _transcript(), str(tmp_path), 100.0)
        assert recorded == [], verdict
        assert len(refused) == 1, verdict
        if said:
            assert said in refused[0]["reason"]
    assert tc.keep_exclusions(str(tmp_path)) == []


def _an_exact_duplicate_is_not_re_recorded(tmp_path):
    tmp_path.mkdir()
    tc.record_keep_exclusion(str(tmp_path), 12.0, 15.0,
                             "captain strike", author="captain")
    recorded, refused = post_bridge.record_take_verdicts(
        _survivor({"takes_dropped": [
            {"start": 12.0, "end": 15.0, "reason": "tighter"}]}),
        _transcript(), str(tmp_path), 100.0)
    assert recorded == []
    assert refused == []
    assert len(tc.keep_exclusions(str(tmp_path))) == 1
