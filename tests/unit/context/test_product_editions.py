"""The product edition is baked once and gates packaged/runtime components."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ren import edition


RESTRICTED = {
    "model.audio_flamingo_next",
    "model.insightface_buffalo_l",
    "python.praat_parselmouth",
    "renderer.remotion",
}


def test_each_inventory_component_has_one_stable_edition_tag():
    text = (ROOT / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
    components = edition.read_components(ROOT)

    assert len(re.findall(r"^component\s*:", text, re.MULTILINE)) == len(components)
    assert set(components) == {entry.id for entry in components.values()}
    assert {key for key, entry in components.items()
            if entry.edition == edition.PERSONAL_ONLY} == RESTRICTED
    assert all(entry.edition in {edition.PUBLIC, edition.PERSONAL_ONLY}
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


def test_public_build_excludes_personal_only_components(tmp_path, monkeypatch):
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
    assert not edition.component_allowed(
        "python.praat_parselmouth", root=built)
    assert not edition.component_allowed(
        "model.audio_flamingo_next", root=built)


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


def test_personal_build_keeps_current_renderer_and_components(tmp_path, monkeypatch):
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
    from library.tools.analysis import speech_advanced_pipeline, sfx_pipeline
    from library.tools.person_entity import FaceIdentityUnavailable, _face_app

    with pytest.raises(FaceIdentityUnavailable, match="public edition"):
        _face_app()
    with pytest.raises(speech_advanced_pipeline.ProsodyUnavailable,
                       match="public edition"):
        speech_advanced_pipeline.analyze_prosody("no-audio-read")
    with pytest.raises(RuntimeError, match="public edition"):
        sfx_pipeline.load_afnext_model()


def test_public_graphics_select_hyperframes_and_refuse_remotion(monkeypatch):
    from library.tools import graphics_renderer

    monkeypatch.setenv(edition.EDITION_ENV, edition.PUBLIC)
    monkeypatch.delenv(graphics_renderer.USER_SETTING_KEY, raising=False)
    assert graphics_renderer.resolve_engine() == graphics_renderer.ENGINE_HYPERFRAMES

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
