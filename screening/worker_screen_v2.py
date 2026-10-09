#!/usr/bin/env python3
"""Qwen2.5-3B four-stage worker screening (v2 - hardened).

Stage 1: Backend verification (model loads, chat interface works)
Stage 2: v2.3 competence gate (3/3 labels, 12/12 fields)
Stage 3: Report-generation diagnostic (A10 task, structured output)
Stage 4: Failure-to-correction check (specific correction for documented failure)

Usage: worker_screen_v2.py <model_path>
Output: JSON results to stdout, detailed logs to files.

Each stage must pass before the next begins. Failures are logged with
tracebacks; a stage failure terminates screening with the error recorded.
"""

import json
import sys
import time
import traceback
from pathlib import Path


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def stage1_backend(model_path):
    """Stage 1: Verify model loads and chat interface works."""
    log("Stage 1: Backend verification")
    log(f"  Model path: {model_path}")
    log(f"  File exists: {Path(model_path).exists()}")
    if Path(model_path).exists():
        log(f"  File size: {Path(model_path).stat().st_size / 1e9:.2f} GB")

    llm = None
    try:
        from llama_cpp import Llama
        log("  llama_cpp imported OK")

        log("  Loading model (n_ctx=512, n_threads=2)...")
        llm = Llama(model_path=model_path, n_ctx=512, n_threads=2, verbose=False)
        log("  Model loaded OK")

        # Check metadata (gracefully)
        try:
            meta = llm.metadata
            log(f"  Metadata keys: {list(meta.keys())[:10]}")
            # Try common chat template locations
            template = None
            for key in ["tokenizer.chat_template", "general.chat_template"]:
                if key in meta:
                    template = meta[key]
                    break
            if template:
                log(f"  Chat template found (length {len(template)})")
            else:
                log("  WARNING: No chat template in metadata, will try inference anyway")
        except Exception as e:
            log(f"  WARNING: Metadata check failed: {e}, continuing")

        # Smoke test
        log("  Running smoke test...")
        resp = llm.create_chat_completion(
            messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
            temperature=0.0, max_tokens=10,
        )
        output = resp["choices"][0]["message"]["content"]
        log(f"  Smoke test output: {output[:100]!r}")

        if len(output.strip()) == 0:
            return False, "Empty smoke test output"

        return True, "Backend OK"
    except Exception as e:
        tb = traceback.format_exc()
        log(f"  EXCEPTION in stage 1:\n{tb}")
        Path("stage1_error.txt").write_text(tb)
        return False, f"Stage 1 exception: {e}"
    finally:
        if llm is not None:
            try:
                llm.close()
            except Exception:
                pass


def stage2_gate(model_path):
    """Stage 2: Run v2.3 competence gate via subprocess."""
    log("Stage 2: v2.3 competence gate")
    import subprocess

    gate_script = Path(__file__).parent / "capability_task_v2_3.py"
    log(f"  Gate script: {gate_script}")
    log(f"  Exists: {gate_script.exists()}")
    if not gate_script.exists():
        return False, f"Gate script not found: {gate_script}"

    log("  Running gate (timeout 1800s)...")
    try:
        result = subprocess.run(
            [sys.executable, str(gate_script), model_path],
            capture_output=True, text=True, timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return False, "Gate timed out after 1800s"
    except Exception as e:
        tb = traceback.format_exc()
        Path("stage2_error.txt").write_text(tb)
        return False, f"Gate subprocess error: {e}"

    log(f"  Gate exit code: {result.returncode}")
    log(f"  Gate stdout (first 500 chars): {result.stdout[:500]!r}")
    if result.stderr:
        log(f"  Gate stderr (first 500 chars): {result.stderr[:500]!r}")
    Path("stage2_stdout.txt").write_text(result.stdout)
    Path("stage2_stderr.txt").write_text(result.stderr)

    output = result.stdout
    # v2.3 gate: look for pass indicators
    if "3/3" in output and "12/12" in output:
        return True, "Gate PASS (3/3 labels, 12/12 fields)"
    elif result.returncode == 0:
        # Exit 0 but no clear indicators - check more carefully
        log("  WARNING: exit 0 but no 3/3+12/12 markers")
        return False, f"Gate unclear: exit 0 but markers missing. Output: {output[:300]}"
    else:
        return False, f"Gate FAIL (exit {result.returncode}): {output[:300]}"


def stage3_report(model_path):
    """Stage 3: Report-generation diagnostic."""
    log("Stage 3: Report-generation diagnostic")

    llm = None
    try:
        from llama_cpp import Llama
        llm = Llama(model_path=model_path, n_ctx=1024, n_threads=2, verbose=False)
        log("  Model loaded for stage 3")

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

        log("  Running inference...")
        resp = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=512,
        )
        output = resp["choices"][0]["message"]["content"]
        log(f"  Output length: {len(output)} chars")
        log(f"  Output preview: {output[:300]!r}")

        Path("stage3_output.txt").write_text(output)

        is_echo = output.strip().startswith("You are a research")
        has_invalid = output.lower().count("invalid") >= 4
        has_outcome = "r3-e" in output.lower()

        scores = {"not_echo": not is_echo, "classifications": has_invalid, "outcome": has_outcome}
        passed = sum(scores.values())
        log(f"  Scores: {scores} ({passed}/3)")

        if passed >= 2:
            return True, f"Report diagnostic PASS ({passed}/3)"
        else:
            return False, f"Report diagnostic FAIL ({passed}/3)"
    except Exception as e:
        tb = traceback.format_exc()
        log(f"  EXCEPTION in stage 3:\n{tb}")
        Path("stage3_error.txt").write_text(tb)
        return False, f"Stage 3 exception: {e}"
    finally:
        if llm is not None:
            try:
                llm.close()
            except Exception:
                pass


