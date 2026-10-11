"""Lane A2: one moment key from selection to placement.

R8 (cascade plan, report section 2.2): proposal moments (number/slug),
selects, build spans and reel names shared no key - a renumbering or a
retitle silently broke the join.  `moment-{NN}-{span}` is minted ONCE at
selection and carried unchanged through every later record; readers of
records that predate the key derive it instead of refusing.

This test walks one fixture moment through every stage a real build
touches - select output, proposal file, build-time snap, build ranges,
build record, spine, placement ledger - and asserts the id is identical
at each.  No Resolve, no project writes outside `tmp_path`.
"""
from __future__ import annotations

import json

from library.steps.step_3_04_select_reels.post_bridge import resolve as select
from library.tools import timeline_decisions
from library.tools.reel_build import (
    built_name,
    moment_key,
    moment_span_record,
    promote_moment_records,
    reel_ranges,
)
from library.tools.reel_proposal import (
    ReelMoment,
    enrich,
    mint_moment_id,
    read_proposal,
    snap_moment_to_speech,
    write_proposal,
)
from library.tools.reel_spine import spine_for_reel


def _words(text, start, step=0.4):
    return [{"word": w, "start": start + i * step,
             "end": start + (i + 1) * step}
            for i, w in enumerate(text.split())]


def _spoken(text, start, end, uid, speaker):
    return {"speaker": speaker, "text": text,
            "timeline_start": start, "timeline_end": end,
            "source_file": "/m/a.MXF", "resolve_item_id": uid,
            "source_start": start, "source_end": end,
            "words": _words(text, start)}


def _transcript():
    return {
        "derived_from": {"timeline": "fixture",
                         "duration_seconds": 2656.6},
        "segments": [
            _spoken("seo is about convincing a crawler to rank a page",
                    10.0, 18.0, "uid-1", "SpeakerTwo"),
            _spoken("geo is about whether a machine comprehends a company",
                    18.5, 26.0, "uid-2", "SpeakerOne"),
        ],
    }


def test_one_moment_id_is_identical_at_every_stage(tmp_path):
    """The whole lane in one assertion chain: select -> proposal ->
    snap -> ranges -> build record -> spine -> placement ledger."""
    transcript = _transcript()

    # ── 1. select mints the key once ─────────────────────────────
    out = select(
        {"moments": [{
            "start": 10.0, "end": 26.0, "slug": "seo-vs-geo",
            "reason": ("the clearest one-line contrast in the episode, "
                       "both hosts with real turns"),
        }]},
        {"timeline_transcript": transcript, "project_folder": ""},
    )
    selected = out["reel_selection"]["moments"]
    assert len(selected) == 1
    moment_id = selected[0]["moment_id"]
    assert moment_id == mint_moment_id(1, 10.0, 26.0) == "moment-01-10.00-26.00"

    moment = ReelMoment.from_dict(selected[0])
    assert moment.moment_id == moment_id

    # ── 2. the proposal file round-trips it ──────────────────────
    path = write_proposal(tmp_path / "reel_proposals_v2.json",
                          [moment], transcript)
    (reread,) = read_proposal(path)
    assert reread.moment_id == moment_id

    # ── 3. records that predate the key derive the same one ──────
    legacy = {k: v for k, v in json.loads(path.read_text(
        encoding="utf-8"))["moments"][0].items() if k != "moment_id"}
    assert ReelMoment.from_dict(legacy).moment_id == moment_id
    # ... and serialising a keyless in-memory moment mints it too.
    keyless = enrich(ReelMoment(
        number=1, slug="seo-vs-geo", reason="r",
        timeline_start=10.0, timeline_end=26.0), transcript)
    assert keyless.as_dict()["moment_id"] == moment_id

    # ── 4. the build-time snap carries it, never re-mints ────────
    repaired, _moves = snap_moment_to_speech(moment, transcript)
    assert repaired.moment_id == moment_id

    # ── 5. build spans and the reel name join on it ──────────────
    ranges = reel_ranges(repaired, transcript)
    assert ranges, "the fixture moment must play something"
    assert built_name(repaired) == repaired.timeline_name
    assert moment_key(repaired) == moment_id

    final = repaired.timeline_name
    staging = f"{final} (rebuild staging)"
    record = moment_span_record(repaired, ranges, final, staging)
    assert record["body"] == [10.0, 26.0]
    assert record["ranges"] == [[float(s), float(e)] for s, e in ranges]

    moment_ids, moment_spans = promote_moment_records(
        {staging: moment_id}, {moment_id: record},
        {final: staging}, [final])
    assert moment_ids[final] == moment_id
    assert moment_spans[moment_id]["reel"] == final
    assert moment_spans[moment_id]["ranges"] == record["ranges"]

    # ── 6. the spine carries it ──────────────────────────────────
    spine = spine_for_reel(repaired, transcript, list(ranges))
    assert spine["structure"], "the fixture moment must spine"
    assert spine["moment_id"] == moment_id
    assert spine["reel"] == final

    # ── 7. the placement ledger carries it ───────────────────────
    manifest = {
        "moment_id": moment_id,
        "project": {"name": final, "frame_rate": 30.0},
        "tracks": {"V1": {"clips": [
            {"source_file": "/m/a.MXF",
             "timeline_in": float(s), "timeline_out": float(e),
             "moment_id": moment_id, "reel": final}
            for s, e in ranges
        ]}},
    }
    ledger = timeline_decisions.build_ledger(manifest)
    assert ledger["moment_id"] == moment_id
    assert ledger["placements"], "the fixture ranges must place"
    for placement in ledger["placements"]:
        assert placement["moment_id"] == moment_id
        assert placement["reel"] == final

    # ── 8. readers stay compatible with records that lack it ─────
    bland = timeline_decisions.build_ledger({
        "project": {"name": final, "frame_rate": 30.0},
        "tracks": {"V1": {"clips": [
            {"source_file": "/m/a.MXF",
             "timeline_in": 0.0, "timeline_out": 5.0},
        ]}},
    })
    assert bland["moment_id"] == ""
    assert bland["placements"][0]["moment_id"] == ""
    assert bland["placements"][0]["reel"] == ""
