"""A retrieval question is made of parts; the parts must be nameable.

Four defects this file exists to prevent, one test each family:

* **VI-001** - a compound question that names speech AND picture AND
  time was answered as ONE fused score, so nothing could say which part
  was satisfied.  `decompose` must name every modality the question
  names, keep the two clauses apart, and carry the ordinal in "the
  second time".
* **VI-005** - an empty search was an empty list whether the footage had
  been measured for the thing asked about or not.  A fully covered
  search may report a confirmed absence; a search whose lane never ran,
  or whose coverage is partial, may not.
* **VI-006** - a hit carried a clip and a timecode, so nobody could
  check it without hunting through the project.  Every hit must carry
  its interval, its frames and its source digest - and say so when there
  are no frames rather than showing an empty list.
* **VI-007** - two hits describing the same moment were two candidate
  shots, and an editor picking the second had picked the first.  They
  must collapse into one window that records what it absorbed, while two
  windows that merely TOUCH stay two.
"""

import pytest

from library.tools.retrieval import coverage as retrieval_coverage
from library.tools.retrieval import dedup, support
from library.tools.retrieval.requirements import (
    MODALITIES,
    RetrievalRequirement,
    UnknownRequirement,
    decompose,
    repeated_events,
    requirement_kinds,
)

# ─── VI-001: the plan names what was asked ────────────────────────


def test_a_compound_question_names_every_modality_it_mentions():
    plan = decompose(
        "the moment he explains the pricing rule, while the pricing slide is up")
    kinds = {requirement.key for requirement in plan.requirements}
    assert "textual:speech_content" in kinds, (
        "the clause that says what he explains is a requirement about words")
    assert "visual:object" in kinds, (
        "the clause that names the slide is a requirement about picture")
    assert "temporal:interval" in kinds, (
        "'while' is a claim about time, not a conjunction to discard")


def test_two_clauses_that_ask_the_same_thing_are_one_requirement():
    plan = decompose("an open laptop with its lid raised")
    state = [r for r in plan.requirements if r.key == "visual:state"]
    assert len(state) == 1, (
        "'open' and 'raised' are the same question about an object's "
        "state, asked in two clauses")
    assert set(state[0].cues) == {"open", "raised"}


def test_the_second_time_carries_its_ordinal():
    plan = decompose("the second time she opens the box")
    repetition = [r for r in plan.requirements if r.key == "temporal:repetition"]
    assert repetition, "'the second time' is a count, and a count is a requirement"
    assert repetition[0].ordinal == 2, (
        "the ordinal decides WHICH occurrence, and it is not decoration")


def test_a_query_naming_more_than_one_occurrence_asks_for_more_than_one():
    """VI-002: the candidate generation lets one source contribute a
    second window when the query asks for the OCCURRENCES.

    A query that names ONE occurrence asks for one window: which
    occurrence is a ranking question, and the ranking lane owns it.
    """
    assert repeated_events("a shot of the laptop on the desk") == 1
    assert repeated_events("the second time she opens the box") == 1
    assert repeated_events("the laptop on the desk, again") == 1
    assert repeated_events("every time the laptop is on the desk") == 2
    assert repeated_events("the laptop on the desk, twice") == 2


def test_a_connective_between_two_clauses_is_a_temporal_requirement():
    plan = decompose("the same person on the other camera before the demo")
    ordering = [r for r in plan.requirements if r.key == "temporal:ordering"]
    assert ordering, "'before' is a claim about order"
    assert ordering[0].relation == "before"


def test_a_question_that_names_nothing_still_asks_the_content():
    plan = decompose("everything about the invoicing policy")
    assert plan.requirements, (
        "a query always asks something; the honest reading of an "
        "unclassifiable query is the content of what is said")
    assert plan.requirements[0].key == "textual:speech_content"


def test_a_relational_requirement_is_never_answered_by_a_free_text_lane():
    plan = decompose("the same laptop on the other camera")
    relational = [r for r in plan.requirements if r.modality == "relational"]
    assert relational, "'the same laptop' is a relation across shots"
    assert relational[0].lane == "relational", (
        "a relation is answered by the project graph's named queries, "
        "never by a text lane that would return the word 'same'")


def test_an_unknown_modality_raises_rather_than_matching_nothing():
    with pytest.raises(UnknownRequirement):
        RetrievalRequirement(modality="olfactory", kind="smell")
    with pytest.raises(UnknownRequirement):
        requirement_kinds("olfactory")


