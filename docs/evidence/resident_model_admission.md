# The model that loaded beside the one the machine refused

The machine has 24 GiB and has already panicked from memory pressure
once - a 12-17 GB python beside Resolve. The scheduler admits resident
models against LIVE memory, and it refused the 15 GB VLM and the 11 GB
SAM 2 session on this box while it was paging. Then the stem separation
loaded anyway: its profile carried NO demand at all, so nothing about
it was ever checked, and its own code carried an "8 GB RAM ceiling"
that had never been measured.

**What happened.** Two resident models could be resident at once, and
the second one was the one nobody measured. The scheduler's rule is
that the running demands are SUMMED, so a model that declares no
demand is invisible to it: the VLM's 15 GB and the separator's 1.8 GB
were admitted together, which is the shape of the panic this machine
has already had. And the ceiling the code asserted was both unmeasured
and too high: measured 2026-10-21, an 8 GB demand was REFUSED on this
box while 6.0 GB of memory was available - and the separation needs
1.8 GB, so it would have run. A reservation above the measured peak is
a refusal, not a safety margin.

**Measured, before and after, with the denominators**
(`docs/benchmarks/2026-10-21-vi015-model-admission.md`; the bench is
`docs/benchmarks/vi015_model_admission_bench.py`, run unchanged on the
base commit `5a3368ce8` and on the branch):

- the stem separation's peak physical footprint: 7 sampled grants,
  1.552 - 1.833 GB - and its demand is now 2 GB, the measured peak plus
  a margin. Before: `{}`, no memory check at all.
- competing passes, one resident model held and a second asked for
  beside it: 2 / 2 admitted before, 1 / 2 after - the second is
  refused with the reading.
- the fixed multi-model analysis run's output: the stem separation's
  samples are bit-identical before and after (max absolute sample
  difference 0.0), and the two models this box cannot host are refused
  in both trees, for the same reason.
- the peak resident memory of that run: 1.552 GB before, 1.686 GB after
  (8% apart, both under the reservation, so nothing overran it).
- the cost of the bounded recovery: a refusal is reported one 5 s
  backoff later than before (the SAM 2 segmentation: 0.12 s before,
  5.46 s after). Bounded: two attempts, never more.

**The rule.** A RESIDENT model is admitted against a demand computed
from the artifact that is about to load - the weights on disk, plus the
overhead this machine measured - floored by that model's own MEASURED
peak, and the demand is computed BEFORE the load. A model that
declares no demand is invisible to the scheduler's sum, so a resident
model with no demand is admitted beside another resident model; that is
the defect, and it is why the enumeration of resident models is one
list. An asserted ceiling is not a reservation: a reservation above the
measured peak refuses work that would have run. And an unavailable
resource and a failed inference are DIFFERENT facts: a machine that
could not host the model is recorded as `model_unavailable`, and the
model that was resident and broke on the footage as `inference_failed`.

One enumeration, `library/tools/resident_model_admission.py`; the scheduler is
unchanged (`library/tools/resource_scheduler.py`) and the admission is
its existing one. The defect-naming tests are in
`tests/unit/context/test_resident_model_admission.py`, and PR #3027's bounded
wait and progress channel are preserved beside them.
