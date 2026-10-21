"""The blind review pack (QA-007).

The defect this file names, in one sentence: a human review that does not
know which candidate produced the video, is asked for one opinion, and
that opinion becomes the truth about the deliverable.

Every test here names a defect the pack refuses, or a property the
results record has to keep:

- a reviewer-facing view that names a producer is not a blind view;
- a brief that carries the answer key is refused before it reaches a
  reviewer;
- a reviewer who leaves a leg out has not answered the rubric;
- a judgment with no evidence is an opinion, and the pack grades it with
  the SAME contract the engine grades an agent's with;
- two reviewers who disagree have established nothing, and the record
  says `disagreement` rather than averaging them;
- one reviewer's malformed rows do not erase the other reviewers';
- the engine's own measurement is published BESIDE the panel's reading,
  so a human's approval cannot clear it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from library.tools.editorial_acceptance import blind_review, judgement

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / \
    "project_model_e2e" / "media"


def _brief(**overrides) -> dict:
    brief = {
        "id": "brief-01",
        "title": "The pricing reel",
        "instruction": "Open on the claim, then the proof.",
        "project_state": {"footage_profile": "one speaker, one camera"},
        "acceptance_criteria": [
            {"id": "hook-first", "statement": "it opens on the claim"},
        ],
    }
    brief.update(overrides)
    return brief


def _media(tmp_path: Path, name: str = "coast-broll.mp4") -> Path:
    """A delivered file, copied into the test's own directory."""
    target = tmp_path / name
    shutil.copyfile(FIXTURES / name, target)
    return target


def _candidates(tmp_path: Path) -> list[dict]:
    return [
        {"candidate_id": "cand-alpha", "origin": "producer-alpha",
         "path": str(_media(tmp_path, "coast-broll.mp4"))},
        {"candidate_id": "cand-beta", "origin": "producer-beta",
         "path": str(_media(tmp_path, "person-ada-aroll.mp4"))},
    ]


def _pack(tmp_path: Path, **overrides) -> dict:
    kwargs = {
        "pack_id": "pack-01",
        "brief": _brief(),
        "candidates": _candidates(tmp_path),
        "out_dir": tmp_path / "pack",
    }
    kwargs.update(overrides)
    return blind_review.build_pack(**kwargs)


def _leg(leg: str, status: str = "pass", **overrides) -> dict:
    row = {
        "leg": leg,
        "status": status,
        "rationale": "read off the delivered file the reviewer watched",
        "evidence": [{"artifact": "entries/entry.mp4",
                      "observation": "the frame the reviewer stopped on"}],
        "uncertainty": {"level": "low",
                        "note": "a second reader might weigh the pause "
                                "differently"},
    }
    row.update(overrides)
    return row


def _legs(status: str = "pass", **overrides) -> list[dict]:
    return [_leg(leg, status, **overrides)
            for leg in judgement.REVIEW_LEG_IDS]


def _answers(pack: dict, reviewer: str, *, entry_ids=None,
             decision: str = blind_review.ACCEPTABLE,
             legs=None) -> dict:
    ids = entry_ids or [entry["entry_id"] for entry in pack["entries"]]
    return {
        "schema": blind_review.ANSWERS_SCHEMA,
        "pack_id": pack["pack_id"],
        "reviewer": reviewer,
        "entries": [
            {"entry_id": entry_id,
             "legs": legs if legs is not None else _legs(),
             "decision": decision,
             "notes": ""}
            for entry_id in ids],
    }


# ── the blinding ───────────────────────────────────────────────────────


def test_the_reviewer_view_names_no_producer(tmp_path):
    """The defect: a reviewer-facing view that says which candidate
    produced the video, which is the whole thing the pack exists to
    prevent."""
    pack = _pack(tmp_path)
    view = blind_review.reviewer_view(pack)
    text = json.dumps(view, ensure_ascii=False)
    assert "cand-alpha" not in text
    assert "cand-beta" not in text
    assert "producer-alpha" not in text
    assert "producer-beta" not in text
    assert "coast-broll" not in text
    assert "person-ada-aroll" not in text


