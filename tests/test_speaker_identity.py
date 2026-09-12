"""The speaker lower third, and the two ways it is easy to get wrong.

The capability the captain asked for on 2026-09-12 has two properties
that are not obvious from the feature description, and both of them are
what this file exists to pin:

1. **A project that declares no speakers gets no lower thirds, and does
   not crash.** The engine serves a daily channel AND client work
   (AGENTS.md 14), so the four strings of one project may not reach
   library code and a second project must be able to declare a different
   cast - or none.
2. **A speaker who appears twice gets ONE graphic.** "on the first
   appearance" is the ask; a reel where Craig comes back six times must
   name him once.

Every project here is built under `tmp_path`. No test reaches a real
one (`tests/test_tests_never_reach_real_projects.py`).
"""

import json

import pytest

from library.tools import speaker_identity as si


# ── Fixtures: projects that declare, and projects that do not ────────

def _project(tmp_path, name, pipeline=None, effect=None):
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    config = {"name": name, "slug": name}
    if pipeline is not None:
        config["pipeline"] = pipeline
    if effect is not None:
        config["effect"] = effect
    import yaml
    (folder / "project.yaml").write_text(
        yaml.safe_dump(config), encoding="utf-8")
    return str(folder)


DECLARATION = {
    "anchor": "bottom_left",
    "hold_seconds": 3.0,
    "entrance": "draw",
    "exit": "fade",
    "speakers": {
        "Ada": {"name": "Ada Lovelace", "title": "Analyst",
                "colour": "#11FFAA"},
        "Bram": {"name": "Bram Stoker", "title": "Editor",
                 "colour": "#FF3366"},
    },
}


def _lines(*pairs):
    """`(speaker, reel_start)` pairs as `played_speech`'s shape."""
    return [{"speaker": who, "reel_start": at, "text": f"{who} line at {at}"}
            for who, at in pairs]


# ── 1. A project that declares nothing ───────────────────────────────

def test_a_project_declaring_no_speakers_gets_no_lower_thirds(tmp_path):
    """The generalisation case. No crash, no placeholder, no graphic."""
    folder = _project(tmp_path, "declares-nothing")
    plan = si.plan_for_reel(
        reel_name="Reel 01", lines=_lines(("Ada", 1.0), ("Bram", 8.0)),
        reel_seconds=60.0, project_folder=folder)
    assert plan.entries == []
    assert plan.introductions == []
    assert plan.declared is False
    assert plan.basis == si.NOT_DECLARED


def test_a_project_with_no_project_yaml_at_all_gets_no_lower_thirds(tmp_path):
    """A folder that is not a project answers the same way it answers
    every other optional declaration: nothing, rather than an error."""
    folder = tmp_path / "not-a-project"
    folder.mkdir()
    assert si.project_declaration(str(folder)) is None
    plan = si.plan_for_reel(
        reel_name="Reel 01", lines=_lines(("Ada", 1.0)),
        reel_seconds=60.0, project_folder=str(folder))
    assert plan.basis == si.NOT_DECLARED and plan.entries == []


def test_a_second_project_declares_a_different_cast(tmp_path):
    """Two projects, two casts, one engine. If this can fail, the four
    strings of one project have reached library code."""
    first = _project(tmp_path, "first",
                     effect={si.DECLARATION_KEY: DECLARATION})
    other = dict(DECLARATION)
    other["speakers"] = {
        "Zoe": {"name": "Zoe Okafor", "title": "Head of Research",
                "colour": "#0055FF"}}
    second = _project(tmp_path, "second",
                      effect={si.DECLARATION_KEY: other})

    plan_one = si.plan_for_reel(
        "R", _lines(("Ada", 1.0), ("Zoe", 2.0)), 60.0, first)
    plan_two = si.plan_for_reel(
        "R", _lines(("Ada", 1.0), ("Zoe", 2.0)), 60.0, second)

    assert [i.name for i in plan_one.introductions] == ["Ada Lovelace"]
    assert [i.name for i in plan_two.introductions] == ["Zoe Okafor"]


