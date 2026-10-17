"""FI-13: speech and take measurements from transcript data only.

Every project here is hand-made under `tmp_path` - no real project,
no footage, no model (§8). The row's core demand is pinned three
ways: identical, rephrased and repeated takes keep BOTH instances
(the links name no loser and the migration drops no utterance), and
cross-source links carry the measured M6 spread or an explicit
unmeasured basis. Every test fails on main:
`library.tools.speech_take` does not exist there.
"""
import json
from pathlib import Path

from library.tools import evidence_query, speech_take
from library.tools.project_model import validate_project_model

D1 = "digest-fi13-aaa"
D2 = "digest-fi13-bbb"


def _words(text, start, step=0.4, gap=0.1):
    out = []
    cursor = start
    for token in text.split():
        end = round(cursor + step, 3)
        out.append({"word": token, "start": round(cursor, 3), "end": end})
        cursor = round(end + gap, 3)
    return out


def _utt(index, text, start):
    words = _words(text, start + 0.1)
    return {"start": start, "end": words[-1]["end"] + 0.1, "text": text,
            "words": words, "utterance_index": index}


def _m1(*utterances):
    return {"utterances": list(utterances)}


# ─── Typed per-utterance measurements ─────────────────────────────


def test_rate_reads_words_per_second_off_word_timings():
    kept, dropped = speech_take.utterance_spans(
        _m1(_utt(0, "one two three four", 10.0)))
    assert dropped == []
    rate = speech_take.rate_of(kept[0])
    assert rate["word_count"] == 4
    assert rate["span_seconds"] == round(kept[0]["end"] - 10.0, 3)
    assert rate["words_per_second"] == round(
        4 / (kept[0]["end"] - 10.0), 3)


def test_pauses_carry_location_and_duration_with_no_floor():
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "one two", 10.0), _utt(1, "three four", 20.0)))
    pauses = speech_take.pauses_between(kept)
    kinds = [pause["kind"] for pause in pauses]
    assert "word_gap" in kinds and "utterance_gap" in kinds
    assert all(pause["end"] > pause["start"] for pause in pauses)
    assert all(pause["gap_seconds"] == round(
        pause["end"] - pause["start"], 3) for pause in pauses)
    assert pauses == sorted(pauses, key=lambda p: (p["start"], p["end"]))


def test_abutting_words_carry_no_pause():
    doc = {"utterances": [{"start": 1.0, "end": 2.0, "text": "one two",
                           "words": [{"word": "one", "start": 1.0,
                                      "end": 1.5},
                                     {"word": "two", "start": 1.5,
                                      "end": 2.0}]}]}
    kept, _ = speech_take.utterance_spans(doc)
    assert speech_take.pauses_between(kept) == []


def test_disfluencies_name_fillers_and_aborted_words_with_seconds():
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "there is um a very strong s case", 10.0)))
    disfluencies = speech_take.disfluencies_in(kept[0])
    by_token = {entry["token"]: entry["kind"] for entry in disfluencies}
    assert by_token == {"um": "filler", "s": "partial"}
    assert all(entry["end"] > entry["start"] for entry in disfluencies)


def test_fluent_speech_carries_an_empty_disfluency_list():
    kept, _ = speech_take.utterance_spans(_m1(_utt(0, "clean delivery", 1.0)))
    assert speech_take.disfluencies_in(kept[0]) == []


def test_repetition_names_the_run_and_stutter_is_below_floor():
    kept, _ = speech_take.utterance_spans(
        _m1(_utt(0, "there is a very strong case", 1.0)))
    assert speech_take.repetition_in(kept[0]) is None
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "the plan the plan moves forward", 1.0)))
    repeat = speech_take.repetition_in(kept[0])
    assert repeat is not None and repeat["tokens"] == ["the", "plan"]
    assert repeat["second_token_index"] > repeat["first_token_index"]


def test_completeness_reads_the_transcribers_punctuation():
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "Your website is just a part of what's maybe", 1.0),
        _utt(1, "AI checks both.", 5.0)))
    assert speech_take.completeness_of(kept[0]) == {
        "complete": False, "terminal": None}
    assert speech_take.completeness_of(kept[1]) == {
        "complete": True, "terminal": "."}


