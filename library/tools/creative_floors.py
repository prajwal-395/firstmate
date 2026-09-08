"""A creative floor, and the one vocabulary that names one.

Captain's ruling 2026-08-20: remove the B-roll minimum and the SFX
minimum entirely - not warnings, not a reconciled range, not per-template
minimums. The creative direction decides how many of anything a piece
gets; nothing is padded to satisfy a number.

A floor is a floor whether it pads, rejects, warns, or cuts, and whether
it lives in a prompt or in code. Until this module the vocabulary for one
lived in `tests/test_no_creative_floors.py` alone, which meant a second
reader - a project-declared creative task whose role and handoff arrive
from outside the repository (`library/tools/creative_tasks.py`) - had no
enumeration to be read for. A guard whose vocabulary exists in only one
place cannot reach a second prompt without being restated, and a restated
guard is two guards that can disagree about what a floor is.

So the phrases and patterns live here, once. The test imports them, and
a declared task is refused at declaration time against the same list -
the prompt a task carries is read by the same gate that reads a step's.

Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module. They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**There are NO creative floors, and there must not be again.**
[why](docs/RULE_EVIDENCE.md#no-creative-floors)
"""

from __future__ import annotations

import re


#: A quota does not have to be phrased as a demand. These are the
#: literal phrases a floor has worn before - the lines removed on
#: 2026-08-25 and the warnings that replaced them.
QUOTA_PHRASES = [
    "must plan exactly 5",
    "must plan exactly 5-15",
    "must plan exactly 5-10",
    "must select 5",
    "must plan between",
    "exactly 5-10 sfx",
    "5-15 b-roll",
    "default 5-10 sfx",
    # The two that survived the ruling until 2026-08-25.
    "must plan at least",
    "strictly select exactly",
    "an empty list is a failure",
]


#: A quota does not have to be phrased as one. "at least N", "N-M items"
#: and "every clip MUST have" all set a floor, so the guard also refuses a
#: bare numeric range next to a plural noun and a per-clip MUST.
QUOTA_PATTERNS = [
    # "at least 3", "at least three"
    r"at least\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    # "3-7 VFX items", "10-15 passages"
    r"\b\d+\s*-\s*\d+\s+(?:vfx|sfx|b-roll|passages|items|effects|cutaways|sounds)\b",
    # "every talking head clip >3s MUST have at least a slow zoom".
    # Deliberately narrower than a bare "every ... must have": select_broll
    # says every non-speech block must have B-roll, and that is a COVERAGE
    # requirement, not a floor - an uncovered block is a black hole that
    # compile_manifest._assert_timeline_fully_covered fails on.
    r"every\b[^.\n]{0,80}\bmust\s+have\s+at\s+least\b",
]


def find_floors(text: str) -> list:
    """Every floor `text` carries, as the matched phrases and patterns.

    Read case-insensitively, because a floor in title case pads the edit
    identically to one in lower case. Empty means no floor found - which
    is a reading, not a proof: the vocabulary names the floors seen so
    far, and a new shape of floor needs a new row here before any reader
    of this list can see it.
    """
    lowered = (text or "").lower()
    hits = [phrase for phrase in QUOTA_PHRASES if phrase in lowered]
    hits += [m.group(0) for pattern in QUOTA_PATTERNS
             for m in re.finditer(pattern, lowered)]
    return hits


class CreativeFloor(ValueError):
    """Text that demands a creative count."""


def assert_no_floors(text: str, where: str) -> None:
    """Raise naming the floor, or return silently when there is none.

    `where` is whose text this is - a step path, a task name - so the
    refusal says what to fix rather than just what matched.
    """
    hits = find_floors(text)
    if hits:
        raise CreativeFloor(
            f"{where} demands a creative count ({hits}). The floors were "
            f"removed by ruling; a prompt-level quota reintroduces exactly "
            f"the padding they caused."
        )
