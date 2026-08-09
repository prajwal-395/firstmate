import os
import sys

file_path = "/Users/prajwal/.treehouse/video_editing_pilot-9487f5/3/video_editing_pilot/library/tools/resolve_project_sync.py"

with open(file_path, "r") as f:
    content = f.read()

old_get_resolve = """def _get_resolve():
    \"\"\"Get a connection to DaVinci Resolve via the scripting API.

    Returns the resolve object or None if Resolve is not running.
    \"\"\"
    try:
        script_modules = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        if script_modules not in sys.path:
            sys.path.append(script_modules)

        os.environ.setdefault(
            "RESOLVE_SCRIPT_API",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
        )
        os.environ.setdefault(
            "RESOLVE_SCRIPT_LIB",
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
        )

        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        return resolve
    except (ImportError, Exception):
        return None"""

new_get_resolve = """def _get_resolve():
    \"\"\"Get a connection to DaVinci Resolve via the scripting API.

    Returns the resolve object or None if Resolve is not running.
    \"\"\"
    try:
        script_modules = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        if script_modules not in sys.path:
            sys.path.append(script_modules)

        os.environ.setdefault(
            "RESOLVE_SCRIPT_API",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
        )
        os.environ.setdefault(
            "RESOLVE_SCRIPT_LIB",
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
        )

        try:
            from library.tools.resolve_health import check_resolve_connection
            health = check_resolve_connection()
            if health.get("success"):
                return health["resolve"]
        except ImportError:
            pass

        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        return resolve
    except (ImportError, Exception):
        return None"""

content = content.replace(old_get_resolve, new_get_resolve)

with open(file_path, "w") as f:
    f.write(content)
print("Patch 3 completed.")
