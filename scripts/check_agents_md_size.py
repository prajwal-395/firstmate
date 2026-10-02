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
# LOWERED 2026-09-06 with `## 5`, from 53,343: `## 5` gained one index row for
# `library/tools/resolve_organization.py` and paid for it inside its own budget
# - a whitespace-only line, a per-property sentence the Detail line below it
# already points at, one over-long `[why]` link text, and a two-line marker
# paragraph reduced to an index row.  The 9 characters left over went off the
# ceiling rather than back into the spare, because spare nobody claimed is how
# the budgets came to be looser than their sections.  What did NOT pay for it
# was the empty `### Visual verification` heading: removing it failed
# `check_agents_md_preservation.py`, which is right - a heading is
# cross-referenced whether or not it currently carries prose.
# LOWERED 2026-09-07 with `## 5`, from 53,326: `## 5` gained two index rows -
# `orphan_removal.py` (removing a pool item, which the organiser must never do)
# and `master_markers.py` - and paid for both inside its own budget by moving
# two HOW-TO sentences to the files their `Detail:` lines already point at:
# the `neural_engine_directives` pop now lives only at
# `resolve_build_timeline.py:37` and the `GetProperty()` read-back only at
# `probe_resolve_capabilities.py:22`, both verbatim. The 78 characters left
# over went off the ceiling rather than back into the spare.
# MERGED 2026-09-10: fifteen-lane merge; ceiling re-seeded from measurement below.
# MOVED 2026-09-11, `## 5` -> owner modules, 1,037 chars: the gate failed on
# HEAD (`## 5` 4,741 over its 4,501 budget, file 219 over the ceiling) and
# blocked the `CAPTION_LIFT_PX` rule. Five `## 5` bodies became index rows -
# frame mapping (verbatim in `played_window.py:69`), media pool and audio
# (in `resolve_build_timeline.py:39-42`, which gained the two sentences it
# lacked: the row rule and the period-form prefix rule), the NEVER/ALWAYS
# item lists (in `comp_builder.py`, which gained a contiguous `Center`),
# and the audio-level enumeration (verbatim in
# `probe_resolve_capabilities.py:24`). `## 10` paid for the new lift entry
# inside its own budget four times over: the `MotionGraphics/index.tsx`
# sentence (verbatim in `safe_area.py:77`), a doubled render_qa Detail
# line, the `SUBJECT_HEADROOM` sentence (in `subject_framing.py:64`, its
# [why] folded onto the rule), the model-judged-gate guidance (in
# `pipeline_skills.py:53`), and the join-key clause (in
# `semantic_index.py`, which gained the `clip_XXX` form). Both budgets are
# set to measurement plus ONE index row of declared spare (`## 5` 3,704 ->
# 3,900, `## 10` 16,759 -> 16,950) - the spare is claimed here, for the
# next rule in the section that blocked, not left unclaimed. The CEILING
# is HELD at 53,072 rather than lowered by the net saving, so the file
# carries ~1,000 chars of global headroom: an exact-fit file re-blocks
# the next lane, which is the incident this move repairs.
# LOWERED 2026-09-14 with `## 9`, from 53,061: `## 9` gained ONE index row - the
# shared dependency store, `docs/SHARED_ENVIRONMENT.md` - and paid for it twice over
# inside its own budget.  The ML-preflight bullet restated its own second line
# ("checked for `run` only (ML_DEPENDENT_COMMANDS)" against "only for the commands
# in ML_DEPENDENT_COMMANDS, which is `run` alone") and then explained
# `sys.executable` in words `manage_project.py:46-57` already carries verbatim; it
# is now a headline plus that pointer.  The three consecutive "Set <var>" bullets
# became one, with every variable name kept.  The 115 characters left over went off
# the ceiling rather than back into the spare.
# LOWERED 2026-09-16 with `## 5`, from 52,946: `## 5`'s existing timeline-speech row
# gained the transcriber SEAM and `library/tools/hybrid_transcription.py`, and paid
# for it three times inside its own budget.  The frame-mapping row restated the rule
# `composed_edit.py` opens with (re-derive or REFUSE) beside the pointer to it; the
# Tracks row said "track index and name" one word after naming `timeline_layout`;
# and the media-pool row listed the three things the one module it points at
# enumerates.  All three are now headline plus pointer.  The 10 characters left over
# went off the ceiling rather than back into the spare.
CEILING = 52936

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
# MOVED 2026-09-07, `## 10` -> `## 16`, 86 characters: `## 16` gained one index
# row for `library/tools/explainer_plan.py` - an animated explainer is a staged
# plan over that roster, timed to the words that say each stage. `## 10` paid
# for it by moving the `INTENSITY_MAP` narrative to
# `library/steps/step_4_03_plan_vfx/post_bridge.py`, which already held it in
# fuller form and now also states the half AGENTS.md was carrying alone: that
# the VALUES in a readable parameter name are never checked, clamped or
# substituted. Both budgets are set to what their sections now MEASURE, so the
# 164 characters left over went off the CEILING rather than back into the
# spare - spare nobody claimed is how the budgets came to be looser than their
# sections.
# MOVED 2026-09-12, `## 10` -> `render_qa.py` and `manifest_validator.py`, 242
# characters: `## 10` gained one index row for the version object - a version
# is a ROUND, a BUILT reel carries a durable sign-off, and promotion RETIRES
# rather than deletes (`round_version.py`, `reel_signoff.py`,
# `reel_retirement.py`). It paid for it inside its own budget by dropping two
# restatements: the `subtitle_gaps` sentence, which `render_qa.py:94` already
# carried VERBATIM, and the two `manifest_validator` thresholds, which that
# module already declares as `MIN_DISPLAY_DURATION` and
# `MAX_UNIFORM_PARAMETER_SETS` and now states as prose beside them. `## 10`'s
# budget is set to what it now measures plus the 6 it already had, and the 11
# characters left over went off the CEILING rather than back into the spare.
# MOVED 2026-09-22, `## 2`/`## 3`/`## 4`/`## 5`/`## 10`/`## 11`/`## 15`/
# `## 16` -> `## 14`, 173 characters: `## 14` gained one index row for the
# series-shared brand store (`library/tools/brand_library.py`,
# `docs/BRAND_LIBRARY_STORE.md`). No donor had content moved, so every donor
# budget is set to what its section now MEASURES - none has slack left to
# spend, and the next addition rebalances again. Sum unchanged, ceiling
# untouched.
# MOVED 2026-10-01: 100 of `## 15`'s unused budget to `## 8`, for the one
# index row naming `library/tools/perf_ledger.py` (the run's time ledger).
# The sum is unchanged.
SECTION_BUDGETS = {
    "## How to read this file": 268,
    "## 1. Identity and purpose": 234,
    "## 2. Repo layout": 2124,
    "## 3. Pipeline execution": 5337,
    "## 4. Dashboard": 3118,
    "## 5. DaVinci Resolve integration - CRITICAL RULES": 3884,
    "## 6. The spine contract": 1282,
    "## 7. Data flow": 826,
    "## 8. Project management": 1801,
    "## 9. Environment and dependencies": 5948,
    "## 10. Cross-cutting rules": 16882,
    "## 11. Third-Party Asset Licenses": 1273,
    "## 12. The look": 558,
    "## 13. Intros, outros and end cards": 1114,
    "## 14. General assets vs project assets": 2300,
    "## 15. Notes the captain types onto the timeline": 1681,
    "## 16. Motion graphics": 2349,
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
