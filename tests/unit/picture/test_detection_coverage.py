"""D-01's measurement harness, and the defect its first run found.

Each test names the defect it prevents. Nothing here loads a model: the
scoring is pure, so the numbers in
`docs/benchmarks/2026-10-21-d01-detection-coverage.md` can be recomputed
from synthetic boxes.
"""

import pytest

from library.tools.analysis import detection_coverage as coverage
from library.tools.analysis import open_vocabulary_detect as ovd


def _manifest(frames):
    return {"schema": coverage.MANIFEST_SCHEMA, "frames": frames}


def _frame(frame_id, objects, *, unusual=False):
    return {"frame_id": frame_id, "image": f"{frame_id}.jpg",
            "unusual_footage": unusual,
            "source": {"path": "/tmp/x.mov", "timestamp_s": 1.0,
                       "width": 100, "height": 100},
            "objects": objects}


def _obj(klass, query, box):
    return {"class": klass, "query": query, "box": list(box)}


# ── the defect D-01's first run found ───────────────────────────────────

def test_a_fixed_vocabulary_detection_of_the_queried_object_is_not_dropped_for_a_naming_reason():
    """RF-DETR says "potted plant" where the query said "plant pot".

    The adapter dropped that TRUE detection because neither name
    contains the other, and the fixed-vocabulary path measured 0/4
    plant-pot coverage on real footage while the box was right there
    (D-01, 2026-10-21). A detector that found the object must not be
    told it did not.
    """
    assert ovd._match_phrase("potted plant", ["plant pot"]) == "plant pot"
    assert ovd._match_phrase("plant pot", ["plant pot"]) == "plant pot"


def test_the_naming_rule_still_refuses_a_one_word_label_for_a_longer_phrase():
    """The fix must not turn "car" into a "car door handle", nor a
    "traffic light" into a "light switch": a shared word is not a
    shared object."""
    assert ovd._match_phrase("car", ["car door handle"]) == "car"
    assert ovd._match_phrase("traffic light", ["light switch"]) == (
        "traffic light")
    assert ovd._match_phrase("wine glass", ["cup"]) == "wine glass"


def test_a_longer_label_still_answers_a_shorter_query():
    """The mapping must keep working where it was right: COCO's "cell
    phone" IS a "phone"."""
    assert ovd._match_phrase("cell phone", ["phone"]) == "phone"


# ── the denominators ────────────────────────────────────────────────────

def test_a_class_with_no_labelled_instance_is_unmeasured_and_not_a_zero():
    """D-01 names phones, and this corpus holds no phone. A report that
    printed 0.000 for phone would read as a measurement of phone
    coverage; there was nothing to measure."""
    manifest = _manifest([
        _frame("f1", [_obj("person", "person", [0.1, 0.1, 0.5, 0.5])]),
    ])
    report = coverage.coverage_report(
        manifest, {"f1": []}, detector="none")
    phone = report["by_class"]["phone"]
    assert phone["instances"] == 0
    assert phone["recall"] is None
    assert phone["precision"] is None
    assert phone["measured"] is False
    markdown = coverage.render_markdown({"detectors": {"none": report}})
    assert "| phone |" in markdown
    assert "UNMEASURED" in markdown


def test_every_recalled_instance_carries_its_own_denominator():
    """A coverage number without its denominator is a rumour."""
    manifest = _manifest([
        _frame("f1", [_obj("person", "person", [0.1, 0.1, 0.5, 0.5]),
                      _obj("cup", "cup", [0.6, 0.6, 0.7, 0.7])]),
    ])
    report = coverage.coverage_report(
        manifest,
        {"f1": [{"phrase": "person", "box": [0.1, 0.1, 0.5, 0.5]}]},
        detector="none")
    assert report["by_class"]["person"]["instances"] == 1
    assert report["by_class"]["person"]["recalled"] == 1
    assert report["by_class"]["cup"]["instances"] == 1
    assert report["by_class"]["cup"]["recalled"] == 0
    assert report["by_class"]["cup"]["recall"] == 0.0


# ── the matching rule ───────────────────────────────────────────────────

def test_one_detection_cannot_recall_two_instances():
    """Two labelled hands under one predicted box is one hit, not two.
    A duplicate answer is a false positive, and it stays one."""
    objects = [_obj("hand", "hand", [0.1, 0.1, 0.5, 0.5]),
               _obj("hand", "hand", [0.2, 0.2, 0.6, 0.6])]
    result = coverage.score_frame(
        objects, [{"phrase": "hand", "box": [0.1, 0.1, 0.6, 0.6]}])
    assert len(result["recalled_objects"]) == 1
    assert len(result["missed_objects"]) == 1
    assert result["false_positives"] == []


