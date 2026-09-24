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
        reel_seconds=60.0, project_folder=folder, width=1080, height=1920)
    assert plan.entries == []
    assert plan.introductions == []
    assert plan.declared is False
    assert plan.basis == si.NOT_DECLARED


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
        "R", _lines(("Ada", 1.0), ("Zoe", 2.0)), 60.0, first, width=1080, height=1920)
    plan_two = si.plan_for_reel(
        "R", _lines(("Ada", 1.0), ("Zoe", 2.0)), 60.0, second, width=1080, height=1920)

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
        reel_seconds=45.0, project_folder=folder, width=1080, height=1920)
    speakers = [i.speaker for i in plan.introductions]
    assert speakers == ["Ada", "Bram"]
    assert len(plan.entries) == 2
    assert [e["start_seconds"] for e in plan.entries] == [0.5, 4.0]



# ── The colour: the project's, or nothing ────────────────────────────


def test_a_speaker_with_no_colour_anywhere_is_refused_not_invented(tmp_path):
    """There are no house looks. A colour the engine chose is a defect."""
    folder = _project(
        tmp_path, "colourless",
        effect={si.DECLARATION_KEY: {
            **DECLARATION,
            "speakers": {"Ada": {"name": "Ada Lovelace"}},
        }})
    plan = si.plan_for_reel("R", _lines(("Ada", 1.0)), 40.0, folder, width=1080, height=1920)
    assert plan.entries == []
    assert [r["reason"] for r in plan.refused] == [si.NO_COLOUR_DECLARED]



# ── A declaration that cannot be read is refused, never completed ────



def test_a_key_nothing_reads_raises():
    with pytest.raises(si.SpeakerIdentityError) as why:
        si.declared_speakers({**DECLARATION, "opacity": 0.5})
    assert "opacity" in str(why.value)


# ── The plan reaches the renderer's own resolver ─────────────────────

def test_the_entry_resolves_through_step_4_06s_own_resolver(tmp_path):
    """The plan this module writes must be a plan `resolve_plan` accepts
    - otherwise the graphic is dropped at the door and the drop reason
    is the first anyone hears of it."""
    from library.tools.motion_graphics_plan import resolve_plan

    folder = _project(tmp_path, "resolves",
                      effect={si.DECLARATION_KEY: DECLARATION})
    plan = si.plan_for_reel("R", _lines(("Ada", 2.0)), 40.0, folder, width=1080, height=1920)
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
        "R", _lines(("Ada", 0.0), ("Bram", 3.23)), 60.0, folder, width=1080, height=1920)
    assert [(e["start_seconds"], e["duration_seconds"])
            for e in plan.entries] == [(0.0, 3.23), (3.23, 3.5)]
    first = plan.entries[0]
    assert first["data"]["truncated_for_next"] == {
        "hold_seconds": 3.5, "duration_seconds": 3.23,
        "next_starts_at": 3.23}
    assert "Truncated to 3.23s from 3.5s" in first["why"]
    assert "truncated_for_next" not in plan.entries[1]["data"]
    assert [i.speaker for i in plan.introductions] == ["Ada", "Bram"]



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
        "R", _lines(("Ada", 0.0), ("Bram", 0.3)), 60.0, folder, width=1080, height=1920)
    assert [(e["start_seconds"], e["duration_seconds"])
            for e in plan.entries] == [(0.3, 3.5)]
    assert [i.speaker for i in plan.introductions] == ["Bram"]
    assert [(r["speaker"], r["reason"]) for r in plan.refused] == [
        ("Ada", si.TRUNCATED_BELOW_READABLE)]
    detail = plan.refused[0]["detail"]
    assert "0.3s" in detail and "3.5s" in detail
    assert str(MIN_CAPTION_DISPLAY_SECONDS) in detail
    assert si.TRUNCATED_BELOW_READABLE in si.REFUSALS



# ── Where it sits: measured, above the captions ──────────────────────



# ── What was DRAWN, not what was planned ─────────────────────────────

INSETS = {"top": 120, "right": 120, "bottom": 540, "left": 90}


def _measured(rows, cols, frame=(1080, 1920)):
    return {"frame": list(frame), "ink_pixels": 1000,
            "rows": list(rows), "cols": list(cols),
            "touches_frame_edge": []}


def test_ink_on_the_caption_row_is_refused():
    findings = si.render_findings(
        _measured((1300, 1450), (90, 700)), INSETS, 1080, 1920)
    assert [f["code"] for f in findings] == [si.INK_LEFT_THE_BOX]
    assert findings[0]["severity"] == "error"




def test_a_measurement_on_a_small_canvas_is_not_read_as_frame_pixels():
    """A tight-boxed overlay's extents are not delivery-frame
    coordinates, and reading them as if they were is how a placement
    check becomes a confident wrong answer."""
    findings = si.render_findings(
        _measured((0, 120), (0, 400), frame=(420, 140)),
        INSETS, 1080, 1920)
    assert [f["severity"] for f in findings] == ["warning"]
    assert findings[0]["code"] == "not_the_delivery_frame"


# ── The record a check grades against ────────────────────────────────


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
        staging, _lines(("Ada", 1.0)), 40.0, folder, width=1080, height=1920)])
    assert si.plan_for(si.read_plans(folder), final) is None

    si.rename_plan_reels(folder, {staging: final})

    stored = si.read_plans(folder)
    assert {p["reel"] for p in stored["plans"]} == {final}
    assert si.plan_for(stored, final)["basis"] == si.SPEAKERS_INTRODUCED


def test_a_refused_staging_leaves_no_lower_third_record(tmp_path):
    """The gate-fail half: no record may survive for a container that
    is about to be deleted."""
    folder = _project(tmp_path, "refused",
                      effect={si.DECLARATION_KEY: DECLARATION})
    si.write_plans(folder, [
        si.plan_for_reel("Reel 07 (rebuild staging)",
                         _lines(("Ada", 1.0)), 40.0, folder, width=1080, height=1920),
        si.plan_for_reel("Reel 08", _lines(("Bram", 1.0)), 40.0, folder, width=1080, height=1920)])

    si.drop_plan_reels(folder, ["Reel 07 (rebuild staging)"])

    assert {p["reel"] for p in si.read_plans(folder)["plans"]} == {"Reel 08"}


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
