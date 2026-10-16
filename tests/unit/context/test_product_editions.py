"""The product edition is baked once and gates packaged/runtime components."""

import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ren import edition

RESTRICTED = {
    "model.audio_flamingo_next",
    "model.insightface_buffalo_l",
    "renderer.remotion",
}

PUBLIC_OPTIONAL = {
    "python.praat_parselmouth",
}

ENGINE_COMPONENTS = {
    "filmcraft": "engine.filmcraft_cli",
    "effectcraft": "engine.effectcraft_cli",
}

ENGINE_UI_PACKAGES = {
    "filmcraft",
    "effectcraft",
    "filmcraft-web",
    "effectcraft-web",
    "filmcraft-ui-egui",
    "effectcraft-ui-egui",
    "egui",
    "eframe",
    "winit",
    "rfd",
    "cpal",
    "muda",
}


def test_each_inventory_component_has_one_stable_edition_tag():
    text = (ROOT / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
    components = edition.read_components(ROOT)

    assert len(re.findall(r"^component\s*:", text, re.MULTILINE)) == len(components)
    assert set(components) == {entry.id for entry in components.values()}
    assert {key for key, entry in components.items()
            if entry.edition == edition.PERSONAL_ONLY} == RESTRICTED
    assert {key for key, entry in components.items()
            if entry.edition == edition.PUBLIC_OPTIONAL} == PUBLIC_OPTIONAL
    assert all(entry.edition in {edition.PUBLIC, edition.PERSONAL_ONLY,
                                 edition.PUBLIC_OPTIONAL}
               for entry in components.values())


def test_unbuilt_checkout_defaults_to_personal(monkeypatch):
    monkeypatch.delenv(edition.EDITION_ENV, raising=False)
    assert edition.current_edition(ROOT) == edition.PERSONAL
    assert edition.component_allowed("model.insightface_buffalo_l", root=ROOT)


def test_public_selection_never_uses_personal_provider(monkeypatch):
    monkeypatch.setenv(edition.EDITION_ENV, edition.PUBLIC)

    assert not edition.component_allowed(
        "model.insightface_buffalo_l", root=ROOT)
    with pytest.raises(edition.ComponentRefused, match="cannot fetch personal-only"):
        edition.require_component(
            "model.audio_flamingo_next", action="fetch", root=ROOT)
    assert edition.select_component(
        "graphics rendering",
        personal_component="renderer.remotion",
        public_component="renderer.hyperframes",
        root=ROOT) == "renderer.hyperframes"
    with pytest.raises(edition.ComponentUnavailable, match="face identity"):
        edition.select_component(
            "face identity",
            personal_component="model.insightface_buffalo_l",
            public_component=None,
            root=ROOT)


def test_built_edition_cannot_be_overridden(tmp_path, monkeypatch):
    engine = tmp_path / "engine"
    (engine / "ren").mkdir(parents=True)
    (engine / "ren" / "_build.py").write_text(
        "BUILD_EDITION = 'public'\n", encoding="utf-8")
    monkeypatch.setenv(edition.EDITION_ENV, edition.PERSONAL)

    with pytest.raises(edition.EditionError, match="cannot change this public"):
        edition.current_edition(engine)


def test_public_package_filter_excludes_nested_personal_path(tmp_path):
    from ren.package_engine import _ignore

    source = tmp_path / "source"
    ignored = _ignore(
        str(source / "library"),
        ["personal_weights", "public_tool.py"],
        source_root=source,
        blocked=frozenset({"library/personal_weights"}),
    )

    assert "personal_weights" in ignored
    assert "public_tool.py" not in ignored


def _active_requirement_lines(path: Path) -> list[str]:
    return [line.split("#", 1)[0].strip() for line in
            path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def test_public_build_excludes_personal_only_components(
        tmp_path, monkeypatch, packageable_native_runtime):
    monkeypatch.delenv(edition.EDITION_ENV, raising=False)
    from ren.package_engine import build_engine_tree

    built = build_engine_tree(
        ROOT, tmp_path / "public", channel="test", sha="abc123",
        built_at="2026-10-04", edition=edition.PUBLIC)

    assert edition.current_edition(built) == edition.PUBLIC
    assert not (built / "remotion-subtitles").exists()
    assert not (built / "scripts" / "install_node_deps.sh").exists()
    assert not (built / "scripts" / "install_insightface.sh").exists()
    assert (built / "hyperframes" / "compositions" / "SubtitleOverlay.html").is_file()
    assert (built / "hyperframes" / "compositions" / "Montserrat-Variable.ttf").is_file()
    assert (built / "hyperframes" / "compositions" / "OFL-Montserrat.txt").is_file()

    for requirement_file in (
            "requirements/analysis.txt",
            "requirements/lock/macos-arm64-py312.txt"):
        assert not any(
            re.match(r"praat[-_.]parselmouth(?:[<>=!~;\[]|$)", line,
                     flags=re.IGNORECASE)
            for line in _active_requirement_lines(built / requirement_file))
    assert not edition.component_allowed(
        "renderer.remotion", root=built)
    assert not edition.component_allowed(
        "model.insightface_buffalo_l", root=built)
    assert edition.component_allowed(
        "python.praat_parselmouth", root=built)
    assert not edition.component_allowed(
        "model.audio_flamingo_next", root=built)


def test_public_build_refuses_restricted_model_artifact_in_package_path(
        tmp_path, monkeypatch):
    from ren.package_engine import build_engine_tree

    source = tmp_path / "source"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "target", ".pytest_cache", "node_modules"))
    monkeypatch.delenv(edition.EDITION_ENV, raising=False)
    with (source / "THIRD_PARTY_NOTICES").open("a", encoding="utf-8") as handle:
        handle.write("\npackage-path : restricted-checkpoint.bin\n")
    # Add the path to the restricted component's existing inventory record.
    text = (source / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
    text = text.replace("component-id : model.audio_flamingo_next\n",
                        "component-id : model.audio_flamingo_next\n"
                        "package-path : restricted-checkpoint.bin\n")
    (source / "THIRD_PARTY_NOTICES").write_text(text, encoding="utf-8")
    (source / "restricted-checkpoint.bin").write_bytes(b"restricted")
    with pytest.raises(RuntimeError, match="restricted model artifact"):
        build_engine_tree(
            source, tmp_path / "public-restricted", channel="test", sha="abc123",
            built_at="2026-10-16", edition=edition.PUBLIC)


def test_build_emits_derived_model_manifest_and_notices(
        tmp_path, monkeypatch, packageable_native_runtime):
    from ren.package_engine import build_engine_tree

    monkeypatch.delenv(edition.EDITION_ENV, raising=False)
    built = build_engine_tree(
        ROOT, tmp_path / "personal-model-policy", channel="test", sha="abc123",
        built_at="2026-10-16", edition=edition.PERSONAL)
    manifest = json.loads((built / "library/model_registry/installed.manifest.json").read_text())
    notices = (built / "MODEL_NOTICES.md").read_text(encoding="utf-8")
    assert manifest["edition"] == "personal"
    assert manifest["purpose"] == "commercial_client"
    assert "PANNs Cnn14 DecisionLevelMax" in notices
    assert "Audio Flamingo Next" not in notices


def test_versioned_staging_carries_the_requested_edition(tmp_path, monkeypatch):
    from ren import package_engine

    built = {}

    def record_build(src, dest, **kwargs):
        built.update(kwargs)
        (dest / "ren").mkdir(parents=True)

    monkeypatch.setattr(package_engine, "build_engine_tree", record_build)
    staged = package_engine.stage_versioned(
        tmp_path / "vep", ROOT, channel="test", sha="abc123",
        built_at="2026-10-04", edition=edition.PUBLIC)

    assert built["edition"] == edition.PUBLIC
    assert ".public." in staged.name


def test_personal_build_keeps_current_renderer_and_components(
        tmp_path, monkeypatch, packageable_native_runtime):
    monkeypatch.delenv(edition.EDITION_ENV, raising=False)
    from ren.package_engine import build_engine_tree

    built = build_engine_tree(
        ROOT, tmp_path / "personal", channel="test", sha="abc123",
        built_at="2026-10-04", edition=edition.PERSONAL)

    assert edition.current_edition(built) == edition.PERSONAL
    assert (built / "remotion-subtitles" / "package-lock.json").is_file()
    assert (built / "scripts" / "install_node_deps.sh").is_file()
    assert (built / "scripts" / "install_insightface.sh").is_file()
    assert "praat-parselmouth" in "\n".join(
        _active_requirement_lines(built / "requirements/analysis.txt"))
    assert edition.component_allowed("renderer.remotion", root=built)
    assert edition.component_allowed(
        "model.insightface_buffalo_l", root=built)


@pytest.mark.parametrize("script, component", [
    ("install_insightface.sh", "model.insightface_buffalo_l"),
    ("install_node_deps.sh", "renderer.remotion"),
])
def test_personal_component_installers_refuse_in_public_mode(
        script, component, monkeypatch):
    env = dict(os.environ)
    env[edition.EDITION_ENV] = edition.PUBLIC
    env["REN_ENGINE_ROOT"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT)
    done = subprocess.run(
        ["bash", str(ROOT / "scripts" / script), "--check"],
        cwd=ROOT, env=env, capture_output=True, encoding="utf-8", check=False)

    assert done.returncode != 0
    assert f"personal-only component {component}" in done.stderr


def test_runtime_loaders_refuse_personal_models_in_public(monkeypatch):
    monkeypatch.setenv(edition.EDITION_ENV, edition.PUBLIC)
    from library.tools.analysis import sfx_pipeline, speech_advanced_pipeline
    from library.tools.person_entity import FaceIdentityUnavailable, _face_app

    with pytest.raises(FaceIdentityUnavailable, match="public edition"):
        _face_app()
    assert edition.component_allowed(
        "python.praat_parselmouth", root=ROOT)
    assert edition.select_component(
        "SFX profiling",
        personal_component="model.audio_flamingo_next",
        public_component="model.laion_clap",
        root=ROOT) == "model.laion_clap"


def test_public_graphics_select_fframes_and_refuse_remotion(monkeypatch):
    from library.tools import graphics_renderer

    monkeypatch.setenv(edition.EDITION_ENV, edition.PUBLIC)
    monkeypatch.delenv(graphics_renderer.USER_SETTING_KEY, raising=False)
    # The FFrames default needs a resolvable binary; stub the probe so
    # this pins the selection, not the machine it runs on.
    monkeypatch.setattr(graphics_renderer, "_fframes_binary",
                        lambda: "/fake/ren-graphics-fframes")
    assert graphics_renderer.resolve_engine() == graphics_renderer.ENGINE_FFRAMES

    monkeypatch.setenv(graphics_renderer.USER_SETTING_KEY,
                       graphics_renderer.ENGINE_REMOTION)
    with pytest.raises(edition.ComponentRefused, match="renderer.remotion"):
        graphics_renderer.resolve_engine()


def test_doctor_reports_the_product_edition(monkeypatch):
    from ren import doctor

    monkeypatch.setenv(edition.EDITION_ENV, edition.PUBLIC)
    check = doctor.edition_check()
    assert check.ok
    assert check.detail.startswith("public;")
    assert "model routes" in check.detail
    assert "unqualified=" in check.detail


def _driver_pin(crate_dir: str, constant: str) -> str:
    text = (ROOT / "native" / crate_dir / "src" / "lib.rs").read_text(
        encoding="utf-8")
    match = re.search(rf'{constant}\s*:\s*&str\s*=\s*"([^"]+)"', text)
    assert match, f"{crate_dir}/src/lib.rs must pin {constant}"
    return match.group(1)


def test_engine_integrations_are_inventoried_public():
    from library.tools import engine_edition

    components = edition.read_components(ROOT)
    text = (ROOT / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
    manifest = json.loads(
        (ROOT / "library" / "tools" / "upstream_engines.json").read_text(
            encoding="utf-8"))

    assert set(engine_edition.ENGINE_COMPONENTS.values()) == set(
        ENGINE_COMPONENTS.values())
    for engine, component_id in ENGINE_COMPONENTS.items():
        entry = components[component_id]
        assert entry.edition == edition.PUBLIC
        pin = _driver_pin(
            f"ren-{engine}",
            "FILMCRAFT_REVISION" if engine == "filmcraft"
            else "PINNED_EFFECTCRAFT_REV")
        assert len(pin) == 40
        assert manifest["engines"][engine]["qualified_sha"] == pin
        block = re.search(
            rf"component-id\s*:\s*{re.escape(component_id)}\b(.*?)(?="
            r"^component-id\s*:|\Z)", text, re.MULTILINE | re.DOTALL)
        assert block and "commercial  : ok" in block.group(1)


def test_engine_edition_seam_gates_unknown_engine(monkeypatch):
    from library.tools import engine_edition

    for requested in (edition.PERSONAL, edition.PUBLIC):
        monkeypatch.setenv(edition.EDITION_ENV, requested)
        for engine, component_id in ENGINE_COMPONENTS.items():
            assert engine_edition.require_engine(
                engine, action="load").id == component_id
            assert engine_edition.require_engine(
                engine, action="fetch").id == component_id

    with pytest.raises(ValueError, match="unknown engine"):
        engine_edition.component_id("no-such-engine")
    with pytest.raises(ValueError, match="unknown engine"):
        engine_edition.require_engine("no-such-engine")
    with pytest.raises(ValueError, match="unknown engine"):
        engine_edition.intake_packages("no-such-engine")


def test_intake_builds_only_headless_packages():
    from library.tools import engine_edition
    from library.tools.local_ci import upstream

    for engine, packages in engine_edition.INTAKE_PACKAGES.items():
        assert packages[0] in ("filmcraft-cli", "effectcraft-cli")
        assert not set(packages) & set(
            engine_edition.UI_PACKAGES[engine]), (
            f"{engine} intake must never build a UI package")

    source = inspect.getsource(upstream._qualify_engine)
    assert "engine_edition.intake_packages" in source
    assert '"filmcraft-cli" if name' not in source
    assert "filmcraft-web" not in source
    assert "effectcraft-web" not in source


def _ren_lock_names(root: Path) -> set[str]:
    lock = tomllib.loads(
        (root / "native" / "Cargo.lock").read_text(encoding="utf-8"))
    return {package["name"] for package in lock["package"]}


def test_public_build_ships_engines_without_ui_or_personal_assets(
        tmp_path, monkeypatch, packageable_native_runtime):
    from library.tools import engine_edition

    monkeypatch.delenv(edition.EDITION_ENV, raising=False)
    from ren.package_engine import build_engine_tree

    built = build_engine_tree(
        ROOT, tmp_path / "public-engines", channel="test", sha="abc123",
        built_at="2026-10-10", edition=edition.PUBLIC)

    assert edition.current_edition(built) == edition.PUBLIC
    # Both adapters and the shared protocol ship byte-identical in the
    # public build: one model and adapter protocol in both editions.
    for relative in (
            "native/ren-filmcraft/src/lib.rs",
            "native/ren-filmcraft/src/adapter.rs",
            "native/ren-effectcraft/src/lib.rs",
            "library/tools/engine_edition.py",
            "library/tools/project_model_effectcraft.py"):
        assert (built / relative).read_bytes() == (ROOT / relative).read_bytes()
    for component_id in ENGINE_COMPONENTS.values():
        assert edition.component_allowed(component_id, root=built)
    assert set(engine_edition.INTAKE_PACKAGES) == set(ENGINE_COMPONENTS)

    # No engine UI crate is reachable from Ren's own workspace: Ren shells
    # out to the headless CLI binaries and links no upstream engine crate.
    assert not (_ren_lock_names(built) & ENGINE_UI_PACKAGES)
    assert not {name for name in _ren_lock_names(built)
                if name in ("filmcraft-cli", "effectcraft-cli")}
    members = tomllib.loads(
        (built / "native" / "Cargo.toml").read_text(encoding="utf-8"))
    assert not (set(members["workspace"]["members"]) & ENGINE_UI_PACKAGES)

    # No desktop UI, web bundle, brand mark, or personal-only asset ships.
    assert not (built / "docs" / "brand").exists()
    marked = [path for path in built.rglob("*")
              if "artcraft" in path.name.lower()
              or "brand" in path.parts and "docs" in path.parts]
    assert marked == []
    for component_id in RESTRICTED:
        assert not edition.component_allowed(component_id, root=built)


def test_edition_delta_is_exactly_the_inventory(
        tmp_path, monkeypatch, packageable_native_runtime):
    from ren.package_engine import (
        COPY_DIRS,
        _edition_policy,
        build_engine_tree,
    )

    blocked_paths, blocked_installers, _, _ = _edition_policy(ROOT, "public")
    assert blocked_paths and blocked_installers
    for relative in blocked_paths | blocked_installers:
        assert (ROOT / relative).exists(), (
            f"inventory blocks {relative} but it is absent from the source")

    monkeypatch.delenv(edition.EDITION_ENV, raising=False)
    built = build_engine_tree(
        ROOT, tmp_path / "public-delta", channel="test", sha="abc123",
        built_at="2026-10-10", edition=edition.PUBLIC)

    for relative in blocked_paths:
        assert not (built / relative).exists()
    for entry in COPY_DIRS:
        if entry in blocked_paths or any(
                entry.startswith(f"{path}/") for path in blocked_paths):
            continue
        assert (built / entry).exists(), (
            f"{entry} is not inventoried personal-only but is missing "
            "from the public build")


def test_personal_build_keeps_engine_adapters(monkeypatch):
    monkeypatch.delenv(edition.EDITION_ENV, raising=False)
    from library.tools import engine_edition

    for engine, component_id in ENGINE_COMPONENTS.items():
        assert edition.component_allowed(component_id, root=ROOT)
        assert engine_edition.require_engine(engine).id == component_id
