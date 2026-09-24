"""HyperFrames compositions render with no network.

The defect this names is the webfont race (`docs/RULE_EVIDENCE.md`):
a composition that loads GSAP or a typeface over HTTP renders whenever
the request wins the race, and neither the frames nor anything
downstream can tell a fallback face from the real one. The spike
rendered its parity frames vendored for exactly this reason, so the
templates assert it structurally: no remote URL anywhere, and the
vendored GSAP plus the bundled Montserrat beside the comps.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import hyperframes_render as hf  # noqa: E402

_REMOTE = re.compile(r"""https?://|src\s*=\s*["']//""", re.IGNORECASE)

#: Identifiers that look remote but are never fetched: the SVG namespace
#: is an element-creation token (`createElementNS`), not a request.
#: Anything else matching `_REMOTE` is a race with the network.
REMOTE_ALLOWLIST = ("http://www.w3.org/2000/svg",)


def _templates() -> list:
    root = hf.hyperframes_dir()
    found = sorted((root / "compositions").glob("*.html"))
    assert found, f"no HyperFrames templates under {root / 'compositions'}"
    return found


def test_no_template_reaches_the_network():
    offenders = []
    for template in _templates():
        text = template.read_text(encoding="utf-8")
        for allowed in REMOTE_ALLOWLIST:
            text = text.replace(allowed, "")
        if _REMOTE.search(text):
            offenders.append(template.name)
    assert not offenders, (
        f"{offenders} load something over the network; vendor it beside "
        f"the comp (GSAP) or stage it per card (fonts, brand files).")


def test_gsap_is_vendored_at_the_pinned_version():
    vendor = hf.hyperframes_dir() / "vendor" / "gsap.min.js"
    assert vendor.is_file(), (
        f"vendored {vendor} is missing; a render without it races the CDN.")
    assert hf.GSAP_VERSION_PIN in vendor.read_text(
        encoding="utf-8")[:600], (
        f"vendored GSAP is not {hf.GSAP_VERSION_PIN}; the spike measured "
        f"seeks on the pinned version.")


def test_bundled_montserrat_is_staged_from_the_repo():
    assert hf.bundled_font_file().is_file(), (
        "the Montserrat both engines draw from is missing from "
        "remotion-subtitles/public/fonts/.")


def test_every_listed_composition_has_its_template():
    missing = [name for name in hf.HYPERFRAMES_COMPOSITIONS
               if not hf.template_path(name).is_file()]
    assert not missing, (
        f"{missing} are listed as ported but have no template; "
        f"hyperframes_template promises what is not there.")
