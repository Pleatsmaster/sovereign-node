#!/usr/bin/env python3
"""Worker capability task v2.2: rule-grounded classification + field-assignment check.

SUCCESSOR to frozen v2.1 (preserved at sha256
9dfc4cc05e839661a6ab88a0d5f156e1524081dc21cf6ef2edc0850130509ada).

v2.2 repairs two demonstrated checker defects (Stephan, 2026-10-05):
- conflicting assignments in one segment no longer false-pass (first-match-wins
  replaced by collect-all + CONFLICT sentinel);
- permitted plain prose ("control outcome is FAILURE") no longer false-rejects
  (defined per-record answer shape requested in the prompt + tolerant fallback).

Prompt change vs v2.1 (RECORDED): the defined answer shape block. Classification
rule, records, and scoring unchanged: PASS iff 3/3 labels AND 12/12 fields.

FREEZE RECORD (2026-10-04, operator directive):
- capability_task_v1.py is FROZEN UNCHANGED at
  sha256 43fc6e6b00cd2cab3ff3b43452e70d26df6fab838e928d5f560e8f3979d768ad
  (record of the rejected model's run; direct-comparison baseline).

The model supplies candidate content only; the deterministic checker owns
extraction, normalization, and the verdict. No executor activation until a
candidate clears this gate.
"""
import hashlib
import json
import os
import re
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llama_cpp import Llama

MODEL = sys.argv[1]

MAX_TOKENS = 512
TEMPLATE_SLACK = 256  # bound on chat-template overhead around the frozen prompt
CTX_ROUND = 64
HASH_CHUNK = 1 << 20  # 1 MiB — incremental GGUF hashing, never whole-file in RAM


def sha256_file(path):
    """Incremental SHA-256; avoids reading the entire file into RAM."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def peak_rss_mib():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)

VOCAB = ["NO_EFFECT", "RELEVANT_IMPROVEMENT", "RELEVANT_REGRESSION"]
ACTION_CLASSES = ["SEARCH_CONTENT", "EXECUTE_TOOL", "WRITE_ARTIFACT"]
OUTCOMES = ["SUCCESS", "FAILURE"]

# --- frozen task definition (identical to capability_task_v1.py) ---
RECORDS = [
    dict(id="p01", control_action="SEARCH_CONTENT", treatment_action="EXECUTE_TOOL",
         control_outcome="FAILURE", treatment_outcome="SUCCESS"),
    dict(id="p02", control_action="EXECUTE_TOOL", treatment_action="SEARCH_CONTENT",
         control_outcome="SUCCESS", treatment_outcome="FAILURE"),
    dict(id="p03", control_action="WRITE_ARTIFACT", treatment_action="WRITE_ARTIFACT",
         control_outcome="SUCCESS", treatment_outcome="FAILURE"),
]


def rule_answer(r):
    differs = r["treatment_action"] != r["control_action"]
    better = r["treatment_outcome"] == "SUCCESS" and r["control_outcome"] == "FAILURE"
    worse = r["treatment_outcome"] == "FAILURE" and r["control_outcome"] == "SUCCESS"
    if differs and better:
        return "RELEVANT_IMPROVEMENT"
    if differs and worse:
        return "RELEVANT_REGRESSION"
    return "NO_EFFECT"


EXPECTED = {r["id"]: rule_answer(r) for r in RECORDS}
EXPECTED_FIELDS = {
    r["id"]: {"control_action": r["control_action"],
              "treatment_action": r["treatment_action"],
              "control_outcome": r["control_outcome"],
              "treatment_outcome": r["treatment_outcome"]}
    for r in RECORDS
}

PROMPT = """You are given three pivot records from a fictional assessment apparatus. Each record compares a control run and a treatment run.

Classification rule (apply exactly as stated):
- RELEVANT_IMPROVEMENT if the treatment action class DIFFERS from the control action class AND the treatment outcome is better (treatment SUCCESS where control was FAILURE)
- RELEVANT_REGRESSION if the treatment action class DIFFERS from the control action class AND the treatment outcome is worse (treatment FAILURE where control was SUCCESS)
- NO_EFFECT in all other cases

Records:
p01: control action class=SEARCH_CONTENT outcome=FAILURE | treatment action class=EXECUTE_TOOL outcome=SUCCESS
p02: control action class=EXECUTE_TOOL outcome=SUCCESS | treatment action class=SEARCH_CONTENT outcome=FAILURE
p03: control action class=WRITE_ARTIFACT outcome=SUCCESS | treatment action class=WRITE_ARTIFACT outcome=FAILURE

Give one classification for each record (p01, p02, p03). For each record, state the control and treatment action classes and the control and treatment outcomes you are basing your classification on. You may explain your reasoning in plain prose.

Present each record's answer in this shape, one block per record:

