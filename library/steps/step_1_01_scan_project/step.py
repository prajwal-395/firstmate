"""Legacy pipeline adapter for the footage.scan capability."""
import json
import os
import sys

from library.capabilities.footage.scan import scan_project_folder as _scan


def scan_project_folder(project_folder: str) -> dict:
    """Preserve the adapter's explicit required-input refusal, then delegate."""
    if not project_folder or not os.path.exists(project_folder):
        raise FileNotFoundError(
            f"Project folder does not exist: {project_folder}")
    return _scan(project_folder)


def main():
    """Keep the runner's JSON adapter at the legacy step entry point."""
    input_data = json.loads(sys.stdin.read())
    project_folder = input_data.get("project_folder")
    if not project_folder:
        print(json.dumps({
            "error": "Missing required input: project_folder",
            "step": "1.1_scan_project_folder"
        }))
        sys.exit(1)
    try:
        result = scan_project_folder(project_folder)
    except (FileNotFoundError, NotADirectoryError,
            PermissionError, ValueError) as e:
        print(json.dumps({
            "error": str(e),
            "step": "1.1_scan_project_folder"
        }))
        sys.exit(1)
    json.dump(result, sys.stdout, indent=2)

__all__ = ["main", "scan_project_folder"]

if __name__ == "__main__":
    main()
