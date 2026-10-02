# broll coverage

Test: `tests/test_broll_coverage_reaches_the_tables.py`.

The B-roll rows of two candidate tables said nothing was measurable.

Step 4.03's and step 4.04's pre-bridges build one row per spine block and
both read `block.get("clip_id")` off the SPINE. A `transition_slot` has
none, so both wrote **`not measured (no source clip)`** on every
non-speech block - 5 of 13 rows on project 001, 38% of each table, and
precisely the rows where a cutaway effect or a whoosh would go. The
covering clip is named in `b_roll_assignments`, 4,394 bytes, in the same
prompt.

The two tables want different things from that clip and get different
answers, which is the point:

- **4.03 gets a measurement.** A cutaway has a real camera description,
  so the row now reads `3.0s, stationary, stable, B-roll cutaway
  clip_001` where it read `not measured (no source clip)`.
- **4.04 gets an admitted absence with a reason.** A cutaway is placed
  `video_only`, so its own audio is never heard and there are no
  transients to count: `covered by clip_001, video only - the cutaway's
  own audio is never heard`.
