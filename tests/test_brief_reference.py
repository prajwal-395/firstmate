"""The brief travels as a reference the model can follow, not as a copy.

`#256` wired the captain's 47,903-byte channel brief into seven prompts
and it became 37.0%-84.3% of each of them - 46.9% of every byte the
pipeline's replayable steps send, with 41.9% of the document in sections
no LLM planning step can act on.  `library/tools/brief_reference.py`
replaces the copy with a path plus a map.

The load-bearing property is REACHABILITY: a step that can no longer
find the brief is a regression, not a saving.  So the tests here do not
assert on the shape of the map.  They FOLLOW it - parsing the path and
the line range out of the reference exactly as the model reads them, and
running the command a shell-capable harness would run - and require that
what comes back is content the step was never handed.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools.brief_reference import (  # noqa: E402
    HARNESS_READS_FILES,
    INLINE_WHEN_UNDER_BYTES,
    UnknownHarness,
    build_reference,
    harness_reads_files,
    is_inline,
    parse_sections,
    project_pinned_sections,
    project_series_identity,
    reference_path,
    restore_for_harness,
)

# A document shaped like the captain's: a preamble, a section far too big
# to inline, and a section small enough that quoting it costs nothing.
BIG_SECTION_BODY = "Music is company, never encouragement. " * 60
DOCUMENT = (
    "# Channel Spec\n\n"
    "Master reference for the channel.\n\n"
    "## Music & Sound Philosophy\n\n"
    f"{BIG_SECTION_BODY}\n\n"
    "### Sound Design Direction\n\n"
    "UNIQUE_DEEP_SENTENCE_7c21 silence is a tool.\n\n"
    "## Volume Targets\n\n"
    "Thirty videos a month.\n"
)


def _reference(tmp_path, **kw):
    brief = tmp_path / "brief.md"
    brief.write_text(DOCUMENT, encoding="utf-8")
    return brief, build_reference(str(brief), DOCUMENT, **kw)


# ── The rule ────────────────────────────────────────────────────────



def test_the_preamble_is_inline_in_full(tmp_path):
    """Clause 1: the document's own statement of what it is."""
    _, ref = _reference(tmp_path)
    assert "Master reference for the channel." in ref


def test_a_section_under_the_threshold_is_inline(tmp_path):
    """Clause 2: quoting it costs about what describing it costs."""
    _, sections = parse_sections(DOCUMENT)
    small = [s for s in sections if s.title == "Volume Targets"][0]
    assert small.body_bytes < INLINE_WHEN_UNDER_BYTES
    inline, reason = is_inline(small, set())
    assert inline and str(INLINE_WHEN_UNDER_BYTES) in reason

    _, ref = _reference(tmp_path)
    assert "Thirty videos a month." in ref


def test_a_section_over_the_threshold_is_a_map_entry_and_not_a_copy(tmp_path):
    """Clause 4: heading, size, line range and lede - not the body."""
    _, ref = _reference(tmp_path)
    assert "## Music & Sound Philosophy" in ref
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in ref, (
        "the deep section's body reached the prompt; the map is a map")
    assert "- Sound Design Direction" in ref, (
        "the map has to name the sub-headings or the model cannot tell "
        "whether the section holds what it needs")


def test_the_project_can_pin_a_section_inline(tmp_path):
    """Clause 3: the judgement belongs to whoever owns the video."""
    _, ref = _reference(tmp_path, pinned=["music & sound philosophy"])
    assert "UNIQUE_DEEP_SENTENCE_7c21" in ref
    assert "pinned by the project" in ref


def test_nothing_is_pinned_by_default(tmp_path):
    """There is no default list, because a default list would be the
    engine deciding which parts of the captain's document are about the
    captain's video."""
    project = tmp_path / "p"
    project.mkdir()
    (project / "project.yaml").write_text('name: "T"\nslug: "t"\n',
                                          encoding="utf-8")
    assert project_pinned_sections(str(project)) == []






# ── Reachability: the property the whole change rests on ────────────

