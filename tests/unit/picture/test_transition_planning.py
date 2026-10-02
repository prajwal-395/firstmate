from library.tools.transition_selector import select_transition
from library.tools.transition_vocabulary import NATIVE_TYPES, PLANNABLE_TYPES
import pytest
import sys
from pathlib import Path
import library.steps.step_4_02_plan_transitions.post_bridge as transition_bridge
import json
import os
import subprocess
from library.tools.transition_carriers import (
    block_reaches_v1,
    cut_carriers,
)
import copy


def test_same_source_clip_is_a_jump_cut():
    """A cut inside one take has no second angle to move to."""
    clip = {"clip_id": "clip_001"}
    res = select_transition(clip, dict(clip), {}, {})
    assert res["type"] == "jump_cut"
    assert res["duration_ms"] == 0


def test_a_scene_change_the_plan_did_not_decorate_is_a_hard_cut():
    """No request means nothing is drawn.

    This used to answer `defocus` once every twenty seconds, and
    `fade_to_black` or `flash` off the incoming block's type - taste
    chosen by a constant for a cut nobody asked to decorate. See
    `WITHDRAWN_SCENE_CHANGE_DEFAULTS`; this scenario pins the behavior.
    """
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002", "timeline_start": 5.0},
        {"transition_duration_ms": 600}, {},
    )
    assert res["type"] == "hard_cut"
    assert res["duration_ms"] == 0
    # Neither a block type nor a music behaviour invents one.
    for to_clip in (
        {"clip_id": "clip_002", "timeline_start": 25.0,
         "music_behavior": "step_up"},
        {"clip_id": "clip_002", "timeline_start": 25.0,
         "block_type": "breather"},
        {"clip_id": "clip_002", "timeline_start": 25.0,
         "block_type": "transition_slot"},
    ):
        res = select_transition({"clip_id": "clip_001"}, to_clip, {}, {})
        assert res["type"] == "hard_cut", to_clip
    # The allow-list is a permission, not an instruction: the final line
    # used to be `settle(preferred_types[0])`, so a brand whose list began
    # with a drawn type got it on every undecorated cut.
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_types": ["defocus", "hard_cut"]}, {},
    )
    assert res["type"] == "hard_cut"


@pytest.mark.parametrize("requested, resolved", [
    ("zoom_blur", "zoom_blur"),
    # `cross_dissolve` is drawn by Resolve itself, not downgraded.
    ("cross_dissolve", "cross_dissolve"),
    ("Dip_To_Black", "fade_to_black"),     # an alias, canonicalised
])
def test_a_granted_request_is_honoured(requested, resolved):
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type=requested,
    )
    assert res["type"] == resolved
    assert res["requested_type"] == requested
    assert res["downgrade_reason"] == ""


def test_an_undrawable_request_becomes_a_hard_cut_with_a_reason():
    """Never quietly swapped for a different creative transition."""
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="wipe",
    )
    assert res["type"] == "hard_cut"
    assert res["requested_type"] == "wipe"
    assert "wipe" in res["downgrade_reason"]


def test_a_measured_refusal_ships_the_stated_fallback_only():
    """The nearest granted native transition ships only when the plan
    states it in `fallback_type` - never substituted by the engine."""
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="whip_pan",
        fallback_type="cross_dissolve",
    )
    assert res["type"] == "cross_dissolve"
    assert "fallback" in res["downgrade_reason"]


def test_brand_types_no_route_can_draw_are_rejected(capsys):
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_types": ["cut", "dissolve", "wipe"]},
        {"target_energy": "high"},
    )
    # "cut" and the native "dissolve" survive the filter; "wipe" is
    # rejected loudly. No request means nothing is drawn either way -
    # and none is invented.
    assert res["type"] == "hard_cut"
    err = capsys.readouterr().err
    assert "wipe" in err and "dissolve" not in err


def test_a_brand_range_is_a_bound_and_not_a_length():
    """A brand file writes {min, max}, and a RANGE names no length.

    Answering `max` here is what overruled the plan: project 001's two
    drawn transitions were planned "quick" and "medium" and both were
    held for 500 ms. A range now yields bounds and no duration, so the
    plan's own `duration_feel` decides inside them.
    """
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_duration_ms": {"min": 200, "max": 500}}, {},
        requested_type="defocus",
    )
    assert res["duration_ms"] is None
    assert res["duration_bounds_ms"] == (200, 500)
    # One number is the template author saying how long, exactly.
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {"transition_duration_ms": 600}, {}, requested_type="defocus",
    )
    assert res["duration_ms"] == 600
    assert res["duration_bounds_ms"] == (600, 600)
    # No brand duration invents no length (`_resolve_duration_ms` once
    # ended `return default` with default=500).
    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"}, {}, {},
        requested_type="defocus",
    )
    assert res["duration_ms"] is None
    assert res["duration_bounds_ms"] == (None, None)


