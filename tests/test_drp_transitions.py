import pytest
import os
import zipfile
from io import BytesIO
from library.tools.execution.apply_native_transitions import apply_native_transitions

# apply_native_transitions is an UNUSED tool. The DRP-surgery route it
# belongs to is closed: it wrote to a temp file that the renderer never
# loaded, and the pipeline draws transitions with Fusion instead (see
# library/tools/transition_vocabulary.py). These tests keep the tool
# honest; nothing in the pipeline calls it.

@pytest.fixture
def dummy_drp():
    # Create a dummy DRP with a SeqContainer containing a VideoTrackVec
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<Sm2SequenceContainer DbId="1234">
 <VideoTrackVec>
  <Element>
   <Sm2TiTrack DbId="5678">
    <Items>
     <Element>
      <Sm2TiVideoClip>
       <Start>0</Start>
       <Duration>96</Duration>
      </Sm2TiVideoClip>
     </Element>
     <Element>
      <Sm2TiVideoClip>
       <Start>96</Start>
       <Duration>100</Duration>
      </Sm2TiVideoClip>
     </Element>
    </Items>
   </Sm2TiTrack>
  </Element>
 </VideoTrackVec>
</Sm2SequenceContainer>
"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr("SeqContainer1.xml", xml_content)
    
    return buf.getvalue()

def test_apply_native_transitions_no_ops():
    res = apply_native_transitions("/tmp/fake.drp", [])
    assert res == "/tmp/fake.drp"

def test_apply_native_transitions_insertion(tmp_path, dummy_drp):
    drp_file = tmp_path / "test.drp"
    drp_file.write_bytes(dummy_drp)
    
    transitions = [
        {"track": 1, "at_frame": 96, "duration_frames": 24, "type": "Cross Dissolve"}
    ]
    
    apply_native_transitions(str(drp_file), transitions)
    
    # Check that transition was added
    with zipfile.ZipFile(drp_file, 'r') as z:
        xml = z.read("SeqContainer1.xml").decode("utf-8")
        assert "<Sm2TiTransition" in xml
        assert "<PrettyType>Cross Dissolve</PrettyType>" in xml
        # Centered transition at cut 96, dur 24 => start 84
        assert "<Start>84</Start>" in xml
        assert "<Duration>24</Duration>" in xml

def test_apply_native_transitions_unsupported(tmp_path, dummy_drp):
    drp_file = tmp_path / "test.drp"
    drp_file.write_bytes(dummy_drp)
    
    transitions = [
        {"track": 1, "at_frame": 96, "duration_frames": 24, "type": "Unsupported"}
    ]
    
    apply_native_transitions(str(drp_file), transitions)
    
    with zipfile.ZipFile(drp_file, 'r') as z:
        xml = z.read("SeqContainer1.xml").decode("utf-8")
        assert "<Sm2TiTransition" not in xml