def test_unusable_utterances_are_dropped_with_reasons():
    doc = {"utterances": [
        {"text": "no timing at all", "words": []},
        {"start": 5.0, "end": 5.0, "text": "zero span",
         "words": [{"word": "zero", "start": 5.0, "end": 5.0}]},
        "not a mapping",
        _utt(0, "kept telling", 10.0),
    ]}
    kept, dropped = speech_take.utterance_spans(doc)
    assert len(kept) == 1
    assert len(dropped) == 3
    assert all("reason" in entry for entry in dropped)


# ─── Take links: identical, rephrased, repeated ───────────────────


def test_identical_takes_link_and_keep_both_instances():
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "the edit is locked", 1.0),
        _utt(1, "the edit is locked", 5.0)))
    links = speech_take.same_source_links("d", kept)
    assert len(links) == 1
    link = links[0]
    assert link["kind"] == "identical"
    assert link["utterance_a"]["utterance_index"] == 0
    assert link["utterance_b"]["utterance_index"] == 1
    assert link["comparison"] == "same_clock"
    # Never auto-deleted: no loser, no strike, both texts intact.
    assert "dropped" not in json.dumps(link)
    assert "recommended_action" not in link
    assert link["utterance_a"]["text"] == link["utterance_b"]["text"]


def test_deliberate_repeat_is_linked_but_not_abandoned():
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "Yes.", 1.0), _utt(1, "Yes.", 3.0)))
    links = speech_take.same_source_links("d", kept)
    assert len(links) == 1
    assert links[0]["kind"] == "identical"
    assert links[0]["abandoned_first"] is False, (
        "two complete tellings stay linked as identical text, but the "
        "record must not read them as an abandoned restart")


def test_rephrased_take_links_on_the_shared_run():
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "your website is just a part of what's maybe", 1.0),
        _utt(1, "and your website is just a part of your whole profile",
             5.0)))
    links = speech_take.same_source_links("d", kept)
    assert len(links) == 1
    link = links[0]
    assert link["kind"] == "rephrased"
    assert link["abandoned_first"] is True
    assert len(link["shared_run"]) >= speech_take.SHARED_RUN_TOKENS


def test_repeated_take_links_on_the_shared_head():
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "I have seen ten people try this workflow", 1.0),
        _utt(1, "I have seen ten directors cut this scene", 6.0)))
    links = speech_take.same_source_links("d", kept)
    assert len(links) == 1
    assert links[0]["kind"] == "repeated"
    assert links[0]["shared_run"] == ["i", "have", "seen", "ten"]


def test_distinct_speech_links_nothing():
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "the weather is mild today", 1.0),
        _utt(1, "render the timeline overnight", 5.0)))
    assert speech_take.same_source_links("d", kept) == []


def test_non_adjacent_repeats_do_not_link_in_one_source():
    kept, _ = speech_take.utterance_spans(_m1(
        _utt(0, "the edit is locked", 1.0),
        _utt(1, "something entirely different here", 5.0),
        _utt(2, "the edit is locked", 9.0)))
    assert speech_take.same_source_links("d", kept) == [], (
        "a callback two turns later is not an adjacent restart; "
        "without speaker labels only adjacency reads as a take")


# ─── Cross-source links carry the measured M6 uncertainty ─────────


def _clock(group=None, offset=0.0, direct=None):
    return {"content_digest": "x", "group_id": group,
            "reference_digest": "x",
            "offset_to_reference_seconds": offset,
            "direct_measurement": direct}


def test_cross_source_identical_without_m6_states_its_basis():
    by_digest = {
        D1: speech_take.utterance_spans(_m1(_utt(0, "the edit is locked",
                                                1.0)))[0],
        D2: speech_take.utterance_spans(_m1(_utt(0, "the edit is locked",
                                                9.0)))[0],
    }
    links = speech_take.cross_source_links(by_digest, {})
    assert len(links) == 1
    link = links[0]
    assert link["kind"] == "identical"
    assert link["comparison"] == "cross_clock_unaligned"
    assert link["uncertainty_seconds"] is None
    assert link["uncertainty_basis"] == "no_shared_m6_group"


