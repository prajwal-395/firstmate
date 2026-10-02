"""No spent one-shot migrations live in scripts/.

`scripts/migrate_projects.py` moved in-repo project data out to
PROJECTS_ROOT and was deleted once its source tree (`projects/`) was
gone (backlog `vep-hygiene-leftovers`, item 1). A one-shot that has
run must not linger: the next reader cannot tell a spent migration
from a live tool, and re-running one against a tree that no longer
exists is the failure direction.

Run it alone with `python3 -m pytest tests/tooling/test_repo_hygiene.py -q`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_SPENT_MIGRATIONS = ("migrate_projects.py",)


def test_spent_migrations_stay_gone():
    """Fails the moment a removed one-shot migration returns."""
    for name in _SPENT_MIGRATIONS:
        assert not (REPO_ROOT / "scripts" / name).exists(), (
            f"scripts/{name} is a spent one-shot migration whose source "
            "tree is gone; do not re-add it, write a live tool instead."
        )


def test_no_script_references_the_removed_projects_tree():
    """No script may migrate from the in-repo projects/ tree, which no longer exists."""
    offenders = []
    for path in sorted((REPO_ROOT / "scripts").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"""["']projects/""", text) or "REPO_ROOT / \"projects\"" in text:
            offenders.append(path.name)
    assert not offenders, (
        f"scripts referencing the removed in-repo projects/ tree: {offenders}; "
        "the migration is spent, point at PROJECTS_ROOT instead."
    )
