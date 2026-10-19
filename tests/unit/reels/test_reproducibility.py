"""VC-39 portable identity and preflight recovery checks."""

from library.tools.versions import reproducibility


def test_portable_identity_does_not_depend_on_checkout_path(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "relocated"
    first.mkdir()
    second.mkdir()

    identity = reproducibility.project_identity(first)
    (second / reproducibility.IDENTITY_REL).parent.mkdir(parents=True)
    (second / reproducibility.IDENTITY_REL).write_bytes(
        (first / reproducibility.IDENTITY_REL).read_bytes())

    assert reproducibility.project_identity(second) == identity
    assert str(first) not in str(identity)


def test_asset_reference_is_content_addressed_and_keeps_source_path(tmp_path):
    source = tmp_path / "title.ttf"
    source.write_bytes(b"font bytes")

    first = reproducibility.logical_asset(source, kind="font")
    source.rename(tmp_path / "renamed.ttf")
    second = reproducibility.logical_asset(tmp_path / "renamed.ttf", kind="font")

    assert first["asset_ref"] == second["asset_ref"]
    assert first["original_path"] != second["original_path"]


def test_recovery_assessment_distinguishes_native_and_semantic(tmp_path,
                                                                 monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    media = project / "media.mov"
    archive = project / "reel.drt"
    font = project / "brand.ttf"
    for path, content in ((media, b"media"), (archive, b"archive"),
                          (font, b"font")):
        path.write_bytes(content)
    environment = {"renderers": {"remotion": "renderer-1"}}
    monkeypatch.setattr(reproducibility, "environment_fingerprint",
                        lambda _root: environment)
    evidence = reproducibility.recovery_evidence(
        project, "Reel 01",
        {"archive_path": str(archive),
         "assets": [{"path": str(media)}]},
        resolve_version="21.1",
        required_assets=[{"path": str(font), "kind": "font"}],
    )
    font.unlink()

    result = reproducibility.assess_recovery(
        evidence, project_folder=project,
        current_environment=environment, current_resolve_version="21.1")

    assert result["exact_native"]["restorable"] is False
    assert result["semantic_reconstruction"]["available"] is False
    assert result["semantic_reconstruction"]["missing_assets"] == [
        evidence["assets"][1]["asset_ref"]]
    assert evidence["asset_requirements"]["font"]


def test_renderer_and_resolve_mismatch_refuse_exact_recovery(tmp_path,
                                                                monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    archive = project / "reel.drt"
    archive.write_bytes(b"archive")
    monkeypatch.setattr(reproducibility, "environment_fingerprint",
                        lambda _root: {"renderers": {"remotion": "old"}})
    evidence = reproducibility.recovery_evidence(
        project, "Reel 01", {"archive_path": str(archive), "assets": []},
        resolve_version="21.1")

    result = reproducibility.assess_recovery(
        evidence, project_folder=project,
        current_environment={"renderers": {"remotion": "new"}},
        current_resolve_version="21.2")

    assert result["exact_native"]["restorable"] is False
    assert result["exact_native"]["resolve_version_mismatch"] is True
    assert result["semantic_reconstruction"]["available"] is False
