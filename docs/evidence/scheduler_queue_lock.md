# The queue that locked the waiters out

Test: `tests/unit/context/test_concurrency.py` -
`test_live_waiter_is_not_reaped_for_stale_queue_heartbeat` and
`test_a_queued_large_job_is_not_starved_by_later_small_ones`.

The scheduler's queue was a LOCK that the queue's own readers held.
`resource_scheduler` reaped dead rows inside the caller's write
transaction, and the reap spends its time outside the database: it
shells out to `ps` to read whether each pid is still the process the
row recorded. So every queue poll took the database's write lock and
held it across a process spawn per row.

**What it cost.** On a loaded machine `ps` costs a quarter of a
second, so a poll of a three-row queue held the write lock for about a
second - and the waiters it was polling FOR could not write at all.
Measured 2026-10-21 on the pool-271 lane, with the machine at a load
average of 40+:

- `sqlite3.OperationalError: database is locked` raised inside
  `BEGIN IMMEDIATE` - the waiter's own admission, refused because a
  READER was holding the lock. The waiter's thread died, and the
  test that was watching the queue read it as "the placement never ran"
  and "the second build writer did not enter the queue";
- the same test, failing the other way: the reaper correctly removed a
  row whose recorded owner had died (a stand-in process that outlived
  its own usefulness), and the test's release of that row then raised
  "release of a job that is not running";
- the live-waiter test, whose waiter gave up at its own 15 s deadline
  while the test was still working, and whose missing row read as
  "a LIVE waiter got reaped".

The scheduler's own test file - 69 tests - took 290 s on that machine
and 32 s after the repair.

**Two more defects the same work found.** `ps` refuses a pid above the
kernel's limit ("process id too large") and takes the WHOLE call down
with it, so one row that recorded a pid from another machine's
numbering silenced every reading - and a holder whose start could not
be read was read as never recycled. And a heartbeat that spent the
busy timeout (30 s) waiting for the write lock, then waited its
interval (15 s) for the next tick, was 45 s late: two such writes are
past the lease (60 s), so a LIVE holder's grant was freed while its
holder was still working (measured 2026-10-21: a holder's release
refused with "release of a job that is not running").

**The rule.** A measurement that costs a PROCESS is taken OUTSIDE the
database's write lock. The reap reads its candidates, takes the
liveness readings (ONE `ps` for every pid at once, not one per row),
and only then deletes the rows it condemned, in its own short
transaction - and the delete is CONDITIONAL on the row still being the
row that was judged, so a row whose owner refreshed its heartbeat in
the meantime is a live row, and it stays. A reader that only LOOKS
never takes the write lock at all. A pid the kernel cannot hand out is
left out of the call, and a call `ps` refuses is retried one pid at a
time. The heartbeat does not spend the lease waiting for the
database: it gives up on the lock quickly and retries on its next
tick. The reaping rules themselves do not change: a live waiter is
never reaped, a row whose owner is provably dead is, and a holder
killed with `kill -9` frees the machine at once.
