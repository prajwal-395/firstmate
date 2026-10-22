# Evidence - INFRA-006: the native render gate failed open on what it drops

**Measured on:** 2026-10-21, macOS (Apple M5), repository at `3c04310c6`
(detached worktree of `video-editing-pilot`).
**Population:** the geo-podcast project's own 86 shipped motion-graphics
cards - the props file step 4.06 wrote and the `.mov` artefact it
shipped. Read only; nothing in the captain's project was written, and
no broker was started.
**Metric:** ink IoU per frame (alpha > 16), averaged over the card, of
the card **as the engine the gate selects draws it today** against the
artefact the project shipped.

The report with the full per-arm table and the classification of every
card still below the floor is
`docs/benchmarks/2026-10-21-infra006-native-render-parity.md`. This
file is the incident and the rule.

## The incident

The native renderer is Ren's default graphics engine
(`library/tools/graphics_renderer.py`, `DEFAULT_ENGINE = ENGINE_FFRAMES`,
flipped 2026-10-06 in `b6cde2753`, PR #1948). The gate that decides
whether it may draw a card - `_fframes_fallback_reason` in
`library/steps/step_4_06_render_motion_graphics/post_bridge.py` - is
supposed to be fail-closed: an arm with no lowering, an image node with
no bounds, and anything it cannot read are all named, and the card goes
to the Remotion render explicitly.

It was open in three places, all measured on the project's own cards:

1. **An element that declares an asset and produces no image node was
   dropped silently.** The IR lowers a `lower_third` to plain text
   runs, so the brand asset the plan declared
   (`brand/Lucie_Logo_Icon_2023.svg`) was never drawn - ink IoU 0.096
   and 0.065 against the artefact the project shipped. The gate
   answered "FFrames can draw" for 10 of the 10 lower thirds, because
   it only rejected a BOUNDLESS image node, and a dropped asset leaves
   no image node at all.
2. **The IR uppercased every display run.** `_mg_run_style` read
   `"uppercase" if display else None`, so a plan that declared
   `uppercase: false` was drawn in caps: `AKSHITA GORTI` where the reel
   says `Akshita Gorti`. The same file already had the correct reading
   in two other places (`_mg_emblem_label_style`, `_mg_counter_label_style`),
   and the composition's `runStyle` takes the run's own flag.
3. **The arm was claimed although the IR does not draw it as the plan
   declared.** The composition draws one of three things for a
   `lower_third` - the staged rule construction, the AEP comp25
   construction, or the attribution block - and the IR draws plain text
   runs for all three: no rule, no wipes, no brand typeface, no brand
   asset.

**26 of the 86 shipped cards** would have shipped a different picture,
and nothing said so. Two of those cards are inside the delivered
Reel 26 - `mg_76853f5f` and `mg_56e3dc30`, 168 of the reel's 1104
frames, 15.2% of it, on screen.

## What was measured, before and after, with the denominators

| Measure | before | after |
|---|---:|---:|
| shipped cards within ink IoU 0.90 | 62/86 | 71/86 |
| cards that regressed | - | 0/86 |
| lower thirds within ink IoU 0.90 | 0/10 | 8/10 |
| lower thirds keeping their declared brand asset | 0/2 | 2/2 |
| parity score (`python3 -m library.tools.parity_score`) | 9/12 | 10/12 |

The scout's own measurement of the same population said 60/86
(`data/scout-infra006-native-render/report.md`, firstmate home); this
measurement says 62/86 before. The two differ by the ink threshold and
the frame alignment, not by the population: the same 86 cards, the same
artefacts, the same method on both sides of the change.

## The rule

**An element that declares an asset and produces no image node is an
ERROR in the IR lowering**, not a silent omission: the card would ship
without the asset the plan declared, and the omission is silent in the
picture. The gate then falls back to the renderer that does draw it,
and says which asset it could not draw.

**An arm the IR does not draw as the plan declared is not claimed by the
native renderer at all.** The covered-arm set is the mechanism, and
`MG_FFRAMES_UNCOVERED_ARMS` carries the reason.

**The case of a run is the run's own flag**, never the role's: copy
renders as written unless the run declares `uppercase: true`.

**A phase in the ledger cannot outlast the run that recorded it.** The
run's own id carries the instant it was minted at, so a phase longer
than the run is impossible; `perf_ledger.record` clamps it and the row
says so (`clamped_from_s`). The phantom this exists for:
`otio_placement.fusion_comps_s` read 131,422-167,228 s per reel
placement - 36 hours of "Fusion comps" for a phase the same runs
measured at 4.8-16.1 s - because a start timestamp that was really an
elapsed average was subtracted from the monotonic clock. Both
`perf_ledger.py` and `critical_path.py` read it as engine time, so
every "is Resolve or the native renderer cheaper?" analysis was reading
36 hours where the phase took under a minute.

## Where the evidence lives

- the population, the floor and the props: `tests/fixtures/native_render_parity/`
- the gate and the refusal: `tests/unit/graphics_ir/test_native_render_gate.py`
- the live parity check: `tests/unit/graphics_ir/test_native_render_parity.py`
- the clamp: `tests/unit/tools/test_perf_ledger.py`
- the measurement report: `docs/benchmarks/2026-10-21-infra006-native-render-parity.md`
