"""PLAN-003: grounding is a required planning stage.

Each test here names a defect the stage is supposed to close:

- a target the timeline carries was reported as unresolved, and the run's
  own claim was read as a fact;
- two recordings of ONE clip were counted as two candidates, so a target
  that IS unique looked ambiguous;
- an ambiguous target was resolved by picking the first candidate, and
  the re-query never happened;
- a destructive change was allowed to act on a target nobody established;
- a measurement nobody took was read as a refusal.
"""

from __future__ import annotations

from library.tools import target_grounding


def _clip(uid="clip-1", name="a.mov", track_type="video", track_index=1,
          record_in=0, record_out=48, source_file="/media/a.mxf",
          source_in=100, source_out=148) -> dict:
    return {
        "unique_id": uid,
        "name": name,
        "track_type": track_type,
        "track_index": track_index,
        "record_in": record_in,
        "record_out": record_out,
        "source_file": source_file,
        "source_in_frame": source_in,
        "source_out_frame": source_out,
        "media_type": "Video + Audio",
    }


def _model_with_event(event_class="hand_near_mouth", asset_id="asset-a",
                      count=2) -> dict:
    """A Project Model with one class of event in one source."""
    events = []
    for index in range(count):
        events.append({
            "id": f"event-{index + 1}",
            "kind": event_class,
            "participant_ids": ["person-1"],
            "interval": {"clock_id": "clock-a", "start": index * 10,
                         "end": index * 10 + 4},
            "evidence_ids": [f"obs-{index + 1}"],
        })
    return {
        "clocks": [{"id": "clock-a", "ticks_per_second": 1000,
                    "domain": {"asset_id": asset_id}}],
        "events": events,
    }


def test_a_target_the_timeline_carries_is_verified_with_its_interval():
    # Defect: the run's claim was reported as if it were a fact, and the
    # source interval the edit would act on was never stored.
    items = target_grounding.items_of([_clip()])

    row = target_grounding.ground_role("name_bar", {"unique_id": "clip-1"},
                                       items=items)

    assert row["state"] == target_grounding.VERIFIED
    assert row["identity"]["unique_id"] == "clip-1"
    assert row["identity"]["row"] == "V1"
    assert row["interval"]["start_frame"] == 100
    assert row["interval"]["end_frame"] == 148
    assert row["interval"]["file"].endswith("a.mxf")


def test_two_recordings_of_one_clip_are_one_candidate():
    # Defect: a rebuilt timeline records the same clip under a new id,
    # and the stage counted that as two candidates - so a target that IS
    # unique looked ambiguous and its destructive change was refused.
    items = target_grounding.items_of([
        _clip(uid="clip-old"),
        _clip(uid="clip-new"),
    ])

    row = target_grounding.ground_role("name_bar", {"name": "a.mov"},
                                       items=items)

    assert row["state"] == target_grounding.VERIFIED
    assert row["candidate_count"] == 1
    assert row["identity"]["recorded_under"] == ["clip-old", "clip-new"]


def test_a_span_that_covers_two_clips_is_observed_and_re_queried():
    # Defect: an ambiguous target was resolved by picking the first
    # candidate, and the second surface was never asked.
    items = target_grounding.items_of([
        _clip(uid="clip-1", name="a.mov", record_in=0, record_out=75),
        _clip(uid="clip-2", name="b.mov", track_index=2, record_in=75,
              record_out=129, source_file="/media/b.mxf", source_in=200,
              source_out=254),
    ])

    row = target_grounding.ground_role("opening", {"record_spans": [(0, 129)]},
                                       items=items)

    assert row["state"] == target_grounding.OBSERVED
    assert row["candidate_count"] == 2
    surfaces = [attempt["surface"] for attempt in row["attempts"]]
    assert surfaces == ["timeline", "evidence"]


def test_the_re_query_can_narrow_an_ambiguous_target():
    # Defect: the re-query existed but its answer was thrown away.
    items = target_grounding.items_of([
        _clip(uid="clip-1", name="a.mov", record_in=0, record_out=75),
        _clip(uid="clip-2", name="b.mov", track_index=2, record_in=75,
              record_out=129),
    ])
    model = _model_with_event()

    row = target_grounding.ground_role(
        "opening",
        {"record_spans": [(0, 129)],
         "event": {"event_class": "hand_near_mouth", "nth": 1}},
        items=items, model=model)

    assert row["state"] == target_grounding.VERIFIED
    assert row["identity"]["kind"] == "occurrence"
    assert row["identity"]["occurrence"]["index"] == 1
    assert row["evidence"] == ["obs-1"]
    assert row["interval"]["start"] == 0


