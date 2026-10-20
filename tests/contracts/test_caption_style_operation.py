"""No builder of a `caption.style` operation omits the style it applies.

`edit_algebra` declares `required=("unique_id", "style")` for
`caption.style`, and `library/tools/edit_request.py::from_axi_operations`
refuses a request built from an operation without it. That refusal is the
contract: the fix for a builder that omitted the style belongs IN the
builder.

Defect (AXI-11 request 4/8): the live adapter recorded
`{"op": "caption.style", "unique_id": ...}` with no style, so the
request never became a request and the recorded route was a refusal
rather than the AXI path that ran.

This is a SOURCE survey, not a behavioural one: it reads the code that
BUILDS operations - the engine and the live fixture - and finds the
dict literals that leave the style out. It reads the live fixture's
broker script too, because that script is a STRING the fixture runs
inside Resolve, and the operation it builds is recorded from there.

The unit tests are not surveyed: they construct partial operations on
purpose to exercise other contracts, and a fixture that says
"caption.style" is not a builder of one.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The code that BUILDS operations. `tests/unit` is excluded: a fixture
#: that names an operation is not a builder of one.
SEARCH_ROOTS = ("library", "ren", "tests/live")
SKIP_PARTS = {".git", ".venv", "__pycache__", "node_modules", "target"}


def _literal_pairs(node: ast.Dict) -> dict:
    """The literal string keys of a dict literal, by their value."""
    pairs = {}
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            pairs[key.value] = value
    return pairs


def _python_files() -> list[Path]:
    files = []
    for root in SEARCH_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if SKIP_PARTS.intersection(path.parts):
                continue
            files.append(path)
    return sorted(files)


def _styleless_operations(tree: ast.AST) -> list[int]:
    """The lines where a `caption.style` dict literal names no style."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs = _literal_pairs(node)
        op = pairs.get("op")
        if not (isinstance(op, ast.Constant)
                and op.value == "caption.style"):
            continue
        if "style" not in pairs:
            lines.append(node.lineno)
    return lines


def test_no_builder_omits_the_style_a_caption_style_operation_declares():
    """Every dict literal that builds a `caption.style` states its style."""
    offenders = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        if "caption.style" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for lineno in _styleless_operations(tree):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        # A script the fixture runs inside Resolve is a STRING: its
        # operations are built by the code in that string, so it is
        # surveyed as source too.
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "caption.style" in node.value):
                continue
            try:
                embedded = ast.parse(node.value)
            except SyntaxError:
                continue
            for lineno in _styleless_operations(embedded):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} "
                    f"(script line {lineno})")
    assert offenders == [], (
        "a caption.style operation is built with no style: "
        + ", ".join(offenders)
    )
