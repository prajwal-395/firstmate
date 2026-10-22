"""R06: semantic verification is an explicit typed stage - offline.

Proves `library/tools/retrieval/semantic_verify.py` and its two
consumers against injected judges (no model, no media):

* structural validity and semantic validity are separate fields, and
  the top-level verdict is the semantic one;
* a span the file cannot hold is never put to the model;
* only an explicit "no" rejects - anything else is `uncertain`;
* an edit-critical event is verified only with refined timestamps;
* verification is cached by candidate identity, model, task and stage
  version;
* planning labels a structural fill as a verified CLIP RANGE, never as
  a visually verified action.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest

from library.tools import search_in_planning as sip
from library.tools.retrieval import semantic_verify as sv
from library.tools.retrieval import temporal_rerank as tr


@pytest.fixture(autouse=True)
def _isolated_stage_cache():
    """One test, one cache: verdicts never leak between questions."""
    sv.clear_memory_cache()
    yield
    sv.clear_memory_cache()


def _catalog():
    return [
        {"clip_id": "clip_001", "duration_seconds": 200.0},
        {"clip_id": "clip_002", "duration_seconds": 10.0},
    ]


def _frames(start=1.0, n=4, step=1.0):
    return [{"t": round(start + i * step, 3),
             "file": f"/frames/{round(start + i * step, 3)}.jpg"}
            for i in range(n)]


def _judge(answer="yes", **extra):
    calls = []

    def verify(candidates, statement):
        calls.append((list(candidates), statement))
        return [{"answer": answer, "reason": "the test judge spoke",
                 "seconds": 0.5, "cached": False, **extra}
                for _ in candidates]

    verify.calls = calls
    return verify


# ── The two halves stay named ───────────────────────────────────────


def test_a_structurally_sound_but_rejected_span_is_not_verified():
    """The picture said no: the verdict is rejected, the span stands."""
    out = sv.verify_candidate(
        {"clip_id": "clip_001", "start": 1.0, "end": 5.0,
         "frames": _frames()},
        {"statement": "the open laptop on the desk"},
        _catalog(), task="appearance", verify=_judge("no"),
        model_id="test-model")
    assert out["verdict"] == "rejected"
    assert out["structural"] == {
        "ok": True,
        "reason": None,
        "what": ("the span lies inside the source file: "
                 "a verified clip range, never a verified action"),
    }
    assert out["semantic"]["verdict"] == "rejected"
    assert out["semantic"]["task"] == "appearance"


def test_a_span_the_file_cannot_hold_is_never_put_to_the_model():
    """Structural failure short-circuits: the judge is not asked."""
    judge = _judge("yes")
    out = sv.verify_candidate(
        {"clip_id": "clip_002", "start": 0.0, "end": 50.0,
         "frames": _frames()},
        {"statement": "the open laptop on the desk"},
        _catalog(), task="appearance", verify=judge,
        model_id="test-model")
    assert out["verdict"] == "uncertain"
    assert out["structural"]["ok"] is False
    assert out["semantic"]["verdict"] == "uncertain"
    assert "not put to the model" in out["semantic"]["reason"]
    assert judge.calls == []


def test_only_an_explicit_no_rejects():
    """An unparseable answer is uncertainty, not evidence of absence."""
    out = sv.verify_candidate(
        {"clip_id": "clip_001", "start": 1.0, "end": 5.0,
         "frames": _frames()},
        {"statement": "the open laptop on the desk"},
        _catalog(), task="appearance",
        verify=_judge("maybe"), model_id="test-model")
    assert out["verdict"] == "uncertain"
    assert out["semantic"]["verdict"] == "uncertain"
    assert out["cost"] == {"calls": 1, "cached_calls": 0, "seconds": 0.5}


def test_a_failing_judge_is_uncertainty_not_a_rejection():
    def verify(candidates, statement):
        raise RuntimeError("the model fell over")

    out = sv.verify_candidate(
        {"clip_id": "clip_001", "start": 1.0, "end": 5.0,
         "frames": _frames()},
        {"statement": "the open laptop on the desk"},
        _catalog(), task="appearance", verify=verify,
        model_id="test-model")
    assert out["verdict"] == "uncertain"
    assert "the judge failed" in out["semantic"]["reason"]


# ── Edit-critical events need timestamps ────────────────────────────


def test_an_established_event_carries_its_refined_timestamps():
    """The editor cuts on the refinement, inside the candidate."""
    out = sv.verify_candidate(
        {"clip_id": "clip_001", "start": 100.0, "end": 120.0,
         "frames": _frames(100.0, n=4, step=1.0)},
        {"statement": "someone opens the laptop"},
        _catalog(), task="event",
        verify=_judge("yes", frame_t=102.0), model_id="test-model")
    assert out["verdict"] == "verified"
    assert out["refined"] is not None
    assert 100.0 <= out["refined"]["start"] <= 102.0 <= out["refined"]["end"] <= 120.0
    assert out["refined"]["end"] - out["refined"]["start"] < 20.0


def test_an_event_yes_with_no_timestamp_is_not_a_verification():
    """A yes that names no frame leaves nothing the editor can cut on."""
    out = sv.verify_candidate(
        {"clip_id": "clip_001", "start": 100.0, "end": 120.0,
         "frames": []},
        {"statement": "someone opens the laptop"},
        _catalog(), task="event", verify=_judge("yes"),
        model_id="test-model")
    assert out["verdict"] == "uncertain"
    assert "no timestamp the editor can cut on" in out["semantic"]["reason"]
    assert out["refined"] is None


def test_a_non_event_does_not_need_a_timestamp():
    """Appearances are judged on the frame, not on a moment in time."""
    out = sv.verify_candidate(
        {"clip_id": "clip_001", "start": 1.0, "end": 5.0,
         "frames": _frames()},
        {"statement": "the open laptop on the desk"},
        _catalog(), task="appearance", verify=_judge("yes"),
        model_id="test-model")
    assert out["verdict"] == "verified"
    assert out["refined"] is None


# ── The cache ───────────────────────────────────────────────────────


def test_the_second_identical_question_is_free():
    """Same candidate, model, task and version: the judge is asked once."""
    sv.clear_memory_cache()
    judge = _judge("yes")
    candidate = {"clip_id": "clip_001", "start": 1.0, "end": 5.0,
                 "frames": _frames()}
    first = sv.verify_candidate(candidate, {"statement": "the laptop"},
                                _catalog(), task="appearance",
                                verify=judge, model_id="test-model")
    second = sv.verify_candidate(candidate, {"statement": "the laptop"},
                                 _catalog(), task="appearance",
                                 verify=judge, model_id="test-model")
    assert len(judge.calls) == 1
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert second["cost"]["calls"] == 0
    assert second["cost"]["cached_calls"] == 1
    assert second["verdict"] == first["verdict"] == "verified"
    sv.clear_memory_cache()


def test_task_and_model_are_part_of_the_identity():
    """A verdict given as appearance is never read as action."""
    sv.clear_memory_cache()
    judge = _judge("yes")
    candidate = {"clip_id": "clip_001", "start": 1.0, "end": 5.0,
                 "frames": _frames()}
    sv.verify_candidate(candidate, {"statement": "the laptop"},
                        _catalog(), task="appearance",
                        verify=judge, model_id="model-a")
    sv.verify_candidate(candidate, {"statement": "the laptop"},
                        _catalog(), task="action",
                        verify=judge, model_id="model-a")
    sv.verify_candidate(candidate, {"statement": "the laptop"},
                        _catalog(), task="appearance",
                        verify=judge, model_id="model-b")
    assert len(judge.calls) == 3, (
        "task and model are cache dimensions: a new one asks again")
    sv.clear_memory_cache()


def test_the_evidence_names_what_the_verdict_was_about():
    out = sv.verify_candidate(
        {"clip_id": "clip_001", "start": 1.0, "end": 5.0,
         "frames": _frames()},
        {"statement": "the open laptop on the desk"},
        _catalog(), task="appearance",
        verify=_judge("yes", prompt_version=7, reply='{"answer":"yes"}'),
        model_id="test-model")
    evidence = out["evidence"]
    assert evidence["statement"] == "the open laptop on the desk"
    assert evidence["model"] == "test-model"
    assert evidence["prompt_version"] == 7
    assert evidence["frames_shown"] == [f["t"] for f in _frames()]
    assert evidence["frame_files"] == [f["file"] for f in _frames()]


# ── The temporal pass speaks the stage ──────────────────────────────


def _rerank_frames(times, clip="clip_001"):
    return {clip: [{"t": t, "file": f"/frames/{t}.jpg"} for t in times]}


def _rerank_ranges():
    return [{"clip_id": "clip_001", "filename": "A.MXF", "start": 100.0,
             "end": 120.0, "best_score": 0.30, "peak_t": 100.0,
             "n_frames": 3, "timecode": "01:40.000-02:00.000"}]


def test_an_unparseable_answer_is_uncertain_and_unpresented():
    """The pass counts it apart from rejections and never presents it."""
    def verify(candidates, statement):
        return [{"answer": "perhaps", "reason": "the judge mumbled",
                 "seconds": 1.0, "cached": False} for _ in candidates]

    result = tr.rerank(
        project_folder="/nowhere", query="the open laptop",
        ranges=_rerank_ranges(),
        frames_by_clip=_rerank_frames([100.0, 110.0, 120.0]),
        top_ranges=5, verify=verify)
    assert result["ranges"] == []
    assert result["cost"]["uncertain"] == 1
    assert result["cost"]["rejected"] == 0
    assert result["candidates"][0]["verdict"] == "uncertain"


def test_an_event_yes_names_its_refined_span_on_the_window():
    """The presented window carries WHEN, beside the shot it presents."""
    def verify(candidates, statement):
        return [{"answer": "yes", "reason": "she lifts the lid",
                 "frame_t": 102.0, "frames": [100.0, 101.0, 102.0, 103.0],
                 "seconds": 1.0, "cached": False} for _ in candidates]

    result = tr.rerank(
        project_folder="/nowhere",
        query="the moment someone opens the laptop",
        ranges=_rerank_ranges(),
        frames_by_clip=_rerank_frames([100.0, 101.0, 102.0, 103.0]),
        top_ranges=5, verify=verify)
    assert result["classification"]["event"] is True
    window = result["ranges"][0]
    assert window["start"] == 100.0 and window["end"] == 120.0, (
        "the presented shot is unchanged")
    refined = window["justification"]["refined_span"]
    assert 100.0 <= refined["start"] <= 102.0 <= refined["end"] <= 120.0


def test_an_event_yes_without_any_frame_evidence_is_uncertain():
    """No timestamp anywhere in the answer: the editor cannot cut on it."""
    def verify(candidates, statement):
        return [{"answer": "yes", "reason": "yes",
                 "seconds": 1.0, "cached": False} for _ in candidates]

    result = tr.rerank(
        project_folder="/nowhere",
        query="the moment someone opens the laptop",
        ranges=_rerank_ranges(),
        frames_by_clip=_rerank_frames([100.0, 101.0, 102.0, 103.0]),
        top_ranges=5, verify=verify)
    assert result["ranges"] == []
    assert result["candidates"][0]["verdict"] == "uncertain"


# ── Planning labels ─────────────────────────────────────────────────


def test_a_structural_fill_is_labelled_a_clip_range(tmp_path):
    """No judge ran: the fill says clip range, never verified picture."""
    import json

    project = tmp_path / "analysed"
    (project / "pipeline_output" / "scratch" / "footage_index").mkdir(
        parents=True)
    (project / "pipeline_output" / "scratch" / "footage_index"
     / "footage_index.json").write_text(json.dumps({
         "segments": [{
             "segment_id": "clip_001#action#000", "clip_id": "clip_001",
             "filename": "clip_001.MOV", "kind": "action",
             "start": 0.0, "end": 5.0, "duration": 5.0,
             "text": "wide view of parked cars panning right",
             "facets": {}}],
         "embed_model": "all-MiniLM-L6-v2",
         "embed_backend": "sentence-transformers",
         "embed_dimension": 0,
         "coverage": {"per_clip": {"clip_001": {"picture": True,
                                                "action": True}}},
     }), encoding="utf-8")
    result = sip.slot_search(
        str(project), "show me the parked cars", slot_kind="visual",
        mode="lexical", catalog=_catalog())
    assert result["status"] == "filled"
    assert result["verification"]["structural"] == "verified"
    assert result["verification"]["semantic"] == "not_run"
    assert result["verification"]["label"] == "verified clip range"


def test_a_rejected_candidate_is_not_placed(tmp_path):
    """The judge said no about the only candidate: the slot abstains."""
    import json

    project = tmp_path / "judged"
    (project / "pipeline_output" / "scratch" / "footage_index").mkdir(
        parents=True)
    (project / "pipeline_output" / "scratch" / "footage_index"
     / "footage_index.json").write_text(json.dumps({
         "segments": [{
             "segment_id": "clip_001#action#000", "clip_id": "clip_001",
             "filename": "clip_001.MOV", "kind": "action",
             "start": 0.0, "end": 5.0, "duration": 5.0,
             "text": "wide view of parked cars panning right",
             "facets": {}}],
         "embed_model": "all-MiniLM-L6-v2",
         "embed_backend": "sentence-transformers",
         "embed_dimension": 0,
         "coverage": {"per_clip": {"clip_001": {"picture": True,
                                                "action": True}}},
     }), encoding="utf-8")

    def verify(candidates, statement):
        return [{"answer": "no", "reason": "no cars in these frames",
                 "seconds": 0.1, "cached": False} for _ in candidates]

    result = sip.slot_search(
        str(project), "show me the parked cars", slot_kind="visual",
        mode="lexical", catalog=_catalog(), verify=verify)
    assert result["status"] == "abstained_unverified", result
    assert "rejected by the semantic judge" in result["reason"]


def test_an_uncertain_candidate_fills_as_a_clip_range(tmp_path):
    """The judge could not decide: the span places, said as a range."""
    import json

    project = tmp_path / "uncertain"
    (project / "pipeline_output" / "scratch" / "footage_index").mkdir(
        parents=True)
    (project / "pipeline_output" / "scratch" / "footage_index"
     / "footage_index.json").write_text(json.dumps({
         "segments": [{
             "segment_id": "clip_001#action#000", "clip_id": "clip_001",
             "filename": "clip_001.MOV", "kind": "action",
             "start": 0.0, "end": 5.0, "duration": 5.0,
             "text": "wide view of parked cars panning right",
             "facets": {}}],
         "embed_model": "all-MiniLM-L6-v2",
         "embed_backend": "sentence-transformers",
         "embed_dimension": 0,
         "coverage": {"per_clip": {"clip_001": {"picture": True,
                                                "action": True}}},
     }), encoding="utf-8")

    def verify(candidates, statement):
        return [{"answer": "maybe", "reason": "the judge mumbled",
                 "seconds": 0.1, "cached": False} for _ in candidates]

    result = sip.slot_search(
        str(project), "show me the parked cars", slot_kind="visual",
        mode="lexical", catalog=_catalog(), verify=verify)
    assert result["status"] == "filled", result
    assert result["verification"]["semantic"] == "uncertain"
    assert result["verification"]["label"] == "verified clip range"
