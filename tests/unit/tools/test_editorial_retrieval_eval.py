"""An EDITORIAL search is not a content description, and it is measured.

The punch list asks for two things this file holds to account:

* **VI-002** - retrieval is benchmarked on real editorial searches rather
  than on broad object descriptions.  The committed editorial set must
  ask the way an editor asks (by what a moment is FOR and what it
  contains), and it must carry ground-truth INTERVALS, not a verdict.
* **VI-003** - the held-out dataset covers actions, object states,
  identities, locations and absent events.  A category whose labels do
  not exist must be DECLARED as such and refused by the scorer, never
  counted as a miss.
* **VI-010** - a false positive is priced in the edit it would cause,
  and the price is a vector rather than a number.

Three defects, three families of tests:

* a query with no ground truth is scored as a miss (it must not be);
* a wrong window outranking every right one is not distinguished from a
  wrong window trailing them (it must be);
* the requirement decomposition is never compared with the declared
  requirement (it must be).
"""

import json
from pathlib import Path

import pytest

from library.tools import retrieval_eval
from library.tools.retrieval import consequence
from library.tools.retrieval.requirements import decompose

REPO_ROOT = Path(__file__).resolve().parents[3]
EDITORIAL_SET = Path("library/tools/editorial_retrieval_set.json")
HELD_OUT_SET = Path("library/tools/editorial_retrieval_heldout_set.json")


def _load(path):
    return json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))


# ─── VI-002: the set asks the way an editor asks ──────────────────


def test_the_editorial_set_asks_editor_shaped_questions_with_intervals():
    payload = _load(EDITORIAL_SET)
    queries = payload["queries"]
    assert queries, "the editorial set must have queries"
    for query in queries:
        assert query["family"] == "editorial"
        assert query.get("editorial_need"), (
            f"{query['id']} names no use for the moment; an editorial "
            "search without a use is a content description")
        assert query["query"] != query["editorial_need"], (
            f"{query['id']} repeats itself; the question and the need are "
            "different things")
        if query["expect"] == "present":
            assert query["moments"], (
                f"{query['id']} expects a moment and declares none")
            for moment in query["moments"]:
                for instance in moment["instances"]:
                    assert instance["start"] < instance["end"], (
                        f"{query['id']} declares a non-positive interval")
                    assert instance["asset"] in payload["corpus"]["assets"], (
                        f"{query['id']} names a source the corpus does not "
                        "hold")


def test_every_editorial_moment_is_carried_from_a_named_set():
    payload = _load(EDITORIAL_SET)
    for query in payload["queries"]:
        provenance = query.get("provenance") or {}
        assert provenance.get("derived_from"), (
            f"{query['id']} declares a moment without saying which set it "
            "came from; a new label is not a held-out label")
        assert provenance.get("basis"), (
            f"{query['id']} says where its labels came from but not on "
            "what basis")


def test_the_editorial_set_covers_absent_searches_as_well_as_present_ones():
    payload = _load(EDITORIAL_SET)
    expects = {query["expect"] for query in payload["queries"]}
    assert "present" in expects
    assert "absent" in expects, (
        "a benchmark of retrieval that never asks for something missing "
        "measures only the easy half")


# ─── VI-003: a category with no labels is declared, not scored ─────


def test_the_held_out_set_measures_both_lanes():
    """The defect: the held-out set carried no text-lane present query,
    so its recall was a frame-lane number read as a retrieval number."""
    payload = _load(HELD_OUT_SET)
    present = [q for q in payload["queries"] if q.get("expect") == "present"]
    lanes = {q.get("lane") for q in present}
    assert "text" in lanes, (
        "the held-out set measures the frame lane alone, so it says "
        "nothing about the lane an editor asks in most")
    assert "visual" in lanes
    for query in present:
        if query.get("lane") == "text":
            assert query["moments"], (
                f"{query['id']} is a text-lane present query with no "
                "ground truth")


