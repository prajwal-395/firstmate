"""The project's declared brand template must reach the steps that read it.

`state["brand_template"]` was populated from exactly one source - a
`default` on the process manifest's `brand_template` input, which has
none - so `gather_step_inputs` called `load_brand_template("")` for every
step of every project and handed back the in-code `_get_default_template()`.
A project.yaml naming `cinematic_narrative`, `lucie_client` or anything
else rendered with none of that template's effect slots, and the run
reported SUCCESS.  Nothing errored, nothing warned.

Same defect class as the timed-text slot with no reader (CLAUDE.md
section 14): a declaration nothing reads.  These tests are the reader
half plus the assertion that the slots actually differ from the default -
without the second half, "a reader exists" is the empty claim
`smart_reframe` made for months.
"""
import json
import os
import textwrap

import pytest
import yaml

from library.processes.edit_video.run_pipeline import (
    gather_step_inputs, load_pipeline_state)
from library.tools.brand_registry import (
    DEFAULT_TEMPLATE_NAME, no_brand_template, project_template_name,
    query_slots, reference_template_name, resolve_template_reference)

TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "library", "templates")

# The step that really declares brand_effect.  Discovered rather than
# spelled out, so a manifest rename fails here instead of quietly
# testing a step nothing routes brand slots to.
_PLAN_SUBTITLES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "library", "steps", "step_4_01_plan_subtitles", "manifest.json")


def _manifest_declaring_brand_effect():
    with open(_PLAN_SUBTITLES, encoding="utf-8") as f:
        manifest = json.load(f)
    declared = {i.get("name") for i in
                manifest.get("interface", {}).get("inputs", [])}
    assert "brand_effect" in declared, (
        "step_4_01_plan_subtitles no longer declares brand_effect; "
        "point this test at whichever step does")
    return manifest


