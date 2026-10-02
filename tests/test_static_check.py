"""The static check phase holds, and each of its sections can FAIL.

`library/tools/static_check.py` runs the source-text policies together -
home paths, direct Resolve access, sys.path discipline, Ruling 1, bridge
code markers - so the one-policy-per-file scanners retire onto one
`problems() == []` assertion.  Every section plants its violation
deliberately, because a gate never seen refusing reads as coverage
(AGENTS.md 10.4).
"""

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

from library.tools import capabilities, operations, static_check


def test_the_tree_is_clean():
    assert static_check.problems() == []


def test_an_invalid_creative_policy_is_named(monkeypatch):
    monkeypatch.setitem(
        capabilities.CREATIVE_POLICIES, "broll.resolve", "engine_picks_count"
    )
    found = capabilities.problems()
    assert any("unknown creative policy 'engine_picks_count'" in p for p in found), (
        found
    )


def test_a_home_path_in_code_is_named(tmp_path):
    mod = tmp_path / "m.py"
    literal = "/Users" + "/someone/.cache/vep"
    mod.write_text(f"CACHE = {literal!r}\n", encoding="utf-8")
    found = static_check.home_paths(roots=[tmp_path])
    assert any("m.py:1" in p and "absolute home path" in p for p in found), found


def test_a_home_path_in_prose_is_not_a_path(tmp_path):
    mod = tmp_path / "m.py"
    prose = '"""Destinations under ' + "/Users" + '/Shared were tried."""\n'
    mod.write_text(prose, encoding="utf-8")
    assert static_check.home_paths(roots=[tmp_path]) == []


def test_a_projects_root_attribute_read_is_named(tmp_path):
    mod = tmp_path / "test_example.py"
    mod.write_text("root = paths.PROJECTS_ROOT\n", encoding="utf-8")
    assert static_check.project_root_reads(files=[mod]) == [
        "test_example.py:1: .PROJECTS_ROOT"
    ]


def test_an_imported_projects_root_is_named(tmp_path):
    mod = tmp_path / "test_example.py"
    mod.write_text(
        "from library.tools.paths import PROJECTS_ROOT\n", encoding="utf-8"
    )
    assert "PROJECTS_ROOT" in static_check.project_root_reads(files=[mod])[0]


def test_a_direct_scriptapp_call_is_named(tmp_path):
    mod = tmp_path / "m.py"
    mod.write_text("resolve = dvr.scriptapp('Resolve')\n", encoding="utf-8")
    found = static_check.resolve_access(roots=[tmp_path])
    assert any("m.py:1" in p and "scriptapp_preserving_locale" in p for p in found), (
        found
    )


def test_a_bare_repo_local_import_is_named(tmp_path):
    mod = tmp_path / "test_x.py"
    mod.write_text("import step\n", encoding="utf-8")
    found = static_check.syspath(files=[mod])
    assert any("bare `import step`" in p for p in found), found


def test_an_import_time_path_beyond_the_root_is_named(tmp_path):
    mod = tmp_path / "test_x.py"
    mod.write_text("import sys\nsys.path.insert(0, '/tmp')\n", encoding="utf-8")
    found = static_check.syspath(files=[mod])
    assert any(
        "test_x.py" in p and "may be added at import time" in p for p in found
    ), found


def test_path_checker_does_not_execute_test_source(tmp_path):
    marker = tmp_path / "executed"
    mod = tmp_path / "test_x.py"
    payload = (
        "import sys\n"
        f"sys.path.insert(0, open({str(marker)!r}, 'w').write('bad'))\n"
    )
    mod.write_text(payload, encoding="utf-8")
    found = static_check.syspath(files=[mod])
    assert any("unresolvable sys.path expression" in p for p in found), found
    assert not marker.exists()


@dataclass(frozen=True)
class _Planted:
    name: str
    owning_dir: str
    run: Callable
    is_prompt: bool = False
    body: str = "step.py"
    attr: str = "run"


