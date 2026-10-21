"""M0-1: a request for a named object answers with detections and a map.

The defect these name: `object_segmentation` (1.06) is PLAN-triggered, so
a request-driven grounding question found no objects at all - the query
had no way to ask for the object lane, and the answer carried nothing
about what had been looked at. The object lane now answers a REQUEST:
the query names the object, the producer the task requires runs, and the
answer carries the detections AND the coverage map.

Each test names one defect; the "before" column is what the query did
before the request-driven path existed.
"""

import pytest

from library.tools import evidence_query as eq
from library.tools.analysis import object_grounding as og


@pytest.fixture(autouse=True)
def _coco_vocabulary(monkeypatch):
    """The COCO class list, without loading a model to read it.

    The production path reads the real list from the detector's own
    config; these tests are about the SELECTION, and loading
    transformers here would cost the whole file its runtime.
    """
    from library.tools.analysis import open_vocabulary_detect as ovd

    monkeypatch.setattr(ovd, "fixed_vocabulary_labels",
                        lambda detector: ["cell phone", "cup", "person"])


def _model(rows):
    """A Project Model carrying the given observations."""
    return {
        "id": "model-1",
        "observations": list(rows),
        "entities": [],
        "provenance": [],
        "object_classes": [],
        "tracks": [],
    }


def _detection_result(*, phrase="handheld device", start=10.0, frames=(3, 13)):
    """What a detection pass returns: two detections and its coverage."""
    detections = []
    for index, frame in enumerate(frames):
        detections.append({
            "phrase": phrase,
            "label": phrase,
            "box": [0.4, 0.3, 0.6, 0.5],
            "score": 0.4 + 0.1 * index,
            "detector": "owlv2",
            "frame_index": frame,
            "time_seconds": frame / 30.0,
            "frame_width": 1920,
            "frame_height": 1080,
        })
    return {
        "detections": detections,
        "coverage": {
            "scope": {"start_s": start, "end_s": start + 2.0},
            "sample_rate_hz": 1.0,
            "frames_inspected": len(frames),
            "frame_indices": list(frames),
            "inspected": [{"start_s": start, "end_s": start + 2.0}],
            "unexamined": [],
            "source_fps": 30.0,
            "source_frame_count": 1440,
        },
        "producer": {"id": "owlv2_named", "detector": "owlv2",
                     "tier": "standard", "measured_fidelity": 132 / 148,
                     "note": "measured"},
        "producer_version": og.PRODUCER_VERSION,
        "cost_seconds": 7.2,
        "notes": [],
    }


def _rows(phrase="handheld device", **kwargs):
    """The Project Model rows one request produces."""
    model = _model([])
    return og.build_observations(
        model=model, digest="digest-1", asset_id="asset-1",
        clock_id="clock-1", phrases=[phrase],
        result=_detection_result(phrase=phrase, **kwargs))


# ── the query names the work it needs ───────────────────────────────────

def test_a_query_for_a_named_object_is_compilable():
    """Before: the query layer knew only 'measurement' and 'event_span',
    so a request for an object could not even be written down."""
    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "handheld device",
         "requirement": "grounded"}])
    assert plan["predicates"][0]["phrase"] == "handheld device"
    assert plan["object_queries"] == [
        {"phrase": "handheld device", "requirement": "grounded"}]


def test_the_object_request_names_the_lane_that_can_answer_it():
    """Before: the object measurement named the TRACKER's lane, and no
    request-driven producer served it - so the query could not ask for
    the work it needed."""
    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "handheld device"}])
    assert plan["required_lanes"] == [og.OBJECT_LANE]
    assert og.OBJECT_LANE != "segmentation", (
        "the request-driven rows must not share 1.06's provenance "
        "record: a provenance record names one producer")


def test_an_unknown_task_requirement_is_refused():
    """A request that does not say what it needs gets neither a silent
    upgrade nor a silent downgrade."""
    with pytest.raises(eq.EvidenceQueryError):
        eq.compile_plan([{"predicate": "object_presence",
                          "phrase": "cup", "requirement": "as-precise-as-"
                          "possible"}])


def test_a_query_with_no_object_lane_yet_says_so():
    """Before: a grounding question about an object found nothing and
    could not say which lane owed it an answer."""
    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "handheld device"}],
        scope={"clock_id": "clock-1", "asset_id": "asset-1"})
    answer = eq.evidence_for(_model([]), plan)
    assert answer["status"] == eq.NOT_YET_ANALYSED
    assert answer["required_missing_lanes"] == [og.OBJECT_LANE]
    assert "object_detection" in " ".join(answer["reasons"])


