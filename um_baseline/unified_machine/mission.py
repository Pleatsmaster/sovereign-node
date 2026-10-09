from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .artifacts import ArtifactStore
from .ledger import Ledger
from .policy import PolicyError, WorkPolicy
from .residue import (
    RETRIEVAL_LIMIT,
    ResidueStore,
    admit_candidate,
    build_proposal_packet,
    default_residue_dir,
    parse_proposal,
)
from .types import Action, MissionResult, ToolResult
from .workers import Worker
from .workspace import Workspace


class MissionRunner:
    def __init__(
        self,
        *,
        objective: str,
        repo: Path,
        state_dir: Path,
        worker: Worker,
        budget: int = 12,
        acceptance: Optional[List[List[str]]] = None,
        mission_id: Optional[str] = None,
        memory_query: Optional[str] = None,
        residue_dir: Optional[Path] = None,
        worker_label: Optional[str] = None,
    ):
        self.objective = objective.strip()
        if not self.objective:
            raise ValueError("objective cannot be empty")
        self.repo = Path(repo).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.worker = worker
        self.budget = int(budget)
        if self.budget < 1:
            raise ValueError("budget must be >=1")
        self.acceptance = acceptance or []
        self.mission_id = mission_id or uuid.uuid4().hex[:16]
        self.memory_query = memory_query or self._memory_terms(objective)
        self.ledger = Ledger(self.state_dir)
        self.artifacts = ArtifactStore(self.state_dir)
        self.workspace = Workspace(self.repo)
        self.policy = WorkPolicy()
        self.history: List[Dict[str, Any]] = []
        self.failed_acts = 0
        # R_t: the organism's persistent acquired residue. Read at packet
        # construction, written once per completed mission through the
        # mechanical admission rule. Lives outside any worktree so it
        # survives lineage checkouts and organism generations.
        self.worker_label = worker_label or "unknown"
        self.residue_store = ResidueStore(
            Path(residue_dir) if residue_dir else default_residue_dir()
        )
        # Snapshot once: the store cannot change mid-mission (admission only
        # happens post-mission), so packet content and ledger provenance agree.
        self.injected_residues = self.residue_store.active(limit=RETRIEVAL_LIMIT)

    @staticmethod
    def _memory_terms(text: str) -> str:
        words = [w.strip(".,:;!?()[]{}\"'") for w in text.split()]
        words = [w for w in words if len(w) >= 4]
        return " OR ".join(words[:8]) if words else ""

    def _packet(self, step: int) -> Dict[str, Any]:
        memories = self.ledger.search_memory(self.memory_query, limit=6)
        return {
            "mission": {
                "id": self.mission_id,
                "objective": self.objective,
                "repo": str(self.repo),
                "repo_commit": self.workspace.git_commit(),
                "step": step,
                "budget": self.budget,
                "remaining": self.budget - step + 1,
                "acceptance_commands": self.acceptance,
            },
            "relevant_memory": memories,
            # Organism residue: reusable lessons the machine acquired from
            # doing prior work, admitted through the mechanical admission
            # rule. This is persistent organism state, causally available to
            # the worker -- not observer metadata.
            "organism_residue": [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "condition": r["condition"],
                    "content": r["content"],
                    "scope": r.get("scope", ""),
                    "derived_from": r.get("derived_from", []),
                }
                for r in self.injected_residues
            ],
            "history": self.history[-8:],
            "workspace_rules": {
                "cwd": str(self.repo),
                "one_act_at_a_time": True,
                "ordinary_work_first": True,
                "persistent_self_change": False,
                "run_argv_no_shell": True,
            },
        }

    def _execute(self, action: Action) -> ToolResult:
        k, a = action.kind, action.args
        if k == "read_file":
            return self.workspace.read_file(a["path"], a.get("start_line", 1), a.get("end_line"))
        if k == "search":
            return self.workspace.search(a["query"], a.get("path", "."), int(a.get("limit", 80)))
        if k == "run":
            return self.workspace.run(a["argv"], a.get("timeout"))
        if k == "write_file":
            return self.workspace.write_file(a["path"], a["content"])
        if k == "apply_patch":
            return self.workspace.apply_patch(a["patch"])
        if k == "git_diff":
            diff = self.workspace.git_diff()
            return ToolResult(True, "captured git diff", {"diff": diff})
        if k == "status":
            return self.workspace.status()
        if k == "stop":
            return ToolResult(True, "worker stopped mission", {"status": a.get("status", "complete"), "reason": a.get("reason", "")})
        return ToolResult(False, f"unsupported action: {k}", {})

    def _residue_opportunity(
        self,
        *,
        status: str,
        steps: int,
        stop_reason: str,
        acceptance_ok: Optional[bool],
        report: str,
    ) -> None:
        """One worker proposal per completed mission, admitted mechanically.

        Every failure path is contained: a worker that cannot propose, a
        declined/unparseable proposal, or a rejected candidate is recorded in
        the mission ledger and the mission result stands unchanged.
        """
        try:
            packet = build_proposal_packet(
                mission_id=self.mission_id,
                objective=self.objective,
                status=status,
                steps=steps,
                failed_acts=self.failed_acts,
                acceptance_ok=acceptance_ok,
                stop_reason=stop_reason,
                report=report,
                ledger=self.ledger,
                store=self.residue_store,
            )
            raw = self.worker.propose_residue(packet)
        except NotImplementedError:
            self.ledger.append(
                "residue_skipped",
                {"mission_id": self.mission_id, "reason": "worker_unsupported"},
            )
            return
        except Exception as exc:  # noqa: BLE001 - proposal must not break completion
            self.ledger.append(
                "residue_error",
                {
                    "mission_id": self.mission_id,
                    "stage": "propose",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return
        verdict, payload = parse_proposal(raw)
        if verdict == "none":
            self.ledger.append(
                "residue_declined",
                {"mission_id": self.mission_id, "reason": payload},
            )
            return
        if verdict == "unparseable":
            self.ledger.append(
                "residue_rejected",
                {
                    "mission_id": self.mission_id,
                    "reason": "unparseable proposal",
                    "excerpt": payload[:500],
                },
            )
            return
        try:
            ok, result = admit_candidate(
                self.residue_store,
                payload,
                mission_id=self.mission_id,
                ledger=self.ledger,
                worker_label=self.worker_label,
            )
        except Exception as exc:  # noqa: BLE001 - admission must not break completion
            self.ledger.append(
                "residue_error",
                {
                    "mission_id": self.mission_id,
                    "stage": "admit",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return
        if ok:
            self.ledger.append(
                "residue_admitted",
                {
                    "mission_id": self.mission_id,
                    "residue_id": result["id"],
                    "kind": result["kind"],
                },
            )
        else:
            self.ledger.append(
                "residue_rejected",
                {"mission_id": self.mission_id, "reason": result},
            )

    def _run_acceptance(self) -> List[Dict[str, Any]]:
        results = []
        for argv in self.acceptance:
            r = self.workspace.run(argv)
            results.append(r.to_obj())
        return results

    def _has_evidence(self) -> bool:
        """True if the repo shows any observable change (modified or
        untracked files). git status --short covers both; git diff alone
        would miss newly created files."""
        try:
            out = self.workspace.status().data.get("stdout", "")
        except Exception:
            return True  # fail open: never block completion on an inspection error
        return bool(out.strip())

    def run(self) -> MissionResult:
        started = time.time()
        start_commit = self.workspace.git_commit()
        self.ledger.append(
            "mission_started",
            {
                "mission_id": self.mission_id,
                "objective": self.objective,
                "repo": str(self.repo),
                "repo_commit": start_commit,
                "budget": self.budget,
                "acceptance": self.acceptance,
                "residue_injected": [r["id"] for r in self.injected_residues],
            },
        )
        status = "budget_exhausted"
        stop_reason = ""
        steps = 0

        for step in range(1, self.budget + 1):
            steps = step
            packet = self._packet(step)
            # --- U2B exemplar retrieval: packet-only injection, Gate B0 ---------------
            # One causal edge: packet construction -> deterministic exemplar lookup.
            # On match, exactly one key ("acquired_procedure") is added to the packet
            # before worker.next_action. On NO_EXEMPLAR, or when UM_EXEMPLAR_RETRIEVAL=0,
            # the packet is returned untouched (byte-identical to U2A). The retrieved
            # procedure is never executed directly; the worker authors the next action.
            # Failure-contained: any error -> unmodified packet. No ledger/repo/
            # acceptance/artifact writes from this path.
            if os.environ.get("UM_EXEMPLAR_RETRIEVAL", "1") == "1":
                try:
                    from .exemplars import retrieve, retrieve_into_packet, signature_from_packet
                    sig = signature_from_packet(packet)
                    retrieval, instance = retrieve(packet)
                    packet = retrieve_into_packet(packet)
                    log_line = {
                        "mission_id": self.mission_id,
                        "step": step,
                        "retrieval": retrieval,
                        "signature": sig,
                    }
                    if instance is not None:
                        log_line["exemplar_id"] = instance["id"]
                    with open(self.state_dir / "retrieval_log.jsonl", "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(log_line, sort_keys=True) + "\n")
                except Exception as exc:  # noqa: BLE001 -- retrieval must never break a mission
                    print(f"[u2b-retrieval] suppressed: {type(exc).__name__}: {exc}", file=sys.stderr)
            try:
                action = self.worker.next_action(packet)
                self.policy.validate(action)
            except Exception as exc:
                self.failed_acts += 1
                record = {"step": step, "action_error": f"{type(exc).__name__}: {exc}"}
                self.history.append(record)
                self.ledger.append("act_rejected", {"mission_id": self.mission_id, **record})
                continue

            before = self.workspace.git_commit()
            result = self._execute(action)
            after = self.workspace.git_commit()
            if not result.ok:
                self.failed_acts += 1
            record = {
                "step": step,
                "action": action.to_obj(),
                "result": result.to_obj(),
                "repo_commit_before": before,
                "repo_commit_after": after,
            }
            self.history.append(record)
            self.ledger.append("act_completed", {"mission_id": self.mission_id, **record})

            if action.kind == "stop":
                claimed = action.args.get("status", "complete")
                stop_reason = action.args.get("reason", "")
                # Evidence gate (Z): a worker-declared "complete" is not
                # honored when the mission produced no observable evidence
                # and no acceptance commands are configured to verify it.
                # The mission continues with its remaining budget instead
                # of stopping early on an unverified claim.
                if claimed == "complete" and not self.acceptance and not self._has_evidence():
                    stop_reason = (stop_reason + " " if stop_reason else "") + (
                        "[evidence-gate] 'complete' claimed with no repo changes "
                        "and no acceptance configured; stop not honored"
                    )
                    self.history.append(
                        {"step": step, "evidence_gate": "stop-complete rejected: no observable evidence"}
                    )
                    continue
                status = claimed
                break

        acceptance_results = self._run_acceptance() if self.acceptance else []
        acceptance_ok = all(x["ok"] for x in acceptance_results) if acceptance_results else None
        if status == "complete" and acceptance_ok is False:
            status = "acceptance_failed"

        final_packet = {
            "mission": {
                "id": self.mission_id,
                "objective": self.objective,
                "repo": str(self.repo),
                "start_commit": start_commit,
                "end_commit": self.workspace.git_commit(),
                "status": status,
                "stop_reason": stop_reason,
                "steps": steps,
                "failed_acts": self.failed_acts,
                "acceptance_results": acceptance_results,
                "git_diff": self.workspace.git_diff(),
            },
            "history": self.history,
        }
        try:
            report = self.worker.report(final_packet)
        except Exception as exc:
            report = f"Report generation failed: {type(exc).__name__}: {exc}\n\n" + json.dumps(final_packet, indent=2)
        report_artifact = self.artifacts.put_text(report, suffix=".md")
        wall = time.time() - started
        final_hash = self.ledger.append(
            "mission_finished",
            {
                "mission_id": self.mission_id,
                "status": status,
                "steps": steps,
                "failed_acts": self.failed_acts,
                "wall_seconds": wall,
                "acceptance_ok": acceptance_ok,
                "report_artifact": report_artifact,
                "repo_commit": self.workspace.git_commit(),
            },
        )
        memory_text = (
            f"Objective: {self.objective}\nStatus: {status}\nFailed acts: {self.failed_acts}\n"
            f"Acceptance: {acceptance_ok}\nReport:\n{report}"
        )
        self.ledger.remember(
            "mission",
            memory_text,
            {"mission_id": self.mission_id, "status": status, "repo": str(self.repo)},
        )
        self.ledger.verify()
        # Phase I closed loop: one post-mission residue opportunity. The worker
        # may propose at most one reusable lesson from this mission's evidence;
        # it becomes persistent organism state only through the mechanical
        # admission rule. This never alters the mission result.
        self._residue_opportunity(
            status=status,
            steps=steps,
            stop_reason=stop_reason,
            acceptance_ok=acceptance_ok,
            report=report,
        )
        result = MissionResult(
            mission_id=self.mission_id,
            status=status,
            objective=self.objective,
            repo=str(self.repo),
            repo_commit=self.workspace.git_commit(),
            steps=steps,
            failed_acts=self.failed_acts,
            wall_seconds=wall,
            report=report,
            final_event_hash=final_hash,
        )
        # --- U2A developmental hook: passive organ, Gate A -------------------
        # Exactly one causal edge: mission terminal event -> D0 recompute.
        # This runs after full termination (ledger verified, result built).
        # on_mission_finished is failure-contained and can never affect the
        # returned result; there is no path back into the worker packet,
        # action selection, acceptance, or artifact of any mission.
        # UM_DEVELOPMENTAL_HOOK=0 disables the hook (noninterference tests).
        if os.environ.get("UM_DEVELOPMENTAL_HOOK", "1") == "1":
            try:
                from .developmental import on_mission_finished
                on_mission_finished(self, result)
            except Exception as exc:  # noqa: BLE001 -- import-time containment only
                print(f"[u2a-hook] suppressed import failure: {exc}", file=sys.stderr)
        return result
