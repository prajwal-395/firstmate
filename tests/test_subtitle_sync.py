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
