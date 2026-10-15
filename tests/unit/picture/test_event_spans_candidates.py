"""Candidate action/gesture/event predicates for M7, and the VLM
verifier that judges them.

The defect each test names: a geometric rule that proposes no span (so
the VLM never sees the moment), or a rule that proposes a span for a
motion that is not the predicate. A candidate is never an answer - the
verifier disposes - so a rule that cannot clear the bar by itself must
still propose. No test here asserts that a registry or schema contains
a named entry; each drives a generator on fixture M3 and asserts the
spans it proposes.
"""
import pytest
from library.tools import event_spans, span_verification
from library.tools.ren_refusal import RenRefusal


def _m3(frames):
    return {"instrument": {"frame_pixels": [384, 216]}, "frames": frames}


def _face(centre_y, height=0.2):
    top = centre_y - height / 2.0
    return {"box": [0.45, top, 0.55, top + height]}


def _hand(y):
    return {"chirality": "left", "confidence": 1.0,
            "joints": {"wrist": [0.5, y, 0.9], "tip": [0.5, y - 0.05, 0.9]}}


def _frames_with_hand(hand_ys, face_cy=0.3):
    """One face track and one hand at `hand_ys[i]` in frame i."""
    frames, assignment = [], []
    for i, y in enumerate(hand_ys):
        frames.append({"t": i * 0.5, "faces": [_face(face_cy)],
                       "hands": [_hand(y)]})
        assignment.append(["face_001"])
    return frames, assignment


# ── hand_raise ─────────────────────────────────────────────────────


def test_hand_raise_proposes_a_span_when_a_hand_is_lifted_above_the_face():
    """A hand lifted from the lap into the air proposes no candidate unless
    the rule looks for a rise ending above the face's centre - and the
    verifier must be shown the transition, not only the raised hand.

    The span is tightened to the motion's measured onset and last moving
    frame: the step happens between t=2.5 and t=3.0, so the span shows
    exactly the before/after pair, not the full detector window."""
    hand_ys = [0.7] * 6 + [0.08] * 6  # low, then above the face's centre
    frames, assignment = _frames_with_hand(hand_ys)
    spans = event_spans.hand_raise_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    shown = span_verification.select_frames(spans[0])
    assert [f["t"] for f in shown] == [2.5, 3.0]
    assert spans[0]["tier"] == 0  # an abrupt 12x-threshold rise is strong


def test_hand_raise_proposes_nothing_when_the_hand_stays_low():
    """A hand that rises a little but stays below the face's centre is a
    gesture, not a raise - proposing it would flood the VLM."""
    hand_ys = [0.7] * 6 + [0.5] * 6  # rises but stays below the centre
    frames, assignment = _frames_with_hand(hand_ys)
    assert event_spans.hand_raise_candidates(_m3(frames), assignment) == []


# ── nod ────────────────────────────────────────────────────────────


def test_nod_proposes_a_span_for_a_vertical_oscillation():
    """A head that bobs down-then-up proposes no nod candidate unless the
    rule looks for a vertical peak-to-peak displacement that reverses."""
    frames, assignment = [], []
    for i in range(12):
        cy = 0.3 + (0.06 if i % 2 else -0.06)  # oscillate vertically
        frames.append({"t": i * 0.5, "faces": [_face(cy)], "hands": []})
        assignment.append(["face_001"])
    assert len(event_spans.nod_candidates(_m3(frames), assignment)) == 1


def test_nod_proposes_nothing_for_a_horizontal_head_move():
    """A head that translates sideways carries no vertical oscillation - a
    nod rule keyed on vertical displacement must not propose it."""
    frames, assignment = [], []
    for i in range(12):
        top = 0.2
        face = {"box": [0.45 + 0.01 * i, top, 0.55 + 0.01 * i, top + 0.2]}
        frames.append({"t": i * 0.5, "faces": [face], "hands": []})
        assignment.append(["face_001"])
    assert event_spans.nod_candidates(_m3(frames), assignment) == []


# ── object_pickup ──────────────────────────────────────────────────


def test_object_pickup_proposes_a_span_when_a_hand_rises_from_below():
    """A hand lifted from below the face's bottom to above it proposes no
    pickup candidate unless the rule requires that arc."""
    hand_ys = [0.8] * 8 + [0.25] * 6  # from below the bottom (0.4) to above
    frames, assignment = _frames_with_hand(hand_ys)
    assert len(event_spans.object_pickup_candidates(_m3(frames),
                                                   assignment)) == 1


def test_object_pickup_proposes_nothing_when_the_hand_stays_low():
    """A hand that rises but never reaches the face's bottom is a gesture,
    not a pickup - proposing it would flood the VLM."""
    hand_ys = [0.8] * 8 + [0.55] * 6  # rises but stays below the bottom
    frames, assignment = _frames_with_hand(hand_ys)
    assert event_spans.object_pickup_candidates(_m3(frames),
                                                assignment) == []


