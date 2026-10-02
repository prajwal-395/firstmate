# The .env must load under an ASCII locale

Moved from `tests/unit/context/test_dotenv_encoding.py`.

This is why project 001's first complete render carried no Fusion effects at
all. `library/tools/paths.py` opened the .env with no encoding, so it decoded
with `locale.getpreferredencoding()` - ASCII inside the Python that Resolve's
Fusion subprocess runs. The shipped .env has box-drawing characters in its
section headers, so importing `library.tools.paths` raised:

    UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2

`apply_fusion_comps` died at import, before drawing anything. The render
still produced an mp4 - it was simply missing every planned effect, and the
only reason anyone knows is that `verify_fusion_comps` reported "expected 7
clips carrying a Fusion comp, got 0".

AGENTS.md records this hazard for `subprocess` text decoding. It is the same
hazard for every file read. The test runs the loader in a subprocess under
`LC_ALL=C` with `PYTHONUTF8=0` (without the latter CPython's UTF-8 mode keeps
the preferred encoding at utf-8 and the test cannot fail).
