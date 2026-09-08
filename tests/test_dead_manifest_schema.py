"""The dead step-manifest schema stays dead - by staying deleted.

``library/schema/manifest.schema.json`` required a top-level
``state: {reads, writes}`` that no step or process manifest has carried
since #73 migrated the pipeline to DAG-driven data flow, and no Python
in the tree ever loaded the file - only ``assembly_manifest.schema.json``
is ever loaded (``library/tools/manifest_validator.py``). It was INERT,
not misleading: it never ran, so it never reported a false pass.

The decision was DELETE, not wire-up: beyond ``state`` the schema
disagreed with the manifests in ~20 further ways (``determinism:
hybrid``, ``archetype: validation``, output extras like
``expected_schema``, process-level ``llm_config``/``visual_qa``), and
wiring it up would have blessed each of those drifts rather than fixing
them. Enforcement of what the schema claimed to cover already lives in
code: ``step_ledger.stage_of``, ``run_scope``, ``context_projector``
and the tests that walk every manifest.

These tests pin the deletion from both ends: the file stays gone, and
no functional pointer at it comes back. Both fail against the pre-fix
tree (the file existed; two `$schema` values and one test loaded it)
and pass after it. Past-tense prose history notes are not pointers and
do not trip the scan - only a line that treats the file as a loadable
artifact does.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The bare identifier, swept across every file type once. What broke here
# was one dead schema; what breaks next is the next dead pointer, so the
# test scans text rather than re-asserting one path.
DEAD_SCHEMA_ID = "manifest.schema.json"

# `assembly_manifest.schema.json` is the LIVE assembly schema and must not
# match: the dead id is only a hit where it is not the tail of that name.
DEAD_SCHEMA_RE = re.compile(r"(?<!assembly_)manifest\.schema\.json")

# Directories worth scanning, derived from where the six pre-fix
# references lived (library code, process manifests, tests). Hidden
# directories, virtualenvs and vendored trees are never the deliverable.
SEARCH_ROOTS = ("library", "tests", "scripts", "docs")

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}


# A prose history note ("a dead schema once described X") is not a
# pointer - only a line that TREATS the file as a loadable artifact is.
# A mention inside a `.json` file is always functional (a `$schema` value
# or equivalent); elsewhere the line must reach for the file.
FUNCTIONAL_RE = re.compile(
    r"\$schema|read_text|open\(|Path\(|exists|json\.load|"
    r"load.*schema|schema.*load|import |require\(|href|src="
)


def _references_to_dead_schema() -> list:
    hits = []
    own = Path(__file__).resolve()
    for root in SEARCH_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.resolve() == own:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line in text.splitlines():
                if not DEAD_SCHEMA_RE.search(line):
                    continue
                if path.suffix == ".json" or FUNCTIONAL_RE.search(line):
                    hits.append(str(path.relative_to(REPO)))
                    break
    return sorted(hits)


def test_the_dead_schema_stays_deleted():
    assert not (REPO / "library" / "schema" / DEAD_SCHEMA_ID).exists(), (
        f"library/schema/{DEAD_SCHEMA_ID} is back. It required a "
        f"top-level `state` block no manifest carries and nothing loaded "
        f"it - delete it again rather than leaving it as decoration."
    )


def test_no_pointer_at_the_deleted_schema_survives():
    assert _references_to_dead_schema() == []
