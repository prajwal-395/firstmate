# Timed text overlay: the tests' history

Moved from the module docstring of `tests/unit/captions/test_timed_text_overlay.py`.

The component and its prop generator survive: the captain confirmed on
2026-08-20 that N timed text moments with per-item colour, size, start frame
and fade IS a general engine component, and all eight series want intro cards
and episode text.

What did NOT survive is the one asset that declared it. The 4th Wall night card
and closing "end card" ritual were lifted verbatim out of a previous manual
trial run and checked into the now-deleted `fourth_wall.yaml` as series
DEFAULTS - absolute frame numbers baked to that run's 60.000s timeline,
normalised y positions authored against a full-bleed vertical frame, and an
unbundled typeface. Captain, 2026-08-20: "it was something made in a previous
trial run and is a pretty shoddy asset, so lets just get rid of it". (A
source-scan test that the asset's strings - `FourthWallOverlay`, "Attack the
day tomorrow", "It's 2:16.", "1 / 100" - stayed out of the engine was retired
in the 2026-10 suite halving as a source-text pin.)

And what did not EXIST until then is a reader. #119 shipped the schema field,
the generator and the Remotion composition, `docs/PIPELINE_PLAN.md` recorded
the gap as closed, and no step in `library/steps/` ever imported the generator
- so three declared moments reached no frame of any render and every run still
reported SUCCESS. `library.tools.timed_text_overlay` held the slot shut with a
`NO_READER` constant until the step that reads it landed; both are gone
together. `test_a_pipeline_step_reads_the_slot` is the inverse guard.

A sweep over `library/templates/*.yaml` asserting an undeclaring template plans
no segments was removed when #1262 deleted the in-engine templates; do not
restore a sweep over a directory the product deliberately does not ship.
