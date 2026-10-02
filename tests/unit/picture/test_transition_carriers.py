"""The transitions step is told which cuts can carry a drawn transition.

The rule is derived from where the picture is PLACED, so the test drives
the real placement - ``compile_manifest``'s own V1 membership predicate -
against ``cut_carriers``' answer, and drives the real bridge to check the
fact reaches the table.  It never asserts the shape of a string the model
does not read.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


from library.tools.transition_carriers import (
    block_reaches_v1,
    cut_carriers,
)

REPO = Path(__file__).resolve().parents[3]
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
