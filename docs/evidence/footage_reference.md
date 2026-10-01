# `library.tools.footage_reference` - the history behind its contract

This is the module docstring of `library/tools/footage_reference.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The vision pass's per-clip analysis as ONE document, reached by path.

Step 3.02 `select_broll` carried **three views of one vision analysis**
in a single prompt.  Measured on 001's frozen snapshot at 75d3e84, over
a context of 88,475 B:

    semantic_analysis_documents   35,813 B   40.5%
    picture                       10,250 B   11.6%
    broll_candidates_toon          7,613 B    8.6%
    ---------------------------------------------
                                  53,676 B   60.7%   of 88,475 B

After: 57,169 B, and the structure is at a path.  With #340's frame
strips also in the context, 94,994 B -> 63,688 B: the collapse is what
pays for putting a picture in front of the step that chooses one.

    picture                       10,250 B   17.9%
    broll_candidates_toon          7,613 B   13.3%
    footage_analysis_reference     4,507 B    7.9%
    ---------------------------------------------
                                  22,370 B   39.1%

and the share had grown, not shrunk: #295 stopped copying the captain's
brief into the prompt, so the denominator fell faster than the
duplication did.  This is the step whose cutaway choices the captain
complained about, and it is the step about to be handed a real frame.

## What each of the three carries, and why only one of them moves

`broll_candidates_toon` is the pre-bridge's table and the handoff calls
it "the primary source of WHAT is in each clip".  Its `description` is
`vision_schema_adapter.scene_prose` - `scene[]` rendered - and its
`framing`/`stability`/`camera_move` are the `camera[]` summaries.  On
001 that rendering is LOSSLESS: no clip's scene prose reaches the 600
character cap, and 16 of 17 clips have exactly one `camera[]` segment.
It also carries two things the analysis has not got at all - the clip's
catalogue `duration_s`, and `used_as_aroll`.  It stays inline.

`view:picture` is `actions[]`: one row per observed action window, with
its own bounds, covering 95.0% of 001's footage where `scene[]` covers
46.4%.  It is also the view the ANSWER is resolved against -
`cutaway_window.choose_window` matches the model's `preferred_moment`
against `blocks[].visual` - so a model that cannot see it is writing a
moment into a matcher it cannot see.  It stays inline.

`semantic_analysis_documents` is the raw structure both of those were
rendered from, and it is 40.5% of the prompt.  62.8% of its cells are
`objects[]` alone.  It is also the only one of the three keyed by the
document's own file stem (`IMG_1806_v3`) rather than the catalogue id
(`clip_001`) every other table in the context uses, so it is the one
view the model cannot join to the others without a mapping.  It moves.

## What moving it must not lose

The handoff tells the model to read it, twice: *"read these when a
candidate row is not enough to choose a sub-range"*, and Step C's *"use
the scene segment bounds ... and the per-range `camera[]` entries"*.
So it is not deleted.  Every byte of it is written to the step's own
directory and reached by a line range, through the mechanism #295 built
for the brief and #299 applied to the SFX catalogue - the same rule,
the same map, the same clause 5.  Nothing is filtered, ranked,
shortlisted or summarised away.

Four things really are only in here, and the map's per-clip lede is
sized to say which clips have them:

  * `objects[]` past the four labels the table's `subjects` column
    keeps - 159 objects on 001, of which 68 reach the table - with the
    role, the category, the time ranges and any `readable_text`;
  * `camera[]`'s per-segment TIME BOUNDS, which the table's deduped
    `wide -> close-up` summary does not carry;
  * `scene[]` as structure - location, type, lighting and
    notable_features as fields rather than as one prose line;
  * the assessment fields the table has no column for:
    `interest_score`, `keywords`, `clip_type`, `primary_subject_visible`
    and `usable_ranges_method`.

## The shape

One `##` section per clip, titled with the CATALOGUE clip id - the
exact string an answer has to name, and the id `broll_candidates_toon`
and `view:picture` are keyed by, so following the map lands in the
same vocabulary the rest of the context speaks.  A document that joins
to no routed clip list keeps its own id and is NAMED as such, the rule
`context_views._picture` already follows.

The section opens with its measured facts on one line, so `##`-splitting
alone gives the map a per-clip lede saying how much is behind the range.

Two shapes inside a section are chosen for the MAP rather than for the
page.  Nothing below a `##` is a heading, because `parse_sections`
collects every deeper heading into the map and four identical
subheadings per clip is 68 lines of the prompt saying nothing.  And the
identity line carries no underscore, because `brief_reference._lede`
strips markdown emphasis out of a lede and would turn `content_type`
into `contenttype` and `IMG_1806_v3` into `IMG1806v3` - a corrupted
identifier in the one line a model reads to decide whether to open the
section.  The vision pass's own id for a clip is inside the section,
where nothing rewrites it.


Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**A step may carry ONE reading of a measurement, or two on different axes - never the reading and the structure it was read from.**
The summary rule below says it for a rendered pair; this is the same rule when the second copy is the whole structure. [why](docs/RULE_EVIDENCE.md#three-views-of-one-analysis)
- **Establish what each view uniquely carries before deleting one**, and move the STRUCTURE rather than a rendering: the largest view is usually the one holding the per-segment bounds, the object labels and the assessment fields no table has a column for.
- The structure goes BY REFERENCE (`library/tools/footage_reference.footage_document`); nothing is filtered away and every byte is at the path.
- **Shape a referenced document for the MAP.** Nothing below a `##` may be a heading (`parse_sections` lifts deeper headings into the map), and the section's opening line carries no underscore or backtick (`_lede` strips markdown emphasis).
- `tests/test_broll_context_share.py` guards the RATIO of what the prompt spends on readings to what the structure costs inline, because that number does not depend on a fixture.
- **Measure a value where the model READS it** - the whole assembled prompt, pre-bridge included - not on the one route it used to arrive by.
- **Collapsing a structure into a summary makes the summary's blank cells load-bearing.** All three states - measured and usable, measured and unusable, never measured - must be legible in the cell itself.
```