# ── person_enters / person_leaves ──────────────────────────────────


def test_person_enters_proposes_a_span_at_a_track_appearance():
    """A face track that appears mid-clip proposes no enter candidate
    unless the rule looks at the run's first frame - and the span must
    lead with the frame before it, or the transition is invisible."""
    frames, assignment = [], []
    for i in range(10):
        present = i >= 4
        frames.append({"t": i * 0.5,
                       "faces": [_face(0.3)] if present else [], "hands": []})
        assignment.append(["face_001"] if present else [])
    spans = event_spans.person_enters_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert spans[0]["frames"][0]["t"] == 1.5  # the frame before it
    assert spans[0]["frames"][0]["d"] == 0.0


def test_person_enters_at_the_clip_start_has_no_frame_before_it():
    """A track present from the first frame proposes an enter with no
    leading frame - the person was there when the clip opened."""
    frames, assignment = [], []
    for i in range(10):
        frames.append({"t": i * 0.5, "faces": [_face(0.3)], "hands": []})
        assignment.append(["face_001"])
    spans = event_spans.person_enters_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert spans[0]["frames"][0]["t"] == 0.0


def test_person_leaves_proposes_a_span_at_a_track_disappearance():
    """A face track that disappears proposes no leave candidate unless the
    rule looks at the run's last frame - and the span must trail with the
    frame after it."""
    frames, assignment = [], []
    for i in range(10):
        present = i < 6
        frames.append({"t": i * 0.5,
                       "faces": [_face(0.3)] if present else [], "hands": []})
        assignment.append(["face_001"] if present else [])
    spans = event_spans.person_leaves_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert spans[0]["frames"][-1]["t"] == 3.0  # the frame after it
    assert spans[0]["frames"][-1]["d"] == 0.0


def test_person_leaves_at_the_clip_end_has_no_frame_after_it():
    """A track present until the last frame proposes a leave with no
    trailing frame - the clip ends, not the person."""
    frames, assignment = [], []
    for i in range(10):
        frames.append({"t": i * 0.5, "faces": [_face(0.3)], "hands": []})
        assignment.append(["face_001"])
    spans = event_spans.person_leaves_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert spans[0]["frames"][-1]["t"] == 4.5


# ── the query and the verifier ─────────────────────────────────────


def test_a_candidate_predicate_is_refused_with_a_verify_pointer():
    """Asking for a candidate predicate alone reads as "never happens" -
    the refusal must name --verify, the only way to answer it."""
    with pytest.raises(RenRefusal) as refused:
        event_spans.query("/nonexistent", "SpeakerTwo", "hand_raise")
    assert "hand_raise" in refused.value.what
    assert "--verify" in refused.value.fix


def test_the_prompt_judges_a_sequence_so_an_event_description_works():
    """An event description (a person entering) is a transition no single
    frame contains - the prompt must ask about the sequence, not only
    about one frame, or the transition the frames collectively show is
    rejected."""
    prompt = span_verification.PROMPT.format(n=4, statement="enters the frame")
    assert "enters the frame" in prompt
    assert "across the sequence" in prompt


# ── tiering: cheap filters before any VLM call ───────────────────


def test_a_gradual_lift_keeps_a_longer_lead_in_than_an_abrupt_step():
    """Tightening follows the measured onset: an abrupt step clears the
    threshold in one frame (one before-frame), while a gradual lift
    needs several frames to accumulate it (a longer lead-in)."""
    gradual = [0.7 - 0.04 * i for i in range(12)]  # never abrupt
    frames, assignment = _frames_with_hand(gradual)
    [span] = event_spans.hand_raise_candidates(_m3(frames), assignment)
    assert [f["t"] for f in span["frames"]] == [3.5, 4.0, 4.5, 5.0, 5.5]
    assert span["tier"] == 0  # 3.2x the threshold is still strong


def test_a_bare_threshold_crossing_verifies_in_the_marginal_tier():
    """A rise that barely clears the geometric threshold (slow drift,
    jitter) still proposes - the verifier disposes - but in tier 1,
    after every strong motion."""
    hand_ys = [0.34] * 6 + [0.28] * 6  # rise 0.06 over a 0.05 threshold
    frames, assignment = _frames_with_hand(hand_ys)
    [span] = event_spans.hand_raise_candidates(_m3(frames), assignment)
    assert span["tier"] == 1
    assert span["score"] == pytest.approx(1.2)