def test_following_the_reference_reaches_a_section_it_was_not_handed(tmp_path):
    """The proof.

    Everything used here is parsed out of the reference string itself -
    the path off the `FILE:` line, the line range off the heading - and
    the command is the one the reference tells the model to run.  Nothing
    is smuggled in from the test.
    """
    brief, ref = _reference(tmp_path)

    path = reference_path(ref)
    assert Path(path).is_absolute() or Path(path).exists()

    import re
    m = re.search(r"## Music & Sound Philosophy\s+\[[\d,]+ B, "
                  r"lines (\d+)-(\d+)\]", ref)
    assert m, "the map must carry a line range or the path is not followable"
    start, end = m.groups()

    body = subprocess.run(["sed", "-n", f"{start},{end}p", path],
                          capture_output=True, encoding="utf-8").stdout

    assert "UNIQUE_DEEP_SENTENCE_7c21" in body, (
        "the line range in the map does not hold the section it names")
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in ref, (
        "this only proves anything if the sentence was NOT handed over")


def test_the_example_command_the_reference_prints_actually_runs(tmp_path):
    """A path with an apostrophe in it is the captain's real case:
    `series portfolio '26 planning`.  A command the model cannot paste is
    not an affordance."""
    planning = tmp_path / "series portfolio '26 planning"
    planning.mkdir()
    brief = planning / "brand.md"
    brief.write_text(DOCUMENT, encoding="utf-8")
    ref = build_reference(str(brief), DOCUMENT)

    command = [l.strip() for l in ref.split("\n") if "sed -n" in l][0]
    out = subprocess.run(["bash", "-c", command],
                         capture_output=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip(), "the example command returned nothing"




# ── Clause 5: the harness ───────────────────────────────────────────





def test_the_agent_harness_gets_the_brief_restored_in_the_request(tmp_path):
    """`gather_step_inputs` has no idea which backend will answer, so the
    restore happens in `present_llm_step`, which does.  This drives the
    real function through the agent path and reads the request file -
    the artefact the answering harness actually reads."""
    from unittest.mock import patch

    from library.processes.edit_video import run_pipeline

    brief = tmp_path / "brief.md"
    brief.write_text(DOCUMENT, encoding="utf-8")
    ref = build_reference(str(brief), DOCUMENT)
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in ref

    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text('name: "T"\nslug: "t"\n',
                                          encoding="utf-8")
    prompt = tmp_path / "handoff.md"
    prompt.write_text("Do the work.\n", encoding="utf-8")

    with patch.object(run_pipeline, "_agent_sleep"), patch.object(
            run_pipeline, "_agent_clock",
            side_effect=[0, 0, 10, 10, 10, 10]):
        with pytest.raises(run_pipeline.LLMError, match="Timeout"):
            run_pipeline.present_llm_step(
                str(prompt),
                {"creative_brief": ref, "project_folder": str(project)},
                "plan_vfx",
                manifest={"interface": {"inputs": [{"name": "creative_brief"}],
                                        "outputs": [{"name": "vfx_plan"}]}},
                full_auto="agent", llm_timeout=1)

    req = json.loads((project / "pipeline_output" / "llm_requests"
                      / "plan_vfx.json").read_text(encoding="utf-8"))
    assert "NOT copied into this prompt" in req["context"]
    assert str(brief) in req["context"], (
        "the agent harness follows a path, so the filed request carries "
        "the reference - the model reads the document from the file, "
        "not from a copy")
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in req["context"], (
        "the whole document must not travel when the harness can read "
        "the file - that copy was the 37%-84% bloat this module removed")


# ── #258: which series this video belongs to ─────────────────────────
#
# The captain's channel brief names eight series, and the blind A/B on
# 001 showed the model reaching for one of them by name - "Through the
# 4th Wall", precisely the series 001 is not.  Nothing in the run said
# which series the video is in, so the roster read as a choice.  The
# reference header now states the project's membership - or, when the
# project names none, says that absence out loud.

def test_a_declared_series_is_stated_where_the_model_reads_the_brief(tmp_path):
    _, ref = _reference(tmp_path, series_identity="Through the 4th Wall")
    assert ("This video belongs to the series 'Through the 4th Wall'."
            in ref)
    assert "do not present this video as one of them." in ref


def test_an_undeclared_series_is_stated_as_an_absence(tmp_path):
    """001 names no series, and that silence is the case that
    mis-steered the model: a roster with no membership statement reads
    as a menu."""
    _, ref = _reference(tmp_path, series_identity="")
    assert "The project names no series for this video." in ref
    assert ("do not present this video as belonging to any of them "
            "by name." in ref)


def test_documents_that_are_not_the_brief_carry_no_membership_line(tmp_path):
    """The anchor is the brief's, not the mechanism's.  The SFX
    catalogue and the footage analysis name no series, so a reference
    built without an identity carries no line either way."""
    _, ref = _reference(tmp_path)
    assert "belongs to the series" not in ref
    assert "names no series" not in ref


def _project_with(tmp_path, declaration: str):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "project.yaml").write_text(declaration, encoding="utf-8")
    return project




