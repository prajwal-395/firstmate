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