# ── the answer carries the detections and the coverage map ──────────────

def test_the_answer_carries_the_detections_it_found():
    observations, _provenance = _rows()
    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "handheld device"}],
        scope={"clock_id": "clock-1", "asset_id": "asset-1"})
    answer = eq.evidence_for(_model(observations), plan)
    assert answer["status"] == eq.PRESENT
    assert len(answer["detections"]) == 2
    assert answer["detections"][0]["box"] == [0.4, 0.3, 0.6, 0.5]
    assert answer["detections"][0]["detector"] == "owlv2"
    assert answer["detections"][0]["frame_index"] == 3
    assert answer["detections"][0]["evidence_id"] == observations[0]["id"]


def test_the_answer_carries_the_coverage_map():
    """Before: nothing in the answer said what had been looked at, so a
    reader could not tell a narrow answer from a broad one."""
    observations, _provenance = _rows()
    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "handheld device"}],
        scope={"clock_id": "clock-1", "asset_id": "asset-1",
               "start_s": 10.0, "end_s": 12.0})
    answer = eq.evidence_for(_model(observations), plan)
    coverage = answer["coverage_map"]
    assert coverage["frames_inspected"] == 2
    assert coverage["inspected"] == [{"start_s": 10.0, "end_s": 12.0}]
    assert coverage["unexamined"] == []
    assert coverage["sample_rate_hz"] == 1.0


def test_the_coverage_map_names_what_nobody_looked_at():
    """A request that covered less than it asked for says so."""
    observations, _provenance = _rows()
    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "handheld device"}],
        scope={"clock_id": "clock-1", "asset_id": "asset-1",
               "start_s": 0.0, "end_s": 30.0})
    answer = eq.evidence_for(_model(observations), plan)
    unexamined = answer["coverage_map"]["unexamined"]
    assert unexamined == [{"start_s": 0.0, "end_s": 10.0},
                          {"start_s": 12.0, "end_s": 30.0}]


def test_the_coverage_table_by_category_is_measurable_not_empty():
    """Before: the D-01 coverage table was empty for a request - the
    request-driven path had no table to report at all."""
    observations, _provenance = _rows(phrase="coffee cup")
    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "coffee cup"}],
        scope={"clock_id": "clock-1", "asset_id": "asset-1"})
    answer = eq.evidence_for(_model(observations), plan)
    table = answer["coverage_by_category"]
    cup = table["by_category"]["cup"]
    assert cup["measured"] is True
    assert cup["instances"] == 2
    assert cup["frames_inspected"] == 2
    assert cup["coverage"] == 1.0
    # A category nobody asked about is UNMEASURED, never a zero.
    assert table["by_category"]["phone"]["measured"] is False
    assert table["by_category"]["phone"]["coverage"] is None


# ── the answer stays honest ─────────────────────────────────────────────

def test_a_pass_that_found_nothing_is_not_an_absence():
    """A sampled pass cannot support an exhaustive claim: the answer is
    INCONCLUSIVE with its coverage, never 'the object is not there'."""
    model = _model([])
    result = _detection_result()
    result["detections"] = []
    observations, _provenance = og.build_observations(
        model=model, digest="digest-1", asset_id="asset-1",
        clock_id="clock-1", phrases=["handheld device"], result=result)
    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "handheld device"}],
        scope={"clock_id": "clock-1", "asset_id": "asset-1",
               "start_s": 10.0, "end_s": 12.0})
    answer = eq.evidence_for(model | {"observations": observations}, plan)
    assert answer["status"] == eq.INCONCLUSIVE
    assert answer["detections"] == []
    assert answer["coverage_map"]["frames_inspected"] == 2
    assert "coverage" in " ".join(answer["reasons"])


def test_the_coverage_row_is_not_a_detection():
    """The row that records what a pass looked at must not be counted as
    an object: otherwise a pass that found nothing reports one."""
    model = _model([])
    result = _detection_result()
    result["detections"] = []
    observations, _provenance = og.build_observations(
        model=model, digest="digest-1", asset_id="asset-1",
        clock_id="clock-1", phrases=["handheld device"], result=result)
    coverage_rows = [row for row in observations
                     if og.is_coverage_row(row)]
    assert len(coverage_rows) == 1
    assert og.value_of(coverage_rows[0])["looked"]["frames"] == 2
    assert og.phrase_of(coverage_rows[0]) == "handheld device"


def test_a_query_for_one_object_does_not_answer_for_another():
    observations, _provenance = _rows(phrase="coffee cup")
    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "handheld device"}],
        scope={"clock_id": "clock-1", "asset_id": "asset-1"})
    answer = eq.evidence_for(_model(observations), plan)
    assert answer["status"] != eq.PRESENT
    assert answer["detections"] == []


