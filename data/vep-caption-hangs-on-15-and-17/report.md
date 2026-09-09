# Reels 15 and 17 caption hangs: measured, and why the fix is editorial

Measured 2026-09-09. The gate refused both reels (F15); the rebuild left
their timelines untouched. This report gives the captain the numbers
behind "needs an editorial re-cut": the placed card, the word it belongs
to, the gap between them, and the arithmetic showing no regrouping
removes the hang. No threshold was changed and none is proposed.

Verdict up front: both cards derive their end from the WORD, not from a
block boundary or a neighbour's start. The words are timed longer than
they sound - trailing silence inside the timed span, under every bound
the pipeline owns - so every card containing them fails the hang check
and no planner regrouping fixes it. The re-cut must remove or replace
the passage, not retime the card. Detail per reel below.

All seconds below use 24000/1001 fps. Reel seconds are master seconds
minus the reel's keep-range start (R15: 2009.53, R17: 2288.17).

## Reel 15 - why-youtube-outranks-other-video

Placed item (still live on the old timeline, V3):
`sub_reel-15-why-youtube-outranks-oth_akshita_concise_4794fbac.mov`,
frames 1058-1107 (49f) = reel 44.127-46.171s, duration 2.044s.

The word it belongs to: `concise,`, master 2053.648-2055.676 (2.028s)
= reel 44.118-46.146s.

The gap: the card starts 0.009s after the word starts and ends 0.025s
(about half a frame) after the word ends. The card IS the word's span.

The gate's finding - `caption card 'concise,' hangs far past its speech
(2.03s for 1 words, limit 1.0s)` - measures the word-clipped card
(2.028s), not the raw item (2.044s); the two differ by under a frame.

Why the word is long: the timed end is the VAD segment end (segment
2051.5-2055.68). At the local neighbour rate (0.2-0.5s/word) the
utterance occupies roughly the first 0.4s and about 1.6s is trailing
silence. This is the same master utterance the reel-24 investigation
measured (2053.648-2055.676, span 2.028s) - same words, same seconds.

The rebuild's new-grouping card for the same passage:
`very strong, concise,`, reel 42.671-46.147, 3.476s for 3 words
against the 3.0s limit - over by 0.476s, refused for the same word.

## Reel 17 - four-slots-and-nothing-else

Placed item (still live on the old timeline, V3):
`sub_reel-17-four-slots-and-nothing-e_craig_four-to-five-recommendations_92f65e59.mov`,
frames 333-410 (77f) = reel 13.889-17.100s, duration 3.212s. It covers
master 2302.059-2305.270: `four` (head clipped by 0.005s), `to`,
`five`, `recommendations.`.

The words the finding is about: `five` master 2303.158-2303.459
(0.301s) = reel 14.989-15.290s, and `recommendations.` master
2303.679-2305.245 (1.566s) = reel 15.510-17.076s. The head word `to`
runs master 2302.154-2303.138 (0.984s) = reel 13.985-14.969s.

The gap: the item ends 0.024s (about half a frame) after
`recommendations.` ends. The card IS the words' span.

The gate's finding - `caption card 'five recommendations.' hangs far
past its speech (2.09s for 2 words, limit 2.0s)` - measures the span
2303.158-2305.245 (2.087s).

Why the words are long: `recommendations` is five syllables, roughly
0.6s spoken at neighbour rates, so about 0.95s of its 1.566s timed
span is trailing silence to the segment end. `to` (0.984s) bridges the
~1.0s pause after `four` (ends 2302.134) before `five` starts
(2303.158). Both spans sit under every bound below. The utterance /
silence split is inferred from neighbour rates, not listened to.

The rebuild's new-grouping card for the same passage:
`to five recommendations.`, reel 13.985-17.076, 3.091s for 3 words
against the 3.0s limit - over by 0.091s.

## Why no partition works (the arithmetic, not the conclusion)

F15 allows a card of N words N x 1.0s. Every grouping below was run
through the live `check_caption_hangs`, not hand-computed.

Reel 15 - the feasible set is exhausted (the 2.99s gap back to
`there's` exceeds the 1.0s max_gap, so no longer grouping exists):

| card | span | words | limit | over by |
|---|---|---|---|---|
| `concise,` | 2.028s | 1 | 1.0s | 1.028s, refused |
| `strong, concise,` | 2.590s | 2 | 2.0s | 0.590s, refused |
| `very strong, concise,` | 3.476s | 3 | 3.0s | 0.476s, refused |

Reel 17:

| card | span | words | limit | result |
|---|---|---|---|---|
| `recommendations.` | 1.566s | 1 | 1.0s | over 0.566s, refused |
| `five recommendations.` | 2.087s | 2 | 2.0s | over 0.087s, refused |
| `to five recommendations.` | 3.091s | 3 | 3.0s | over 0.091s, refused |
| `you four to five recommendations.` | 3.452s | 4 | 4.0s | under by 0.548s, PASSES the check |

The fourth row needs saying plainly, because it looks like a fix and
is not one. It passes the word-count heuristic, and it fits the
caption box, but the grouper rejected it: keeping `you four` on the
first card makes the shortest card 1.264s instead of 0.843s, and the
partitioner prefers the longest shortest card. More importantly, the
regrouped card still shows `recommendations.` for its full 1.566s
timed span - the ~0.95s of trailing silence stays on screen either
way. That regrouping silences the gate, not the symptom the captain
was asked about. Teaching the partitioner the gate's heuristic would
split genuine speech to satisfy a number - gaming the gate from the
inside - so it is refused as a fix, not just unpursued. (A five-word
grouping fails the caption-box width check, which is why the grouper
never made one.)

## Reel 24, and whether this is three reels in one family

Note on the cited path: `data/vep-001-run-after-the-five/report.md`
does not exist in this repository. The reel-24 sources used here are
the pinning tests (`tests/test_reel24_card_end_derivation.py`,
`tests/test_stretched_word_caption.py`), which record the live
measurement: word `concise,` master 2053.648-2055.676 (2.028s), card
end == word end == block end, F15 refuses the 3-word card.

Reel 15 is the SAME shape down to the same utterance: its word is
master 2053.648-2055.676, identical seconds, because its keep range
covers the same passage. Reel 17 is the same mechanism on different
words (a sub-bound stretched tail on the card's final word, plus a
gap-bridging head word). So yes: three reels, one family, named as
`sub-bound trailing-silence stretch on the card's final word`.

On the calibration question: the gate's limit is not miscalibrated.
It is set against card durations in finished work (92 cards, highest
genuine per-word rate 0.960s) and the derivation bound against speech
itself (8,492 bound words, genuine ceiling 2.65s). Recalibrating the
limit to cover 2.65s words would legalise every real hang - the bound
would stop being a hang check. The limit measures the right thing
(a card on screen past audible speech); these three are the
irreducible remainder, speech whose timed span exceeds its audible
span by an amount no code bound can isolate without failing genuine
slow speech. That is the pattern, and it resolves editorially, not
by tuning.

## What a re-cut has to change

The hang sits INSIDE the word timing (word end = segment end), so no
keep-range trim that keeps the word shortens the card.Trimming the V1
clip inside the stretched span only cuts the picture from under the
caption. The acting choices, per reel:

Reel 15 - the fragment `Make sure there's a very strong, concise,`
(master ~2052.0-2055.7) dangles; the thought completes at master
2058.666-2061.96 (`but that there's a very concise description of what
exactly your video is about`, tightly timed - `concise` there spans
0.462s). Either (a) cut the dangling fragment from the reel (end the
passage after `geo-optimized`, before `Make sure`), letting the
finished sentence carry the idea; or (b) replace the fragment's span
with the completed take at 2058.666-2061.96; or (c) record an
acceptance of the 3.48s card with its ~1.6s of trailing silence. The
CTA range (master 814.69-819.13) is unaffected by any of these.

Reel 17 - the sentence `It's only giving you four to five
recommendations.` (master 2300.87-2305.245). Either (a) accept the
rebuild's 3.09s card - 0.091s over a heuristic bound, symptom ~0.9s
of trailing silence under the final word; or (b) end the passage
after `five` (master 2303.459), which drops the noun and should not
be taken without watching the cut. There is no retiming option: the
timings are measured, not chosen.

## Method and provenance

Transcript words: the project's
`pipeline_output/scratch/timeline_transcript/transcript.json`.
Moments and keep ranges: `pipeline_output/review/` proposals
(`reel_proposals_v2_20260908T171409Z.json` carries the nineteen
timelines' names and spans). Placed items: read off read-only SQLite
copies of Project.db made before the rebuild
(`/private/tmp/vep-rebuild-nineteen/Project_rebuild_before.db`,
`/private/tmp/vep-current-state/Project_copy.db` - item counts and
max durations match the lane's `placed_measure.json` exactly, so the
copies agree with what the gate graded). Gate strings: the 2026-09-08
`verify_results.json` lane output plus the live `check_caption_hangs`
on current main. The live Resolve app was not touched (another lane
is working in it); the captain's timelines are byte-identical to
before. No repository code was changed for this report.