def test_an_entry_added_after_the_set_was_measured_says_so():
    """The defect: a new label slipped into a set that claims to be
    pre-registered, and the report cannot say which entries are old."""
    payload = _load(HELD_OUT_SET)
    amendments = payload.get("amendments") or []
    assert amendments, (
        "the entries added after the set was measured must say so in the "
        "set, not only in a commit message")
    for amendment in amendments:
        assert amendment["registered_before_measurement"] is False
        assert amendment["why"]
        for identifier in amendment["entries"]:
            query = next(q for q in payload["queries"]
                         if q["id"] == identifier)
            provenance = query.get("provenance") or {}
            store = query.get("label_store")
            assert provenance.get("derived_from") or store, (
                f"{identifier} carries a label and does not say where it "
                "came from")
            assert provenance.get("basis") or store
            if not store:
                assert query["expect"] == "present"
                assert query["moments"]


def test_the_five_categories_carry_their_labels_in_a_sealed_store():
    """The defect: the five categories the punch list names carried no
    label, so the corpus had no negative or relational query for a
    floor or an action refusal to be measured against."""
    payload = _load(HELD_OUT_SET)
    by_id = {query["id"]: query for query in payload["queries"]}
    for identifier in ("H11", "H12", "H13", "H14", "H15"):
        query = by_id[identifier]
        assert query["expect"] == "unlabelled", (
            f"{identifier} stays unlabelled in the repository; its label is "
            "sealed, so a checkout without the store must not score it")
        store = query.get("label_store") or {}
        assert store.get("environment_variable"), (
            f"{identifier} does not say where its label lives")
        requirement = query.get("label_requirement") or {}
        assert requirement.get("annotation_state") == "created"
        assert requirement.get("label_payloads_in_repository") is False
        assert requirement.get("tuning_access") is False


def test_a_sealed_label_makes_its_entry_scorable_and_a_missing_one_does_not():
    from library.tools import editorial_eval_labels
    payload = _load(HELD_OUT_SET)
    labels = editorial_eval_labels.load_store("/nonexistent/label-store.json")
    assert labels == {}, (
        "a store that is not there is an empty store: the harness then "
        "scores the set exactly as it did before the labels existed")
    applied, applied_ids = editorial_eval_labels.apply_labels(payload, labels)
    assert applied_ids == []
    for query in applied["queries"]:
        if query["id"] == "H11":
            assert query["expect"] == "unlabelled"


def test_a_sealed_label_is_applied_when_the_store_carries_it():
    from library.tools import editorial_eval_labels
    payload = _load(HELD_OUT_SET)
    label = {"entry_id": "H11", "expect": "present",
             "moments": [{"instances": [
                 {"asset": "LC4930", "start": 5.9, "end": 7.1}]}]}
    applied, applied_ids = editorial_eval_labels.apply_labels(
        payload, {"H11": label})
    assert applied_ids == ["H11"]
    entry = next(q for q in applied["queries"] if q["id"] == "H11")
    assert entry["expect"] == "present"
    assert entry["moments"] == label["moments"]
    assert entry["labels"] == "sealed_custodian_store"


def test_a_label_that_cannot_be_read_is_not_applied():
    from library.tools import editorial_eval_labels
    payload = _load(HELD_OUT_SET)
    broken = {"entry_id": "H11", "expect": "present",
              "moments": [{"instances": [
                  {"asset": "NOT-A-SOURCE", "start": 5.9, "end": 7.1}]}]}
    applied, applied_ids = editorial_eval_labels.apply_labels(
        payload, {"H11": broken})
    assert applied_ids == []
    entry = next(q for q in applied["queries"] if q["id"] == "H11")
    assert entry["expect"] == "unlabelled"
    assert entry.get("label_problems"), (
        "a label that names a source the set does not hold is refused, and "
        "the refusal is said out loud")