def test_a_brief_that_names_the_producer_is_refused(tmp_path):
    """The defect: the brief carries the origin in its own words, and the
    pack is built anyway - so the blinding is a promise, not a fact."""
    brief = _brief(instruction="Cut this the way producer-beta would.")
    with pytest.raises(blind_review.BlindReviewError) as caught:
        _pack(tmp_path, brief=brief)
    assert "not blind" in str(caught.value)
    assert "producer-beta" in str(caught.value)


def test_a_brief_that_carries_the_answer_key_is_refused(tmp_path):
    """The defect: the evaluator's half of the brief reaches the
    reviewer's hands, and the reviewer recognises the answer instead of
    reading the film."""
    brief = _brief(expected_operations=["trim"])
    with pytest.raises(ValueError) as caught:
        _pack(tmp_path, brief=brief)
    assert "answer key" in str(caught.value)


def test_a_candidate_named_in_two_letters_cannot_be_blinded(tmp_path):
    """The defect: an origin too short to be checked for leaks is
    accepted, so the pack cannot say it is blind."""
    candidates = [{"candidate_id": "cand-alpha", "origin": "x",
                   "path": str(_media(tmp_path))}]
    with pytest.raises(blind_review.BlindReviewError) as caught:
        blind_review.build_pack(pack_id="pack-01", brief=_brief(),
                                candidates=candidates,
                                out_dir=tmp_path / "pack")
    assert "cannot be checked for leaks" in str(caught.value)


def test_the_media_is_copied_under_an_opaque_name(tmp_path):
    """The defect: the reviewer is handed a file whose NAME says which
    candidate it is, whatever the manifest says."""
    pack = _pack(tmp_path)
    entries = Path(tmp_path / "pack" / "entries")
    names = sorted(path.name for path in entries.iterdir())
    assert names == sorted(f"{entry['entry_id']}.mp4"
                           for entry in pack["entries"])
    assert not any("coast" in name or "ada" in name for name in names)


def test_the_order_does_not_follow_the_order_the_candidates_arrived_in(
        tmp_path):
    """The defect: the entries are shown in the order they were built, so
    the first one is always the same producer's."""
    forward = _pack(tmp_path, out_dir=tmp_path / "forward")
    reversed_candidates = list(reversed(_candidates(tmp_path)))
    backward = blind_review.build_pack(
        pack_id="pack-01", brief=_brief(), candidates=reversed_candidates,
        out_dir=tmp_path / "backward")
    assert ([entry["entry_id"] for entry in forward["entries"]]
            == [entry["entry_id"] for entry in backward["entries"]])


def test_the_pack_carries_the_brief_its_constraints_and_its_references(
        tmp_path):
    """The defect: a reviewer is asked to judge a deliverable without the
    brief it was made from, so the judgement is about nothing."""
    pack = _pack(tmp_path,
                 constraints=["keep the caption inside the safe area"],
                 references=[{"kind": "text", "label": "the house cut",
                              "text": "open on the claim, cut on the beat"}])
    view = blind_review.reviewer_view(pack)
    assert view["brief"]["instruction"] == "Open on the claim, then the proof."
    assert view["constraints"] == ["keep the caption inside the safe area"]
    assert view["references"][0]["text"] == "open on the claim, cut on the beat"


def test_the_rubric_is_the_engines_own_eight_legs(tmp_path):
    """The defect: the pack invents its own rubric, and the review it
    produces cannot be read against the engine's other reviews."""
    pack = _pack(tmp_path)
    assert [leg["id"] for leg in pack["rubric"]] == list(
        judgement.REVIEW_LEG_IDS)


# ── the answers ────────────────────────────────────────────────────────


