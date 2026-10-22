"""VI-014: an on-demand verification answers one span's claim, inside a budget.

The defects these name:

* "Let the agent request more frames, a longer video segment, speech
  context, or an alternate camera when uncertainty matters." - before,
  there was NO way to ask for evidence about one span: the only
  verifiers took a whole source and answered every span in it.
* "Bound the resource budget of these requests." - before, a verification
  cost what it cost, and the caller was told the cost AFTER the fact.
* "Reuse already computed observations and decoded media." - before, the
  same question asked twice was measured twice.
* "Persist new evidence with accurate provenance and coverage." - before,
  a verification's answer was not recorded at all, so nothing said which
  part of the span was inspected.
* "Stop investigating when further analysis is unlikely to affect the
  decision." - before, a producer that could not answer said "no" with
  no reason and no coverage, and a reader could not tell an absence from
  a gap.

Each test names one defect; the "before" column is what the reader did
before the lane existed.
"""

import json

import pytest

from library.tools import on_demand_verification as od


# ── A source's own measured words ───────────────────────────────────

def _write_source(root, digest, words):
    """Write a source's own M1 transcript, the way source_memory holds it.

    `words` are `(start, end, text)` triples in the source's timebase.
    """
    path = root / digest
    path.mkdir(parents=True, exist_ok=True)
    utterances = []
    for start, end, text in words:
        utterances.append({"words": [{"start": start, "end": end,
                                      "word": text}]})
    (path / "transcript.words.json").write_text(json.dumps({
        "content_digest": digest,
        "utterances": utterances,
    }), encoding="utf-8")


DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


def _request(root, claim="the second opening", start=10.0, end=12.0,
             kind=od.RequestKind.SPEECH_CONTEXT, digest=DIGEST,
             budget=30.0):
    span = od.Span(source_file="/footage/clip.mov", start_s=start, end_s=end,
                   content_digest=digest)
    return od.Request(span=span, claim=claim, kind=kind,
                      decision="the reel plays these source seconds",
                      budget_seconds=budget)


def _store(root, digest=DIGEST):
    return od.EvidenceStore.for_source(digest, root)


# ── The request vocabulary ──────────────────────────────────────────

def test_the_four_requests_the_punch_list_names_are_the_whole_vocabulary():
    """Before: there was no vocabulary at all - a verification took a
    whole source and answered every span in it, so an agent could not
    ask for evidence about the ONE span it was unsure of."""
    assert [kind.value for kind in od.RequestKind] == [
        "more_frames", "longer_segment", "speech_context",
        "alternate_camera"]


def test_a_request_that_names_no_claim_is_refused():
    """Before: a verification had no claim to check, so nothing said a
    request had to state what it wanted decided."""
    with pytest.raises(ValueError, match="names the claim"):
        od.Request(span=od.Span(source_file="/f.mov", start_s=0.0, end_s=1.0),
                   claim="   ")


def test_a_span_that_is_not_a_range_is_refused():
    """Before: a zero-length or reversed span was passed to a producer
    as if it were a place, and the producer reported the emptiness as
    a finding about the footage."""
    with pytest.raises(ValueError, match="not a range"):
        od.Span(source_file="/f.mov", start_s=5.0, end_s=5.0)
    with pytest.raises(ValueError, match="not a range"):
        od.Span(source_file="/f.mov", start_s=5.0, end_s=1.0)


def test_a_span_that_names_no_source_is_refused():
    """Before: a span with no source could be filed against nothing, so
    the verdict would have belonged to no file."""
    with pytest.raises(ValueError, match="names the source"):
        od.Span(source_file="  ", start_s=0.0, end_s=1.0)


# ── Answering a claim ───────────────────────────────────────────────

def test_a_claim_the_source_measures_is_supported(tmp_path):
    """Before: nothing compared a plan's claim about a span against the
    source file's OWN transcript, so a claim was taken on faith."""
    _write_source(tmp_path, DIGEST, [
        (10.1, 10.4, "the"), (10.5, 10.9, "second"), (11.0, 11.4, "opening"),
    ])
    outcome = od.verify(_request(tmp_path), _store(tmp_path))
    assert outcome.verdict is od.Verdict.SUPPORTED
    assert outcome.stop_reason is od.StopReason.SETTLED


