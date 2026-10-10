"""Edition registration for the pinned native engine integrations.

Both craft engine integrations run headless behind Ren-owned adapters
(``native/ren-filmcraft``, ``native/ren-effectcraft``) over one Ren model
and adapter protocol in both product editions. Their component rows live
in ``THIRD_PARTY_NOTICES``; this module is the Python seam that resolves
an engine name to its inventoried component and refuses unregistered or
edition-disallowed use before Ren fetches, builds, or loads the engine.

The engine UIs stay unavailable in Ren: ``INTAKE_PACKAGES`` names the only
upstream packages Ren ever builds (headless CLIs plus the named test
packages), and ``UI_PACKAGES`` names the desktop/web/UI packages Ren never
builds, packages, or invokes. ``library/tools/local_ci/upstream.py``
builds from ``INTAKE_PACKAGES`` so the exclusion is structural, not prose.
"""

from __future__ import annotations

from ren.edition import Component, require_component

ENGINE_COMPONENTS = {
    "filmcraft": "engine.filmcraft_cli",
    "effectcraft": "engine.effectcraft_cli",
}

#: Upstream packages Ren builds during intake: the headless CLI plus the
#: named test packages the qualification gate runs. No desktop, web, or
#: UI package may appear here.
INTAKE_PACKAGES = {
    "filmcraft": ("filmcraft-cli", "filmcraft-interchange", "filmcraft-golden"),
    "effectcraft": ("effectcraft-cli", "effectcraft-effects"),
}

#: Upstream desktop, web, and UI-frontend packages Ren never builds,
#: packages, or invokes in either edition.
UI_PACKAGES = {
    "filmcraft": ("filmcraft", "filmcraft-web", "filmcraft-ui-egui"),
    "effectcraft": ("effectcraft", "effectcraft-web", "effectcraft-ui-egui"),
}


def component_id(engine: str) -> str:
    """Return the inventoried component id for an engine or refuse it."""
    try:
        return ENGINE_COMPONENTS[engine]
    except KeyError as exc:
        raise ValueError(
            f"unknown engine {engine!r}; expected one of "
            f"{', '.join(sorted(ENGINE_COMPONENTS))}") from exc


def require_engine(engine: str, *, action: str = "load") -> Component:
    """Refuse before an unregistered or edition-disallowed engine is used."""
    return require_component(component_id(engine), action=action)


def intake_packages(engine: str) -> tuple[str, ...]:
    """Return the upstream packages Ren may build for an engine."""
    try:
        return INTAKE_PACKAGES[engine]
    except KeyError as exc:
        raise ValueError(
            f"unknown engine {engine!r}; expected one of "
            f"{', '.join(sorted(INTAKE_PACKAGES))}") from exc
