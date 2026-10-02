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
from library.tools.project_registry import get_project
import os
from library.tools.project_layout import Area, layout_for
import json
import sys
from pathlib import Path
from library.tools.footage_identity import (
    enumerate_footage,
    footage_root,
)
from types import SimpleNamespace


def _config(**source_kwargs) -> ProjectConfig:
    return ProjectConfig(name="N", slug="s",
                         source=SourceConfig(**source_kwargs))


# ── validation and round trip ────────────────────────────────────

def test_defaults_and_known_shapes_validate():
    assert SourceConfig().language == "en"
    assert SourceConfig().speakers is None
    for shape in ("speech", "music", "both", "picture-led", ""):
        assert _config(shape=shape).validate() == [], shape
    assert _config().validate() == []


MALFORMED = [
    ("language", "e", "source.language"),
    ("language", "english!", "source.language"),
    ("language", "", "source.language"),
    ("language", 5, "source.language"),
    ("language", ["en"], "source.language"),
    ("shape", "podcast", "source.shape"),
    ("speakers", "Craig", "source.speakers must be a list"),
    ("speakers", [{"title": "host"}], "names no speaker"),
    ("speakers", [{"name": "  "}], "names no speaker"),
    ("speakers", [{"name": "Craig", "role": 5}], "must be a string"),
    ("speakers", [{"name": "Craig", "agent": "x"}], "which nothing reads"),
]


def test_malformed_source_declarations_are_refused_by_name():
    for field, bad, fragment in MALFORMED:
        errors = _config(**{field: bad}).validate()
        assert any(f"source.{field}" in e and fragment in e
                   for e in errors), (field, bad, errors)


def test_declared_source_round_trips():
    """Non-default values round-trip; the default language is not
    written (`language: en` everywhere would read as a decision nobody
    made); a declared language is normalised."""
    assert _dict_to_project_config(
        {"source": {"language": "ES"}}).source.language == "es"
    assert "language" not in project_config_to_dict(_config())["source"]
    roster = [{"name": "Craig", "role": "host"}, {"name": "Akshita"}]
    config = _config(language="es", shape="both", speakers=roster)
    assert config.validate() == []
    as_dict = project_config_to_dict(config)
    assert as_dict["source"]["language"] == "es"
    assert as_dict["source"]["shape"] == "both"
    assert as_dict["source"]["speakers"] == roster
    reread = _dict_to_project_config(as_dict).source
    assert (reread.language, reread.shape, reread.speakers) == (
        "es", "both", roster)


