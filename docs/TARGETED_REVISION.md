# The targeted revision contract

QA-009 of the captain's punch list (2026-10-20): targeted quality-driven
revisions.  The contract lives in `library/tools/targeted_revision.py`, beside
the delivered-file checks it reads.

## The shape of it

The delivered-file checks NAME their failures.  The audio spec (QA-004), the
caption spec (QA-003), the picture spec (QA-002) and the editorial review
(QA-006) each say which property failed, over which interval, with the number
they measured.  A name is not an action: before this contract, the only way to
act on a failed delivery was to rebuild the timeline, which throws away every
part of the work that was already accepted.

So the contract is one turn:

```
    a failing CHECK  ->  one narrow EditRequest  ->  the checks again
```

and it is deliberately narrow in three ways at once.

| | what it means here |
|---|---|
| the property | the request changes the one thing the failure measured, and nothing else about it |
| the interval | the request touches the item the failure covers, and the scope says which frames that is |
| the accepted work | every row, clip and marker the request does not touch is a PRESERVATION requirement, so the read-back can judge it |

## The five things the punch list asks for

1. **Identify the precise interval, decision, or property behind a quality
   failure.**  `objective_checks` reads a `DeliverableQCVerdict` and turns each
   leg into one `QualityCheck`: a measurement, with its property, its interval
   and its evidence.  The caption spec's collision finding now names the
   colliding graphic's box and the frames it was seen at, so the failure says
   WHERE it happened.
2. **Create a narrow new edit request.**  `plan_revision` turns ONE failing
   check into ONE `EditRequest` (`library/tools/edit_request.py`) whose
   operations touch only the item the failure implicates.  The parameters are
   read off the measurement: the loudness gain is the spec's target minus the
   file's own integrated loudness; the graphic's displacement is the overlap
   the file's own boxes measure.
3. **Preserve previously accepted portions of the work.**  Every request
   carries `preservation` requirements: the rows the revision does not touch,
   the clips on the rows it does, and the captain's markers.  A requirement the
   engine cannot judge is refused at the request.
4. **Verify both the corrected issue and previously satisfied constraints.**
   `verify_revision` takes the checks BEFORE and AFTER and answers two
   questions separately: did the targeted check flip to passing, and did every
   check that was passing before stay passing.  A revision that fixes the
   caption and breaks the loudness is a REGRESSION, and it is reported as one.
5. **Stop retrying if the result is not improving or the remaining decision is
   subjective.**  A SUBJECTIVE check is never revised by the engine - it is
   handed to a person.  A check whose last attempt was `not_improving` or
   `regressed` is not attempted again.

## The narrow revisions

`REVISION_KINDS` is the whole list, and a property that is not in it is
REPORTED (`no_narrow_revision`), never guessed at.

| property | revision | op |
|---|---|---|
| `integrated_loudness` | the audio is gained by the measured distance to the target | `audio.gain` |
| `caption_collision` | the GRAPHIC is moved clear of the caption's own box; the caption is left alone | `overlay.position` |
| `overlay_geometry` | the overlay is drawn at the size the plan asked for | `overlay.resize` |
| `caption_words` | the card is rewritten with the plan's own words | `caption_fix` |
| `undeclared_freeze` | the freeze-hold's window is repaired in place | `hold.repair_window` |
| `digital_silence`, `undeclared_black` | the fix needs material the timeline does not carry | `needs_source` - a person decides what goes there |

## Where it is reached from

`ren accept --review <delivered file> ... [--timeline-view <view>]` writes the
review, and the review now carries a `targeted_revisions` section: one entry
per objective failure, each either a narrow `EditRequest` or the reason there
is none.  The timeline view is the caller's account of the timeline - the
items, their rows, their spans and their boxes - because a revision names an
ITEM, and an item is a thing on a timeline.  Without a view the planner says
so (`no_timeline_view`) rather than guessing an item.

The CLI is `python3 -m library.tools.editorial_acceptance --review <file>
--brief-file <brief> --judgments <the judge's rows> --timeline-view <view>`.

## The proof

A synthetic scratch file (90 frames at 15 fps, 540x960; synthetic because the
captain's media is read-only) with two seeded defects: a lower third drawn over
the caption's own box, and an audio track 5.15 dB under the delivery spec's
target.  The engine's own delivered-file checks read **14 checks, 12 passing,
2 failing** - the loudness and the collision, and nothing else.

| | before | the narrow revision | after |
|---|---|---|---|
| integrated loudness | -19.15 LUFS (5.15 dB off the -14.0 target) | `audio.gain` +5.15 dB on the one audio item | -14.0 LUFS, inside the +/-1.0 tolerance |
| caption collision | the lower third over the caption's box | `overlay.position` -32 px on the lower third; the caption's row declared unchanged | no collision |
| the other 12 checks | passing | - | passing |

"Nothing else changed" is measured on the seed's own frames: outside the
graphic's old and new boxes, the frames are identical, pixel for pixel; and
the preservation invariant, run over a before/after snapshot pair, reports
`preserved` with no unrelated change.

The incident behind the contract, and what it does not fix:
[docs/RULE_EVIDENCE.md](RULE_EVIDENCE.md#the-failure-nobody-could-act-on).