def test_a_judgment_with_no_evidence_is_refused_by_the_same_contract(
        tmp_path):
    """The defect: a reviewer's judgment is an opinion wearing a
    verdict's clothes, and only the engine's own judgments were held to
    the contract."""
    pack = _pack(tmp_path)
    broken = _legs()
    broken[0] = _leg(broken[0]["leg"], "pass", evidence=[])
    answers = [_answers(pack, "reader-one", legs=broken)]
    record = blind_review.results(pack, answers)
    reviewer = record["candidates"][0]["reviewers"][0]
    assert reviewer["review_error"] is not None
    assert "carries no evidence" in reviewer["review_error"]
    assert reviewer["subjective_score"] is None


def test_a_reviewer_who_leaves_a_leg_out_has_not_answered(tmp_path):
    """The defect: seven legs answered is a review of five eighths, and it
    is recorded as if it were the whole rubric."""
    pack = _pack(tmp_path)
    short = _legs()[:-1]
    answers = [_answers(pack, "reader-one", legs=short)]
    record = blind_review.results(pack, answers)
    reviewer = record["candidates"][0]["reviewers"][0]
    assert reviewer["review_error"] is not None
    assert "does not carry a row for" in reviewer["review_error"]


def test_one_malformed_reviewer_does_not_erase_the_others(tmp_path):
    """The defect: one reviewer's bad rows throw the whole panel away,
    and the other reviewer's reading is lost with it."""
    pack = _pack(tmp_path)
    broken = _legs()
    broken[0] = _leg(broken[0]["leg"], "pass", evidence=[])
    answers = [_answers(pack, "reader-one", legs=broken),
               _answers(pack, "reader-two")]
    record = blind_review.results(pack, answers)
    reviewers = {row["reviewer"]: row
                 for row in record["candidates"][0]["reviewers"]}
    assert reviewers["reader-one"]["review_error"] is not None
    assert reviewers["reader-two"]["review_error"] is None
    assert reviewers["reader-two"]["subjective_score"] == 1.0


def test_an_answers_file_for_another_pack_is_refused(tmp_path):
    """The defect: answers from another pack are counted as if they
    answered this one."""
    pack = _pack(tmp_path)
    answers = _answers(pack, "reader-one")
    answers["pack_id"] = "pack-99"
    with pytest.raises(blind_review.BlindReviewError) as caught:
        blind_review.results(pack, [answers])
    assert "answers pack 'pack-99'" in str(caught.value)


def test_a_decision_outside_the_vocabulary_is_refused(tmp_path):
    """The defect: a reviewer's overall decision is free text, so the
    record cannot tell 'acceptable' from 'fine'."""
    pack = _pack(tmp_path)
    answers = _answers(pack, "reader-one", decision="looks good to me")
    with pytest.raises(blind_review.BlindReviewError) as caught:
        blind_review.results(pack, [answers])
    assert "not one of" in str(caught.value)


# ── the results record ─────────────────────────────────────────────────


def test_two_reviewers_who_disagree_are_recorded_as_a_disagreement(
        tmp_path):
    """The defect: one reviewer's opinion is treated as the truth about
    the deliverable, and the other's is averaged away."""
    pack = _pack(tmp_path)
    strict = _legs()
    strict[0] = _leg(strict[0]["leg"], "fail",
                      rationale="the sentence breaks in the middle")
    answers = [
        _answers(pack, "reader-one", legs=strict,
                 decision=blind_review.SIGNIFICANT_REPAIR),
        _answers(pack, "reader-two", decision=blind_review.ACCEPTABLE),
    ]
    record = blind_review.results(pack, answers)
    candidate = record["candidates"][0]
    assert candidate["standing"] == blind_review.STANDING_DISAGREEMENT
    assert candidate["decisions"] == {
        "reader-one": blind_review.SIGNIFICANT_REPAIR,
        "reader-two": blind_review.ACCEPTABLE,
    }
    disagreements = {row["id"] for row in candidate["leg_disagreements"]}
    assert disagreements == {"narrative_coherence"}
    # both entries in the pack carry the same two readings, so both
    # candidates are disagreements.
    assert record["counts"]["candidates_disagreed"] == 2


