"""Legacy import and process entry point for ``objects.segment``."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.capabilities.objects import segment as _impl

if __name__ == "__main__":
    _impl.main()
else:
    # Keep old imports, including test/client monkeypatches, bound to the
    # capability module's globals rather than a copied namespace.
    sys.modules[__name__] = _impl
