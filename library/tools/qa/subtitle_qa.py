import os
import sys
import tempfile
import subprocess
import glob
from pathlib import Path

# Add tools to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from llm_client import LLMClient

def run_subtitle_qa(mov_path: str, project_folder: str = None) -> dict:
    """Runs vision QA on a rendered subtitle segment."""
    if os.environ.get("SKIP_QA_CHECKS") == "1":
        print("Skipping subtitle QA check (SKIP_QA_CHECKS=1)", file=sys.stderr)
        return {"passed": True, "reason": "skipped"}
        
    print(f"Running subtitle QA on {os.path.basename(mov_path)}...", file=sys.stderr)
    
    # Get project folder to put temp frames in
    if not project_folder:
        # try to derive from mov_path, assuming it's in pipeline_output/subtitle_segments/
        project_folder = os.path.dirname(os.path.dirname(os.path.dirname(mov_path)))
        
    qa_frames_dir = os.path.join(project_folder, "pipeline_output", "qa_frames")
    os.makedirs(qa_frames_dir, exist_ok=True)
    
    # Extract 2-3 frames from it using ffmpeg
    # One near start (e.g., 0.5s), one in middle (e.g., 1.5s)
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', mov_path],
            capture_output=True, text=True, check=True
        )
        duration = float(result.stdout.strip())
    except Exception as e:
        print(f"Warning: could not get duration of {mov_path}, defaulting to 2s: {e}", file=sys.stderr)
        duration = 2.0
        
    t1 = min(0.5, duration * 0.25)
    t2 = min(1.5, duration * 0.75)
    
    basename = os.path.basename(mov_path).replace('.mov', '')
    frame1_path = os.path.join(qa_frames_dir, f"{basename}_frame1.png")
    frame2_path = os.path.join(qa_frames_dir, f"{basename}_frame2.png")
    
    try:
        subprocess.run(['ffmpeg', '-y', '-ss', str(t1), '-i', mov_path, '-frames:v', '1', frame1_path], 
                      capture_output=True, check=True)
        subprocess.run(['ffmpeg', '-y', '-ss', str(t2), '-i', mov_path, '-frames:v', '1', frame2_path], 
                      capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to extract frames for QA: {e.stderr.decode()}")
        
    # Prepare LLM call
    client = LLMClient(provider="gemini", model="gemini-1.5-pro")
    
    try:
        import PIL.Image
        img1 = PIL.Image.open(frame1_path)
        img2 = PIL.Image.open(frame2_path)
    except ImportError:
        raise RuntimeError("Pillow is required for Gemini Vision API.")
        
    prompt_text = """This is a rendered subtitle overlay for vertical shortform video (1080x1920). Check:
(1) Are words properly spaced and readable?
(2) Is text positioned at the bottom of the frame?
(3) Is the font style clean and professional?
(4) Are there any obvious rendering issues?
Report pass/fail with specific issues. 
Format your response starting with exactly "PASS" or "FAIL", followed by a newline and your explanation.
If there is no text at all on the frames, and they are completely blank, report FAIL."""

    prompt = [prompt_text, img1, img2]
    
    print("Calling Gemini Vision API...", file=sys.stderr)
    try:
        response = client.generate(prompt)
    except Exception as e:
        raise RuntimeError(f"Gemini API call failed: {e}")
        
    # Clean up frames
    try:
        os.remove(frame1_path)
        os.remove(frame2_path)
    except OSError:
        pass
        
    if not response:
        raise RuntimeError("Empty response from Gemini Vision API")
        
    is_pass = response.strip().upper().startswith("PASS")
    
    if not is_pass:
        raise RuntimeError(f"Subtitle QA Failed:\n{response}")
        
    print(f"Subtitle QA passed.", file=sys.stderr)
    return {"passed": True, "reason": response}
