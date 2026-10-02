#!/usr/bin/env python3
"""Project the safe-area enumeration into the Remotion studio's defaults.

`library/tools/safe_area.py` is the one enumeration and every real render
is handed its values in props. The Remotion STUDIO has no pipeline behind
it, so its `defaultProps` need the numbers written down somewhere the
TypeScript can import - and a second hand-written margin is exactly what
the captain's ruling of 2026-08-25 forbade.

So this writes them, from the enumeration, into a generated TypeScript
module. `tests/unit/captions/test_caption_layout.py` fails if that file drifts.

    python3 scripts/generate_safe_area_defaults.py
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from library.tools.safe_area import safe_area_for_format  # noqa: E402

# The studio previews the default delivery format and nothing else.
PREVIEW_FORMAT = "vertical_1080x1920"
OUTPUT = os.path.join(
    REPO_ROOT, "remotion-subtitles", "src", "safeArea.generated.ts")

TEMPLATE = """// GENERATED - do not edit.
//
// A projection of library/tools/safe_area.py's {format} profile, for
// the Remotion STUDIO's preview defaults only. Every real render is handed
// these values in its props by the pipeline; the studio has no pipeline
// behind it, and a second hand-written margin here is what the captain's
// ruling of 2026-08-25 forbade.
//
// Regenerate with: python3 scripts/generate_safe_area_defaults.py
// tests/unit/captions/test_caption_layout.py fails if this drifts.

export const DELIVERY_FORMAT = "{format}";

export const SAFE_AREA = {{
  top: {top},
  right: {right},
  bottom: {bottom},
  left: {left},
}};

export const CAPTION_MAX_WIDTH = {caption_max_width};
"""


def render() -> str:
    insets = safe_area_for_format(PREVIEW_FORMAT)
    return TEMPLATE.format(
        format=PREVIEW_FORMAT,
        top=insets.top,
        right=insets.right,
        bottom=insets.bottom,
        left=insets.left,
        caption_max_width=insets.centered_usable_width,
    )


def main() -> None:
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(render())
    print(f"wrote {os.path.relpath(OUTPUT, REPO_ROOT)}")


if __name__ == "__main__":
    main()
