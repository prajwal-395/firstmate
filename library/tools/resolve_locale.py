"""Connecting to Resolve resets the process's text encoding. Undo it.

`DaVinciResolveScript.scriptapp("Resolve")` calls `setlocale` down in
Blackmagic's own library and leaves `LC_CTYPE` on `C`.  Python reads
`LC_CTYPE` for `locale.getpreferredencoding()`, which is the default for
`open()`, `Path.read_text()` and `subprocess` with `text=True`.  So after
one `scriptapp` call, every subsequent read of a UTF-8 file without an
explicit encoding raises `UnicodeDecodeError` - and this repository's
step files are full of UTF-8 box-drawing and status glyphs.

Measured on DaVinci Resolve Studio 21.0.0b.28, macOS, 2026-08-28:

    before import        LC_CTYPE en_US.UTF-8   getpreferredencoding UTF-8
    after import         LC_CTYPE en_US.UTF-8   getpreferredencoding UTF-8
    after scriptapp()    LC_CTYPE C            getpreferredencoding US-ASCII

    Path("library/steps/step_1_04_temporal_index/step.py").read_text()
    UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2 in position 251

The import is harmless; it is `scriptapp` that does it.  It is the same
defect class as AGENTS.md 9's rule about `text=True` decoding with the
locale codec, arriving from the other direction: there the caller picked
the wrong codec, here the codec was changed underneath a caller who
picked nothing.

ONLY `LC_CTYPE` IS RESTORED.  `LC_NUMERIC` measured `C` both before and
after, so Python never set it and Resolve did not change it - restoring a
category nothing touched could hand fusionscript a decimal comma on a
machine whose locale uses one, which would corrupt every number crossing
the boundary.  Restore what was broken and nothing else.

WHO GOES THROUGH THE WRAPPER. This ledger is here rather than in
AGENTS.md 9, which keeps the rule and points at it. Every in-repository
Resolve scripting handshake goes through this wrapper. It also refuses
to connect unless the caller already holds `resolve_lease`, so the
bounded wait happens before Resolve sees a second client. The AST gate
in `tests/contracts/test_resolve_guard_wiring.py` pins the single raw call site.
"""

from __future__ import annotations

import locale


def _configured_script_paths():
    """The Scripting, Modules and fusionscript paths for this machine."""
    import os
    from pathlib import Path

    from library.tools.paths import RESOLVE_SCRIPT_API, RESOLVE_SCRIPT_LIB

    api_value = os.environ.get("RESOLVE_SCRIPT_API", "").strip()
    api_path = Path(api_value or RESOLVE_SCRIPT_API).expanduser()
    if api_path.name == "Modules":
        modules_path = api_path
        api_path = api_path.parent
    else:
        modules_path = api_path / "Modules"

    lib_value = os.environ.get("RESOLVE_SCRIPT_LIB", "").strip()
    lib_path = lib_value or os.fspath(RESOLVE_SCRIPT_LIB)
    return api_path, modules_path, lib_path


def resolve_script_modules_path() -> str:
    """The configured directory containing DaVinciResolveScript.py."""
    import os

    return os.fspath(_configured_script_paths()[1])


def load_resolve_script():
    """Import DaVinciResolveScript from the configured Resolve install.

    `RESOLVE_SCRIPT_API` and `RESOLVE_SCRIPT_LIB` may be supplied by the
    caller or by `library.tools.paths`' user/checkout configuration. When
    neither supplies them, the standard macOS paths declared there are
    used. Resolve's Python module lives in the API directory's `Modules`
    child, which must be on `sys.path` before import.

    Keep this import lazy: many callers also run on machines without
    Resolve, and importing their module must not load Blackmagic's native
    library.
    """
    import importlib
    import os
    import sys

    # A caller may have installed a Resolve double (or already imported
    # the bindings). In that case the module is the connection boundary;
    # resolving machine paths first would turn a usable double into an
    # installation prerequisite.
    loaded = sys.modules.get("DaVinciResolveScript")
    if loaded is not None:
        return loaded

    api_path, modules_path, lib_path = _configured_script_paths()

    # fusionscript reads these during import. Set them before loading the
    # module, even when they came from the standard path configuration, then
    # restore the caller's environment after import.
    env_keys = ("RESOLVE_SCRIPT_API", "RESOLVE_SCRIPT_LIB")
    prior_environment = {key: os.environ.get(key) for key in env_keys}
    modules = os.fspath(modules_path)
    added_modules_path = modules not in sys.path
    if added_modules_path:
        sys.path.insert(0, modules)
    try:
        os.environ["RESOLVE_SCRIPT_API"] = os.fspath(api_path)
        os.environ["RESOLVE_SCRIPT_LIB"] = lib_path
        importlib.invalidate_caches()
        return importlib.import_module("DaVinciResolveScript")
    finally:
        try:
            for key, value in prior_environment.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        finally:
            if added_modules_path:
                try:
                    sys.path.remove(modules)
                except ValueError:  # pragma: no cover - changed by another thread
                    pass


def scriptapp_preserving_locale(dvr, name: str = "Resolve"):
    """`dvr.scriptapp(name)`, with `LC_CTYPE` put back afterwards.

    Every route into Resolve should come through here with the instance
    lease already held. The return value is `scriptapp`'s own - None
    when Resolve is not running - and is not interpreted.
    """
    from library.tools.resolve_lock import held

    if not held():
        raise RuntimeError(
            "refusing Resolve scriptapp handshake outside the instance "
            "lease; take resolve_lease before connecting")
    try:
        before = locale.setlocale(locale.LC_CTYPE)
    except locale.Error:  # pragma: no cover - a locale we cannot read back
        before = None
    try:
        return dvr.scriptapp(name)
    finally:
        if before is not None:
            try:
                locale.setlocale(locale.LC_CTYPE, before)
            except locale.Error:  # pragma: no cover - refused the round trip
                pass


class LazyResolveScript:
    """DaVinciResolveScript, imported when `scriptapp` is CALLED.

    Importing the bindings loads Blackmagic's fusionscript.so, so a
    module-level `import DaVinciResolveScript` puts it into every process
    that merely imports the module holding it - every test worker among
    them (tests/conftest.py). Hold one of these instead and pass it to
    `scriptapp_preserving_locale` like the module: reading or patching
    `.scriptapp` loads nothing, and a machine without Resolve raises at
    the call, not at import.
    """

    def scriptapp(self, name: str = "Resolve"):
        try:
            dvr = load_resolve_script()
        except ImportError as exc:
            raise RuntimeError(
                "DaVinciResolveScript is not installed or not found on "
                f"the configured Resolve scripting path: {exc}") from exc
        return dvr.scriptapp(name)
