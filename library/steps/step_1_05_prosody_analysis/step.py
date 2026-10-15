"""Legacy pipeline adapter for the prosody.analyse capability."""
from library.capabilities.prosody.analyse import *  # noqa: F403,F401
from library.capabilities.prosody.analyse import (
    analyse_prosody,
    main,
)


__all__ = [
    "analyse_prosody",
    "main",
]


if __name__ == "__main__":
    main()
