# The progress record that lost a frame

Test: `tests/unit/context/test_vision_frame_extract.py` -
`test_the_waves_still_report_every_frame_to_the_progress_channel`.

The vision child's progress channel counted FINISHED units, and the
parent read that count to tell a working child from a wedged one. The
record was written atomically - a staged file, then a replace - and
two threads that finished a unit at the same time each wrote it. The
replace made each write WHOLE; it did not make them ORDERED, so the
file could end up holding the OLDER of the two snapshots.

**What it cost.** Measured 2026-10-21: 26 of 200 five-frame
extractions reported FOUR units, and left a frame in flight - the
parent saw a counter that stood still while the child was working,
which is exactly the signal it reads as a wedge. The test failed with
"4 == 5".

**The rule.** The snapshot and the replace happen under ONE lock, so
the record always holds the newest state: the last writer to arrive
reads the counters as they stand and writes them, and no earlier
snapshot can land after it. An atomic write is whole; it is not
ordered, and a record two threads write at once needs both.
