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
"""

from __future__ import annotations

import locale


def scriptapp_preserving_locale(dvr, name: str = "Resolve"):
    """`dvr.scriptapp(name)`, with `LC_CTYPE` put back afterwards.

    Every route into Resolve should come through here.  The return value
    is `scriptapp`'s own - None when Resolve is not running - and is not
    interpreted.
    """
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