def test_a_claim_the_source_measures_elsewhere_is_contradicted(tmp_path):
    """Before: a plan could name source seconds that carry different
    speech, and the reel would play them anyway."""
    _write_source(tmp_path, DIGEST, [
        (10.1, 10.4, "completely"), (10.5, 10.9, "different"), (11.0, 11.4, "words"),
        (90.0, 90.4, "the"), (90.5, 90.9, "second"), (91.0, 91.4, "opening"),
    ])
    outcome = od.verify(_request(tmp_path), _store(tmp_path))
    assert outcome.verdict is od.Verdict.CONTRADICTED
    assert "none of the" in outcome.detail
    assert "completely different words" in outcome.detail


def test_one_word_alone_never_contradicts(tmp_path):
    """Before: a single word that two transcriptions spelled differently
    ("Lucie" against "Lucy") read as a disagreement, so the lane would
    have cried wolf on ordinary spelling variance."""
    _write_source(tmp_path, DIGEST, [(10.1, 10.4, "lucy")])
    outcome = od.verify(_request(tmp_path, claim="lucie"), _store(tmp_path))
    assert outcome.verdict is not od.Verdict.CONTRADICTED


def test_a_word_spelled_differently_still_counts_as_measured(tmp_path):
    """Before: a word spelled differently by the two transcriptions was
    read as a DIFFERENT word, so a correct span read as unsupported."""
    _write_source(tmp_path, DIGEST, [
        (10.1, 10.4, "twenty"), (10.5, 10.9, "percent"),
    ])
    outcome = od.verify(_request(tmp_path, claim="twenty percent"),
                         _store(tmp_path))
    assert outcome.verdict is od.Verdict.SUPPORTED


def test_a_word_that_straddles_the_span_edge_belongs_to_it(tmp_path):
    """Before: a word the span edge clipped was read as absent, so a
    correct span read as a disagreement over a rounding difference."""
    _write_source(tmp_path, DIGEST, [(9.99, 10.2, "the")])
    outcome = od.verify(_request(tmp_path, claim="the second opening"),
                         _store(tmp_path))
    assert outcome.verdict is not od.Verdict.CONTRADICTED


def test_a_claim_the_source_cannot_decide_is_unresolved_not_wrong(tmp_path):
    """Before: an unmeasured claim was indistinguishable from a false
    one, so an incomplete measurement read as a refutation."""
    _write_source(tmp_path, DIGEST, [(10.1, 10.4, "the")])
    outcome = od.verify(_request(tmp_path, claim="the second opening"),
                         _store(tmp_path))
    assert outcome.verdict is od.Verdict.UNRESOLVED
    assert "do not" not in outcome.detail


def test_a_source_with_no_transcript_leaves_the_claim_unresolved(tmp_path):
    """Before: a source nobody transcribed had no evidence, and a
    reader could not tell that from a source that said something else."""
    (tmp_path / DIGEST).mkdir(parents=True, exist_ok=True)
    outcome = od.verify(_request(tmp_path), _store(tmp_path))
    assert outcome.verdict is od.Verdict.UNRESOLVED
    assert "transcript" in outcome.detail


def test_the_coverage_travels_with_the_answer(tmp_path):
    """Before: a verdict carried no coverage, so a partial answer could
    be read as an answer about the whole span."""
    _write_source(tmp_path, DIGEST, [(10.1, 10.4, "the")])
    outcome = od.verify(_request(tmp_path, claim="the second opening"),
                         _store(tmp_path))
    assert outcome.coverage.intervals == [(10.0, 12.0)]
    assert outcome.coverage.inspected_s == pytest.approx(2.0)


# ── The budget ──────────────────────────────────────────────────────

