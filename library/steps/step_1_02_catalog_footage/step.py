"""Legacy pipeline adapter for the footage.catalog capability."""
import json
import sys

from library.capabilities.footage.catalog import *  # noqa: F403
from library.capabilities.footage.catalog import catalog_footage


def main():
    """Keep the legacy JSON interface and delegate catalog execution."""
    input_data = json.loads(sys.stdin.read())
    raw_footage_files = input_data.get("raw_footage_files")
    if not raw_footage_files:
        print(json.dumps({
            "error": "Missing required input: raw_footage_files",
            "step": "1.2_catalog_footage"
        }))
        sys.exit(1)
    try:
        result = catalog_footage(
            raw_footage_files,
            program_stream=input_data.get("program_stream"),
            project_config=input_data.get("project_config"),
            project_folder=input_data.get("project_folder", ""),
            measure_program_stream=input_data.get("measure_program_stream"),
            raw_audio_files=input_data.get("raw_audio_files"),
            model_revision=input_data.get("model_revision"),
        )
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({
            "error": str(exc),
            "step": "1.2_catalog_footage"
        }))
        sys.exit(1)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
