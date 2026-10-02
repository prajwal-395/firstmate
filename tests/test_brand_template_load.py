"""The project's declared brand template must reach the steps that read it.

A declaration nothing reads rendered with the in-code default and reported
SUCCESS; these are the reader half plus proof the slots differ from the
default. Every fixture is synthetic (`tests/brand_fixtures.py`). History:
docs/RULE_EVIDENCE.md#brand-template-never-reached-the-run.
"""
import json
import os

import pytest
import yaml

from library.processes.edit_video.run_pipeline import (
    gather_step_inputs, load_pipeline_state)
from library.tools.brand_registry import (
    no_brand_template, query_slots, resolve_template_reference)
from tests.brand_fixtures import SYNTHETIC_CINEMATIC, write_brand_json

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


# ── the declaration reaches the run ─────────────────────────────────


def test_an_existing_declaration_in_state_is_not_overwritten(tmp_path):
    """State already written by an earlier run wins - the same rule
    project_folder and sfx_library follow."""
    folder = _project(tmp_path, "geo", "synthetic_cinematic")
    (tmp_path / "geo" / "pipeline_data.json").write_text(
        json.dumps({"brand_template": "synthetic_shortform"}), encoding="utf-8")
    state = load_pipeline_state(folder)
    assert state["brand_template"] == "synthetic_shortform"


# ── and it changes what the step is handed ──────────────────────────

def test_declared_template_supplies_the_effect_slots(tmp_path):
    """The end of the wire: a project whose brand.json carries the
    cinematic shape gets those effect slots, not the in-code default's.

    Against the unfixed loader `state` has no `brand_template` at all and
    both sides of this assertion are `no_brand_template()`.
    """
    folder = _project(tmp_path, "geo", "synthetic_cinematic")
    write_brand_json(folder, "synthetic_cinematic")
    state = load_pipeline_state(folder)
    manifest = _manifest_declaring_brand_effect()

    inputs = gather_step_inputs(
        "step_4_01_plan_subtitles", {"edges": []}, state, manifest)

    declared = query_slots(
        resolve_template_reference(
            "synthetic_cinematic", project_folder=folder), "effect")
    in_code_default = query_slots(no_brand_template(), "effect")

    assert inputs["brand_effect"] == declared
    assert inputs["brand_effect"] != in_code_default
    # Named so the failure says WHICH slot, not just "dicts differ".
    assert inputs["brand_effect"]["vfx_intensity"] == 0.3
    assert in_code_default["vfx_intensity"] == 0.0
    assert inputs["brand_effect"]["subtitle_style"] == "minimal"
    assert inputs["brand_effect"]["transition_types"] == [
        "hard_cut", "match_cut", "fade_to_black", "defocus"]


def test_a_brand_json_satisfies_a_name_nothing_else_could(tmp_path):
    """The product ships no templates, so a name resolves ONLY through
    the project's own copy - and through nothing when it has none."""
    folder = _project(tmp_path, "geo", "synthetic_cinematic")
    with pytest.raises(FileNotFoundError):
        resolve_template_reference(
            "synthetic_cinematic", templates_dir=str(tmp_path / "empty"),
            project_folder=folder)
    write_brand_json(folder, "synthetic_cinematic")
    resolved = resolve_template_reference(
        "synthetic_cinematic", templates_dir=str(tmp_path / "empty"),
        project_folder=folder)
    assert query_slots(resolved, "effect")["subtitle_style"] == "minimal"


