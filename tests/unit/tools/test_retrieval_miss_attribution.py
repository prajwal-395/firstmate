"""A retrieval miss is not one thing, and every miss says which thing.

**VI-001** - the punch list asks for the visual retrieval deficit to be
diagnosed AT EACH STAGE: extraction coverage, indexing, candidate
generation, ranking, temporal localization, result presentation, and the
floor that removes a candidate.  Acceptance: *every missed evaluation
query has an attributable failure category.*

The defects named here, one test each:

* a miss that cannot say WHICH stage lost it (it must, and it must be
  one of the enumerated stages, not a free-text guess);
* a target that was never DECODED reported as a ranking miss (the two
  are different defects with different owners);
* a target that was decoded and never EMBEDDED reported as a ranking
  miss (same);
* a target that was indexed and never POOLED reported as a ranking miss
  (same - the pool is a cut, and naming it is naming where the cut is);
* a target the CLIP ranking put below the clip's own window reported as
  a presentation miss (the ranking lost it, not the presentation);
* a target found on the OTHER ANGLE reported as lost (the localization
  landed on the wrong camera, and that is its own stage);
* a target the FLOOR turned away reported as a ranking miss (the floor
  is not the ranking);
* a target the search FOUND but the credit rule did not count reported
  as a retrieval failure (the metric's rule is not the search's
  failure, and blaming the search sends the reader to fix the wrong
  thing);
* an unlabelled, refused or absent entry borrowing a failure stage from
  a present one (it has no wanted moment to lose).
"""

from library.tools import retrieval_eval
from library.tools.retrieval import miss_attribution

# ── the fixtures ───────────────────────────────────────────────────


def _query(asset="LC4930", start=10.0, end=20.0, lane="visual"):
    return {"id": "Q1", "family": "editorial", "lane": lane,
            "category": "object", "expect": "present",
            "query": "the open laptop on the desk",
            "moments": [{"instances": [{"asset": asset, "start": start,
                                        "end": end}]}]}


def _attribute(query=None, *, pool=(), ranges=(), rows=(), policy=None,
               abstained=False, index_times=None, sampled_times=None,
               sampled_status=None, clip_of_asset=None, offsets=None,
               groups=None, credited=(), tolerance=1.0, lane="visual"):
    """Run the visual ladder over synthetic evidence."""
    query = query or _query()
    return miss_attribution.attribute_visual(
        query,
        pool=pool, ranges=ranges, rows=rows, policy=policy or {},
        abstained=abstained,
        index_times=index_times or {}, sampled_times=sampled_times or {},
        sampled_status=sampled_status or {},
        clip_of_asset=clip_of_asset or {}, offsets=offsets or {},
        groups=groups or {}, credited=credited, tolerance=tolerance)


def _stage(result):
    return result["per_moment"][0]["stage"]


class _Evidence:
    """The harness's evidence, in memory: the index, the extracted
    sample and the transcript segments."""

    def __init__(self, index_times=None, sampled_times=None,
                 segment_spans=None, sampled_status=None):
        self._index = index_times or {}
        self._sampled = sampled_times or {}
        self._segments = segment_spans or {}
        self._status = sampled_status or {}
        self.offsets = {}
        self.groups = {}

    def index_times(self):
        return self._index

    def sampled_times(self):
        return self._sampled

    def sampling_status(self):
        return self._status

    def segment_spans(self):
        return self._segments


# ── every miss says which stage lost it ────────────────────────────


def test_a_miss_names_one_of_the_enumerated_stages():
    result = _attribute()
    assert _stage(result) in miss_attribution.STAGES
    assert result["failure_stage"] == _stage(result)
    assert result["failure_pipeline_stage"] in (
        miss_attribution.PIPELINE_STAGE.values())


def test_the_stage_vocabulary_is_closed():
    """A report that names a stage outside the enumeration says nothing.

    The defect: two reports use the same words for different places and
    nobody can compare them.
    """
    for stage in miss_attribution.STAGES:
        assert stage in miss_attribution.PIPELINE_STAGE
    assert set(miss_attribution.PIPELINE_STAGE) == set(
        miss_attribution.STAGES)
    assert miss_attribution.RETRIEVAL_FAILURE_STAGES & (
        miss_attribution.CREDIT_ACCOUNTING_STAGES) == frozenset()


