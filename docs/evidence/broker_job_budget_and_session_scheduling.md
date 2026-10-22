# The broker's session scheduling: a bounded job, and a project open that does not wait

Measured 2026-10-21/22, the PERF-004 lane
(`docs/PERF004_BROKER_SESSION_SCHEDULING.md` is the lane's full report:
the method, the denominators and the after-measurement recipe).

Three incidents, and the rules they bought.

## The job with no bound

A broker job had **no upper bound on its run**. The instance lease's own
timeout bounds the WAIT for the machine, never the HOLD of it, so a
Resolve call that blocks holds the one Resolve and the whole queue for
as long as it blocks.

The live shape of it: job `a6138299f6964c8b` - a
`resolve.ui_transaction`, "Completing a verified Resolve task" - whose
submitter (pid 71608) was already dead, ran **1,412 s** in a broker
worker at **0% CPU** and held the whole queue (**six jobs**) until a
lane killed worker pid 55751 by hand. The machine's own 24 h KPI read
`resolve hold p99 900.58s, max 4297.34s`.

**A job now carries a DECLARED BUDGET**
(`library/tools/resolved/job_budget.py`, the one enumeration): the kind's
budget, or the caller's own (`params["budget_seconds"]`), visible in the
job's shape before the job is queued. The daemon enforces it the only
way a wedged Resolve call can be enforced from outside the process it
wedges: the worker is KILLED at the budget, which releases the instance
flock and gives the one Resolve back, and the job's receipt is a TYPED
REFUSAL - `refusal`, `blocker: resource_contention`, `next_action:
wait_for_the_resource`, the budget and the elapsed time. The worker
enforces the same budget from inside: the per-call deadline is capped by
what is left of it, and the job stops at its next safe checkpoint once
it is gone.

A job that stopped at a committed checkpoint KEEPS its result: the write
may have landed, and a refusal that dropped the receipt would lose the
only record that it did.

`tests/unit/resolve/test_job_budget.py` and the Rust daemon's own test
(`a_job_that_overruns_its_budget_is_refused_with_a_typed_receipt`, a
worker that never answers) name the defect.

## The project open that waited inside the turn

Ten `project scratch open` jobs on 2026-10-21 each held the machine's
turn for the whole **900 s** lease timeout - the queue waited behind
them for a wait, not for an open - and the blocked seconds they caused
were **208,081 s, 30% of every blocked second on the machine that day**.

The wait was in the WRONG PLACE. The design already said it: the wait
for the scratch-origin slot belongs to the CALLER, because the holder of
the slot is inside Resolve and needs that very turn to restore the
origin and release it. But the AXI dispatcher waited in BOTH places - in
the caller (correct) and inside the broker's turn (the defect) - so the
job that ran inside the turn waited for the slot with the turn already
held, and every other lane waited behind it.

**Inside the turn there is no wait now**: the command refuses FAST, with
its own exit code, and the caller retries on it. And the caller does not
leave a job to start against a held slot either: while a
scratch-lifecycle job is still QUEUED, the caller watches the slot, and
a slot another live writer has taken CANCELS the queued job - the
broker drops a queued job without running it - and the caller goes back
to waiting outside the turn.

`tests/unit/resolve/test_broker_turn_scheduling.py` names the defect.

## The second broker that served the one machine

The PROD-001 clean-install check started a **second broker** - from
`/tmp/prod001-clean`, on `/tmp/ren-resolved.sock` - and left it serving
for **seven hours**, with two workers. Two brokers against the one
Resolve move the cursor under each other, and they blocked Resolve
project switches for every other lane.

Two rules, and neither of them is "be careful":

- **ONE BROKER SERVES THE MACHINE.** A broker on the socket a plain
  client reaches CLAIMS the machine - a machine-wide lock, at a FIXED
  path, because a lock inside the temp dir a process may be pointed at
  is no lock at all (a sandboxed install, a check and the machine's own
  broker each held their own) - and a second one refuses to serve while
  the first holds it. A PRIVATE broker, on a socket of its own, is left
  alone: the qualification harness's broker and a lane's own are both
  allowed beside the machine's.
- **A CHECK THAT STARTS A BROKER STOPS IT ON EXIT**, by that broker's
  own pid, on its own socket, and never the machine's broker. The
  sandbox's broker is private - its socket is inside the sandbox - and
  it drives the MACHINE's Resolve lease, so a check never takes the
  machine's broker away and never puts a second writer on the one
  Resolve.

`tests/unit/resolve/test_broker_lifecycle.py` names both defects.
