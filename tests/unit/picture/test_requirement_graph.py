"""L5 planner: seven families, conjunctions, order, negatives."""

import pytest

from library.tools import requirement_graph as rg


def families_of(graph):
    return {req["id"]: req["family"] for req in graph["requirements"]}


def test_seven_families_are_distinguishable():
    graph = rg.parse_query(
        "speaker discussing revenue while a chart is visible")
    assert set(families_of(graph).values()) == {"speech", "image"}
    compiled = rg.compile_graph(graph)
    assert set(compiled["answerable"]) == {"req-1", "req-2"}
    assert compiled["needs_verification"] == []
    assert compiled["unanswerable"] == []


def test_speech_topic_compiles_to_word_plan():
    graph = rg.parse_query("where does the speaker discuss revenue")
    compiled = rg.compile_graph(graph)
    row = next(item for item in compiled["compiled"]
               if item["requirement_id"] == "req-1")
    assert row["kind"] == "plan"
    predicate = row["plan"]["predicates"][0]
    assert predicate["measurement_type"] == "speech.word"
    assert predicate["equals"] == "revenue"


def test_multimodal_conjunction_is_one_graph():
    graph = rg.parse_query(
        "speaker discussing revenue while a chart is visible")
    assert graph["conjunction"] is True
    assert len(graph["requirements"]) == 2


def test_temporal_ordering_records_before_after():
    graph = rg.parse_query("person opens the box and then shows the product")
    assert graph["order"] != []
    target = next(req for req in graph["requirements"]
                  if req["cue"] == "product")
    assert target["order_after"] != []
    first = target["order_after"][0]
    assert next(req for req in graph["requirements"]
                if req["id"] == first)["family"] == "action"


def test_negative_condition_compiles_positive_plan_marked_negated():
    graph = rg.parse_query("footage with no visible people")
    assert len(graph["requirements"]) == 1
    req = graph["requirements"][0]
    assert req["negated"] is True
    assert req["family"] == "image"
    compiled = rg.compile_graph(graph)
    row = compiled["compiled"][0]
    assert row["kind"] == "plan"
    assert row["negated"] is True
    assert row["plan"]["predicates"][0]["event"] == "on_screen"


def test_camera_constraint_is_typed_but_unanswerable_without_a_floor():
    graph = rg.parse_query("static wide shots with low motion")
    assert {req["family"] for req in graph["requirements"]} == {"technical"}
    compiled = rg.compile_graph(graph)
    assert compiled["answerable"] == []
    assert len(compiled["unanswerable"]) == 3
    row = next(item for item in compiled["compiled"]
               if item["requirement_id"] == "req-1")
    assert "no number" in row["reason"] or "floor" in row["needed"]


def test_spatial_relationship_routes_to_verification():
    graph = rg.parse_query("the person on the left holding the product")
    kinds = {(req["family"], req["kind"]) for req in graph["requirements"]}
    assert ("relationship", "spatial") in kinds
    compiled = rg.compile_graph(graph)
    assert set(compiled["needs_verification"]) == {"req-2", "req-3"}
    verify = next(item for item in compiled["compiled"]
                  if item["requirement_id"] == "req-2")
    assert verify["lane"] == "m10"


def test_audio_visual_relationship_joins_two_plans():
    graph = rg.parse_query("visible speaker with synchronized dialogue")
    compiled = rg.compile_graph(graph)
    row = next(item for item in compiled["compiled"]
               if item["requirement_id"] == "req-1")
    assert row["kind"] == "plan"
    events = {predicate["event"] for predicate in row["plan"]["predicates"]}
    assert events == {"on_screen", "speaking"}


def test_distant_speaker_and_visible_is_conjunction_not_sync():
    graph = rg.parse_query(
        "speaker discussing revenue while a chart is visible")
    assert all(req["kind"] != "audio_visual"
               for req in graph["requirements"])


def test_ocr_value_is_extracted_from_the_anchor():
    graph = rg.parse_query(
        "where does the text read revenue growth appear on screen")
    assert len(graph["requirements"]) == 1
    req = graph["requirements"][0]
    assert req["family"] == "ocr"
    assert req["value"] == "revenue growth"
    compiled = rg.compile_graph(graph)
    row = compiled["compiled"][0]
    assert row["plan"]["predicates"][0]["equals"] == "revenue growth"
    assert row["required_lanes"] == ["ocr_extraction"]


