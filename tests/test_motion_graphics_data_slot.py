"""The answer schema carries `data` and `asset`, and junk payloads are refused.

Captain's intent 2026-09-21 (the cheap half of
vep-motion-graphics-are-type-not-graphics): the answer schema handed to
the motion-graphics planner enumerates an entry's fields as
element/anchor/row/copy/color-or-colour_role/entrance/exit/why. `data`
and `asset` are not in that list, and the seven roster elements that
draw a THING rather than words carry their content in exactly those two
fields. Measured consequence: comparison_bars, counter_roll,
digit_counter, pointer_annotation, step_counter, website_panel and
channel_bug were proposed ZERO times each across all 145 entries ever
planned.

The proof lane (vep-mg-schema-data-slot-proof) showed the slot alone
invites junk: equal-pair payloads ([3,3], [5,5]) that mean nothing as
comparisons, and `data` copied onto entries whose element never draws
it. So the schema surface AND the two refusals are pinned here
together: a planner CAN choose a depicting element, and the two junk
shapes are dropped by name.
"""

import json
import os
import re
from types import SimpleNamespace

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from library.tools import motion_graphics_plan as mgp
from library.tools import reel_semantic_visual as sem_vis

HANDOFF = os.path.join(
    PROJECT_ROOT, "library", "steps", "step_4_06_render_motion_graphics",
    "handoff.md")
MANIFEST = os.path.join(
    PROJECT_ROOT, "library", "steps", "step_4_06_render_motion_graphics",
    "manifest.json")

FPS = 30
DURATION = 12.0


def _entry(**kw):
    base = {
        "element": "title_lockup",
        "start_seconds": 0.5,
        "duration_seconds": 2.0,
        "anchor": "top_left",
        "copy": {"display": "A NAME"},
        "color": "#F5F5F0",
    }
    base.update(kw)
    return base


def _resolve(plan):
    return mgp.resolve_plan(plan, timeline_duration=DURATION, fps=FPS,
                            palette_roles={})


def _bars(values):
    return _entry(
        element="comparison_bars", anchor="centre",
        copy={"display": "THIS YEAR", "supporting": "LAST YEAR"},
        data={"values": list(values)})


def _roll(start, end):
    return _entry(
        element="counter_roll", anchor="centre",
        copy={"display": "SIGNUPS"},
        data={"start_value": start, "end_value": end})


# ── the schema surface: data and asset exist wherever a planner learns
# what an entry may contain ────────────────────────────────────────────

def test_manifest_llm_outputs_names_data_and_asset():
    with open(MANIFEST, encoding="utf-8") as handle:
        manifest = json.load(handle)
    outputs = manifest["interface"]["llm_outputs"]
    plan = next(o for o in outputs if o["name"] == "motion_graphics_plan")
    assert "data" in plan["description"]
    assert "asset" in plan["description"]


def _answer_blocks():
    with open(HANDOFF, encoding="utf-8") as handle:
        text = handle.read()
    start = text.index("## Your answer")
    return re.findall(r"```json(.*?)```", text[start:], re.S)


def test_handoff_answer_examples_carry_data_and_asset_slots():
    blocks = _answer_blocks()
    assert blocks, "no worked examples under 'Your answer'"
    joined = "\n".join(blocks)
    assert '"data"' in joined
    assert '"asset"' in joined


def test_handoff_shows_a_worked_depicting_example():
    """At least one example is a depicting element, not a copy element.

    Every example today is a copy element, which is part of why every
    answer is one. A planner that has never SEEN a comparison_bars
    entry with a data payload does not write one.
    """
    blocks = _answer_blocks()
    assert any("comparison_bars" in block and '"data"' in block
               for block in blocks), (
        "no worked example plans a depicting element with a data payload")


def _moment():
    return SimpleNamespace(
        number=9,
        timeline_name="Reel 09 - plays with his mind",
        timeline_start=10.0, timeline_end=18.0)


