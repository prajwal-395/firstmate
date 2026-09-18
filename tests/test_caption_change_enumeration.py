"""The changed-card enumeration comes from the ledger, never from memory.

`library/tools/caption_change_enumeration.py` builds the PR-body
before/after listing a lane pastes - every changed card grouped by
reel, with counts, names and timelines all read off the render ledger
and the props sidecars. These tests pin that contract on synthetic
fixtures (a `tmp_path` asset dir plus a ledger), so no test reaches a
real project: pairing by text derivation, the canvas-rerender skip,
same-text disambiguation, the live/historical split, and the
ledger-unbound boundary that needs a Resolve read-back.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, ".")

from library.tools.caption_change_enumeration import (
    ChangeRecord,
    card_text_from_props,
    collect_caption_changes,
    pair_predecessor,
    render_enumeration,
)


def _props(path, text, *, card_index=0, block=0):
    payload = {
        "subtitles": [{"text": text, "startFrame": 0, "endFrame": 10, "words": []}],
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": 10,
        "_block_position": block,
        "_card_index": card_index,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _mov(asset, stem):
    """A rendered file plus its props record; returns the mov path."""
    mov = os.path.join(asset, stem + ".mov")
    with open(mov, "wb") as handle:
        handle.write(b"fake-pixels")
    return mov


def _entry(segment_id, overlay_path, timeline, superseded):
    return {
        "segment_id": segment_id,
        "overlay_path": overlay_path,
        "binding": {
            "timeline": timeline,
            "speaker": "Craig",
            "block_position": 0,
            "source_clip_id": "clip",
            "source_start": 1.0,
            "source_end": 2.0,
        },
        "provenance": "rendered",
        "superseded": superseded,
    }


def _ledger(asset, entries):
    path = os.path.join(asset, "render_ledger.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"subtitle_overlay": {"segments": entries}}, handle)
    return path


def _collect(asset, ledger, **kwargs):
    return collect_caption_changes(asset, ledger, **kwargs)


class TestPairPredecessor:
    def test_reading_derivation_pairs(self):
        before, basis = pair_predecessor(
            "SEO 2.0", {"seo two point oh", "unrelated card here"}
        )
        assert (before, basis) == ("seo two point oh", "reading")

    def test_subsequence_pairs_the_splice_shape(self):
        before, basis = pair_predecessor(
            "the link's in our bio.", {"the link's bio.", "unrelated card here"}
        )
        assert (before, basis) == ("the link's bio.", "subsequence")

    def test_reading_self_derives_by_idempotence_and_collect_filters_it(self):
        # The reading is idempotent, so an identical text derives
        # itself here; collect_caption_changes discards identical
        # olds before pairing, so a self-pair never reaches the
        # enumeration - the filter lives there, not in the matcher.
        before, basis = pair_predecessor("AI sees you.", {"AI sees you."})
        assert (before, basis) == ("AI sees you.", "reading")

    def test_several_derivations_are_ambiguous_not_picked(self):
        before, basis = pair_predecessor("AI", {"ai", "Ai"})
        assert (before, basis) == (None, "ambiguous")


class TestCardText:
    def test_missing_props_reads_as_missing_not_empty(self, tmp_path):
        assert card_text_from_props(str(tmp_path / "gone_props.json")) is None

    def test_multi_card_segment_joins(self, tmp_path):
        path = str(tmp_path / "x_props.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"subtitles": [{"text": "one"}, {"text": "two"}]}, handle)
        assert card_text_from_props(path) == "one | two"


class TestCollect:
    def test_reading_change_pairs_from_superseded(self, tmp_path):
        asset = str(tmp_path)
        old = _mov(asset, "sub_craig_clip_1000-2000_aaaa1111")
        _props(os.path.splitext(old)[0] + "_props.json", "seo two point oh")
        new = _mov(asset, "sub_craig_clip_1000-2000_bbbb2222")
        _props(
            os.path.splitext(new)[0] + "_props.json",
            "SEO 2.0",
            card_index=None,
        )
        ledger = _ledger(
            asset, [_entry("sub_craig_clip_1000-2000_bbbb2222", new, "Reel 01", [old])]
        )
        found = _collect(asset, ledger)
        assert [(c.timeline, c.before, c.after, c.basis) for c in found.changes] == [
            ("Reel 01", "seo two point oh", "SEO 2.0", "reading")
        ]
        assert found.diagnostics == []

    def test_canvas_rerender_with_same_text_is_skipped(self, tmp_path):
        asset = str(tmp_path)
        old = _mov(asset, "sub_craig_clip_1000-2000_aaaa1111")
        _props(os.path.splitext(old)[0] + "_props.json", "same words here")
        new = _mov(asset, "sub_craig_clip_1000-2000_bbbb2222")
        _props(os.path.splitext(new)[0] + "_props.json", "same words here")
        ledger = _ledger(
            asset, [_entry("sub_craig_clip_1000-2000_bbbb2222", new, "Reel 01", [old])]
        )
        found = _collect(asset, ledger)
        assert found.changes == []

    def test_sibling_cards_in_superseded_list_do_not_mispair(self, tmp_path):
        asset = str(tmp_path)
        sibling = _mov(asset, "sub_craig_clip_1000-2000_aaaa1111")
        _props(os.path.splitext(sibling)[0] + "_props.json", "agencies ai over here")
        true_old = _mov(asset, "sub_craig_clip_1000-2000_cccc3333")
        _props(os.path.splitext(true_old)[0] + "_props.json", "companies fifty times x")
        new = _mov(asset, "sub_craig_clip_1000-2000_bbbb2222")
        _props(os.path.splitext(new)[0] + "_props.json", "companies 50 times x")
        ledger = _ledger(
            asset,
            [_entry("sub_craig_clip_1000-2000_bbbb2222", new, "Reel 06", [sibling, true_old])],
        )
        found = _collect(asset, ledger)
        assert [(c.before, c.after) for c in found.changes] == [
            ("companies fifty times x", "companies 50 times x")
        ]

    def test_ambiguous_entry_is_reported_not_paired(self, tmp_path):
        asset = str(tmp_path)
        old1 = _mov(asset, "sub_craig_clip_1000-2000_aaaa1111")
        _props(os.path.splitext(old1)[0] + "_props.json", "ai")
        old2 = _mov(asset, "sub_craig_clip_1000-2000_cccc3333")
        _props(os.path.splitext(old2)[0] + "_props.json", "Ai")
        new = _mov(asset, "sub_craig_clip_1000-2000_bbbb2222")
        _props(os.path.splitext(new)[0] + "_props.json", "AI")
        ledger = _ledger(
            asset,
            [_entry("sub_craig_clip_1000-2000_bbbb2222", new, "Reel 01", [old1, old2])],
        )
        found = _collect(asset, ledger)
        assert found.changes == []
        assert [d.reason for d in found.diagnostics] == ["ambiguous"]

    def test_unreadable_new_props_are_reported(self, tmp_path):
        asset = str(tmp_path)
        new = _mov(asset, "sub_craig_clip_1000-2000_bbbb2222")
        ledger = _ledger(
            asset, [_entry("sub_craig_clip_1000-2000_bbbb2222", new, "Reel 01", ["x"])]
        )
        found = _collect(asset, ledger)
        assert found.changes == []
        assert [d.reason for d in found.diagnostics] == ["new-props-unreadable"]

    def test_unbound_file_stays_out_without_placements(self, tmp_path):
        asset = str(tmp_path)
        old = _mov(asset, "sub_craig_clip_1000-2000_aaaa1111")
        _props(os.path.splitext(old)[0] + "_props.json", "seven modules here")
        new = _mov(asset, "sub_craig_clip_1000-2000_bbbb2222")
        _props(os.path.splitext(new)[0] + "_props.json", "7 modules here")
        ledger = _ledger(asset, [])
        found = _collect(asset, ledger)
        assert found.changes == []
        assert [d.reason for d in found.diagnostics] == ["ledger-unbound"]

    def test_unbound_placed_file_joins_with_placements(self, tmp_path):
        asset = str(tmp_path)
        old = _mov(asset, "sub_craig_clip_1000-2000_aaaa1111")
        _props(os.path.splitext(old)[0] + "_props.json", "seven modules here")
        new = _mov(asset, "sub_craig_clip_1000-2000_bbbb2222")
        _props(os.path.splitext(new)[0] + "_props.json", "7 modules here")
        ledger = _ledger(asset, [])
        found = _collect(asset, ledger, placements={"Reel 17": [new]})
        assert [(c.timeline, c.before, c.after, c.source) for c in found.changes] == [
            ("Reel 17", "seven modules here", "7 modules here", "placement")
        ]


class TestRender:
    def _changes(self):
        return [
            ChangeRecord(
                timeline="Reel 02",
                before="ai sees you.",
                after="AI sees you.",
                overlay_path="/a/x_bbbb2222.mov",
            ),
            ChangeRecord(
                timeline="Reel 01",
                before="seo two point oh",
                after="SEO 2.0",
                overlay_path="/a/y_cccc3333.mov",
            ),
            ChangeRecord(
                timeline="Reel 01",
                before="the link's bio.",
                after="the link's in our bio.",
                overlay_path="/a/z_dddd4444.mov",
            ),
        ]

    def test_exact_markdown_shape(self):
        assert render_enumeration(self._changes()) == (
            "TOTAL changed cards: 3 placement(s) across 3 file(s) on 2 timeline(s)\n"
            "\n"
            "### Reel 01 (2)\n"
            "- BEFORE 'seo two point oh'  ->  AFTER 'SEO 2.0'\n"
            "- BEFORE \"the link's bio.\"  ->  AFTER \"the link's in our bio.\"\n"
            "\n"
            "### Reel 02 (1)\n"
            "- BEFORE 'ai sees you.'  ->  AFTER 'AI sees you.'\n"
        )

    def test_counts_come_from_the_records(self):
        out = render_enumeration(self._changes())
        assert out.startswith(
            "TOTAL changed cards: 3 placement(s) across 3 file(s) on 2 timeline(s)"
        )

    def test_shared_file_repeats_per_reel(self):
        changes = [
            ChangeRecord(
                timeline="Reel 01", before="a", after="b", overlay_path="/a/1.mov"
            ),
            ChangeRecord(
                timeline="Reel 03", before="a", after="b", overlay_path="/a/1.mov"
            ),
        ]
        out = render_enumeration(changes)
        assert "across 1 file(s) on 2 timeline(s)" in out
        assert out.count("- BEFORE 'a'  ->  AFTER 'b'") == 2

    def test_same_text_distinct_files_are_disambiguated(self):
        changes = [
            ChangeRecord(
                timeline="Reel 11",
                before="both, ai checks both.",
                after="both, AI checks both.",
                overlay_path="/a/1_972fe5bc.mov",
            ),
            ChangeRecord(
                timeline="Reel 11",
                before="both, ai checks both.",
                after="both, AI checks both.",
                overlay_path="/a/2_8b087f32.mov",
            ),
        ]
        out = render_enumeration(changes)
        assert "### Reel 11 (2)" in out
        assert "- BEFORE 'both, ai checks both.'  ->  AFTER 'both, AI checks both.'" in out
        assert "- BEFORE 'both, ai checks both.'  ->  AFTER 'both, AI checks both.' [8b087f32]" in out

    def test_live_split_marks_historical_bindings(self):
        out = render_enumeration(
            self._changes(), live_timelines={"Reel 01", "Reel 02"}
        )
        assert "(3 live, 0 historical)" in out
        assert "(no live timeline)" not in out

    def test_historical_binding_is_named_not_dropped(self):
        changes = self._changes() + [
            ChangeRecord(
                timeline="Reel 26 (old)",
                before="ai",
                after="AI",
                overlay_path="/a/9.mov",
            )
        ]
        out = render_enumeration(changes, live_timelines={"Reel 01", "Reel 02"})
        assert "### Reel 26 (old) (1) (no live timeline)" in out
        assert "(3 live, 1 historical)" in out

    def test_no_live_claim_without_live_set(self):
        out = render_enumeration(self._changes())
        assert "live" not in out.splitlines()[0]
        assert "(no live timeline)" not in out

    def test_empty_changes_still_report_counts(self):
        assert render_enumeration([]) == (
            "TOTAL changed cards: 0 placement(s) across 0 file(s) on 0 timeline(s)\n"
        )


class TestLedgerRefusals:
    def test_missing_ledger_refuses(self, tmp_path):
        with pytest.raises(SystemExit):
            _collect(str(tmp_path), str(tmp_path / "render_ledger.json"))

    def test_ledger_without_segments_refuses(self, tmp_path):
        asset = str(tmp_path)
        ledger = _ledger(asset, [])
        with open(ledger, "w", encoding="utf-8") as handle:
            json.dump({"subtitle_overlay": {}}, handle)
        with pytest.raises(SystemExit):
            _collect(asset, ledger)
