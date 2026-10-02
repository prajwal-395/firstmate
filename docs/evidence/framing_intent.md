# `library.tools.framing_intent` - the history behind its contract

This is the module docstring of `library/tools/framing_intent.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
How much of the delivery frame the picture fills. One enumeration.

``framing_intent`` is a normalised scalar: **0.0 letterboxes** (the source
is fitted inside the frame and the leftover is black), **1.0 fills** (the
source is scaled until the frame is covered and the crop window follows
the subject), and values between punch in partway.
``compile_manifest._conform_fields`` owns the geometry that turns it into
a zoom and a pan; this module owns only the question of *which number a
clip gets*.

Why the default is FILL
-----------------------
It used to be a heuristic, and the heuristic was inverted.  With no
declaration, ``_conform_fields`` ran a "legacy auto-decision" whose rule
was: *if the primary subject is visible during this clip's source range,
a centre crop might cut them off, so prefer letterbox*.  On a selfie
monologue the subject is visible in every clip, so **every clip
letterboxed, always**.

Measured on the only finished export on disk - project 001,
``exports/Pipeline_Edit.mp4``, 1080x1920 - the A-roll picture occupied
rows 656..1263: 608 of 1920 rows, 31.7% of the frame height, with 61.6%
of all pixels below luma 12 averaged over 55 samples.  The three B-roll
cutaways are portrait-shot and filled the frame, so the video also
flipped between full-bleed and a thin strip three times.

That is not 9:16 delivery.  The captain's standing direction
(``PLAN/series portfolio '26 planning/overall_branding_creative_direction.md``)
says "Aspect ratio: 9:16 (1080x1920)" at line 363, "Every frame should
look deliberate" at 364 and "the frame should never be lazy" at 76.  A
template declares ``delivery_format: vertical_1080x1920`` to say what the
product IS; silently shipping a 16:9 strip inside it is the same class of
defect as the retired source-derived render target that
``delivery_format.py`` exists to close.

The thing the old rule was protecting is real - a blind centre crop
beheads a speaker standing in the left third - and it is now protected
properly rather than by refusing to crop at all.  The crop window follows
``library/tools/subject_framing.subject_center_x``, measured off the
face-centre track ``step_1_04_temporal_index.compute_face_presence``
already banks at 5Hz.  When the footage cannot support a position the
window stays centred, which is what ``subject_framing`` returning None has
always meant.

A series that genuinely wants bars still says so, and one already does:
a project's own ``brand.json`` declares ``style.framing_intent: 0.0``
because the letterbox is its look.

Precedence
----------
    spine block ``framing_intent``      per-clip creative choice
      > project.yaml ``pipeline.framing_intent``
      > brand template ``style.framing_intent``
      > :data:`DEFAULT_FRAMING_INTENT`

The project level is the one this module ADDS.  It is the same
project-over-template precedence ``delivery_format_name`` and
``timed_text_overlay.resolve_declaration`` use, and for the same reason: a
series may ship one video framed differently without forking its
template.

A malformed declaration RAISES.  A framing declaration that is silently
dropped is a frame the editor believes shipped.

Declared is not delivered
-------------------------
A declaration says what the editor WANTS.  What a clip can actually give
is arithmetic, and the two differ in one direction: **a source whose
display aspect already matches the delivery frame has no bars to give.**
It covers the frame at every intent, ``0.0`` included, because there is
nothing to letterbox.

That is not a corner case - it is project 001's whole edit.  Its eleven
A-roll placements are landscape 1920x1080 into a 1080x1920 frame and its
seven cutaways are shot portrait, so ONE declaration of ``0.0`` puts bars
on the A-roll and leaves the cutaways full-bleed.  The per-clip framing
of that video is not a second creative choice on top of the first; it is
the first choice meeting seventeen measured source aspects.

:func:`delivered_framing_intent` is that reading, and
``compile_manifest._conform_fields`` records it beside the declaration as
``framing_delivered``.  Recording the declaration alone is what made the
distinction invisible: ``render_qa``'s occupancy gate reads the manifest
to learn what the picture was SUPPOSED to look like, and a portrait clip
carrying ``framing_intent: 0.0`` tells it the frame is barred when the
frame is full.  Same defect class as AGENTS.md section 10.3's rule about
an assessment field reporting a value nobody measured, arriving through
the manifest instead of through the vision pass.


Rules relocated from AGENTS.md 10.3
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.3 keeps the headline
and points here.

**The frame FILLS by default, and there is no heuristic.**
One enumeration, `library/tools/framing_intent.py`: 0.0 letterboxes, 1.0 fills.
The number comes from the spine block > the project's `pipeline.framing_intent` > the template's `style.framing_intent` > `DEFAULT_FRAMING_INTENT` (1.0).
`tests/unit/picture/test_framing_intent.py`.
Source: spine block > project > template > `DEFAULT_FRAMING_INTENT` (1.0). [why](docs/RULE_EVIDENCE.md#the-letterbox-default)
**A framing DECLARATION is not a framing DELIVERED, and the manifest records both.**
`_conform_fields` writes `framing_intent` and `framing_delivered` on every clip. [why](docs/RULE_EVIDENCE.md#a-declaration-a-clip-cannot-honour)
- **A source whose display aspect already covers the delivery frame has no bars to give**, so it fills at every intent, `0.0` included. `framing_intent.source_covers_frame` is that predicate and `delivered_framing_intent` is the reading. Do not put the coverage arithmetic anywhere else.
- **Which clips letterbox is therefore a MEASUREMENT, not a second creative choice.** A step that chose a framing per clip would be inventing taste where the arithmetic already answers (section 10.5). **Whether the picture is inset at all is the captain's PREFERENCE and belongs in the project's own declaration.**
- **The spine-block level of the chain is reachable and unwritten.** `compile_manifest` really reads `block["framing_intent"]` (`tests/scenarios/test_compile_manifest.py`), but no handoff asks for one and `spine_contract` does not list the key. On the B-roll side the hook is the PLACEMENT's own key, not the block it covers: a cutaway is different footage.
- **A source that already covers the delivery frame fills at every intent.** `source_covers_frame` is the predicate.
- **Which clips letterbox is a MEASUREMENT, not a second creative choice** (section 10.5).
```

## `tests/unit/reels/test_reel_look.py` module docstring (moved 2026-10-02)

```text
The picture a built reel puts on the frame, and the gate that reads it.

Every number in this file is either arithmetic or a measurement recorded
elsewhere in the repository.  The two that are measurements:

- **rows 656..1264** for 3840x2160 fitted into 1080x1920.  Independently
  measured by ``render_qa``'s occupancy pass on project 001's real export
  and recorded in ``library/tools/framing_intent.py`` as rows 656..1263,
  608 of 1920 rows.  A different project, different source files,
  different resolution, and the arithmetic here lands on the same top row
  and one below on the bottom - 607.5 rounded.
- **the twenty harvest reels** of the GEO Podcast field test, read off
  Resolve on 2026-09-06: 376 video items across 49 timelines, every one
  of them ``ZoomX=ZoomY=1.0, Pan=Tilt=0, Crop*=0`` on a 1080x1920
  ``scaleToFit`` timeline.  ``_HARVEST`` below is that transform.

AGENTS.md 10.4: a gate that cannot fail is worse than no gate, and one
that fails correct output is the same defect from the other side.  Both
directions are pinned here, on the geometry the captain's reels really
carry.
```
