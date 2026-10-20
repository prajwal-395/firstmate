"""Every op the protocol declares is handled by every dispatch over it.

The native edit backend is the Rust extension `ren_native`
(`native/ren-core`), built by `scripts/build_ren_native.sh` and selected
by `library/tools/edit_backend.py`. Rust refuses to compile a match that
does not cover its enum, so an op a dispatch does not handle is not a
wrong answer - it is NO extension at all, and the whole editorial core
falls back to Python.

That is what the break test found. PR 2883 added `EditOp::OverlayReplace`
to the protocol (`native/ren-protocol/src/edit.rs`) and to the core's
`span`, `key` and `op_unique_id` dispatches, but not to `op_frame`. The
crate stopped compiling (E0004, "non-exhaustive patterns:
`&EditOp::OverlayReplace { .. }` not covered"), `native_refresh`
reported "build failed (exit 1); kept the previous build", and the
extension the backend wanted was never produced. The op SET was already
checked (`tests/unit/resolve/test_protocol_conformance.py`); the
dispatches over that set were not, and that is where the defect lived.

The compiler was always going to catch it - the core says so itself
(`native/ren-core/src/lib.rs`: "Every dispatch matches over the `EditOp`
enum EXHAUSTIVELY (no wildcard arm), so adding a variant fails
compilation"). What was missing was a gate that reads the same
declaration where the defect is visible, and without a Rust toolchain:
this one reads the enum and the dispatches, and names the function and
the op it does not handle.

The rule it holds: a dispatch over `EditOp` that names no wildcard arm
is exhaustive by the repo's own contract, so it must name every declared
op. A dispatch WITH a wildcard arm is a deliberate partial match
(`verify_property` reads two variants of many) and is not this rule's
business.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_EDIT = REPO_ROOT / "native" / "ren-protocol" / "src" / "edit.rs"
NATIVE_SRC = REPO_ROOT / "native"

#: A variant declaration: the name at the enum's own indent, followed by
#: its field block or by nothing.
_VARIANT = re.compile(r"^    ([A-Z][A-Za-z0-9_]*)\s*(\{|,|$)")

#: A function-like block: `fn`, `pub fn` or `pub(crate) fn`, with the
#: indent it is written at (a method of an `impl` block is indented).
_FUNCTION = re.compile(r"^(\s*)(?:pub(?:\(crate\))? )?fn ([A-Za-z0-9_]+)")

#: A match arm over the op enum, and the wildcard arm that makes a match
#: deliberately partial.
_ARM = re.compile(r"^\s*EditOp::")
_WILDCARD = re.compile(r"^\s*_ (?:=>|if )")

#: Every op named in a pattern or an expression.
_NAMED_OP = re.compile(r"\bEditOp::([A-Z][A-Za-z0-9_]*)")

#: The dispatches the core and the protocol declare exhaustive. A
#: dispatch that stops being one (a wildcard arm, a rename, a move)
#: drops out of the discovered set, and this list is what says so -
#: without it the gate above would go quietly blind.
EXPECTED_DISPATCHES = frozenset({
    "native/ren-protocol/src/edit.rs::op_id",
    "native/ren-core/src/algebra.rs::span",
    "native/ren-core/src/algebra.rs::key",
    "native/ren-core/src/algebra.rs::op_unique_id",
    "native/ren-core/src/algebra.rs::op_frame",
    "native/ren-core/src/verify.rs::verify",
})


def declared_ops() -> list[str]:
    """The op variants `native/ren-protocol/src/edit.rs` declares."""
    lines = PROTOCOL_EDIT.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines)
                 if line.startswith("pub enum EditOp {"))
    end = next(index for index, line in enumerate(lines)
               if index > start and line == "}")
    declared: list[str] = []
    for line in lines[start + 1:end]:
        if not line.startswith("    ") or line.startswith("     "):
            continue
        stripped = line.strip()
        if stripped.startswith(("//", "#", "}")):
            continue
        match = _VARIANT.match(line)
        if match is not None:
            declared.append(match.group(1))
    return declared


def _blocks(path: Path) -> list[tuple[str, int, list[str]]]:
    """`(name, line number, body)` for every function-like block.

    A block runs from its `fn` line to the line that closes it at the
    same indent - the shape rustfmt gives the workspace's sources.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[tuple[str, int, list[str]]] = []
    index = 0
    while index < len(lines):
        match = _FUNCTION.match(lines[index])
        if match is None:
            index += 1
            continue
        indent = match.group(1)
        end = index + 1
        while end < len(lines) and lines[end] != indent + "}":
            end += 1
        blocks.append((match.group(2), index + 1, lines[index:end + 1]))
        index = end + 1
    return blocks


def dispatches() -> list[tuple[str, str, int, set[str]]]:
    """`(relative path, name, line, ops named)` for every dispatch over
    the op enum that names no wildcard arm."""
    found: list[tuple[str, str, int, set[str]]] = []
    for path in sorted(NATIVE_SRC.rglob("*.rs")):
        if "target" in path.relative_to(NATIVE_SRC).parts:
            continue
        for name, line, body in _blocks(path):
            if not any(_ARM.match(text) for text in body):
                continue
            if any(_WILDCARD.match(text) for text in body):
                continue
            named: set[str] = set()
            for text in body:
                named.update(_NAMED_OP.findall(text))
            found.append((path.relative_to(REPO_ROOT).as_posix(),
                          name, line, named))
    return found


def test_every_declared_op_is_handled_by_every_exhaustive_dispatch():
    """A dispatch over the op enum that names no wildcard arm must name
    every op the protocol declares.

    The gap this holds is the one the break test found: `op_frame` did
    not name `EditOp::OverlayReplace`, so the core did not compile, so
    the extension was never built.
    """
    declared = declared_ops()
    assert declared, (
        "the op enum yielded no variants - the reader is broken, and this "
        "gate would pass on anything")

    gaps: list[str] = []
    for path, name, line, named in dispatches():
        missing = [op for op in declared if op not in named]
        if missing:
            gaps.append(
                f"{path}:{line} {name}() names {len(named)} of "
                f"{len(declared)} ops and handles no {', '.join(missing)}")
    assert not gaps, (
        "an op the protocol declares is handled by no dispatch over it, so "
        "the Rust core does not compile, so the ren_native extension is "
        "never built and the editorial core falls back to Python:\n  "
        + "\n  ".join(gaps))


def test_the_exhaustive_dispatches_are_still_found():
    """The dispatches the core declares exhaustive are still dispatches.

    Without this, a rename or a wildcard arm would move a dispatch out
    of the gate above and the gate would pass on a tree it cannot read.
    """
    found = {f"{path}::{name}" for path, name, _line, _named in dispatches()}
    missing = EXPECTED_DISPATCHES - found
    assert not missing, (
        "these dispatches over the op enum are no longer found, so the "
        "gate above cannot see them: "
        + ", ".join(sorted(missing)))
