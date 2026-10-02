# Remotion batch renderer: test history

Moved from `tests/unit/captions/test_subtitle_render.py`.

## The node-store binding fixture (2026-09-15)

Six tests in that file failed in every fresh worktree - not because the code
was wrong but because `render_batch` and `PersistentRenderer.start` refuse an
unbound checkout BEFORE reaching the mocked subprocess, so the gate reported an
environment gap as six test failures. The tests that really render skip through
the `remotion` capability and narrow the verdict by name; the unit tests pin the
binding aside with an autouse fixture, and the refusal itself is pinned against
a tmp layout.

## Laziness, and why it is not measured with pgrep (2026-09-14)

`render_one_segment` can return WITHOUT rendering - with reuse on, a
region-scoped pass skips most cards. A bundle paid at construction would be paid
in full to draw one card. `step_4_05_render_subtitles` builds ONE renderer for
the whole pass, so eager construction would cost every scoped re-render a full
bundle.

Laziness is pinned by intercepting the single spawn path -
`PersistentRenderer.start()` is the only caller of `Popen`, and only `render()`
calls `start()` - rather than by counting processes named `render-batch.mjs`.
That counting failed the full-suite gate with `assert running() == before`
reading 1: `pgrep -f` matches ANY process whose argv carries the string, so a
concurrent probe in another lane, an `rg` search, or a real render elsewhere
flips the count. Reproduced: ~5/25 beside three tight `pgrep` loops, 25/25 alone.

## Retired in the 2026-10 suite halving

An AST scan asserting `PersistentRenderer` never launches `npx` (the
no-silent-fallback rule read off the code) and a signature pin on
`PersistentRenderer.render` were removed as source-shape pins; the behaviour is
held by the dead-child refusal and the sequence refusal tests.
