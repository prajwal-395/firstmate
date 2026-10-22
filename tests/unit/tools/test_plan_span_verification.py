"""VI-014: a reel plan's source-span claims are checked against the sources.

The defects these name:

* A reel moment names the raw footage it plays as
  ``{source_file, source_start, source_end}``.  Those spans were derived
  by REBASING the master timeline's transcript rows onto the source
  files, and nothing ever checked the rebase against the source file's
  OWN transcript.  Before, a plan could name source seconds that carry
  different speech and the reel would play them anyway.
* The rebase is an INFERENCE, so a span it cannot decide is an
  UNMEASURED claim.  Before, an undecided claim and a contradicted one
  looked the same to a reader.
* The check costs a read of the source's own transcript.  Before, a
  caller had no budget to bound it, so a sweep over a whole plan ran
  until it ran out of time.

Each test names one defect; the "before" column is what the reader did
before the check existed.
"""

import json

import pytest

from library.tools import on_demand_verification as od
from library.tools import plan_span_verification as psv


DIGEST = "c" * 64
OTHER_DIGEST = "d" * 64


def _source_words(root, digest, words):
    path = root / digest
    path.mkdir(parents=True, exist_ok=True)
    (path / "transcript.words.json").write_text(json.dumps({
        "content_digest": digest,
        "utterances": [{"words": [{"start": s, "end": e, "word": w}]}
                       for s, e, w in words],
    }), encoding="utf-8")


def _project(root):
    """A project whose runner recorded a fingerprint for one source."""
    project = root / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "pipeline_data.json").write_text(json.dumps({
        "source_fingerprints": {
            "clip_001": {"path": "/footage/clip.mov",
                         "content_digest": DIGEST},
        },
    }), encoding="utf-8")
    return project


class _Moment:
    def __init__(self, number, spans):
        self.number = number
        self.source_spans = tuple(spans)


def _span(start, end, source_file="/footage/clip.mov"):
    return {"source_file": source_file, "source_start": start,
            "source_end": end}


def _transcript(rows):
    """A master transcript whose rows rebased onto the source file."""
    segments = []
    for tl_start, tl_end, src_start, src_end, words in rows:
        segments.append({
            "source_file": "/footage/clip.mov",
            "timeline_start": tl_start, "timeline_end": tl_end,
            "source_start": src_start, "source_end": src_end,
            "resolve_item_id": "item-1",
            "words": [{"word": w, "start": t, "end": t + 0.3}
                      for t, w in words],
        })
    return {"segments": segments}


def test_a_span_the_source_measures_is_supported(tmp_path):
    """Before: the plan's claim about a span was never checked against
    the source file's own transcript, so it was taken on faith."""
    memory = tmp_path / "memory"
    _source_words(memory, DIGEST, [
        (10.1, 10.4, "the"), (10.5, 10.9, "second"), (11.0, 11.4, "opening")])
    project = _project(tmp_path)
    moments = [_Moment(1, [_span(10.0, 12.0)])]
    transcript = _transcript([(10.0, 12.0, 10.0, 12.0,
                               [(10.1, "the"), (10.5, "second"),
                                (11.0, "opening")])])
    report = psv.verify_plan_spans(moments, transcript, str(project),
                                    memory_root=str(memory))
    assert report.spans_checked == 1
    assert report.spans_unverified == 0
    assert report.by_verdict(od.Verdict.SUPPORTED)


def test_a_span_that_carries_other_speech_is_contradicted(tmp_path):
    """Before: a plan could name source seconds carrying different
    speech, and the reel played them anyway."""
    memory = tmp_path / "memory"
    _source_words(memory, DIGEST, [
        (10.1, 10.4, "completely"), (10.5, 10.9, "different"),
        (11.0, 11.4, "words"),
        (90.1, 90.4, "the"), (90.5, 90.9, "second"),
        (91.0, 91.4, "opening")])
    project = _project(tmp_path)
    moments = [_Moment(1, [_span(10.0, 12.0)])]
    transcript = _transcript([(10.0, 12.0, 10.0, 12.0,
                               [(10.1, "the"), (10.5, "second"),
                                (11.0, "opening")])])
    report = psv.verify_plan_spans(moments, transcript, str(project),
                                    memory_root=str(memory))
    assert report.spans_checked == 1
    contradicted = report.by_verdict(od.Verdict.CONTRADICTED)
    assert len(contradicted) == 1
    assert contradicted[0].reel_number == 1


def test_a_span_the_source_cannot_decide_is_unverified_not_wrong(tmp_path):
    """Before: an unmeasured claim was indistinguishable from a false
    one, so an incomplete measurement read as a refutation."""
    memory = tmp_path / "memory"
    _source_words(memory, DIGEST, [(10.1, 10.4, "completely")])
    project = _project(tmp_path)
    moments = [_Moment(1, [_span(10.0, 12.0)])]
    transcript = _transcript([(10.0, 12.0, 10.0, 12.0,
                               [(10.1, "the"), (10.5, "second"),
                                (11.0, "opening")])])
    report = psv.verify_plan_spans(moments, transcript, str(project),
                                    memory_root=str(memory))
    assert report.spans_checked == 0
    assert report.spans_unverified == 1
    assert not report.by_verdict(od.Verdict.CONTRADICTED)


