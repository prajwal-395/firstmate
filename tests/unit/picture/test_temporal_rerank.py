"""The temporal pass: what it classifies, what it rejects, what it costs.

These tests name DEFECTS, and each one can fail:

* a query about an EVENT must not be answered by a frame that merely
  CONTAINS the thing;
* the claim put to the model must be the picture the editor asked for,
  not the editor's label for it;
* a candidate whose evidence does not establish the query must not be
  offered with a lower rank - it must not be offered;
* every presented window must carry the verdict that justified it;
* a window of no length is not an interval, and an interval is what the
  pass presents;
* the pass must stop when the editor's list is full, because it costs a
  model call per candidate;
* a claim about ORDER is a claim about two things, and one interval
  cannot carry it.
"""

import pytest

from library.tools.retrieval import temporal_rerank as tr

# ── The classification ──────────────────────────────────────────────


def test_a_query_about_an_event_is_an_action_and_not_an_appearance():
    """The punch list's acceptance case.

    "The moment someone opens the laptop" is a claim about something
    HAPPENING.  The frame lane ranks the frames of a laptop and calls
    that an answer; the classification has to say the claim is an
    action, so the pass judges an EVENT rather than the presence of a
    thing.
    """
    classification = tr.classify("the moment someone opens the laptop")
    assert "action_recognition" in classification["kinds"]
    assert classification["primary"] == "action_recognition"
    assert classification["event"] is True
    assert classification["statement"] == "someone opens the laptop"


def test_a_picture_of_a_thing_is_not_an_event():
    """The other half: "the open laptop on the desk" is a PICTURE.

    The word "open" is a state and an action alike, and the pass must
    judge it as what it is - a thing in a state - or every shot with a
    lid in it would be inspected as though an event were in it.
    """
    classification = tr.classify("a shot of the open laptop on the desk")
    assert classification["event"] is False
    assert "appearance_matching" in classification["kinds"]
    assert classification["statement"] == "the open laptop on the desk"


def test_the_claim_is_the_picture_and_not_the_editors_label_for_it():
    """"the woman wearing glasses, the host" asks for a PICTURE.

    The apposition names WHO, which no frame can establish, and putting
    it to the model only makes the model argue about who is hosting
    instead of looking at the woman.  The claim is the picture.
    """
    classification = tr.classify(
        "the shot of the woman wearing glasses, the host")
    assert classification["statement"] == "the woman wearing glasses"


def test_a_clause_joined_by_with_is_part_of_the_claim():
    """An apposition is a label; "with" is a second thing asked for.

    "the table with the plant behind it" asks for BOTH, so dropping the
    first would ask the model about the plant alone.
    """
    classification = tr.classify(
        "the table with the plant behind it in the host's shot")
    assert classification["statement"] == (
        "the table with the plant behind it in the host's shot")


def test_a_claim_about_order_is_not_answered_by_one_interval():
    """Order is a claim about TWO things, and the pass says so.

    An interval can establish what a shot shows; it cannot establish
    that this came before that.  The pass names the lane that owns the
    claim instead of answering it badly.
    """
    classification = tr.classify("the shot before the slide is up")
    assert "temporal_order" in classification["kinds"]
    assert classification["interval_evidence"] is False
    assert classification["owner_lanes"]["temporal_order"]


# ── The candidates ──────────────────────────────────────────────────


def _frames(times):
    return [{"t": t, "file": f"/frames/{t}.jpg"} for t in times]


def _ranges():
    return [
        {"clip_id": "clip_001", "filename": "A.MXF", "start": 100.0,
         "end": 120.0, "best_score": 0.30, "peak_t": 100.0, "n_frames": 3,
         "timecode": "01:40.000-02:00.000"},
        {"clip_id": "clip_001", "filename": "A.MXF", "start": 300.0,
         "end": 320.0, "best_score": 0.29, "peak_t": 300.0, "n_frames": 3,
         "timecode": "05:00.000-05:20.000"},
        {"clip_id": "clip_002", "filename": "B.MXF", "start": 50.0,
         "end": 70.0, "best_score": 0.28, "peak_t": 50.0, "n_frames": 3,
         "timecode": "00:50.000-01:10.000"},
    ]