def test_a_malformed_series_declaration_raises(tmp_path):
    project = _project_with(
        tmp_path,
        'name: "T"\nslug: "t"\npipeline:\n  series:\n    - "a"\n')
    with pytest.raises(ValueError, match="series"):
        project_series_identity(str(project))




def test_the_agent_restore_states_the_series_ahead_of_the_document(tmp_path):
    """`present_llm_step` replaces the reference with the whole file in
    the filed request.  That is the route on which the model reads the
    full eight-series roster, so the membership line is stated ahead of
    it rather than lost with the header."""
    from unittest.mock import patch

    from library.processes.edit_video import run_pipeline

    brief = tmp_path / "brief.md"
    brief.write_text(DOCUMENT, encoding="utf-8")
    ref = build_reference(str(brief), DOCUMENT,
                          series_identity="Through the 4th Wall")

    project = _project_with(
        tmp_path, 'name: "T"\nslug: "t"\nseries: "Through the 4th Wall"\n')
    prompt = tmp_path / "handoff.md"
    prompt.write_text("Do the work.\n", encoding="utf-8")

    with patch.object(run_pipeline, "_agent_sleep"), patch.object(
            run_pipeline, "_agent_clock",
            side_effect=[0, 0, 10, 10, 10, 10]):
        with pytest.raises(run_pipeline.LLMError, match="Timeout"):
            run_pipeline.present_llm_step(
                str(prompt),
                {"creative_brief": ref, "project_folder": str(project)},
                "plan_vfx",
                manifest={"interface": {"inputs": [{"name": "creative_brief"}],
                                        "outputs": [{"name": "vfx_plan"}]}},
                full_auto="agent", llm_timeout=1)

    req = json.loads((project / "pipeline_output" / "llm_requests"
                      / "plan_vfx.json").read_text(encoding="utf-8"))
    assert ("This video belongs to the series 'Through the 4th Wall'."
            in req["context"]), (
        "the membership line travels in the reference header, ahead of "
        "the roster the model reads from the file")
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in req["context"]


def test_the_api_restore_states_the_absence_for_a_project_naming_none(tmp_path):
    """The 001 case under `api`: no series declared, full roster
    travelling, and the absence said out loud ahead of it."""
    brief = tmp_path / "brief.md"
    brief.write_text(DOCUMENT, encoding="utf-8")
    ref = build_reference(str(brief), DOCUMENT, series_identity="")

    project = _project_with(tmp_path, 'name: "T"\nslug: "t"\n')
    inputs, restored = restore_for_harness(
        {"creative_brief": ref, "project_folder": str(project)}, "api")

    assert restored == ["creative_brief"]
    assert inputs["creative_brief"].startswith(
        "The project names no series for this video.")
    assert "UNIQUE_DEEP_SENTENCE_7c21" not in ref
    assert "UNIQUE_DEEP_SENTENCE_7c21" in inputs["creative_brief"]




# ── The declaration round-trips through the project config ────────────
#
# `manage_project.py new` writes `project.yaml` off
# `project_config_to_dict`, so a series the schema cannot carry is a
# series the next rewrite silently drops.



def test_a_top_level_series_is_kept_not_dropped(tmp_path):
    """The runtime reads `series` at the top level or under `pipeline:`,
    so the schema must honour a top-level declaration rather than lose
    it on the next rewrite."""
    from library.schemas.project_config import (
        load_project_config, project_config_to_dict,
    )
    project = _project_with(
        tmp_path, 'name: "T"\nslug: "t"\nseries: "Night Owls"\n')

    config = load_project_config(project / "project.yaml")
    assert config.pipeline.series == "Night Owls"
    assert (project_config_to_dict(config)["pipeline"]["series"]
            == "Night Owls")




