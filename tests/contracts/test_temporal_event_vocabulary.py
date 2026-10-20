"""The temporal-event vocabularies agree with each other and with the
evidence contract.

The defect each test names:

* an ACTION CLASS that the held-out temporal-event benchmark does not
  know, so a ground-truth interval cannot be scored against these
  events without a translation that was never written down;
* an ACTION CLASS with no lexicon entry, so nothing can ever classify
  as it;
* a lexicon entry naming a class outside the vocabulary, so a trigger
  points at a class that does not exist;
* a MODEL EVENT KIND mapped to an event type or action class that does
  not exist, so a rename silently produces an untyped event;
* a REQUIRED EVIDENCE name that no measurement type carries, so an
  event type is satisfied by a measurement that was never registered.
"""

from library.tools import edit_critical_events, evidence_contract, temporal_events
from library.tools.analysis import temporal_event_benchmark


def test_the_benchmarks_frozen_classes_are_all_action_classes():
    """The held-out benchmark's event classes are the ground truth a
    temporal event is scored against.  They must be spellable here, or
    the scoring needs a translation nobody wrote."""
    frozen = set(temporal_event_benchmark.EVENT_CLASSES)
    known = set(temporal_events.ACTION_CLASSES)
    assert frozen <= known, (
        f"action classes missing from the typed vocabulary: "
        f"{sorted(frozen - known)}")


def test_every_action_class_has_a_lexicon_entry():
    """A class nothing can classify as is a class the machinery cannot
    produce."""
    missing = sorted(set(temporal_events.ACTION_CLASSES)
                     - set(temporal_events.ACTION_LEXICON))
    assert missing == []


def test_no_lexicon_entry_names_a_class_outside_the_vocabulary():
    extra = sorted(set(temporal_events.ACTION_LEXICON)
                   - set(temporal_events.ACTION_CLASSES))
    assert extra == []


def test_every_lexicon_trigger_is_a_real_phrase():
    empty = sorted(class_name for class_name, triggers
                   in temporal_events.ACTION_LEXICON.items()
                   if not triggers or any(not trigger.strip()
                                          for trigger in triggers))
    assert empty == []


def test_every_model_event_kind_maps_to_a_real_type_and_action():
    for kind, (event_type, action) in temporal_events.MODEL_EVENT_KINDS.items():
        assert event_type in edit_critical_events.event_types(), kind
        assert action is None or action in temporal_events.ACTION_CLASSES, kind


def test_every_required_evidence_name_is_a_registered_measurement_type():
    """A requirement naming a measurement type the contract does not
    register can never be satisfied, and the gap must be named rather
    than papered over."""
    registry = evidence_contract._REGISTRY
    for event_type, names in temporal_events.REQUIRED_EVIDENCE_BY_TYPE.items():
        for name in names:
            assert name in registry, f"{event_type}: {name}"


def test_every_edit_critical_evidence_name_is_accounted_for():
    """Each name ``edit_critical_events`` declares is either carried by
    a registered measurement type (possibly under another name) or
    NAMED as carrying none.  A name in neither column is a silent
    mismatch."""
    registry = evidence_contract._REGISTRY
    substitutions = temporal_events.EVIDENCE_NAME_SUBSTITUTIONS
    unsatisfiable = temporal_events.EVIDENCE_NAMES_WITHOUT_A_MEASUREMENT_TYPE
    for event_type in edit_critical_events.event_types():
        declared = edit_critical_events.fidelity_requirement(
            event_type)["required_evidence"]
        accounted = set(substitutions.get(event_type, {}))
        accounted |= set(unsatisfiable.get(event_type, ()))
        for name in declared:
            if name in registry:
                continue
            assert name in accounted, (
                f"{event_type}: the evidence name {name!r} is neither "
                f"registered nor declared as carrying no measurement type")


def test_every_edit_critical_type_has_a_required_evidence_row():
    assert sorted(temporal_events.REQUIRED_EVIDENCE_BY_TYPE) == sorted(
        edit_critical_events.event_types())