def test_every_modality_has_at_least_one_kind_and_a_lane():
    from library.tools.retrieval.requirements import REQUIREMENT_LANE
    for modality in MODALITIES:
        assert requirement_kinds(modality), (
            f"{modality} is a modality with no kind would never be asked")
    for key in REQUIREMENT_LANE:
        modality, kind = key.split(":", 1)
        assert kind in requirement_kinds(modality)


# ─── VI-005: not found is two answers ──────────────────────────────


def _coverage(per_clip):
    return {"clips_in_catalog": len(per_clip), "per_clip": per_clip}


def test_a_fully_covered_search_may_report_a_confirmed_absence():
    plan = decompose("where does he talk about parking")
    sufficiency = retrieval_coverage.coverage_sufficiency(
        plan, _coverage({"clip_001": {"speech": True}}), lanes_ran=("text",))
    verdict = retrieval_coverage.classify_retrieval(
        plan, found=False, sufficiency=sufficiency)
    assert verdict["status"] == "confidently_absent"
    assert verdict["state"] == "not_observed", (
        "a completed search with no assertion either way is 'not observed', "
        "not 'absent'")


def test_a_lane_that_never_ran_cannot_claim_an_absence():
    plan = decompose("where does he talk about parking")
    sufficiency = retrieval_coverage.coverage_sufficiency(
        plan, _coverage({"clip_001": {"speech": True}}), lanes_ran=())
    verdict = retrieval_coverage.classify_retrieval(
        plan, found=False, sufficiency=sufficiency)
    assert verdict["status"] != "confidently_absent", (
        "the measurements exist on disk but nobody asked them")
    assert verdict["state"] == "not_analyzed", (
        "a lane that never ran means the question was not analysed, and "
        "the caller can fix that by running it")
    assert any("lane did not run" in reason
               for reason in sufficiency["reasons"])


def test_a_relational_requirement_blocks_a_confirmed_absence():
    plan = decompose("the same laptop on the other camera")
    sufficiency = retrieval_coverage.coverage_sufficiency(
        plan,
        _coverage({"clip_001": {"speech": True, "picture": True,
                                "object": True}}),
        lanes_ran=("text", "visual", "relational"))
    verdict = retrieval_coverage.classify_retrieval(
        plan, found=False, sufficiency=sufficiency)
    assert verdict["status"] != "confidently_absent", (
        "nothing measures whether the same laptop is on the other camera, "
        "so 'not there' cannot be claimed")
    assert verdict["state"] == "inconclusive"


def test_partial_coverage_is_inconclusive_not_absent():
    plan = decompose("where does he talk about parking")
    sufficiency = retrieval_coverage.coverage_sufficiency(
        plan,
        _coverage({"clip_001": {"speech": True}, "clip_002": {"speech": False}}),
        lanes_ran=("text",))
    verdict = retrieval_coverage.classify_retrieval(
        plan, found=False, sufficiency=sufficiency)
    assert verdict["status"] == "inconclusive"
    assert verdict["state"] == "inconclusive"


def test_a_requirement_whose_measurement_never_ran_anywhere_is_not_analysed():
    plan = decompose("the sign on the wall")
    sufficiency = retrieval_coverage.coverage_sufficiency(
        plan, _coverage({"clip_001": {"speech": True, "picture": True}}),
        lanes_ran=("text", "visual"))
    verdict = retrieval_coverage.classify_retrieval(
        plan, found=False, sufficiency=sufficiency)
    assert verdict["status"] == "not_yet_analysed"
    assert verdict["state"] == "not_analyzed"


# ─── VI-006: a result that cannot be checked is a claim ──────────


def test_a_hit_carries_its_interval_frames_and_source_digest():
    hit = {
        "segment_id": "clip_001#speech#000",
        "clip_id": "clip_001",
        "filename": "IMG_0001.MOV",
        "kind": "speech",
        "start": 4.0,
        "end": 9.5,
        "word_hits": [{"word": "parking", "start": 4.2, "end": 4.9}],
    }
    frames = [{"file": "/cache/clip_001/0006.jpg", "clip_id": "clip_001",
               "filename": "IMG_0001.MOV", "t": 6.0}]
    evidence = support.supporting_evidence(
        hit, source_digest="sha256:abc", file="IMG_0001.MOV", frames=frames)
    assert evidence["interval"] == {
        "clip_id": "clip_001", "start_s": 4.0, "end_s": 9.5,
        "duration_s": 5.5}
    assert evidence["source"]["source_digest"] == "sha256:abc"
    assert evidence["frames"] == [
        {"t": 6.0, "timecode": "00:06.000", "path": "/cache/clip_001/0006.jpg",
         "filename": "IMG_0001.MOV"}]
    assert evidence["words"][0]["word"] == "parking"
    assert "source:sha256:abc" in evidence["evidence_refs"]


