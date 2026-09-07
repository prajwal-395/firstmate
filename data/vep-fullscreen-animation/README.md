# Full-screen animation: what was rendered, and where to look

Evidence for `docs/FULL_FRAME_ELEMENTS.md`. Project `geo-podcast`, reel 07
(`number-one-on-google-invisible-to-ai`), built into its own container
`[ff-lane8]` in Resolve, read back, then deleted along with every media-pool
item it added.

## The full-frame card

| file | what it is |
|---|---|
| `card_hold.png` | The card at hold. 1080x1920, opaque, ink rows 597..1134. The copy is the reel's OWN opening line, quoted verbatim through `reel_opening.opening_words`, plus its speakers. |
| `card_entrance_blur.png` | Frame 2 of 53 - the declared `blur` entrance mid-ramp. |
| `card_then_picture.mp4` | 53 frames of card, then 120 frames of the reel's first V1 clip at the framing this project actually delivers. The cut is what a viewer sees. **Not committed** - `*.mp4` is gitignored, so the two frames below are what ships. |
| `card_then_picture_f40_card.png` | Frame 40: still the card, mid `fade` exit. |
| `card_then_picture_f70_picture.png` | Frame 70: picture, rows 656..1263 - the 31.6% strip this project's `framing_intent: 0.0` declares. |
| `real_reel_frame.png` | The same reel frame with no graphic on it, for comparison. |
| `reel_07_card_01_props.json` | The props the pipeline generated, not hand-written. |
| `proof_project.yaml` | The `effect.full_frame_elements` declaration that produced all of it. |

Measured on `card_then_picture.mp4` across the boundary, LUMA plane mean:
11.22 at frame 44 -> 4.26 at frame 50 (the declared `fade` exit really draws),
then 16.64 at frame 51 and steady through 16.74 at frame 70 - picture.
**No black frame between them**: the fade's darkest frame is followed directly
by a brighter one, not by a hole.

Read back off Resolve after the build:

```
1080x1920 @ 23.9760  total_frames=2023
V1 [    0..   53) dur=  53  reel_07_card_01.mov
V1 [   53..  163) dur= 110  LC4932.MXF
V3 [   53..  126)            sub_..._akshita_0_927518-930622_....mov
A1 [   53..  163)
```

The first build placed the card with `endFrame: duration_frames - 1` and it came
back 52 frames long, leaving a one-frame black hole at frame 52. **F1 and F13
both reported it** before anything else noticed; `endFrame` is EXCLUSIVE.

## PR #602's elements over a real reel frame

`pr602_over_real_f*.png` are PR #602's own `demo_props.json`, unchanged,
composited over `real_reel_frame.png` instead of over the generated fractal in
`data/vep-graphics-rest/composited_on_reel.png`.

| frame | what is on screen | ink ON PICTURE |
|---|---|---|
| 15 | title_lockup mid-typewriter, frame_accents, progress_bar | **0.0%** |
| 45 | title_lockup, digit_counter | 45.4% |
| 145 | quote_card glitch entrance | 76.3% |

The elements draw correctly. What they land ON is the finding: this project
delivers picture in rows 656..1263 only, so anything anchored to the DELIVERY
frame's safe area sits in the letterbox. See `docs/FULL_FRAME_ELEMENTS.md` §7.

## Reproducing

```sh
python3 -m library.tools.full_frame_element              # the roster
python3 -m library.tools.full_frame_element --check      # the roster, as a gate
python3 -m pytest tests/test_full_frame_element.py \
                  tests/test_reel_conformance_full_frame.py -q
```