def _project(tmp_path, name, template_name):
    folder = tmp_path / name
    folder.mkdir()
    cfg = {"name": name, "slug": name, "pipeline": {}}
    if template_name is not None:
        cfg["pipeline"]["brand_template"] = template_name
    (folder / "project.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(folder)


# ── the declaration is read off project.yaml ────────────────────────

def test_project_template_name_reads_the_declaration(tmp_path):
    folder = _project(tmp_path, "geo", "cinematic_narrative")
    assert project_template_name(folder) == "cinematic_narrative"


def test_project_template_name_is_empty_when_none_declared(tmp_path):
    folder = _project(tmp_path, "bare", None)
    assert project_template_name(folder) == ""


def test_project_template_name_survives_a_missing_project_yaml(tmp_path):
    assert project_template_name(str(tmp_path)) == ""


# ── the declaration reaches the run ─────────────────────────────────

def test_load_pipeline_state_carries_the_declared_template(tmp_path):
    """The line that was missing.  Fails against the unfixed loader."""
    folder = _project(tmp_path, "geo", "cinematic_narrative")
    state = load_pipeline_state(folder)
    assert state["brand_template"] == "cinematic_narrative"


def test_a_project_declaring_none_stays_declaring_none(tmp_path):
    folder = _project(tmp_path, "bare", None)
    state = load_pipeline_state(folder)
    assert "brand_template" not in state


def test_an_existing_declaration_in_state_is_not_overwritten(tmp_path):
    """State already written by an earlier run wins - the same rule
    project_folder and sfx_library follow."""
    folder = _project(tmp_path, "geo", "cinematic_narrative")
    (tmp_path / "geo" / "pipeline_data.json").write_text(
        json.dumps({"brand_template": "shortform_energetic"}), encoding="utf-8")
    state = load_pipeline_state(folder)
    assert state["brand_template"] == "shortform_energetic"


# ── and it changes what the step is handed ──────────────────────────

def test_declared_template_supplies_the_effect_slots(tmp_path):
    """The end of the wire: a project naming cinematic_narrative gets
    cinematic_narrative's effect slots, not the in-code default's.

    Against the unfixed loader `state` has no `brand_template` at all and
    both sides of this assertion are `no_brand_template()`.
    """
    folder = _project(tmp_path, "geo", "cinematic_narrative")
    state = load_pipeline_state(folder)
    manifest = _manifest_declaring_brand_effect()

    inputs = gather_step_inputs(
        "step_4_01_plan_subtitles", {"edges": []}, state, manifest)

    declared = query_slots(
        resolve_template_reference("cinematic_narrative",
                                   templates_dir=TEMPLATES_DIR), "effect")
    in_code_default = query_slots(no_brand_template(), "effect")

    assert inputs["brand_effect"] == declared
    assert inputs["brand_effect"] != in_code_default
    # Named so the failure says WHICH slot, not just "dicts differ".
    assert inputs["brand_effect"]["vfx_intensity"] == 0.3
    assert in_code_default["vfx_intensity"] == 0.0
    assert inputs["brand_effect"]["subtitle_style"] == "minimal"
    assert inputs["brand_effect"]["transition_types"] == [
        "hard_cut", "match_cut", "fade_to_black", "defocus"]


def test_a_project_declaring_none_inherits_no_taste(tmp_path):
    """The whole point of `no_brand_template()`.

    This used to assert `inputs["brand_effect"]["transition_types"]` was
    non-empty, and it was - because a project that had chosen no brand
    was handed `default_brand.yaml`'s seven types, its 200-500 ms
    transition range and its 0.5 VFX intensity.  A project that declares
    nothing now gets nothing, and every consumer's reading of an absent
    slot is recorded in `ABSENT_SLOT_READINGS`.
    """
    folder = _project(tmp_path, "bare", None)
    state = load_pipeline_state(folder)
    inputs = gather_step_inputs(
        "step_4_01_plan_subtitles", {"edges": []}, state,
        _manifest_declaring_brand_effect())
    effect = inputs["brand_effect"]
    assert effect["transition_types"] == []
    assert effect["transition_duration_ms"] == {}
    assert effect["vfx_intensity"] == 0.0
    assert effect["subtitle_style"] == ""
    assert inputs["brand_style"]["house_look"] is None
    assert inputs["brand_style"]["energy_profile"] == ""
    assert inputs["brand_style"]["typography"] == {}
    # The one exception, and it is recorded as one.
    assert effect["caption_case"] == "lowercase"


def test_the_absent_reading_of_every_slot_is_written_down():
    """A slot whose absence nobody has stated is a silent default again."""
    from library.tools.brand_registry import ABSENT_SLOT_READINGS
    from dataclasses import fields
    from library.schemas.brand_template import (
        ContentSlots, EffectSlots, StyleSlots)
    for group, cls in (("style", StyleSlots), ("effect", EffectSlots),
                       ("content", ContentSlots)):
        for f in fields(cls):
            if f.name in ("reference_look_image", "watermark",
                          "motion_accents", "motion_progress_bar",
                          "series_title", "channel_name"):
                continue
            key = f"{group}.{f.name}"
            assert key in ABSENT_SLOT_READINGS, (
                f"{key} has no recorded reading of absence. Say what a "
                f"project with no brand template gets for it, in "
                f"library/tools/brand_registry.ABSENT_SLOT_READINGS.")


def test_default_brand_is_still_loadable_by_name(tmp_path):
    """It stops being the fallback; it does not stop being a template.

    A project that NAMES it has chosen its values, which is what makes
    them a brand decision rather than one nobody made.
    """
    folder = _project(tmp_path, "chose", DEFAULT_TEMPLATE_NAME)
    state = load_pipeline_state(folder)
    inputs = gather_step_inputs(
        "step_4_01_plan_subtitles", {"edges": []}, state,
        _manifest_declaring_brand_effect())
    assert inputs["brand_effect"]["transition_duration_ms"] == {
        "min": 200, "max": 500}
    assert inputs["brand_style"]["energy_profile"] == "high"


# ── a template nobody has raises, rather than rendering a default ───

def test_a_typo_in_the_declaration_raises(tmp_path):
    folder = _project(tmp_path, "typo", "cinematic_narative")
    state = load_pipeline_state(folder)
    with pytest.raises(FileNotFoundError):
        gather_step_inputs("step_4_01_plan_subtitles", {"edges": []}, state,
                           _manifest_declaring_brand_effect())


def test_a_missing_path_raises_as_a_path(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        resolve_template_reference(str(tmp_path / "nope.yaml"))
    assert "nope.yaml" in str(exc.value)


# ── both spellings of the reference resolve ─────────────────────────

def test_a_path_reference_still_loads(tmp_path):
    path = os.path.join(TEMPLATES_DIR, "cinematic_narrative.yaml")
    by_path = resolve_template_reference(path)
    by_name = resolve_template_reference("cinematic_narrative",
                                         templates_dir=TEMPLATES_DIR)
    assert query_slots(by_path, "effect") == query_slots(by_name, "effect")


def test_reference_name_is_the_name_half_of_either_form():
    path = os.path.join(TEMPLATES_DIR, "cinematic_narrative.yaml")
    assert reference_template_name(path) == "cinematic_narrative"
    assert reference_template_name("cinematic_narrative") == "cinematic_narrative"
    # "" is NOT `default_brand`.  Answering `default_brand` here is what
    # sent every template-less project the fallback's brand constraints.
    assert reference_template_name("") == ""


# ── step 5.01 asked for the WHOLE template, and got nothing ─────────

_COLOR_GRADE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "library", "steps", "step_5_01_color_grade", "manifest.json")


def test_a_step_declaring_brand_template_gets_the_resolved_template(tmp_path):
    """step_5_01_color_grade does `brand_template.get("style", {})` and
    reads `style.house_look` off it.  The key was never set, so the look
    a template declares never reached the grade - `main()` fell through
    to the neutral CDL on every run of every project.

    It needs a DICT, so this also pins the type: broadcasting the
    reference string under the same key would crash the step.

    `house_look` is now a DECLARATION rather than a name into a catalogue
    (the catalogue was removed - see tests/test_house_look.py), and no
    shipped template declares one, so the route is proved with a
    declaration this test writes into the project's own template.
    """
    with open(_COLOR_GRADE, encoding="utf-8") as f:
        manifest = json.load(f)
    declared = {i.get("name") for i in
                manifest.get("interface", {}).get("inputs", [])}
    assert "brand_template" in declared

    folder = _project(tmp_path, "geo", "cinematic_narrative")
    state = load_pipeline_state(folder)
    inputs = gather_step_inputs(
        "step_5_01_color_grade", {"edges": []}, state, manifest)

    template = inputs["brand_template"]
    assert isinstance(template, dict), "step 5.01 calls .get() on this"
    # The shipped template declares no look, and that is the point: it
    # reaches the step as an absence rather than as a substitute.
    assert template["style"]["house_look"] is None
    assert "color_palette" in template["style"], "the template still arrives"


def test_the_grade_actually_reads_a_declared_look(tmp_path):
    """The delivery half: a look the template DECLARES changes the CDL
    the grade emits, not just the dict handed to the step."""
    from library.steps.step_5_01_color_grade.step import define_color_grade

    declaration = {
        "name": "geo_declaration",
        "cdl": {"slope": [1.045, 1.01, 0.935],
                "offset": [0.016, 0.008, -0.004],
                "power": [0.985, 0.995, 1.025],
                "saturation": 1.08},
        "vignette": {"blend": 0.2, "soft": 0.4},
    }

    shot_list = {"entries": [{"track": "V1", "clip_id": "c1",
                              "entry_id": "e1", "source_file": "f1.mov"}]}
    with_look = define_color_grade(shot_list, project_folder=str(tmp_path),
                                   house_look=declaration)
    without = define_color_grade(shot_list, project_folder=str(tmp_path),
                                 house_look=None)
    graded, neutral = with_look["color_grade_spec"], without["color_grade_spec"]
    assert graded["house_look"] == "geo_declaration"
    assert neutral["house_look"] is None
    # The two halves the look is delivered in (AGENTS.md section 12).
    # Neutral is the identity CDL and an empty Fusion block: literally no
    # grade, which is what every project with no declaration gets.
    assert graded["per_clip_adjustments"][0]["cdl_values"]["slope_b"] == 0.935
    assert neutral["per_clip_adjustments"][0]["cdl_values"]["slope_b"] == 1.0
    assert graded["fusion_look"]["vignette"] is True
    assert neutral["fusion_look"] == {}


def test_a_step_that_does_not_declare_it_is_handed_no_template(tmp_path):
    """It is not broadcast.  A step gets brand slots because its manifest
    asked - the same rule sfx_library follows."""
    folder = _project(tmp_path, "geo", "cinematic_narrative")
    state = load_pipeline_state(folder)
    inputs = gather_step_inputs(
        "step_2_03_broll_selection", {"edges": []}, state,
        {"interface": {"inputs": [{"name": "catalog"}]}})
    assert "brand_template" not in inputs
    assert "brand_effect" not in inputs