def test_a_detection_only_answers_the_phrase_it_was_asked_for():
    """A "person" box is not a "cup" box, however well it overlaps."""
    objects = [_obj("cup", "cup", [0.1, 0.1, 0.5, 0.5])]
    result = coverage.score_frame(
        objects, [{"phrase": "person", "box": [0.1, 0.1, 0.5, 0.5]}])
    assert result["recalled_objects"] == []
    assert len(result["false_positives"]) == 1


def test_a_class_agnostic_proposal_recalls_any_instance_but_is_no_true_positive():
    """Apple Vision's foreground proposal names no class. It can cover a
    person, but it is no class's true positive and no class's false
    positive either: it has no class to be right or wrong about."""
    manifest = _manifest([
        _frame("f1", [_obj("person", "person", [0.1, 0.1, 0.5, 0.5])]),
    ])
    report = coverage.coverage_report(
        manifest,
        {"f1": [{"phrase": "", "box": [0.1, 0.1, 0.5, 0.5]}]},
        detector="vision_foreground")
    assert report["by_class"]["person"]["recalled"] == 1
    assert report["by_class"]["person"]["true_positives"] == 0
    assert report["by_class"]["person"]["detections"] == 0
    assert report["class_agnostic_proposals"]["proposals"] == 1
    assert report["class_agnostic_proposals"]["matched"] == 1


def test_a_false_positive_belongs_to_the_class_its_own_phrase_names():
    """A "glasses" box that matched nothing is a false positive of
    glasses, not of whichever class happens to sit first in the frame."""
    manifest = _manifest([
        _frame("f1", [_obj("person", "person", [0.1, 0.1, 0.5, 0.5]),
                      _obj("small_object", "glasses", [0.6, 0.6, 0.7, 0.7])]),
    ])
    report = coverage.coverage_report(
        manifest,
        {"f1": [{"phrase": "glasses", "box": [0.2, 0.2, 0.3, 0.3]}]},
        detector="none")
    assert report["by_class"]["small_object"]["false_positives"] == 1
    assert report["by_class"]["person"]["false_positives"] == 0


# ── the measured strata ─────────────────────────────────────────────────

def test_small_and_partially_visible_are_measured_from_the_geometry():
    """D-01 asks about small objects and partially visible objects. Both
    are read off the box, so a labeler never asserts them."""
    tiny = [0.0, 0.0, 0.05, 0.05]
    edge = [0.0, 0.2, 0.4, 0.6]
    assert coverage.is_small(tiny)
    assert not coverage.is_small([0.0, 0.0, 0.6, 0.6])
    assert coverage.is_partial(edge)
    assert not coverage.is_partial([0.3, 0.3, 0.7, 0.7])


def test_the_strata_carry_their_own_denominators():
    manifest = _manifest([
        _frame("f1", [_obj("hand", "hand", [0.0, 0.0, 0.05, 0.05])],
               unusual=True),
        _frame("f2", [_obj("person", "person", [0.3, 0.3, 0.7, 0.7])]),
    ])
    report = coverage.coverage_report(manifest, {"f1": [], "f2": []},
                                      detector="none")
    assert report["strata"]["small"]["instances"] == 1
    assert report["strata"]["partial"]["instances"] == 1
    assert report["strata"]["unusual_footage"]["instances"] == 1
    assert report["strata"]["studio_footage"]["instances"] == 1


# ── the manifest is checked, not assumed ────────────────────────────────

def test_a_manifest_without_frames_is_refused():
    """An empty label packet would print a table of zeros that reads as
    a measurement."""
    with pytest.raises(ValueError):
        coverage.validate_manifest(_manifest([]))


def test_a_manifest_with_a_box_outside_the_frame_is_refused():
    with pytest.raises(ValueError):
        coverage.validate_manifest(_manifest([
            _frame("f1", [_obj("person", "person", [0.1, 0.1, 1.4, 0.5])]),
        ]))


def test_a_manifest_with_an_unknown_class_is_refused():
    with pytest.raises(ValueError):
        coverage.validate_manifest(_manifest([
            _frame("f1", [_obj("unicorn", "unicorn", [0.1, 0.1, 0.5, 0.5])]),
        ]))


def test_a_frame_without_its_source_cannot_be_re_extracted_and_is_refused():
    """The frames live outside the repository, so the manifest must name
    the clip and the second to rebuild them."""
    frame = _frame("f1", [])
    frame["source"] = {"path": "/tmp/x.mov"}
    with pytest.raises(ValueError):
        coverage.validate_manifest(_manifest([frame]))
