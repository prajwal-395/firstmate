"""One skill source: `.agents/skills/`, read by every agent harness.

Codex and opencode read `.agents/skills/` natively and Claude Code reads
`.claude/skills/`, which is a link to it - so all three read one text.
The defects this names: `.claude/skills` becoming a second copy that
drifts, and a pipeline-runtime skill whose SKILL.md the prompt tells the
model to read (`pipeline_skills.skill_text`) not being there.
"""

from pathlib import Path

from library.tools import pipeline_skills

REPO = Path(__file__).resolve().parents[1]
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


def test_every_catalogued_skill_has_its_text_in_the_source():
    for name in pipeline_skills.SKILLS:
        skill_md = SOURCE / name / "SKILL.md"
        assert skill_md.is_file(), (
            f"the prompt tells the model to read {skill_md} for "
            f"{name}'s flags, and it is not there")
        assert _frontmatter_name(skill_md) == name


def test_every_skill_in_the_source_names_its_own_directory():
    for skill_md in sorted(SOURCE.glob("*/SKILL.md")):
        assert _frontmatter_name(skill_md) == skill_md.parent.name, (
            f"{skill_md}: a harness loads a skill by its frontmatter "
            f"name, and this one disagrees with its directory")
