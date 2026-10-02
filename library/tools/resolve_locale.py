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
in `tests/test_resolve_guard_wiring.py` pins the single raw call site.
"""

from __future__ import annotations

import locale


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
            import DaVinciResolveScript as dvr
        except ImportError as exc:
            raise RuntimeError(
                "DaVinciResolveScript is not installed or not found on "
                f"PYTHONPATH: {exc}") from exc
        return dvr.scriptapp(name)
