"""Legacy pipeline adapter for the spine.mesh capability."""
from library.capabilities.spine.mesh import *  # noqa: F403,F401
from library.capabilities.spine.mesh import (
    BED_ENTRY_KEYS,
    DECISION_ENTRY_KEYS,
    PACING_SLOT,
    PACING_WINDOW_KEYS,
    SPINE_BLOCK_KEYS,
    _attach_handles,
    _resolve_pacing,
    enrich_spine,
    main,
    resolve_spine,
)


__all__ = [
    "BED_ENTRY_KEYS",
    "DECISION_ENTRY_KEYS",
    "PACING_SLOT",
    "PACING_WINDOW_KEYS",
    "SPINE_BLOCK_KEYS",
    "_attach_handles",
    "_resolve_pacing",
    "enrich_spine",
    "main",
    "resolve_spine",
]


if __name__ == "__main__":
    main()