def test_every_outcome_is_a_drawable_type():
    cases = [
        ({}, {}, ""),
        ({"transition_types": ["macro", "light_leak"]}, {}, "j_cut"),
        ({"transition_types": []}, {"target_energy": "calm"}, "nonsense_type"),
        ({}, {}, "cross_dissolve"),
    ]
    for brand, creative, requested in cases:
        res = select_transition(
            {"clip_id": "a"}, {"clip_id": "b"}, brand, creative,
            requested_type=requested,
        )
        assert res["type"] in PLANNABLE_TYPES + NATIVE_TYPES, res


# --------------------------------------------------------------------------
# From test_plan_transitions_precision.py
#
# Rung 7 precision vocabulary on step 4.02 (K1: TR3.1, TR3.2, C3.1):
# stated frame/second holds, the `"end"` slot, and J/L offsets in frames.
# History: `docs/evidence/transition_precision.md`.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_02_plan_transitions.post_bridge import (
    TransitionSpecRefused,
    resolve_transitions,
)

FPS = 30.0


def _spine():
    # Three speech blocks, back to back, each with one word filling it
    # so the word-end resolution sits exactly on the boundary.
    blocks = []
    cursor = 0.0
    for pos, word in ((1, "one"), (2, "two"), (3, "three")):
        dur = 2.0
        blocks.append({
            "position": pos,
            "block_type": "speech",
            "clip_id": "clip_001",
            "source_start": cursor,
            "source_end": cursor + dur,
            "timeline_start": cursor,
            "timeline_end": cursor + dur,
            "word_timestamps": [
                {"word": word, "source_start": cursor,
                 "source_end": cursor + dur},
            ],
        })
        cursor += dur
    return {"structure": blocks, "frame_rate": FPS}


def _base(**over):
    params = {
        "music_selection": {},
        "music_analysis": {},
        "frame_rate": FPS,
    }
    params.update(over)
    return params


def test_the_hold_resolves_from_what_the_plan_stated_and_says_so():
    """TR3.1's 12-frame dissolve and 1s end fade: the number survives,
    sourced; a feel word alone still resolves."""
    rows = [
        ({"cut_point_position": 2, "type": "cross_dissolve",
          "duration_frames": 12}, None, 12, "stated_frames"),
        ({"cut_point_position": "end", "type": "fade_to_black",
          "duration_seconds": 1.0}, 1.0, 30, "stated_seconds"),
        ({"cut_point_position": 2, "type": "cross_dissolve",
          "duration_seconds": 0.4, "duration_frames": 12},
         0.4, 12, "stated_frames_and_seconds"),
        ({"cut_point_position": 2, "type": "cross_dissolve",
          "duration_feel": "medium"}, None, 10, "feel"),
    ]
    for plan, seconds, frames, source in rows:
        (entry,) = resolve_transitions(
            [dict(plan, rationale="x")], _spine(), **_base())
        assert entry["transition_type"] == plan["type"]
        assert entry["duration_frames"] == frames, plan
        assert entry["duration_source"] == source, plan
        if seconds is not None:
            assert entry["duration_seconds"] == pytest.approx(seconds)
        if "duration_feel" in plan:
            assert entry["duration_feel"] == "medium"


def test_an_ambiguous_hold_refuses():
    """Seconds and frames that disagree, or a number beside a different
    feel, are two holds."""
    for extra, match in [
            ({"duration_seconds": 0.4, "duration_frames": 13}, "disagree"),
            ({"duration_seconds": 0.4, "duration_feel": "quick"},
             "duration_feel")]:
        with pytest.raises(TransitionSpecRefused, match=match):
            resolve_transitions(
                [dict({"cut_point_position": 2, "type": "cross_dissolve",
                       "rationale": "two holds"}, **extra)],
                _spine(), **_base())


