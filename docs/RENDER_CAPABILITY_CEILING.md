# What this pipeline can draw, and what it cannot

Measured 2026-09-07 against `1eb2a8c` (PR #606, the full-frame lane).

The inventory itself is NOT in this file. It is derived, by
`library/tools/render_capability_index.py`, from the vocabularies and the
compositions' own source:

```sh
python3 -m library.tools.render_capability_index
```

A hand-written table here would rot. `assert_index_is_consistent()` fails a
test the moment the two halves disagree, in both directions.

Where it stands after this lane: **43 draws, 1 not yet, 1 refused, 0
disagreements.**

After the website-panel lane: **46 draws, 1 not yet, 1 refused, 0
disagreements** - `website_panel` plus the `flip` entrance and exit.
`python3 -m library.tools.render_capability_index` is the inventory;
what follows is the reading of it.

## 1. The ceiling moved, and the join is what found it

A drawing capability lives in two files that nothing joined. The Python half
declares an element and a reachability flag; the TSX half either has a node
or it does not; `motion_graphics_plan.DRAWABLE` is derived from the Python
flag **alone**. Nothing compared them.

| Surface | Declared | Drew | Draws now |
|---|---|---|---|
| Overlay elements | 16 | 13 | **16**, then **17** with `website_panel` |
| Full-frame elements (#606) | 1 | 1 | 1 |
| Entrance characters | 9 | 8 | **9**, then **10** with `flip` |
| Exit characters | 9 | 8 | **9**, then **10** with `flip` |
| Anchors | 10 | 9 (+1 refused) | 9 (+1 refused) |

### 1.1 `lower_third` - drawn, and declared unreachable

Flagged `needs_renderer_work`, note *"No component. Also needs a
caption-collision rule"*. The component had been written (#602). Because
`DRAWABLE` derives from the flag, `resolve_plan` dropped every planned lower
third as `renderer_cannot_draw_it_yet`.

**A declaration understating the renderer costs a real capability just as
silently as one overstating it.** This is the inverse of the defect class
this project has spent a week removing, and nothing looked for it. Fixed by
building the rule the note was waiting on (§2) and flipping the flag.

### 1.2 `exit: "typewriter"` - a fade wearing another name

`exitTransform` carried `case "typewriter": return {};` under a comment
saying characters *"disappear in reverse"*. No reverse reveal existed; the
reveal was computed from the ENTRANCE field alone.

| | ink x-range | covered px |
|---|---|---|
| held | 232..847 | 23,771 |
| mid-exit, before | 235..845 | 20,273 |
| mid-exit, after | **379..690** | **10,412** |

The 15% drop before the fix is the alpha threshold losing faint edge pixels
as opacity falls - the glyphs were all still there.

**The same defect had just landed in `FullFrameCard`.** #606 factored the
reveal into a shared `typewriterProgress(localFrame, entrance)` and the card
composition calls it, so `exit: "typewriter"` on a full-frame card was a fade
too. Both now read `typewriterShown(localFrame, durationFrames, entrance,
exit)`, which takes both ends.

### 1.3 `glitch` - a chromatic split that never reached a frame

Both arms returned the RGB split as `textShadow` on the container. `Runs`
sets its own `textShadow` on every run, which **overrides an inherited one**.

| | px with \|R-B\| > 40 | max \|R-B\| |
|---|---|---|
| glitch entrance, before | **0** | **0** |
| glitch entrance, after | 3,446 | 218 |
| control: a deliberately red element | 5,774 | 255 |

The control is there because a zero from an unvalidated instrument is not
evidence. Carried now as a `filter: drop-shadow` chain, which composites the
rendered subtree rather than being a text property: it survives a child
restating its own shadow, and it draws the split on the elements carrying no
text at all - bars, brackets, rules - which `textShadow` never could. **The
magnitudes are unchanged**; only the CSS property.

### 1.4 `centre`, the anchor served by the fallback

`anchorStyle` had no `case "centre"`. It worked, via the same `default` arm
that catches an anchor nobody recognises - so in the source the declared
value and the fallback were indistinguishable. Now explicit.

## 2. The caption-collision rule

`library/tools/caption_band.py` - the executable form of a refusal
`lower_third`'s own `never` already declared in prose. **No thresholds and no
taste.** Three facts, each read from where it is already stated:

- **Which band the captions occupy**: the project's declared caption
  `position`, plus every per-speaker override. The union.
- **Which anchors lie in a band**: the anchor's own name, the way
  `anchorStyle` decomposes it.
- **When a card is on screen**: the blocks 4.01 captions, which is
  `block_type in ("hook", "speech")` and nothing else.

Refused only for an element that draws COPY - two blocks of text in one band
is unreadable; a bar, a bracket or a bug is not a collision. Measured:

```
lower third, bottom, over speech           -> dropped: collides_with_the_caption_band
lower third, bottom, over non-speech       -> DREW
lower third, top, over speech              -> DREW
progress bar, bottom, over speech          -> DREW
frame accents, bottom, over speech         -> DREW
no band information supplied               -> DREW
```

**The assumption, said out loud**: it reads the spine, not the caption plan,
because 4.06 has no DAG edge from `plan_subtitles`. A run that deliberately
skips `render_subtitles` gets a refusal it did not need - and the drop record
names this rule, so the operator sees which rule cost them the element.

## 3. What the real-reel composite found that no demo card could

`data/vep-animation-completeness/all_elements_over_real_reel.png` in the
firstmate home is every
reachable element at once, over a real reel frame at delivery size. It shows
something nine separate demo renders could not: **elements at different
anchors draw straight through each other.**

`row` stacks elements sharing ONE anchor. It says nothing about two anchors -
and `anchorStyle` gives a `*_centre` element `left: safeArea.left` AND
`right: safeArea.right`, the whole usable width. So a centred title and a
corner stamp in the same band, live at the same moment, collide by
construction. In the composite: the title through the context stamp, the
quote card through the lower third and the stat callout, the step counter on
the progress bar.

`motion_graphics_plan.overlapping_pairs` measures it, and it is
**REPORTED, not enforced** - it travels on `basis_record()` as
`drawn_through_each_other`. A full-width `progress_bar` under a `bottom_left`
counter may be exactly what the plan meant, and a gate that fails correct
output is no more coverage than one that cannot fail (AGENTS.md 10.4). Each
pair carries `involves_chrome`, read from the roster's own `persist`
function, so chrome-under-content and content-through-content are
distinguishable without this module deciding which a pair is.

**Fixing the layout is the top unbuilt item** (§5). It is not built here
because every route to it - shrink-wrapping a centred element, reserving
columns, an automatic reflow - is a decision about how the frame is laid out,
and that is the captain's, once.

## 4. Remotion Bits and its MCP server - evaluated

Flagged in `data/vep-graphics-fidelity/report.md` in the firstmate home as
reachable and
unevaluated. It is evaluated now, and **three of that report's claims about
it are wrong.**

`remotion-bits@0.2.0`, MIT, published 2026-03-10. The MCP server is real and
starts. Probed directly over stdio:

```
initialize -> {"protocolVersion":"2024-11-05","capabilities":{"tools":{}},
               "serverInfo":{"name":"remotion-bits","version":"0.2.0"}}
tools/list -> find_remotion_bits, fetch_remotion_bit
```

The tools are `find_remotion_bits` / `fetch_remotion_bit`. The earlier report
called them `find` and `fetch` - those are the **CLI** subcommands; a call to
`find` returns `-32601 Unknown tool`. The catalogue is **42 bits**, not the
~44 read off the docs sidebar (text 16, 3d 12, motion 9, particles 7,
staggered-motion 6, transition 4, background 4).

**Claim 1, wrong: "fetch retrieves the component's TypeScript source."** It
returns the usage EXAMPLE. `basic-typewriter` in full is 39 lines whose body
is `<TypeWriter text="Ah, those sunny days!" cursor />`, importing
`TypeWriter` from the npm package. Same for `bit-glitch-in`,
`bit-blur-slide-word`, `bit-list-reveal`.

**Claim 2, wrong: "zero new runtime dependencies if copying component
source."** `npm i remotion-bits` pulls `three`, `@types/three`,
`prism-react-renderer` and `@modelcontextprotocol/sdk`. The copy-source route
does exist, but not where the report said: the npm tarball ships
`package/src/` with the real `.tsx` (`TypeWriter.tsx` 408 lines,
`AnimatedText.tsx` 189, `AnimatedCounter.tsx` 132), MIT.
`https://remotion-bits.dev/r/<name>.json` and `/jsrepo-manifest.json` both
return the site's SPA HTML - there is no registry to `jsrepo add` from.

**Claim 3, wrong: "step 4.06's model could call it at plan time."** It cannot.
`library/tools/llm_client.py` calls `messages.create` /
`chat.completions.create` / `generate_content` with **no `tools` parameter**
and no tool-use loop; `grep` for `tools=`, `tool_use` or `mcp` across
`llm_client.py`, `run_pipeline.py` and `step_4_06_*/` returns nothing. The
`agent` backend writes a request JSON and polls for a response JSON - the
answering harness is external, nothing asks it for an MCP server, nothing
records whether one was used, and the pipeline cannot require it. **And even
if the model fetched source, there is no route to a frame**: the composition
is fixed, type-checked TSX bundled by `npx remotion render`, so getting
model-fetched source onto a frame means compiling model-authored TypeScript
into the render bundle at run time.

**Verdict: reachable from a developer's terminal, not from this pipeline.**
Its value here is as a catalogue to read while deciding what to build. The
components worth having are copied from the npm tarball's `src/` by hand
under MIT - which is what #602 already did for `BasicTypewriter`, `BlurIn`,
`GlitchIn` and `AnimatedCounter`.

## 5. The remainder, ranked

| Rank | What | Cost | Automatable, or a human? |
|---|---|---|---|
| 1 | **Cross-anchor layout** (§3). Elements in one band drawn through each other. | The detector is built and reports. The repair is a layout policy. | **A human, once.** Every route is a decision about how the frame is laid out - shrink-wrap a centred element, reserve columns, reflow. That is taste and it is the captain's. |
| 2 | `tracked_label` on a **face** | `compute_face_presence` already measures `face_center_x` and `face_width` at 5 Hz. It does **not** record `face_center_y` - the cascade returns `(x,y,w,h)` and the y is discarded. Recording it, a DAG edge from `temporal_index` to 4.06, a per-frame track in props, and a re-run of `temporal_index` per project. | **Automatable**, medium. This is one discarded measurement, not a SAM 2 re-wiring - which corrects the roster's own note, whose "a track for a face and not for an object" reads as though SAM 2 were the only route. |
| 3 | `tracked_label` on an **object** | Needs `object_segmentation` (1.06) wired, unwired for GPU-memory reasons (`docs/SUBJECT_MASKING_MEASURED.md`). | Engineering decision on GPU budget. |
| 4 | More Remotion Bits characters (`MatrixRain`, `ParticleSystem`, `Scene3D`) | Copy from the npm tarball's `src/`, MIT. `three` for the 3D ones. | Automatable, but **nothing asks for them.** A character with no planner asking for it is a vocabulary entry that draws nothing - the defect this lane removed three of. |

### Built in this lane

`channel_bug` was rank 1 of the remainder and is built. The staging half
already existed (`remotion_brand_linker.link_brand_assets` copies a project's
`brand_assets/` into Remotion's `public/brand/`); what was missing was a
drawing node and a way to turn a NAMED file into a staged path.
`generate_motion_props.project_asset_resolver` is that, and it stages
**lazily**, on the first asset actually asked for, so a run planning no asset
copies nothing.

The engine still ships no artwork (AGENTS.md 14). An entry naming a file the
project does not have is dropped as `asset_not_found_on_disk`; one naming no
file at all as `no_asset_for_an_element_that_needs_one`. Both measured:

```
channel_bug with a real project asset -> DREW  (asset: brand/acme_bug.png)
channel_bug naming a missing file     -> dropped: asset_not_found_on_disk
channel_bug naming no asset           -> dropped: no_asset_for_an_element_that_needs_one
```

Rendered at 1080x1920 it puts 16,027 pixels inside the safe area (bbox x
811..952 against a right inset at 960, y 128..269 against a top inset at 120).

## 6. Honest limits, with evidence

- **The pipeline cannot call an MCP server, or any tool, at plan time.**
  Evidence in §4. A property of `llm_client.py`, not of the model.
- **Model-authored components cannot reach a frame.** The render bundle is
  fixed TSX compiled ahead of the render.
- **`anchor: "tracked"` is correctly refused, not missing.** `resolve_plan`
  drops it as `anchor_needs_a_measurement_nothing_takes`; pinning it to a
  fixed point would be the engine choosing a position.
- **A source parse cannot prove a node draws.** `render_capability_index`
  proves a node EXISTS. Two characters passed that bar and drew nothing.
  `tests/test_motion_character_draws.py` carries the other half, by
  rendering.
- **The renderer is still not the limit.** Every fix here was React and CSS.
  No renderer change, no pipeline change, no Resolve change. The earlier
  report's central conclusion holds.

## 7. Being sceptical of the instrument

The render test's measurement was **wrong twice**, and both times it passed
against a build with the defect deliberately re-introduced:

1. **Alpha IoU against the held frame.** Measured, `fade` scores 0.873 and
   `glitch` 0.917 - so an IoU test calls the character that draws nothing
   extra MORE distinct than the one that draws an RGB split.
2. **Ink width, after normalising alpha by its peak.** A drop shadow's faint
   tail quantises to alpha 0 at low opacity and no normalisation recovers a
   zero, so every ramped frame's box is a few pixels tighter whatever the
   character does. `test_typewriter_reveals_in_both_directions` passed
   against a build whose typewriter exit was disabled.

What works is **`fade` as the control**: it changes opacity and nothing else,
so whatever box drift it shows IS the drift opacity causes, and a character
has to beat it. With that control in place, re-introducing each original
defect fails the suite:

```
BREAK: typewriter exit disabled
  FAILED test_every_declared_character_changes_the_frame[typewriter-exit]
  FAILED test_typewriter_reveals_in_both_directions[exit]
BREAK: glitch split back to textShadow
  FAILED test_glitch_separates_the_colour_channels[entrance]
  FAILED test_glitch_separates_the_colour_channels[exit]
```

## 8. What this lane did not do

- The **explainer lane** had not landed and is not covered.
- The **cross-anchor layout repair** is measured and reported, not fixed (§5,
  rank 1) - the repair is taste.
- No project was re-run end to end. The evidence is stills at delivery size
  composited over a real reel frame, not a rebuilt reel.

## 9. The website-panel lane (2026-09-08) - what it built and what it refused

The captain's 2026-09-04 instruction ("take a website ... chroma key
that in, or use some kinda alpha setting ... models and graphics that
popup") named two capabilities the roster did not have. Both are built;
the two readings that look like them and are not are refused here, with
the reason, so a later lane does not re-decide them by accident.

**Built: `website_panel`.** A project-supplied still capture of a page,
framed in a drawn browser chrome whose address bar shows the plan's
copy when it states any, composited with the alpha the engine already
requires. The capture travels the `channel_bug` route - a named file
out of the project's own `brand_assets/`, staged verbatim by
`remotion_brand_linker`, resolved lazily by
`generate_motion_props.project_asset_resolver` - so no new mechanism
was built beside the old one. `tests/test_website_panel.py` pins it:
resolves with a staged asset, drops as `no_asset_for_an_element_that
_needs_one` with none named and `asset_not_found_on_disk` with a
missing one, and puts ink on a rendered frame (failed beforehand with
"drew nothing at all", verified both ways round).

**Built: `flip`.** A CSS-3D quarter-turn entrance and exit
(`perspective` plus `rotateY`, edge-on to facing on arrival, facing to
edge-on the other way on departure) - the popup half of the
instruction, with no new renderer dependency. Declared on the
vocabulary's entrance/exit axes, drawn in both transform switches, and
covered by the existing render-backed parametrisation in
`tests/test_motion_character_draws.py`, which reads new axis positions
as new cases on its own.

**Refused: a live website fetch at render time.** The roster entry says
it: a fetch needs the network, answers differently when it is
repeated, and draws artwork nobody supplied. The capture is taken by
whoever publishes the video and staged verbatim - the same line
`channel_bug` draws between engine and project.

**Refused: true 3D-model rendering (`@remotion/three`).** No project
owns a model asset - the same measurement that closed the chroma-key
question in `transition_overlay.py` (no green-screen source on any
project or library) applies: a renderer for assets nobody holds is a
capability with no planner asking for it, the defect §5 rank 4 names.
It would also add a runtime 3D dependency to `remotion-subtitles/` for
one element. If a project ships a model, the honest route is an
asset-axis entry like this one, with the model file staged verbatim -
not a second renderer beside the composition.
