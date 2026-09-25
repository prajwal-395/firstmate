"""Read a clip's CDL back off Resolve and compare it with the plan.

Finding 28: per-clip CDL reached `SetCDL` but barely showed in the
export, while the build printed "Applied CDL base grade" off the
write's return alone. A `True` return from `SetCDL` is not evidence
the grade landed (`library/tools/color_page_grade.py`) - only what
Resolve holds afterwards, and then what the exported pixels show, is.

This module is the first half: parse what `GetCDL()` answers and
compare it with the four terms the plan specified. The second half is
`render_qa.measure_grade_delivery`, which judges the exported pixels.
"""

from __future__ import annotations

#: A read-back triple within this of the specified value counts as the
#: specified value. CDL terms ride as 4-decimal strings; Resolve stores
#: floats, so float noise at 1e-4 magnitudes is representation, while a
#: grade that did not land misses by whole tenths.
TRIPLE_TOLERANCE = 0.02


def parse_triple(raw) -> list | None:
    """`"1.0800 1.0800 1.0800"` -> [1.08, 1.08, 1.08], or None.

    None means the read-back names no triple (empty answer, a scalar,
    non-numeric text) - the caller reports the grade unverified, never
    parses a grade out of nothing. A lone scalar broadcasts to all
    three channels: `GetCDL` answers Saturation either way depending
    on build, and a scalar that matches is agreement however spelled.
    """
    if raw is None:
        return None
    try:
        parts = [float(p) for p in str(raw).split()]
    except (TypeError, ValueError):
        return None
    if len(parts) == 1:
        return parts * 3
    if len(parts) != 3:
        return None
    return parts


def expected_terms(cdl_values: dict) -> dict:
    """The four specified terms as floats, in `GetCDL` key spelling."""
    cdl = cdl_values or {}
    return {
        "Slope": [float(cdl.get("slope_r", 1.0)),
                  float(cdl.get("slope_g", 1.0)),
                  float(cdl.get("slope_b", 1.0))],
        "Offset": [float(cdl.get("offset_r", 0.0)),
                   float(cdl.get("offset_g", 0.0)),
                   float(cdl.get("offset_b", 0.0))],
        "Power": [float(cdl.get("power_r", 1.0)),
                  float(cdl.get("power_g", 1.0)),
                  float(cdl.get("power_b", 1.0))],
        "Saturation": [float(cdl.get("saturation", 1.0))] * 3,
    }


def compare_cdl(actual: dict, cdl_values: dict,
                tolerance: float = TRIPLE_TOLERANCE) -> list:
    """The mismatch lines between a `GetCDL` answer and the plan.

    `actual` is the `GetCDL()` dict (or the clip-property fallback
    mapping with the same keys). Empty when the read-back IS the
    specified grade; one line per disagreeing channel otherwise. A
    missing or unparsable term is reported as unverifiable, never as
    agreement - an unreadable grade is not a matching one.
    """
    expected = expected_terms(cdl_values)
    problems = []
    for key, want in expected.items():
        have = parse_triple((actual or {}).get(key))
        if have is None:
            problems.append(
                f"{key}: read back {(actual or {}).get(key)!r} - "
                f"unverifiable, expected "
                f"{' '.join(f'{v:.4f}' for v in want)}")
            continue
        for channel, (h, w) in enumerate(zip(have, want)):
            if abs(h - w) > tolerance:
                problems.append(
                    f"{key}[{channel}]: reads {h:.4f}, specified "
                    f"{w:.4f}")
    return problems
