#!/usr/bin/env python3
"""Write screening summary from stage result files."""
import json
from pathlib import Path

summary = {}

# Stage 2: check gate output
p = Path("stage2_output.log")
if p.exists():
    text = p.read_text()
    summary["stage2_gate"] = "PASS" if ("3/3" in text and "12/12" in text) else "FAIL"
else:
    summary["stage2_gate"] = "NOT_RUN"

# Stage 3
p = Path("stage3_result.json")
if p.exists():
    summary["stage3_report"] = "PASS" if json.loads(p.read_text())["passed"] else "FAIL"
else:
    summary["stage3_report"] = "NOT_RUN"

# Stage 4
p = Path("stage4_result.json")
if p.exists():
    summary["stage4_correction"] = "PASS" if json.loads(p.read_text())["passed"] else "FAIL"
else:
    summary["stage4_correction"] = "NOT_RUN"

print(json.dumps(summary, indent=1))
Path("screening_summary.json").write_text(json.dumps(summary, indent=1))