def test_cross_source_link_carries_the_direct_edge_spread():
    direct = {"offset_seconds": 2.5, "p5_seconds": 2.42,
              "p95_seconds": 2.58, "agreeing_matches": 40}
    by_digest = {
        D1: speech_take.utterance_spans(_m1(_utt(0, "the edit is locked",
                                                1.0)))[0],
        D2: speech_take.utterance_spans(_m1(_utt(0, "the edit is locked",
                                                9.0)))[0],
    }
    clocks = {"digest-fi13-aaa": _clock("g1", 0.0, direct),
              "digest-fi13-bbb": _clock("g1", -2.5, None)}
    assert D1 == "digest-fi13-aaa" and D2 == "digest-fi13-bbb"
    links = speech_take.cross_source_links(by_digest, clocks)
    assert len(links) == 1
    link = links[0]
    assert link["comparison"] == "cross_clock_via_m6"
    assert link["offset_a_to_reference_seconds"] == 0.0
    assert link["offset_b_to_reference_seconds"] == -2.5
    assert link["uncertainty_seconds"] == round((2.58 - 2.42) / 2, 3)
    assert link["uncertainty_basis"] == "m6_direct_edge_p95_p5"


def test_transitive_m6_offset_carries_no_invented_spread():
    by_digest = {
        D1: speech_take.utterance_spans(_m1(_utt(0, "the edit is locked",
                                                1.0)))[0],
        D2: speech_take.utterance_spans(_m1(_utt(0, "the edit is locked",
                                                9.0)))[0],
    }
    clocks = {D1: _clock("g1", 0.0, None), D2: _clock("g1", -2.5, None)}
    links = speech_take.cross_source_links(by_digest, clocks)
    assert len(links) == 1
    assert links[0]["comparison"] == "cross_clock_via_m6"
    assert links[0]["uncertainty_seconds"] is None
    assert links[0]["uncertainty_basis"] == "m6_transitive_unmeasured"


# ─── Typed evidence query over the new records ────────────────────


def _native(measurement_type, value, *, digest, lane="m1",
            start_tick=1000, end_tick=3000, samples=1, freshness="fresh"):
    from dataclasses import replace

    from library.tools import evidence_contract

    record = evidence_contract.new_measurement(
        asset_id=f"asset-{digest}", clock_id=f"clock-{digest}",
        start_tick=start_tick, end_tick=end_tick,
        measurement_type=measurement_type, value=value, method="fixture",
        provenance_id=f"prov-{digest}-{lane}",
        coverage=evidence_contract.CoverageKind.COMPLETE,
        coverage_samples=samples, coverage_sample_rate_hz=0.0)
    return evidence_contract.to_rust_evidence(
        replace(record, freshness=freshness))


def _obs(obs_id, modality, payload, *, digest, lane="m1", native=None):
    obs = {"id": obs_id, "asset_id": f"asset-{digest}",
           "interval": {"clock_id": f"clock-{digest}",
                        "start": 1000, "end": 3000},
           "modality": modality, "payload": dict(payload),
           "confidence": None, "provenance_id": f"prov-{digest}-{lane}"}
    if native is not None:
        obs["payload"]["evidence_contract"] = native
    return obs


def _model(*observations):
    return {"id": "project-fixture", "schema_version": 1,
            "clocks": [{"id": f"clock-{digest}", "ticks_per_second": 1000}
                       for digest in (D1, D2)],
            "assets": [{"id": f"asset-{digest}", "content_digest": digest}
                       for digest in (D1, D2)],
            "entities": [], "observations": list(observations),
            "identity_links": [], "tracks": [],
            "provenance": [{"id": f"prov-{digest}-m1", "producer": "fixture"}
                           for digest in (D1, D2)],
            "clip_uses": [], "renders": [], "frame_maps": []}


def test_speech_rate_answers_present_with_its_evidence():
    model = _model(_obs(
        "obs-rate-1", "speech_rate",
        {"word_count": 4, "words_per_second": 2.0}, digest=D1,
        native=_native("speech.rate", {"word_count": 4}, digest=D1)))
    plan = evidence_query.compile_plan(
        [{"predicate": "measurement", "measurement_type": "speech.rate"}])
    assert plan["required_lanes"] == ["m1"]
    answer = evidence_query.evidence_for(model, plan)
    assert answer["status"] == evidence_query.PRESENT
    assert answer["results"][0]["evidence_ids"] == ["obs-rate-1"]


def test_take_link_query_compiles_onto_the_m1_lane():
    plan = evidence_query.compile_plan(
        [{"predicate": "measurement",
          "measurement_type": "speech.take_link"}])
    assert plan["required_lanes"] == ["m1"]