# ── the stages are told apart ──────────────────────────────────────


def test_a_target_that_was_never_decoded_is_not_a_ranking_miss():
    """Extraction coverage: nothing was decoded, so nothing downstream
    could have found it."""
    result = _attribute(sampled_times={"clip_a": [100.0]},
                        index_times={"clip_a": [100.0]},
                        clip_of_asset={"LC4930": "clip_a"})
    assert _stage(result) == "never_sampled"
    assert result["failure_pipeline_stage"] == "extraction_coverage"


def test_a_target_that_was_decoded_and_never_embedded_is_an_indexing_miss():
    result = _attribute(sampled_times={"clip_a": [5.0, 15.0, 25.0]},
                        index_times={"clip_a": [100.0]},
                        clip_of_asset={"LC4930": "clip_a"})
    assert _stage(result) == "never_indexed"
    assert result["failure_pipeline_stage"] == "indexing"


def test_a_target_that_was_indexed_and_never_pooled_is_a_candidate_miss():
    result = _attribute(sampled_times={"clip_a": [15.0]},
                        index_times={"clip_a": [15.0]},
                        pool=[],
                        clip_of_asset={"LC4930": "clip_a"})
    assert _stage(result) == "not_in_candidate_pool"
    assert result["failure_pipeline_stage"] == "candidate_generation"


def test_a_target_the_ranking_put_below_the_clip_window_is_a_ranking_miss():
    """The pool held it; a better-scoring window on the SAME clip took
    the slot.  That is the ranking, not the presentation."""
    result = _attribute(
        sampled_times={"clip_a": [15.0]}, index_times={"clip_a": [15.0]},
        pool=[{"clip_id": "clip_a", "t": 15.0, "score": 0.10}],
        ranges=[{"clip_id": "clip_a", "start": 80.0, "end": 90.0,
                 "best_score": 0.30}],
        rows=[{"asset": "LC4930", "start": 80.0, "end": 90.0}],
        clip_of_asset={"LC4930": "clip_a"})
    assert _stage(result) == "ranked_too_low"
    assert result["failure_pipeline_stage"] == "ranking"


def test_a_target_found_on_the_other_angle_is_a_localization_miss():
    """The window that covers it is on the sibling camera.  The moment was
    localized; it was localized on the wrong asset."""
    result = _attribute(
        sampled_times={"clip_a": [15.0], "clip_b": [26.31]},
        index_times={"clip_a": [15.0], "clip_b": [26.31]},
        pool=[{"clip_id": "clip_a", "t": 15.0, "score": 0.10}],
        ranges=[{"clip_id": "clip_b", "start": 26.31, "end": 26.31,
                 "best_score": 0.30}],
        rows=[{"asset": "LCATL0011", "start": 26.31, "end": 26.31}],
        clip_of_asset={"LC4930": "clip_a", "LCATL0011": "clip_b"},
        offsets={"LC4930": 0.0, "LCATL0011": 11.31},
        groups={"LC4930": "A", "LCATL0011": "A"})
    assert _stage(result) == "temporal_localization"
    assert result["failure_pipeline_stage"] == "temporal_localization"


def test_a_target_the_floor_turned_away_is_a_floor_miss():
    """The lane abstained.  The floor did that, and the floor is not the
    ranking."""
    result = _attribute(
        sampled_times={"clip_a": [15.0]}, index_times={"clip_a": [15.0]},
        pool=[{"clip_id": "clip_a", "t": 15.0, "score": 0.05}],
        policy={"query_floor": 0.15}, abstained=True,
        clip_of_asset={"LC4930": "clip_a"})
    assert _stage(result) == "removed_by_query_floor"
    assert result["failure_pipeline_stage"] == "retrieval_floor"