def test_mid_clip_transitions_outrank_clip_edges():
    """A mid-clip appearance shows the entry; a track present from the
    first frame shows none. The edge verifies in tier 1 so budgeted
    verification spends its VLM calls on visible transitions first."""
    mid, mid_assign = [], []
    for i in range(10):
        present = i >= 4
        mid.append({"t": i * 0.5,
                    "faces": [_face(0.3)] if present else [], "hands": []})
        mid_assign.append(["face_001"] if present else [])
    [enter] = event_spans.person_enters_candidates(_m3(mid), mid_assign)
    assert enter["tier"] == 0

    edge, edge_assign = [], []
    for i in range(10):
        edge.append({"t": i * 0.5, "faces": [_face(0.3)], "hands": []})
        edge_assign.append(["face_001"])
    [edge_enter] = event_spans.person_enters_candidates(
        _m3(edge), edge_assign)
    assert edge_enter["tier"] == 1
    [edge_leave] = event_spans.person_leaves_candidates(
        _m3(edge), edge_assign)
    assert edge_leave["tier"] == 1


def _mouth_frames(hand_ys, t0=0.0):
    """Faces with lips plus a hand at `hand_ys[i]` (None: no hand)."""
    face = {"box": [0.45, 0.2, 0.55, 0.4], "outer_lips": [[0.5, 0.4]]}
    frames, assignment = [], []
    for i, y in enumerate(hand_ys):
        hands = [] if y is None else [
            {"joints": {"wrist": [0.5, y, 0.9]}}]
        frames.append({"t": round(t0 + i * 0.5, 3), "faces": [dict(face)],
                        "hands": hands})
        assignment.append(["face_001"])
    return frames, assignment


def test_detection_gap_fragments_merge_into_one_span():
    """Two hit frames missed mid-hold split the run the gap bridge does
    not reunite; merging reunites them into one span (one VLM call)."""
    hand_ys = [0.42, 0.42, None, None, 0.42, 0.42]
    frames, assignment = _mouth_frames(hand_ys)
    spans = event_spans.hand_at_mouth_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert "merged" in spans[0]["tier_reason"]
    assert spans[0]["tier"] == 0


def test_a_hold_and_an_adjacent_hover_stay_split_by_tier():
    """A firm hold next to a loose-band hover must not stretch the
    strong span: runs form per tier band, so each span covers only its
    own band's frames."""
    hand_ys = [0.42] * 4 + [0.65] * 4  # hold, then loose hover, gapless
    frames, assignment = _mouth_frames(hand_ys)
    spans = event_spans.hand_at_mouth_candidates(_m3(frames), assignment)
    assert [s["tier"] for s in spans] == [0, 1]
    assert spans[0]["end"] <= spans[1]["start"]


def test_an_isolated_band_flicker_joins_its_neighbours():
    """One loose-band frame inside a firm hold is flicker, not an
    episode: it joins the hold's span instead of splitting it."""
    hand_ys = [0.42, 0.42, 0.65, 0.42, 0.42]
    frames, assignment = _mouth_frames(hand_ys)
    spans = event_spans.hand_at_mouth_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert spans[0]["tier"] == 0


def test_a_lone_touch_with_no_near_neighbour_keeps_its_band():
    """Smoothing never flips a touch nobody is near: a lone hit far
    from every other hit keeps its own span and tier."""
    hand_ys = [0.42, None, None, None, None, 0.65, 0.65]
    frames, assignment = _mouth_frames(hand_ys)
    spans = event_spans.hand_at_mouth_candidates(_m3(frames), assignment)
    assert [s["tier"] for s in spans] == [0, 1]