def test_the_rule_refuses_a_tool_the_owning_step_does_not_import():
    from library.tools.music_bed import resolve_bed

    planted = _Planted("planted.bed", "step_4_01_plan_subtitles", resolve_bed)
    assert "does not import" in (static_check.operation_violation(planted) or "")


def test_the_rule_refuses_reaching_into_another_steps_body():
    from library.steps.step_5_03_creative_cohesion.step import (
        review_creative_cohesion,
    )

    planted = _Planted(
        "planted.cohesion", "step_4_01_plan_subtitles", review_creative_cohesion
    )
    assert "owned by" in (static_check.operation_violation(planted) or "")


def test_the_rule_refuses_a_callable_the_registry_defines_itself():
    planted = _Planted("planted.wrapped", "step_4_01_plan_subtitles", lambda **kw: None)
    assert "neither library/steps/" in (static_check.operation_violation(planted) or "")


def test_the_rule_refuses_a_prompt_no_manifest_declares():
    planted = operations.Operation(
        name="planted.prompt",
        summary="a prompt nobody declared",
        owning_dir="step_1_01_scan_project",
        body="handoff.md",
        attr="",
        produces=(),
        consumes=(),
    )
    assert "handoff.md" in (static_check.operation_violation(planted) or "")


def test_the_rule_accepts_the_two_legal_shapes():
    from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
    from library.tools.safe_area import resolve_safe_area

    assert (
        static_check.operation_violation(
            _Planted("legal.own", "step_4_01_plan_subtitles", generate_subtitles)
        )
        is None
    )
    assert (
        static_check.operation_violation(
            _Planted("legal.tool", "step_4_01_plan_subtitles", resolve_safe_area)
        )
        is None
    )


def test_a_tagged_capability_scans_its_bridge(tmp_path):
    repo = tmp_path
    marker = repo / "library" / "steps" / "step_9_99_probe"
    marker.mkdir(parents=True)
    (marker / "bridge.py").write_text(
        'MESSAGE = "recommended is 3"\n', encoding="utf-8"
    )
    spec = SimpleNamespace(
        creative_policy=capabilities.MODEL_DECIDES_QUANTITY,
        executor=SimpleNamespace(path="library/steps/step_9_99_probe/bridge.py"),
    )
    found = static_check.creative_code_markers(registry=[spec], repo_root=repo)
    assert any("plan-size recommendation" in p and "bridge.py:1" in p for p in found), found


def test_a_capability_without_the_creative_policy_is_not_scanned(tmp_path):
    repo = tmp_path
    marker = repo / "library" / "steps" / "step_9_99_probe"
    marker.mkdir(parents=True)
    (marker / "bridge.py").write_text(
        'MESSAGE = "recommended is 3"\n', encoding="utf-8"
    )
    spec = SimpleNamespace(
        creative_policy=None,
        executor=SimpleNamespace(path="library/steps/step_9_99_probe/bridge.py"),
    )
    assert static_check.creative_code_markers(registry=[spec], repo_root=repo) == []


def test_a_density_scaler_helper_is_named_outside_a_bridge(tmp_path):
    marker = tmp_path / "library" / "tools" / "audio_reactive_sfx.py"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        "def scale_sfx_density(plan):\n    return plan\n", encoding="utf-8"
    )
    found = static_check.creative_code_markers(registry=[], repo_root=tmp_path)
    assert any("scale_sfx_density" in p for p in found), found


def test_a_removed_creative_helper_is_named_outside_a_bridge(tmp_path):
    helper = tmp_path / "library" / "tools" / "audio_reactive_sfx.py"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        "def align_sfx_to_prosody(plan):\n    return plan\n", encoding="utf-8"
    )
    found = static_check.creative_code_markers(registry=[], repo_root=tmp_path)
    assert any("align_sfx_to_prosody" in p for p in found), found