def test_the_summary_reports_the_lanes_on_their_own_denominators():
    """The defect: a macro average over two lanes hides which one
    moved, and a set that carries one lane says nothing about the
    other."""
    payload = _load(HELD_OUT_SET)
    query = next(q for q in payload["queries"] if q["id"] == "H16")
    corpus = retrieval_eval.Corpus.__new__(retrieval_eval.Corpus)
    corpus.name_of_clip = {"LC4930": "LC4930"}
    report = {"results": [{"clip_id": "LC4930", "start": 134.4, "end": 151.51,
                           "score": 0.5}]}
    scored = retrieval_eval.score_editorial(
        query, report, corpus, payload["scoring"]["editorial"])
    summary = retrieval_eval.summarise([scored], {})
    block = summary["editorial"]["by_lane"]
    assert block["text"]["queries_present"] == 1
    assert block["text"]["macro_recall"] == pytest.approx(1.0)
    assert "visual" not in block, (
        "a lane the set does not query is not reported as a lane")


def test_the_held_out_set_declares_all_five_categories():
    payload = _load(HELD_OUT_SET)
    categories = payload["categories"]
    for category in ("action", "object_state", "identity", "location",
                     "absent_event"):
        assert category in categories, (
            f"{category} is one of the categories the punch list names for "
            "the held-out dataset")
        assert categories[category]["annotated"] or categories[category]["not_created"], (
            f"{category} is declared with neither annotated entries nor "
            "entries still owed")


def test_a_category_with_no_labels_is_marked_unlabelled_and_owed():
    payload = _load(HELD_OUT_SET)
    by_id = {query["id"]: query for query in payload["queries"]}
    for entry in payload["categories"].values():
        for identifier in entry["not_created"]:
            query = by_id[identifier]
            assert query["expect"] == "unlabelled", (
                f"{identifier} is owed a label and must not be scored as a "
                "present or absent query")
            assert query["labels"] == "not_created"
            requirement = query["label_requirement"]
            assert requirement["held_out_sources"], (
                f"{identifier} names no held-out source for the custodian "
                "to label")
            assert requirement["annotation_state"] == "not_started"
            assert requirement["label_payloads_in_repository"] is False, (
                "a held-out label must not live in this repository")


def test_the_scorer_refuses_to_score_an_unlabelled_query():
    payload = _load(HELD_OUT_SET)
    unlabelled = next(query for query in payload["queries"]
                       if query["expect"] == "unlabelled")
    scored = retrieval_eval.score_editorial(
        unlabelled, {"results": []}, None,
        payload["scoring"]["editorial"])
    assert scored["kind"] == "unlabelled"
    assert scored["scored"] is False
    assert "recall" not in scored, (
        "a query with no ground truth has no recall; reporting zero would "
        "count it as a miss")
    assert "consequence" not in scored


def test_an_unlabelled_query_is_reported_and_never_counted_as_a_miss():
    payload = _load(HELD_OUT_SET)
    unlabelled = next(query for query in payload["queries"]
                      if query["expect"] == "unlabelled")
    scored = retrieval_eval.score_editorial(
        unlabelled, {"results": []}, None,
        payload["scoring"]["editorial"])
    summary = retrieval_eval.summarise([scored], {})
    assert summary["editorial"]["queries_unlabelled"] == 1
    assert summary["editorial"]["queries_present"] == 0
    assert summary["editorial"]["queries_absent"] == 0


# ─── VI-002: the requirement decomposition is measured ─────────────


def test_the_decomposition_names_every_declared_modality_in_the_committed_sets():
    for path in (EDITORIAL_SET, HELD_OUT_SET):
        payload = _load(path)
        for query in payload["queries"]:
            agreement = retrieval_eval._requirement_agreement(query)
            assert not agreement["missing"], (
                f"{query['id']}: the decomposition never names "
                f"{agreement['missing']}, so the search will not look for "
                "it")
            assert not agreement["unambiguous_extra"], (
                f"{query['id']}: the decomposition looks for "
                f"{agreement['unambiguous_extra']}, which the set does not "
                "ask for and which is not an ambiguous reading or a "
                "constraint")


