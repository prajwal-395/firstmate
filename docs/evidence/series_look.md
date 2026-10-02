# `library.tools.series_look` - the history behind its contract

This is the module docstring of `library/tools/series_look.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The look a brand template DECLARES, and the two halves it is delivered in.

**There is no house look.** This module used to carry four of them -
`pmk_default`, `warm_reflection`, `electric_contrast`,
`film_stock_warmth` - each a complete set of numbers: a slope, an offset
and a power triple, a saturation, a pivot contrast, a glow gain,
threshold and size, a grain power and size, and a vignette blend and
falloff. Every one of those numbers was authored here. The *directions*
came from the captain's planning documents ("warm shadows, never blue";
"cream highlights"); the *strengths* did not, and could not - the
documents state a direction, not a magnitude. On project 001, whose
`project.yaml` names no brand template at all, `pmk_default` still
reached all seventeen per-clip Fusion comps and all ten CDLs.

The captain's ruling, 2026-08-28: *"i want no hardcoded values. there are
no house glow looks, there are no settled house grain or anything"*. So
the catalogue is gone, and it is not relocated: no shipped template
carries those numbers either.

What survives is the MECHANISM, which is not taste. Which terms an ASC
CDL has, which parameter names the Fusion comp builder dispatches on,
and which of the two can express a given idea are facts about Resolve
and about this repository. A look is now something a brand template a
project NAMED writes down, and this module reads that declaration:

* **CDL** carries hue and level. An ASC CDL is
  ``out = (in * slope + offset) ** power`` per channel, then a
  saturation term, so:
  - `slope` scales, which moves the bright end most -> highlight tint;
  - `offset` adds, which moves the dark end most -> shadow tint and
    the black floor;
  - `power` warps the middle -> midtone tint;
  - `saturation` is global.
  That is a complete split-tone in four terms, and it is what the
  Resolve API can set on a timeline item without a hand-built node tree
  (`TimelineItem.SetCDL`).

* **Fusion** carries everything a CDL has no term for: pivot contrast,
  highlight bloom, grain, and a shaped - and optionally coloured -
  vignette. Every key it emits is a name
  `library/tools/fusion/comp_builder.build_effect_comp` dispatches on.
  Emitting a name that module does not read produces a comp without that
  effect in it and no warning, which is how three VFX types and four
  grade nodes were silently lost before; `tests/test_series_look.py`
  asserts the nodes get drawn.

Three rules make a declaration incapable of smuggling a value back in:

1. **A project that declares nothing gets nothing.** Not a substitute
   look, not a reduced one - `resolve_look` returns None, step 5.01
   writes an empty `fusion_look`, no clip gets a comp for the look's
   sake, and the CDL stays `NEUTRAL_CDL`. This is the shape #297
   established for every other brand slot.
2. **An element is declared WHOLE or not at all.** A declaration
   carrying a glow gain but no threshold is refused by name, because the
   only way to finish it is for this file to pick the missing number.
   That is exactly what it must never do again.
3. **No element has a default and none has a bound.** How strong a glow
   is, and how far a slope may travel, are the declaring author's
   decisions. An engine-supplied range is a strength nobody chose,
   arriving one level up.

Nothing here depends on a file inside a DaVinci Resolve installation.

**On the names.** This module, the brand-template slot, the project
slot and the manifest key were all called `house_look` until 2026-09-10.
Keeping the name was argued for as leaving an ADDRESS alone: templates
write it, the renderer reads it, project 001's recorded state carries it,
and renaming an address moves no frame while risking every reader. The
captain overruled that on sight of his own `project.yaml`:

    *"you better not be pulling some random shit from like a 'house
    look' because there is no house look and all references to any
    hardcoded values around it should be removed."*

The values under it were his own pick and were never a house default -
`v04_teal_split`, chosen from five rendered variants and copied verbatim
into `lucie/geo-podcast` alone. But a slot named for the thing the engine
is forbidden to have reads as smuggled defaults every time somebody opens
the file, and a reader who has to be told "it does not mean what it says"
is a reader the name has already failed. It is a look a PROJECT or its
SERIES declares, so it is `series_look`.

`house_look` is still READ, from a brand template and from a
`project.yaml` alike, so every declaration written before the rename
keeps working; it is never written and never offered in an error
message. `slot_from` is the one place that reads it.

The old addresses, written out so that a search for any of them lands
here: `library/tools/house_look.py` IS this file;
`house_look.NEUTRAL_CDL` is `series_look.NEUTRAL_CDL`;
`house_look.resolve_look`, `house_look.project_house_look` and
`house_look.effective_house_look` are `resolve_look`,
`project_series_look` and `effective_series_look`; `style.house_look` is
`style.series_look` in a brand template and in a `project.yaml`; and
`tests/test_house_look.py` is `tests/test_series_look.py`.


Rules relocated from AGENTS.md 12
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 12
keeps the headline and points here.

**There is no house look.** The engine ships no slope, no saturation, no contrast, no glow, no grain and no vignette, and a project gets a grade only where a brand template it NAMED declares one.
`library/tools/series_look.py` holds no values of its own. [why](docs/RULE_EVIDENCE.md#there-is-no-house-look)

A look is delivered in two halves, because that is what the mechanisms can express:
- **CDL** carries hue and level - slope (highlights), offset (shadows and the black floor), power (midtones), saturation - applied by `SetCDL` in `resolve_build_timeline`.
- **Fusion** carries what a CDL has no term for - pivot contrast, glow, grain, and a shaped, optionally coloured vignette - and reaches the picture only through the parameter names `fusion/comp_builder.build_effect_comp` dispatches on (§10.2).
- **`LOOK_ELEMENTS` is the whole vocabulary**, and an element outside it is REFUSED by name. Each row says which half delivers it and why that half and not the other.
- **An element is declared WHOLE or refused.** A glow with a gain and no threshold cannot be finished without the engine choosing the missing number, which is the defect this section exists for. Same shape as `bookends` (§13): raise, never drop and never complete.
- **No element has a default and none has a bound.** How strong a glow is, and how far a slope may travel, are the declaring author's decisions; an engine-supplied range is a strength nobody chose arriving one level up.
- **A project declaring no look gets NOTHING** - not a reduced look and not exposure normalisation. `NEUTRAL_CDL` is identity and `fusion_look` is `{}`, so no clip gets a comp for the look's sake at all. This is the shape #297 established for every other brand slot (§10.1).
- **A vignette is drawn only where one was asked for.** `build_effect_comp` used to default `vignette` to True, drawing one at blend 0.25 on every clip carrying a zoom.
- **Exposure is MEASURED, and normalised only onto a reference the declaration carries.** A clip nothing measured carries `null` and a reason, never `0.0`. `exposure_reference` is the declared target. [why](docs/RULE_EVIDENCE.md#the-exposure-probe-measured-nothing)
- `tests/test_series_look.py`, `tests/test_color_grade_delivery.py`.
```