def test_declared_language_reads_off_the_project_folder(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    assert fi.declared_language(str(project)) == "en"
    (project / "project.yaml").write_text("source:\n  language: pt-BR\n")
    assert fi.declared_language(str(project)) == "pt-br"
    (project / "project.yaml").write_text("source:\n  language: 5\n")
    assert fi.declared_language(str(project)) == "en"


# ── speakers ─────────────────────────────────────────────────────

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


def test_declared_speakers_read_off_the_project_folder(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    assert fi.declared_speakers(str(project)) is None
    assert fi.expected_speaker_count(str(project)) is None
    (project / "project.yaml").write_text("source:\n  speakers: []\n")
    assert fi.declared_speakers(str(project)) == []
    (project / "project.yaml").write_text(
        "source:\n  speakers:\n    - name: Craig\n      role: host\n"
        "    - {title: nameless}\n    - just-a-string\n")
    assert fi.declared_speakers(str(project)) == [
        {"name": "Craig", "role": "host"}]
    assert fi.expected_speaker_count(str(project)) == 1
    assert fi.expected_speaker_count(declaration=None) is None
    assert fi.expected_speaker_count(declaration=[]) == 0
    assert fi.expected_speaker_count(declaration=[{"name": "Jo"}]) == 1


# ── scaffolding ──────────────────────────────────────────────────

def test_new_scaffolds_what_intake_collects(tmp_path):
    """`new` writes the four intake files and the declarations that
    drive them; a project that declares nothing gets starters that
    change nothing. The defect: intake answers with nowhere to land."""
    from library.tools.brand_registry import resolve_project_template
    from library.tools.video_prefs import load_video_preferences

    create_project(
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

    # The scaffold is the layout (it drifted from the steps once): the
    # containers, README-LAYOUT.md and raw/ only on the input side, and
    # no step directory pre-created.
    root = tmp_path / "blank"
    assert (root / "pipeline_output" / "steps").is_dir()
    assert (root / "README-LAYOUT.md").is_file()
    assert (root / "raw").is_dir()
    for absent in ("music", "assets", "brand_assets", "compositions"):
        assert not (root / absent).exists(), absent
    assert not list((root / "pipeline_output" / "steps").iterdir())


def test_new_refuses_a_malformed_declaration_before_touching_disk(
        tmp_path):
    """Validation runs before the mkdir: a refusal must not leave an
    empty directory behind."""
    with pytest.raises(ValueError, match="source.shape"):
        create_project("bad", name="Bad", root=tmp_path,
                       shape="podcast")
    assert not (tmp_path / "bad").exists()


# --------------------------------------------------------------------------
# From test_project_registry_guidance.py
#
# Project lookup guidance points outside-root projects at a live command.

def test_missing_slug_shows_a_valid_path_based_command(tmp_path):
    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    with pytest.raises(FileNotFoundError) as missing:
        get_project("outside-project", root=projects_root)

    message = str(missing.value)
    assert "python3 manage_project.py info /path/to/outside-project" in message
    assert "manage_project.py dashboard" not in message


# --------------------------------------------------------------------------
# From test_project_scripts.py
#
# The readiness check SEES standalone scripts beside the footage.
#
# Repository-side guards cannot see a script living next to the footage, so
# the finder matches the general shape (any loose `*.py`), never the names
# one record observed. It REPORTS, never refuses.
# History: docs/evidence/project_scripts.md.

def _project(tmp_path):
    """A bare project folder under tmp_path - never a real one."""
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return project


def _plant(project, *relpaths):
    for rel in relpaths:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# standalone\n", encoding="utf-8")
    from library.tools.project_scripts import find_standalone_scripts
    return find_standalone_scripts(str(project))


def test_any_loose_script_is_found_not_a_list_of_names(tmp_path):
    """The three record scripts by name, the fourth the record did not
    name (`render_subtitle_segments.py`, real, from the live folder) and
    a nested one: a finder matching only observed names would miss the
    next script."""
    from library.tools.project_scripts import find_standalone_scripts
    project = _project(tmp_path)
    names = ("place_subtitles.py",
             "generate_podcast_subtitles.py",
             "export_audio.py",
             "render_subtitle_segments.py",
             os.path.join("subtitle_plans", "helper.py"))
    found = _plant(project, *names)
    assert set(found) == {os.path.join(str(project), n) for n in names}
    assert found == sorted(found), "findings arrive in a stable order"
    assert find_standalone_scripts(str(project)) == found


def test_pipeline_output_and_non_python_files_are_not_findings(tmp_path):
    """Scripts under the pipeline's own output area are not reported,
    and neither is anything that is not Python.

    The exclusion is derived from the layout's own read route
    (`read_dir`, which never creates) - not from a copied `"..."`.
    """
    project = _project(tmp_path)
    out_root = layout_for(str(project)).read_dir(Area.OUTPUT_ROOT)
    rel = os.path.join(os.path.basename(str(out_root)), "scratch", "x.py")
    found = _plant(project, "place_subtitles.py", rel, "project.yaml",
                   "notes.md", os.path.join("raw", "LC4930.MXF"))
    assert set(found) == {os.path.join(str(project), "place_subtitles.py")}
    assert not any(os.path.realpath(p).startswith(os.path.realpath(out_root))
                   for p in found)


# --------------------------------------------------------------------------
# From test_project_declared_creative_tasks.py
#
# A project declares a creative task the pipeline invokes, instead of adding a step.
#
# Acceptance: a project-declared creative task reaches a model through
# `present_llm_step` with its role prepended, the floors gate reads its
# prompt, and a declaration that would break a guard is refused. History:
# docs/evidence/creative_tasks.md. Every project is built under tmp_path.

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import creative_tasks  # noqa: E402
from library.tools import creative_floors  # noqa: E402
from library.tools import craft_role  # noqa: E402
from library.tools import undetermined  # noqa: E402
from library.tools import model_task  # noqa: E402


HANDOFF_BODY = "# Pick reels\n\nSENTINEL_TASK_HANDOFF_BODY\n"

ROLE = {
    "discipline": "short-form editor",
    "addressed_as": "You are the short-form editor on this episode.",
    "reads_with": ["A short is a complete small thing, not an excerpt."],
    "decides": ["Which stretches are worth cutting as shorts."],
    "defers": ["Whether a chosen short is built. That is the captain's."],
}

OUTPUTS = [
    {"name": "reel_selection", "type": "object",
     "description": "The chosen stretches."},
]


def _write_project(tmp_path, tasks, handoff_text=HANDOFF_BODY):
    import yaml

    project = tmp_path / "proj"
    project.mkdir()
    (project / "task_handoff.md").write_text(handoff_text, encoding="utf-8")
    (project / "project.yaml").write_text(
        yaml.safe_dump({
            "name": "Task Test",
            "slug": "task-test",
            "pipeline": {"creative_tasks": tasks},
        }),
        encoding="utf-8",
    )
    return project


def _task_entry(name="reel_pick", **overrides):
    entry = {
        "name": name,
        "role": dict(ROLE),
        "handoff": "task_handoff.md",
        "inputs": ["timeline_transcript"],
        "outputs": [dict(o) for o in OUTPUTS],
    }
    entry.update(overrides)
    return entry


def _canned_answer(**extra):
    answer = {"reel_selection": {"moments": [], "considered": []},
              "could_not_determine": []}
    answer.update(extra)
    return answer


def _run_task(monkeypatch, project, name, context, answer):
    """Invoke a declared task through the agent backend with a stubbed wait.

    Mirrors tests/contracts/test_context_contracts.py: the request file the answering
    agent reads is the artifact asserted on.
    """
    from library.processes.edit_video import run_pipeline
    from library.tools.project_layout import Area, layout_for

    layout = layout_for(str(project))
    layout.ensure()
    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = model_task._agent_sleep

    def _sleep(seconds):
        (responses / f"{creative_tasks.task_key(name)}.json").write_text(
            json.dumps(answer))
        return original_sleep(0)

    monkeypatch.setattr(model_task, "_agent_sleep", _sleep)
    undetermined.reset()
    return creative_tasks.present_creative_task(
        str(project), name, context, full_auto="agent", llm_timeout=5)


# ── Acceptance: reaches a model with its role attached ─────────────────

def test_a_declared_task_reaches_a_model_with_its_role_attached(
        tmp_path, monkeypatch):
    project = _write_project(tmp_path, [_task_entry()])
    context = {"timeline_transcript": {"lines": []}}

    result = _run_task(monkeypatch, project, "reel_pick", context,
                       _canned_answer())

    assert result["reel_selection"] == {"moments": [], "considered": []}

    from library.tools.project_layout import Area, layout_for
    prompt = json.loads(
        (layout_for(str(project)).read_dir(Area.LLM_REQUESTS)
         / f"{creative_tasks.task_key('reel_pick')}.json"
         ).read_text(encoding="utf-8"))["prompt"]

    # The role the PROJECT declared, rendered by the shared renderer -
    # expected values derived from the expressions the code uses, not
    # copied literals.
    task = creative_tasks.tasks_for_project(str(project))["reel_pick"]
    expected_block = craft_role.render_block(task.role)
    assert expected_block in prompt
    assert ROLE["addressed_as"] in prompt
    # PREPENDED: the frame comes before the document it frames.
    assert prompt.index(ROLE["addressed_as"]) < prompt.index(
        "SENTINEL_TASK_HANDOFF_BODY")

    # The undetermined forcing function reached it: the answer the stub
    # gave is recorded as a real reading, not a non-answer.
    assert undetermined.collected()
    assert undetermined.collected()[-1].reading == undetermined.NOTHING_MISSING


# ── The declaration is refused when it would break a guard ─────────────

def test_a_declaration_that_would_break_a_guard_is_refused_by_name(tmp_path):
    """A handoff demanding a count (floor), a name shadowing a step id, a
    task with nothing to ask, and a missing handoff each refuse."""
    rows = [
        ("floor", dict(handoff_text=(
            "# Pick reels\n\nYou must plan at least 3 reels.\n")), {}),
        ("step", {}, dict(name="select_broll")),
        ("nothing to ask", {}, dict(outputs=[])),
        ("handoff", {}, dict(handoff="not_there.md")),
    ]
    for i, (match, project_kw, entry_kw) in enumerate(rows):
        case = tmp_path / f"case{i}"
        case.mkdir()
        project = _write_project(case, [_task_entry(**entry_kw)], **project_kw)
        with pytest.raises(creative_tasks.CreativeTaskError, match=match):
            creative_tasks.tasks_for_project(str(project))


# ── The third guard reconciles against the declaration ─────────────────

def test_a_task_with_direction_and_evidence_gets_the_flag_field(
        tmp_path, monkeypatch):
    entry = _task_entry(inputs=["creative_direction", "reel_candidates"],
                        evidence={"reel_candidates": "turn counts per stretch"})
    project = _write_project(tmp_path, [entry])
    context = {"creative_direction": {"mood": "bright"}, "reel_candidates": []}

    _run_task(monkeypatch, project, "reel_pick", context,
              _canned_answer(contradicts_direction=[]))

    from library.tools.project_layout import Area, layout_for
    request = json.loads(
        (layout_for(str(project)).read_dir(Area.LLM_REQUESTS)
         / f"{creative_tasks.task_key('reel_pick')}.json"
         ).read_text(encoding="utf-8"))
    schema_names = [o["name"] for o in json.loads(request["expected_schema"])]
    from library.tools.direction_contradiction import FIELD
    assert FIELD in schema_names


# --------------------------------------------------------------------------
# From test_footage_root.py
#
# A project may declare where its footage lives, and it is CHECKED.
#
# The gap this closes, measured 2026-09-04: the GEO Podcast field test was
# cut by hand in Resolve from footage under `Lucie consulting/Social
# Media/podcast media`.  `<project>/raw` was empty, `enumerate_footage`
# scans only that directory, and the readiness check therefore refused the
# project as having no footage at all.
#
# The alternative fix was to symlink or copy the media into `raw`.  That is
# a write to the captain's own material to work around a missing
# capability, so the capability exists instead.
#
# What is tested hardest here is the REFUSAL. A declared root that is
# relative or absent must not fall back to `raw`: falling back turns a typo
# into "this project has no footage", which is the exact unhelpful refusal
# this declaration exists to prevent.

def _project_2(tmp_path, yaml_body=None):
    project = tmp_path / "project"
    (project / "raw").mkdir(parents=True)
    if yaml_body is not None:
        (project / "project.yaml").write_text(yaml_body)
    return project


def _media(tmp_path, *names):
    media = tmp_path / "elsewhere"
    media.mkdir(exist_ok=True)
    for name in names:
        (media / name).write_bytes(b"\x00" * 64)
    return media


def test_the_root_is_raw_unless_declared(tmp_path):
    project = _project_2(tmp_path)
    assert footage_root(str(project)) == str(project / "raw")
    media = _media(tmp_path, "LC4930.MXF")
    (project / "project.yaml").write_text(
        f"name: T\nslug: t\nsource:\n  footage_root: {media}\n")
    assert footage_root(str(project)) == str(media)


def test_footage_under_a_declared_root_is_enumerated_and_numbered(tmp_path):
    """Including .MXF in capitals - cameras write .MXF, not .mxf."""
    media = _media(tmp_path, "LCATL0011.MXF", "LC4930.MXF")
    project = _project_2(tmp_path, f"name: T\nslug: t\nsource:\n"
                                 f"  footage_root: {media}\n")
    files, skipped = enumerate_footage(str(project))
    assert [f["filename"] for f in files] == ["LC4930.MXF", "LCATL0011.MXF"]
    assert [f["clip_id"] for f in files] == ["clip_001", "clip_002"]
    assert skipped == []


# ── The refusals ─────────────────────────────────────────────────────

def test_a_bad_declared_root_is_refused_never_defaulted(tmp_path):
    """Falling back to raw would turn a typo into 'no footage'."""
    project = _project_2(tmp_path, "name: T\nslug: t\nsource:\n"
                                 "  footage_root: ../media\n")
    with pytest.raises(FileNotFoundError, match="relative"):
        footage_root(str(project))

    (project / "project.yaml").write_text(
        "name: T\nslug: t\nsource:\n  footage_root: /nowhere/at/all\n")
    with pytest.raises(FileNotFoundError) as excinfo:
        footage_root(str(project))
    message = str(excinfo.value)
    assert "/nowhere/at/all" in message
    assert "raw" in message, "the refusal must say what it declined to do"


# ── The frame rate a project declares ────────────────────────────────

def test_a_fractional_frame_rate_survives_the_config(tmp_path):
    """`fps` was an int, which silently truncated 23.976 to 23.

    `type`/`resolution` used to be declared beside it and are now
    ignored - nothing ever read them, the catalog measures both off
    the footage. A file that still carries them reads fine.
    """
    from library.schemas.project_config import load_project_config
    project = _project_2(tmp_path, "name: T\nslug: t\nsource:\n"
                                 "  type: mxf\n"
                                 "  resolution: 3840x2160\n"
                                 "  fps: 23.976\n")
    config = load_project_config(str(project / "project.yaml"))
    assert config.source.fps == 23.976


# --------------------------------------------------------------------------
# From test_new_fractional_fps.py
#
# `manage_project.py new --fps` keeps fractional frame rates.
#
# The defect: `cmd_new` passed `int(args.fps)`, so `--fps 23.976`
# became 23 while `SourceConfig.fps` is a float. The schema half
# already has its test (`test_a_fractional_frame_rate_survives_the_config`);
# this pins the CLI half. No project is created here - `create_project`
# is stubbed and the call is stopped at its own refusal.

sys.path.insert(0, str(REPO))

import manage_project  # noqa: E402
from library.tools.ren_refusal import RenRefusal  # noqa: E402


def test_new_with_fractional_fps_reaches_create_project_untruncated(
        monkeypatch):
    """`--fps 23.976` must arrive as 23.976, not 23."""
    seen = {}

    def fake_create_project(**kwargs):
        seen.update(kwargs)
        raise FileExistsError("stop here - the fps value is what matters")

    monkeypatch.setattr(manage_project, "create_project",
                        fake_create_project)
    args = SimpleNamespace(
        slug="t", name="T", client="", template="", source_type="",
        resolution="", fps="23.976", resolve_name="", tags="",
        description="",
    )
    with pytest.raises(RenRefusal):
        manage_project.cmd_new(args)
    assert seen["fps"] == 23.976
