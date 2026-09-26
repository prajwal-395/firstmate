"""The two AGENTS.md gates must be able to FAIL.

See AGENTS.md 10.4 for the test-authoring rule. Both gates below guard a file
that regrew 34,848 characters in the two working days after the 2026-09-01
condensation, so a future worker weakening either one silently is the failure
this pins down.

The size gate runs in CI and fails the build.  The preservation check is a
manual restructure tool - it needs a `--before` - so it is exercised here
rather than in the workflow.

Every case builds its own file under `tmp_path`; nothing here edits the real
AGENTS.md.  `--gained-since HEAD` is used rather than `origin/main` because a
shallow CI checkout has HEAD and may not have the remote ref.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIZE = REPO_ROOT / "scripts" / "check_agents_md_size.py"
PRESERVE = REPO_ROOT / "scripts" / "check_agents_md_preservation.py"
AGENTS = REPO_ROOT / "AGENTS.md"


def run(script, *args):
    p = subprocess.run([sys.executable, str(script), *[str(a) for a in args]],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=REPO_ROOT, check=False)
    return p.returncode, p.stdout + p.stderr


# --------------------------------------------------------------------------
# The size gate, and the per-section budget that makes it durable
# --------------------------------------------------------------------------

def test_size_gate_passes_on_the_real_file():
    """The baseline. If this fails, the budgets are stale, not the file."""
    code, out = run(SIZE)
    assert code == 0, out
    assert "all within budget" in out


def test_a_new_subsection_breaches_its_section_budget(tmp_path):
    """The measured regrowth shape: +2,913 chars of new ### inside a section."""
    text = AGENTS.read_text(encoding="utf-8")
    at = text.index("## 4. Dashboard")
    grown = (text[:at] + "### A new feature explains itself\n\n"
             + "- A rule about the new module. " * 97 + "\n\n" + text[at:])
    f = tmp_path / "AGENTS.md"
    f.write_text(grown, encoding="utf-8")
    code, out = run(SIZE, f)
    assert code == 1
    assert "## 3. Pipeline execution" in out and "over its" in out


def test_a_new_section_with_no_budget_is_refused(tmp_path):
    """A section that exists and silently has no bound is the trap."""
    f = tmp_path / "AGENTS.md"
    f.write_text(AGENTS.read_text(encoding="utf-8")
                 + "\n## 17. Something new\n\nRules nobody budgeted.\n", encoding="utf-8")
    code, out = run(SIZE, f)
    assert code == 1
    assert "NO BUDGET DECLARED" in out




def test_budgets_cannot_be_raised_one_section_at_a_time():
    """The ratchet cannot be defeated a section at a time: the budgets must
    sum to no more than the global ceiling, so raising one forces lowering
    another."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_size", SIZE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert sum(mod.SECTION_BUDGETS.values()) <= mod.CEILING
    assert set(mod.SECTION_BUDGETS) == set(mod.sections(AGENTS.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# The preservation check, widened to an enumerated corpus
# --------------------------------------------------------------------------



def test_a_rule_deleted_with_no_destination_fails(tmp_path):
    text = AGENTS.read_text(encoding="utf-8")
    s = text.index("### A step that makes a craft judgement is told what craft it is")
    e = text.index("### A step with no creative brief ASKS")
    f = tmp_path / "AGENTS.md"
    f.write_text(text[:s] + text[e:], encoding="utf-8")
    code, out = run(PRESERVE, "--before", AGENTS, "--after", f)
    assert code == 1
    assert "VANISHED" in out


def test_a_rule_that_moved_to_an_enumerated_destination_is_reported(tmp_path):
    """A PASS must be readable as a MAPPING - which destination received what -
    not merely as a count."""
    text = AGENTS.read_text(encoding="utf-8")
    s = text.index("### A step that makes a craft judgement is told what craft it is")
    e = text.index("### A step with no creative brief ASKS")
    moved = text[s:e]
    dest = tmp_path / "craft_role_copy.py"
    dest.write_text('"""\n' + moved + '\n"""\n', encoding="utf-8")
    # The heading STAYS - it is the index row, and section numbers are cited
    # from code.  Only the body moves.
    heading = moved.split("\n", 1)[0]
    row = heading + "\n\nOne enumeration, `craft_role_copy.py`.\n\n"
    f = tmp_path / "AGENTS.md"
    f.write_text(text[:s] + row + text[e:], encoding="utf-8")
    code, out = run(PRESERVE, "--before", AGENTS, "--after", f, dest)
    assert code == 0, out
    assert "WHERE THE MOVED RULES LANDED" in out
    assert "craft_role_copy.py" in out


def test_the_corpus_is_enumerated_so_an_unlisted_destination_does_not_count(tmp_path):
    """If the check scanned the repo, a rule would 'survive' by coincidence.
    The same content, present on disk but NOT enumerated, must still fail."""
    text = AGENTS.read_text(encoding="utf-8")
    s = text.index("### A step that makes a craft judgement is told what craft it is")
    e = text.index("### A step with no creative brief ASKS")
    (tmp_path / "somewhere_else.py").write_text('"""\n' + text[s:e] + '\n"""\n', encoding="utf-8")
    heading = text[s:e].split("\n", 1)[0]
    f = tmp_path / "AGENTS.md"
    f.write_text(text[:s] + heading + "\n\nMoved.\n\n" + text[e:], encoding="utf-8")
    code, out = run(PRESERVE, "--before", AGENTS, "--after", f)   # not enumerated
    assert code == 1
    assert "VANISHED" in out


def test_a_gutted_section_must_point_at_a_destination_that_gained(tmp_path):
    """What replaced MAX_SECTION_CUT / MIN_SECTION_BODY. A section may shrink
    to an index row; it may not shrink into silence."""
    text = AGENTS.read_text(encoding="utf-8")
    s = text.index("## 12. The look")
    e = text.index("## 13. Intros, outros and end cards")

    silent = tmp_path / "silent.md"
    silent.write_text(text[:s] + "## 12. The look\n\nThere is no house look.\n\n" + text[e:],
                      encoding="utf-8")
    code, out = run(PRESERVE, "--before", AGENTS, "--after", silent,
                    REPO_ROOT / "library/tools/series_look.py", "--gained-since", "HEAD")
    assert code == 1
    assert "SHRANK INTO SILENCE" in out




def test_a_token_dump_still_fails(tmp_path):
    """The 2026-09-01 gaming pattern: delete the sentence, paste its
    identifiers after it. It satisfies every token check and destroys the rule."""
    f = tmp_path / "AGENTS.md"
    f.write_text(AGENTS.read_text(encoding="utf-8")
                 + "\n- A rule (M: `foo_bar`, `baz_qux`)\n", encoding="utf-8")
    code, out = run(PRESERVE, "--before", AGENTS, "--after", f)
    assert code == 1
    assert "TOKEN DUMPS" in out

