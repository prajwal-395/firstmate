"""Pin the series-shared brand library contract (AGENTS.md 14).

The gap: shared brand masters with no history, no undo and no
integrity record, while the declaration naming them IS versioned.
These tests pin the three halves - the store initialises as its own
repo, the manifest records what each file is, and drift is found -
plus the plan-time reader that keeps the manifest honest.  Every
project here is built under ``tmp_path``; no test reaches a real
store.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
import yaml
from library.processes.edit_video.run_pipeline import (
    gather_step_inputs, load_pipeline_state)
from library.tools.brand_registry import (
    no_brand_template, query_slots, resolve_template_reference)
from tests.brand_fixtures import SYNTHETIC_CINEMATIC, write_brand_json


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


from library.tools import brand_library as bl
from library.tools import full_frame_element as ffe

FPS_30 = 30.0


def _git(store, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(store), capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _store(tmp_path, files=None):
    """A series-shared store layout: brand-assets/motion/<files>."""
    motion = tmp_path / "brand-assets" / "motion"
    motion.mkdir(parents=True)
    for name, content in (files or {}).items():
        path = motion / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return tmp_path, motion


def _stub_measure(path):
    return {"width": 1080, "height": 1920, "fps": 30.0, "frames": 90,
            "duration_seconds": 3.0, "container": "mov",
            "video_codec": "prores", "has_audio": True}


def _clip(motion, name="logo.mov", seconds=1.0, fps=30):
    """A real movie file, because a clip is admitted on a MEASUREMENT."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    out = motion / name
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=red:s=1080x1920:r={fps}:d={seconds}",
         "-c:v", "prores_ks", "-profile:v", "3", str(out)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0 or not out.exists():
        pytest.skip("ffmpeg cannot encode a fixture here")
    return str(out)


# ── The store itself ───────────────────────────────────────────────

def test_a_directory_without_brand_assets_is_not_a_store(tmp_path):
    assert bl.is_shared_store(str(tmp_path)) is False
    with pytest.raises(bl.NotASharedStore):
        bl.init_shared_repo(str(tmp_path))
    with pytest.raises(bl.NotASharedStore):
        bl.write_manifest(str(tmp_path), measure=_stub_measure)


# ── The manifest ───────────────────────────────────────────────────

def test_write_manifest_records_integrity_and_measures_what_it_can(tmp_path):
    """Hash, size and measurement per file; where nothing can measure a
    file the hash still lands and the reason is recorded."""
    store, _ = _store(tmp_path, {"logo.mov": b"picture-bytes" * 64})
    report = bl.write_manifest(str(store), measure=_stub_measure)
    assert report["new"] == 1
    assert report["preserved"] == 0
    data = json.loads(Path(report["manifest"]).read_text(encoding="utf-8"))
    assert data["schema"] == bl.SCHEMA_VERSION
    entry = data["files"]["logo.mov"]
    assert entry["bytes"] == len(b"picture-bytes" * 64)
    assert len(entry["sha256"]) == 64
    assert entry["measured"]["frames"] == 90
    assert entry["role"] == "asset"

    store, _ = _store(tmp_path / "second", {"notes.md": "# review notes"})

    def refuse(path):
        raise ValueError("not a picture")

    report = bl.write_manifest(str(store), measure=refuse)
    data = json.loads(Path(report["manifest"]).read_text(encoding="utf-8"))
    entry = data["files"]["notes.md"]
    # Integrity first: the hash lands even where no measurement can.
    assert len(entry["sha256"]) == 64
    assert "measured" not in entry
    assert "not a picture" in entry["unmeasured"]


