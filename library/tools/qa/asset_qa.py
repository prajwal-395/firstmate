import os
import subprocess
import json
import sys

def verify_alpha_channel(mov_path: str) -> bool:
    """
    Verify that the given .mov file has an alpha channel and is not purely black.
    Returns True if valid, False otherwise.
    """
    if not os.path.exists(mov_path):
        return False
        
    # Check for alpha using ffprobe
    cmd_probe = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=pix_fmt,codec_name,profile",
        "-of", "json",
        mov_path
    ]
    try:
        result = subprocess.run(cmd_probe, capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
        stream = data.get("streams", [{}])[0]
        pix_fmt = stream.get("pix_fmt", "")
        codec_name = stream.get("codec_name", "")
        profile = stream.get("profile", "")
        
        has_alpha = "yuva" in str(pix_fmt).lower() or (str(codec_name).lower() == "prores" and "4444" in str(profile))
        if not has_alpha:
            print(f"QA: Alpha check failed for {mov_path}: no alpha detected (pix_fmt={pix_fmt}, codec={codec_name}, profile={profile})", file=sys.stderr)
            return False
            
    except Exception as e:
        print(f"QA: Alpha check failed to run ffprobe: {e}", file=sys.stderr)
        return False
        
    # Check for non-black pixels by sampling a frame
    cmd_img = [
        "ffmpeg", "-v", "error",
        "-ss", "00:00:00.500", # sample at 0.5s or whatever is available
        "-i", mov_path,
        "-vframes", "1",
        "-f", "image2pipe",
        "-vcodec", "rawvideo",
        "-pix_fmt", "rgba",
        "-"
    ]
    try:
        res = subprocess.run(cmd_img, capture_output=True, timeout=10)
        # If sampling at 0.5s fails, try at 0.0s
        if not res.stdout:
            cmd_img[4] = "00:00:00.000"
            res = subprocess.run(cmd_img, capture_output=True, timeout=10)
            
        if not res.stdout:
            print(f"QA: Alpha check failed to extract frame from {mov_path}", file=sys.stderr)
            return False
            
        # Check if there's any non-zero value in RGB channels (ignoring Alpha which is every 4th byte)
        is_all_black = True
        for i in range(0, len(res.stdout), 4):
            # Check R, G, B
            if i + 2 < len(res.stdout):
                if res.stdout[i] != 0 or res.stdout[i+1] != 0 or res.stdout[i+2] != 0:
                    is_all_black = False
                    break
                    
        if is_all_black:
            print(f"QA: Alpha check failed for {mov_path}: frame is purely black", file=sys.stderr)
            return False
            
    except Exception as e:
        print(f"QA: Alpha check pixel verification failed: {e}", file=sys.stderr)
        return False

    return True