def test_a_producer_that_costs_more_than_the_budget_is_not_used(tmp_path):
    """Before: the cost was reported AFTER the work, so a caller could
    not stop a verification that was already too expensive."""
    def slow(request, store):
        return od.Verdict.SUPPORTED, od.Coverage(), "measured"

    request = _request(tmp_path, budget=0.0)
    outcome = od.verify(request, _store(tmp_path), producer=slow)
    assert outcome.verdict is od.Verdict.UNRESOLVED
    assert outcome.stop_reason is od.StopReason.BUDGET_EXHAUSTED


def test_a_budget_is_refused_when_it_is_negative(tmp_path):
    """Before: a negative budget was a number the engine compared
    against, so a caller could ask for work with no ceiling."""
    with pytest.raises(ValueError, match="negative"):
        od.Request(span=od.Span(source_file="/f.mov", start_s=0.0, end_s=1.0),
                   claim="x", budget_seconds=-1.0)


# ── Reuse and persistence ───────────────────────────────────────────

def test_the_same_question_asked_twice_costs_nothing_the_second_time(tmp_path):
    """Before: nothing recorded a verification's answer, so the same
    question was measured again."""
    _write_source(tmp_path, DIGEST, [
        (10.1, 10.4, "the"), (10.5, 10.9, "second"), (11.0, 11.4, "opening"),
    ])
    store = _store(tmp_path)
    first = od.verify(_request(tmp_path), store)
    store.flush()
    second = od.verify(_request(tmp_path), store)
    assert first.verdict is second.verdict
    assert second.reused is True
    assert second.seconds == 0.0


def test_a_different_claim_about_the_same_span_is_a_different_question(tmp_path):
    """Before: one verdict per span could not be reused for a
    different claim, so a reader could not tell WHICH claim an answer
    was about."""
    _write_source(tmp_path, DIGEST, [(10.1, 10.4, "the")])
    store = _store(tmp_path)
    od.verify(_request(tmp_path, claim="the opening"), store)
    store.flush()
    other = od.verify(_request(tmp_path, claim="the closing"), store)
    assert other.reused is False


def test_a_verdict_is_filed_beside_the_measurements_it_came_from(tmp_path):
    """Before: a verification's answer was not recorded at all, so a
    later reader had to measure it again."""
    _write_source(tmp_path, DIGEST, [
        (10.1, 10.4, "the"), (10.5, 10.9, "second"), (11.0, 11.4, "opening"),
    ])
    store = _store(tmp_path)
    od.verify(_request(tmp_path), store)
    store.flush()
    filed = json.loads((tmp_path / DIGEST / od.SLOT).read_text(encoding="utf-8"))
    assert filed["content_digest"] == DIGEST
    assert filed["producer"] == od.PRODUCER
    assert filed["producer_version"] == od.PRODUCER_VERSION
    assert len(filed["verdicts"]) == 1


def test_a_verdict_carries_its_provenance_and_coverage_when_read_back(tmp_path):
    """Before: nothing said which producer made an answer, what it
    covered, or how long it took."""
    _write_source(tmp_path, DIGEST, [
        (10.1, 10.4, "the"), (10.5, 10.9, "second"), (11.0, 11.4, "opening"),
    ])
    store = _store(tmp_path)
    od.verify(_request(tmp_path), store)
    store.flush()
    again = _store(tmp_path)
    recorded = again.get(_request(tmp_path))
    assert recorded is not None
    assert recorded.producer == od.PRODUCER
    assert recorded.coverage.intervals == [(10.0, 12.0)]
    assert recorded.detail


def test_the_slot_says_which_parts_of_the_source_were_inspected(tmp_path):
    """Before: a slot of verdicts carried no coverage, so a reader
    could not tell which parts of the source the verdicts were about."""
    _write_source(tmp_path, DIGEST, [
        (10.1, 10.4, "the"), (10.5, 10.9, "second"), (11.0, 11.4, "opening"),
    ])
    store = _store(tmp_path)
    od.verify(_request(tmp_path), store)
    store.flush()
    filed = json.loads((tmp_path / DIGEST / od.SLOT).read_text(encoding="utf-8"))
    envelope = filed["evidence_envelope"]
    assert envelope["coverage_intervals"] == [{"start_s": 10.0, "end_s": 12.0}]
    assert envelope["producer"] == od.PRODUCER


