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
        
def test_subtitle_sync_logic():
    # Simulate data
    v1_clips = [
        {'label': 'speech_3', 'timeline_in_frame': 116}, # estimated 3.88s * 30fps ~ 116
        {'label': 'speech_4', 'timeline_in_frame': 197}, # estimated 6.56s * 30fps ~ 197
    ]
    
    # Simulate what GetStart() returns (the actual V1 timeline positions)
    # block 3 actual: 4.37s * 30fps ~ 131
    # block 4 actual: 5.97s * 30fps ~ 179
    v1_timeline_items = [
        MockTimelineItem(131, 60),
        MockTimelineItem(179, 60)
    ]
    
    v1_placed_labels = ['speech_3', 'speech_4']
    
    # 1. Build mapping
    block_offsets = {}
    placed_by_label = dict(zip(v1_placed_labels, v1_timeline_items))
    
    for clip in v1_clips:
        label = clip.get('label', '')
        parts = label.split('_')
        if len(parts) >= 2 and parts[0] in ('speech', 'hook'):
            try:
                block_idx = int(parts[1])
                if block_idx not in block_offsets:
                    if label in placed_by_label:
                        placed = placed_by_label[label]
                        try:
                            actual_start = placed.GetStart()
                        except AttributeError:
                            actual_start = clip.get('timeline_in_frame', 0)
                            
                        estimated_start = clip.get('timeline_in_frame', 0)
                        block_offsets[block_idx] = actual_start - estimated_start
            except ValueError:
                pass
                
    # Check offsets
    assert block_offsets[3] == 131 - 116 # 15 frames offset
    assert block_offsets[4] == 179 - 197 # -18 frames offset
    
    # 2. Process subtitle segments
    sub_segments = [
        {
            'overlay_path': 'sub_block_3.mov',
            'timeline_start': 3.88, # 116 frames
            '_block_position': 3
        },
        {
            'overlay_path': 'sub_block_4.mov',
            'timeline_start': 6.56, # 197 frames
            '_block_position': 4
        },
        {
            'overlay_path': 'sub_block_12.mov',
            'timeline_start': 43.0,
            '_block_position': 12 # Missing from V1!
        }
    ]
    
    import re
    fps = 30
    results = []
    
    for si, seg in enumerate(sub_segments):
        seg_basename = seg['overlay_path']
        block_idx = seg.get('_block_position')
        if block_idx is None:
            m = re.search(r'sub_block_(\d+)', seg_basename)
            if m:
                block_idx = int(m.group(1))
                
        if block_idx is not None and block_idx not in block_offsets:
            continue
            
        offset_f = block_offsets.get(block_idx, 0) if block_idx is not None else 0
        
        tl_in_frame = round(seg.get('timeline_start', 0) * fps)
        tl_in_frame += offset_f
        
        results.append((block_idx, tl_in_frame))
        
    assert len(results) == 2
    assert results[0] == (3, 116 + 15) # 131
    assert results[1] == (4, 197 - 18) # 179
    
    print("Test passed!")

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
