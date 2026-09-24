"""Project intake: language, shape and speakers are project settings.

The defects these pin, one per group:

- temporal_index forced English two literals deep, so a non-English
  project could never be transcribed as what it speaks. `language`
  is now a `source:` setting the step reads.
- Reel selection refused anything under two voices, so a monologue
  or a music project was unrepresentable. The COUNT now comes from
  `source.speakers`, with undeclared reading as the historical two.
- `source.shape` (speech/music/both/picture-led) had nowhere to be
  declared at all; intake scaffolds it.

`[]` speakers (declared zero) and missing speakers (undeclared) must
round-trip as different answers - that distinction is the whole
zero-speaker project.
"""

from __future__ import annotations

import pytest

from library.schemas.project_config import (
    ProjectConfig,
    SourceConfig,
    _dict_to_project_config,
    load_project_config,
    project_config_to_dict,
)
from library.tools import footage_identity as fi
from library.tools.project_registry import create_project


def _config(**source_kwargs) -> ProjectConfig:
    return ProjectConfig(name="N", slug="s",
                         source=SourceConfig(**source_kwargs))


# ── language ─────────────────────────────────────────────────────

def test_language_defaults_to_english():
    assert SourceConfig().language == "en"
    assert _config().validate() == []


def test_declared_language_is_normalised():
    assert _dict_to_project_config(
        {"source": {"language": "ES"}}).source.language == "es"


@pytest.mark.parametrize("bad", ["e", "english!", "", 5, ["en"]])
def test_malformed_language_is_refused_by_name(bad):
    errors = _config(language=bad).validate()
    assert any("source.language" in e for e in errors), errors


def test_language_round_trips_when_non_default():
    config = _config(language="es")
    assert (project_config_to_dict(config)["source"]["language"]
            == "es")
    assert (_dict_to_project_config(
        project_config_to_dict(config)).source.language == "es")


def test_default_language_is_not_written():
    """`language: en` in every project.yaml would read as a decision
    nobody made, and behaves identically to absent."""
    assert "language" not in project_config_to_dict(_config())["source"]


def test_declared_language_reads_off_the_project_folder(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    assert fi.declared_language(str(project)) == "en"
    (project / "project.yaml").write_text("source:\n  language: pt-BR\n")
    assert fi.declared_language(str(project)) == "pt-br"
    (project / "project.yaml").write_text("source:\n  language: 5\n")
    assert fi.declared_language(str(project)) == "en"


# ── shape ────────────────────────────────────────────────────────

@pytest.mark.parametrize("shape", ["speech", "music", "both",
                                   "picture-led", ""])
def test_known_shapes_validate(shape):
    assert _config(shape=shape).validate() == []


def test_unknown_shape_is_refused_by_name():
    errors = _config(shape="podcast").validate()
    assert any("source.shape" in e for e in errors), errors


def test_shape_round_trips():
    config = _config(shape="both")
    assert (project_config_to_dict(config)["source"]["shape"]
            == "both")


# ── speakers ─────────────────────────────────────────────────────

def test_speakers_default_to_undeclared():
    assert SourceConfig().speakers is None
    assert _config().validate() == []


def test_declared_zero_and_undeclared_stay_distinct():
    """The load-bearing distinction: `[]` is a music/montage project,
    None is one that never said."""
    zero = _dict_to_project_config({"source": {"speakers": []}})
    missing = _dict_to_project_config({"source": {}})
    assert zero.source.speakers == []
    assert missing.source.speakers is None
    assert (project_config_to_dict(zero)["source"]["speakers"] == [])
    assert ("speakers" not in
            project_config_to_dict(missing)["source"])


def test_speaker_names_and_roles_round_trip():
    roster = [{"name": "Craig", "role": "host"}, {"name": "Akshita"}]
    config = _config(speakers=roster)
    assert config.validate() == []
    assert (project_config_to_dict(config)["source"]["speakers"]
            == roster)


@pytest.mark.parametrize("bad, fragment", [
    ("Craig", "source.speakers must be a list"),
    ([{"title": "host"}], "names no speaker"),
    ([{"name": "  "}], "names no speaker"),
    ([{"name": "Craig", "role": 5}], "must be a string"),
    ([{"name": "Craig", "agent": "x"}], "which nothing reads"),
])
def test_malformed_speakers_are_refused_by_name(bad, fragment):
    errors = _config(speakers=bad).validate()
    assert any("source.speakers" in e and fragment in e
               for e in errors), errors


def test_declared_speakers_read_off_the_project_folder(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    assert fi.declared_speakers(str(project)) is None
    (project / "project.yaml").write_text("source:\n  speakers: []\n")
    assert fi.declared_speakers(str(project)) == []
    (project / "project.yaml").write_text(
        "source:\n  speakers:\n    - name: Craig\n      role: host\n"
        "    - {title: nameless}\n    - just-a-string\n")
    assert fi.declared_speakers(str(project)) == [
        {"name": "Craig", "role": "host"}]
    assert fi.expected_speaker_count(str(project)) == 1


def test_expected_count_defaults_to_none_when_undeclared(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    assert fi.expected_speaker_count(str(project)) is None
    assert fi.expected_speaker_count(declaration=None) is None
    assert fi.expected_speaker_count(declaration=[]) == 0
    assert fi.expected_speaker_count(
        declaration=[{"name": "Jo"}]) == 1


# ── scaffolding ──────────────────────────────────────────────────

def test_new_scaffolds_what_intake_collects(tmp_path):
    """`new` writes the four intake files and the declarations that
    drive them; a project that declares nothing gets starters that
    change nothing. The defect: intake answers with nowhere to land."""
    from library.tools.brand_registry import resolve_project_template
    from library.tools.video_prefs import load_video_preferences

    config = create_project(
        "intake", name="Intake", root=tmp_path, language="es",
        shape="both", speakers=[{"name": "Craig", "role": "host"}],
        brief_title="The test brief", brand_series="intake-series")
    project = tmp_path / "intake"
    for filename in ("project.yaml", "brand.json", "brief.md",
                     "style.yaml", "video.yaml"):
        assert (project / filename).is_file(), filename

    reread = load_project_config(project / "project.yaml")
    assert reread.source.language == "es"
    assert reread.source.shape == "both"
    assert reread.source.speakers == [{"name": "Craig",
                                       "role": "host"}]
    assert reread.pipeline.creative_brief == "brief.md"

    template = resolve_project_template("", project_folder=project)
    assert template.series_id == "intake-series"
    assert load_video_preferences(project) is None
    brief = (project / "brief.md").read_text(encoding="utf-8")
    assert "Craig (host)" in brief


def test_new_with_no_answers_scaffolds_undecided(tmp_path):
    config = create_project("blank", name="Blank", root=tmp_path)
    assert config.source.speakers is None
    assert config.source.shape == ""
    reread = load_project_config(tmp_path / "blank" / "project.yaml")
    assert reread.source.speakers is None
    brief = (tmp_path / "blank" / "brief.md").read_text(
        encoding="utf-8")
    assert "Undecided" in brief


def test_new_refuses_a_malformed_declaration_before_touching_disk(
        tmp_path):
    """Validation runs before the mkdir: a refusal must not leave an
    empty directory behind."""
    with pytest.raises(ValueError, match="source.shape"):
        create_project("bad", name="Bad", root=tmp_path,
                       shape="podcast")
    assert not (tmp_path / "bad").exists()
