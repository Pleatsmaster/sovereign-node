#!/usr/bin/env python3
"""Stage 4: Failure-to-correction check (standalone).
Usage: stage4_correction.py <model_path>
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
    
    prompt = """A worker was asked to write a structured report from evidence.
Instead, it echoed the prompt text verbatim without producing any report structure.

Propose ONE specific procedural correction that would address this exact failure.
The correction must:
- Reference the specific failure (echoing/continuation, not generic input problems)
- Propose a concrete behavioral change (not just "be careful" or "verify input")

Write 2-3 sentences."""
    
    print("Running inference...", flush=True)
    resp = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0, max_tokens=256,
    )
    output = resp["choices"][0]["message"]["content"]
    print(f"Output: {output[:400]}", flush=True)
    Path("stage4_output.txt").write_text(output)
    
    output_lower = output.lower()
    mentions_failure = any(t in output_lower for t in ["echo", "continuation", "reproduc", "repeat"])
    generic_only = all(t in output_lower for t in ["verify", "check"]) and not any(
        t in output_lower for t in ["template", "format", "structure", "section",
                                    "heading", "field", "slot", "begin with"])
    has_concrete = not generic_only and len(output.strip()) > 50
    
    passed = mentions_failure and has_concrete
    result = {"passed": passed, "mentions_failure": mentions_failure, 
              "has_concrete": has_concrete, "output_preview": output[:200]}
    Path("stage4_result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    llm.close()
    sys.exit(0 if passed else 1)

if __name__ == "__main__":
    main()
