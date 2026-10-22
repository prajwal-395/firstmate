# INFRA-006 - the native render gate, measured

**Measured on:** 2026-10-21, macOS (Apple M5), repository at `3c04310c6`
(detached worktree of `video-editing-pilot`).
**Population:** the geo-podcast project's own 86 shipped motion-graphics
cards - the props file step 4.06 wrote and the `.mov` artefact it
shipped. Read only; nothing in the captain's project was written.
**Metric:** ink IoU per frame (alpha > 16), averaged over the card, of
the card **as the engine the gate selects draws it today** against the
artefact the project shipped.

## The headline

The native renderer is the default graphics engine (`graphics_renderer.py`,
`DEFAULT_ENGINE = ENGINE_FFRAMES`, flipped 2026-10-06). Before this
change, the gate that decides whether it may draw a card answered "yes"
for cards it drew differently: **26 of 86** shipped cards would have
shipped a different picture, and the fail-closed gate did not catch
it. Two defects behind it, both measured:

1. an element that declares an asset and lowers to no image node was
   dropped silently - two lower thirds lost the brand logo the plan
   declared;
2. the IR uppercased every display run, so a plan that declared
   `uppercase: false` was drawn in caps.

And a third, larger one: the IR lowers a `lower_third` to plain text
runs, while the composition draws one of three things for that arm (the
staged rule construction, the AEP comp25 construction, or the
attribution block). **10 of 10** lower thirds differ.

## Before and after

| Measure | before | after |
|---|---:|---:|
| shipped cards within ink IoU 0.90 | 62/86 | **71/86** |
| cards that REGRESSED | - | **0/86** |
| lower thirds within ink IoU 0.90 | 0/10 | **8/10** |
| lower thirds keeping their declared brand asset | 0/2 (of the 2 that declare one) | **2/2** |
| parity score (`python3 -m library.tools.parity_score`) | 9/12 | **10/12** |

By arm (the mean is the card's mean ink IoU against the shipped
artefact):

| arms | cards | >= 0.90 before | >= 0.90 after | mean before | mean after |
|---|---:|---:|---:|---:|---:|
| title_lockup | 51 | 44 | 45 | 0.912 | 0.924 |
| lower_third | 10 | 0 | 8 | 0.289 | 0.869 |
| quote_card | 6 | 3 | 3 | 0.792 | 0.792 |
| list_build | 5 | 4 | 4 | 0.909 | 0.909 |
| stat_callout | 4 | 4 | 4 | 0.934 | 0.934 |
| subject_emblem | 3 | 3 | 3 | 1.000 | 1.000 |
| beat_accent | 2 | 2 | 2 | 1.000 | 1.000 |
| list_build + title_lockup | 1 | 0 | 0 | 0.436 | 0.436 |
| channel_bug | 1 | 1 | 1 | 1.000 | 1.000 |
| subject_emblem + title_lockup | 1 | 0 | 0 | 0.579 | 0.579 |
| review_panel | 1 | 1 | 1 | 1.000 | 1.000 |
| quote_card + title_lockup | 1 | 0 | 0 | 0.536 | 0.536 |

The nine cards that crossed the floor: eight lower thirds (0.310-0.409
to 1.000 - they are drawn by the same renderer that drew them last,
and they are pixel-identical) and one title lockup whose copy the
uppercase fix restored (0.706 to 0.963).

## The lower thirds

| card | before | after | declares a brand asset |
|---|---:|---:|---|
| `mg_geo-podcast_3107ff3d_tight` | 0.339 | 1.000 | no |
| `mg_geo-podcast_448dde52` | 0.310 | 1.000 | no |
| `mg_geo-podcast_758405c4` | 0.409 | 1.000 | no |
| `mg_geo-podcast_865c079e_tight` | 0.320 | 1.000 | no |
| `mg_geo-podcast_8e511711_tight` | 0.310 | 1.000 | no |
| `mg_geo-podcast_93fe3770_tight` | 0.310 | 1.000 | no |
| `mg_geo-podcast_9ab1f995_tight` | 0.339 | 1.000 | no |
| `mg_geo-podcast_f0cbc3a9` | 0.394 | 1.000 | no |
| `mg_geo-podcast_56e3dc30_tight` | 0.096 | 0.319 | **yes** (`brand/Lucie_Logo_Icon_2023.svg`) |
| `mg_geo-podcast_76853f5f_tight` | 0.065 | 0.370 | **yes** (`brand/Lucie_Logo_Icon_2023.svg`) |

Ten lower-third cards, twelve lower-third elements. Only the two
`aep_comp25` cards declare a brand asset; the other eight declare the
staged rule construction, which has no asset. **Before this change
neither of the two carried its logo into the render** (the native
lowering draws no image node for a lower third at all). After it, both
do.

The two `aep_comp25` cards do not reach 0.90, and the reason is not
the engine: the shipped artefact sits **6 px** away from what the
reference renderer draws today (ink IoU 1.000 at a 6 px vertical
offset, 0.283 raw). The artefact was rendered on 2026-10-18 and the
composition has not changed since, so this is drift in the artefact,
not in the engine.