def test_an_unreadable_extraction_sample_is_not_a_gap_in_the_extraction():
    """The sample cannot be read, so whether the frame was decoded cannot
    be said.  Reporting a gap the evidence does not show is a lie that
    points at the decoder."""
    result = _attribute(
        sampled_times={"clip_a": []}, index_times={"clip_a": []},
        sampled_status={"clip_a": "missing"},
        clip_of_asset={"LC4930": "clip_a"})
    assert _stage(result) == "extraction_evidence_unavailable"
    assert result["failure_pipeline_stage"] == "extraction_coverage"


def test_the_sibling_angle_is_reported_even_when_the_ladder_stops_early():
    """The localization on the other camera is a fact about the answer,
    and it is reported whether or not it is the stage that lost the
    wanted moment."""
    result = _attribute(
        sampled_times={"clip_a": [15.0], "clip_b": [26.31]},
        index_times={"clip_a": [15.0], "clip_b": [26.31]},
        pool=[],
        ranges=[{"clip_id": "clip_b", "start": 26.31, "end": 36.31,
                 "best_score": 0.30}],
        rows=[{"asset": "LCATL0011", "start": 26.31, "end": 36.31}],
        clip_of_asset={"LC4930": "clip_a", "LCATL0011": "clip_b"},
        offsets={"LC4930": 0.0, "LCATL0011": 11.31},
        groups={"LC4930": "A", "LCATL0011": "A"})
    assert _stage(result) == "not_in_candidate_pool"
    assert result["instances_localized_on_sibling"] == 1


def test_a_window_the_floor_dropped_is_a_window_floor_miss():
    result = _attribute(
        sampled_times={"clip_a": [15.0]}, index_times={"clip_a": [15.0]},
        pool=[{"clip_id": "clip_a", "t": 15.0, "score": 0.05}],
        policy={"window_floor": 0.22},
        clip_of_asset={"LC4930": "clip_a"})
    assert _stage(result) == "removed_by_window_floor"


# ── the credit rule is not the search ──────────────────────────────


def test_a_target_the_credit_rule_refused_is_not_a_retrieval_failure():
    """The window lands inside the wanted span and the harness calls it a
    hit, and the credit rule counts a window only when it OVERLAPS the
    span.  The search found it; the metric did not count it."""
    result = _attribute(
        sampled_times={"clip_a": [15.0]}, index_times={"clip_a": [15.0]},
        pool=[{"clip_id": "clip_a", "t": 15.0, "score": 0.30}],
        ranges=[{"clip_id": "clip_a", "start": 20.5, "end": 30.0,
                 "best_score": 0.30}],
        rows=[{"asset": "LC4930", "start": 20.5, "end": 30.0}],
        clip_of_asset={"LC4930": "clip_a"})
    assert _stage(result) == "touched_within_tolerance"
    assert result["failure_kind"] == "credit_accounting"
    assert result["is_retrieval_failure"] is False


def test_a_window_of_no_length_inside_the_wanted_span_is_named_as_such():
    """A point window inside the span is a hit to the harness and no
    credit to the rule.  Naming it apart keeps the metric's own defect
    out of the search's column."""
    result = _attribute(
        sampled_times={"clip_a": [15.0]}, index_times={"clip_a": [15.0]},
        pool=[{"clip_id": "clip_a", "t": 15.0, "score": 0.30}],
        ranges=[{"clip_id": "clip_a", "start": 15.0, "end": 15.0,
                 "best_score": 0.30}],
        rows=[{"asset": "LC4930", "start": 15.0, "end": 15.0}],
        clip_of_asset={"LC4930": "clip_a"})
    assert _stage(result) == "point_window_not_credited"
    assert result["failure_kind"] == "credit_accounting"


# ── the entries that have no wanted moment ─────────────────────────


def test_an_unlabelled_entry_borrows_no_failure_stage():
    result = miss_attribution.attribute_unscored(
        {"id": "U1", "category": "action"}, "visual", "unlabelled_not_scored")
    assert result["failure_stage"] is None
    assert result["outcome"] == "unlabelled_not_scored"
    assert result["wanted_items"] == 0