def test_action_candidate_maps_to_known_kind():
    graph = rg.parse_query("show me the moment she picks up the phone")
    kinds = {(req["family"], req.get("value")) for req in graph["requirements"]}
    assert ("action", "pick_up_put_down") in kinds
    compiled = rg.compile_graph(graph)
    row = next(item for item in compiled["compiled"]
               if item["kind"] == "verify")
    assert row["hint"]["candidate_kind"] == "pick_up_put_down"


def test_open_action_verb_is_unanswerable_not_guessed():
    graph = rg.parse_query("person demonstrates the product")
    compiled = rg.compile_graph(graph)
    assert compiled["unanswerable"] != []
    row = next(item for item in compiled["compiled"]
               if item["kind"] == "unanswerable")
    assert row["required_lane"] == "m7"
    assert "demonstrates" in row["reason"]


def test_refusals_name_what_is_missing():
    with pytest.raises(rg.RequirementGraphError):
        rg.parse_query("   ")
    with pytest.raises(rg.RequirementGraphError):
        rg.parse_query("make it feel cinematic and warm")
    with pytest.raises(rg.RequirementGraphError):
        rg.parse_query("revenue or applause")


def test_no_duplicate_requirements_from_overlapping_cues():
    graph = rg.parse_query(
        "Find the part where the speaker discusses the product")
    cues = [req["cue"] for req in graph["requirements"]]
    assert len(cues) == len(set(cues))
    assert len(graph["requirements"]) == 2


def test_modalities_of_names_one_family_per_requirement():
    graph = rg.parse_query("find laughter during the applause")
    assert rg.modalities_of(graph) == {"req-1": ["sound"],
                                       "req-2": ["sound"]}


def test_describe_is_compact_and_cites_ids():
    graph = rg.parse_query("footage with no visible people")
    lines = rg.describe(graph)
    assert lines == ["req-1 [image] NOT people"]


def test_open_vocabulary_fallback_names_unknown_objects():
    graph = rg.parse_query(
        "footage where person and backpack and laptop visible")
    values = {req.get("value") for req in graph["requirements"]}
    assert {"person", "backpack", "laptop"} <= values
    compiled = rg.compile_graph(graph)
    assert len(compiled["answerable"]) == 3


def test_speech_topic_is_not_a_visible_object():
    graph = rg.parse_query("speaker discussing revenue while a chart is visible")
    assert len(graph["requirements"]) == 2
    assert all(req["value"] != "revenue" or req["family"] == "speech"
               for req in graph["requirements"])


def test_framing_modifiers_never_become_object_queries():
    graph = rg.parse_query(
        "where does the text read revenue growth appear on screen")
    assert len(graph["requirements"]) == 1
    assert graph["requirements"][0]["family"] == "ocr"


def test_possessive_action_phrases_map_to_candidate_kinds():
    graph = rg.parse_query("the moment she covers her mouth")
    assert len(graph["requirements"]) == 1
    req = graph["requirements"][0]
    assert (req["family"], req["kind"], req["value"]) == (
        "action", "candidate", "hand_to_face")
    compiled = rg.compile_graph(graph)
    assert compiled["needs_verification"] == ["req-1"]


def test_pickup_sequence_is_one_candidate():
    graph = rg.parse_query("she picks it up and puts it down")
    assert len(graph["requirements"]) == 1
    assert graph["requirements"][0]["value"] == "pick_up_put_down"


def test_after_orders_the_anchor_before_the_event():
    graph = rg.parse_query(
        "Find the close-up where the speaker picks up the phone "
        "after saying goodbye")
    assert graph["order"] != []
    first, second = graph["order"][0]
    by_id = {req["id"]: req for req in graph["requirements"]}
    assert by_id[first]["family"] == "speech"
    assert by_id[second]["value"] == "pick_up_put_down", (
        "after attaches the anchor to the event, not to the "
        "earliest mention before the marker")


def test_order_markers_never_become_object_queries():
    graph = rg.parse_query(
        "Find the close-up where the speaker picks up the phone "
        "after saying goodbye")
    assert all(req.get("value") not in ("after", "before")
               for req in graph["requirements"])


def test_scaffolding_words_never_become_queries():
    graph = rg.parse_query(
        "the boundary of the second time face_001 covers their mouth")
    assert all(req.get("value") not in ("boundary", "ever", "doing",
                                        "one")
               for req in graph["requirements"])


def test_plural_objects_fold_to_the_singular_label():
    graph = rg.parse_query("a shot of a busy city street full of cars")
    assert any(req.get("value") == "car"
               for req in graph["requirements"]
               if req["family"] == "image")
