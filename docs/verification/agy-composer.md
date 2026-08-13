# agy composer shape - verification record

Byte-level capture backing agy's entry in `bin/fm-composer-lib.sh`'s shape
catalogue. Captured from agy 1.1.12 in a 120x30 pty, launched exactly as
`bin/fm-spawn.sh` launches it (`agy --dangerously-skip-permissions`), with the
raw escape sequences preserved.

## Shape: `separated`

agy draws a full-pane-width rule pair bracketing a single content row, then a
status footer OUTSIDE the pair:

```
ESC[38;2;65;72;104m  ──── x120 (U+2500)  ESC[m     <- top rule
ESC[38;2;122;162;247m > ESC[m                      <- content row, `>` glyph
ESC[38;2;65;72;104m  ──── x120 (U+2500)  ESC[m     <- bottom rule
Claude Opus 4.6 (Thinking) | ctx: 0.0% | quota: 100% (4h 59m)
```

| Fact | Value |
| --- | --- |
| Family | `separated` (same rule-pair family as pi) |
| Rule glyph / width | U+2500 `─`, full pane width (120 of 120) |
| Rule foreground | truecolor RGB(65,72,104), luma ~74 |
| Prompt glyph | ASCII `>` (U+003E), a SHELL glyph |
| Glyph foreground | truecolor RGB(122,162,247), luma ~160 |
| Glyph/content separator | one ASCII space; cursor rests at column 2 |
| Empty state | glyph alone - agy renders NO idle placeholder or ghost text |
| Pending state | default-foreground, normal-intensity text inline after `> ` |
| Footer | one row BELOW the bottom rule; outside the composer |

## Why the container matters

agy's `>` is in `FM_COMPOSER_SHELL_PROMPT_GLYPHS`, so THE SAFETY RULE forbids
reading it as an empty composer on a bare row - that is what a pane shows once
its agent has exited to a login shell. agy is provable only because the rule
pair contains the glyph. The verdict therefore routes through
`_fm_composer_pi_verdict`'s identity-plus-structure conjunction exactly like
pi, and a bare `>` with no rule pair still reads `unknown`.

agy differs from pi in one respect: pi leaves its content region blank, so it
is read by `_fm_composer_classify_pi_rows`, while agy's row carries the glyph
and goes through the shared `_fm_composer_classify_rows` container classifier,
where a contained shell glyph is legitimately empty.

## Verified verdicts

Against the captured bytes, through `fm_composer_classify_screen` with
`styled=1 identity=1`:

| Screen | Identity | Verdict |
| --- | --- | --- |
| rule / `>` / rule | `agy idle` | `empty` |
| rule / `>` / rule | `agy busy` | `unknown` |
| rule / `> hello firstmate` / rule | `agy idle` | `pending` |
| rule / `> hello firstmate` / rule | `agy busy` | `pending` |
| `>` with no rule pair (dead shell) | `agy idle` | `unknown` |
| rule / `>` / rule | `grok idle` | `unknown` |

`tests/fm-composer-lib.test.sh` (28 assertions) and
`tests/fm-composer-ghost.test.sh` (33) pass unchanged alongside these.

## Capture note

The trust dialog (`Do you trust the contents of this project?`) gates the first
launch in an untrusted directory and must be cleared before the composer
paints. `bin/fm-spawn.sh` already handles this for dispatch by injecting the
worktree path into agy's `trustedWorkspaces` before launch.