def test_an_absent_query_that_abstained_is_not_a_miss():
    corpus = retrieval_eval.Corpus.__new__(retrieval_eval.Corpus)
    corpus.name_of_clip = {}
    corpus.clip_of_asset = {}
    query = {"id": "A1", "family": "editorial", "lane": "visual",
             "category": "absent_event", "expect": "absent",
             "query": "a shot of a dog", "moments": []}
    scored = retrieval_eval.score_editorial(
        query, {"results": []}, corpus,
        {"overlap_tolerance_seconds": 1.0, "selection_k": 1})
    assert scored["attribution"]["outcome"] == "absent_abstained"
    assert scored["attribution"]["failure_stage"] is None


# ── the harness carries it ─────────────────────────────────────────


def test_the_scored_query_carries_its_attribution():
    corpus = retrieval_eval.Corpus.__new__(retrieval_eval.Corpus)
    corpus.name_of_clip = {"clip_a": "LC4930"}
    corpus.clip_of_asset = {"LC4930": "clip_a"}
    query = _query()
    report = {"results": [{"clip_id": "clip_a", "start": 10.0, "end": 20.0,
                           "score": 0.2, "dense_score": None, "text": ""}],
              "candidate_frames": [{"clip_id": "clip_a", "t": 15.0,
                                    "score": 0.2}],
              "ranges": [{"clip_id": "clip_a", "start": 10.0, "end": 20.0,
                          "best_score": 0.2}],
              "search_policy": {"query_floor": 0.15}}
    scored = retrieval_eval.score_editorial(
        query, report, corpus,
        {"overlap_tolerance_seconds": 1.0, "selection_k": 1},
        evidence=_Evidence({"clip_a": [15.0]}, {"clip_a": [15.0]}))
    assert scored["attribution"]["wanted_items"] == 1
    assert scored["attribution"]["credited_items"] == 1
    assert scored["attribution"]["outcome"] == "found"


def test_the_summary_counts_the_stages_by_category():
    corpus = retrieval_eval.Corpus.__new__(retrieval_eval.Corpus)
    corpus.name_of_clip = {"clip_a": "LC4930"}
    corpus.clip_of_asset = {"LC4930": "clip_a"}
    query = _query()
    report = {"results": [], "candidate_frames": [],
              "ranges": [], "search_policy": {}}
    scored = retrieval_eval.score_editorial(
        query, report, corpus,
        {"overlap_tolerance_seconds": 1.0, "selection_k": 1},
        evidence=_Evidence({"clip_a": [15.0]}, {"clip_a": [15.0]}))
    summary = retrieval_eval.summarise([scored], {})
    table = summary["stage_attribution"]
    assert table["by_category"]["object"]["not_in_candidate_pool"] == 1
    assert table["missed_queries"] == ["Q1"]
    assert table["missed_queries_without_a_stage"] == [], (
        "every missed query must carry a stage; a query with no stage is "
        "a report that says nothing about why")


def test_a_declared_category_with_no_misses_still_appears():
    """An axis with no labelled query has no denominator, and the reader
    must be able to SEE that rather than infer it from its absence."""
    corpus = retrieval_eval.Corpus.__new__(retrieval_eval.Corpus)
    corpus.name_of_clip = {"clip_a": "LC4930"}
    corpus.clip_of_asset = {"LC4930": "clip_a"}
    query = _query()
    report = {"results": [], "candidate_frames": [],
              "ranges": [], "search_policy": {}}
    scored = retrieval_eval.score_editorial(
        query, report, corpus,
        {"overlap_tolerance_seconds": 1.0, "selection_k": 1},
        evidence=_Evidence({"clip_a": [15.0]}, {"clip_a": [15.0]}))
    summary = retrieval_eval.summarise(
        [scored], {}, ["object", "action", "identity"])
    table = summary["stage_attribution"]
    assert table["declared_categories"] == ["object", "action", "identity"]
    assert table["by_category"]["action"] == {}, (
        "the action axis is declared and empty; it must appear empty, not "
        "vanish")
    assert table["by_category"]["object"]
