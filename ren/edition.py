"""One edition policy for packaged builds and runtime component gates.

The build records ``public`` or ``personal`` in ``ren/_build.py``.
An unbuilt checkout defaults to ``personal``; ``REN_EDITION`` lets a
developer exercise either policy there. An installed build cannot be
changed by an environment variable.

Every selectable component is declared by ``component-id`` and
``edition`` in ``THIRD_PARTY_NOTICES``. Runtime providers call
``select_component`` with their personal and public implementation IDs.
That is the seam for qualified public alternatives: the public provider
must first be inventoried as ``edition: public``.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PUBLIC = "public"
PERSONAL = "personal"
PERSONAL_ONLY = "personal-only"
EDITIONS = (PUBLIC, PERSONAL)
EDITION_ENV = "REN_EDITION"
NOTICE_FILE = "THIRD_PARTY_NOTICES"
_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")


class EditionError(RuntimeError):
    """The selected edition or component policy is invalid."""


class ComponentRefused(EditionError):
    """The current edition cannot package, fetch, or load a component."""


class ComponentUnavailable(EditionError):
    """No provider for a capability is available in this edition."""


@dataclass(frozen=True)
class Component:
    id: str
    edition: str
    label: str
    package_paths: tuple[str, ...] = ()
    installer_paths: tuple[str, ...] = ()
    requirement_refs: tuple[tuple[str, str], ...] = ()
    package_copies: tuple[tuple[str, str], ...] = ()


def engine_root(root: str | Path | None = None) -> Path:
    """Resolve the selected engine without requiring library imports."""
    if root is not None:
        return Path(root).expanduser().resolve()
    try:
        from ren.engine_root import find_engine_root

        found = find_engine_root()
    except ImportError:
        found = None
    return found or Path(__file__).resolve().parent.parent


def _build_edition(root: Path) -> str | None:
    """Read the literal edition baked into this engine, if it is packaged."""
    path = root / "ren" / "_build.py"
    if not path.is_file():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise EditionError(f"cannot read the Ren build record at {path}: {exc}") from exc
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "BUILD_EDITION"
               for target in statement.targets):
            try:
                value = ast.literal_eval(statement.value)
            except (ValueError, TypeError) as exc:
                raise EditionError(
                    f"the Ren build record at {path} has an invalid edition") from exc
            return _normalise_edition(value)
    # Trees built before edition metadata were introduced retain their
    # historical personal behaviour.
    return PERSONAL


def _normalise_edition(value: object) -> str:
    if not isinstance(value, str) or value not in EDITIONS:
        raise EditionError(
            f"Ren edition must be one of {list(EDITIONS)}, got {value!r}")
    return value


def current_edition(root: str | Path | None = None) -> str:
    """Return the immutable build edition, or the checkout's one setting."""
    selected_root = engine_root(root)
    built = _build_edition(selected_root)
    requested = os.environ.get(EDITION_ENV)
    if built is not None:
        if requested is not None and _normalise_edition(requested) != built:
            raise EditionError(
                f"{EDITION_ENV}={requested!r} cannot change this {built} Ren build")
        return built
    return _normalise_edition(requested) if requested is not None else PERSONAL