def test_a_declared_modality_the_decomposition_misses_is_reported():
    query = {"id": "X", "family": "editorial",
             "query": "the pricing rule",
             "requirements": ["textual", "acoustic"]}
    agreement = retrieval_eval._requirement_agreement(
        query)
    assert agreement["missing"], (
        "a declared modality the decomposition never names is a "
        "requirement the search will not look for")
    assert agreement["requirement_recall"] < 1.0
    assert agreement["agrees"] is False


def test_an_ambiguous_reading_is_not_counted_as_a_spurious_requirement():
    query = {"id": "Y", "family": "editorial",
             "query": "the laptop on the desk",
             "requirements": ["visual"]}
    agreement = retrieval_eval._requirement_agreement(
        query)
    assert agreement["ambiguous_requirements"], (
        "a bare noun with no framing word is ambiguous, and the plan says so")
    assert agreement["unambiguous_extra"] == []


# ─── VI-010: the cost of a wrong window, in the edit ──────────────


def test_a_wrong_window_outranking_every_right_one_is_reported_as_shadowing():
    moments = [{"instances": [{"asset": "A", "start": 10.0, "end": 20.0}]}]
    rows = [
        {"asset": "A", "start": 50.0, "end": 60.0, "hit": False},
        {"asset": "A", "start": 11.0, "end": 19.0, "hit": True},
    ]
    result = consequence.edit_consequence(moments, rows, selection_k=1)
    assert result["shadowing"] is True, (
        "the first pick is the wrong window; that is a different failure "
        "from a wrong window nobody takes")
    assert result["wrong_windows_selected"] == 1
    assert result["wrong_seconds_selected"] == 10.0
    assert result["missing_seconds"] == 10.0


def test_a_wrong_window_trailing_the_right_one_is_not_shadowing():
    moments = [{"instances": [{"asset": "A", "start": 10.0, "end": 20.0}]}]
    rows = [
        {"asset": "A", "start": 11.0, "end": 19.0, "hit": True},
        {"asset": "A", "start": 50.0, "end": 60.0, "hit": False},
    ]
    result = consequence.edit_consequence(moments, rows, selection_k=1)
    assert result["shadowing"] is False
    assert result["wrong_windows_selected"] == 0
    assert result["false_positive_windows"] == 1, (
        "the whole result still carries the false positive, even when the "
        "selection did not take it")
    assert result["missing_seconds"] == pytest.approx(2.0), (
        "the selection took eight of the moment's ten seconds, so two "
        "seconds of what was wanted are not in the edit")


def test_the_consequence_is_a_vector_and_not_a_single_score():
    moments = [{"instances": [{"asset": "A", "start": 10.0, "end": 20.0}]}]
    rows = [{"asset": "A", "start": 10.0, "end": 20.0, "hit": True}]
    result = consequence.edit_consequence(moments, rows, selection_k=1)
    assert "score" not in result and "total" not in result, (
        "weighting a wrong second against a missing second is the edit "
        "policy's decision, not the harness's")
    assert result["wrong_seconds_selected"] == 0.0
    assert result["missing_seconds"] == 0.0
    assert result["coverage_fraction"] == pytest.approx(1.0)


def test_a_moment_on_three_cameras_is_one_moment_not_three():
    moment = {"instances": [
        {"asset": "A", "start": 10.0, "end": 20.0},
        {"asset": "B", "start": 10.0, "end": 20.0},
        {"asset": "C", "start": 10.0, "end": 20.0},
    ]}
    result = consequence.edit_consequence([moment], [], selection_k=1)
    assert result["ground_truth_seconds"] == pytest.approx(10.0), (
        "counting the cameras separately makes a well-covered moment look "
        "like a bigger miss")


