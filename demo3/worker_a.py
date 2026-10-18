#!/usr/bin/env python3
"""DEMO_3 worker A — AMENDMENT A1 (2026-10-18).

Answers the frozen DEMO_3 questions with llama-cpp-python in a
FRESH interpreter. Run 39146126987 failed Phase 3B because the
driver pip-installed llama-cpp-python successfully (rc=0) and
then could not import it from its own long-running process;
a fresh interpreter started after the install sees the package
normally. Prompts arrive as a JSON list on stdin (built by the
driver's single build_prompt template); inference parameters
match the original in-process worker exactly (n_ctx=2048,
temperature=0.0, max_tokens=64). Answers + package version are
written as JSON to the file named by argv[2].

argv: worker_a.py <model_path> <output_json_path>
"""
import json
import sys


def main():
    prompts = json.load(sys.stdin)
    import llama_cpp
    llm = llama_cpp.Llama(model_path=sys.argv[1], n_ctx=2048,
                          verbose=False)
    answers = []
    for prompt in prompts:
        r = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=64)
        answers.append(r["choices"][0]["message"]["content"])
    payload = {"version": getattr(llama_cpp, "__version__",
                                  "unknown"),
               "answers": answers}
    with open(sys.argv[2], "w") as f:
        json.dump(payload, f)


if __name__ == "__main__":
    main()
