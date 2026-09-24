import pytest
from unittest.mock import Mock, MagicMock

class MockTimelineItem:
    def __init__(self, start, duration):
        self._start = start
        self._duration = duration

    def GetStart(self):
        return self._start
        
    def GetDuration(self):
        return self._duration
        

def test_subtitle_cascade_no_drop():
    from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
    
    audio_spine = {
        "structure": [
            {
                "position": 1,
                "block_type": "speech",
                "timeline_start": 20.87,
                "timeline_end": 24.41,
                "source_start": 63.135,
                "source_end": 66.675, 
                "content": {"text": "and so my very, very small announcement is that i just want to post every single day."},
                "word_timestamps": [
                    {"word": "and", "source_start": 63.0, "source_end": 63.1},
                    {"word": "so", "source_start": 63.1, "source_end": 63.2},
                    {"word": "my", "source_start": 63.2, "source_end": 63.3},
                    {"word": "very,", "source_start": 63.3, "source_end": 63.4},
                    {"word": "very", "source_start": 63.4, "source_end": 63.5},
                    {"word": "small", "source_start": 63.5, "source_end": 63.6},
                    {"word": "announcement", "source_start": 63.6, "source_end": 64.0},
                    {"word": "is", "source_start": 64.0, "source_end": 64.1},
                    {"word": "that", "source_start": 64.1, "source_end": 64.2},
                    {"word": "i", "source_start": 64.2, "source_end": 64.3},
                    {"word": "just", "source_start": 64.3, "source_end": 64.4},
                    {"word": "want", "source_start": 64.4, "source_end": 64.5},
                    {"word": "to", "source_start": 64.5, "source_end": 65.0},
                    {"word": "post", "source_start": 65.0, "source_end": 65.5},
                    {"word": "every", "source_start": 65.5, "source_end": 66.0},
                    {"word": "single", "source_start": 66.395, "source_end": 66.535},
                    {"word": "day.", "source_start": 66.595, "source_end": 66.675},
                ]
            }
        ]
    }
    
    result = generate_subtitles(audio_spine, caption_case="lowercase", brand_effect={}, brand_style={})
    entries = result["subtitle_plan"]["subtitle_entries"]
    
    # Assert that all words made it through the cascade and clamping logic.
    # Previous behaviour truncated the tail of the block ("single day.").
    #
    # This asserts the WORDS survive, not which card each lands on. It used
    # to assert the literal card "single day.", which was the grouping a
    # `max_chars = 18` fallback produced; captions are grouped by measured
    # width now (library/tools/safe_area.py, and step 4.01's CaptionFitter),
    # and the split is balanced rather than greedy, so which card any given
    # word lands on is not stable and is not the invariant here. The
    # invariant the test is named for is that nothing is dropped.
    texts = [e["text"] for e in entries]
    spoken = " ".join(w["word"] for w in audio_spine["structure"][0]
                      ["word_timestamps"]).lower()
    assert " ".join(texts) == spoken, (
        f"words lost or reordered.\n  got: {texts}\n  want: {spoken}")
    # The tail of the block specifically, because that is what used to be
    # truncated - on whichever card the split put them.
    assert texts[-1].endswith("single day."), texts
