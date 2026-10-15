"""Legacy import and process entry point for ``ocr.extract``."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.capabilities.ocr import extract as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
