import os
import re
import uuid
import zipfile
from io import BytesIO

TEMPLATE_TRANSITION = """<Element>
      <Sm2TiTransition DbId="{db_id}">
       <FieldsBlob>000000020000001c8012190000002c789c6366606400028ee3f30fbf642000006baa031f</FieldsBlob>
       <PrettyType>Cross Dissolve</PrettyType>
       <Name>Cross Dissolve</Name>
       <Start>{start}</Start>
       <Duration>{duration}</Duration>
       <LinkedItemSync/>
       <WasDisbanded>false</WasDisbanded>
       <MarkersBA/>
       <UiMemento>0</UiMemento>
       <Flags>0</Flags>
       <PriorityIndex>0</PriorityIndex>
       <EffectFiltersBA>000000020000003a800a37081238024a004a004a004a004a29088b015224ffffffe00000000000000c0000000000000000001800000000000c00000000000000f03f</EffectFiltersBA>
       <ImportExportMetadataBA/>
       <RenderTextEnabled>true</RenderTextEnabled>
       <RenderTextGanged>true</RenderTextGanged>
       <RenderTextPrefixed>true</RenderTextPrefixed>
       <AlignmentType>2</AlignmentType>
       <Position>2</Position>
      </Sm2TiTransition>
     </Element>"""

def split_elements(regex, text):
    return [m.group(0) for m in re.finditer(regex, text)]

def apply_native_transitions(drp_path: str, transitions: list) -> str:
    """
    Applies native transitions to the DaVinci Resolve Project (.drp) via XML surgery.
    """
    if not transitions:
        return drp_path

    # Read zip in memory
    with open(drp_path, 'rb') as f:
        drp_bytes = f.read()

    # Apply each transition
    for trans in transitions:
        if trans.get("type") not in ("Cross Dissolve",):
            print(f"Skipping unsupported native transition type: {trans.get('type')}")
            continue

        with zipfile.ZipFile(BytesIO(drp_bytes), 'r') as z_in:
            seq_entries = [info.filename for info in z_in.infolist() 
                           if re.search(r'(^|/)SeqContainer(\d*\.xml|/[^/]+\.xml)$', info.filename)]
            
            if not seq_entries:
                raise ValueError("No SeqContainer found in DRP")

            # Assume first timeline with VideoTrackVec
            target_entry = None
            target_xml = ""
            for e in seq_entries:
                xml_data = z_in.read(e).decode('utf-8')
                if "<VideoTrackVec>" in xml_data:
                    target_entry = e
                    target_xml = xml_data
                    break
            
            if not target_entry:
                raise ValueError("No VideoTrackVec found in DRP SeqContainers")

            # Extract VideoTrackVec
            vtv_match = re.search(r'<VideoTrackVec>([\s\S]*?)</VideoTrackVec>', target_xml)
            if not vtv_match:
                raise ValueError("Could not parse VideoTrackVec")
            
            vtv_inner = vtv_match.group(1)
            
            # Extract tracks
            tracks = split_elements(r'<Element>\s*<Sm2TiTrack\b[\s\S]*?</Sm2TiTrack>\s*</Element>', vtv_inner)
            
            track_idx = trans.get("track", 1) - 1
            if track_idx < 0 or track_idx >= len(tracks):
                raise ValueError(f"Track {track_idx + 1} does not exist")
                
            track_xml = tracks[track_idx]
            
            # Find Items
            items_match = re.search(r'<Items>([\s\S]*?)</Items>', track_xml)
            if not items_match:
                continue
                
            items_inner = items_match.group(1)
            
            # Find clips
            clip_regex = r'<Element>\s*<Sm2Ti(?:VideoClip|AudioClip|Generator)\b[\s\S]*?</Sm2Ti(?:VideoClip|AudioClip|Generator)>\s*</Element>'
            clips = split_elements(clip_regex, items_inner)
            
            at_frame = trans.get("at_frame")
            duration = trans.get("duration_frames", 24)
            
            left_idx = -1
            for i in range(len(clips) - 1):
                start_match = re.search(r'<Start>(\d+)</Start>', clips[i])
                dur_match = re.search(r'<Duration>(\d+)</Duration>', clips[i])
                if start_match and dur_match:
                    end = int(start_match.group(1)) + int(dur_match.group(1))
                    next_start_match = re.search(r'<Start>(\d+)</Start>', clips[i+1])
                    if next_start_match and end == int(next_start_match.group(1)) and end == at_frame:
                        left_idx = i
                        break
            
            if left_idx < 0:
                print(f"Warning: No abutting clip boundary at frame {at_frame} on track {track_idx+1}")
                continue
                
            # Create transition
            start_frame = at_frame - (duration // 2)
            trans_xml = TEMPLATE_TRANSITION.format(
                db_id=str(uuid.uuid4()),
                start=start_frame,
                duration=duration
            )
            
            # Insert transition
            new_items_inner = items_inner.replace(clips[left_idx], clips[left_idx] + "\n" + trans_xml)
            new_track_xml = track_xml.replace(f"<Items>{items_inner}</Items>", f"<Items>{new_items_inner}</Items>")
            new_vtv_inner = vtv_inner.replace(track_xml, new_track_xml)
            new_target_xml = target_xml.replace(f"<VideoTrackVec>{vtv_inner}</VideoTrackVec>", f"<VideoTrackVec>{new_vtv_inner}</VideoTrackVec>")
            
            # Rebuild Zip in memory
            out_bytes = BytesIO()
            with zipfile.ZipFile(out_bytes, 'w', zipfile.ZIP_DEFLATED) as z_out:
                for item in z_in.infolist():
                    if item.filename == target_entry:
                        z_out.writestr(item, new_target_xml.encode('utf-8'))
                    else:
                        z_out.writestr(item, z_in.read(item.filename))
                        
            drp_bytes = out_bytes.getvalue()

    with open(drp_path, 'wb') as f:
        f.write(drp_bytes)

    return drp_path
