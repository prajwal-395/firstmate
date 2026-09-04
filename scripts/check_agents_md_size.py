#!/usr/bin/env python3
"""Gate the size of AGENTS.md, globally and PER SECTION.

Two ratchets, and the per-section one is the half that makes this durable.

The global ceiling alone could not say what to do about a breach. Measured over
the two working days after the 2026-09-01 condensation (#378): twelve commits
grew the file by a mean of 2,913 characters each, `###` subsections went 39 -> 47
(+8 new, -0 removed), and the whole 42,221-character saving was gone. Every one
of those additions was a design note for one new `library/tools/*.py`. A global
number cannot object to that; a budget on the section the note lands in can, and
it names the fix - keep the index row here, put the detail in the module.

Both ratchets may ONLY EVER MOVE DOWN. Do not raise one to make a change fit.
`SECTION_BUDGETS` must also SUM to no more than `CEILING`, so a budget cannot be
raised without lowering another one: the ratchet cannot be defeated a section at
a time.

A `##` section with no budget row FAILS rather than defaulting. A section that
exists and silently has no bound is the trap this file exists to stop.
"""

import re
import sys

# RATCHET: This ceiling may ONLY EVER MOVE DOWN, never up.
# It exists to force condensation. Do not raise it to make your change fit.
CEILING = 53507

# RATCHET, per section: lowered 2026-09-03 as each section's detail moved to the
# modules that own it - `## 10` 81,459 -> 17,591, `## 3` 28,708 -> 5,590,
# `## 15` 20,149 -> 1,800, `## 5` 9,962 -> 4,565, `## 8` 7,809 -> 1,829 and
# `## 12` 4,803 -> 564.  Lower a row as its content moves; never raise one.
#
# The moves STOP here, and that is a decision rather than an interruption.  What
# is left is the universal core - the repo map, the run vocabulary and flags, the
# state-key glossary, the environment - plus one index row per subsystem.  The
# sections still carrying bullets carry orientation every session needs before it
# can read anything else, and moving those would cost a reader the map to save
# bytes.
#
# TWO rows have been raised, both on `## 9` and both to document these gates in
# the file they govern: +1,849 for the two gates themselves, and +842 for the
# captain's rule that CI is the full-suite gate.  The file had 0 spare before
# `## 10` moved, so the gate could not describe itself until a move made room.
# They are recorded here rather than left to be discovered, and the sum rule
# below is what made them payable: either raise without the characters that left
# would have failed.
#
# `## 5` is 9,962 rather than the 9,975 seeded earlier.  #484 edited that section
# while #483 was in flight, so the budget it landed with was 13 characters loose;
# reseeding from the file corrects it downward.  A budget that is looser than the
# section is the same lie as one for a section that no longer exists.
# LOWERED 2026-09-04 as detail moved to the modules that own it: `## 5`'s
# per-property Resolve findings were already verbatim in
# `probe_resolve_capabilities.py` (lines 23-26), and `## 3`'s captain quote
# verbatim in `brief_attachment.py`.  `## 10` lost three lines that each
# restated the line directly above them with the same Detail target and the
# same [why] anchor.  Four index rows were added for the timeline-ingest
# work and paid for out of those moves, so the ceiling still went DOWN.
SECTION_BUDGETS = {
    "## How to read this file": 268,
    "## 1. Identity and purpose": 234,
    "## 2. Repo layout": 2144,
    "## 3. Pipeline execution": 5404,
    "## 4. Dashboard": 3120,
    "## 5. DaVinci Resolve integration - CRITICAL RULES": 4538,
    "## 6. The spine contract": 1282,
    "## 7. Data flow": 826,
    "## 8. Project management": 1701,
    "## 9. Environment and dependencies": 6324,
    "## 10. Cross-cutting rules": 17394,
    "## 11. Third-Party Asset Licenses": 1313,
    "## 12. The look": 573,
    "## 13. Intros, outros and end cards": 1114,
    "## 14. General assets vs project assets": 2127,
    "## 15. Notes the captain types onto the timeline": 1800,
    "## 16. Motion graphics": 2271,
    "## Maintaining this file": 553,
}


def sections(text):
    out = {}
    ms = list(re.finditer(r'(?m)^## .*$', text))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out[re.sub(r'\s+', ' ', m.group(0)).strip()] = end - m.start() - len(m.group(0))
    return out


def main():
    filename = sys.argv[1] if len(sys.argv) > 1 else 'AGENTS.md'

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"{filename} not found.")
        sys.exit(1)

    failures = []
    secs = sections(content)
    size = len(content)

    budgeted = sum(SECTION_BUDGETS.values())
    if budgeted > CEILING:
        failures.append(
            f"SECTION_BUDGETS sum to {budgeted:,}, over the {CEILING:,} ceiling.\n"
            f"  A budget was raised without lowering another. Lower one; never raise the ceiling.")

    if size > CEILING:
        failures.append(
            f"AGENTS.md size ({size:,} chars) exceeds the ceiling ({CEILING:,} chars).\n"
            f"  This file only grows if something else shrinks.")

    for name, sz in sorted(secs.items(), key=lambda x: -x[1]):
        budget = SECTION_BUDGETS.get(name)
        if budget is None:
            failures.append(
                f"{name}: {sz:,} chars and NO BUDGET DECLARED.\n"
                f"  Add a row to SECTION_BUDGETS in {__file__.split('/')[-1]}.\n"
                f"  A new section is where this file regrows: keep an index row here and\n"
                f"  put the detail in the module the rules are about.")
        elif sz > budget:
            failures.append(
                f"{name}: {sz:,} chars, over its {budget:,} budget by {sz - budget:,}.\n"
                f"  Move the detail to the module that owns it and leave an index row,\n"
                f"  then lower the budget by what you moved. Do not raise it.")

    for name in SECTION_BUDGETS:
        if name not in secs:
            failures.append(
                f"{name}: budgeted but no longer in the file.\n"
                f"  Delete its SECTION_BUDGETS row - a budget for a section that does not\n"
                f"  exist is a lie about what is still owed.")

    if failures:
        print("AGENTS.md size gate FAILED:\n")
        for f in failures:
            print(f"- {f}\n")
        print("Largest sections:")
        for name, sz in sorted(secs.items(), key=lambda x: -x[1])[:5]:
            b = SECTION_BUDGETS.get(name)
            print(f"  - {name}: {sz:,}" + (f" / {b:,} budget" if b else "  (no budget)"))
        sys.exit(1)

    headroom = CEILING - size
    print(f"AGENTS.md size check passed: {size:,} <= {CEILING:,} ({headroom:,} spare)")
    print(f"  {len(secs)} sections, all within budget; "
          f"budgets sum to {budgeted:,} of {CEILING:,}")


if __name__ == "__main__":
    main()
