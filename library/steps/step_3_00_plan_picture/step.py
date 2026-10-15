#!/usr/bin/env python3
"""Legacy stdin/stdout adapter for the ``picture.plan`` capability."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.capabilities.picture.plan import main  # noqa: E402


if __name__ == "__main__":
    main()
