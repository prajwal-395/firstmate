#!/usr/bin/env python3
"""4.01 post-bridge: verify each caption correction against timed speech."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools.caption_feedback import resolve_feedback


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
        print(json.dumps(resolve_feedback(data)))
    except Exception:
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
