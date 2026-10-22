"""One retrieval contract for every footage entry point (R02 defects).

Each test names one defect this lane exists to prevent:

* **R02-1** - `search_footage` ran the text embedding pass first on EVERY
  query, even one whose plan names no textual requirement ("applause
  after the speech" paid for a transcript ranking it cannot use).  A
  query its own lanes answered must execute no text pass, and the
  skipped lane must say so by name.
* **R02-2** - skipping the text pass must never strand a requirement no
  other lane answered.  When the covering lane is missing or refuses,
  the text lane runs as backstop and says what it ran for.
* **R02-3** - "dog barking" never reached the sound lane: the cue table
  had no bark family, so an acoustic question was answered from
  transcript and still frames only.
* **R02-4** - the text pass ran with no facet filters, so deterministic
  filtering never preceded the expensive ranking.  Caller-supplied
  facets must reach `search_report` unchanged.
* **R02-5** - equivalent requests from the CLI, the planner and the
  internal tools must see the same candidates.  The multilane text lane
  and a direct `search_report` with the same filters return the same
  rows.
* **R02-6** - every route returns timestamped evidence carrying its
  uncertainty and its provenance, whatever lane produced it.
* **R02-7** - lookup vs semantic similarity is typed on the route, not
  implied by which function was called.  OCR, sound, timeline and
  roster reads are LOOKUPs; only the text dense pass and the frame
  lane are SEMANTIC.
* **R02-8** - a skipped lane is observable in the answer, never silent.
"""

import json
from pathlib import Path

import pytest

from library.tools.retrieval import contract as retrieval_contract
from library.tools.retrieval import search as retrieval_search
from library.tools.retrieval.requirements import decompose


def _hit(seg, clip, kind, start, text, score=0.9):
    return {
        "segment_id": seg,
        "clip_id": clip,
        "filename": clip + ".mp4",
        "kind": kind,
        "start": start,
        "end": start + 2.0,
        "score": score,
        "text": text,
        "dense_score": score,
        "lexical_score": 0.5,
        "word_hits": [],
    }


class StubTextIndex:
    """A text index that records its calls and answers from canned rows."""

    def __init__(self, calls):
        self.calls = calls

    def search_report(
        self, query, top_k=5, mode="hybrid", floor=None, filters=None, **_kw
    ):
        self.calls.append({"query": query, "filters": filters})
        return {
            "query": query,
            "mode": mode,
            "results": [
                _hit(
                    "seg1",
                    "clip_001",
                    "speech",
                    10.0,
                    "he explains the pricing rule here",
                ),
                _hit(
                    "seg2",
                    "clip_002",
                    "scene",
                    40.0,
                    "wide shot of the parking lot",
                    0.8,
                ),
            ],
            "weak": [],
            "best_rejected": None,
            "error": None,
            "degraded": None,
            "answer_status": "answered",
            "backend_answered": "stub-backend",
            "abstention": {"status": "answered"},
        }

    def ocr_search(self, query, top_k=5):
        return {
            "query": query,
            "hits": [
                {
                    "clip_id": "clip_003",
                    "filename": "clip_003.mp4",
                    "text": "PRICE $9",
                    "start": 5.0,
                    "end": 7.0,
                    "confidence": 0.95,
                },
            ],
            "error": None,
            "available_texts": ["PRICE $9"],
            "abstention": None,
        }

    def sound_search(self, query, top_k=5):
        return {
            "query": query,
            "hits": [
                {
                    "clip_id": "clip_004",
                    "filename": "clip_004.mp4",
                    "label": "Laughter",
                    "start": 20.0,
                    "end": 22.0,
                    "confidence": 0.9,
                },
            ],
            "error": None,
            "available_labels": ["Laughter", "Bark"],
            "abstention": None,
        }


class StubFrameIndex:
    def search_ranges(self, query, top_ranges=5, temporal=False, **_kw):
        return {
            "ranges": [
                {
                    "clip_id": "clip_002",
                    "filename": "clip_002.mp4",
                    "start": 40.0,
                    "end": 44.0,
                    "best_score": 0.85,
                    "n_frames": 4,
                },
            ],
            "abstention": None,
        }

    @property
    def frames(self):
        return []


