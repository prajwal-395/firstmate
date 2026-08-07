import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

from vision_model import get_model

try:
    from visual_qa_prompts import PROMPTS
except ImportError:
    PROMPTS = {
        "transition": "Analyze the transition in these video frames. Describe any visual effects, cuts, or blending used.",
        "vfx": "Analyze the visual effects in these video frames. Describe what effects are present and their quality.",
        "color": "Analyze the color grading and lighting in these video frames. Are there any issues with exposure or saturation?",
        "subtitle": "Check for subtitles in these video frames. Are they readable, correctly positioned, and spelled correctly?",
        "audio": "Listen to the audio in the video segment (if applicable) and describe any issues.",
        "general": "{question}"
    }

# Import deterministic checks
import render_qa


def run_analysis(
    video_path: str,
    question: str,
    checks: List[str]
) -> Dict[str, Any]:
    """Run video segment analysis using vision model and deterministic checks."""
    results: Dict[str, Any] = {
        "video": video_path,
        "checks_requested": checks,
        "model_analysis": {},
        "deterministic_checks": {}
    }

    # Run deterministic checks
    # Run relevant checks based on the check types requested
    if "color" in checks or "general" in checks:
        results["deterministic_checks"]["color_histogram"] = _qa_result_to_dict(
            render_qa.analyze_color_histogram(video_path, sample_count=5)
        )
    
    if "audio" in checks or "general" in checks:
        results["deterministic_checks"]["lufs"] = _qa_result_to_dict(
            render_qa.measure_lufs(video_path)
        )
        
    if "general" in checks or "transition" in checks or "vfx" in checks:
        results["deterministic_checks"]["black_frames"] = _qa_result_to_dict(
            render_qa.detect_black_frames(video_path)
        )
        results["deterministic_checks"]["freeze_frames"] = _qa_result_to_dict(
            render_qa.detect_freeze_frames(video_path)
        )

    # Run vision model analysis
    model = get_model()
    for check in checks:
        if check not in PROMPTS and check != "general":
            continue
            
        prompt_template = PROMPTS.get(check, PROMPTS["general"])
        prompt = prompt_template.format(question=question) if "{question}" in prompt_template else prompt_template
        
        # If it's a general check, ensure the question is included if not in template
        if check == "general" and "{question}" not in prompt_template:
            prompt = f"{prompt}\nQuestion: {question}"

        try:
            model_response = model.analyze_video(
                video_path, 
                prompt
            )
            results["model_analysis"][check] = model_response
        except Exception as e:
            results["model_analysis"][check] = {"error": str(e)}

    return results


def _qa_result_to_dict(qa_result: Any) -> Dict[str, Any]:
    """Convert RenderQAResult to dictionary."""
    if hasattr(qa_result, '__dataclass_fields__'):
        return {
            "metric": qa_result.metric,
            "passed": qa_result.passed,
            "value": qa_result.value,
            "threshold": qa_result.threshold,
            "severity": qa_result.severity,
            "detail": qa_result.detail
        }
    return str(qa_result)


def main():
    parser = argparse.ArgumentParser(description="Video Segment Analyzer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--video", required=True, help="Path to video file")
    analyze_parser.add_argument("--question", default="", help="Question or prompt for analysis")
    analyze_parser.add_argument("--checks", default="general", help="Comma-separated list of checks (transition,vfx,color,subtitle,audio,general)")
    analyze_parser.add_argument("--cleanup", action="store_true", help="Delete video file after analysis")

    args = parser.parse_args()

    if args.command == "analyze":
        video_path = args.video
        checks = [c.strip() for c in args.checks.split(",")]
        
        try:
            if not os.path.exists(video_path):
                print(json.dumps({"error": f"Video file not found: {video_path}"}))
                sys.exit(1)
                
            results = run_analysis(
                video_path=video_path,
                question=args.question,
                checks=checks
            )
            print(json.dumps(results, indent=2))
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            sys.exit(1)
        finally:
            if args.cleanup and os.path.exists(video_path):
                try:
                    os.remove(video_path)
                except Exception as e:
                    print(json.dumps({"warning": f"Failed to cleanup {video_path}: {e}"}), file=sys.stderr)


if __name__ == "__main__":
    main()
