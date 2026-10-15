"""Legacy pipeline adapter for the semantics.analyse capability."""
import json
import sys

from library.capabilities.semantics.analyse import *  # noqa: F403
from library.capabilities.semantics.analyse import (
    VisionPipelineMissing,
    _analyse_missing,
    _is_collision_duplicate,
    _profile_stems,
    _run_clip_vision,
    analyse_semantics,
)


def main():
    """Preserve the legacy stdin/stdout boundary and delegate analysis."""
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    try:
        from library.tools.semantic_profile_stream import current_run_context
        stream_context = current_run_context(data.get("project_folder", ""))
        result = analyse_semantics(
            raw_footage_files=data.get("raw_footage_files", []),
            project_folder=data.get("project_folder", ""),
            clip_catalog=data["clip_catalog"],
            stream_run_id=(stream_context["run_id"]
                           if stream_context["expected"] else ""),
        )
    except VisionPipelineMissing:
        sys.exit(1)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
