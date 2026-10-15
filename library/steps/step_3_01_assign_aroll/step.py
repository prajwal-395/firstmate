"""Legacy pipeline adapter for the capability-owned A-roll assignment."""
from library.capabilities.aroll.assign import (
    assign_a_roll,
    main,
    needs_conform,
    splice_region_aroll,
)
from library.tools.duration_tolerance import DURATION_TOLERANCE

__all__ = (
    "assign_a_roll",
    "main",
    "needs_conform",
    "splice_region_aroll",
    "DURATION_TOLERANCE",
)


if __name__ == "__main__":
    main()