def test_verified_query_verifies_strongest_first_and_budgets_by_tier(
        monkeypatch):
    """Tier 0 spans reach the verifier before tier 1, and max_tier=0
    skips the marginal band entirely, reporting the skip and per-tier
    costs instead of spending the calls."""
    strong = {"face_track": "face_001", "start": 1.0, "end": 2.0,
              "frames": [{"index": 2, "t": 1.0, "d": 0.1, "box": None}],
              "tier": 0, "score": 3.0, "tier_reason": "strong"}
    marginal = {"face_track": "face_001", "start": 5.0, "end": 6.0,
                "frames": [{"index": 10, "t": 5.0, "d": 1.4, "box": None}],
                "tier": 1, "score": -1.4, "tier_reason": "marginal"}
    record = {"content_digest": "d" * 64, "source_file": "cam.MOV",
              "predicates": {"speaking": {"spans": []},
                             "on_screen": {"spans": []}},
              "candidates": {"hand_near_mouth":
                              {"spans": [marginal, strong]}}}
    person = {"person_id": "p1",
              "face_tracks": [{"content_digest": "d" * 64,
                               "track_id": "face_001"}]}
    monkeypatch.setattr(event_spans, "resolve_person",
                        lambda *args, **kwargs: (person, {"by": "test"}))
    monkeypatch.setattr(event_spans, "read_m7",
                        lambda *args, **kwargs: record)
    monkeypatch.setattr(event_spans, "timeline_items", lambda *args: [])
    monkeypatch.setattr(event_spans, "_digest_of_files", lambda *args: {})
    seen = []

    def fake_verify(digest, spans, statement, **kwargs):
        seen.append([s["start"] for s in spans])
        return [{"answer": "no", "reason": "t", "frames": [], "frame_t": None,
                 "model": "m", "cached": False, "seconds": 0.1}
                for _ in spans]

    monkeypatch.setattr(span_verification, "verify_spans", fake_verify)

    full = event_spans.verified_query(
        "/proj", "p1", "hand_near_mouth", "covers his mouth", max_tier=1)
    assert seen == [[1.0, 5.0]]  # tier 0 first, both verified
    assert full["candidates"] == 2
    assert full["cost"]["by_tier"] == {
        "0": {"candidates": 1, "calls": 1},
        "1": {"candidates": 1, "calls": 1}}

    seen.clear()
    budgeted = event_spans.verified_query(
        "/proj", "p1", "hand_near_mouth", "covers his mouth")
    assert seen == [[1.0]]  # the marginal span never reaches the VLM
    assert budgeted["verified_candidates"] == 1
    assert budgeted["skipped_by_tier"] == 1
    assert budgeted["cost"]["vlm_calls"] == 1

    record["candidates"] = {"hand_raise": {"spans": [marginal, strong]}}
    seen.clear()
    other = event_spans.verified_query(
        "/proj", "p1", "hand_raise", "raises a hand")
    assert seen == [[1.0, 5.0]]
    assert other["verified_candidates"] == 2


def test_legacy_hand_candidates_get_tiered_from_recorded_distance(monkeypatch):
    """Older M7 files have no tier field, but retain frame distances.
    The query derives the same strong/marginal split without writing M7."""
    strong = {"face_track": "face_001", "start": 1.0, "end": 2.0,
              "frames": [{"index": 2, "t": 1.0, "d": 1.2, "box": None}]}
    marginal = {"face_track": "face_001", "start": 5.0, "end": 6.0,
                "frames": [{"index": 10, "t": 5.0, "d": 1.4, "box": None}]}
    record = {"content_digest": "d" * 64, "source_file": "cam.MOV",
              "candidates": {"hand_near_mouth":
                              {"spans": [strong, marginal]}}}
    person = {"person_id": "p1", "face_tracks": [
        {"content_digest": "d" * 64, "track_id": "face_001"}]}
    monkeypatch.setattr(event_spans, "resolve_person",
                        lambda *args, **kwargs: (person, {"by": "test"}))
    monkeypatch.setattr(event_spans, "read_m7",
                        lambda *args, **kwargs: record)
    spans = event_spans._candidate_spans(person, "hand_near_mouth")
    assert [span["tier"] for span in spans] == [0, 1]
    assert "tier" not in strong
    assert "tier" not in marginal


class TestEventLanes:
    """`build_project_events` runs one lane per distinct digest on the
    bounded FREE lanes. Dedup already happens serially in
    catalog_sources; each unit is pure compute plus one atomic slot
    write, so the lanes take no lock. A wiring that silently
    serializes must fail the overlap assertion here, not in
    production."""

    def test_rows_match_serial_contract_and_lanes_overlap(
            self, monkeypatch):
        import threading
        import time

        from library.tools import event_spans
        from library.tools.concurrency_routing import lane_width

        units = [(f"clip_{i:03d}", "d" * 63 + str(i),
                  f"/cam/C{i:03d}.MOV") for i in (1, 2, 3)]
        monkeypatch.setattr(event_spans, "catalog_sources",
                            lambda project_folder: list(units))

        spans = []
        spans_lock = threading.Lock()

        def fake_build(digest, path, root=None):
            if digest.endswith("2"):
                raise RuntimeError("no M3 persons")
            start = time.monotonic()
            time.sleep(0.2)
            with spans_lock:
                spans.append((start, time.monotonic()))
            return {"content_digest": digest, "faces_assigned": 1}

        monkeypatch.setattr(event_spans, "build_source_events",
                            fake_build)

        report = event_spans.build_project_events("/proj")

        rows = report["clips"]
        assert [r["clip_id"] for r in rows] == [
            "clip_001", "clip_002", "clip_003"]
        assert report["failed"] == ["clip_002"]
        assert rows[1]["failed"] == "RuntimeError: no M3 persons"
        assert rows[0]["faces_assigned"] == 1

        width = lane_width()
        peak, depth, events = 0, 0, sorted(
            [(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
        for _, delta in events:
            depth += delta
            peak = max(peak, depth)
        assert peak >= min(2, width), (
            f"lanes never overlapped (peak {peak}, width {width})")
