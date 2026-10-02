# `library.tools.reel_prebuild_census` - why it measures before the build

Moved from the module docstring of `tests/unit/reels/test_reel_build_runner.py` (2026-10-02).

The 2026-09-11 round decided the caption row from a census that existed only
after a four-reel build, a refusal and a discard: four reels at tilt
-425..-436 against one at -870. The pre-build census reads that same state
before anything is placed - same kind of element, materially different places
- prints it, and lets the build proceed either way.