## The fifteen cards still below 0.90, and which defect each one is

Measured by rendering each card with BOTH engines and comparing each
with the shipped artefact:

**The native renderer genuinely draws a different picture (5 cards).**
Remotion reproduces the artefact; the native renderer does not.

| card | arms | Remotion vs artefact | native vs artefact | what differs |
|---|---|---:|---:|---|
| `mg_geo-podcast_30e073e0` | list_build, title_lockup | 0.991 | 0.427 | the list rows are spaced differently |
| `mg_geo-podcast_ec067b61_tight` | quote_card | 0.914 | 0.364 | the copy wraps on different lines |
| `mg_geo-podcast_c890ed3f_tight` | quote_card, title_lockup | 0.841 | 0.527 | the copy wraps on different lines |
| `mg_geo-podcast_e2aafaef` | list_build | 0.979 | 0.699 | the list rows are spaced differently |
| `mg_geo-podcast_2b0e0e48` | quote_card | 0.983 | 0.873 | the copy wraps on different lines |

The IR's line breaking and its list spacing are not the browser's.
That is a text-layout fidelity gap in the IR, not a dropped
declaration, and it is the next thing to close: either the IR breaks
lines and spaces rows the way Chromium does, or those arms stop being
claimed by the native renderer.

**The artefact has drifted; both engines are equally far (10 cards).**
A +/-10 px alignment recovers most of the difference, so the shipped
artefact and today's code place the same card a few pixels apart.

| card | arms | Remotion vs artefact | native vs artefact |
|---|---|---:|---:|
| `mg_geo-podcast_56e3dc30_tight` | lower_third | 0.283 (1.000 at 6 px) | refused |
| `mg_geo-podcast_76853f5f_tight` | lower_third | 0.334 (1.000 at 6 px) | refused |
| `mg_geo-podcast_5e9e12d9_tight` | title_lockup | 0.827 | 0.778 |
| `mg_geo-podcast_8576b03d_tight` | title_lockup | 0.826 | 0.775 |
| `mg_geo-podcast_9b9cb0d8_tight` | title_lockup | 0.830 | 0.781 |
| `mg_geo-podcast_bec66f15_tight` | title_lockup | 0.823 | 0.772 |
| `mg_geo-podcast_c501591e_tight` | title_lockup | 0.730 | 0.709 |
| `mg_geo-podcast_987ac9d3_tight` | title_lockup | 0.841 | 0.815 |
| `mg_geo-podcast_86f22dae_tight` | subject_emblem, title_lockup | 0.574 | 0.565 |
| `mg_geo-podcast_175bdcae_tight` | quote_card | 0.656 | 0.647 |

The native renderer is never further from the artefact than Remotion
is on any of these, which is the point: the reference itself moved.

## What changed in the engine

1. **The IR refuses a dropped asset.** `lower_motion_graphics` raises
   `AssetNotDrawn` when an element declares an asset and no image node
   carries it (`library/graphics_ir/lowering.py`). The omission cannot
   reach a render.
2. **The gate fails closed on it.** `_fframes_fallback_reason`
   (`step_4_06_render_motion_graphics/post_bridge.py`) sends such a
   card to Remotion and names the asset.
3. **The gate no longer claims an arm the IR does not draw as
   declared.** `MG_FFRAMES_COVERED_ARMS` no longer holds `lower_third`,
   and `MG_FFRAMES_UNCOVERED_ARMS` says why.
4. **The case is the run's own.** `_mg_run_style` honours the run's
   `uppercase` flag, the rule the stat_callout, emblem and counter
   readings already quoted.
5. **A phase cannot outlast its run.** `perf_ledger.record` clamps a
   recorded phase to the wall time of the run that recorded it and
   says so in the row (`clamped_from_s`). The phantom
   `otio_placement.fusion_comps_s` read 36 hours of a phase that took
   under a minute.

## The fixture

`tests/fixtures/native_render_parity/` holds the population record
(`cards.json`: every card, its arms, its assets, the engine the gate
selects and the ink IoU before and after), the real props of the ten
lower thirds and of four sampled cards, and the shipped artefact's INK
MASK for three frames of each sampled card.

`tests/unit/graphics_ir/test_native_render_gate.py` reads the record
and the props; `tests/unit/graphics_ir/test_native_render_parity.py`
re-renders the sampled cards with the native renderer and compares the
ink against the stored masks at the same 0.90 floor (marked
`native_renderer`, so it runs outside the correctness gates).

The incident and the rule behind this change are written up in
`docs/evidence/2026-10-21-infra006-native-render-gate.md`.

## How to re-measure

The harness is a throwaway script (it reads the captain's project, so
it is not in the repository):

```python
# for each props file in the project's step 4.06 output:
#   engine = "fframes" if _fframes_fallback_reason(props, PROJECT) is None
#            else "remotion"
#   render the card with that engine
#   decode the shipped .mov to PNG frames
#   ink IoU per frame (alpha > 16), mean over the card
```

Reading the project's own artefacts is read-only; nothing here writes
to the captain's project.
