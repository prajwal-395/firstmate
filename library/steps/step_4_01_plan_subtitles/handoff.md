# 4.01 Caption feedback review

The bridge has already made the deterministic subtitle plan. You are asked
only when timeline notes reached this step. Check each note against the
timed spoken words and the caption entries in `caption_feedback_context`.

Return one `caption_feedback` result for every note id:

- `caption_fix` only when the proposed replacement is a contiguous phrase
  in the timed speech and the anchor phrase is present in the current
  caption. Preserve the spoken words; punctuation and capitalization may
  differ. The code checks both phrases before recording the edit.
- `already_correct` only when `requested_phrase` appears in both the timed
  speech and current caption entries. Include that phrase so the code can
  check your reading, including a year or number rendered as digits.
- `upstream_transcript_needed` when `requested_phrase` is absent from the
  timed speech or no shown block can be matched to the note. Do not add
  words to a caption or invent their timing. The source transcript must be
  reindexed before captions are planned again.
- `outside_scope` for a note about a different decision, such as caption
  size or position. Say which owner or Ren operation should handle it.

Use the exact note id. Include `requested_phrase` for `already_correct` and
`upstream_transcript_needed`; quote an anchor and replacement for
`caption_fix`. Do not rely on memory, add a word, alter timing, or report a
fix without grounding it in the timed speech. The post-bridge records
supported caption fixes in the project's edit ledger so every reel rebuild
can read the same correction.

### Timeline Notes

Return a `note_acknowledgements` entry for every timeline note. Use the
same note id, action and rationale as the corresponding
`caption_feedback` result. A note that needs transcript reindexing is
acknowledged with that reason; do not imply that its caption changed.

When `caption_feedback_context.notes` is empty, the model call is skipped.