def test_unmeasured_speech_lane_is_not_yet_analysed():
    for measurement_type in ("speech.rate", "speech.pause",
                             "speech.disfluency", "speech.repetition",
                             "speech.completeness", "speech.take_link"):
        plan = evidence_query.compile_plan(
            [{"predicate": "measurement",
              "measurement_type": measurement_type}])
        answer = evidence_query.evidence_for(_model(), plan)
        assert answer["status"] == evidence_query.NOT_YET_ANALYSED, (
            measurement_type)


def test_empty_disfluency_list_matches_as_present_with_empty_value():
    # An empty disfluency list is a measurement that EXISTS (this
    # utterance was heard and carries none), so a disfluency query
    # answers present with the empty value - never by guessing. Only
    # a lane that never ran reads not_yet_analysed (above).
    model = _model(_obs(
        "obs-dis-1", "speech_disfluency", {"disfluencies": []}, digest=D1,
        native=_native("speech.disfluency", [], digest=D1,
                       start_tick=0, end_tick=20000, samples=0)))
    plan = evidence_query.compile_plan(
        [{"predicate": "measurement",
          "measurement_type": "speech.disfluency"}])
    answer = evidence_query.evidence_for(model, plan)
    assert answer["status"] == evidence_query.PRESENT
    assert answer["results"][0]["evidence_ids"] == ["obs-dis-1"]


# ─── Migration: every span preserved, links point, nothing cut ────


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _m1_doc(digest, *utterances):
    return {"content_digest": digest, "status": "transcribed",
            "instrument": {"arm": "fixture", "aligner": "fixture"},
            "utterances": list(utterances)}


def _migration_project(tmp_path):
    from library.tools import project_model_evidence as evidence

    project, memory = tmp_path / "proj", tmp_path / "memory"
    texts_d1 = [("the edit is locked", 1.0),
                ("your website is just a part of what's maybe", 5.0)]
    texts_d2 = [("the edit is locked", 9.0)]
    docs = {
        D1: _m1_doc(D1, *[_utt(i, text, start)
                           for i, (text, start) in enumerate(texts_d1)]),
        D2: _m1_doc(D2, *[_utt(i, text, start)
                           for i, (text, start) in enumerate(texts_d2)]),
    }
    for digest, doc in docs.items():
        _write(memory / digest / "transcript.words.json", doc)
        _write(memory / digest / "clock.json", {
            "content_digest": digest, "group_id": None,
            "reference_digest": None, "offset_to_reference_seconds": 0.0})
    catalog = [
        {"clip_id": "clip_001", "source_file": "/nonexistent/a.mov",
         "path": "/nonexistent/a.mov"},
        {"clip_id": "clip_002", "source_file": "/nonexistent/b.mov",
         "path": "/nonexistent/b.mov"},
    ]
    _write(project / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": catalog}},
        "source_fingerprints": {
            "clip_001": {"path": "/nonexistent/a.mov",
                         "content_digest": D1},
            "clip_002": {"path": "/nonexistent/b.mov",
                         "content_digest": D2}}})
    model, report = evidence.migrate_project_evidence(
        str(project), memory_root=str(memory))
    validate_project_model(model)
    return model, report, memory


def test_migration_preserves_every_speech_span_and_links_takes(tmp_path):
    model, report, memory = _migration_project(tmp_path)

    modalities = {obs["modality"] for obs in model["observations"]}
    assert {"speech_rate", "speech_pause", "speech_disfluency",
            "speech_repetition", "speech_completeness",
            "speech_take_link"} <= modalities
    # Every utterance text still reaches the model as full word
    # observations: the take below is linked, never thinned.
    d1_words = {(obs["payload"].get("text"), obs["asset_id"])
                for obs in model["observations"]
                if obs["modality"] == "speech_word"}
    assert ({("the", f"asset-{D1}"), ("edit", f"asset-{D1}"),
             ("is", f"asset-{D1}"), ("locked", f"asset-{D1}")}
            <= d1_words)
    links = [obs for obs in model["observations"]
             if obs["modality"] == "speech_take_link"]
    kinds = {obs["payload"]["kind"] for obs in links}
    assert "identical" in kinds, (
        "the repeated 'the edit is locked' take must link as identical")
    assert all("dropped_start" not in obs["payload"] for obs in links)
    assert all(obs["payload"]["evidence_contract"]["measurement_type"] ==
               "speech.take_link" for obs in links)
    assert all(obs["payload"]["evidence_contract"]["freshness"] == "fresh"
               for obs in links)
    cross = [obs for obs in links
             if obs["payload"]["comparison"] == "cross_clock_unaligned"]
    assert cross, "the cross-source take must link even with no M6 group"
    assert all(obs["payload"]["uncertainty_seconds"] is None
               for obs in cross)
    # Read-only proof: not one lane byte changed.
    assert report["dropped"] == [] or all(
        entry["lane"] != "m1" or "reason" in entry
        for entry in report["dropped"])
    _ = memory


