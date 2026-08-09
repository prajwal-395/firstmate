import os
import sys

file_path = "/Users/prajwal/.treehouse/video_editing_pilot-9487f5/3/video_editing_pilot/library/steps/step_6_01_render/resolve_build_timeline.py"

with open(file_path, "r") as f:
    content = f.read()

import_code_old = """    # ── Import all media to pool ──
    all_media_paths = set()
    for clip in v1_clips + v2_clips:
        src = clip.get('source_file', '')
        if src and os.path.exists(src):
            all_media_paths.add(src)
    for clip in a2_clips + a3_clips:
        src = clip.get('source_file', '')
        if src and os.path.exists(src):
            all_media_paths.add(src)
    # Collect overlay segment media files
    for seg in sub_segments:
        p = seg.get('overlay_path', '')
        if p and os.path.exists(p):
            all_media_paths.add(p)
    for seg in mg_segments:
        p = seg.get('overlay_path', '')
        if p and os.path.exists(p):
            all_media_paths.add(p)

    if all_media_paths:
        imported = media_pool.ImportMedia(list(all_media_paths))
        print(f"✓ Imported {len(imported) if imported else 0} media files", file=sys.stderr)"""

import_code_new = """    # ── Import all media to pool with subdirectory organization ──
    root_folder = media_pool.GetRootFolder()

    def _import_to_folder(folder_name, paths):
        paths = list(set(p for p in paths if p and os.path.exists(p)))
        if not paths:
            return 0
        
        media_pool.SetCurrentFolder(root_folder)
        folder = None
        for sub in (root_folder.GetSubFolderList() or []):
            if sub.GetName() == folder_name:
                folder = sub
                break
        if not folder:
            folder = media_pool.AddSubFolder(root_folder, folder_name)
            
        media_pool.SetCurrentFolder(folder)
        imported = media_pool.ImportMedia(paths)
        media_pool.SetCurrentFolder(root_folder)
        return len(imported) if imported else 0

    total_imported = 0
    total_imported += _import_to_folder("V1", [c.get('source_file', '') for c in v1_clips])
    total_imported += _import_to_folder("V2", [c.get('source_file', '') for c in v2_clips])
    total_imported += _import_to_folder("Audio", [c.get('source_file', '') for c in a2_clips + a3_clips])
    total_imported += _import_to_folder("Subtitles", [s.get('overlay_path', '') for s in sub_segments])
    total_imported += _import_to_folder("MotionGraphics", [s.get('overlay_path', '') for s in mg_segments])
    
    if total_imported > 0:
        print(f"✓ Imported {total_imported} media files into subfolders", file=sys.stderr)"""

content = content.replace(import_code_old, import_code_new)

with open(file_path, "w") as f:
    f.write(content)
print("Patch 2 completed.")
