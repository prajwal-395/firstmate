"""Session-local AST parsing shared by repository source analyzers."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

_TREES: dict[tuple[str, str], ast.Module] = {}
_PARSE_COUNT = 0


def parse_source(source: str, *, path: str | Path = "") -> ast.Module:
    """Parse source once per path and content for this Python process."""
    global _PARSE_COUNT
    identity = str(Path(path).resolve()) if path else ""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    key = (identity, digest)
    tree = _TREES.get(key)
    if tree is None:
        tree = ast.parse(source)
        _TREES[key] = tree
        _PARSE_COUNT += 1
    return tree


def parse_file(path: str | Path) -> ast.Module:
    """Read and parse a Python file through the shared cache."""
    source_path = Path(path)
    return parse_source(source_path.read_text(encoding="utf-8"), path=source_path)


def parse_count() -> int:
    """Number of actual ``ast.parse`` calls made by this module."""
    return _PARSE_COUNT


def clear_cache() -> None:
    """Clear cached trees and counter, primarily for isolated tests."""
    global _PARSE_COUNT
    _TREES.clear()
    _PARSE_COUNT = 0