def test_a_window_with_no_stills_says_so_rather_than_looking_empty():
    evidence = support.supporting_evidence(
        {"clip_id": "clip_001", "start": 4.0, "end": 9.5}, frames=[])
    assert evidence["frames"] == []
    assert evidence["frames_not_found"], (
        "an empty list reads as 'there are no stills'; the window may "
        "simply have none cached")


# ─── VI-007: two hits on one moment are one moment ────────────────


def test_two_windows_describing_the_same_moment_collapse_into_one():
    hits = [
        {"segment_id": "clip_001#speech#000", "clip_id": "clip_001",
         "kind": "speech", "start": 4.0, "end": 20.0, "score": 0.81},
        {"segment_id": "clip_001#action#001", "clip_id": "clip_001",
         "kind": "action", "start": 6.0, "end": 14.0, "score": 0.62},
    ]
    kept, report = dedup.dedupe_windows(hits)
    assert len(kept) == 1, (
        "the action window sits inside the speech window: one moment, "
        "described twice")
    assert report["merged"] == 1
    assert kept[0]["merged_from"], (
        "a merge is a claim that two things are the same thing, and it "
        "must say which things it merged")


def test_two_windows_that_merely_touch_stay_two():
    hits = [
        {"segment_id": "clip_001#speech#000", "clip_id": "clip_001",
         "kind": "speech", "start": 0.0, "end": 20.0, "score": 0.81},
        {"segment_id": "clip_001#action#001", "clip_id": "clip_001",
         "kind": "action", "start": 19.5, "end": 21.0, "score": 0.62},
    ]
    kept, report = dedup.dedupe_windows(hits)
    assert len(kept) == 2, (
        "a one-second overlap is not a redundant window; merging them "
        "would report the action as lasting as long as the speech")
    assert report["merged"] == 0


def test_windows_on_different_clips_are_never_merged():
    hits = [
        {"segment_id": "clip_001#speech#000", "clip_id": "clip_001",
         "kind": "speech", "start": 4.0, "end": 20.0, "score": 0.81},
        {"segment_id": "clip_002#speech#000", "clip_id": "clip_002",
         "kind": "speech", "start": 4.0, "end": 20.0, "score": 0.77},
    ]
    kept, report = dedup.dedupe_windows(hits)
    assert len(kept) == 2
    assert report["merged"] == 0


def test_a_hit_without_a_timeline_placement_is_passed_through_untouched():
    hits = [{"segment_id": "orphan", "kind": "ocr"}]
    kept, report = dedup.dedupe_windows(hits)
    assert kept == hits
    assert report["merged"] == 0


# ─── The lanes a plan names are the lanes that run ─────────────────


class _StubIndex:
    """The shipped lanes, with fixed answers, so the routing is visible."""

    def __init__(self, text_rows=(), ocr_hits=(), sound_hits=(),
                 ocr_error=None, sound_error=None):
        self.text_rows = list(text_rows)
        self.ocr_hits = list(ocr_hits)
        self.sound_hits = list(sound_hits)
        self.ocr_error = ocr_error
        self.sound_error = sound_error
        self.asked = []

    def search_report(self, query, top_k=5, mode="hybrid", floor=None):
        self.asked.append("text")
        return {
            "results": self.text_rows,
            "weak": [],
            "answer_status": "found" if self.text_rows else "not_found",
            "error": None,
            "degraded": None,
        }

    def ocr_search(self, query, top_k=5):
        self.asked.append("ocr")
        return {"hits": self.ocr_hits, "error": self.ocr_error,
                "available_texts": [], "abstained": not self.ocr_hits}

    def sound_search(self, query, top_k=5):
        self.asked.append("sound")
        return {"hits": self.sound_hits, "error": self.sound_error,
                "available_labels": [], "abstained": not self.sound_hits}


class _StubFrameIndex:
    def __init__(self, frames):
        self.frames = list(frames)

    def search_ranges(self, query, top_ranges=5, temporal=False):
        return {"ranges": [], "results": []}