def test_no_speaker_identity_string_is_anywhere_in_library():
    """The identities in this repository's own field-test project may
    not be in the code that serves every project - not as a constant,
    not as a docstring example, not as a fallback.

    Scoped to `library/`, which is the engine. `tests/` carries
    `Akshita` and `Craig` as FIXTURES across many files and renaming
    those is a separate, declared piece of work; a fixture in a test is
    not a value the engine can reach.
    """
    import pathlib

    root = pathlib.Path(si.__file__).resolve().parents[2] / "library"
    # The four strings from the captain's marker of 2026-09-12. Built
    # rather than written, so this file does not carry them either.
    forbidden = [" ".join(parts) for parts in (
        ("Craig", "Lucie"), ("CEO", "Lucie", "Content"),
        ("Akshita", "Gorti"), ("AI", "@", "Lucie", "Content"))]
    offenders = []
    for path in root.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        offenders.extend(f"{path}: {bad!r}"
                         for bad in forbidden if bad in source)
    assert not offenders, (
        "one project's speaker identities are in engine code: "
        + "; ".join(offenders))


# ── 2. Once per speaker per reel ─────────────────────────────────────

def test_a_speaker_appearing_twice_gets_exactly_one_graphic(tmp_path):
    """The other case the ask turns on."""
    folder = _project(tmp_path, "repeat",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel(
        reel_name="Reel 13",
        lines=_lines(("Ada", 0.5), ("Bram", 4.0), ("Ada", 9.0),
                     ("Ada", 17.0), ("Bram", 22.0), ("Ada", 30.0)),
        reel_seconds=45.0, project_folder=folder)
    speakers = [i.speaker for i in plan.introductions]
    assert speakers == ["Ada", "Bram"]
    assert len(plan.entries) == 2
    assert [e["start_seconds"] for e in plan.entries] == [0.5, 4.0]


def test_first_appearance_is_the_reels_order_not_the_declarations(tmp_path):
    """Whoever the REEL plays first is introduced first, whatever order
    the project wrote its cast in."""
    folder = _project(tmp_path, "order",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel(
        "R", _lines(("Bram", 2.0), ("Ada", 7.0)), 40.0, folder)
    assert [i.speaker for i in plan.introductions] == ["Bram", "Ada"]


def test_an_undeclared_speaker_is_passed_over_in_silence(tmp_path):
    folder = _project(tmp_path, "third-voice",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel(
        "R", _lines(("Caller", 1.0), ("Ada", 5.0)), 40.0, folder)
    assert [i.speaker for i in plan.introductions] == ["Ada"]


def test_a_reel_no_declared_speaker_speaks_in_says_so(tmp_path):
    folder = _project(tmp_path, "nobody",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel("R", _lines(("Caller", 1.0)), 40.0, folder)
    assert plan.declared is True
    assert plan.basis == si.NO_DECLARED_SPEAKER_SPOKE
    assert plan.entries == []


def test_first_appearances_is_once_per_speaker_on_its_own():
    got = si.first_appearances(
        _lines(("A", 5.0), ("B", 1.0), ("A", 0.5)), ["A", "B"])
    assert [(g["speaker"], g["at_seconds"]) for g in got] == [
        ("A", 0.5), ("B", 1.0)]


# ── The colour: the project's, or nothing ────────────────────────────

def test_the_colour_falls_back_to_this_projects_own_caption_accent(tmp_path):
    folder = _project(
        tmp_path, "accent",
        pipeline={"speaker_subtitle_styles": {
            "Ada": {"accentColor": "#ABCDEF"}}},
        effect={si.DECLARATION_KEY: {
            **DECLARATION,
            "speakers": {"Ada": {"name": "Ada Lovelace", "title": "Analyst"}},
        }})
    plan = si.plan_for_reel("R", _lines(("Ada", 1.0)), 40.0, folder)
    assert plan.introductions[0].colour == "#ABCDEF"
    assert "speaker_subtitle_styles" in plan.introductions[0].colour_basis


def test_a_speaker_with_no_colour_anywhere_is_refused_not_invented(tmp_path):
    """There are no house looks. A colour the engine chose is a defect."""
    folder = _project(
        tmp_path, "colourless",
        effect={si.DECLARATION_KEY: {
            **DECLARATION,
            "speakers": {"Ada": {"name": "Ada Lovelace"}},
        }})
    plan = si.plan_for_reel("R", _lines(("Ada", 1.0)), 40.0, folder)
    assert plan.entries == []
    assert [r["reason"] for r in plan.refused] == [si.NO_COLOUR_DECLARED]


def test_a_speakers_own_colour_wins_over_the_caption_accent(tmp_path):
    folder = _project(
        tmp_path, "own-colour",
        pipeline={"speaker_subtitle_styles": {
            "Ada": {"accentColor": "#ABCDEF"}}},
        effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel("R", _lines(("Ada", 1.0)), 40.0, folder)
    assert plan.introductions[0].colour == "#11FFAA"


# ── A declaration that cannot be read is refused, never completed ────

@pytest.mark.parametrize("missing", ["anchor", "hold_seconds",
                                     "entrance", "exit"])
def test_a_declaration_missing_a_required_value_raises(missing):
    declaration = {k: v for k, v in DECLARATION.items() if k != missing}
    with pytest.raises(si.SpeakerIdentityError) as why:
        si.declared_speakers(declaration)
    assert missing in str(why.value)


def test_an_anchor_outside_the_vocabulary_raises():
    with pytest.raises(si.SpeakerIdentityError):
        si.declared_speakers({**DECLARATION, "anchor": "somewhere_nice"})


def test_a_motion_character_outside_the_vocabulary_raises():
    with pytest.raises(si.SpeakerIdentityError):
        si.declared_speakers({**DECLARATION, "entrance": "swoosh"})


def test_a_key_nothing_reads_raises():
    with pytest.raises(si.SpeakerIdentityError) as why:
        si.declared_speakers({**DECLARATION, "opacity": 0.5})
    assert "opacity" in str(why.value)


def test_a_speaker_with_no_name_raises():
    with pytest.raises(si.SpeakerIdentityError):
        si.declared_speakers({**DECLARATION,
                              "speakers": {"Ada": {"title": "Analyst"}}})


# ── The plan reaches the renderer's own resolver ─────────────────────

def test_the_entry_resolves_through_step_4_06s_own_resolver(tmp_path):
    """The plan this module writes must be a plan `resolve_plan` accepts
    - otherwise the graphic is dropped at the door and the drop reason
    is the first anyone hears of it."""
    from library.tools.motion_graphics_plan import resolve_plan

    folder = _project(tmp_path, "resolves",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel("R", _lines(("Ada", 2.0)), 40.0, folder)
    resolved = resolve_plan(plan.entries, timeline_duration=40.0, fps=24.0)
    assert not resolved.dropped, [d.reason for d in resolved.dropped]
    assert len(resolved.moments) == 1
    moment = resolved.moments[0]
    assert moment["element"] == "lower_third"
    assert moment["color"] == "#11FFAA"
    assert [run["text"] for run in moment["runs"]] == [
        "Ada Lovelace", "Analyst"]
    # What makes it the CONSTRUCTION rather than the flat panel.
    assert moment["data"]["construction"] == "staged_rule"


def test_a_speaker_with_no_title_sends_one_run(tmp_path):
    folder = _project(
        tmp_path, "no-title",
        effect={si.DECLARATION_KEY: {
            **DECLARATION,
            "speakers": {"Ada": {"name": "Ada Lovelace",
                                 "colour": "#11FFAA"}}}})
    plan = si.plan_for_reel("R", _lines(("Ada", 1.0)), 40.0, folder)
    assert plan.entries[0]["copy"] == [
        {"text": "Ada Lovelace", "type_role": "display"}]


def test_a_first_appearance_past_the_end_of_the_reel_is_refused(tmp_path):
    folder = _project(tmp_path, "past-the-end",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel("R", _lines(("Ada", 99.0)), 40.0, folder)
    assert plan.entries == []
    assert [r["reason"] for r in plan.refused] == [si.OUTSIDE_THE_REEL]


# ── One row, one moment: a card ends where the next begins ────────

HOLD_3_5 = {
    "anchor": "bottom_left",
    "hold_seconds": 3.5,
    "entrance": "draw",
    "exit": "fade",
    "speakers": {
        "Ada": {"name": "Ada Lovelace", "title": "Analyst",
                "colour": "#11FFAA"},
        "Bram": {"name": "Bram Stoker", "title": "Editor",
                 "colour": "#FF3366"},
        "Cy": {"name": "Cy Okonkwo", "title": "Producer",
               "colour": "#33AAFF"},
    },
}


def _held_project(tmp_path, name):
    return _project(tmp_path, name,
                    effect={si.DECLARATION_KEY: HOLD_3_5})


def test_a_card_ends_where_the_next_speakers_card_begins(tmp_path):
    """The Reel 06 overlap of 2026-09-12: 0.0s and 3.23s against a
    3.5s hold put both cards on screen together for 0.27s in one
    layout slot. Two things cannot occupy one row at one time, so the
    earlier card is truncated - mechanically, not by taste - and the
    hold itself is untouched."""
    folder = _held_project(tmp_path, "truncate")
    plan = si.plan_for_reel(
        "R", _lines(("Ada", 0.0), ("Bram", 3.23)), 60.0, folder)
    assert [(e["start_seconds"], e["duration_seconds"])
            for e in plan.entries] == [(0.0, 3.23), (3.23, 3.5)]
    first = plan.entries[0]
    assert first["data"]["truncated_for_next"] == {
        "hold_seconds": 3.5, "duration_seconds": 3.23,
        "next_starts_at": 3.23}
    assert "Truncated to 3.23s from 3.5s" in first["why"]
    assert "truncated_for_next" not in plan.entries[1]["data"]
    assert [i.speaker for i in plan.introductions] == ["Ada", "Bram"]


def test_a_card_clear_of_the_next_keeps_its_full_hold(tmp_path):
    """The Reel 13 shape: a 3.54s gap against a 3.5s hold never
    overlaps, so nothing is truncated and the 0.04s clearance is
    guaranteed by the invariant rather than luck."""
    folder = _held_project(tmp_path, "clear")
    plan = si.plan_for_reel(
        "R", _lines(("Ada", 0.0), ("Bram", 3.54)), 60.0, folder)
    assert [e["duration_seconds"] for e in plan.entries] == [3.5, 3.5]
    assert all("truncated_for_next" not in e["data"]
               for e in plan.entries)


def test_each_card_in_a_chain_ends_where_the_next_begins(tmp_path):
    """Truncation is per card, against the card after it - never the
    hold, never the card after that."""
    folder = _held_project(tmp_path, "chain")
    plan = si.plan_for_reel(
        "R", _lines(("Ada", 0.0), ("Bram", 2.0), ("Cy", 9.0)),
        60.0, folder)
    assert [(e["start_seconds"], e["duration_seconds"])
            for e in plan.entries] == [(0.0, 2.0), (2.0, 3.5), (9.0, 3.5)]


def test_a_card_truncated_below_the_readability_floor_is_refused(tmp_path):
    """A 0.3s card is not a name the viewer saw. Below the pipeline's
    own floor for timed on-screen text
    (`manifest_validator.MIN_CAPTION_DISPLAY_SECONDS`) the card is
    refused - with the gap, the hold and the floor all named, so the
    run says which line the project has to move - and its introduction
    goes with it, because the two lists are parallel."""
    from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS

    folder = _held_project(tmp_path, "too-short")
    plan = si.plan_for_reel(
        "R", _lines(("Ada", 0.0), ("Bram", 0.3)), 60.0, folder)
    assert [(e["start_seconds"], e["duration_seconds"])
            for e in plan.entries] == [(0.3, 3.5)]
    assert [i.speaker for i in plan.introductions] == ["Bram"]
    assert [(r["speaker"], r["reason"]) for r in plan.refused] == [
        ("Ada", si.TRUNCATED_BELOW_READABLE)]
    detail = plan.refused[0]["detail"]
    assert "0.3s" in detail and "3.5s" in detail
    assert str(MIN_CAPTION_DISPLAY_SECONDS) in detail
    assert si.TRUNCATED_BELOW_READABLE in si.REFUSALS


def test_a_truncation_landing_exactly_on_the_floor_is_kept(tmp_path):
    """The floor refuses BELOW, not AT: a card ending with exactly the
    floor's seconds still draws."""
    from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS

    folder = _held_project(tmp_path, "on-the-floor")
    plan = si.plan_for_reel(
        "R", _lines(("Ada", 0.0),
                    ("Bram", MIN_CAPTION_DISPLAY_SECONDS)),
        60.0, folder)
    assert plan.entries[0]["duration_seconds"] == pytest.approx(
        MIN_CAPTION_DISPLAY_SECONDS)
    assert plan.refused == []


# ── Where it sits: measured, above the captions ──────────────────────

def test_the_box_bottom_clears_the_declared_caption_row(tmp_path):
    folder = _project(
        tmp_path, "caption-row",
        pipeline={"subtitle_position": {"caption_row": 0.71875}})
    box = si.placement_box(folder, 1080, 1920, caption_height=None)
    assert box["caption_row"] == 1380
    # The floor is the row, so nothing is drawn on or below it.
    assert 1920 - box["insets"]["bottom"] == 1380


def test_a_measured_caption_card_lifts_the_box_off_the_row(tmp_path):
    folder = _project(
        tmp_path, "measured",
        pipeline={"subtitle_position": {"caption_row": 0.71875}})
    box = si.placement_box(folder, 1080, 1920, caption_height=188)
    assert 1920 - box["insets"]["bottom"] == 1380 - 188
    assert "measured caption card" in box["basis"]


def test_the_caption_height_is_read_off_the_cards_own_measured_boxes():
    segments = [
        {"tight_box": {"width": 800, "height": 120}},
        {"tight_box": {"width": 820, "height": 188}},
        {"tight_box": None},
    ]
    assert si.measured_caption_height(segments) == 188


def test_a_run_with_no_measured_caption_box_says_so_rather_than_guessing():
    assert si.measured_caption_height([{"tight_box": None}]) is None
    assert si.measured_caption_height([]) is None


def test_the_box_never_reaches_below_the_strictest_safe_area(tmp_path):
    """No declared row: the engine's own caption row is the floor, and
    that already sits inside the platform's bottom inset."""
    folder = _project(tmp_path, "no-row")
    box = si.placement_box(folder, 1080, 1920, caption_height=None)
    assert box["insets"]["bottom"] >= 320
    assert box["insets"]["left"] == 90 and box["insets"]["right"] == 120


# ── What was DRAWN, not what was planned ─────────────────────────────

INSETS = {"top": 120, "right": 120, "bottom": 540, "left": 90}


def _measured(rows, cols, frame=(1080, 1920)):
    return {"frame": list(frame), "ink_pixels": 1000,
            "rows": list(rows), "cols": list(cols),
            "touches_frame_edge": []}


def test_ink_inside_the_box_passes():
    assert si.render_findings(
        _measured((1150, 1370), (90, 700)), INSETS, 1080, 1920) == []


def test_ink_on_the_caption_row_is_refused():
    findings = si.render_findings(
        _measured((1300, 1450), (90, 700)), INSETS, 1080, 1920)
    assert [f["code"] for f in findings] == [si.INK_LEFT_THE_BOX]
    assert findings[0]["severity"] == "error"


def test_ink_outside_the_safe_columns_is_refused():
    findings = si.render_findings(
        _measured((1150, 1370), (10, 1070)), INSETS, 1080, 1920)
    assert [f["code"] for f in findings] == [si.INK_LEFT_THE_BOX]


def test_a_render_with_no_ink_at_all_is_refused():
    findings = si.render_findings(
        {"frame": [1080, 1920], "rows": None, "cols": None},
        INSETS, 1080, 1920)
    assert [f["code"] for f in findings] == ["nothing_drawn"]


def test_a_measurement_on_a_small_canvas_is_not_read_as_frame_pixels():
    """A tight-boxed overlay's extents are not delivery-frame
    coordinates, and reading them as if they were is how a placement
    check becomes a confident wrong answer."""
    findings = si.render_findings(
        _measured((0, 120), (0, 400), frame=(420, 140)),
        INSETS, 1080, 1920)
    assert [f["severity"] for f in findings] == ["warning"]
    assert findings[0]["code"] == "not_the_delivery_frame"


def test_measured_box_is_left_top_right_bottom():
    assert si.measured_box(_measured((100, 200), (300, 400))) == (
        300, 100, 400, 200)


# ── The record a check grades against ────────────────────────────────

def test_the_record_carries_what_was_rendered_not_what_was_intended(tmp_path):
    folder = _project(tmp_path, "record",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel("Reel 07", _lines(("Ada", 1.0)), 40.0, folder)
    plan.segments = [{"overlay_path": "/x/lt_reel_07_00.mov",
                      "timeline_start": 1.0, "timeline_end": 4.0,
                      "total_frames": 72, "measured_box": (90, 1150, 700, 1370),
                      "elements": ["lower_third"]}]
    path = si.write_plans(folder, [plan])
    stored = json.loads(open(path, encoding="utf-8").read())
    assert stored["plans"][0]["reel"] == "Reel 07"
    assert stored["plans"][0]["segments"][0]["total_frames"] == 72
    assert si.plan_for(stored, "Reel 07")["basis"] == si.SPEAKERS_INTRODUCED
    assert si.plan_for(stored, "Reel 99") is None


def test_write_plans_merges_rather_than_overwriting(tmp_path):
    """A partial build must not delete the record of reels it never
    touched - the same discipline `explainer_plan.write_plans` keeps."""
    folder = _project(tmp_path, "merge",
                      effect={si.DECLARATION_KEY: DECLARATION})
    first = si.plan_for_reel("Reel 01", _lines(("Ada", 1.0)), 40.0, folder)
    si.write_plans(folder, [first])
    second = si.plan_for_reel("Reel 02", _lines(("Bram", 1.0)), 40.0, folder)
    path = si.write_plans(folder, [second])
    stored = json.loads(open(path, encoding="utf-8").read())
    assert {p["reel"] for p in stored["plans"]} == {"Reel 01", "Reel 02"}


def test_promotion_renames_the_staging_record_to_the_final_name(tmp_path):
    """A staged build records under `<final> (rebuild staging)`; the
    promotion must rename it, or `plan_for` answers None for a reel
    that really has a plan and F24 reports the graphics the build
    placed as items nothing accounts for.

    Measured 2026-09-12 on the first beside build of Reel 01 in the
    captain's project: two lower thirds placed on V7, F24 ERROR "2
    item(s) on the lower-third row (V7) and this reel has no recorded
    speaker lower-third plan at all". The three sibling records
    (`explainer_plan`, `reel_semantic_visual` twice) were renamed at
    promotion and this one was not.
    """
    folder = _project(tmp_path, "promote",
                      effect={si.DECLARATION_KEY: DECLARATION})
    staging = "Reel 07 (rebuild staging)"
    final = "Reel 07"
    si.write_plans(folder, [si.plan_for_reel(
        staging, _lines(("Ada", 1.0)), 40.0, folder)])
    assert si.plan_for(si.read_plans(folder), final) is None

    si.rename_plan_reels(folder, {staging: final})

    stored = si.read_plans(folder)
    assert {p["reel"] for p in stored["plans"]} == {final}
    assert si.plan_for(stored, final)["basis"] == si.SPEAKERS_INTRODUCED


def test_promotion_replaces_a_previous_build_under_the_final_name(tmp_path):
    """Two plans for one reel would leave `plan_for` reading the stale
    first - the same rule `explainer_plan.rename_plan_reels` states."""
    folder = _project(tmp_path, "replace",
                      effect={si.DECLARATION_KEY: DECLARATION})
    si.write_plans(folder, [si.plan_for_reel(
        "Reel 07", _lines(("Ada", 1.0)), 40.0, folder)])
    si.write_plans(folder, [si.plan_for_reel(
        "Reel 07 (rebuild staging)", _lines(("Bram", 2.0)), 40.0, folder)])

    si.rename_plan_reels(folder, {"Reel 07 (rebuild staging)": "Reel 07"})

    stored = si.read_plans(folder)
    assert [p["reel"] for p in stored["plans"]] == ["Reel 07"]
    speakers = [i["speaker"]
                for i in si.plan_for(stored, "Reel 07")["introductions"]]
    assert speakers == ["Bram"]


def test_a_refused_staging_leaves_no_lower_third_record(tmp_path):
    """The gate-fail half: no record may survive for a container that
    is about to be deleted."""
    folder = _project(tmp_path, "refused",
                      effect={si.DECLARATION_KEY: DECLARATION})
    si.write_plans(folder, [
        si.plan_for_reel("Reel 07 (rebuild staging)",
                         _lines(("Ada", 1.0)), 40.0, folder),
        si.plan_for_reel("Reel 08", _lines(("Bram", 1.0)), 40.0, folder)])

    si.drop_plan_reels(folder, ["Reel 07 (rebuild staging)"])

    assert {p["reel"] for p in si.read_plans(folder)["plans"]} == {"Reel 08"}


def test_rename_and_drop_are_no_ops_without_a_file(tmp_path):
    """No file yet is a build that recorded nothing, not an error."""
    folder = _project(tmp_path, "empty",
                      effect={si.DECLARATION_KEY: DECLARATION})
    si.rename_plan_reels(folder, {"a": "b"})
    si.drop_plan_reels(folder, ["a"])
    assert si.read_plans(folder) == {}


# ── What the rebuild decision sees ───────────────────────────────────

def test_a_lower_third_that_moved_changes_the_rebuild_digest():
    """The file is CONTENT-KEYED, so two reels naming the same speaker
    for the same length share one `segment_id`. The id alone therefore
    cannot tell a graphic that moved from one that did not - which is
    exactly what a first appearance shifting does - so the row carries
    `timeline_start` as well.

    `reel_rebuild_need.decide` is fail-closed; this is the half that
    feeds it, and a digest that cannot see a change is a reel that
    silently keeps the old graphic.
    """
    from library.tools import reel_build as rb
    from library.tools import reel_rebuild_need as need

    base = dict(
        reel_number=23, engine_code="eng", project_wide="proj",
        plan_content_hash="plan", transcript_hash="tr", master_digest="m",
        ranges=[(0.0, 10.0)], placements_list=[], cards=[],
        caption_segments=[], explainer_segments=[], semantic_segments=[],
        overlay_placements=None, motion_record=None, ending=None,
        look=None, grade_cdl=None, grade_look=None, power_grade=None)
    common = {"extra_cuts": [], "insisted": []}

    def digest(segments):
        extra = dict(common)
        if segments:
            extra["lower_thirds"] = rb._lower_third_rows(segments)
        return need.derivation_digest(**base, extra=extra)

    same_id = "mg_project_abc123"
    at_start = [{"segment_id": same_id, "timeline_start": 0.3,
                 "total_frames": 84,
                 "measured_box": (90, 1070, 506, 1191)}]
    moved = [{**at_start[0], "timeline_start": 5.9}]
    redrawn = [{**at_start[0], "measured_box": (90, 900, 506, 1021)}]

    assert digest(at_start) != digest(None)
    assert digest(moved) != digest(at_start), (
        "a lower third that MOVED digests the same as one that did not")
    assert digest(redrawn) != digest(at_start), (
        "a lower third whose ink landed elsewhere digests the same")


def test_a_project_with_no_lower_thirds_digests_exactly_as_before():
    """Absent means none. A project that declares no speakers must not
    re-place every reel it has just because this layer was added."""
    from library.tools import reel_rebuild_need as need

    base = dict(
        reel_number=1, engine_code="eng", project_wide="proj",
        plan_content_hash="plan", transcript_hash="tr", master_digest="m",
        ranges=[(0.0, 10.0)], placements_list=[], cards=[],
        caption_segments=[], explainer_segments=[], semantic_segments=[],
        overlay_placements=None, motion_record=None, ending=None,
        look=None, grade_cdl=None, grade_look=None, power_grade=None)
    common = {"extra_cuts": [], "insisted": []}
    segments = []
    without_the_key = need.derivation_digest(**base, extra=dict(common))
    as_the_build_computes_it = need.derivation_digest(
        **base, extra={**common,
                       **({"lower_thirds": []} if segments else {})})
    assert without_the_key == as_the_build_computes_it
