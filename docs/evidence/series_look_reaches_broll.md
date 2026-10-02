# The look reaches B-roll

Tests: `tests/test_series_look_reaches_broll.py`. Moved from that module's docstring in the 2026-10 suite halving.

```textThe house look reaches B-roll, not only A-roll.

`compile_manifest` merges `fusion_look` - pivot contrast, glow, grain and
a shaped vignette - onto every V1 **and V2** clip, because a CDL has no
term for any of the four and a Fusion comp is their only route to the
picture. The Fusion pass read `tracks['V1']` alone, so on project 001
eleven clips carried a merged look and eight got a comp: the three
cutaways played at a different contrast, with no grain and no vignette,
beside the A-roll they were cut into. Commit 85634d5 added the detection
that names the dropped labels and deliberately left the gap open.

Making the look CONSISTENT is right under any answer to the open
`vep-house-look-grain-never-chosen` decision, so nothing here asserts a
grain, vignette or glow VALUE - only that whatever value is chosen
arrives on both tracks.
```