def stage4_correction(model_path):
    """Stage 4: Failure-to-correction check."""
    log("Stage 4: Failure-to-correction check")

    llm = None
    try:
        from llama_cpp import Llama
        llm = Llama(model_path=model_path, n_ctx=1024, n_threads=2, verbose=False)
        log("  Model loaded for stage 4")

        prompt = """A worker was asked to write a structured report from evidence.
Instead, it echoed the prompt text verbatim without producing any report structure.

Propose ONE specific procedural correction that would address this exact failure.
The correction must:
- Reference the specific failure (echoing/continuation, not generic input problems)
- Propose a concrete behavioral change (not just "be careful" or "verify input")

Write 2-3 sentences."""

        log("  Running inference...")
        resp = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=256,
        )
        output = resp["choices"][0]["message"]["content"]
        log(f"  Output: {output[:400]!r}")

        Path("stage4_output.txt").write_text(output)
        output_lower = output.lower()

        mentions_failure = any(t in output_lower for t in ["echo", "continuation", "reproduc", "repeat"])
        generic_only = all(t in output_lower for t in ["verify", "check"]) and not any(
            t in output_lower for t in ["template", "format", "structure", "section",
                                        "heading", "field", "slot", "begin with"])
        has_concrete = not generic_only and len(output.strip()) > 50

        log(f"  mentions_failure={mentions_failure}, has_concrete={has_concrete}")

        if mentions_failure and has_concrete:
            return True, "Correction addresses failure specifically"
        elif not mentions_failure:
            return False, "Correction does not reference echoing failure"
        else:
            return False, "Correction is generic, not specific"
    except Exception as e:
        tb = traceback.format_exc()
        log(f"  EXCEPTION in stage 4:\n{tb}")
        Path("stage4_error.txt").write_text(tb)
        return False, f"Stage 4 exception: {e}"
    finally:
        if llm is not None:
            try:
                llm.close()
            except Exception:
                pass


def main():
    if len(sys.argv) != 2:
        print("Usage: worker_screen_v2.py <model_path>", file=sys.stderr)
        sys.exit(2)

    model_path = sys.argv[1]
    log(f"Starting 4-stage screening")
    log(f"Model: {model_path}")
    log(f"Python: {sys.version.split()[0]}")

    results = {
        "model": model_path,
        "script_version": "v2-hardened",
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
        log(f"ENTERING {stage_name}")
        try:
            passed, message = stage_fn(model_path)
            results["stages"][stage_name] = {"passed": passed, "message": message}
            log(f"{stage_name}: {'PASS' if passed else 'FAIL'} - {message}")
            if not passed:
                log(f"Screening TERMINATED at {stage_name} (stage failed)")
                break
        except Exception as e:
            tb = traceback.format_exc()
            log(f"{stage_name}: UNCAUGHT EXCEPTION\n{tb}")
            results["stages"][stage_name] = {"passed": False, "message": f"Uncaught: {e}"}
            Path(f"{stage_name}_uncaught.txt").write_text(tb)
            break

    results["completed"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results["overall"] = (
        all(s.get("passed", False) for s in results["stages"].values())
        and len(results["stages"]) == 4
    )

    log(f"\n{'='*50}")
    log(f"Screening complete: overall={'PASS' if results['overall'] else 'FAIL'}")
    log(f"Stages completed: {len(results['stages'])}/4")

    print("\n" + json.dumps(results, indent=1))
    Path("screening_results.json").write_text(json.dumps(results, indent=1))

    sys.exit(0 if results["overall"] else 1)


if __name__ == "__main__":
    main()