def test_the_summary_carries_both_recall_and_the_false_positive_consequence():
    payload = _load(EDITORIAL_SET)
    query = next(q for q in payload["queries"] if q["expect"] == "present")
    rules = payload["scoring"]["editorial"]
    corpus = retrieval_eval.Corpus.__new__(retrieval_eval.Corpus)
    corpus.name_of_clip = {"LC4932": "LC4932"}
    report = {"results": [
        {"clip_id": "LC4932", "start": 3562.1, "end": 3610.9, "score": 0.7,
         "text": "twenty percent"},
        {"clip_id": "LC4932", "start": 5000.0, "end": 5010.0, "score": 0.6,
         "text": "something else"},
    ]}
    scored = retrieval_eval.score_editorial(query, report, corpus, rules)
    assert scored["kind"] == "present"
    assert scored["recall"] == pytest.approx(1.0)
    assert scored["precision"] == pytest.approx(0.5)
    assert scored["consequence"]["false_positive_windows"] == 1
    summary = retrieval_eval.summarise([scored], {})
    block = summary["editorial"]
    assert block["macro_recall"] == pytest.approx(1.0)
    assert block["false_positive_consequence"]["false_positive_windows"] == 1
    assert block["false_positive_consequence"]["queries"] == 1


def test_the_decomposition_of_an_editorial_query_is_deterministic():
    query = "the twenty percent line, where he says a beautiful website " \
            "is only twenty percent of what AI looks at"
    first = decompose(query)
    second = decompose(query)
    assert [r.key for r in first.requirements] == [
        r.key for r in second.requirements], (
        "a benchmark that measures a decomposition which changes between "
        "runs measures nothing")


# ─── The frame lane's WINDOWS are scored, not its stills ───────────


def test_the_frame_lane_is_scored_on_its_ranges_not_on_its_stills():
    """The frame report carries BOTH lists, and only one of them is a window.

    `search_ranges` returns the individual STILLS under `results` and
    the merged intervals under `ranges`.  The scorer compares a window
    with a ground-truth interval, so a still - which has a `t` and no
    `start`/`end` - is not a window: reading the stills made the scorer
    compare None with a number and crash the benchmark on every
    frame-lane query that returned a candidate (measured 2026-10-20, see
    docs/benchmarks/2026-10-20-editorial-retrieval-benchmark.md).
    """
    report = {
        "results": [{"clip_id": "clip_003", "filename": "LC4932.MXF",
                     "t": 3562.4, "score": 0.21, "verified": False}],
        "ranges": [{"clip_id": "clip_003", "filename": "LC4932.MXF",
                    "start": 3562.1, "end": 3610.9, "best_score": 0.21,
                    "n_frames": 3, "verified": False}],
    }
    rows = retrieval_eval._editorial_rows(report)
    assert len(rows) == 1
    assert rows[0]["start"] == 3562.1
    assert rows[0]["end"] == 3610.9
    assert rows[0]["score"] == 0.21


def test_a_refused_frame_lane_scores_as_a_refusal_and_not_as_an_absence():
    """A lane that refused has not observed anything.

    The held-out set carries an action query the frame lane refuses by
    design (H10).  The scorer reads that refusal as the answer it is;
    it must not be counted as a query that searched and found nothing.
    """
    payload = _load(HELD_OUT_SET)
    query = next(q for q in payload["queries"] if q["id"] == "H10")
    rules = payload["scoring"]["editorial"]
    corpus = retrieval_eval.Corpus.__new__(retrieval_eval.Corpus)
    corpus.name_of_clip = {}
    refusal = {"matched": "gesturing", "reason": "frame search covers "
               "objects, people and scenes", "hint": "the event pipeline"}
    scored = retrieval_eval.score_editorial(
        query, {"results": [], "refused": refusal}, corpus, rules)
    assert scored["kind"] == "refusal"
    assert scored["refused"] is True
    assert scored["abstained"] is True
