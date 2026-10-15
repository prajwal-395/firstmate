"""Legacy pipeline adapter for the temporal.index capability."""
from library.capabilities.temporal.index import *  # noqa: F403,F401
from library.capabilities.temporal.index import (
    build_temporal_index,
    index_project,
    main,
    reindex_region,
    splice_region_index,
)


__all__ = [
    "build_temporal_index",
    "index_project",
    "main",
    "reindex_region",
    "splice_region_index",
]


if __name__ == "__main__":
    main()
