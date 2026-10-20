"""The identity chain a `ren revise` edit travels: folder -> Resolve project
-> request -> broker -> executor.

Each test here names a defect that made the chain break. The chain was
measured end to end on the Ren Scratch - breaktest project (a copy of one
reel out of the captain's Podcast (field test) project) on 2026-10-20:
every step below failed on the way, and each failure refused the edit
before any timeline write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from library.tools import (
    edit_qualification_policy,
    feedback_patch,
    project_registry,
    resolve_axi_core,
    revise,
    timeline_shadow,
)

REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"


def _project(tmp_path: Path, *, resolve_name: str = "Podcast (field test)",
             slug: str = "scratch-project") -> Path:
    project = tmp_path / slug
    project.mkdir(parents=True)
    (project / "project.yaml").write_text(
        "name: Scratch\n"
        f"slug: {slug}\n"
        "source:\n  fps: 23.976\n"
        "pipeline:\n  creative_brief: brief.md\n"
        "resolve:\n"
        f"  project_name: {resolve_name}\n"
        "  database: local\n"
        "  folder: Lucie Content/Social Media/Podcast\n"
        f"  timeline_name: {REEL}\n",
        encoding="utf-8")
    return project


# ── 1. The request names the RESOLVE project, not the project folder ──
#
# The broker's open-project check compares the request's project with the
# name of the open Resolve project, and the shadow store is keyed by that
# name too. A request that named the Ren project FOLDER was refused as
# "Resolve has 'Podcast (field test)' open, not '/Users/.../geo-podcast'".


def test_the_resolve_project_name_is_read_from_the_project_yaml(tmp_path):
    project = _project(tmp_path)
    assert revise._resolve_project_name(str(project)) == "Podcast (field test)"


def test_the_registry_resolves_a_project_folder_to_its_resolve_name(tmp_path):
    project = _project(tmp_path)
    assert project_registry.resolve_project_name(str(project)) == (
        "Podcast (field test)")


def test_the_registry_finds_the_project_folder_behind_a_resolve_name(tmp_path,
                                                                     monkeypatch):
    """Catches: the touch-up executor unable to find the Ren project
    folder, because the request names the Resolve project and the
    registry only knew slugs."""
    project = _project(tmp_path, resolve_name="Ren Scratch - breaktest")
    monkeypatch.setattr(project_registry, "PROJECTS_ROOT", tmp_path)
    found = project_registry.project_folder_for_resolve_name(
        "Ren Scratch - breaktest")
    assert found is not None
    assert Path(found) == project


# ── 2. The recorded base is found for the request that names it ────────
#
# The shadow store records a generation under the RESOLVE project name.
# Looking the base up by the request's own project string found nothing
# for every revise request, so no edit could be prepared.


def test_the_recorded_base_is_found_for_a_request_naming_a_project_folder(
        tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    store = timeline_shadow.ShadowStore()
    store.record(project="Podcast (field test)", timeline_id="t1",
                 timeline_name=REEL, snapshot={"tracks": [], "fps": 23.976},
                 source=timeline_shadow.OBSERVED, expected_head=0)
    assert resolve_axi_core._shadow_project_key(str(project)) == (
        "Podcast (field test)")
    assert resolve_axi_core._shadow_project_key("Podcast (field test)") == (
        "Podcast (field test)")


def test_a_request_naming_the_resolve_project_finds_its_recorded_base(
        tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    store = timeline_shadow.ShadowStore()
    store.record(project="Podcast (field test)", timeline_id="t1",
                 timeline_name=REEL, snapshot={"tracks": [], "fps": 23.976},
                 source=timeline_shadow.OBSERVED, expected_head=0)
    from library.tools.edit_request import from_edit_program

    request = from_edit_program(
        {"ops": [{"op": "clip.delete", "params": {"unique_id": "clip-1"}}],
         "reel": REEL, "base_generation": "1",
         "idempotency_key": "t1"},
        project=revise._resolve_project_name(str(project)), timeline=REEL)
    with pytest.raises(Exception) as raised:
        resolve_axi_core._prepare_request_plan(request)
    assert "resolved to 0 timelines" not in str(raised.value)


# ── 3. The front door and the AXI planner share ONE policy ─────────────
#
# `resolve_axi_core` grants an unqualified operation a protected
# experimental route for `ren revise` and `ren touch`. The front door's
# classifier asked for the production policy and refused those same
# operations first, so the grant was unreachable and only the three
# production-enabled operations could ever be edited.


def test_the_protected_policy_has_one_owner():
    """Catches: the covered operation set written down twice, in the
    planner and in the front door, and the two copies disagreeing."""
    context = edit_qualification_policy.protected_experimental_context(
        ["overlay.resize"], originator_name="ren revise")
    assert context is not None
    assert context["qualification_policy"] == "protected_experimental"
    for name in edit_qualification_policy.PROTECTED_EXPERIMENTAL_GUARANTEES:
        assert context[name] is True


def test_the_policy_is_not_granted_to_another_originator():
    assert edit_qualification_policy.protected_experimental_context(
        ["overlay.resize"], originator_name="somebody else") is None


def test_the_policy_is_not_granted_to_an_uncovered_operation():
    assert edit_qualification_policy.protected_experimental_context(
        ["clip.move"], originator_name="ren revise") is None
    assert edit_qualification_policy.protected_experimental_context(
        ["overlay.resize", "audio.gain"], originator_name="ren revise") is None


def _program_note(ops: list, originator: str = "") -> dict:
    parameters = {
        "overlay.resize": {"unique_id": "clip-1", "width": 121, "height": 104},
        "audio.gain": {"unique_id": "clip-1", "db": -4},
        "clip.delete": {"unique_id": "clip-1"},
        "clip.set_property": {"unique_id": "clip-1", "key": "Pan", "value": 1.0},
    }
    evidence = {"edit_spec_ops": [
        {"op": op, "owner": None, "ledger_op": "edit_program",
         **parameters.get(op, {})}
        for op in ops]}
    if originator:
        evidence["originator"] = originator
    return {"basis": "edit_spec", "outcome": "unrouted", "steps": [],
            "frame": None, "evidence": evidence}


def test_the_front_door_reaches_the_operations_the_planner_would_run():
    """Catches: the front door's classifier refusing an operation the AXI
    planner grants to `ren revise`, so the grant was unreachable and the
    whole edit surface closed behind the production-enabled operations."""
    verdict = feedback_patch.decide(
        _program_note(["overlay.resize"], originator="ren revise"))
    assert verdict["verdict"] == feedback_patch.VERDICT_PATCH
    assert verdict["execution"]["backend"] == "reel_touchup"


def test_a_marker_note_keeps_the_production_policy():
    """Catches: the protected route widened to notes that do not own the
    staging guarantee."""
    verdict = feedback_patch.decide(_program_note(["overlay.resize"]))
    assert verdict["verdict"] == feedback_patch.VERDICT_REFUSED
    assert "overlay.resize" in verdict["reason"]


def test_the_planner_and_the_front_door_agree_on_the_policy():
    """Catches: the planner reading its own copy of the covered operation
    set, so the front door and the planner could disagree about what
    `ren revise` may run."""
    from types import SimpleNamespace

    request = SimpleNamespace(
        originator={"kind": "human", "name": "ren revise"})
    planner = resolve_axi_core._protected_route_context(
        request, ("overlay.resize",))
    front_door = edit_qualification_policy.protected_experimental_context(
        ["overlay.resize"], originator_name="ren revise")
    assert planner == front_door
    assert planner["qualification_policy"] == "protected_experimental"