def test_stated_frame_anchor_survives_as_an_exact_cut_coordinate():
    """A timecode anchor remains an integer frame through transition planning."""
    (entry,) = resolve_transitions(
        [{"cut_point_position": 2, "type": "cross_dissolve",
          "anchor": {"frame": 45}, "duration_frames": 12,
          "rationale": "at the stated timecode"}],
        _spine(), **_base())
    assert entry["cut_point_frame"] == 45
    assert entry["cut_point_timeline"] == pytest.approx(1.5)


def test_end_fade_resolves_tail_only_at_the_timeline_end():
    """TR3.1's dip: fade_to_black at "end" sits on the last frame."""
    (entry,) = resolve_transitions(
        [{"cut_point_position": "end", "type": "fade_to_black",
          "duration_frames": 30, "rationale": "dip out"}],
        _spine(), **_base())
    assert entry["at_end"] is True
    assert entry["transition_type"] == "fade_to_black"
    assert entry["duration_frames"] == 30
    assert entry["duration_source"] == "stated_frames"
    assert entry["cut_point_timeline"] == pytest.approx(6.0)
    assert entry["placement_method"] == "timeline-end"


def test_runner_attempt_does_not_treat_end_fade_as_a_cut_carrier():
    """The carrier retry check must not dereference an absent end block."""
    (entry,) = resolve_transitions(
        [{"cut_point_position": "end", "type": "fade_to_black",
          "duration_seconds": 1.0, "rationale": "one second out"}],
        _spine(), **_base(attempt=1, v2_spans=[]))
    assert entry["at_end"] is True
    assert entry["transition_type"] == "fade_to_black"
    assert entry["duration_seconds"] == pytest.approx(1.0)


def test_j_lead_in_frames_reaches_the_audio_offset():
    """TR3.3's shape through the plan: 20 frames, on the boundary."""
    spine = _spine()
    # A pause before the join: block 1's word ends a second early, so
    # the 20-frame lead trims silence, never speech.
    spine["structure"][0]["word_timestamps"] = [
        {"word": "one", "source_start": 0.0, "source_end": 1.0},
    ]
    (entry,) = resolve_transitions(
        [{"cut_point_position": 2, "type": "j_cut",
          "lead_frames": 20, "rationale": "early ear"}],
        spine, **_base())
    assert entry["transition_type"] == "hard_cut"
    offset = entry["audio_offset"]
    assert offset["kind"] == "j_cut"
    assert offset["picture_cut_frame"] - offset["audio_cut_frame"] == 20


def test_jl_cut_uses_the_mesh_shared_boundary_frame(monkeypatch):
    """Rounding the displayed seconds must not shift the shared join frame."""
    from library.tools import jl_cut

    readback = {}

    def capture_boundary(**kwargs):
        readback["boundary_frame"] = kwargs["boundary_frame"]
        frame = kwargs["boundary_frame"]
        return {
            "picture_cut_timeline": frame / FPS,
            "picture_cut_frame": frame,
            "audio_cut_timeline": (frame - 1) / FPS,
            "audio_cut_frame": frame - 1,
            "audio_cut_exact": True,
            "lead_seconds": 1 / FPS,
            "method": "stated 1 frame",
        }

    monkeypatch.setattr(jl_cut, "resolve_audio_cut", capture_boundary)
    incoming = {
        "position": 2, "block_type": "speech", "clip_id": "clip_001",
        "timeline_start": 25.576, "timeline_start_frame": 767,
    }
    outgoing = {
        "position": 1, "block_type": "speech", "clip_id": "clip_001",
        "timeline_start": 0.0, "timeline_end": 25.576,
    }

    result = transition_bridge._attach_jl_offset(
        {"type": "j_cut", "lead_frames": 1}, None, incoming, outgoing,
        [], FPS, {}, {}, index=0)

    assert readback["boundary_frame"] == 767
    assert result["audio_offset"]["picture_cut_frame"] == 767


def test_jl_cut_refuses_an_explicit_but_malformed_shared_frame(monkeypatch):
    """Finding 13: None must not be mistaken for an absent frame boundary."""
    from library.tools import jl_cut
    from library.tools.jl_cut import JLCutRefused

    monkeypatch.setattr(
        jl_cut, "resolve_audio_cut",
        lambda **kwargs: pytest.fail("malformed boundary reached frame resolver"),
    )
    incoming = {
        "position": 2, "block_type": "speech", "clip_id": "clip_001",
        "timeline_start": 25.576, "timeline_start_frame": None,
    }
    outgoing = {
        "position": 1, "block_type": "speech", "clip_id": "clip_001",
        "timeline_start": 0.0, "timeline_end": 25.576,
    }

    with pytest.raises(JLCutRefused, match="unreadable shared boundary"):
        transition_bridge._attach_jl_offset(
            {"type": "j_cut", "lead_frames": 1}, None, incoming, outgoing,
            [], FPS, {}, {}, index=0)


