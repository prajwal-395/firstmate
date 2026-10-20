"""A retrieval score is not a probability, until somebody measures it.

The shipped floors (`library/tools/analysis/footage_query.py`) are RANK
thresholds: a segment above 0.40 dense cosine is kept.  That number was
calibrated against hand-checked ABSENT probes at build time, which makes
it a good separator and nothing more.  It is not the chance that a
returned window holds what the editor asked for, and reading it as one
is how a search becomes confidently wrong.

This module turns the score into something readable, from the only
evidence that can: the retrieval harness's own labelled outcomes.  Every
returned window is either a HIT (it overlaps a ground-truth interval) or
it is not, and that is exactly the population a calibration curve wants.

**What it produces.**

* `reliability_curve` - the score bins, the observed hit rate in each
  bin, and the EXPECTED CALIBRATION ERROR.  A score of 0.7 that comes
  back right 70% of the time is calibrated; a score of 0.7 that comes
  back right 20% of the time is not, and no threshold can repair that.
* `abstention_threshold` - the cutoff that maximises BALANCED ACCURACY:
  the rate at which a right answer is kept and the rate at which a wrong
  one is refused.  Both matter, and a cutoff that only maximises recall
  is how a search answers everything.
* `calibrate` - both, plus an honest statement of whether the score can
  be read as a probability at all.

**What it refuses to do.**  It does not produce ONE threshold for the
whole engine.  The text lane's dense cosine and the frame lane's CLIP
cosine are different spaces with different score distributions, so
calibration is per lane; pooling them would produce a number that
describes neither.

**The fused score is not calibratable, and this says so.**  The text
lane ranks by an RRF fusion of several signals, which is not on a
probability scale at all.  The samples therefore carry the RAW signal
the floor judged on (`dense_score` for the text lane, the frame cosine
for the frame lane), and a sample that carries no such signal is
reported as uncalibratable rather than silently dropped.

**Related but different.**  `library/tools/blind_label_pilot.py` also
sweeps a cutoff, over BLIND EDITORIAL LABELS - a human or agent saying
"this window does or does not show the thing".  That answers "should we
trust the search on this footage"; this answers "when a returned window
overlaps a ground-truth interval, how often is it right".  Different
population, different question, same arithmetic.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import pairwise

DEFAULT_BINS = 10
"""How many equal-width score bins the reliability curve uses."""

#: A score outside this range is not on a probability scale, so it is
#: not binned and the sample is reported as uncalibratable.
PROBABILITY_RANGE = (0.0, 1.0)

CALIBRATABLE_SIGNALS = ("dense", "frame_cosine")
"""The signals whose number CAN be read as a probability.

A sample must name the signal it carries.  The text lane's fused rank
is a Reciprocal Rank Fusion of several signals - a number near 0.016
that looks like a probability and is not one - and no range check can
tell the difference, because it is in range.  Only the NAME tells, so
only the name is honoured.

* `dense` - the text lane's raw dense cosine, the signal its floor
  judges on.
* `frame_cosine` - the frame lane's CLIP cosine.