def read_components(root: str | Path | None = None) -> dict[str, Component]:
    """Read and validate the component edition inventory."""
    path = engine_root(root) / NOTICE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EditionError(f"cannot read the component inventory at {path}: {exc}") from exc

    blocks = re.split(r"(?=^component-id\s*:)", text, flags=re.MULTILINE)
    components: dict[str, Component] = {}
    for block in blocks:
        if not block.startswith("component-id"):
            continue
        fields: dict[str, list[str]] = {}
        for line in block.splitlines():
            match = re.match(r"^([a-z][a-z-]*)\s*:\s*(.*?)\s*$", line)
            if match:
                fields.setdefault(match.group(1), []).append(match.group(2))

        component_id = _one(fields, "component-id", path)
        if _COMPONENT_ID.fullmatch(component_id) is None:
            raise EditionError(f"invalid component id {component_id!r} in {path}")
        edition = _one(fields, "edition", path)
        if edition not in (PUBLIC, PERSONAL_ONLY):
            raise EditionError(
                f"component {component_id} has invalid edition {edition!r} in {path}")
        labels = fields.get("component", [])
        if len(labels) != 1:
            raise EditionError(
                f"component {component_id} must have one component field in {path}")
        requirement_refs = []
        for reference in fields.get("requirement-ref", []):
            file_name, separator, package_name = reference.partition(":")
            if not separator or not file_name or not package_name:
                raise EditionError(
                    f"component {component_id} has invalid requirement-ref "
                    f"{reference!r} in {path}")
            requirement_refs.append((file_name, package_name))
        package_copies = []
        for copy in fields.get("package-copy", []):
            source, separator, destination = copy.partition("->")
            if not separator or not source.strip() or not destination.strip():
                raise EditionError(
                    f"component {component_id} has invalid package-copy "
                    f"{copy!r} in {path}")
            package_copies.append((source.strip(), destination.strip()))
        if component_id in components:
            raise EditionError(f"duplicate component id {component_id!r} in {path}")
        components[component_id] = Component(
            id=component_id,
            edition=edition,
            label=labels[0],
            package_paths=tuple(fields.get("package-path", ())),
            installer_paths=tuple(fields.get("installer-path", ())),
            requirement_refs=tuple(requirement_refs),
            package_copies=tuple(package_copies),
        )

    if not components:
        raise EditionError(f"no component edition records found in {path}")
    row_count = len(re.findall(r"^component\s*:", text, flags=re.MULTILINE))
    if row_count != len(components):
        raise EditionError(
            f"{path} has {row_count} component rows but {len(components)} "
            "component-id tags; every inventory row needs an edition")
    return components


def _one(fields: dict[str, list[str]], name: str, path: Path) -> str:
    values = fields.get(name, [])
    if len(values) != 1 or not values[0]:
        raise EditionError(f"each component needs one {name} field in {path}")
    return values[0]


def component(component_id: str, root: str | Path | None = None) -> Component:
    """Return an inventoried component or refuse an undeclared dependency."""
    try:
        return read_components(root)[component_id]
    except KeyError as exc:
        raise EditionError(
            f"component {component_id!r} is missing from {NOTICE_FILE}; "
            "add its license and edition row before using it") from exc


def component_allowed(component_id: str, edition: str | None = None,
                      root: str | Path | None = None) -> bool:
    """Whether an inventoried component can be used in the selected edition."""
    selected = _normalise_edition(edition) if edition is not None else current_edition(root)
    entry = component(component_id, root)
    return selected == PERSONAL or entry.edition == PUBLIC


def require_component(component_id: str, *, action: str = "load",
                      root: str | Path | None = None) -> Component:
    """Refuse before a restricted component is packaged, fetched, or loaded."""
    selected = current_edition(root)
    entry = component(component_id, root)
    if selected == PUBLIC and entry.edition == PERSONAL_ONLY:
        raise ComponentRefused(
            f"the public edition cannot {action} personal-only component "
            f"{component_id} ({entry.label}); select its public provider instead")
    return entry


def select_component(capability: str, *, personal_component: str,
                      public_component: str | None,
                      root: str | Path | None = None) -> str:
    """Choose one provider ID, preferring the stronger personal implementation.

    Personal mode tries its personal-only implementation first and then
    the public implementation. Public mode considers only its public
    implementation. If a public alternative has not yet been registered,
    callers receive ``ComponentUnavailable`` before loading or fetching
    the personal component.
    """
    selected = current_edition(root)
    candidates: Iterable[str | None] = (
        (personal_component, public_component)
        if selected == PERSONAL else (public_component,)
    )
    for candidate in candidates:
        if candidate is None:
            continue
        if component_allowed(candidate, selected, root):
            return candidate
    choices = "none registered" if public_component is None else public_component
    raise ComponentUnavailable(
        f"capability {capability!r} has no usable provider in the {selected} "
        f"edition (public provider: {choices}); add and select a public "
        "component in THIRD_PARTY_NOTICES")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("current", help="print the active edition")
    check = commands.add_parser("check", help="check a capability-pack operation")
    check.add_argument("component_id")
    check.add_argument("--action", default="fetch",
                       choices=("package", "fetch", "load"))
    args = parser.parse_args(argv)
    try:
        if args.command == "current":
            print(current_edition())
        else:
            entry = require_component(args.component_id, action=args.action)
            print(f"{current_edition()}: {args.action} allowed for {entry.id}")
    except EditionError as exc:
        print(f"ren edition: refused - {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