# --------------------------------------------------------------------------
# From test_transition_carriers.py
#
# The transitions step is told which cuts can carry a drawn transition.
#
# The rule is derived from where the picture is PLACED, so the test drives
# the real placement - ``compile_manifest``'s own V1 membership predicate -
# against ``cut_carriers``' answer, and drives the real bridge to check the
# fact reaches the table.  It never asserts the shape of a string the model
# does not read.

STEP = REPO / "library" / "steps" / "step_4_02_plan_transitions"
BRIDGE = STEP / "bridge.py"

# `narrative_verdict` and `verdict_note` are step 3.03's per-cut judgement,
# folded in beside the buildability columns by the same bridge
# (library/tools/cut_verdicts.py). This file owns the buildability half;
# tests/scenarios/test_cut_decisions_reach_a_reader.py owns the verdict half.
CUTS_HEADERS = [
    "cut_point_position", "cut_time", "type",
    "can_carry_drawn_transition", "carry_basis",
    "narrative_verdict", "verdict_note",
    "beat_near_cut", "outgoing_motion", "incoming_motion",
    "outgoing_footage", "incoming_footage",
]


def speech(position, start, end, block_type="speech"):
    return {
        "position": position,
        "block_type": block_type,
        "timeline_start": start,
        "timeline_end": end,
        "clip_id": "clip_001",
        "source_start": start,
        "source_end": end,
    }


def slot(position, start, end):
    return {
        "position": position,
        "block_type": "transition_slot",
        "timeline_start": start,
        "timeline_end": end,
    }


def card(position, start, end, slot_name="intro"):
    return {
        "position": position,
        "block_type": f"{slot_name}_card",
        "timeline_start": start,
        "timeline_end": end,
        "content": {"bookend": {
            "slot": slot_name,
            "asset_path": "/nowhere/card.mov",
            "duration_seconds": end - start,
        }},
    }


# The 001 spine of the run of record, block types and boundaries only.
SPINE_001 = [
    speech("hook", 0.0, 2.398, block_type="hook"),
    slot("1", 2.398, 6.06),
    speech("2", 6.06, 8.742),
    speech("3", 8.742, 11.444),
    slot("4", 11.444, 13.7),
    speech("5", 13.7, 17.24),
    slot("6", 17.24, 18.622),
    speech("7", 18.622, 34.615),
    slot("8", 34.615, 38.801),
    speech("9", 38.801, 41.183),
    speech("10", 41.183, 42.921),
    speech("11", 42.921, 44.684),
    speech("12", 44.684, 46.147),
    slot("13", 46.147, 48.065),
    speech("14", 48.065, 56.731),
    speech("15", 56.731, 59.437),
]


def test_a_cut_out_of_a_transition_slot_cannot_carry_one():
    """The rule that failed 001's run, on 001's own spine."""
    rows = {r["position"]: r for r in cut_carriers(SPINE_001)}
    # Fifteen cuts plus the rung-7 end-of-piece row: 001 ends on
    # speech, so the end carries a tail-only fade out of V1.
    assert len(rows) == 16

    unbuildable = sorted(p for p, r in rows.items() if not r["can_carry"])
    assert unbuildable == ["14", "2", "5", "7", "9"]
    assert rows["end"]["can_carry"] is True
    assert rows["end"]["cut_time"] == 59.437
    assert rows["end"]["basis"] == (
        "outgoing speech on V1, nothing follows: the end of the piece "
        "- a tail-only fade or an end-placed native dissolve only")

    for position in unbuildable:
        assert rows[position]["basis"] == (
            "outgoing transition_slot on V2: no V1 clip ends here")

    # Buildable, but the head half lands after the cutaway rather than on
    # the picture the viewer sees next - a different gesture, said so.
    assert rows["4"]["basis"] == (
        "outgoing speech on V1, incoming transition_slot on V2: the tail "
        "draws here, the head on the next V1 clip after the cutaway")
    assert rows["3"]["basis"] == (
        "outgoing speech on V1, incoming speech on V1: draws through this "
        "cut")


