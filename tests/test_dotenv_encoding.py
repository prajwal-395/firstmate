"""The .env must load under an ASCII locale.

This is why project 001's first complete render carried no Fusion effects
at all. `library/tools/paths.py` opened the .env with no encoding, so it
decoded with `locale.getpreferredencoding()` - ASCII inside the Python
that Resolve's Fusion subprocess runs. The shipped .env has box-drawing
characters in its section headers, so importing `library.tools.paths`
raised:

    UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2

`apply_fusion_comps` died at import, before drawing anything. The render
still produced an mp4 - it was simply missing every planned effect, and
the only reason anyone knows is that `verify_fusion_comps` reported
"expected 7 clips carrying a Fusion comp, got 0".

AGENTS.md records this hazard for `subprocess` text decoding. It is the
same hazard for every file read.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The exact bytes that broke it: BOX DRAWINGS LIGHT HORIZONTAL is 0xe2 0x94 0x80.
ENV_WITH_NON_ASCII = (
    "# ─── Pipeline Configuration ───\n"
    "PIPELINE_TEST_DOTENV_PROBE=/tmp/sfx\n"
    "# café - a stray accent is enough too\n"
    "PIPELINE_MUSIC_LIBRARY=/tmp/music\n"
)


def test_the_shipped_env_loader_names_its_encoding():
    src = (REPO_ROOT / "library" / "tools" / "paths.py").read_text()
    assert 'open(env_path, encoding="utf-8")' in src, (
        "the .env loader must not decode with the locale default")


def test_a_non_ascii_env_loads_under_an_ascii_locale(tmp_path):
    """Run the loader in a subprocess pinned to an ASCII locale.

    Importing `library.tools.paths` at all is half the test: the module
    loads the REPO's own .env at import time, and that file is the one
    with the box-drawing headers. Under LC_ALL=C the unfixed loader
    raises before this test's own file is ever read.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_WITH_NON_ASCII, encoding="utf-8")

    program = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, r"{REPO_ROOT}")
        from library.tools.paths import _load_dotenv
        from pathlib import Path
        import os
        _load_dotenv(Path(r"{env_file}"))
        print(os.environ.get("PIPELINE_TEST_DOTENV_PROBE", "MISSING"))
    """)

    proc = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        # PYTHONUTF8=0 is what makes this bite: without it CPython's
        # UTF-8 mode keeps locale.getpreferredencoding() at utf-8 even
        # under LC_ALL=C, and the test cannot fail. Verified: with it,
        # the preferred encoding is US-ASCII, which is what Resolve's
        # Fusion subprocess had.
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C",
             "PYTHONUTF8": "0"},
    )

    assert proc.returncode == 0, (
        f"the loader failed under an ASCII locale:\n{proc.stderr}")
    assert "UnicodeDecodeError" not in proc.stderr
    assert proc.stdout.strip() == "/tmp/sfx"
