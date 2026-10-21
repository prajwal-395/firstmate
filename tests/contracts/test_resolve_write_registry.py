"""Every statically dispatched Resolve mutation is present in the P0.1 registry.

The registry is keyed by file, enclosing function, method, and occurrence within
that function. Line numbers are excluded from identity so routine edits do not
churn the inventory. Dynamic dispatch is listed separately in the inventory.

The enumeration of the call sites lives with the inventory that renders it
(`library/tools/resolve_write_inventory.py`): the registry, the document's
static table and this gate all read it, so a call site cannot be registered in
one and missing from another.
"""
import json
from pathlib import Path

from library.tools import resolve_write_inventory

REGISTRY = Path(__file__).with_name("resolve_write_registry.json")


def _sites():
    return {tuple(row) for row in resolve_write_inventory.registry_rows()}


def test_production_resolve_mutations_are_registered():
    expected = {tuple(row) for row in json.loads(REGISTRY.read_text())}
    actual = _sites()
    missing = sorted(actual - expected)
    stale = sorted(expected - actual)
    assert not missing and not stale, (
        "Resolve write inventory changed. Classify each new call site and "
        "regenerate docs/RESOLVE_WRITE_INVENTORY.md; remove stale rows. "
        f"Unregistered={missing}; stale={stale}")


def test_the_inventory_document_names_every_registered_call_site():
    """The document and the registry are the same inventory.

    A call site the registry knows and the document does not name is a
    call site nobody classified: the registry says it was counted and
    the document says nothing about it.
    """
    document = (Path(__file__).resolve().parents[2] / "docs"
                / "RESOLVE_WRITE_INVENTORY.md").read_text(encoding="utf-8")
    for row in json.loads(REGISTRY.read_text()):
        path, function, method, occurrence = row
        assert f"`{path}`" in document, (
            f"{path} is registered but the inventory document never names it")
        assert f"(`{function}`, occurrence {occurrence} of `{method}`)" in document, (
            f"{path}: {function} occurrence {occurrence} of {method} is "
            "registered but the inventory document never names it")