def test_a_span_with_no_words_behind_it_is_unverified(tmp_path):
    """Before: a span the master transcript places no word in was
    indistinguishable from a span that was checked and found empty."""
    memory = tmp_path / "memory"
    _source_words(memory, DIGEST, [(10.1, 10.4, "the")])
    project = _project(tmp_path)
    moments = [_Moment(1, [_span(10.0, 12.0)])]
    transcript = _transcript([])
    report = psv.verify_plan_spans(moments, transcript, str(project),
                                    memory_root=str(memory))
    assert report.spans_unverified == 1
    assert report.spans_checked == 0


def test_a_source_the_runner_never_fingerprinted_is_unverified(tmp_path):
    """Before: a plan could name a file the runner never fingerprinted,
    and nothing said that such a file had no identity to check against."""
    memory = tmp_path / "memory"
    _source_words(memory, OTHER_DIGEST, [(10.1, 10.4, "the")])
    project = _project(tmp_path)
    moments = [_Moment(1, [_span(10.0, 12.0,
                                 source_file="/footage/never-seen.mov")])]
    transcript = _transcript([(10.0, 12.0, 10.0, 12.0,
                               [(10.1, "the")])])
    report = psv.verify_plan_spans(moments, transcript, str(project),
                                    memory_root=str(memory))
    assert report.spans_unverified == 1
    assert "content digest" in report.findings[0].detail


def test_the_budget_bounds_the_sweep_and_the_rest_is_unverified(tmp_path):
    """Before: a caller had no budget to bound a sweep, so a plan ran
    until it ran out of time and the spans it never reached were
    indistinguishable from the ones it did."""
    memory = tmp_path / "memory"
    _source_words(memory, DIGEST, [(10.1, 10.4, "the")])
    project = _project(tmp_path)
    moments = [_Moment(1, [_span(10.0, 12.0)])]
    transcript = _transcript([(10.0, 12.0, 10.0, 12.0,
                               [(10.1, "the")])])
    report = psv.verify_plan_spans(moments, transcript, str(project),
                                    budget_seconds=0.0,
                                    memory_root=str(memory))
    assert report.spans_checked == 0
    assert report.spans_unverified == 1
    assert "budget" in report.findings[0].detail


def test_the_report_names_the_denominators_a_reader_needs(tmp_path):
    """Before: a sweep reported a count of findings with no denominator,
    so a reader could not tell a clean plan from a plan nobody
    checked."""
    memory = tmp_path / "memory"
    _source_words(memory, DIGEST, [(10.1, 10.4, "the")])
    project = _project(tmp_path)
    moments = [_Moment(1, [_span(10.0, 12.0)])]
    transcript = _transcript([(10.0, 12.0, 10.0, 12.0,
                               [(10.1, "the")])])
    report = psv.verify_plan_spans(moments, transcript, str(project),
                                    memory_root=str(memory))
    summary = report.to_dict()["summary"]
    assert summary["spans_total"] == 1
    assert summary["spans_checked"] + summary["spans_unverified"] == 1
    assert summary["seconds"] >= 0.0


def test_the_report_is_written_where_a_reader_can_find_it(tmp_path):
    """Before: nothing recorded what the verification found, so the
    answer existed only in the run that produced it."""
    from library.tools.project_layout import Area, ProjectLayout
    memory = tmp_path / "memory"
    _source_words(memory, DIGEST, [(10.1, 10.4, "the")])
    project = _project(tmp_path)
    moments = [_Moment(1, [_span(10.0, 12.0)])]
    transcript = _transcript([(10.0, 12.0, 10.0, 12.0,
                               [(10.1, "the")])])
    report = psv.verify_plan_spans(moments, transcript, str(project),
                                    memory_root=str(memory))
    path = psv.write_report(str(project), report)
    layout = ProjectLayout(str(project))
    assert path == str(layout.read_path(Area.REVIEW, psv.FINDINGS_FILENAME))
    read_back = psv.read_report(str(project))
    assert read_back["summary"]["spans_total"] == 1


def test_the_check_runs_before_the_build_places_anything(tmp_path):
    """Before: the plan's spans were trusted until the media was
    already on the timeline."""
    import inspect

    from library.tools import reel_build
    source = inspect.getsource(reel_build.rebuild_reels_in_project)
    read_back = source.index("read_proposal(proposal_path)")
    verify = source.index("_verify_plan_span_claims")
    assert verify < read_back + 4000, (
        "the span verification must run at the plan read-back, before "
        "the build places media")
