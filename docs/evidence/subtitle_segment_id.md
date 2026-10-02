# Subtitle segment identity: history

Moved from `tests/test_subtitle_segment_id.py`.

The captain reported the defect (2026-09-04): rendered overlays were named
`sub_block_<block_position>`, an ordinal within one spine, written into a
directory that is per PROJECT and not per timeline. Two failures in one name:
a master and a reel both have a `body_1`, so one overwrote the other; and no
part of the name said whose speech it captioned or which span of source audio
it came from. The captain's instruction: the fix is a binding, not a wider
ordinal (a dedicated "a bigger number would not have fixed it" test was folded
into the provenance-vs-placement test in the 2026-10 suite halving).

The second change (2026-09-10): the name used to carry the TIMELINE as the
discriminator, so three variant timelines captioning the same words rendered
the same pixels three times. The captain's ruling roots the identity in
PROVENANCE - the source footage for subtitles - and keys it by content:
`sub_<speaker>_<clip>_<span>_<digest>`. Two variants captioning the same words
compute the same name (the sharing); anything that draws differently digests
differently and can never overwrite (the collision stays dead).

`stable_prefix`: pins key on the re-renderable half (`overlay_intent`), because
a re-render changes the digest and nothing else. The 2026-09-13 wipe proved it:
every one of the captain's caption pins died on the digest while its prefix was
still live.
