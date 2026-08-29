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
    reference_path,
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

def test_the_map_costs_a_fraction_of_the_copy(tmp_path):
    _, ref = _reference(tmp_path)
    assert len(ref.encode()) < len(DOCUMENT.encode())


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


def test_a_pinned_section_is_read_off_project_yaml(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "project.yaml").write_text(
        'name: "T"\nslug: "t"\n'
        'pipeline:\n  creative_brief_inline:\n'
        '    - "Music & Sound Philosophy"\n', encoding="utf-8")
    assert project_pinned_sections(str(project)) == [
        "Music & Sound Philosophy"]


def test_a_malformed_pin_declaration_raises(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "project.yaml").write_text(
        'name: "T"\nslug: "t"\n'
        'pipeline:\n  creative_brief_inline:\n    a: 1\n', encoding="utf-8")
    with pytest.raises(ValueError):
        project_pinned_sections(str(project))


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


def test_the_path_is_read_off_the_same_line_the_model_reads(tmp_path):
    brief, ref = _reference(tmp_path)
    assert reference_path(ref) == str(brief)


# ── Clause 5: the harness ───────────────────────────────────────────

def test_the_harness_enumeration_is_complete_and_an_unknown_one_raises():
    assert set(HARNESS_READS_FILES) == {"agy", "mock", "api"}
    assert harness_reads_files("agy") is True
    assert harness_reads_files("api") is False
    with pytest.raises(UnknownHarness):
        harness_reads_files("some_new_backend")


def test_a_harness_that_cannot_read_a_file_gets_the_document_whole(tmp_path):
    ref = build_reference(str(tmp_path / "b.md"), DOCUMENT, harness="api")
    assert ref == DOCUMENT
    assert reference_path(ref) == "", (
        "a document carried whole has no FILE: marker to follow")


def test_the_api_harness_gets_the_brief_restored_in_the_prompt(tmp_path):
    """`gather_step_inputs` has no idea which backend will answer, so the
    restore happens in `present_llm_step`, which does.  This drives the
    real function through the real `api` path far enough to see the
    context it builds."""
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

    seen = {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def generate(self, full_prompt, system=None):
            seen["prompt"] = full_prompt
            return json.dumps({"vfx_plan": []})

    import library.tools.llm_client as llm_client
    original = llm_client.LLMClient
    llm_client.LLMClient = _FakeClient
    try:
        run_pipeline.present_llm_step(
            str(prompt),
            {"creative_brief": ref, "project_folder": str(project)},
            "plan_vfx",
            manifest={"interface": {"inputs": [{"name": "creative_brief"}],
                                    "outputs": [{"name": "vfx_plan"}]}},
            full_auto="api", llm_timeout=5)
    finally:
        llm_client.LLMClient = original

    assert "UNIQUE_DEEP_SENTENCE_7c21" in seen["prompt"], (
        "the api harness cannot follow a path, so it must be handed the "
        "document whole - a route the model cannot follow is a loss")
