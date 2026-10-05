"""The watch block carries each strip's DECLARED framing.

A declared letterbox is the project's stated preference, not a defect,
and the visual reviewer cannot tell a declared letterbox from a conform
that failed to fill.  The `framing` column on each strip is what closes
that gap: it is read off the manifest's `framing_intent`, so a strip
whose clip declares bars is not flagged for having them.

These tests pin the label, the per-strip attribution, and the column in
the block.  They are the defect: project 001's warm render failed
validation because its visual review read the declared letterbox as a
black-matte defect.
"""

from library.tools.render_watch import (
    build_watch_block,
    framing_label,
    framing_labels_for_rows,
)


def _manifest():
    """A two-track manifest: landscape A-roll, portrait cutaway.

    Both declare the project's letterbox (0.0); the portrait cutaway
    delivers fill (1.0) because a source that already covers the frame
    has no bars to give.  The declared intent is what the reviewer is
    shown, so both read `letterbox`.
    """
    return {
        "project": {"frame_rate": 30.0},
        "tracks": {
            "V1": {"clips": [
                {"label": "aroll_1", "framing_intent": 0.0,
                 "framing_delivered": 0.0,
                 "timeline_in_frame": 0, "timeline_out_frame": 72},
                {"label": "aroll_2", "framing_intent": 0.0,
                 "framing_delivered": 0.0,
                 "timeline_in_frame": 162, "timeline_out_frame": 495},
            ]},
            "V2": {"clips": [
                {"label": "cutaway", "framing_intent": 0.0,
                 "framing_delivered": 1.0,
                 "timeline_in_frame": 72, "timeline_out_frame": 162},
            ]},
        },
    }


def _rows():
    return [
        {"span_start": 0.0, "span_end": 2.4, "frames": 3, "file": "s0.png"},
        {"span_start": 2.4, "span_end": 5.4, "frames": 3, "file": "s1.png"},
        {"span_start": 5.4, "span_end": 16.5, "frames": 3, "file": "s2.png"},
    ]


def test_framing_label_reads_the_declaration():
    assert framing_label(0.0) == "letterbox"
    assert framing_label(1.0) == "fill"
    assert framing_label(0.5) == "partial"
    assert framing_label(None) == ""


def test_labels_follow_the_clip_playing_over_each_strip():
    # The V2 cutaway covers 2.4-5.4s; the later span wins the overlap,
    # so that strip is labelled with the cutaway's declaration.
    labels = framing_labels_for_rows(_manifest(), _rows())
    assert labels == ["letterbox", "letterbox", "letterbox"]


def test_labels_empty_when_the_manifest_declares_nothing():
    manifest = {"project": {"frame_rate": 30.0},
                "tracks": {"V1": {"clips": [
                    {"label": "a", "timeline_in_frame": 0,
                     "timeline_out_frame": 72}]}}}
    assert framing_labels_for_rows(manifest, _rows()) == ["", "", ""]


def test_labels_empty_when_there_is_no_manifest():
    assert framing_labels_for_rows({}, _rows()) == ["", "", ""]


def test_block_carries_the_framing_column():
    block = build_watch_block("/tmp/x", _rows(),
                              framing=["letterbox", "fill", "partial"])
    assert "framing" in block
    # The header row names the column and each strip carries its label.
    assert "[3]{span_start,span_end,frames,file,framing}" in block
    assert "s0.png\tletterbox" in block
    assert "s1.png\tfill" in block
    assert "s2.png\tpartial" in block


def test_block_without_framing_leaves_the_column_empty():
    # The render step and the rewatch path draw strips without a manifest,
    # so the column must be present but empty rather than absent.
    block = build_watch_block("/tmp/x", _rows())
    assert "[3]{span_start,span_end,frames,file,framing}" in block
    # Each data row ends with a tab and nothing after it.
    assert "s0.png\t\n" in block
    assert "s1.png\t\n" in block
    assert "s2.png\t\n" in block