p01: <RELEVANT_IMPROVEMENT | RELEVANT_REGRESSION | NO_EFFECT>
control action class: <SEARCH_CONTENT | EXECUTE_TOOL | WRITE_ARTIFACT>
control outcome: <SUCCESS | FAILURE>
treatment action class: <SEARCH_CONTENT | EXECUTE_TOOL | WRITE_ARTIFACT>
treatment outcome: <SUCCESS | FAILURE>

(Repeat the block for p02 and p03, separated by a blank line. Plain-prose reasoning may appear outside the blocks.)"""
# --- end frozen task definition ---
#
# v2.2 SUCCESSOR RECORD (2026-10-05, per Stephan's directive; v2.1 preserved
# frozen at sha256 9dfc4cc05e839661a6ab88a0d5f156e1524081dc21cf6ef2edc0850130509ada).
# Prompt change (RECORDED): the defined per-record answer shape above is new.
# The classification rule, records, and scoring are unchanged.
# Checker repairs:
#   (a) Conflicting assignments are rejected: extract_field_values collects
#       EVERY assignment of (role, kind) in the segment; two distinct values
#       -> CONFLICT, which never equals the expected value (no more
#       first-match-wins false passes).
#   (b) Permitted prose is supported: the inline pattern accepts "is" as a
#       separator ("control outcome is FAILURE"), and the defined
#       line-anchored shape is parsed as the primary path, with the tolerant
#       inline patterns as fallback (no more false rejections of plain prose).
#
# v2.1 INSTRUMENTATION REPAIRS ONLY (2026-10-05, per Stephan's directive;
# frozen task/checker semantics unchanged; task definition above byte-identical
# to v2). Repairs:
#   (a) GGUF hashing is incremental (1 MiB chunks) — no longer reads the whole
#       file into RAM merely to compute SHA-256.
#   (b) n_ctx is the smallest context fitting the complete frozen prompt plus
#       output budget: tokenized with the candidate model's own tokenizer
#       (probe instance), n_ctx = prompt_tokens + TEMPLATE_SLACK + MAX_TOKENS,
#       rounded up to CTX_ROUND. Replaces the fixed n_ctx=4096.
#   (c) Chat template: create_chat_completion applies the model file's own
#       embedded template (the Qwen GGUF's Qwen template when that model is
#       used); the applied template identity is recorded best-effort.
#   (d) Peak RSS (ru_maxrss) recorded alongside timing, per the Qwen admission
#       conditions. No task, prompt, extraction, or scoring change.

NEG_RE = re.compile(
    r"\b(not|isn't|wasn't|weren't|never|no)\s+"
    r"(NO_EFFECT|RELEVANT_IMPROVEMENT|RELEVANT_REGRESSION)\b",
    re.IGNORECASE,
)
LABEL_RE = re.compile(r"\b(p01|p02|p03)\b", re.IGNORECASE)


def segment(text):
    labels = [(m.group(1).lower(), m.start()) for m in LABEL_RE.finditer(text)]
    segs = {}
    for i, (rid, start) in enumerate(labels):
        end = labels[i + 1][1] if i + 1 < len(labels) else len(text)
        segs[rid] = text[start:end]
    for rid in ("p01", "p02", "p03"):
        segs.setdefault(rid, "")
    return segs


def extract_label(seg):
    seg = NEG_RE.sub("", seg)
    found = {t for t in VOCAB if re.search(r"\b" + t + r"\b", seg)}
    return found.pop() if len(found) == 1 else None


CONFLICT = "<CONFLICT>"  # sentinel: assigned two different values; never matches


def extract_field_values(seg, role, kind):
    """Every distinct value assigned to (role, kind) anywhere in the segment."""
    vocab = ACTION_CLASSES if kind == "action" else OUTCOMES
    noun = r"action\s+class" if kind == "action" else r"outcome"
    alt = "|".join(vocab)
    patterns = [
        # defined structure, line-anchored: "control action class: SEARCH_CONTENT"
        re.compile(rf"^{role}\s+{noun}\s*:\s*\b({alt})\b",
                   re.IGNORECASE | re.MULTILINE),
        # inline prose: "control action class=SEARCH_CONTENT",
        # "control outcome is FAILURE", "treatment outcome: SUCCESS"
        re.compile(rf"\b{role}\s+{noun}\s*(?:is\s+)?[(\s=:,]*\b({alt})\b",
                   re.IGNORECASE),
    ]
    found = set()
    for pat in patterns:
        for m in pat.finditer(seg):
            found.add(m.group(1).upper())
    return found


def extract_field(seg, role, kind):
    """role: control|treatment; kind: action|outcome.

    Returns the single assigned value, CONFLICT if the segment assigns two
    different values to the same field, None if it assigns none. Conflicts
    and absences both fail the field against the expected value."""
    vals = extract_field_values(seg, role, kind)
    if len(vals) == 1:
        return vals.pop()
    if len(vals) > 1:
        return CONFLICT
    return None


def extract_fields(seg):
    return {
        "control_action": extract_field(seg, "control", "action"),
        "treatment_action": extract_field(seg, "treatment", "action"),
        "control_outcome": extract_field(seg, "control", "outcome"),
        "treatment_outcome": extract_field(seg, "treatment", "outcome"),
    }


def main():
    # Optional: --check-only <results-json> validates the checker on a saved run
    # without inference (used to validate v2 against v1's recorded output).
    if len(sys.argv) > 2 and sys.argv[2] == "--check-only":
        saved = json.load(open(sys.argv[3]))
        text = saved["raw_output"]
        segs = segment(text)
        labels = {rid: extract_label(segs[rid]) for rid in ("p01", "p02", "p03")}
        fields = {rid: extract_fields(segs[rid]) for rid in ("p01", "p02", "p03")}
        label_match = {rid: labels[rid] == EXPECTED[rid] for rid in labels}
        field_match = {
            rid: {k: fields[rid][k] == EXPECTED_FIELDS[rid][k] for k in fields[rid]}
            for rid in fields
        }
        n_fields = sum(v for r in field_match.values() for v in r.values())
        print(json.dumps({
            "mode": "check-only",
            "source": sys.argv[3],
            "labels": labels,
            "label_match": label_match,
            "fields": fields,
            "field_match": field_match,
            "fields_correct": f"{n_fields}/12",
            "passed": all(label_match.values()) and n_fields == 12,
        }, indent=1))
        return

    t0 = time.time()
    # Probe instance (small context) exists only to tokenize the frozen prompt
    # with the candidate model's own tokenizer. n_ctx for the inference
    # instance is then the smallest context fitting the complete frozen prompt
    # (plus chat-template slack) plus the output budget.
    probe = Llama(model_path=MODEL, n_ctx=512, n_threads=2, verbose=False)
    n_prompt_tokens = len(probe.tokenize(PROMPT.encode("utf-8")))
    required = n_prompt_tokens + TEMPLATE_SLACK + MAX_TOKENS
    n_ctx = ((required + CTX_ROUND - 1) // CTX_ROUND) * CTX_ROUND
    probe.close()
    del probe
    llm = Llama(model_path=MODEL, n_ctx=n_ctx, n_threads=2, verbose=False)
    try:
        chat_template = llm.metadata.get("tokenizer.chat_template", None)
    except Exception:
        chat_template = None
    load_s = time.time() - t0
    t1 = time.time()
    resp = llm.create_chat_completion(
        messages=[{"role": "user", "content": PROMPT}],
        temperature=0.0,
        max_tokens=MAX_TOKENS,
    )
    elapsed = time.time() - t1
    text = resp["choices"][0]["message"]["content"]
    toks = resp["usage"]["completion_tokens"]

    segs = segment(text)
    labels = {rid: extract_label(segs[rid]) for rid in ("p01", "p02", "p03")}
    fields = {rid: extract_fields(segs[rid]) for rid in ("p01", "p02", "p03")}
    label_match = {rid: labels[rid] == EXPECTED[rid] for rid in labels}
    field_match = {
        rid: {k: fields[rid][k] == EXPECTED_FIELDS[rid][k] for k in fields[rid]}
        for rid in fields
    }
    n_fields = sum(v for r in field_match.values() for v in r.values())
    passed = all(label_match.values()) and n_fields == 12

    print(json.dumps({
        "task": "rule-grounded-classification-v2",
        "instrument": "capability_task_v2_2",
        "model": MODEL,
        "model_sha256": sha256_file(MODEL),
        "settings": {"n_ctx": n_ctx, "n_ctx_basis": {
            "prompt_tokens": n_prompt_tokens,
            "template_slack": TEMPLATE_SLACK,
            "max_tokens": MAX_TOKENS,
            "rounding": CTX_ROUND},
            "n_threads": 2, "temperature": 0.0,
            "max_tokens": MAX_TOKENS,
            "chat_template": chat_template or "model-embedded (create_chat_completion)"},
        "expected_labels": EXPECTED,
        "expected_fields": EXPECTED_FIELDS,
        "extracted_labels": labels,
        "label_match": label_match,
        "extracted_fields": fields,
        "field_match": field_match,
        "fields_correct": f"{n_fields}/12",
        "passed": passed,
        "elapsed_s": round(elapsed, 1),
        "tok_per_s": round(toks / elapsed, 1) if elapsed else 0,
        "load_s": round(load_s, 1),
        "peak_rss_mib": peak_rss_mib(),
        "raw_output": text,
    }, indent=1))


if __name__ == "__main__":
    main()
