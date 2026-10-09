#!/usr/bin/env python3
"""Stage 3: Report-generation diagnostic (standalone).
Usage: stage3_report.py <model_path>
"""
import sys
import json
from pathlib import Path
from llama_cpp import Llama

def main():
    model_path = sys.argv[1]
    print(f"Loading model: {model_path}", flush=True)
    llm = Llama(model_path=model_path, n_ctx=1024, n_threads=2, verbose=False)
    print("Model loaded", flush=True)
    
    prompt = """You are a research assistant. Read the evidence and write a structured report.

Evidence:
- Cell F_IN_kIN: layerA="invalid", Y=0
- Cell F_IN_kANTI: layerA="invalid", Y=0
- Cell F_ANTI_kIN: layerA="invalid", Y=0
- Cell F_ANTI_kANTI: layerA="invalid", Y=0
- Overall outcome: R3-e

Write a report with:
1. Classification for each cell (valid or invalid)
2. Overall outcome string
3. Evidence citation for each classification

Do not echo the evidence. Write only the report."""
    
    print("Running inference...", flush=True)
    resp = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0, max_tokens=512,
    )
    output = resp["choices"][0]["message"]["content"]
    print(f"Output length: {len(output)}", flush=True)
    Path("stage3_output.txt").write_text(output)
    
    is_echo = output.strip().startswith("You are a research")
    has_invalid = output.lower().count("invalid") >= 4
    has_outcome = "r3-e" in output.lower()
    scores = {"not_echo": not is_echo, "classifications": has_invalid, "outcome": has_outcome}
    passed = sum(scores.values())
    print(f"Scores: {scores} ({passed}/3)", flush=True)
    
    result = {"passed": passed >= 2, "scores": scores, "output_preview": output[:200]}
    Path("stage3_result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    llm.close()
    sys.exit(0 if result["passed"] else 1)

if __name__ == "__main__":
    main()
