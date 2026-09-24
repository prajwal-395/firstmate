"""A caption id is stable under every change outside its own block.

The id used to be a single counter across the whole timeline, so it named
the card's ordinal position in the finished video.  Measured on project
001: forcing one spine block to produce five more cards renumbered 13
entries in blocks that had not changed.

Nothing reads the id, so that was harmless - until a region-scoped
re-plan has to PROVE it changed only the region it was given, which it
does by comparing the entries either side of it.  Under a global counter
that comparison reports churn that is not there, and a proof that cries
wolf is worth no more than one that cannot fail (AGENTS.md 10.4).

`library/steps/step_4_01_plan_subtitles/step.py`.
"""

from library.steps.step_4_01_plan_subtitles.step import generate_subtitles


def _speech_block(position, timeline_start, source_start, words,
                  duration=None):
    """One spine block.

    `duration` is settable so a fixture can change how many words a block
    holds without changing how long it lasts - the words are spread
    evenly across the span either way.  That separation matters: a
    duration change legitimately shifts every later block, so a test
    about card count has to hold duration steady or it measures the
    wrong thing.
    """
    duration = round(len(words) * 0.5, 3) if duration is None else duration
    stride = duration / max(len(words), 1)
    timestamps = [
        {
            "word": word,
            "source_start": round(source_start + i * stride, 3),
            "source_end": round(source_start + i * stride + stride * 0.8, 3),
        }
        for i, word in enumerate(words)
    ]
    return {
        "position": position,
        "block_type": "hook" if position == "hook" else "speech",
        "clip_id": "clip_001",
        "source_start": source_start,
        "source_end": round(source_start + duration, 3),
        "timeline_start": timeline_start,
        "timeline_end": round(timeline_start + duration, 3),
        "word_timestamps": timestamps,
        "alignment_method": "whisperx",
        "content": {"text": " ".join(words), "word_timestamps": timestamps},
    }


_BLOCK_3_SECONDS = 8.0
"""Block 3's span, held constant across every fixture in this file."""


def _spine(third_block_words):
    blocks = [
        _speech_block("hook", 0.0, 0.5, ["alpha", "bravo", "charlie"]),
        _speech_block(2, 1.5, 20.0, ["delta", "echo", "foxtrot"]),
        _speech_block(3, 3.0, 40.0, third_block_words,
                      duration=_BLOCK_3_SECONDS),
    ]
    last = blocks[-1]
    blocks.append(_speech_block(4, last["timeline_end"], 60.0,
                                ["yankee", "zulu"]))
    # Lay the blocks end to end the way mesh_spine's post_bridge does.
    cursor = 0.0
    for block in blocks:
        duration = block["timeline_end"] - block["timeline_start"]
        block["timeline_start"] = round(cursor, 3)
        block["timeline_end"] = round(cursor + duration, 3)
        cursor += duration
    return {"structure": blocks, "frame_rate": 30.0}


def _plan(spine):
    return generate_subtitles(spine)["subtitle_plan"]["subtitle_entries"]


def test_an_id_names_its_block_and_its_place_within_it():
    entries = _plan(_spine(["golf", "hotel"]))
    for entry in entries:
        assert entry["id"].startswith(
            f"sub_{str(entry['spine_block_position']).lower()}_")
    by_block = {}
    for entry in entries:
        by_block.setdefault(entry["spine_block_position"], []).append(entry["id"])
    for position, ids in by_block.items():
        assert ids == [f"sub_{str(position).lower()}_{n:03d}"
                       for n in range(1, len(ids) + 1)], position


def test_ids_outside_a_changed_block_do_not_move():
    """The regression the global counter caused, asserted directly.

    The two spines hold block 3 to the same span and give it a different
    number of words inside it - what a re-index that hears more words in
    the same audio produces - so the block splits into a different number
    of cards while every block's timing stays put.  That isolates the
    thing under test: a change to card count must not reach another
    block.  Letting the duration move instead would legitimately shift
    every later block (`step_2_05_mesh_spine/post_bridge.py` lays them
    end to end) and the test would pass or fail for the wrong reason.
    """
    few = _spine(["golf", "hotel", "india", "juliet"])
    many = _spine([f"word{n}" for n in range(24)])
    assert ([b["timeline_start"] for b in few["structure"]]
            == [b["timeline_start"] for b in many["structure"]]), \
        "the fixture must hold every block's timing steady"

    short, long = _plan(few), _plan(many)
    assert len(long) > len(short), "the change must alter the card count"

    def outside(entries):
        return {e["id"]: (e["text"], e["timeline_start"])
                for e in entries if e["spine_block_position"] != 3}

    assert set(outside(short)) == set(outside(long))
    assert outside(short) == outside(long)


def test_the_hook_block_gets_a_named_id_rather_than_an_ordinal():
    entries = _plan(_spine(["golf", "hotel"]))
    hook_ids = [e["id"] for e in entries if e["spine_block_position"] == "hook"]
    assert hook_ids and all(i.startswith("sub_hook_") for i in hook_ids)