def test_a_presented_window_carries_the_fields_the_lane_prints():
    """A window the pass presents is a window the editor scrubs.

    The human path prints the timecode of every window it is given, so
    a presented window that does not carry the lane's own fields crashes
    the report.  The pass presents WINDOWS, not a shape of its own.
    """
    frames = {"clip_001": _frames([100.0, 110.0, 120.0])}

    def verify(candidates, statement):
        return [{"answer": "yes", "reason": "the lid is up", "seconds": 1.0,
                 "cached": False} for _ in candidates]

    result = tr.rerank(project_folder="/nowhere", query="the open laptop",
                        ranges=_ranges()[:1], frames_by_clip=frames,
                        top_ranges=5, verify=verify)
    window = result["ranges"][0]
    for field in ("clip_id", "filename", "start", "end", "duration",
                  "timecode", "best_score", "n_frames", "verified"):
        assert field in window, f"the presented window must carry {field}"


def test_the_lane_rewrites_the_timecode_of_the_window_it_presents(monkeypatch):
    """The timecode must say the window the editor is shown.

    The pass may present an interval that is NOT the shot the index
    proposed - a shot of no length becomes the interval the pass
    inspected - so the timecode the lane prints has to be the one of the
    window, not of the shot behind it.
    """
    from library.tools.analysis import footage_frames

    frames = {"clip_001": _frames([100.0, 100.5, 101.0, 101.5])}
    ranges = [{"clip_id": "clip_001", "filename": "A.MXF", "start": 100.5,
               "end": 100.5, "best_score": 0.30, "peak_t": 100.5,
               "n_frames": 1, "timecode": "01:40.500-01:40.500"}]

    def fake_rerank(**kwargs):
        return {
            "ranges": [{"clip_id": "clip_001", "filename": "A.MXF",
                        "start": 100.0, "end": 101.5, "duration": 1.5,
                        "timecode": "01:40.500-01:40.500", "best_score": 0.3,
                        "n_frames": 4, "verified": True,
                        "justification": {"verdict": "yes", "reason": "lid up",
                                          "frames_shown": [100.0, 101.5],
                                          "statement": "the open laptop"}}],
            "candidates": [], "status": "ran",
            "cost": {"calls": 1, "cached_calls": 0, "candidates": 1,
                     "verified": 1, "rejected": 0, "seconds": 1.0,
                     "seconds_per_call": 1.0, "wall_seconds": 1.0},
            "classification": tr.classify("the open laptop")}

    monkeypatch.setattr(tr, "rerank", fake_rerank)
    monkeypatch.setattr(footage_frames.FrameIndex, "_source_frames",
                        lambda self, clip_ids: frames)
    index = footage_frames.FrameIndex.__new__(footage_frames.FrameIndex)
    index.project_folder = "/nowhere"
    out = index._temporal_pass("the open laptop", ranges, 5)
    window = out["ranges"][0]
    assert window["start"] == 100.0 and window["end"] == 101.5
    assert window["timecode"] == "01:40.000-01:41.500"


def test_a_candidate_whose_evidence_does_not_establish_the_query_is_rejected():
    """The rule the punch list states: REJECT, do not rerank.

    A candidate the evidence does not establish is not offered with a
    lower rank.  It is not offered.
    """
    frames = {"clip_001": _frames([100.0, 110.0, 120.0, 300.0, 310.0, 320.0]),
              "clip_002": _frames([50.0, 60.0, 70.0])}

    def verify(candidates, statement):
        return [{"answer": "no", "reason": "the lid is down",
                 "seconds": 1.0, "cached": False}
                for _ in candidates]

    result = tr.rerank(project_folder="/nowhere", query="the open laptop",
                        ranges=_ranges(), frames_by_clip=frames,
                        top_ranges=5, verify=verify)
    assert result["ranges"] == []
    assert result["cost"]["rejected"] == result["cost"]["candidates"]
    assert result["cost"]["verified"] == 0


def test_every_presented_window_carries_the_verdict_that_justified_it():
    """A window the pass presents says WHY it was presented."""
    frames = {"clip_001": _frames([100.0, 110.0, 120.0, 300.0, 310.0, 320.0]),
              "clip_002": _frames([50.0, 60.0, 70.0])}

    def verify(candidates, statement):
        return [{"answer": "yes", "reason": "the lid is up",
                 "seconds": 2.0, "cached": False}
                for _ in candidates]

    result = tr.rerank(project_folder="/nowhere", query="the open laptop",
                        ranges=_ranges(), frames_by_clip=frames,
                        top_ranges=5, verify=verify)
    assert result["ranges"], "a verified candidate must be presented"
    for window in result["ranges"]:
        assert window["justification"]["verdict"] == "yes"
        assert window["justification"]["reason"] == "the lid is up"
        assert window["justification"]["frames_shown"]
        assert window["verified"] is True