def test_a_bookend_card_carries_one_because_it_plays_on_v1():
    spine = [
        card("intro", 0.0, 2.0),
        speech("1", 2.0, 5.0),
        slot("2", 5.0, 7.0),
        speech("3", 7.0, 9.0),
    ]
    rows = {r["position"]: r for r in cut_carriers(spine)}
    assert rows["1"]["can_carry"] is True
    assert rows["1"]["basis"].startswith(
        "outgoing intro_card on V1, incoming speech on V1")
    assert rows["2"]["can_carry"] is True     # outgoing speech is on V1
    assert rows["3"]["can_carry"] is False    # outgoing slot is on V2


def test_a_cut_with_nothing_after_it_on_v1_cannot_carry_one():
    """compile_manifest needs an incoming clip for the head half."""
    spine = [
        speech("1", 0.0, 3.0),
        speech("2", 3.0, 6.0),
        slot("3", 6.0, 8.0),
    ]
    rows = {r["position"]: r for r in cut_carriers(spine)}
    assert rows["2"]["can_carry"] is True
    assert rows["3"]["can_carry"] is False
    assert rows["3"]["basis"] == (
        "outgoing speech on V1 but it ends the V1 track")


def test_the_compiler_accepts_every_cut_the_table_calls_buildable():
    """Drive compile_manifest's real V1 lookup over the real spine.

    A `yes` the compiler then refuses is the failure of the run of
    record with the sign flipped, and it would be invisible in a test
    that only re-derived the rule.
    """
    sys.path.insert(0, str(REPO / "library" / "steps"
                           / "step_5_04_compile_manifest"))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_compile_manifest_under_test",
            REPO / "library" / "steps" / "step_5_04_compile_manifest"
            / "step.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)

    v1_clips = [
        {"label": f"v1_{b['position']}",
         "timeline_in": b["timeline_start"],
         "timeline_out": b["timeline_end"]}
        for b in SPINE_001 if block_reaches_v1(b)
    ]

    for row in cut_carriers(SPINE_001):
        if row["position"] == "end":
            # The end row's "yes" is a different shape - a tail-only
            # fade / end-placed dissolve, never a two-picture draw -
            # so the two-picture agreement below does not reach it.
            # Its own agreement is tested in
            # test_end_transition_placement.py, against the compile's
            # end-slot routing.
            continue
        idx = module._v1_index_ending_at(v1_clips, row["cut_time"])
        buildable = idx is not None and idx + 1 < len(v1_clips)
        assert buildable == row["can_carry"], (
            f"cut {row['position']} at {row['cut_time']}s: the table says "
            f"{row['verdict']}, the compiler says "
            f"{'yes' if buildable else 'no'}")


def _run_bridge(payload):
    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=json.dumps(payload), capture_output=True,
        encoding="utf-8", cwd=str(REPO), env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_fact_reaches_the_table_the_prompt_reads():
    out = _run_bridge({
        "timed_spine": {"structure": SPINE_001},
        "music_selection": {},
        "music_analysis": {},
        "semantic_analysis": {},
        "clip_catalog": [],
        "b_roll_assignments": [],
    })

    header = out["cuts_toon"].splitlines()[0]
    # Fifteen cuts plus the rung-7 end-of-piece row.
    assert header == "[16]{" + ",".join(CUTS_HEADERS) + "}"

    rows = [line.split("\t") for line in out["cuts_toon"].splitlines()[1:]]
    verdicts = {r[0]: r[3] for r in rows}
    assert [p for p, v in verdicts.items() if v == "no"] == [
        "2", "5", "7", "9", "14"]
    assert verdicts["end"] == "yes"
    # The bridge filters nothing and re-ranks nothing: every cut is still
    # offered, in spine order (dropping unbuildable rows would move the
    # decision into the bridge - AGENTS.md 10.5).
    assert [r[0] for r in rows] == (
        [b["position"] for b in SPINE_001[1:]] + ["end"])

    # The definition is in the PROMPT now, not shipped beside the table.
    # The freeze that forced a `cuts_legend` dict was lifted 2026-09-09,
    # and a definition living in two places is worse than either.
    assert "cuts_legend" not in out, (
        "the derived columns are defined in handoff.md; shipping the "
        "definition as data too is the duplication the fold removed")