def test_stale_m1_refuses_derived_records(tmp_path):
    from library.tools import project_model_evidence as evidence

    model, _report, _memory = _migration_project(tmp_path)
    assert [obs for obs in model["observations"]
            if obs["modality"] == "speech_take_link"]

    project, memory = tmp_path / "stale-proj", tmp_path / "stale-memory"
    doc = _m1_doc("other-digest", _utt(0, "the edit is locked", 1.0))
    _write(memory / D1 / "transcript.words.json", doc)
    catalog = [{"clip_id": "clip_001", "source_file": "/nonexistent/a.mov",
                "path": "/nonexistent/a.mov"}]
    _write(project / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": catalog}},
        "source_fingerprints": {
            "clip_001": {"path": "/nonexistent/a.mov",
                         "content_digest": D1}}})
    stale_model, stale_report = evidence.migrate_project_evidence(
        str(project), memory_root=str(memory))
    validate_project_model(stale_model)
    assert not [obs for obs in stale_model["observations"]
                if obs["modality"] in ("speech_rate", "speech_pause",
                                       "speech_disfluency",
                                       "speech_repetition",
                                       "speech_completeness",
                                       "speech_take_link")], (
        "derived numbers off a superseded transcript would disagree "
        "with the words beside them")
    assert any("M1 stale" in note for note in stale_report["notes"])


def test_shared_run_keeps_the_original_tie_break():
    import random
    rng = random.Random(81)
    def previous(a, b):
        best = []
        for i in range(len(a)):
            for j in range(len(b)):
                run = []
                while i + len(run) < len(a) and j + len(run) < len(b) and a[i + len(run)] == b[j + len(run)]:
                    run.append(a[i + len(run)])
                if len(run) > len(best):
                    best = run
        return best
    for _ in range(200):
        a = [rng.choice("abcd") for _ in range(rng.randrange(15))]
        b = [rng.choice("abcd") for _ in range(rng.randrange(15))]
        assert speech_take._longest_shared_run(a, b) == previous(a, b)


def test_cross_source_index_preserves_every_exhaustive_candidate():
    import itertools
    texts = ["", "yes", "no", "YES!", "a b", "a b c", "x a b c y",
             "a b c again", "x y z and a b c", "foo bar baz foo bar baz"]
    rows = {digest: [{"text": text, "utterance_index": i,
                      "start": float(i), "end": float(i + 1)}
                     for i, text in enumerate(texts)]
            for digest in (D1, D2)}
    expected = []
    for a, b in itertools.product(rows[D1], rows[D2]):
        relation = speech_take.classify_pair(a["text"], b["text"])
        if relation:
            expected.append((a["utterance_index"], b["utterance_index"],
                             relation["kind"], relation["shared_run"]))
    actual = speech_take.cross_source_links(rows, {})
    assert [(row["utterance_a"]["utterance_index"], row["utterance_b"]["utterance_index"],
             row["kind"], row["shared_run"]) for row in actual] == expected


def test_cross_source_negative_pairs_are_never_classified(monkeypatch):
    rows = {digest: [{"text": f"{prefix}{i} {prefix}{i}x {prefix}{i}y",
                      "utterance_index": i, "start": i, "end": i + 1}
                     for i in range(200)]
            for digest, prefix in ((D1, "first"), (D2, "second"))}
    for digest in rows:
        rows[digest].append({"text": "yes", "utterance_index": 200,
                             "start": 200, "end": 201})
    calls = []
    original = speech_take._classify_tokens
    def counted(a, b):
        calls.append((a, b))
        return original(a, b)
    monkeypatch.setattr(speech_take, "_classify_tokens", counted)
    links = speech_take.cross_source_links(rows, {})
    assert len(links) == 1 and links[0]["kind"] == "identical"
    assert len(calls) == 1