def test_the_pass_does_not_re_shape_the_lane_s_window():
    """The pass SELECTS and JUSTIFIES; it does not re-shape the window.

    A shot of no length is a shot of no length, and the pass presents
    it as it is.  Widening it to the span of the frames that were
    inspected changes the window the editor is shown - and the seconds
    the consequence metric prices - for a reason that has nothing to do
    with whether the query was established.  The frames the pass looked
    at travel with the window as its justification.
    """
    frames = {"clip_001": _frames([100.0, 100.5, 101.0, 101.5])}
    ranges = [{"clip_id": "clip_001", "filename": "A.MXF", "start": 100.5,
               "end": 100.5, "best_score": 0.30, "peak_t": 100.5,
               "n_frames": 1, "timecode": "01:40.500-01:40.500"}]

    def verify(candidates, statement):
        return [{"answer": "yes", "reason": "there it is", "seconds": 1.0,
                 "cached": False} for _ in candidates]

    result = tr.rerank(project_folder="/nowhere", query="the open laptop",
                        ranges=ranges, frames_by_clip=frames, top_ranges=5,
                        verify=verify)
    assert result["ranges"], "the candidate was verified"
    window = result["ranges"][0]
    assert window["start"] == 100.5 and window["end"] == 100.5, (
        "the pass must present the shot the lane proposed, unchanged")
    # The frames it inspected travel with the window as its
    # justification, so the reader can see what the verdict was about.
    assert window["justification"]["frames_shown"] == [100.0, 100.5, 101.0,
                                                        101.5]


def test_the_pass_stops_when_the_editors_list_is_full():
    """The pass costs a model call per candidate, so it must stop.

    Two cameras with three candidates each would be six calls if the
    pass inspected everything; a list of two windows is full after the
    first camera's two candidates are established, and the rest of the
    budget is not spent.
    """
    frames = {"clip_001": _frames([100.0, 110.0, 120.0, 300.0, 310.0, 320.0]),
              "clip_002": _frames([50.0, 60.0, 70.0])}
    asked = []

    def verify(candidates, statement):
        asked.extend(candidates)
        return [{"answer": "yes", "reason": "yes", "seconds": 1.0,
                 "cached": False} for _ in candidates]

    result = tr.rerank(project_folder="/nowhere", query="the open laptop",
                        ranges=_ranges(), frames_by_clip=frames, top_ranges=2,
                        verify=verify)
    assert len(result["ranges"]) == 2
    assert len(asked) == 2, "the pass must stop when the list is full"


def test_the_cost_of_the_pass_is_recorded():
    """The incremental cost of verification is part of the answer.

    A pass whose cost is invisible is a pass nobody can decide to keep.
    """
    frames = {"clip_001": _frames([100.0, 110.0, 120.0])}

    def verify(candidates, statement):
        return [{"answer": "yes", "reason": "yes", "seconds": 3.5,
                 "cached": False} for _ in candidates]

    result = tr.rerank(project_folder="/nowhere", query="the open laptop",
                        ranges=_ranges()[:1], frames_by_clip=frames,
                        top_ranges=5, verify=verify)
    cost = result["cost"]
    assert cost["calls"] == 1
    assert cost["cached_calls"] == 0
    assert cost["seconds"] == 3.5
    assert cost["seconds_per_call"] == 3.5
    assert cost["candidates"] == 1
    assert cost["verified"] == 1


def test_a_cached_verdict_costs_nothing_the_second_time():
    """The same question over the same footage is free the second time."""
    frames = {"clip_001": _frames([100.0, 110.0, 120.0])}

    def verify(candidates, statement):
        return [{"answer": "yes", "reason": "yes", "seconds": 3.5,
                 "cached": True} for _ in candidates]

    result = tr.rerank(project_folder="/nowhere", query="the open laptop",
                        ranges=_ranges()[:1], frames_by_clip=frames,
                        top_ranges=5, verify=verify)
    assert result["cost"]["calls"] == 0
    assert result["cost"]["cached_calls"] == 1


