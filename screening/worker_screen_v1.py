#!/usr/bin/env python3
"""Qwen2.5-3B four-stage worker screening.

Stage 1: Backend verification (model loads, chat template works)
Stage 2: v2.3 competence gate (3/3 labels, 12/12 fields)
Stage 3: Report-generation diagnostic (A10 task, 6-criterion evaluator)
Stage 4: Failure-to-correction check (specific correction for documented failure)

Usage: worker_screen_v1.py <model_path>
Output: JSON results to stdout, detailed logs to files.

Each stage must pass before the next begins. A stage failure terminates
screening with the appropriate verdict.
"""

import json
import sys
import time
from pathlib import Path


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def stage1_backend(model_path):
    """Stage 1: Verify model loads and chat interface works."""
    log("Stage 1: Backend verification")
    from llama_cpp import Llama
    
    llm = Llama(model_path=model_path, n_ctx=2048, n_threads=4, verbose=False)
    try:
        # Check chat template
        template = llm.metadata.get("tokenizer.chat_template", None)
        if not template:
            return False, "No chat template in model metadata"
        log(f"  Chat template present: yes")
        
        # Smoke test
        resp = llm.create_chat_completion(
            messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
            temperature=0.0, max_tokens=10,
        )
        output = resp["choices"][0]["message"]["content"]
        log(f"  Smoke test output: {output[:50]}")
        
        if len(output.strip()) == 0:
            return False, "Empty smoke test output"
        
        return True, "Backend OK"
    finally:
        llm.close()


def stage2_gate(model_path):
    """Stage 2: Run v2.3 competence gate."""
    log("Stage 2: v2.3 competence gate")
    import subprocess
    
    # The v2.3 gate script should be in the same directory
    gate_script = Path(__file__).parent / "capability_task_v2_3.py"
    if not gate_script.exists():
        return False, f"Gate script not found: {gate_script}"
    
    result = subprocess.run(
        [sys.executable, str(gate_script), model_path],
        capture_output=True, text=True, timeout=1800,
    )
    
    # Parse output for pass/fail
    try:
        # v2.3 outputs JSON or text with results
        output = result.stdout
        # Look for pass indicators
        if "3/3" in output and "12/12" in output:
            return True, "Gate PASS (3/3 labels, 12/12 fields)"
        else:
            return False, f"Gate FAIL: {output[:200]}"
    except Exception as e:
        return False, f"Gate error: {e}"


def stage3_report(model_path):
    """Stage 3: Report-generation diagnostic (A10 task)."""
    log("Stage 3: Report-generation diagnostic")
    from llama_cpp import Llama
    
    # A10 prompt (simplified - full prompt would be embedded)
    # For now, use a representative report-generation task
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
    
    llm = Llama(model_path=model_path, n_ctx=2048, n_threads=4, verbose=False)
    try:
        resp = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=512,
        )
        output = resp["choices"][0]["message"]["content"]
        
        # Save raw output
        Path("stage3_output.txt").write_text(output)
        
        # Check: not an echo (doesn't start with prompt text)
        is_echo = output.strip().startswith("You are a research")
        if is_echo:
            return False, "Output is echo, not report"
        
        # Check: contains classifications
        has_invalid = output.lower().count("invalid") >= 4
        has_outcome = "r3-e" in output.lower()
        
        # Mechanical scoring (simplified 6-criterion)
        scores = {
            "classifications": has_invalid,
            "outcome": has_outcome,
            "not_echo": not is_echo,
        }
        passed = sum(scores.values())
        
        log(f"  Scores: {scores} ({passed}/3)")
        
        # Require at least 2/3 for this diagnostic
        if passed >= 2:
            return True, f"Report diagnostic PASS ({passed}/3)"
        else:
            return False, f"Report diagnostic FAIL ({passed}/3)"
    finally:
        llm.close()


def stage4_correction(model_path):
    """Stage 4: Failure-to-correction check."""
    log("Stage 4: Failure-to-correction check")
    from llama_cpp import Llama
    
    prompt = """A worker was asked to write a structured report from evidence.
Instead, it echoed the prompt text verbatim without producing any report structure.

Propose ONE specific procedural correction that would address this exact failure.
The correction must:
- Reference the specific failure (echoing/continuation, not generic input problems)
- Propose a concrete behavioral change (not just "be careful" or "verify input")

Write 2-3 sentences."""
    
    llm = Llama(model_path=model_path, n_ctx=2048, n_threads=4, verbose=False)
    try:
        resp = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=256,
        )
        output = resp["choices"][0]["message"]["content"]
        
        Path("stage4_output.txt").write_text(output)
        log(f"  Correction: {output[:200]}...")
        
        output_lower = output.lower()
        
        # Mechanical checks:
        # 1. References the specific failure
        mentions_failure = any(t in output_lower for t in 
                              ["echo", "continuation", "reproduc", "repeat"])
        
        # 2. Proposes concrete change (not generic)
        generic_only = all(t in output_lower for t in ["verify", "check"]) and \
                      not any(t in output_lower for t in 
                             ["template", "format", "structure", "section", 
                              "heading", "field", "slot", "begin with"])
        has_concrete = not generic_only and len(output.strip()) > 50
        
        if mentions_failure and has_concrete:
            return True, "Correction addresses failure specifically"
        elif not mentions_failure:
            return False, "Correction does not reference echoing failure"
        else:
            return False, "Correction is generic, not specific"
    finally:
        llm.close()


def main():
    if len(sys.argv) != 2:
        print("Usage: worker_screen_v1.py <model_path>", file=sys.stderr)
        sys.exit(2)
    
    model_path = sys.argv[1]
    results = {
        "model": model_path,
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stages": {},
    }
    
    stages = [
        ("stage1_backend", stage1_backend),
        ("stage2_gate", stage2_gate),
        ("stage3_report", stage3_report),
        ("stage4_correction", stage4_correction),
    ]
    
    for stage_name, stage_fn in stages:
        log(f"\n{'='*50}")
        try:
            passed, message = stage_fn(model_path)
            results["stages"][stage_name] = {
                "passed": passed,
                "message": message,
            }
            log(f"{stage_name}: {'PASS' if passed else 'FAIL'} - {message}")
            
            if not passed:
                log(f"\nScreening TERMINATED at {stage_name}")
                break
        except Exception as e:
            results["stages"][stage_name] = {
                "passed": False,
                "message": f"Exception: {e}",
            }
            log(f"{stage_name}: ERROR - {e}")
            break
    
    results["completed"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results["overall"] = all(
        s.get("passed", False) for s in results["stages"].values()
    ) and len(results["stages"]) == 4
    
    print("\n" + json.dumps(results, indent=1))
    
    # Write results file
    Path("screening_results.json").write_text(json.dumps(results, indent=1))
    
    sys.exit(0 if results["overall"] else 1)


if __name__ == "__main__":
    main()
