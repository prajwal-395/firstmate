"""One skill source: `.agents/skills/`, read by every agent harness.

Codex and opencode read `.agents/skills/` natively and Claude Code reads
`.claude/skills/`, which is a link to it - so all three read one text.
The defects this names: `.claude/skills` becoming a second copy that
drifts, and a pipeline-runtime skill whose SKILL.md the prompt tells the
model to read (`pipeline_skills.skill_text`) not being there.
The generated `pipeline_operations` skill drifting from its registry:
it shipped missing `picture.plan` and `edit_graph.build`, so agents on
every host chose from an incomplete operation list (J01).
"""

from pathlib import Path

from library.tools import pipeline_skills

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / pipeline_skills.SKILL_TEXT_ROOT


def _frontmatter_name(skill_md: Path) -> str:
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", f"{skill_md} has no frontmatter"
    for line in lines[1:lines.index("---", 1)]:
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"{skill_md} frontmatter names no skill")


def test_claude_code_reads_the_same_directory_not_a_copy():
    link = REPO / ".claude" / "skills"
    assert link.is_symlink(), (
        ".claude/skills is not a link to .agents/skills, so Claude Code "
        "reads a different text from Codex and opencode")
    assert link.resolve() == SOURCE.resolve()


def test_every_skill_text_is_in_the_source_under_its_own_name():
    """The prompt tells the model to read each catalogued skill's
    SKILL.md, and a harness loads a skill by its frontmatter name - so
    every catalogued skill is there, and every skill there names its own
    directory."""
    for name in pipeline_skills.SKILLS:
        skill_md = SOURCE / name / "SKILL.md"
        assert skill_md.is_file(), (
            f"the prompt tells the model to read {skill_md} for "
            f"{name}'s flags, and it is not there")
    for skill_md in sorted(SOURCE.glob("*/SKILL.md")):
        assert _frontmatter_name(skill_md) == skill_md.parent.name, (
            f"{skill_md}: a harness loads a skill by its frontmatter "
            f"name, and this one disagrees with its directory")


def test_pipeline_operations_skill_matches_the_registry():
    """The checked-in skill is what `--emit-skill` prints, byte for byte.

    The skill header says GENERATED - a hand edit to the table is a
    second operation list that drifts, and it did: `picture.plan` and
    `edit_graph.build` shipped in the registry but not in the text
    every host reads, so no agent could choose them. Regenerate with
    `python3 -m library.tools.operations --emit-skill`, never by hand.
    """
    from library.tools import operations

    skill_md = SOURCE / "pipeline_operations" / "SKILL.md"
    assert skill_md.is_file(), (
        "the generated pipeline_operations skill is not checked in")
    # What the documented command writes: `print` adds the one trailing
    # newline `emit_skill()` itself does not carry.
    assert skill_md.read_text(encoding="utf-8") == (
        operations.emit_skill() + "\n"), (
        f"{skill_md} drifts from `operations.emit_skill()` - regenerate "
        "it with `python3 -m library.tools.operations --emit-skill`")


def test_resolve_axi_capability_references_match_the_registry_byte_for_byte():
    """Capability tables are projections of the editing algebra, never a
    second hand-maintained list of operations or qualification claims."""
    from library.tools import operations

    refs = SOURCE / "resolve-axi" / "references"
    assert refs.is_dir(), "resolve-axi generated capability references are missing"
    expected = operations.emit_resolve_axi_references()
    assert {p.name for p in refs.glob("*.md")} == set(expected)
    for name, content in expected.items():
        assert (refs / name).read_text(encoding="utf-8") == content, (
            f"{refs / name} drifts from edit_algebra.OP_STATUS; regenerate "
            "with `bin/vep -m library.tools.operations "
            "--emit-resolve-axi-references`")