def test_a_spine_with_no_cuts_yields_only_the_end_row():
    """No cuts, but the piece still ends: the end row is not a cut row."""
    assert cut_carriers(None) == []
    assert cut_carriers([]) == []
    (row,) = cut_carriers([speech("1", 0.0, 1.0)])
    assert row["position"] == "end"
    assert row["can_carry"] is True


# --------------------------------------------------------------------------
# From test_transition_frames_use_the_projects_timebase.py
#
# `duration_frames` is computed from the timebase the catalog MEASURED
# (`project_fps`), never a 30.0 default nothing produces. History and the
# measured cost: `docs/evidence/transition_frames_timebase.md`.

def _block(position, clip, start, end):
    """A spine block that satisfies the contract in AGENTS.md 6."""
    return {"position": position, "block_type": "speech",
            "timeline_start": start, "timeline_end": end,
            "clip_id": clip, "source_start": 0.0,
            "source_end": end - start, "alignment_method": "test",
            "word_timestamps": [], "content": {"clip_id": clip}}


SPINE = {"structure": [_block(1, "clip_001", 0.0, 4.0),
                       _block(2, "clip_002", 4.0, 8.0)]}
PLAN = [{"cut_point_position": 2, "type": "defocus",
         "duration_feel": "medium", "rationale": "a held breath"}]
MUSIC = {"title": "nothing", "audio_path": "", "duration_seconds": 60.0}


def _run(payload):
    proc = subprocess.run(
        [sys.executable, str(STEP / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True,
        encoding="utf-8", cwd=str(REPO),
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
        check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _frames(payload):
    spec = _run(payload)["transition_spec"]
    drawn = [t for t in spec if t.get("duration_frames")]
    return drawn[0]["duration_frames"] if drawn else None


def test_the_projects_own_timebase_decides_the_frames():
    """Both directions (AGENTS.md 10.4): the right timebase gives the frames
    it implies, a different one CHANGES the answer, and `project_fps` wins
    over `frame_rate`, which nothing writes."""
    base = {"transition_creative": PLAN, "timed_spine": SPINE,
            "music_selection": MUSIC}
    at_ntsc = _frames(dict(base, project_fps=23.976))
    assert at_ntsc == int(10 * (23.976 / 30)), at_ntsc
    assert _frames(dict(base, project_fps=30.0)) == 10
    assert _frames(dict(base, project_fps=23.976, frame_rate=30.0)) == at_ntsc


# --------------------------------------------------------------------------
# From test_end_transition_placement.py
#
# Rung 7 end slot (K1, TR3.1): a transition addressed to the end builds.
#
# TR3.1's "1s dip to black out of the final shot" had no slot: the plan
# could only address cuts between blocks, the compile failed it ("does
# not sit at the end of any V1 clip" - since finding 32, a recorded
# downgrade), and resolve-axi refuses end transitions although the raw
# API places one. The plan addresses the end with `cut_point_position:
# "end"`; the compile routes an end-placed fade to the tail-only Fusion
# build and an end-placed dissolve to the end-placed native build, and
# the applicators place exactly that - judged by what they return.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _compile_with(project, plan_transitions):
    from unittest.mock import patch

    from library.steps.step_5_04_compile_manifest import step

    project_dir, layout, outputs, sfx_file = project
    outputs = copy.deepcopy(outputs)
    outputs["plan_transitions"] = {"transition_spec": plan_transitions}
    layout.pipeline_data_path.write_text(
        json.dumps({"step_outputs": outputs,
                    "project_folder": str(project_dir)}),
        encoding="utf-8")
    with patch.object(step, "load_sfx_catalog", return_value=[
            {"sfx_id": "whoosh.wav", "path": sfx_file,
             "duration_seconds": 0.5, "transient_offset_sec": 0.0}]):
        return step.compile_manifest(str(layout.output_root))


@pytest.fixture
def project(tmp_path):
    """Two abutting V1 clips (0-2.285, 2.285-5.418), under tmp_path only."""
    import tests.scenarios.test_compile_manifest_without_the_decoration as base
    from library.tools import music_audit_trail as audit
    from library.tools.project_layout import ProjectLayout

    media = tmp_path / "media"
    media.mkdir()
    names = {}
    for name in ("a_roll.mov", "b_roll.mov", "bed.wav", "whoosh.wav",
                 "sub_seg_000.mov"):
        path = media / name
        path.write_bytes(b"\x00" * 64)
        names[name] = str(path)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    layout = ProjectLayout(str(project_dir))
    layout.ensure()
    outputs = base._step_outputs(
        names["a_roll.mov"], names["b_roll.mov"], names["bed.wav"],
        names["whoosh.wav"], names["sub_seg_000.mov"])
    audit.write_audit_trail(
        str(project_dir), outputs["music_selection"]["music_selection"])
    return project_dir, layout, outputs, names["whoosh.wav"]


def test_end_fade_compiles_tail_only_on_the_last_clip(project):
    """TR3.1's dip: fade_to_black at the end is one tail row, no head."""
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_099", "transition_type": "fade_to_black",
         "cut_point_timeline": 5.418, "duration_frames": 30,
         "duration_source": "stated_frames", "at_end": True}])
    (row,) = manifest["fusion_effects"]["transitions"]
    assert row["type"] == "fade_to_black"
    assert row["after_clip"] == 1
    assert row["at_end"] is True
    assert row["duration_frames"] == 30
    assert manifest["transitions_downgraded"] == []


