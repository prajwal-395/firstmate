"""The pipeline runner must not carry a hardcoded answer for any step.

PR #99 landed a proof harness inside `present_llm_step`:

    if node_id == "speech_sequence":
        mock_data = json.load(open("/Users/prajwal/.../001/pipeline_data.json.bak2_migrated"))[...]
        return mock_data

It sat in the shipped runner for two days. It fired for EVERY project and
EVERY backend - `--full-auto agent` wrote its request file, waited for the
response, and then discarded it - so every run of every project inherited
project 001's stale narrative from a backup file on one machine. Nothing
in the run said so; the step reported success.

Two checks, because they fail for different reasons:

1. No absolute path into a developer's home directory. A runner that reads
   a file only one machine has is not a pipeline stage.
2. No `if node_id == "<something>": return <literal-ish>` short circuit in
   `present_llm_step`. That is the shape the harness took, and it is
   indistinguishable at a glance from the legitimate per-backend branches
   around it.
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "library" / "processes" / "edit_video" / "run_pipeline.py"

# Any user home outside the repo. `~` expansions and PIPELINE_* env vars are
# how a real path is supposed to reach this module.
ABSOLUTE_HOME = re.compile(r"[\"']/(?:Users|home)/[^\"'\s]+")


def test_runner_has_no_absolute_home_paths():
    source = RUNNER.read_text()
    offenders = [
        f"line {source[: m.start()].count(chr(10)) + 1}: {m.group(0)}"
        for m in ABSOLUTE_HOME.finditer(source)
    ]
    assert not offenders, (
        "run_pipeline.py hardcodes a path into somebody's home directory:\n  "
        + "\n  ".join(offenders)
        + "\nProject paths reach the runner through --project and "
        "library/tools/paths.py, never as a literal."
    )


def _present_llm_step() -> ast.FunctionDef:
    tree = ast.parse(RUNNER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "present_llm_step":
            return node
    raise AssertionError("present_llm_step is gone from run_pipeline.py")


def test_present_llm_step_does_not_branch_on_a_step_id():
    """No `if node_id == "...":` anywhere in the LLM step handler.

    The backend branches (`full_auto == "agent"` and friends) are legitimate
    and are untouched by this check. Singling out one step by name is not:
    a step's answer comes from its handoff prompt, not from the runner.
    """
    offenders = []
    for node in ast.walk(_present_llm_step()):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if isinstance(left, ast.Name) and left.id == "node_id":
            if any(isinstance(op, (ast.Eq, ast.In)) for op in node.ops):
                literal = ast.unparse(node)
                offenders.append(f"line {node.lineno}: {literal}")

    assert not offenders, (
        "present_llm_step special-cases a step by id:\n  "
        + "\n  ".join(offenders)
        + "\nA per-step answer belongs in that step's handoff.md or "
        "post_bridge.py, where the run can see it."
    )
