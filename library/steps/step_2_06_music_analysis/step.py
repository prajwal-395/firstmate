#!/usr/bin/env python3
"""Legacy stdin/stdout adapter for the ``music.analyse`` capability."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.capabilities.music.analyse import (  # noqa: E402
    _audio_content_digest,
    _method_hash,
    analyse_music,
)


def main():
    data = json.loads(sys.stdin.read())
    result = analyse_music(
        music_selection=data.get("music_selection", {}),
        project_folder=data.get("project_folder", ""),
    )
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