def test_an_occurrence_measured_in_two_sources_is_not_picked():
    # Defect: the second time something happened is a different moment
    # in each source, and picking one of them would be a guess.
    model = _model_with_event(asset_id="asset-a")
    model["events"].append({
        "id": "event-3",
        "kind": "hand_near_mouth",
        "participant_ids": ["person-1"],
        "interval": {"clock_id": "clock-b", "start": 0, "end": 4},
        "evidence_ids": ["obs-3"],
    })
    model["clocks"].append({"id": "clock-b", "ticks_per_second": 1000,
                            "domain": {"asset_id": "asset-b"}})

    row = target_grounding.ground_role(
        "second_time", {"event": {"event_class": "hand_near_mouth",
                                   "nth": 1}}, model=model)

    assert row["state"] == target_grounding.CLAIMED
    assert "source" in row["reason"]
    assert row["identity"] is None


def test_a_claim_the_timeline_does_not_carry_is_claimed_not_unresolved():
    # Defect: a run's own claim and a target nobody asserted were read
    # as the same answer.
    items = target_grounding.items_of([_clip()])

    row = target_grounding.ground_role("name_bar",
                                       {"unique_id": "clip-9"}, items=items)

    assert row["state"] == target_grounding.CLAIMED
    assert row["identity"] is None


def test_a_role_nobody_asserted_is_unresolved():
    row = target_grounding.ground_role("music_bed", None, items=[])

    assert row["state"] == target_grounding.UNRESOLVED


def test_a_destructive_change_is_refused_without_a_verified_target():
    # Defect: a destructive change acted on a target nobody established.
    items = target_grounding.items_of([
        _clip(uid="clip-1", name="a.mov", record_in=0, record_out=75),
        _clip(uid="clip-2", name="b.mov", track_index=2, record_in=75,
              record_out=129),
    ])
    operations = [{"op": "clip.trim", "target_role": "opening"}]

    receipt = target_grounding.ground_plan(operations, items=items,
                                           claims={"opening": {
                                               "record_spans": [(0, 129)]}})

    assert receipt["refusals"]
    refusal = receipt["refusals"][0]
    assert refusal["op"] == "clip.trim"
    assert refusal["target_role"] == "opening"
    assert refusal["state"] == target_grounding.OBSERVED


def test_a_destructive_change_with_a_verified_target_is_not_refused():
    items = target_grounding.items_of([_clip()])
    operations = [{"op": "clip.trim", "target_role": "opening"}]

    receipt = target_grounding.ground_plan(
        operations, items=items, claims={"opening": {"unique_id": "clip-1"}})

    assert receipt["refusals"] == []
    assert receipt["counts"][target_grounding.VERIFIED] == 1


def test_a_change_that_destroys_nothing_is_not_refused():
    # Defect: every ungrounded target was refused, so the stage forbade
    # work it had no business forbidding.
    items = target_grounding.items_of([_clip()])
    operations = [{"op": "overlay.position", "target_role": "name_bar"}]

    receipt = target_grounding.ground_plan(
        operations, items=items, claims={"name_bar": {"unique_id": "clip-9"}})

    assert receipt["refusals"] == []


def test_the_destructive_vocabulary_is_the_engines_own_safety_class():
    from library.tools import edit_algebra

    assert target_grounding.is_destructive("clip.trim")
    assert target_grounding.is_destructive("clip.remove")
    assert target_grounding.is_destructive("reel.opening")
    assert target_grounding.is_destructive("caption_fix")
    assert not target_grounding.is_destructive("overlay.position")
    assert not target_grounding.is_destructive("audio.gain")
    assert edit_algebra.OP_SAFETY_CLASS["clip.trim"]


def test_a_machine_that_read_nothing_refuses_nothing():
    # Defect: a measurement nobody took was read as a refusal.
    row = target_grounding.ground_role("opening",
                                       {"unique_id": "clip-1"}, items=[])

    assert row["state"] == target_grounding.CLAIMED
    assert row["attempts"] == [{"surface": "timeline", "candidates": 0}]


def test_the_receipt_carries_the_four_states_and_nothing_else():
    assert target_grounding.STATES == ("claimed", "observed", "verified",
                                       "unresolved")
