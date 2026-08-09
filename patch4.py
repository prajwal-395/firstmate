import os
import sys

file_path = "/Users/prajwal/.treehouse/video_editing_pilot-9487f5/3/video_editing_pilot/library/tools/execution/build_powergrade.py"

with open(file_path, "r") as f:
    content = f.read()

old_get_resolve = """def get_resolve():
    \"\"\"Connect to DaVinci Resolve scripting API.\"\"\"
    try:
        # Method 1: Direct import (works when run from Resolve console)
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        pass

    try:
        # Method 2: Add the scripting module path
        script_module = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        if script_module not in sys.path:
            sys.path.insert(0, script_module)
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        pass

    try:
        # Method 3: Environment variable path
        resolve_script = os.getenv("RESOLVE_SCRIPT_API")
        if resolve_script:
            sys.path.insert(0, os.path.join(resolve_script, "Modules"))
            import DaVinciResolveScript as dvr
            return dvr.scriptapp("Resolve")
    except ImportError:
        pass

    print("ERROR: Could not connect to DaVinci Resolve.")
    print("Make sure Resolve is running and the scripting API is accessible.")
    sys.exit(1)"""

new_get_resolve = """def get_resolve():
    \"\"\"Connect to DaVinci Resolve scripting API.\"\"\"
    try:
        from library.tools.resolve_health import check_resolve_connection
        os.environ.setdefault(
            "RESOLVE_SCRIPT_API",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
        )
        os.environ.setdefault(
            "RESOLVE_SCRIPT_LIB",
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
        )
        health = check_resolve_connection()
        if health.get("success"):
            return health["resolve"]
    except ImportError:
        pass

    try:
        # Method 1: Direct import (works when run from Resolve console)
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        pass

    try:
        # Method 2: Add the scripting module path
        script_module = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        if script_module not in sys.path:
            sys.path.insert(0, script_module)
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        pass

    try:
        # Method 3: Environment variable path
        resolve_script = os.getenv("RESOLVE_SCRIPT_API")
        if resolve_script:
            sys.path.insert(0, os.path.join(resolve_script, "Modules"))
            import DaVinciResolveScript as dvr
            return dvr.scriptapp("Resolve")
    except ImportError:
        pass

    print("ERROR: Could not connect to DaVinci Resolve.")
    print("Make sure Resolve is running and the scripting API is accessible.")
    sys.exit(1)"""

content = content.replace(old_get_resolve, new_get_resolve)

with open(file_path, "w") as f:
    f.write(content)
print("Patch 4 completed.")
