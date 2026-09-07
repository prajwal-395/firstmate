#!/usr/bin/env python3
"""Step 6.02 post-bridge: one verdict from the deterministic and LLM halves.

The work is `resolve_validation`, which takes the merged answer and
returns the final verdict.  `main()` owns the process: stdin and
stdout.  See AGENTS.md 3.
"""
import sys
import json


def resolve_validation(data: dict) -> dict:
    """Combine the deterministic checks and the model's reading into one status.

    The deterministic half is decisive: anything but `pass` there is a
    fail regardless of what the model said.
    """
    det = data.get("deterministic_validation", {})
    llm = data.get("validation_result", {})

    det_status = det.get("status")
    llm_status = llm.get("status")

    if det_status != "pass":
        status = "fail"
        if not det.get("summary"):
            det["summary"] = "Deterministic validation failed to produce a valid status"
    elif llm_status == "fail":
        status = "fail"
    elif llm_status == "undetermined":
        status = "undetermined"
    elif llm_status != "pass":
        status = "fail"
    else:
        status = "pass"
        
    distribution_ready = (status == "pass")

    checks = dict(det.get("checks", {}))
    checks.update(llm.get("checks", {}))
    
    all_issues = list(det.get("all_issues", []))
    all_issues.extend(llm.get("all_issues", []))
    
    summary = det.get("summary", "")
    if llm.get("summary"):
        summary += " | LLM: " + llm.get("summary")

    final_result = {
        "status": status,
        "checks": checks,
        "all_issues": all_issues,
        "distribution_ready": distribution_ready,
        "critical_checks_passed": det.get("critical_checks_passed", False),
        "summary": summary,
        "qa_report_path": det.get("qa_report_path", ""),
        "qa_report": det.get("qa_report", [])
    }
    
    # ONE verdict leaves this node, and it is `validation_result`.
    # `final_qa_decision` was declared beside it and echoed from
    # `data.get("final_qa_decision", "")` - a key no edge routed, no
    # handoff asked the model for and no default supplied, so it was the
    # empty string on every run and no reader existed. Declared, not
    # produced, and unread: the whole shape is deleted rather than given
    # a value nobody would consult. `run_pipeline.py` reads
    # `validation_result.status`, which is the real verdict.
    return {"validation_result": final_result}


def main():
    json.dump(resolve_validation(json.load(sys.stdin)), sys.stdout, indent=2)


if __name__ == "__main__":
    main()