def test_refresh_preserves_curation_and_rehashes_bytes(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"first-bytes" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    manifest_file = motion / bl.MANIFEST_FILENAME
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["files"]["logo.mov"]["role"] = "conform"
    data["files"]["logo.mov"]["recipe"] = "minterpolate blend"
    data["files"]["logo.mov"]["note"] = "captain 2026-09-17"
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    (motion / "logo.mov").write_bytes(b"second-bytes" * 64)
    report = bl.write_manifest(str(store), measure=_stub_measure)
    assert report["preserved"] == 1
    assert report["new"] == 0
    entry = json.loads(manifest_file.read_text(encoding="utf-8"))
    entry = entry["files"]["logo.mov"]
    assert entry["role"] == "conform"
    assert entry["recipe"] == "minterpolate blend"
    assert entry["note"] == "captain 2026-09-17"
    assert entry["bytes"] == len(b"second-bytes" * 64)


def test_a_hand_edited_unknown_role_raises_not_defaults(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    manifest_file = motion / bl.MANIFEST_FILENAME
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["files"]["logo.mov"]["role"] = "masterpiece"
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(bl.UnknownRole):
        bl.write_manifest(str(store), measure=_stub_measure)


def test_write_manifest_records_which_declarations_point_at_it(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    project = tmp_path / "geo-podcast"
    project.mkdir()
    yaml_path = project / "project.yaml"
    yaml_path.write_text(
        f"asset: {motion / 'logo.mov'}\n", encoding="utf-8")
    elsewhere = tmp_path / "other.yaml"
    elsewhere.write_text("asset: /nowhere/nothing.mov\n", encoding="utf-8")
    missing = tmp_path / "gone.yaml"
    report = bl.write_manifest(
        str(store), measure=_stub_measure,
        project_yaml_paths=[str(yaml_path), str(elsewhere), str(missing)])
    assert report["unreadable_yaml"] == [str(missing)]
    data = json.loads(Path(report["manifest"]).read_text(encoding="utf-8"))
    assert data["files"]["logo.mov"]["declared_by"] == [str(yaml_path)]


def test_a_manifest_from_a_newer_writer_is_refused_not_rewritten(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    manifest_file = motion / bl.MANIFEST_FILENAME
    manifest_file.write_text(json.dumps({"schema": 999, "files": {}}),
                             encoding="utf-8")
    before = manifest_file.read_text(encoding="utf-8")
    with pytest.raises(bl.BrandLibraryError):
        bl.write_manifest(str(store), measure=_stub_measure)
    assert manifest_file.read_text(encoding="utf-8") == before


# ── Verification ───────────────────────────────────────────────────


def test_verify_finds_a_changed_file(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    (motion / "logo.mov").write_bytes(b"y" * 64)
    report = bl.verify_manifest(str(store))
    assert report["verified"] is False
    assert report["mismatches"][0]["kind"] == "changed"
    assert report["mismatches"][0]["path"] == "logo.mov"
    with pytest.raises(bl.ManifestDrift) as raised:
        bl.require_clean(report)
    assert "logo.mov" in str(raised.value)
    assert "changed" in str(raised.value)


def test_verify_reports_new_work_without_refusing_it(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    (motion / "bumper.mov").write_bytes(b"new" * 64)
    report = bl.verify_manifest(str(store))
    assert report["untracked"] == ["bumper.mov"]
    # Landing new work is not drift: the strict surface stays silent.
    assert report["verified"] is True
    assert bl.require_clean(report) is None


def test_verify_without_a_manifest_declines_by_name(tmp_path):
    store, _ = _store(tmp_path, {"logo.mov": b"x" * 64})
    report = bl.verify_manifest(str(store))
    assert report["verified"] is False
    assert report["reason"] == "no-manifest"
    with pytest.raises(bl.ManifestDrift):
        bl.require_clean(report)


# ── The plan-time reader ───────────────────────────────────────────


def test_an_unlisted_file_is_said_not_silent(tmp_path):
    """A generated conform with no integrity record is the exact file
    this task was opened for - the reader must not pass it quietly."""
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    (motion / "conform.mov").write_bytes(b"new" * 64)
    note = bl.library_note_for_asset(str(motion / "conform.mov"))
    assert "conform.mov" in note
    assert "no integrity record" in note


# ── The CLI ────────────────────────────────────────────────────────


def test_cli_verify_reports_drift_with_exit_3(tmp_path, capsys):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    assert bl.main(["--init", str(store)]) == 0
    capsys.readouterr()
    assert bl.main(["--write-manifest", str(store)]) == 0
    capsys.readouterr()
    (motion / "logo.mov").write_bytes(b"y" * 64)
    assert bl.main(["--verify", str(store)]) == 3
    err = capsys.readouterr().err
    assert "logo.mov" in err


# ── The plan-time reader, end to end ──────────────────────────────

def _plan(store, asset_path):
    declaration = {"element": "full_frame_clip", "placement": "tail",
                   "asset": asset_path, "reason": "captain marker"}
    cards = ffe.plan_reel_cards(
        ffe.declared_elements({"full_frame_elements": [declaration]}),
        facts=None, body_frames=0, fps=FPS_30,
        project_folder=str(store), width=1080, height=1920)
    assert len(cards) == 1
    return cards[0]


def test_a_planned_card_is_silent_with_no_store_behind_it(tmp_path, capsys):
    """Most clips: no manifest anywhere above them, so the reader
    costs one silent lookup and the card carries nothing."""
    motion = tmp_path / "footage"
    motion.mkdir()
    path = _clip(motion)
    card = _plan(tmp_path, path)
    assert card.library_note == ""
    assert "brand library" not in capsys.readouterr().err


def test_a_planned_card_says_a_moved_master(tmp_path, capsys):
    """The hazard this closes: the manifest says one thing, the disk
    another, and the reel would otherwise bake the move into Resolve.
    The lie is written into the manifest (not the movie) so the clip
    still measures and the card still plans - REPORTED, never a gate.
    """
    store, motion = _store(tmp_path)
    path = _clip(motion)
    bl.write_manifest(str(store))
    manifest_file = motion / bl.MANIFEST_FILENAME
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["files"]["logo.mov"]["sha256"] = "0" * 64
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    card = _plan(store, path)
    assert "logo.mov" in card.library_note
    assert "changed" in card.library_note
    assert card.duration_frames > 0, "the card still plans"
    assert "brand library" in capsys.readouterr().err


# --------------------------------------------------------------------------
# From test_brand_template_load.py
#
# The project's declared brand template must reach the steps that read it.
#
# A declaration nothing reads rendered with the in-code default and reported
# SUCCESS; these are the reader half plus proof the slots differ from the
# default. Every fixture is synthetic (`tests/brand_fixtures.py`). History:
# docs/RULE_EVIDENCE.md#brand-template-never-reached-the-run.

# The step that really declares brand_effect.  Discovered rather than
# spelled out, so a manifest rename fails here instead of quietly
# testing a step nothing routes brand slots to.
_PLAN_SUBTITLES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
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
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
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
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
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
