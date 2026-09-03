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
CEILING = 172348

# RATCHET, per section: seeded 2026-09-03 at the sizes the file actually had, so
# this lands green and every later change has to argue with it. Lower a row as
# its content moves to the module that owns it; never raise one.
SECTION_BUDGETS = {
    "## How to read this file": 274,
    "## 1. Identity and purpose": 236,
    "## 2. Repo layout": 2146,
    "## 3. Pipeline execution": 28708,
    "## 4. Dashboard": 3127,
    "## 5. DaVinci Resolve integration - CRITICAL RULES": 9989,
    "## 6. The spine contract": 1285,
    "## 7. Data flow": 827,
    "## 8. Project management": 7809,
    "## 9. Environment and dependencies": 3634,
    "## 10. Cross-cutting rules": 81459,
    "## 11. Third-Party Asset Licenses": 1313,
    "## 12. The look": 4803,
    "## 13. Intros, outros and end cards": 1114,
    "## 14. General assets vs project assets": 2127,
    "## 15. Notes the captain types onto the timeline": 20149,
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