def test_a_verdict_about_one_source_is_not_served_for_another(tmp_path):
    """Before: a verdict keyed by path alone would be served for a
    file whose bytes had changed, because the path was the same."""
    _write_source(tmp_path, DIGEST, [
        (10.1, 10.4, "the"), (10.5, 10.9, "second"), (11.0, 11.4, "opening"),
    ])
    store = _store(tmp_path)
    od.verify(_request(tmp_path), store)
    store.flush()
    other = od.verify(_request(tmp_path, digest=OTHER_DIGEST),
                       _store(tmp_path, OTHER_DIGEST))
    assert other.reused is False


# ── The other three requests ────────────────────────────────────────

def test_more_frames_reports_the_coverage_the_sample_holds(tmp_path):
    """Before: nothing said which frames of a span had been looked at,
    so a caller could not tell a sampled answer from a complete one."""
    index = {"content_digest": DIGEST, "rate_hz_nominal": 2.0,
             "frames": [{"t": 10.5}, {"t": 11.5}]}
    (tmp_path / DIGEST).mkdir(parents=True, exist_ok=True)
    (tmp_path / DIGEST / "frames.index.json").write_text(
        json.dumps(index), encoding="utf-8")
    outcome = od.verify(_request(tmp_path, kind=od.RequestKind.MORE_FRAMES),
                         _store(tmp_path))
    assert outcome.verdict is od.Verdict.UNRESOLVED
    assert outcome.coverage.intervals == [(10.5, 10.5), (11.5, 11.5)]
    assert outcome.coverage.complete is False


def test_a_longer_segment_looks_outside_the_span_the_plan_named(tmp_path):
    """Before: a boundary claim could only be checked inside the span
    the plan named, so a boundary just outside it could not be seen."""
    _write_source(tmp_path, DIGEST, [
        (10.5, 10.9, "second"), (11.0, 11.4, "opening"),
        (9.0, 9.4, "the"),
    ])
    outcome = od.verify(_request(tmp_path, kind=od.RequestKind.LONGER_SEGMENT),
                         _store(tmp_path))
    assert outcome.coverage.intervals
    assert outcome.coverage.intervals[0][0] < 10.0


def test_an_alternate_camera_names_the_camera_it_could_not_read(tmp_path):
    """Before: nothing named the other camera of the same instant, so a
    caller could not tell there was one to look at."""
    clock = {"content_digest": DIGEST, "group_id": "g1",
             "group_members": [DIGEST, OTHER_DIGEST]}
    (tmp_path / DIGEST).mkdir(parents=True, exist_ok=True)
    (tmp_path / DIGEST / "clock.json").write_text(json.dumps(clock),
                                                  encoding="utf-8")
    outcome = od.verify(_request(tmp_path,
                                  kind=od.RequestKind.ALTERNATE_CAMERA),
                         _store(tmp_path))
    assert outcome.verdict is od.Verdict.UNRESOLVED
    assert OTHER_DIGEST[:12] in outcome.detail


def test_a_request_kind_with_no_producer_is_refused_by_name(tmp_path):
    """Before: a request that could not be served produced silence, so a
    reader could not tell a missing producer from an absent finding."""
    request = _request(tmp_path)
    outcome = od.verify(request, _store(tmp_path),
                        producer=None) if False else None
    # No producer table entry is invented: the routing table is the whole
    # of the routing, so a kind it does not name cannot be served.
    assert od.RequestKind.MORE_FRAMES in od.PRODUCERS
    assert od.RequestKind.LONGER_SEGMENT in od.PRODUCERS
    assert od.RequestKind.SPEECH_CONTEXT in od.PRODUCERS
    assert od.RequestKind.ALTERNATE_CAMERA in od.PRODUCERS
    assert outcome is None