def _transcript():
    return {"segments": [
        {"timeline_start": 10.0, "timeline_end": 14.0,
         "resolve_item_id": "item1", "source_file": "clip_a.mov",
         "source_start": 100.0, "source_end": 104.0,
         "words": [
             {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
             {"word": "plays", "start": 11.0, "end": 11.4, "timed": True},
             {"word": "mind", "start": 12.5, "end": 13.0,
              "timed": True}]},
        {"timeline_start": 14.0, "timeline_end": 18.0,
         "resolve_item_id": "item2", "source_file": "clip_a.mov",
         "source_start": 104.0, "source_end": 108.0,
         "words": [
             {"word": "again", "start": 14.2, "end": 14.6,
              "timed": True}]}]}


def test_reel_expected_schema_names_data_and_asset(tmp_path):
    project = str(tmp_path / "proj")
    os.makedirs(os.path.join(project, "pipeline_output", "review"))
    path = sem_vis.write_request(
        _moment(), _transcript(), [(10.0, 18.0)], project, FPS)
    with open(path, encoding="utf-8") as handle:
        request = json.load(handle)
    assert "data" in request["expected_schema"]
    assert "asset" in request["expected_schema"]


# ── the refusals: the proof lane's two junk shapes ────────────────────

def test_equal_pair_bars_are_dropped_by_name():
    """[3,3] means nothing as a comparison and violates the roster's own
    `never` rules. Dropped, not drawn as two identical bars."""
    resolved = _resolve([_bars([3, 3])])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason in mgp.DROP_REASONS


def test_a_roll_that_goes_nowhere_is_dropped_by_name():
    """A counter_roll whose start is its end is a static figure with
    animation for its own sake - the roster names stat_callout for that."""
    resolved = _resolve([_roll(5, 5)])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason in mgp.DROP_REASONS


def test_data_on_an_element_that_draws_none_is_dropped_by_name():
    """A copy element carrying `data` is the proof lane's copy-everywhere
    shape: the payload reaches no node in the composition and would
    otherwise travel silently on the moment."""
    resolved = _resolve([_entry(data={"values": [3, 9]})])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason in mgp.DROP_REASONS


# ── the exception: the engine's own lower-third directive ──────────

def _lower_third(**kw):
    base = _entry(
        element="lower_third", anchor="bottom_left",
        copy={"display": "ADA LOVELACE", "supporting": "Analyst"},
        color="#11FFAA",
        data={"construction": "staged_rule",
              "speaker": "Ada",
              "colour_basis": "effect.speaker_lower_thirds"})
    base.update(kw)
    return base


def test_the_staged_construction_directive_resolves():
    """PR #1258's `data_no_element_draws` drop read the roster axes as
    the whole of what the composition draws and dropped every speaker
    card the night it landed - the only motion graphics the captain
    kept. `lower_third` declares no `data` axis and correctly so (the
    axis is a measured payload; the directive is engine-written), but
    the composition's own arm reads `data.construction`. The entry the
    deterministic speaker path writes - including the post-truncation
    shape - resolves and carries its payload."""
    entry = _lower_third(data={
        "construction": "staged_rule",
        "speaker": "Ada",
        "colour_basis": "effect.speaker_lower_thirds",
        "truncated_for_next": {"hold_seconds": 3.5,
                               "duration_seconds": 3.23,
                               "next_starts_at": 3.23}})
    resolved = _resolve([entry])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    (moment,) = resolved.moments
    assert moment["data"]["construction"] == "staged_rule"


def test_bare_data_on_a_lower_third_still_drops():
    """The exemption is the directive, not the element: depicting
    magnitudes on a `lower_third` reach no node in the composition and
    still drop by name."""
    resolved = _resolve([_lower_third(data={"values": [3, 9]})])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason == "data_no_element_draws"


# ── the second exception: the engine's own explainer directive ──────

def _staged_list(**kw):
    base = _entry(
        element="list_build", anchor="bottom_left",
        copy=[{"text": "LINKEDIN", "type_role": "supporting"},
              {"text": "CRUNCHBASE", "type_role": "supporting"},
              {"text": "REDDIT", "type_role": "supporting"}],
        color="#FFB8D4",
        data={"stage_offsets": [0.0, 0.8, 2.0]})
    base.update(kw)
    return base


def test_the_explainer_stage_offsets_resolve():
    """The same refusal caught a second deterministic producer the
    night it landed: `explainer_plan.plan_entries` writes one
    `list_build` entry carrying `data.stage_offsets` - the per-item
    seconds that make a staged element an explainer - and `list_build`
    declares no `data` axis. But the renderer's `stageStarts` reads
    exactly that key, so the entry resolves and carries its payload.
    The #1270 exemption covered only the lower-third directive and
    this test stayed red until the second arm landed."""
    resolved = _resolve([_staged_list()])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    (moment,) = resolved.moments
    assert moment["data"] == {"stage_offsets": [0.0, 0.8, 2.0]}


def test_bare_data_on_a_staged_list_still_drops():
    """The exemption is the directive, not the element: depicting
    magnitudes on a `list_build` reach no node in the composition and
    still drop by name."""
    resolved = _resolve([_staged_list(data={"values": [3, 9]})])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason == "data_no_element_draws"

def test_distinct_bars_resolve_and_carry_their_payload():
    resolved = _resolve([_bars([3, 9])])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    (moment,) = resolved.moments
    assert moment["data"] == {"values": [3, 9]}


def test_a_roll_with_a_real_change_resolves():
    resolved = _resolve([_roll(0, 100)])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    assert resolved.moments[0]["data"] == {
        "start_value": 0, "end_value": 100}


def test_a_copy_entry_without_data_is_untouched():
    resolved = _resolve([_entry()])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    assert resolved.moments[0]["data"] == {}


# ── ARM A second half: the slot ships WITH the instruction ──────────
#
# The proof lane showed a bare slot produces garbage: `data` copied
# onto all 8 entries, no switch to a depicting element, invented
# equal-pair payloads ([3,3], [5,5]). So the schema surface above and
# the instruction below are pinned together: the licence to choose a
# depicting element, and which payloads may be asserted versus which
# need a measurement.

def _handoff_text():
    with open(HANDOFF, encoding="utf-8") as handle:
        return handle.read()


def _manifest_plan_description():
    with open(MANIFEST, encoding="utf-8") as handle:
        manifest = json.load(handle)
    outputs = manifest["interface"]["llm_outputs"]
    return next(
        o for o in outputs if o["name"] == "motion_graphics_plan")[
        "description"]


def _reel_request(tmp_path):
    project = str(tmp_path / "proj-agree")
    os.makedirs(os.path.join(project, "pipeline_output", "review"))
    path = sem_vis.write_request(
        _moment(), _transcript(), [(10.0, 18.0)], project, FPS)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_three_schema_surfaces_agree(tmp_path):
    """All three places that describe the slot to the model state one
    contract: the manifest's llm_outputs, the handoff's answer section,
    and the reel request file's expected_schema.

    A planner reading any one of them must get the same slot: `data`
    and `asset` exist, a payload is asserted only from what the speech
    itself states, and the two junk shapes drop by name.
    """
    manifest_desc = _manifest_plan_description()
    handoff = _handoff_text()
    schema = _reel_request(tmp_path)["expected_schema"]
    for surface in (manifest_desc, handoff, schema):
        assert "data" in surface
        assert "asset" in surface
        assert "speech itself" in surface
        assert "all equal" in surface


def test_manifest_and_handoff_name_the_same_depicting_set():
    """The elements a planner may assert a payload for are the same in
    both places that describe the contract to the model."""
    manifest_desc = _manifest_plan_description()
    handoff = _handoff_text()
    for surface in (manifest_desc, handoff):
        for element in ("comparison_bars", "counter_roll",
                        "digit_counter", "step_counter"):
            assert element in surface, (
                f"{element} named in one contract surface but not the other")
        for element in ("channel_bug", "website_panel"):
            assert element in surface, (
                f"{element} named in one contract surface but not the other")


def _flat(text: str) -> str:
    """One line: the handoff wraps prose, so multi-word rules are
    asserted against flattened text rather than raw lines."""
    return " ".join(text.split())


def test_depicting_licence_names_the_five_and_not_title_lockup():
    """The corpus shows a model that will not switch element kind on its
    own, so the instruction says plainly which elements depict a
    comparison, a count, a sequence or a measurement - and what the
    wrong answer is."""
    section = _flat(_handoff_text().split(
        "## A graphic may depict", 1)[1])
    for element in ("comparison_bars", "counter_roll", "digit_counter",
                    "step_counter", "progress_bar"):
        assert element in section, (
            f"{element} not licenced as a depicting element")
    assert "rather than `title_lockup` carrying the same words as type" \
        in section


def test_assert_vs_measure_rule_states_the_fallback():
    """The half that stops the [3,3] garbage: assert what the speech
    states, never invent a measurement - and what to do instead."""
    handoff = _flat(_handoff_text())
    assert "must NOT invent one you would have had to measure" in handoff
    assert "choose an element that does not need one" in handoff


def test_instruction_reaches_the_reel_request(tmp_path):
    """A rule nobody reads is the failure mode this task exists to
    avoid: the depicting section must travel on the prompt the reel
    path actually writes, beside the schema that carries the slot."""
    prompt = _flat(sem_vis.handoff_text())
    assert "## A graphic may depict" in prompt
    assert "must NOT invent one you would have had to measure" in prompt
    assert "choose an element that does not need one" in prompt
    request = _reel_request(tmp_path)
    assert "## A graphic may depict" in request["prompt"]
    assert "must NOT invent one you would have had to measure" in _flat(
        request["prompt"])
    assert "data" in request["expected_schema"]
    assert "asset" in request["expected_schema"]
    assert "speech itself" in request["expected_schema"]
    assert "all equal" in request["expected_schema"]