def test_nothing_to_inspect_means_the_pass_did_not_run():
    """A project with no measured frame sample cannot be inspected.

    The pass says so and leaves the ranked candidates alone, rather
    than rejecting every candidate and answering with nothing.
    """
    result = tr.rerank(project_folder="/nowhere", query="the open laptop",
                        ranges=_ranges(), frames_by_clip={}, top_ranges=5)
    assert result["status"] == "unavailable"
    assert result["ranges"] == []


# ── The lane's own use of the pass ──────────────────────────────────


def test_the_lane_carries_the_pass_record_and_presents_its_windows(monkeypatch):
    """`ren search --visual` answers with windows the evidence supports.

    The lane's report carries the pass: what it classified, what it
    inspected and what it cost, so a reader can see that the windows
    are established rather than ranked.
    """
    from library.tools.analysis import footage_frames
    from library.tools.retrieval import temporal_rerank

    def fake_rerank(**kwargs):
        return {
            "ranges": [{"clip_id": "clip_001", "filename": "A.MXF",
                        "start": 1.0, "end": 2.0, "duration": 1.0,
                        "best_score": 0.3, "n_frames": 2, "verified": True,
                        "justification": {"verdict": "yes", "reason": "lid up",
                                          "frames_shown": [1.0, 2.0],
                                          "statement": "the open laptop"}}],
            "candidates": [], "status": "ran",
            "cost": {"calls": 1, "cached_calls": 0, "candidates": 1,
                     "verified": 1, "rejected": 0, "seconds": 2.0,
                     "seconds_per_call": 2.0, "wall_seconds": 2.0},
            "classification": temporal_rerank.classify("the open laptop")}

    monkeypatch.setattr(temporal_rerank, "rerank", fake_rerank)
    monkeypatch.setattr(footage_frames.FrameIndex, "_source_frames",
                        lambda self, clip_ids: {})
    index = footage_frames.FrameIndex.__new__(footage_frames.FrameIndex)
    index.project_folder = "/nowhere"
    out = index._temporal_pass("the open laptop", _ranges()[:1], 5)
    assert out["temporal_pass"]["status"] == "ran"
    assert out["ranges"][0]["justification"]["verdict"] == "yes"
    assert out["temporal_pass"]["cost"]["calls"] == 1
    assert out["temporal_pass"]["classification"]["statement"] == (
        "the open laptop")


def test_the_lane_presents_nothing_when_no_candidate_is_established(monkeypatch):
    """No established candidate means NO windows, not the ranked ones.

    The lane's own rule is that a window it presents is a window it can
    stand behind.  When the pass establishes nothing, the answer is the
    absence of an answer - and the report says why.
    """
    from library.tools.analysis import footage_frames
    from library.tools.retrieval import temporal_rerank

    def fake_rerank(**kwargs):
        return {"ranges": [], "candidates": [], "status": "ran",
                "cost": {"calls": 3, "cached_calls": 0, "candidates": 3,
                         "verified": 0, "rejected": 3, "seconds": 9.0,
                         "seconds_per_call": 3.0, "wall_seconds": 9.0},
                "classification": temporal_rerank.classify("the open laptop")}

    monkeypatch.setattr(temporal_rerank, "rerank", fake_rerank)
    monkeypatch.setattr(footage_frames.FrameIndex, "_source_frames",
                        lambda self, clip_ids: {})
    monkeypatch.setattr(footage_frames.FrameIndex, "_frame_scope",
                        lambda self: {"complete": True, "note": "the frames"})
    index = footage_frames.FrameIndex.__new__(footage_frames.FrameIndex)
    index.project_folder = "/nowhere"
    index._scope = None
    out = index._temporal_pass("the open laptop", _ranges()[:1], 5)
    assert out["ranges"] == []
    assert out["temporal_pass"]["status"] == "no_established_interval"
    assert "rejected" in out["temporal_pass"]["reason"]
    # The lane's own abstention vocabulary: it RAN and found nothing,
    # which is not the same answer as "it did not look".
    assert out["abstained"] is True
    assert out["answer_status"] == "abstained"
    assert out["abstention"]["outcome"] == "not_observed"
    assert out["abstention"]["answer_state"]


@pytest.mark.parametrize("query,expected", [
    ("the moment someone opens the laptop", "someone opens the laptop"),
    ("a shot of the open laptop on the desk", "the open laptop on the desk"),
])
def test_the_statement_is_the_editors_own_words(query, expected):
    assert tr.classify(query)["statement"] == expected
