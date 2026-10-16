"""Unit tests for the SEM-013 VLM routing policy.

The policy selects model ids; it never loads a model and never changes
the production default. Every test pins that the incumbent is returned
unless escalation is explicitly enabled for a qualifying simple task.
"""

from library.tools import vlm_routing_policy as policy


def test_unknown_task_class_resolves_strong():
    assert policy.select_tier("something_unheard_of") == "strong"


def test_simple_tasks_resolve_lightweight():
    for task in ("object_presence", "scene_classification",
                 "static_object_description"):
        assert policy.select_tier(task) == "lightweight"


def test_complex_tasks_resolve_strong():
    for task in ("action_localization", "person_object_interaction",
                 "camera_motion", "audiovisual_grounding",
                 "multi_window_reasoning"):
        assert policy.select_tier(task) == "strong"


def test_escalation_triggers_are_observable_signals():
    assert policy.should_escalate({"parse": {"json": False}})[0] is True
    assert policy.should_escalate(
        {"parse": {"json": True, "schema": False}})[0] is True
    assert policy.should_escalate(
        {"parse": {"json": True, "schema": True},
         "objects": [], "actions": []})[0] is True
    assert policy.should_escalate(
        {"parse": {"json": True, "schema": True},
         "objects": [{"label": "x"}], "actions": [],
         "scene_class": "unknown"})[0] is True


def test_well_formed_non_empty_answer_does_not_escalate():
    escalate, _ = policy.should_escalate(
        {"parse": {"json": True, "schema": True},
         "objects": [{"label": "car seat"}], "actions": [],
         "scene_class": "vehicle"})
    assert escalate is False


def test_incumbent_is_default_on_every_path():
    assert policy.resolve_model_id("strong") == policy.INCUMBENT_MODEL_ID
    assert policy.resolve_model_id("lightweight") == policy.INCUMBENT_MODEL_ID
    assert policy.resolve_model_id("lightweight",
                                   escalation_enabled=True) == policy.FALLBACK_MODEL_ID
    assert policy.resolve_model_id("strong",
                                   escalation_enabled=True) == policy.INCUMBENT_MODEL_ID
