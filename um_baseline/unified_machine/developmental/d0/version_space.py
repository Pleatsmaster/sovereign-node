"""D0 deterministic version space over the frozen acquisition-v1 vocabulary.

Hypotheses are trigger rules: conjunctions of <= k_max predicates => INTERVENE.
The version space keeps every rule consistent with evidence; it never picks
a winner. NULL (never intervene) is a valid survivor.

Consistency rule (v0, one-sided overbreadth elimination):
  H is ELIMINATED iff exists evidence (s,a,e) with H fires at (s,a)
  AND 'ACQUIRE_COMPLETE_SOURCE_SET' in e.
Rationale and limitation are frozen in the vocabulary JSON: v0 evidence is
passive-observational and supports redundancy-elimination only.
"""

from __future__ import annotations

import itertools

from . import vocabulary as V


def initial_space(vocab: dict) -> list[dict]:
    return V.enumerate_hypotheses(vocab)


def is_complete_effect(evidence: dict, vocab: dict) -> bool:
    token = vocab["consistency_rule"]["effect_complete_token"]
    return token in (evidence.get("observed_effect") or [])


def consistent(hyp: dict, evidence: dict, vocab: dict) -> bool:
    """One-sided: only firing-at-complete-acquisition eliminates."""
    if V.fires(hyp, evidence["state_before"], vocab) and is_complete_effect(evidence, vocab):
        return False
    return True


def canonical_evidence_order(evidence_list: list[dict]) -> list[dict]:
    """Frozen processing order: sorted by evidence_id. Input order is irrelevant."""
    return sorted(evidence_list, key=lambda e: e["evidence_id"])


def apply_evidence(
    hyps: list[dict], evidence_list: list[dict], vocab: dict
) -> tuple[list[dict], list[dict]]:
    """Apply evidence in canonical order.

    Returns (survivors, eliminated) where eliminated entries are
    {"hid": str, "eliminated_by": evidence_id}. Survivor order stays canonical.
    """
    survivors = list(hyps)
    eliminated: list[dict] = []
    for ev in canonical_evidence_order(evidence_list):
        still: list[dict] = []
        for h in survivors:
            if consistent(h, ev, vocab):
                still.append(h)
            else:
                eliminated.append({"hid": h["hid"], "eliminated_by": ev["evidence_id"]})
        survivors = still
    return survivors, eliminated


# --------------------------------------------------------------------------
# disagreement -> experience request target
# --------------------------------------------------------------------------

def complete_observed_in_region(
    region_pids: list[str], evidence_list: list[dict], vocab: dict
) -> bool:
    for ev in evidence_list:
        if V.region_satisfied(region_pids, ev["state_before"], vocab) and is_complete_effect(ev, vocab):
            return True
    return False


def region_observed_in_history(
    region_pids: list[str], evidence_list: list[dict], vocab: dict
) -> bool:
    for ev in evidence_list:
        if V.region_satisfied(region_pids, ev["state_before"], vocab):
            return True
    return False


def eligible_regions(
    survivors: list[dict], evidence_list: list[dict], vocab: dict
) -> list[dict]:
    """All eligible disagreement regions, ranked.

    Eligible: >=1 survivor FIRES (determined) and >=1 survivor QUIET
    (determined) in the region, AND no evidence item observed a complete
    effect inside the region (missing discriminating experience).
    Ranking (frozen request_ranking): ordinary_work_observed regions first,
    then canonical region order. Each entry carries the first FIRES / first
    QUIET hypothesis in canonical hypothesis order.

    Two regions are never eligible, by design:
      - R:TRUE (the whole space): not an actionable experience target; a
        capsule spec / ordinary-work observation cannot target "everywhere".
      - unsatisfiable regions (contain an exclusive predicate pair): no
        state can realize them, so no experience can be gathered there.
        (Contradictory hypotheses therefore survive forever but are inert:
        never FIRES-determined in any eligible region.)
    """
    regions = V.enumerate_regions(vocab)
    pairs = vocab["_exclusive_pairs"]
    eligible_observed: list[dict] = []
    eligible_unknown: list[dict] = []
    for region in regions:
        r_pids = region["pids"]
        if not r_pids:
            continue  # R:TRUE excluded: not an actionable experience target
        satisfiable = True
        for a, b in itertools.combinations(sorted(r_pids), 2):
            if frozenset((a, b)) in pairs:
                satisfiable = False
                break
        if not satisfiable:
            continue
        h_fires = None
        h_quiet = None
        for h in survivors:  # canonical hypothesis order
            pred = V.prediction_in_region(h, r_pids, vocab)
            if pred == "FIRES" and h_fires is None:
                h_fires = h
            elif pred == "QUIET" and h_quiet is None:
                h_quiet = h
            if h_fires is not None and h_quiet is not None:
                break
        if h_fires is None or h_quiet is None:
            continue
        if complete_observed_in_region(r_pids, evidence_list, vocab):
            continue  # discriminating experience already exists here
        entry = {
            "region": region,
            "h_fires": h_fires,
            "h_quiet": h_quiet,
        }
        if region_observed_in_history(r_pids, evidence_list, vocab):
            eligible_observed.append(entry)
        else:
            eligible_unknown.append(entry)
    return eligible_observed + eligible_unknown


def find_disagreement(
    survivors: list[dict], evidence_list: list[dict], vocab: dict
) -> dict | None:
    """First eligible disagreement region under the frozen ranking.

    Returns None when no disagreement exists.
    """
    ranked = eligible_regions(survivors, evidence_list, vocab)
    if not ranked:
        return None
    best = ranked[0]
    region = best["region"]
    observed = region_observed_in_history(region["pids"], evidence_list, vocab)
    return {
        "region": region,
        "region_readable": V.region_readable(region["pids"], vocab),
        "hypothesis_ids": [best["h_fires"]["hid"], best["h_quiet"]["hid"]],
        "predictions": {
            best["h_fires"]["hid"]: "INTERVENE",
            best["h_quiet"]["hid"]: "QUIET",
        },
        "ordinary_work": "observed" if observed else "unknown",
    }