def test_a_project_declaring_none_inherits_no_taste(tmp_path):
    """The whole point of `no_brand_template()`.

    This used to assert `inputs["brand_effect"]["transition_types"]` was
    non-empty, and it was - because a project that had chosen no brand
    was handed a fallback template's seven types, its 200-500 ms
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
    assert inputs["brand_style"]["series_look"] is None
    assert inputs["brand_style"]["energy_profile"] == ""
    assert inputs["brand_style"]["typography"] == {}
    # The one exception, and it is recorded as one.
    assert effect["caption_case"] == "lowercase"


def test_every_brand_slot_has_a_reachable_pipeline_reader_or_no_reader():
    """A reader row must resolve to a pipeline node and its delivery route."""
    from dataclasses import fields

    from library.schemas.brand_template import (
        ContentSlots, EffectSlots, StyleSlots)
    from library.tools.brand_registry import (
        ABSENT_SLOT_READINGS, BRAND_SLOT_READERS)
    from library.tools.processes import every_dag, load_manifests
    from library.tools.template_loader import BRAND_CONSTRAINT_STEPS

    slot_keys = {
        f"{group}.{field.name}"
        for group, cls in (("style", StyleSlots), ("effect", EffectSlots),
                           ("content", ContentSlots))
        for field in fields(cls)
    }
    assert set(BRAND_SLOT_READERS) <= slot_keys
    assert slot_keys <= set(ABSENT_SLOT_READINGS)

    manifests_by_node = {}
    step_paths_by_node = {}
    library_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "library")
    for dag in every_dag().values():
        manifests_by_node.update(load_manifests(dag))
        for node in dag.get("nodes", []):
            step_paths_by_node[node["id"]] = os.path.join(
                library_root, node["step_ref"], "step.py")

    for slot in sorted(slot_keys):
        reading = ABSENT_SLOT_READINGS[slot]
        reader = BRAND_SLOT_READERS.get(slot)
        if reader is None:
            assert reading.startswith("NO READER."), (
                f"{slot} has no reachable pipeline consumer. Mark it "
                "NO READER in ABSENT_SLOT_READINGS or add a reachable "
                "reader to BRAND_SLOT_READERS.")
            continue

        assert not reading.startswith("NO READER."), (
            f"{slot} has a reader route but its absence is recorded as "
            "NO READER")
        node_id, route = reader
        assert node_id in manifests_by_node, (
            f"{slot} names {node_id!r}, which is not in any process DAG")

        if route == "brand_constraints":
            assert node_id in BRAND_CONSTRAINT_STEPS, (
                f"{slot} names {node_id!r} as a prompt reader, but it is "
                "not in TemplateLoader.BRAND_CONSTRAINT_STEPS")
        elif route == "project_template":
            assert node_id == "compile_manifest", (
                f"{slot} uses the direct project-template route, which is "
                "owned by compile_manifest")
            with open(step_paths_by_node[node_id], encoding="utf-8") as f:
                source = f.read()
            assert (
                "resolve_project_template(" in source
                and "template=_template" in source
            ), (
                f"{slot} names compile_manifest, but its direct project "
                "template reader is no longer present")
        else:
            group = slot.split(".", 1)[0]
            expected_input = {
                "style": "brand_style",
                "effect": "brand_effect",
                "content": "brand_content",
            }[group]
            assert route in (expected_input, "brand_template"), (
                f"{slot} cannot reach {node_id!r} through {route!r}")
            declared_inputs = {
                inp.get("name") for inp in
                manifests_by_node[node_id].get("interface", {}).get(
                    "inputs", [])
            }
            assert route in declared_inputs, (
                f"{slot} names {node_id!r}, but its manifest does not "
                f"declare {route!r}")


# ── a template nobody has raises, rather than rendering a default ───

def test_a_typo_in_the_declaration_raises(tmp_path):
    folder = _project(tmp_path, "typo", "synthetic_cinematc")
    state = load_pipeline_state(folder)
    with pytest.raises(FileNotFoundError):
        gather_step_inputs("step_4_01_plan_subtitles", {"edges": []}, state,
                           _manifest_declaring_brand_effect())


# ── step 5.01 asked for the WHOLE template, and got nothing ─────────

_COLOR_GRADE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "library", "steps", "step_5_01_color_grade", "manifest.json")


def test_brand_template_is_handed_resolved_and_only_on_declaration(tmp_path):
    """step_5_01_color_grade does `brand_template.get("style", {})` and
    reads `style.series_look` off it.  The key was never set, so the look
    a template declares never reached the grade - `main()` fell through
    to the neutral CDL on every run of every project.

    It needs a DICT, so this also pins the type: broadcasting the
    reference string under the same key would crash the step. And it is
    not broadcast: a step whose manifest does not ask gets no slots.

    `series_look` is a DECLARATION rather than a name into a catalogue,
    and the synthetic template declares none, so the route is proved
    with the palette the copy does carry: it reaches the step as a
    value rather than as an absence.
    """
    with open(_COLOR_GRADE, encoding="utf-8") as f:
        manifest = json.load(f)
    declared = {i.get("name") for i in
                manifest.get("interface", {}).get("inputs", [])}
    assert "brand_template" in declared

    folder = _project(tmp_path, "geo", "synthetic_cinematic")
    write_brand_json(folder, "synthetic_cinematic")
    state = load_pipeline_state(folder)
    inputs = gather_step_inputs(
        "step_5_01_color_grade", {"edges": []}, state, manifest)

    template = inputs["brand_template"]
    assert isinstance(template, dict), "step 5.01 calls .get() on this"
    assert template["style"]["series_look"] is None
    assert "color_palette" in template["style"], "the copy still arrives"

    inputs = gather_step_inputs(
        "step_2_03_broll_selection", {"edges": []}, state,
        {"interface": {"inputs": [{"name": "catalog"}]}})
    assert "brand_template" not in inputs
    assert "brand_effect" not in inputs


def test_project_brand_json_wins_over_templates_dir(tmp_path):
    """`resolve_project_template` (compile_manifest's direct route) reads
    the project's own brand.json whatever name it is asked for."""
    from library.tools.brand_registry import resolve_project_template
    (tmp_path / "brand.json").write_text(
        json.dumps(SYNTHETIC_CINEMATIC), encoding="utf-8")
    bt = resolve_project_template(
        "any_name_at_all", templates_dir=str(tmp_path),
        project_folder=str(tmp_path))
    assert query_slots(bt, "effect")["subtitle_style"] == "minimal"
