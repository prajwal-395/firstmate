"""THIRD_PARTY_NOTICES is the license inventory, and these are the rules
that keep it one.

Every declared Python dependency needs its row there (walked from the
group files, never a curated list); the file must name the components
no requirements file can carry (models, binaries, fonts, the renderer);
and the fixed MIT claim for the buffalo_l weights must never come back.
History: the P0 commercial-dependency/license-audit lane, which found
requirements/identity.txt calling the non-commercial buffalo_l pack
"both MIT" while scripts/install_insightface.sh correctly said
non-commercial.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import dependency_groups as dg

NOTICES = os.path.join(PROJECT_ROOT, "THIRD_PARTY_NOTICES")
IDENTITY = os.path.join(PROJECT_ROOT, "requirements", "identity.txt")

# Rows no requirements file can carry: models, binaries, fonts, renderer.
# Each is a substring the NOTICES file must contain; the comment names
# why it has to be there.
REQUIRED_ROWS = (
    "buffalo_l",            # face-identity weights (non-commercial)
    "non-commercial research only",  # the buffalo_l term, stated plainly
    "remotion",             # subtitle renderer (Automators license class)
    "Automators",           # the commercial use class Ren falls under
    "audio-flamingo",       # SFX profiling weights (research only)
    "desert-ant",           # da voz/ear transcriber (source-available)
    "Montserrat",           # shipped font (OFL 1.1)
    "GSAP",                 # vendored hyperframes asset
    "PANNs",                # sound-event checkpoint (CC-BY-4.0)
    "ECAPA",                # speaker-encoder checkout
    "DeepFilterNet",        # dialogue-cleanup binary
    "Gemma",                # vision weights (Gemma Terms of Use)
    "Montreal-Forced-Aligner",  # forced aligner (MIT)
    "micromamba",           # MFA bootstrapper (unpinned latest)
)


def _notices_text() -> str:
    with open(NOTICES, encoding="utf-8") as f:
        return f.read()


def _declared_packages() -> list:
    """Every package the runtime groups declare, via the same reader the
    preflight uses (follows -r includes)."""
    found = []
    for group in dg.RUNTIME_GROUPS:
        found.extend(dg.declared_requirements(dg.group_file(group)))
    return found


def _names_of(requirement: str) -> str:
    """The importable/package name of a requirement line, normalised for
    a substring search (underscores and dashes are interchangeable)."""
    name = re.split(r"[<>=!~\s\[;]+", requirement.strip())[0]
    return name.lower().replace("_", "-")


def test_every_declared_package_has_its_notices_row():
    """A dependency added without its inventory row ships unlicensed."""
    text = _notices_text().lower().replace("_", "-")
    missing = [r for r in _declared_packages() if _names_of(r) not in text]
    assert not missing, (
        f"declared in requirements/ but with no THIRD_PARTY_NOTICES row: "
        f"{missing}. Add the row in the same change.")


def test_notices_names_what_no_requirements_file_carries():
    text = _notices_text()
    missing = [row for row in REQUIRED_ROWS if row.lower() not in text.lower()]
    assert not missing, (
        f"THIRD_PARTY_NOTICES names no {missing}. A model, binary, font "
        f"or renderer Ren ships or downloads is invisible to the audit.")


def test_buffalo_l_weights_are_never_called_mit_again():
    """The fixed claim: the CODE is MIT, the pretrained pack is not."""
    with open(IDENTITY, encoding="utf-8") as f:
        identity = f.read()
    assert "both MIT" not in identity, (
        "requirements/identity.txt calls the buffalo_l bundle 'both MIT' "
        "again. Upstream: code MIT, pretrained packs non-commercial "
        "research only (deepinsight/insightface README + model_zoo).")
    text = _notices_text()
    assert "SEPARATE non-commercial term" in text, (
        "THIRD_PARTY_NOTICES must keep stating the code/weights licence "
        "split for insightface, or the next reader repeats the error.")


def test_every_row_carries_a_commercial_determination():
    """A row without a determination is a row nobody can act on."""
    text = _notices_text()
    for determination in ("needs-paid-license", "not-permitted"):
        assert determination in text, (
            f"THIRD_PARTY_NOTICES states no {determination!r} component. "
            f"The blockers (buffalo_l, Remotion, Audio Flamingo Next) "
            f"must read as blockers, not as prose.")