def test_the_results_keep_every_reviewers_score(tmp_path):
    """The defect: the record keeps one number, and nobody can say whose
    reading it was."""
    pack = _pack(tmp_path)
    strict = _legs()
    strict[0] = _leg(strict[0]["leg"], "fail",
                      rationale="the sentence breaks in the middle")
    answers = [
        _answers(pack, "reader-one", legs=strict,
                 decision=blind_review.SIGNIFICANT_REPAIR),
        _answers(pack, "reader-two", decision=blind_review.ACCEPTABLE),
    ]
    record = blind_review.results(pack, answers)
    scores = {row["reviewer"]: row["subjective_score"]
              for row in record["candidates"][0]["reviewers"]}
    assert scores["reader-one"] == pytest.approx(7 / 8)
    assert scores["reader-two"] == 1.0


def test_agreeing_reviewers_give_one_standing(tmp_path):
    """The defect: agreement is reported as a disagreement because the
    two are compared by string rather than by meaning."""
    pack = _pack(tmp_path)
    answers = [_answers(pack, "reader-one"),
               _answers(pack, "reader-two")]
    record = blind_review.results(pack, answers)
    candidate = record["candidates"][0]
    assert candidate["standing"] == blind_review.ACCEPTABLE
    assert candidate["leg_disagreements"] == []
    assert record["counts"]["candidates_disagreed"] == 0


def test_an_unanswered_entry_is_counted_rather_than_dropped(tmp_path):
    """The defect: an entry one reviewer skipped is quietly absent from
    the denominators, and the record claims a full panel."""
    pack = _pack(tmp_path)
    first = pack["entries"][0]["entry_id"]
    answers = [_answers(pack, "reader-one", entry_ids=[first])]
    record = blind_review.results(pack, answers)
    assert record["counts"]["reviews_read"] == 1
    assert record["counts"]["reviews_possible"] == 2
    assert record["counts"]["entries_unanswered"] == 1


def test_the_engine_measurement_is_published_beside_the_panel(tmp_path):
    """The defect: a human's approval is read as clearing a measured
    defect, because the measurement is nowhere in the record."""
    pack = _pack(tmp_path)
    entry_id = next(key for key, row in pack["blinding"].items()
                    if row["candidate_id"] == "cand-alpha")
    objective = {"cand-alpha": [{
        "leg": "shot_quality",
        "status": "fail",
        "basis": judgement.OBJECTIVE_FAILURE,
        "rationale": "the engine's own gate on the delivered file",
        "evidence": [{"artifact": "entries/entry.mp4",
                      "observation": "the file holds an undeclared black "
                                     "frame"}],
        "uncertainty": {"level": "none",
                        "note": "the gate reads the file, not the plan"},
        "measurements": [{
            "name": judgement.BLACK_FRAME_CITATION,
            "citation": judgement.BLACK_FRAME_CITATION,
            "status": "fail",
            "detail": "0.709s of undeclared black at 47.172-47.881s",
        }],
    }]}
    pack = blind_review.build_pack(
        pack_id=pack["pack_id"], brief=pack["brief"],
        candidates=_candidates(tmp_path), out_dir=tmp_path / "pack2",
        objective=objective)
    answers = [_answers(pack, "reader-one"),
               _answers(pack, "reader-two")]
    record = blind_review.results(pack, answers)
    candidate = next(row for row in record["candidates"]
                     if row["entry_id"] == entry_id)
    assert candidate["objective_failures"] == ["shot_quality"]
    assert candidate["standing"] == blind_review.ACCEPTABLE
    assert all(row["review_verdict"] == "fail"
               for row in candidate["reviewers"])


def test_the_review_form_carries_no_producer(tmp_path):
    """The defect: the form the reviewer reads names a producer, even
    though the manifest beside it does not."""
    pack = _pack(tmp_path)
    page = blind_review.render_form(pack)
    assert "producer-alpha" not in page
    assert "producer-beta" not in page
    assert "cand-alpha" not in page
    assert "http://" not in page
    assert "https://" not in page
