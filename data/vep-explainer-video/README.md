# Animated explainer: what was rendered, and how to reproduce it

Evidence for `docs/ANIMATED_EXPLAINER.md`. Everything here comes from the
field-test project's own data - its transcript, its approved plan, its
judgement, its accent colour - and from one added declaration.

## The files

| file | what it is |
|---|---|
| `explainer_over_reel_40s.jpg` | reel 21 at 40.2s: the explainer has not started |
| `explainer_over_reel_42s.jpg` | 42.5s: three stages up, as the speech names them |
| `explainer_over_reel_46s.jpg` | 46.5s: all six |
| `overflow_twelve_items_clipped.jpg` | the FAILING case: twelve items in the same band, cut off by the frame |
| `explainer_over_band_42s.jpg` | the SAME reel with `band: over` - three stages, drawn ON the picture |
| `explainer_over_band_46s.jpg` | 46.5s in the `over` band: all six, the last two across the hands and the table |
| `reel_21_explainer_plan.json` | what the build recorded - the stages, their reel seconds, the band, and the rendered segment |
| `proof_project.yaml` | the declaration the render was produced from |

Every composite is the reel's **real delivered picture** - the source MXF at the
matching source second, fitted 3840x2160 into 1080x1920 exactly as
`reel_framing` measures the reels deliver - with the rendered ProRes 4444
overlay composited over it. They are scaled to 540 wide for the repository; the
measurements in the doc are taken on the full 1080x1920 frames.

## Both bands were rendered, because only one of them is safe by construction

`explainer_over_reel_*.jpg` is `band: above` - the top letterbox bar this
project's `framing_intent: 0.0` leaves empty. Nothing plays there, so nothing
can compete with the graphic and `picture_covered_fraction` is `0.0`.

`explainer_over_band_*.jpg` is the same reel, the same six stages and the same
seconds, re-rendered with `band: over`. That is what a project which FILLS the
frame gets, and FILL is the engine's own default (AGENTS.md 10.3), so it is the
case most projects would actually hit. The build reports it - `covers_picture:
true`, `picture_covered_fraction: 0.3167` - and the picture is measured, not
assumed: the band is `picture_bands` intersecting `reel_framing`'s rectangle
with `safe_area`'s insets, and a band with no height REFUSES rather than
drawing off-frame.

**What the `over` frames show is that legibility there is a property of the
FOOTAGE, and nothing in the reels process measures it.** The first three stages
sit on the dark acoustic wall and read cleanly; the last two cross the speaker's
hands and the table's specular highlight and read markedly worse. That is not a
defect in the plan - the stages are where the words put them - and it is not
something this lane invented a threshold for. It is the gap `reel_framing`
already recorded from the other side: `picture_quality`, `temporal_index` and
`render_qa` all live in `edit_video`, the reels process runs none of them, so a
reel has no measurement of what its own picture is doing at a given second.
`band_report` therefore says the graphic covers the picture and stops there
rather than claiming a legibility it did not measure.

## `proof_project.yaml` is not a change to the captain's project

It is a copy of `video_projects/lucie/geo-podcast/project.yaml` with one slot
added - `effect.explainer` - so this render can be reproduced without editing
the captain's project to make a demonstration work. Everything else in it is
theirs, verbatim. **Nothing in this lane wrote to that project.**

## Reproducing

The build path is `reel_build.reel_explainer_segments`, which is what
`build_reels` (`reel.build`) calls. Driving it for one reel, outside Resolve:

```python
import json
from library.tools import reel_build
from library.tools.reel_proposal import ReelMoment

P = "<the geo-podcast project>"
tr = json.load(open(f"{P}/pipeline_output/scratch/timeline_transcript/transcript.json"))
sel = json.load(open(f"{P}/pipeline_output/review/reel_proposals_v2.json"))
moment = ReelMoment.from_dict(
    [m for m in sel["moments"] if int(m["number"]) == 21][0])

segments, plan = reel_build.reel_explainer_segments(
    moment, tr, reel_build.reel_ranges(moment, tr),
    project_folder="<a copy of the project carrying proof_project.yaml>",
    fps=24000 / 1001, width=1080, height=1920,
    judgement=reel_build._read_judgement("<that folder>"),
    brand_effect={}, timeline_name="Reel 21 - the-website-is-a-fifth")
```

`judgement` must carry `claim_parts` for reel 21. That field is step 3.05's, and
the six parts used here are in `reel_21_explainer_plan.json` under
`anchored.stages` with the quote each was grounded against. Every one of them is
checked by `reel_quality_bar.check_reading` before the build sees it: a quote
that is not in what the reel says refuses the whole reading.

Measuring a render on its own:

```python
from library.tools import explainer_plan as ex
ex.measure_render("<the rendered .mov>")
```
