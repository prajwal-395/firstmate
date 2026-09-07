# Instrument validation (before any new number was reported)

## 1. DB timeline hasher (mine, offline)
First version returned md5 of EMPTY for all 29 timelines (join went through
Sm2SequenceContainer_Sm2TiTrack with the wrong owner direction). Caught because
every hash was d41d8cd98f00b204e9800998ecf8427e - a hash of nothing.
Fixed route: Sm2Timeline -> Sm2Sequence.Sm2Timeline_id -> Sm2SequenceContainer
-> Sm2TiTrack.Sm2SequenceContainer_id -> Sm2TiItem_Sm2TiTrack (DbOwner=TRACK)
-> Sm2TiItem.

Cross-checked against numbers a previous lane published in
pipeline_output/review/conformance_report.json:
  Reel 01: verifier plan/actual "4/3" video, "32/28" captions
           my DB read: V1=1 + V2=2 = 3 video, Captions track = 28   MATCH
  Reel 05: verifier "18/15" video, "53/54" captions
           my DB read: 9 + 6 = 15 video, Captions track = 54        MATCH
  timelines counted: 29 (verifier's read_only_proof also says 29)   MATCH

## 2. reel_quality_bar exact half
Run over the same plan FIRST_MEASUREMENT names (reel_proposals_v2.json,
25 moments, all approved).

  duration within_guidance   published 16   measured 16   MATCH
  duration outside           published  9   measured  9   MATCH
  delivered range seconds    published 23.3-108.1  measured 23.3-108.1  MATCH
  cta declared               published 18   measured 18   MATCH
  cta in_body                published  5   measured  5   MATCH
  cta absent                 published  2   measured  2   MATCH
  most_reused_closer_closes  published  8   measured  7   DISAGREE

The disagreement is the BAR's, and it is a real off-by-one worth reporting:
span (814.69, 819.13) is the ENDING of 8 reels in this batch - 7 declare it as
their closer, and reel 7 lands on the same seconds through its own body
(`source: in_body`). `cta_reading` sets closes_reels = len(declared_closers[key]),
so it counts DECLARATIONS and reports 7.

reel_quality_bar's own docstring says the field exists because "Nothing said
'this 4.4-second sentence is the ending of seven of your nineteen reels', and a
reader who cannot see that cannot rule on it". The stated purpose is ENDINGS.
The implementation counts declarations. On this batch it under-reports by one.

NOT CHANGED. Reported only.
