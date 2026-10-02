# Superseded subtitle props: leave and make visible

Tests: `tests/unit/picture/test_superseded_props_visible.py`. Moved from that module's docstring in the 2026-10 suite halving.

```textA re-plan must never leave a silent duplicate: two props files, one span.

POLICY (leave-and-make-visible): when a re-plan writes a new hashed
filename beside the old one - a plan entry that predates `card_index`
grouping under a None key, a source span shifted past the millisecond
the filename carries - the step LEAVES the old file and REPORTS the
pair, so nobody reading two props files for one span mistakes the
orphan for the live card.

Retire-with-archive was the other honest shape, and it lost on the
captain's standing rule: archive rather than delete, and a step that
cannot archive what it is retiring STOPS instead of proceeding. At
render time the step cannot prove the old file unreferenced -
reachability needs the Resolve database copy plus the current step
records and manifest, mid-write mid-pass, and the old generation may
still be placed on a timeline the captain keeps - so auto-retire
would either duplicate the whole mark/sweep apparatus inside the
render path or turn a harmless leftover (disk and confusion, never a
wrong render) into a refused caption pass. Retirement stays with
`caption_asset_gc` mark + sweep (quarantine, never delete), which
alone can prove the mate unplaced. The codebase already reads this
way: `subtitle_coverage` positions a card by its PLACED record, never
by the props' own stale `_timeline_start`.

The surface under test is `ambiguous_span_pairs` (ledger-grouped,
timeline-scoped) as reported by `_ambiguous_pairs_for_dir` and
carried on the pass payload: the orphan is identifiable as the
non-drawing mate WITHOUT opening either render.
```