class RefusingFrameIndex(StubFrameIndex):
    """A frame lane that refuses by report, the designed channel.

    A still frame cannot establish an action, so the lane answers with
    a `refused` report rather than an empty observation - and refuses
    without raising, the way the shipped lane does."""

    def search_ranges(self, query, top_ranges=5, temporal=False, **_kw):
        return {
            "ranges": [],
            "refused": "a still frame cannot establish this action",
            "abstention": None,
        }


COVERAGE = {
    "clips_in_catalog": ["clip_001", "clip_002", "clip_003", "clip_004"],
    "per_clip": {
        clip: {"speech": True, "picture": True, "sound": True, "ocr": True}
        for clip in ("clip_001", "clip_002", "clip_003", "clip_004")
    },
}

SOURCES = {
    clip: {"source_digest": "d", "file": f"{clip}.mp4"}
    for clip in ("clip_001", "clip_002", "clip_003", "clip_004")
}


def run(query, *, frame_index=None, filters=None):
    calls = []
    result = retrieval_search.search_footage(
        "/nonexistent-project",
        query,
        top_k=5,
        index=StubTextIndex(calls),
        frame_index=StubFrameIndex() if frame_index is None else frame_index,
        coverage=COVERAGE,
        sources=SOURCES,
        filters=filters,
    )
    return result, calls


def recorded_set():
    path = Path(retrieval_contract.__file__).with_name("contract_set.json")
    return json.loads(path.read_text(encoding="utf-8"))["queries"]


# ─── R02-1: no text pass for queries with no textual requirement ────


@pytest.mark.parametrize("entry", recorded_set(), ids=lambda e: e["query"][:40])
def test_recorded_query_runs_the_lanes_the_contract_decided(entry):
    result, calls = run(entry["query"])
    assert result["lanes_ran"] == entry["expected_lanes_ran"], (
        f"{entry['query']!r}: {entry['notes']}"
    )
    text_lane = result["lanes"].get("text") or {}
    assert text_lane.get("policy") == entry["text_policy"]
    if entry["text_policy"] == "skip":
        assert not calls, "a skipped text lane executes no embedding pass at all"
        assert text_lane.get("reason") == retrieval_contract.SKIP_NO_REQUIREMENT


def test_acoustic_query_pays_for_no_transcript_ranking():
    result, calls = run("applause after the speech")
    assert "text" not in result["lanes_ran"]
    assert result["lanes_ran"] == ["sound", "time"]
    assert [row["kind"] for row in result["hits"]] == ["sound"]


def test_visual_query_pays_for_no_transcript_ranking():
    result, calls = run("wide shot of the parking lot")
    assert "text" not in result["lanes_ran"]
    assert [row["kind"] for row in result["hits"]] == ["frames"]


# ─── R02-2: the backstop still owes the plan its unanswered parts ───


def test_text_runs_as_backstop_when_the_frame_lane_is_missing():
    result, calls = run(
        "wide shot of the parking lot", frame_index=RefusingFrameIndex()
    )
    text_lane = result["lanes"]["text"]
    assert text_lane.get("policy") == "backstop", (
        "the visual requirement is uncovered, so the text lane owes it"
    )
    assert "visual:composition" in (text_lane.get("backstop_for") or []), (
        "the backstop says WHAT it ran for"
    )
    assert len(calls) == 1


def test_visual_refusal_is_not_reported_as_an_empty_observation():
    result, _calls = run(
        "wide shot of the parking lot", frame_index=RefusingFrameIndex()
    )
    assert result["lanes"]["visual"]["refused"], "a refused lane says it refused"
    assert "visual" not in result["lanes_ran"]


def test_use_visual_off_is_covered_by_the_backstop():
    calls = []
    result = retrieval_search.search_footage(
        "/nonexistent-project",
        "wide shot of the parking lot",
        top_k=5,
        index=StubTextIndex(calls),
        frame_index=StubFrameIndex(),
        coverage=COVERAGE,
        sources=SOURCES,
        use_visual=False,
    )
    assert (
        result["lanes"]["visual"]["reason"] == retrieval_contract.SKIP_NO_VISUAL_CALLER
    )
    assert result["lanes"]["text"]["policy"] == "backstop"
    assert len(calls) == 1


# ─── R02-3: barking reaches the sound lane ──────────────────────────


def test_dog_barking_names_a_sound_requirement():
    plan = decompose("dog barking in the background")
    assert any(
        requirement.key == "acoustic:sound_event" for requirement in plan.requirements
    ), "barking is heard, not said: the plan must ask the sound lane"


