"""Legacy pipeline adapter for the duration_zone.build capability."""
from library.capabilities.spine.zone import *  # noqa: F403,F401
from library.capabilities.spine.zone import (
    build_duration_zone,
    main,
)


__all__ = [
    "build_duration_zone",
    "main",
]


if __name__ == "__main__":
    main()