def _search(stub, query, **kwargs):
    from library.tools.retrieval.search import search_footage
    kwargs.setdefault("coverage", _coverage({"clip_001": {
        "speech": True, "picture": True, "object": True, "ocr": True,
        "sound": True}}))
    kwargs.setdefault("sources", {"clip_001": {
        "source_digest": "sha256:abc", "file": "IMG_0001.MOV"}})
    kwargs.setdefault("frame_index", _StubFrameIndex([]))
    return search_footage(".", query, index=stub, **kwargs)


def test_only_the_lanes_the_plan_names_are_run():
    stub = _StubIndex()
    _search(stub, "where does he talk about parking")
    assert stub.asked == ["text"], (
        "a question about words must not wake the OCR or sound lanes")


def test_a_question_that_mentions_sound_asks_the_sound_lane():
    stub = _StubIndex()
    report = _search(stub, "where does he laugh")
    assert "sound" in stub.asked, (
        "a question that mentions sound must ask the sound lane")
    assert stub.asked == ["sound"], (
        "R02: the plan names no textual requirement, so no text "
        "embedding pass runs - transcript rows are not sound "
        "evidence, and the skipped lane says so in "
        f"{report['lanes'].get('text')}")


def test_a_relational_question_is_not_answered_by_a_text_lane_alone():
    stub = _StubIndex()
    report = _search(stub, "the same laptop on the other camera")
    relational = [r for r in report["requirements"]
                  if r["modality"] == "relational"]
    assert relational, "the plan names the relation"
    assert relational[0]["status"] == "needs_named_query", (
        "the project graph answers named questions; pretending a text "
        "lane answered the relation would be a false answer")


def test_the_answer_reports_a_confirmed_absence_when_the_coverage_is_whole():
    stub = _StubIndex()
    report = _search(stub, "where does he talk about parking")
    assert report["answer_status"] == "confidently_absent"
    assert report["answer_state"] == "not_observed"


def test_every_hit_carries_its_support():
    stub = _StubIndex(text_rows=[{
        "segment_id": "clip_001#speech#000", "clip_id": "clip_001",
        "filename": "IMG_0001.MOV", "kind": "speech", "start": 4.0,
        "end": 9.5, "score": 0.81, "text": "parking"}])
    report = _search(stub, "where does he talk about parking")
    hit = report["hits"][0]
    assert hit["support"]["interval"]["start_s"] == 4.0
    assert hit["support"]["source"]["source_digest"] == "sha256:abc"


class _RefusingFrameIndex:
    """A frame lane that refuses the question, as it does by design."""

    def __init__(self):
        self.frames = []

    def search_ranges(self, query, top_ranges=5, temporal=False):
        return {"ranges": [], "results": [],
                "refused": {"matched": "gesturing",
                            "reason": "frame search covers objects, people "
                                      "and scenes; 'gesturing' is an "
                                      "action/gesture query",
                            "hint": "the event pipeline measures actions"}}


def test_a_refused_frame_lane_is_reported_as_refused_not_as_an_observation():
    """VI-005: "not there" and "not looked for" are different answers.

    The frame lane refuses an action query by design.  The plan-driven
    search used to drop that refusal, so the lane read as one that ran
    and found nothing, and the requirement was reported as
    `not_observed` - a claim that the lane looked and saw nothing.  It
    did not look at all (measured 2026-10-20, see
    docs/benchmarks/2026-10-20-editorial-retrieval-benchmark.md).
    """
    from library.tools.retrieval.search import search_footage

    stub = _StubIndex()
    answer = search_footage(
        ".", "a person gesturing with both hands", index=stub,
        coverage=_coverage({"clip_001": {"speech": True, "picture": True,
                                         "object": True, "ocr": True,
                                         "sound": True}}),
        sources={"clip_001": {"source_digest": "sha256:abc",
                              "file": "IMG_0001.MOV"}},
        frame_index=_RefusingFrameIndex())
    visual = answer["lanes"]["visual"]
    assert visual["refused"] is not None, (
        "the refusal must reach the answer; it is the difference between "
        "'not there' and 'not looked for'")
    assert visual["ran"] is False, (
        "a lane that refused did not run")
    assert "visual" not in answer["lanes_ran"]
    statuses = {f"{requirement['modality']}:{requirement['kind']}":
                requirement["status"]
                for requirement in answer["requirements"]}
    assert statuses["visual:action"] == "refused"
    assert statuses["visual:action"] != "not_observed"
