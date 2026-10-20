"""AGENT-016: a refusal that asks the person says WHY.

`needs_question` says THAT a question is needed. It does not say which
of the three things genuinely demands a person: the intent is not
determinate, the change is not authorized, or the available evidence
does not settle it. Without that, a host cannot tell an ambiguous
request from an unauthorized one, and "ask only when it must" is a
habit rather than a rule.

These tests name the defects:

- every refusal family that asks the person names its ask reason, and
  the reason is one of the three (`ren_refusal.ASK_REASONS`);
- every RAISE SITE that asks names its reason too, so a family cannot
  inherit "blocked" and still ask;
- a reason outside the closed vocabulary is refused at the raise site,
  because a typo in a reason is a refusal that cannot be acted on;
- the one helper that decides whether a person is needed answers in
  the order the three reasons bite, and answers "" when nobody is
  needed at all.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from library.tools import ren_refusal

REPO = Path(__file__).resolve().parents[2]
LIBRARY = REPO / "library"


def _python_files() -> list[Path]:
    return sorted(path for path in LIBRARY.rglob("*.py")
                  if "__pycache__" not in path.as_posix())


def _is_refusal_base(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "RenRefusal"
    if isinstance(node, ast.Attribute):
        return node.attr == "RenRefusal"
    return False


def _class_index() -> dict[tuple[str, str], tuple[list[str], dict[str, str]]]:
    """Every class in `library/`, by file and name: its bases and its
    string attributes.

    Keyed by BOTH, because two files may each name a class the same
    (`PatchRefused` lives in `edit_patch.py` and in
    `feedback_patch_apply.py`), and a refusal family is only the one
    that is a `RenRefusal`.
    """
    index: dict[tuple[str, str], tuple[list[str], dict[str, str]]] = {}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [base.id if isinstance(base, ast.Name)
                     else base.attr if isinstance(base, ast.Attribute) else ""
                     for base in node.bases]
            index[(relative, node.name)] = (bases, _class_attributes(node))
    return index


def _is_refusal(name: str, path: str,
                index: dict, seen: set | None = None) -> bool:
    """Whether a class is a `RenRefusal`, directly or through a family."""
    seen = seen if seen is not None else set()
    if (path, name) in seen:
        return False
    seen.add((path, name))
    entry = index.get((path, name))
    if entry is None:
        # A base from another file: find it by name, and prefer one in
        # the same file so a family is not confused with a namesake.
        for other_path, other_name in index:
            if other_name != name:
                continue
            if _is_refusal(name, other_path, index, seen):
                return True
        return False
    bases, _attributes = entry
    for base in bases:
        if base == "RenRefusal":
            return True
        if _is_refusal(base, path, index, seen):
            return True
    return False


def _string_constant(node: ast.AST) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(
        node.value, str) else ""


def _class_attributes(class_node: ast.ClassDef) -> dict[str, str]:
    values: dict[str, str] = {}
    for statement in class_node.body:
        if not isinstance(statement, ast.Assign):
            continue
        for target in statement.targets:
            if isinstance(target, ast.Name):
                values[target.id] = _string_constant(statement.value)
    return values


def _asking_classes() -> list[tuple[str, str]]:
    """Every refusal family whose recovery class asks the person."""
    index = _class_index()
    found: list[tuple[str, str]] = []
    for (path, name), (_bases, attributes) in index.items():
        if attributes.get("RECOVERY_CLASS") != "needs_question":
            continue
        if _is_refusal(name, path, index):
            found.append((path, name))
    return sorted(found)


class _AskFinder(ast.NodeVisitor):
    """Every call that asks the person, the family that made it, and the
    reason it names at the call."""

    def __init__(self, path: str):
        self.path = path
        self.families: list[ast.ClassDef] = []
        self.found: list[tuple[str, int, ast.ClassDef | None, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.families.append(node)
        self.generic_visit(node)
        self.families.pop()

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if (keyword.arg == "recovery_class"
                    and _string_constant(keyword.value) == "needs_question"):
                reason = ""
                for other in node.keywords:
                    if other.arg == "ask_reason":
                        reason = _string_constant(other.value)
                self.found.append((
                    self.path, node.lineno,
                    self.families[-1] if self.families else None, reason))
        self.generic_visit(node)


def _asking_call_sites() -> list[tuple[str, int, ast.ClassDef | None, str]]:
    found: list[tuple[str, int, ast.ClassDef | None, str]] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        finder = _AskFinder(path.relative_to(REPO).as_posix())
        finder.visit(tree)
        found.extend(finder.found)
    return found


def _class_reason(path: str, name: str) -> str:
    """The `ASK_REASON` a refusal family declares."""
    for candidate in _python_files():
        if candidate.relative_to(REPO).as_posix() != path:
            continue
        tree = ast.parse(candidate.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                return _class_attributes(node).get("ASK_REASON", "")
    return ""


def test_the_ask_reasons_are_the_three_things_that_demand_a_person():
    assert ren_refusal.ASK_REASONS == (
        "ambiguous_intent", "missing_authorization", "insufficient_evidence")


def test_every_refusal_family_that_asks_names_its_reason():
    # Defect: a refusal said "needs a question" and left the host to
    # work out whether the person, the evidence or the authorization
    # was the thing missing.
    asking = _asking_classes()
    assert asking, "no refusal family asks the person - the scan is wrong"
    for path, name in asking:
        reason = _class_reason(path, name)
        assert reason in ren_refusal.ASK_REASONS, (
            f"{path}:{name} asks the person without naming why "
            f"(ask_reason={reason!r})")


def test_every_raise_site_that_asks_names_its_reason():
    # A family that inherits "blocked" and asks at a raise site must
    # still say why it is asking there.
    sites = _asking_call_sites()
    assert sites, "no raise site asks the person - the scan is wrong"
    for path, line, class_node, reason in sites:
        if not reason and class_node is not None:
            reason = _class_attributes(class_node).get("ASK_REASON", "")
        assert reason in ren_refusal.ASK_REASONS, (
            f"{path}:{line} asks the person without naming why "
            f"(ask_reason={reason!r})")


def test_a_reason_outside_the_vocabulary_is_refused_at_the_raise_site():
    # A typo in a reason is a refusal no host can act on.
    with pytest.raises(ValueError):
        ren_refusal.RenRefusal("what", "why", "fix",
                               recovery_class="needs_question",
                               ask_reason="ambiguous")


def test_a_refusal_that_does_not_ask_carries_no_reason():
    refused = ren_refusal.RenRefusal("what", "why", "fix")
    assert refused.ask_reason == ""
    assert refused.to_dict()["ask_reason"] == ""


def test_the_helper_answers_in_the_order_the_reasons_bite():
    # An ambiguous intent cannot be answered by more evidence, and an
    # unauthorized change cannot be answered by a clearer intent.
    assert ren_refusal.ask_reason(
        intent_unambiguous=False, authorized=False,
        evidence_sufficient=False) == "ambiguous_intent"
    assert ren_refusal.ask_reason(
        intent_unambiguous=True, authorized=False,
        evidence_sufficient=False) == "missing_authorization"
    assert ren_refusal.ask_reason(
        intent_unambiguous=True, authorized=True,
        evidence_sufficient=False) == "insufficient_evidence"
    assert ren_refusal.ask_reason(
        intent_unambiguous=True, authorized=True,
        evidence_sufficient=True) == ""
