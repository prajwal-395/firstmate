import os
import sys

from library.tools.resolve_lock import under_lease

@under_lease("check Resolve connection", exclusive=False)
def check_resolve_connection():
    """Check DaVinci Resolve connection health and API initialization.
    
    Returns a dict with:
        success (bool): True if connection is healthy
        error (str): Diagnostic message on failure
        resolve (object): The Resolve scriptapp object if successful
    """
    api_path = os.environ.get("RESOLVE_SCRIPT_API")
    lib_path = os.environ.get("RESOLVE_SCRIPT_LIB")
    
    if not api_path or not lib_path:
        return {
            "success": False,
            "error": "RESOLVE_SCRIPT_API or RESOLVE_SCRIPT_LIB environment variables are not set."
        }
        
    try:
        if api_path not in sys.path and os.path.join(api_path, "Modules") not in sys.path:
            sys.path.append(os.path.join(api_path, "Modules"))
            
        import DaVinciResolveScript as dvr
        from library.tools.resolve_locale import scriptapp_preserving_locale
        resolve = scriptapp_preserving_locale(dvr, "Resolve")
        
        if resolve is None:
            return {
                "success": False,
                "error": "scriptapp('Resolve') returned None. Is DaVinci Resolve running?"
            }
            
        pm = resolve.GetProjectManager()
        if pm is None:
            return {
                "success": False,
                "error": "resolve.GetProjectManager() returned None. Resolve may not be fully initialized."
            }
            
        project = pm.GetCurrentProject()
        if project is None:
            return {
                "success": False,
                "error": "pm.GetCurrentProject() returned None. No project is currently loaded."
            }
            
        return {
            "success": True,
            "error": None,
            "resolve": resolve
        }
    except ImportError as e:
        return {
            "success": False,
            "error": f"Failed to import DaVinciResolveScript: {e}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Unexpected error connecting to DaVinci Resolve: {e}"
        }