A signal outside this list is uncalibratable, and the sample is
reported as such rather than dropped.
"""

UNCALIBRATABLE = "uncalibratable"
"""The reason a sample cannot be calibrated: it carries no raw signal
the floor judged on."""


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lane_of(sample: Mapping) -> str:
    return str(sample.get("lane") or "unknown")


def _usable(samples: Iterable[Mapping]) -> tuple[list[tuple[float, bool]], list[dict]]:
    """The samples that carry a probability-like score, and the rest."""
    usable: list[tuple[float, bool]] = []
    rejected: list[dict] = []
    for sample in samples or ():
        score = _num(sample.get("score"))
        hit = bool(sample.get("hit"))
        signal = sample.get("signal")
        if score is None:
            rejected.append({"sample": dict(sample), "reason": UNCALIBRATABLE})
            continue
        if signal not in CALIBRATABLE_SIGNALS:
            rejected.append({
                "sample": dict(sample),
                "reason": (f"the {signal!r} signal is not on a probability "
                           "scale")})
            continue
        if not (PROBABILITY_RANGE[0] <= score <= PROBABILITY_RANGE[1]):
            rejected.append({"sample": dict(sample),
                             "reason": "the score is not on a probability scale"})
            continue
        usable.append((score, hit))
    return usable, rejected


def reliability_curve(samples: Iterable[Mapping], *,
                      bins: int = DEFAULT_BINS) -> dict:
    """The observed hit rate in each score bin, and the calibration error.

    The bins are equal-width over the probability range, because the
    question is "when the engine says 0.7, how often is it right?" and
    that question is asked of the SCALE, not of the population.
    """
    usable, rejected = _usable(samples)
    low, high = PROBABILITY_RANGE
    width = (high - low) / max(1, bins)
    buckets: list[dict] = [{
        "low": round(low + index * width, 4),
        "high": round(low + (index + 1) * width, 4),
        "count": 0, "hits": 0, "score_sum": 0.0,
    } for index in range(bins)]
    for score, hit in usable:
        index = min(bins - 1, max(0, int((score - low) / width)))
        bucket = buckets[index]
        bucket["count"] += 1
        bucket["hits"] += 1 if hit else 0
        bucket["score_sum"] += score
    total = len(usable)
    error = 0.0
    rows = []
    for bucket in buckets:
        count = bucket["count"]
        if not count:
            rows.append({**{k: bucket[k] for k in ("low", "high")},
                         "count": 0, "mean_score": None, "observed": None})
            continue
        mean_score = bucket["score_sum"] / count
        observed = bucket["hits"] / count
        error += (count / total) * abs(observed - mean_score)
        rows.append({**{k: bucket[k] for k in ("low", "high")},
                     "count": count,
                     "mean_score": round(mean_score, 4),
                     "observed": round(observed, 4)})
    positives = sum(1 for _, hit in usable if hit)
    return {
        "bins": rows,
        "samples": total,
        "positives": positives,
        "negatives": total - positives,
        "expected_calibration_error": round(error, 4) if total else None,
        "uncalibratable": rejected,
        "status": ("measured" if total else
                   "unmeasured: no sample carries a probability-like score"),
        "note": ("the score is the RAW signal the floor judged on, not the "
                 "fused rank; a fused score is not on a probability scale "
                 "and cannot be calibrated"),
    }


def abstention_threshold(samples: Iterable[Mapping]) -> dict:
    """The cutoff that keeps the right answers and refuses the wrong ones.

    BALANCED ACCURACY - the mean of the rate at which a hit survives the
    cutoff and the rate at which a miss is refused - because the two
    errors cost differently and a cutoff that only maximises recall
    answers every query.  A set with no hits or no misses has no
    threshold to find, and says so.
    """
    usable, rejected = _usable(samples)
    positives = [score for score, hit in usable if hit]
    negatives = [score for score, hit in usable if not hit]
    if not positives or not negatives:
        return {
            "status": "unmeasured: the labels carry one class only, so no "
                      "cutoff can trade one error against the other",
            "cutoff": None,
            "samples": len(usable),
            "positives": len(positives),
            "negatives": len(negatives),
            "method": "balanced accuracy over labelled windows",
            "uncalibratable": rejected,
        }
    values = sorted({score for score, _ in usable})
    candidates = [values[0] - 1e-12]
    candidates.extend((a + b) / 2 for a, b in pairwise(values))
    candidates.append(values[-1] + 1e-12)
    best = None
    for cutoff in candidates:
        kept = sum(1 for score in positives if score >= cutoff)
        refused = sum(1 for score in negatives if score < cutoff)
        sensitivity = kept / len(positives)
        specificity = refused / len(negatives)
        candidate = {
            "cutoff": round(cutoff, 6),
            "balanced_accuracy": round((sensitivity + specificity) / 2, 4),
            "present_retained_rate": round(sensitivity, 4),
            "absent_abstained_rate": round(specificity, 4),
            "not_found_precision": (round(refused / (refused + (len(positives) - kept)), 4)
                                    if refused + (len(positives) - kept) else None),
            "not_found_recall": round(specificity, 4),
            "not_found_true_positives": refused,
            "not_found_false_positives": len(positives) - kept,
        }
        if best is None or candidate["balanced_accuracy"] > best["balanced_accuracy"]:
            best = candidate
    best["status"] = "measured candidate; requires review before policy change"
    best["samples"] = len(usable)
    best["positives"] = len(positives)
    best["negatives"] = len(negatives)
    best["method"] = "balanced accuracy over labelled windows"
    best["uncalibratable"] = rejected
    return best


def calibrate(samples: Iterable[Mapping], *, bins: int = DEFAULT_BINS) -> dict:
    """The curve, the error and the threshold, in one report.

    The verdict is about READABILITY, not quality: a score can be
    perfectly calibrated and still be a bad separator, and a bad
    separator with a calibrated score is still a bad separator.  The
    threshold is what says whether the score separates.
    """
    curve = reliability_curve(samples, bins=bins)
    threshold = abstention_threshold(samples)
    error = curve["expected_calibration_error"]
    if error is None:
        verdict = "unmeasured"
    elif error <= 0.1:
        verdict = "calibrated: the score reads as a probability"
    elif error <= 0.25:
        verdict = "poorly calibrated: the score overstates its confidence"
    else:
        verdict = "miscalibrated: the score does not read as a probability"
    return {
        "reliability": curve,
        "abstention": threshold,
        "expected_calibration_error": error,
        "verdict": verdict,
        "note": ("calibration says how well the score reads; the threshold "
                 "says whether it separates.  Both are needed and neither "
                 "is the other"),
    }


def by_lane(samples: Iterable[Mapping], *, bins: int = DEFAULT_BINS) -> dict:
    """Calibrate each lane on its own.

    The text lane's dense cosine and the frame lane's CLIP cosine are
    different spaces.  Pooling them would produce a number that
    describes neither.
    """
    lanes: dict[str, list] = {}
    for sample in samples or ():
        lanes.setdefault(_lane_of(sample), []).append(sample)
    return {lane: calibrate(rows, bins=bins) for lane, rows in lanes.items()}
