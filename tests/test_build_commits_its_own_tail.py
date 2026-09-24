"""A reel build leaves its project store CLEAN.

Measured 2026-09-11 on the captain's project: HEAD read `reels build:
Reel 13 - the-accounting-firm-ai-called-healthcare` and the working
tree was dirty with exactly one thing - `step_outputs.verify_reels.
reel_verification.organised`, the bin organisation.

The cause is an ORDER, not a missing call.  The per-build commit fires
inside the promoting node (`reel_build`, `step_7_02_verify_reels`), and
the reels runner (`library/processes/reels/run_reels.py`) writes that node's own output to
`pipeline_data.json` AFTER the node returns.  So the last node's record
could never be inside the commit it belongs to, and every build ended
dirty.

A store that is dirty after every build teaches a reader to ignore its
dirtiness, and that is how the captain's hand edits went missing three
times.  The run now closes its own record, in the loop that owns the
state write.

Read off the source and exercised through `commit_build`; nothing here
reaches Resolve or a real project.
"""

import ast
import subprocess
from pathlib import Path

from library.tools.versions import store as bvc

REPO = Path(__file__).resolve().parents[1]


def _function(name):
    tree = ast.parse((REPO / "library" / "processes" / "reels"
                      / "run_reels.py").read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_build_command_closes_its_own_record():
    """The closing commit exists, and it is OUTSIDE the node loop.

    Inside the loop it would commit before the next node's state write
    and reproduce the defect one node later.
    """
    body = _function("run")
    calls = [n for n in ast.walk(body)
             if isinstance(n, ast.Call)
             and ast.unparse(n).startswith("commit_run_tail")]
    assert calls, (
        "the reels runner no longer closes its own version-control "
        "record, so every reel build ends with the state write it just "
        "made uncommitted")
    # The loop over nodes must not contain it.
    loops = [n for n in ast.walk(body) if isinstance(n, ast.For)]
    for loop in loops:
        assert not any(
            ast.unparse(n).startswith("commit_run_tail")
            for n in ast.walk(loop) if isinstance(n, ast.Call)), (
            "the closing commit moved inside the node loop, where it "
            "runs before the next node's state write - which is the "
            "ordering defect it exists to close")
    # And it must come AFTER the state write it completes.
    writes = [n.lineno for n in ast.walk(body) if isinstance(n, ast.Call)
              and "save_pipeline_state" in ast.unparse(n)]
    assert writes and min(c.lineno for c in calls) > max(writes)






def test_the_tail_commit_lands_what_the_final_state_write_left(tmp_path):
    """The defect, reproduced and closed, on a real repository.

    The node's own commit runs first and is clean-ended; then the
    runner writes the last node's output, exactly as `run_reels.run`
    does - and the tail commit is what stops the store ending dirty.
    """
    from library.processes.reels import run_reels

    bvc.init_project_repo(str(tmp_path))
    (tmp_path / "pipeline_data.json").write_text(
        '{"step_outputs": {"build_reels": {}}}', encoding="utf-8")
    first = bvc.commit_build(str(tmp_path), "reels build: Reel 13")
    assert first["committed"] is True

    # The runner's final state write, after the node committed.
    (tmp_path / "pipeline_data.json").write_text(
        '{"step_outputs": {"build_reels": {}, "verify_reels": '
        '{"reel_verification": {"organised": {"moved": 1}}}}}',
        encoding="utf-8")
    assert _porcelain(tmp_path), "the state write left nothing to commit"

    run_reels.commit_run_tail(str(tmp_path))

    assert _porcelain(tmp_path) == "", (
        "the reel build ended with an uncommitted project store, which "
        "is how a hand edit stops being visible as one")
    assert "organised" in subprocess.run(
        ["git", "show", "HEAD:pipeline_data.json"], cwd=str(tmp_path),
        capture_output=True, text=True, encoding="utf-8",
        check=False).stdout


def test_a_build_that_changed_nothing_makes_no_commit(tmp_path):
    """A no-op build produces no spurious commit - `commit_build`'s own
    rule, which the closing call must not talk it out of."""
    from library.processes.reels import run_reels

    bvc.init_project_repo(str(tmp_path))
    bvc.commit_build(str(tmp_path), "reels build: Reel 13")
    before = _head(tmp_path)
    run_reels.commit_run_tail(str(tmp_path))
    assert _head(tmp_path) == before


def _porcelain(root) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=str(root),
                          capture_output=True, text=True,
                          encoding="utf-8", check=False).stdout.strip()


def _head(root) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                          capture_output=True, text=True,
                          encoding="utf-8", check=False).stdout.strip()