def test_end_entry_needing_two_pictures_downgrades(project):
    """Stale state carrying zoom_blur at the end: recorded, run builds."""
    manifest = _compile_with(project, plan_transitions=[
        {"transition_id": "trans_099", "transition_type": "zoom_blur",
         "cut_point_timeline": 5.418, "duration_frames": 8,
         "at_end": True}])
    assert manifest["fusion_effects"]["transitions"] == []
    (downgraded,) = manifest["transitions_downgraded"]
    assert downgraded["transition_id"] == "trans_099"
    assert "only fade_to_black draws its tail half" in downgraded["reason"]


class _FakeItem:
    def __init__(self, name, start, end):
        self._name = name
        self._start = start
        self._end = end
        self.payloads = []

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def AddTransition(self, payload):
        self.payloads.append(dict(payload))
        placed = _FakeItem(payload["type"], self._end - 10, self._end)
        return placed


def test_the_native_applicator_places_the_end_on_the_last_item():
    """The end op lands on the last item at its end - never an incoming."""
    from library.tools import native_ops_apply as apply

    first = _FakeItem("clip_a", 0, 150)
    last = _FakeItem("clip_b", 150, 300)
    report = apply.apply_native_transitions(
        None, [first, last],
        [{"transition_id": "trans_099", "resolve_name": "Cross Dissolve",
          "category": "simple", "after_clip": 1, "at_end": True,
          "duration_frames": 30}],
        fps=30.0)
    assert report["failed"] == []
    (row,) = report["applied"]
    assert row["position"] == "end"
    assert first.payloads == []
    (payload,) = last.payloads
    assert payload["position"] == "end"
    assert payload["duration"] == 30


# --------------------------------------------------------------------------
# From test_misplaced_transition_downgrades.py
#
# Finding 32: a transition 4.02 accepts that no V1 cut can carry ships as
# the hard cut the boundary already is, recorded in `transitions_downgraded`
# with its reason - never failing the whole compile (AGENTS.md 10.5).
# History: `docs/evidence/transition_own_track.md`.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_a_transition_no_v1_cut_carries_downgrades_instead_of_failing(
        project):
    """The B6 shape on the native path (a dissolve mid-clip, no V1 clip
    ends at 1.0s), the same on the Fusion path, and a transition on the
    last V1 clip (5.418s), which has no incoming clip for its head half."""
    cases = [
        ("cross_dissolve", 1.0, 0, "native_transitions", "V1"),
        ("crash_zoom", 1.0, 0, None, None),
        ("cross_dissolve", 5.418, 1, "native_transitions", "incoming"),
    ]
    for ttype, at, after, placed_key, reason in cases:
        manifest = _compile_with(project, plan_transitions=[
            {"transition_id": "trans_002", "transition_type": ttype,
             "cut_point_timeline": at, "duration": 0.4,
             "after_clip": after}])
        if placed_key:
            assert manifest[placed_key] == []
        else:
            assert manifest["fusion_effects"]["transitions"] == []
        (downgraded,) = manifest["transitions_downgraded"]
        assert downgraded["transition_id"] == "trans_002"
        assert downgraded["shipped_type"] == "hard_cut"
        if reason:
            assert reason in downgraded["reason"], (ttype, at)