# ── the producer the task requirement selects ───────────────────────────

def test_the_task_requirement_selects_the_producer():
    """C-08: a cheap producer for a simple question, the measured best
    one when the question needs the box."""
    assert og.select_producer("presence", ["cell phone"]).detector == \
        "rf_detr_nano"
    assert og.select_producer("grounded", ["cell phone"]).detector == "owlv2"
    assert og.select_producer("grounded", ["cell phone"]).tier == \
        og.TIER_STANDARD


def test_a_phrase_the_cheap_detector_cannot_name_buys_the_measured_one():
    """The COCO detector answers only the classes it knows. Asking it
    about a handheld device would answer nothing, and an empty answer
    reads as 'the object is not there'."""
    assert og.can_answer(og.PRODUCERS["rf_detr_nano"], "cell phone") is True
    assert og.can_answer(og.PRODUCERS["rf_detr_nano"],
                          "handheld device") is False
    assert og.select_producer("presence", ["handheld device"]).detector == \
        "owlv2"


def test_a_producer_that_names_no_phrase_is_never_selected():
    """The Apple Vision baseline proposes regions; it cannot say which
    one is the object the request names."""
    assert og.can_answer(og.PRODUCERS["vision_nominations"], "cup") is False
    assert og.select_producer("presence", ["cup"]).detector != \
        "vision_foreground"
    assert og.select_producer("grounded", ["cup"]).detector != \
        "vision_foreground"


def test_an_unknown_requirement_names_the_ones_that_exist():
    with pytest.raises(og.ObjectGroundingError):
        og.select_producer("whenever")


def test_a_request_with_no_object_is_refused():
    """A detector asked about nothing answers nothing, and that empty
    answer would read as a recall of zero."""
    with pytest.raises(og.ObjectGroundingError):
        og.phrases_of([])
    with pytest.raises(og.ObjectGroundingError):
        og.phrases_of(["  "])


# ── the frames the pass inspects ────────────────────────────────────────

def test_the_sampled_frames_are_source_frame_numbers():
    """Before: the M2 sample carried seconds and no frame index, so a
    detection could not name the frame it was measured on."""
    indices = og.sample_frame_indices(
        fps=30.0, frame_count=1440, start_s=10.0, end_s=12.0,
        sample_fps=1.0)
    assert indices == [300, 330]
    assert og.coverage_intervals(indices, fps=30.0, sample_fps=1.0) == [
        {"start_s": 10.0, "end_s": 11.0},
        {"start_s": 11.0, "end_s": 12.0},
    ]


def test_the_unexamined_part_of_a_scope_is_stated():
    assert og.unexamined({"start_s": 0.0, "end_s": 10.0},
                         [{"start_s": 2.0, "end_s": 4.0}]) == [
        {"start_s": 0.0, "end_s": 2.0},
        {"start_s": 4.0, "end_s": 10.0},
    ]


# ── the request can ask for the work ────────────────────────────────────

def test_a_request_can_schedule_the_object_lane_it_needs():
    """The point of the lane: a query that needs the object evidence can
    ask for it, and the acquisition planner schedules that lane."""
    from library.tools import query_directed_acquisition as qda

    plan = eq.compile_plan([
        {"predicate": "object_presence", "phrase": "handheld device"}],
        scope={"clock_id": "clock-1", "asset_id": "asset-1"})
    answer = eq.evidence_for(_model([]), plan)
    scheduled = qda.plan_query_acquisition(
        answer, plan, source_digest="digest-1", duration_seconds=48.0,
        available_lanes=(og.OBJECT_LANE,),
        producer_budgets={og.OBJECT_LANE: 55.0},
        window_seconds_by_lane={og.OBJECT_LANE: 10.0},
        producer_cost_per_window_seconds={og.OBJECT_LANE: 55.0},
        max_cost_seconds=600.0)
    assert scheduled["action"] == "refine"
    assert scheduled["required_lanes"] == [og.OBJECT_LANE]
    assert scheduled["steps"][0]["lane"] == og.OBJECT_LANE


def test_the_rows_name_the_producer_that_wrote_them():
    """A row must never claim a producer that did not write it."""
    observations, provenance = _rows()
    assert observations[0]["provenance_id"] == f"prov-digest-1-{og.OBJECT_LANE}"
    assert provenance[0]["producer"] == og.PRODUCER
    native = observations[0]["payload"]["evidence_contract"]
    assert native["producer"] == og.PRODUCER
    assert native["producer_version"] == og.PRODUCER_VERSION