def test_dog_barking_runs_the_sound_lane():
    result, _calls = run("dog barking in the background")
    assert "sound" in result["lanes_ran"]
    assert any(row["kind"] == "sound" for row in result["hits"])


# ─── R02-4: deterministic facets reach the text pass ────────────────


def test_caller_filters_are_forwarded_before_ranking():
    result, calls = run("he explains the pricing rule", filters={"clip_id": "clip_001"})
    assert calls and calls[0]["filters"] == {"clip_id": "clip_001"}, (
        "facets are applied BEFORE the embedding ranking, never after it"
    )
    assert result["lanes"]["text"]["filters"] == {"clip_id": "clip_001"}


# ─── R02-5: equivalent requests see the same candidates ─────────────


def test_multilane_text_rows_match_a_direct_report_with_same_filters():
    filters = {"clip_id": "clip_001"}
    calls = []
    index = StubTextIndex(calls)
    result = retrieval_search.search_footage(
        "/nonexistent-project",
        "he explains the pricing rule",
        top_k=5,
        index=index,
        frame_index=StubFrameIndex(),
        coverage=COVERAGE,
        sources=SOURCES,
        filters=filters,
    )
    direct = index.search_report(
        "he explains the pricing rule", top_k=5, filters=filters
    )
    multilane_ids = sorted(
        row["segment_id"]
        for row in result["hits"]
        if row["kind"] in ("speech", "chunk")
    )
    direct_ids = sorted(
        hit["segment_id"]
        for hit in direct["results"]
        if hit["kind"] in ("speech", "chunk")
    )
    assert multilane_ids == direct_ids, (
        "the CLI, the planner and the internal tools read the same rows"
    )
    assert calls[0]["filters"] == calls[1]["filters"] == filters


# ─── R02-6: one envelope on every route ─────────────────────────────


def test_every_hit_carries_timestamp_uncertainty_and_provenance():
    for entry in recorded_set():
        result, _calls = run(entry["query"])
        for hit in result["hits"]:
            complete, missing = retrieval_contract.envelope_complete(hit)
            assert complete, (
                f"{entry['query']!r} hit {hit.get('segment_id')}: missing {missing}"
            )
            assert isinstance(hit["start"], (int, float))
            assert isinstance(hit["end"], (int, float))
            assert hit["provenance"]["pass_class"] in (
                retrieval_contract.LOOKUP,
                retrieval_contract.SEMANTIC,
            )


# ─── R02-7: lookup vs semantic is typed ─────────────────────────────


def test_only_embedding_lanes_are_semantic():
    assert retrieval_contract.LANE_PASS_CLASS == {
        "text": retrieval_contract.SEMANTIC,
        "visual": retrieval_contract.SEMANTIC,
        "ocr": retrieval_contract.LOOKUP,
        "sound": retrieval_contract.LOOKUP,
        "time": retrieval_contract.LOOKUP,
        "relational": retrieval_contract.LOOKUP,
    }


def test_eight_modalities_each_name_a_lane_and_a_pass_class():
    assert set(retrieval_contract.MODALITY_ROUTES) == {
        "text",
        "ocr",
        "appearance",
        "objects",
        "action",
        "audio",
        "identity",
        "temporal",
    }
    for modality, route in retrieval_contract.MODALITY_ROUTES.items():
        assert route["lane"] in retrieval_contract.LANES, modality
        assert route["pass_class"] in (
            retrieval_contract.LOOKUP,
            retrieval_contract.SEMANTIC,
        ), modality
        assert route["requirement_keys"], modality


# ─── R02-8: skipped lanes are observable ────────────────────────────


def test_skipped_lanes_say_why_they_did_not_run():
    result, _calls = run("laughter from the audience")
    assert result["lanes"]["sound"]["ran"] is True
    for lane in ("ocr", "visual"):
        assert result["lanes"][lane]["ran"] is False
        assert result["lanes"][lane]["reason"] == retrieval_contract.SKIP_NO_REQUIREMENT
    assert "relational" not in result["lanes"], (
        "no relational requirement, no relational entry at all"
    )


def test_relational_requirement_names_its_graph_query():
    result, _calls = run("find the reaction shot after the announcement")
    assert result["lanes"]["relational"]["ran"] is False
    assert "named" in result["lanes"]["relational"]["error"]
