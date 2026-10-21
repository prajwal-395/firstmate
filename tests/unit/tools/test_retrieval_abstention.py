"""What an EMPTY search answer means, and the three answers it is not.

The defect this file names: a search lane's empty answer used to be
whatever that lane felt like saying.  The text lane said `not_found`,
the frame lane said `abstained`, and the frame lane said
`insufficient_evidence` on a search that returned five windows - a
claim that the evidence is missing when the lane had five.

`library.tools.retrieval.abstention` is the one table, and it says three
things a reader has to tell apart:

* `not_observed` - the lane searched its WHOLE measured scope and
  nothing cleared the floor;
* `insufficient_evidence` - the lane could NOT search its whole
  measured scope, so the empty answer is about the index, not about the
  footage;
* `confirmed_absent` - the measured scope carries an EXPLICIT negative
  assertion over the queried kind.

A REFUSAL is none of those: a lane that declined the question observed
nothing, and saying `not_observed` for it would claim a look that never
happened.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools.retrieval import abstention

CORPUS = Path("library/tools/editorial_abstention_negatives.json")


def _outcome(**kwargs):
    base = {"lane": "text", "ran": True, "found": False,
            "scope_complete": True}
    base.update(kwargs)
    return abstention.lane_outcome(**base)


# ─── The table ──────────────────────────────────────────────────────


def test_a_lane_that_found_something_says_present():
    """The defect: a lane that returned evidence said the evidence was
    insufficient."""
    outcome = _outcome(found=True)
    assert outcome["outcome"] == abstention.OUTCOME_FOUND
    assert outcome["answer_state"] == "present"


def test_a_whole_scope_searched_and_nothing_found_is_not_observed():
    """Not `absent`: we looked, we did not see it, and we are not claiming
    it is not there."""
    outcome = _outcome()
    assert outcome["outcome"] == abstention.OUTCOME_NOT_OBSERVED
    assert outcome["answer_state"] == "not_observed"


def test_a_lane_that_could_not_search_its_whole_scope_is_insufficient():
    """The empty answer is then about the index, and must not be read as a
    statement about the footage."""
    outcome = _outcome(scope_complete=False)
    assert outcome["outcome"] == abstention.OUTCOME_INSUFFICIENT_EVIDENCE
    assert outcome["answer_state"] == "inconclusive"


def test_only_an_explicit_negative_confirms_an_absence():
    """A ranking is not an assertion.  The scope has to carry the negative
    itself before the answer may say `absent`."""
    outcome = _outcome(has_explicit_negative=True)
    assert outcome["outcome"] == abstention.OUTCOME_CONFIRMED_ABSENT
    assert outcome["answer_state"] == "absent"


def test_a_refusal_is_not_an_observation():
    """A lane that declined the question observed nothing; reporting that
    as `not_observed` would claim a look that never happened."""
    outcome = _outcome(refused=True)
    assert outcome["outcome"] == abstention.OUTCOME_REFUSED
    assert outcome["answer_state"] == "not_analyzed"


def test_the_scope_travels_with_the_outcome():
    """`not observed` is a claim about a scope, so the scope is part of
    the answer."""
    outcome = _outcome(scope_note="7 of 7 catalogued sources")
    assert outcome["scope"]["complete"] is True
    assert "7 of 7" in outcome["scope"]["note"]


def test_the_near_miss_is_carried_beside_the_outcome_and_never_inside_it():
    outcome = _outcome(near_miss={"clip_id": "clip_001", "score": 0.31})
    assert outcome["near_miss"]["score"] == 0.31
    assert "near_miss" not in outcome["basis"]


def test_every_outcome_is_in_the_enumeration():
    assert abstention.OUTCOMES == (
        "found", "not_observed", "insufficient_evidence",
        "confirmed_absent", "refused")


# ─── The labelled negative corpus ──────────────────────────────────


def test_a_negative_whose_subject_is_in_the_scope_is_refused():
    """The defect this guards: a corpus entry that is not really negative
    would be scored as a false positive, and the lane would be punished
    for being right about something that IS in the footage."""
    corpus = {"queries": [
        {"id": "T01", "subject_words": ["cookie", "recipe"]},
        {"id": "T02", "subject_words": ["puppy"]},
    ]}
    spoken = {"cookie", "the", "recipe"}
    refused = abstention.unverified_negatives(corpus, spoken)
    assert [entry["id"] for entry in refused] == ["T01"]
    assert refused[0]["subject_words_present"] == ["cookie", "recipe"]


def test_a_corpus_of_real_negatives_is_not_refused():
    corpus = {"queries": [{"id": "T01", "subject_words": ["puppy", "dog"]}]}
    assert abstention.unverified_negatives(corpus, {"marketing", "audit"}) == []


def test_the_shipped_corpus_says_how_every_label_was_established():
    """Every negative says what it is about, which lane it is for, which
    half it is fitted on, and which words make it absent."""
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert corpus["scope"]["text_scope"]["count"] > 0
    assert corpus["scope"]["visual_scope"]["count"] > 0
    assert corpus["verification"]["text"]
    assert corpus["verification"]["visual"]
    ids = set()
    for entry in corpus["queries"]:
        assert entry["id"] not in ids, "two negatives share an id"
        ids.add(entry["id"])
        assert entry["lane"] in ("text", "visual")
        assert entry["split"] in ("tune", "heldout")
        assert entry["query"].strip()
        assert entry["subject_words"], (
            "a negative with no subject words cannot be checked against "
            "the measured scope")
        for word in entry["subject_words"]:
            assert word == word.lower() and word.isalpha()


def test_the_corpus_is_split_so_a_floor_is_fitted_on_one_half_only():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    splits = {entry["split"] for entry in corpus["queries"]}
    assert splits == {"tune", "heldout"}
    for lane in ("text", "visual"):
        for split in splits:
            assert any(entry["lane"] == lane and entry["split"] == split
                       for entry in corpus["queries"]), (
                "each lane needs negatives on both halves, or the floor "
                "cannot be fitted on one and reported on the other")


def test_the_corpus_carries_the_negatives_the_editorial_sets_already_had():
    """The entries the shipped editorial sets carried are RE-VERIFIED
    here, not re-labelled: a corpus that quietly re-labels them would
    change what those sets mean."""
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    carried = [entry for entry in corpus["queries"]
               if entry.get("carried_from")]
    assert carried, "the corpus must re-verify the negatives already shipped"
    for entry in carried:
        assert "editorial_retrieval" in entry["carried_from"]


@pytest.mark.parametrize("path", [CORPUS])
def test_the_corpus_file_is_readable_json(path):
    assert json.loads(path.read_text(encoding="utf-8"))["queries"]
