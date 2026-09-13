"""The full-frame element: its roster, its declaration, and its refusals.

Every gate here is proved in BOTH directions.  A declaration that should
be refused is asserted to raise AND the neighbouring valid one is
asserted to pass, because a validator that refuses everything reads
exactly like a validator that works (AGENTS.md 10.4).

The timeline half - what the conformance verifier does when picture is a
graphic rather than footage - is in
``tests/test_reel_conformance_full_frame.py``.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from library.tools import full_frame_element as ffe
from library.tools import motion_graphics_vocabulary as mg


# ── The roster ───────────────────────────────────────────────────────


def test_the_roster_is_well_formed():
    ffe.assert_roster_is_well_formed()


def test_the_roster_gate_can_fail_on_an_entry_with_no_refusals():
    """The gate that refuses an entry stating only what a thing IS."""
    bad = replace(ffe.ROSTER[0], never=())
    original = ffe.ROSTER
    try:
        ffe.ROSTER = original + (replace(bad, key="silent_entry"),)
        with pytest.raises(ffe.FullFrameVocabularyError, match="no refusals"):
            ffe.assert_roster_is_well_formed()
    finally:
        ffe.ROSTER = original
    ffe.assert_roster_is_well_formed()


def test_the_roster_gate_can_fail_on_an_unknown_reachability():
    original = ffe.ROSTER
    try:
        ffe.ROSTER = original + (
            replace(original[0], key="mystery", reachable="probably"),)
        with pytest.raises(ffe.FullFrameVocabularyError, match="reachability"):
            ffe.assert_roster_is_well_formed()
    finally:
        ffe.ROSTER = original


def test_an_element_cannot_be_in_the_roster_and_out_of_it():
    original = ffe.ROSTER
    try:
        ffe.ROSTER = original + (replace(original[0], key="intro_card"),)
        with pytest.raises(ffe.FullFrameVocabularyError, match="out of it"):
            ffe.assert_roster_is_well_formed()
    finally:
        ffe.ROSTER = original


def test_the_boundary_names_the_module_that_owns_each_near_miss():
    """A near miss is REDIRECTED, never merely refused."""
    for key, why in ffe.OUT_OF_VOCABULARY.items():
        assert ".py" in why or "AGENTS.md" in why, (
            f"{key} is refused without naming who owns it: {why!r}")


def test_the_motion_characters_are_the_overlay_rosters_own():
    """One vocabulary of motion character in this engine, not two."""
    assert ffe.MOTION_CHARACTERS == tuple(
        mg.AXES_BY_NAME["entrance"].positions)
    assert ffe.NO_MOTION in ffe.MOTION_CHARACTERS


def test_roster_rows_carry_every_column_the_legend_names():
    rows = ffe.roster_rows()
    assert rows
    for row in rows:
        for column in ("element", "copy", "reachable", "never"):
            assert row[column], f"{row['element']} has no {column}"
            assert column in ffe.ROSTER_LEGEND


# ── The declaration ──────────────────────────────────────────────────


VALID = {
    "element": "full_frame_card",
    "placement": "head",
    "duration_seconds": 2.0,
    "background": "#101014",
    "entrance": "blur",
    "exit": "fade",
    "font_family": "Montserrat",
    "runs": [
        {"bind": "opening_line", "type_role": "display", "colour": "#FFFFFF"},
    ],
}


def declare(**overrides):
    entry = copy.deepcopy(VALID)
    entry.update(overrides)
    return {"full_frame_elements": [entry]}


def test_a_project_that_declares_nothing_gets_nothing():
    assert ffe.declared_elements(None) == []
    assert ffe.declared_elements({}) == []
    assert ffe.declared_elements({"full_frame_elements": []}) == []


def test_the_valid_declaration_is_accepted():
    """The other half of every refusal below."""
    normalised = ffe.declared_elements(declare())
    assert len(normalised) == 1
    assert normalised[0]["placement"] == "head"
    assert normalised[0]["duration_seconds"] == 2.0
    assert normalised[0]["runs"][0]["bind"] == "opening_line"


@pytest.mark.parametrize("overrides,expect", [
    ({"element": None}, "names no `element`"),
    ({"element": "intro_card"}, "bookend"),
    ({"element": "title_lockup"}, "overlay roster"),
    ({"element": "not_a_thing"}, "does not draw"),
    ({"placement": "middle"}, "no default"),
    ({"placement": None}, "no default"),
    ({"duration_seconds": 0}, "lasts no time"),
    ({"duration_seconds": -1}, "lasts no time"),
    ({"duration_seconds": True}, "lasts no time"),
    ({"duration_seconds": ffe.MAX_CARD_SECONDS + 0.1}, "not a card"),
    ({"background": ""}, "no `background`"),
    ({"background": None}, "no `background`"),
    ({"entrance": "shimmer"}, "motion characters"),
    ({"exit": "shimmer"}, "motion characters"),
    ({"font_family": ""}, "no `font_family`"),
    ({"font_family": "Nonesuch Display"}, "cannot deliver"),
    ({"runs": []}, "no `runs`"),
    ({"runs": None}, "no `runs`"),
    ({"y": 1.4}, "between 0 and 1"),
    ({"y": "middle"}, "between 0 and 1"),
    ({"opening_seconds": 0}, "positive number"),
])
def test_a_malformed_declaration_is_refused_by_name(overrides, expect):
    with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
        ffe.declared_elements(declare(**overrides))


@pytest.mark.parametrize("run,expect", [
    ({"text": "A", "bind": "speakers", "type_role": "display",
      "colour": "#FFF"}, "both `text` and `bind`"),
    ({"type_role": "display", "colour": "#FFF"}, "neither `text` nor `bind`"),
    ({"bind": "episode_title", "type_role": "display", "colour": "#FFF"},
     "does not produce"),
    ({"text": "  ", "type_role": "display", "colour": "#FFF"},
     "empty `text`"),
    ({"text": "A", "type_role": "headline", "colour": "#FFF"},
     "typographic weights"),
    ({"text": "A", "type_role": "display"}, "no `colour`"),
    ({"text": "A", "type_role": "display", "colour": "#FFF",
      "font_size": -4}, "positive number"),
])
def test_a_malformed_run_is_refused_by_name(run, expect):
    with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
        ffe.declared_elements(declare(runs=[run]))


def test_a_run_may_state_its_own_words():
    """The other direction: literal copy is legal and is not a binding."""
    normalised = ffe.declared_elements(declare(runs=[
        {"text": "Season two", "type_role": "supporting", "colour": "#FFF"}]))
    assert normalised[0]["runs"][0]["text"] == "Season two"
    assert normalised[0]["runs"][0]["bind"] is None


def test_a_declaration_with_no_motion_defaults_to_the_one_that_draws_none():
    entry = copy.deepcopy(VALID)
    entry.pop("entrance")
    entry.pop("exit")
    normalised = ffe.declared_elements({"full_frame_elements": [entry]})
    assert normalised[0]["entrance"] == ffe.NO_MOTION
    assert normalised[0]["exit"] == ffe.NO_MOTION


def test_the_project_yaml_wins_over_the_brand_template(tmp_path):
    """A card is copy the viewer reads, so the project owns it."""
    (tmp_path / "project.yaml").write_text(
        "effect:\n"
        "  full_frame_elements:\n"
        "    - element: full_frame_card\n"
        "      placement: tail\n"
        "      duration_seconds: 1.0\n"
        "      background: '#000000'\n"
        "      font_family: Montserrat\n"
        "      runs:\n"
        "        - text: from the project\n"
        "          type_role: micro\n"
        "          colour: '#FFFFFF'\n",
        encoding="utf-8")
    template_slot = {"full_frame_elements": [dict(VALID)]}
    resolved = ffe.resolve_declaration(template_slot, str(tmp_path))
    elements = ffe.declared_elements(resolved)
    assert len(elements) == 1
    assert elements[0]["placement"] == "tail"
    assert elements[0]["runs"][0]["text"] == "from the project"


def test_a_project_that_declares_none_leaves_the_template_slot_alone(tmp_path):
    (tmp_path / "project.yaml").write_text("name: x\n", encoding="utf-8")
    resolved = ffe.resolve_declaration(
        {"full_frame_elements": [dict(VALID)]}, str(tmp_path))
    assert len(ffe.declared_elements(resolved)) == 1


# ── The facts a card may quote ───────────────────────────────────────


class _Moment:
    number = 7
    speakers = ("Akshita", "Craig")


def _transcript():
    return {"segments": [{
        "speaker": "Akshita", "timeline_start": 10.0, "timeline_end": 16.0,
        "text": "So ranking number one on Google but invisible to AI",
        "words": [
            {"word": w, "start": 10.0 + i * 0.4, "end": 10.35 + i * 0.4,
             "timed": True}
            for i, w in enumerate(
                "So ranking number one on Google but invisible to AI".split())
        ],
    }]}


def test_the_opening_line_is_quoted_from_the_ranges_the_reel_plays():
    facts = ffe.ReelFacts.from_moment(
        _Moment(), [(10.0, 16.0)], _transcript(), opening_seconds=3.0)
    assert facts.opening_line().startswith("So ranking number one")
    assert facts.reel_number == 7


def test_a_narrower_window_quotes_fewer_words():
    facts = ffe.ReelFacts.from_moment(
        _Moment(), [(10.0, 16.0)], _transcript(), opening_seconds=4.0)
    wide = facts.opening_line()
    narrow = facts.opening_line(1.0)
    assert len(narrow.split()) < len(wide.split())
    assert wide.startswith(narrow)


def test_a_card_asking_for_more_opening_than_was_measured_is_refused():
    """Never a quotation quietly shorter than the one declared."""
    facts = ffe.ReelFacts.from_moment(
        _Moment(), [(10.0, 16.0)], _transcript(), opening_seconds=2.0)
    with pytest.raises(ffe.FullFrameDeclarationError, match="measured over"):
        facts.opening_line(6.0)


def test_required_opening_window_reads_the_widest_declaration():
    from library.tools import reel_opening
    assert ffe.required_opening_window([]) == reel_opening.OPENING_SECONDS
    assert ffe.required_opening_window(
        [{"opening_seconds": 9.0}, {"opening_seconds": None}]) == 9.0


def test_a_binding_with_nothing_behind_it_refuses_the_card():
    """An empty run is never drawn and never substituted."""
    declarations = ffe.declared_elements(declare())
    empty = ffe.ReelFacts(reel_number=3, speakers=(), opening=(),
                          opening_window=3.0)
    with pytest.raises(ffe.FullFrameDeclarationError, match="nothing there"):
        ffe.plan_reel_cards(declarations, empty, 960, 24000 / 1001, width=1080, height=1920)


def test_an_unknown_binding_name_is_refused_at_the_facts():
    facts = ffe.ReelFacts(reel_number=1, speakers=("A",), opening=(),
                          opening_window=3.0)
    with pytest.raises(ffe.FullFrameDeclarationError, match="not a copy binding"):
        facts.binding("episode_number")


# ── Planning ─────────────────────────────────────────────────────────


FPS = 24000 / 1001


def _facts():
    return ffe.ReelFacts.from_moment(
        _Moment(), [(10.0, 16.0)], _transcript(), opening_seconds=3.0)


def test_a_head_card_starts_at_reel_zero_and_a_tail_card_after_the_body():
    head = copy.deepcopy(VALID)
    tail = copy.deepcopy(VALID)
    tail["placement"] = "tail"
    tail["duration_seconds"] = 1.5
    tail["runs"] = [{"bind": "speakers", "type_role": "micro",
                     "colour": "#FFF"}]
    declarations = ffe.declared_elements(
        {"full_frame_elements": [head, tail]})
    body_frames = 960
    cards = ffe.plan_reel_cards(declarations, _facts(), body_frames, FPS, width=1080, height=1920)
    assert [c.placement for c in cards] == ["head", "tail"]
    assert cards[0].reel_start_frame == 0
    # The tail card starts on the frame after the last one of picture -
    # integer arithmetic, so there is no rounding gap to be an F1 hole.
    assert cards[1].reel_start_frame == cards[0].duration_frames + body_frames
    assert cards[0].reel_end_frame == cards[0].duration_frames


def test_two_head_cards_stack_rather_than_overlap():
    first = copy.deepcopy(VALID)
    second = copy.deepcopy(VALID)
    second["duration_seconds"] = 1.0
    second["runs"] = [{"text": "and then", "type_role": "micro",
                       "colour": "#FFF"}]
    declarations = ffe.declared_elements(
        {"full_frame_elements": [first, second]})
    cards = ffe.plan_reel_cards(declarations, _facts(), 960, FPS, width=1080, height=1920)
    assert cards[0].reel_start_frame == 0
    assert cards[1].reel_start_frame == cards[0].duration_frames
    assert cards[0].reel_end_frame == cards[1].reel_start_frame


def test_the_props_carry_the_declaration_and_nothing_the_engine_chose():
    cards = ffe.plan_reel_cards(
        ffe.declared_elements(declare()), _facts(), 960, FPS, width=1080, height=1920)
    props = cards[0].props
    assert props["background"] == "#101014"
    assert props["entrance"] == "blur"
    assert props["fontFamily"] == "Montserrat"
    assert props["width"] == 1080 and props["height"] == 1920
    assert props["safeArea"]["top"] > 0
    assert props["durationInFrames"] == int(round(2.0 * FPS))
    # The engine states no position when the declaration states none.
    assert "y" not in props
    assert props["runs"][0]["colour"] == "#FFFFFF"
    assert props["runs"][0]["text"].startswith("So ranking")


def test_uppercase_is_the_declarations_and_is_off_unless_asked():
    plain = ffe.plan_reel_cards(
        ffe.declared_elements(declare(runs=[
            {"bind": "speakers", "type_role": "micro", "colour": "#FFF"}])),
        _facts(), 960, FPS, width=1080, height=1920)[0]
    shouted = ffe.plan_reel_cards(
        ffe.declared_elements(declare(runs=[
            {"bind": "speakers", "type_role": "micro", "colour": "#FFF",
             "uppercase": True}])),
        _facts(), 960, FPS, width=1080, height=1920)[0]
    assert plain.props["runs"][0]["text"] == "Akshita, Craig"
    assert shouted.props["runs"][0]["text"] == "AKSHITA, CRAIG"


def test_a_card_that_rounds_to_no_frames_is_refused():
    declarations = ffe.declared_elements(declare(duration_seconds=0.001))
    with pytest.raises(ffe.FullFrameDeclarationError, match="at least one"):
        ffe.plan_reel_cards(declarations, _facts(), 960, FPS, width=1080, height=1920)


def test_a_card_carries_no_file_until_it_has_been_rendered():
    card = ffe.plan_reel_cards(
        ffe.declared_elements(declare()), _facts(), 960, FPS, width=1080, height=1920)[0]
    assert card.rendered_path == ""


# ── Rendering ────────────────────────────────────────────────────────


def _fake_batch_success(seen):
    """A stand-in for the shared batch renderer that draws every job."""
    def fake_batch(jobs, **kwargs):
        seen["calls"] = seen.get("calls", 0) + 1
        seen["composition"] = kwargs.get("composition")
        seen["job_count"] = len(jobs)
        results = []
        for job in jobs:
            with open(job.out_path, "wb") as handle:
                handle.write(b"not empty")
            results.append({"ok": True, "out": job.out_path})
        return results
    return fake_batch


def test_the_render_batches_opaque_through_the_shared_renderer(
        monkeypatch, tmp_path):
    """One batch for every card, on the card's own declared ground.

    The one command-line difference between this layer and the overlay
    layer used to be asserted off the argv (no ``--transparent``); now
    that every card goes through the shared batch renderer, what is
    asserted is the route itself - one ``render_batch`` call carrying
    every card under the FullFrameCard composition - and the opacity
    half is the props: the card carries its own background ground
    rather than relying on black showing through an alpha channel that
    nothing is beneath.
    """
    seen = {}
    monkeypatch.setattr(ffe, "render_batch", _fake_batch_success(seen))
    cards = ffe.plan_reel_cards(
        ffe.declared_elements(declare()), _facts(), 960, FPS, width=1080, height=1920)
    rendered = ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))

    assert seen["calls"] == 1, (
        "every card in one batch: one bundle-and-launch, not one per card")
    assert seen["composition"] == ffe.FULL_FRAME_COMPOSITION
    assert seen["job_count"] == len(cards)
    assert rendered[0].rendered_path.endswith(".mov")
    # The props really reached disk beside the render.
    props = json.loads(
        (tmp_path / f"{cards[0].render_name}_props.json").read_text())
    assert props["background"] == "#101014"


def test_a_render_that_produces_no_file_raises(monkeypatch, tmp_path):
    def fake_batch(jobs, **kwargs):
        return [{"ok": True, "out": job.out_path} for job in jobs]

    monkeypatch.setattr(ffe, "render_batch", fake_batch)
    cards = ffe.plan_reel_cards(
        ffe.declared_elements(declare()), _facts(), 960, FPS, width=1080, height=1920)
    with pytest.raises(ffe.FullFrameRenderError, match="missing or empty"):
        ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))


def test_a_failed_render_raises_rather_than_leaving_a_reel_without_it(
        monkeypatch, tmp_path):
    def fake_batch(jobs, **kwargs):
        return [{"ok": False, "out": job.out_path,
                 "error": "chromium exploded"} for job in jobs]

    monkeypatch.setattr(ffe, "render_batch", fake_batch)
    cards = ffe.plan_reel_cards(
        ffe.declared_elements(declare()), _facts(), 960, FPS, width=1080, height=1920)
    with pytest.raises(ffe.FullFrameRenderError, match="chromium exploded"):
        ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))


def test_a_batch_that_cannot_start_raises(monkeypatch, tmp_path):
    """No renderer is not a card failure: it still refuses, by name."""
    from library.tools.remotion_batch import RemotionBatchError

    def fake_batch(jobs, **kwargs):
        raise RemotionBatchError("no node on PATH")

    monkeypatch.setattr(ffe, "render_batch", fake_batch)
    cards = ffe.plan_reel_cards(
        ffe.declared_elements(declare()), _facts(), 960, FPS, width=1080, height=1920)
    with pytest.raises(ffe.FullFrameRenderError, match="no node on PATH"):
        ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))


# ── The composition really exists ────────────────────────────────────


def test_the_named_composition_is_registered_with_remotion():
    """A props file for a composition nobody registered renders nothing."""
    from library.tools.paths import REMOTION_DIR

    root = (REMOTION_DIR / "src" / "Root.tsx").read_text(encoding="utf-8")
    assert f'id="{ffe.FULL_FRAME_COMPOSITION}"' in root
    component = (REMOTION_DIR / "src" / "compositions" /
                 ffe.FULL_FRAME_COMPOSITION / "index.tsx")
    assert component.is_file()
    source = component.read_text(encoding="utf-8")
    # It reuses the overlay composition's motion characters rather than
    # spelling a second set.
    assert "entranceTransform" in source and "typewriterSplit" in source
