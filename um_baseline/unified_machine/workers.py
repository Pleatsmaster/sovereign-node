from __future__ import annotations

import json
import os
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .types import Action, WorkerSpec
from .residue import PROPOSAL_CONTRACT


ACTION_CONTRACT = r'''
Return exactly one JSON object and nothing else:
{
  "kind": "read_file|search|run|write_file|apply_patch|git_diff|status|stop",
  "args": {...},
  "rationale": "short reason"
}
Tool argument forms:
- read_file: {"path":"...","start_line":1,"end_line":200}
- search: {"query":"...","path":".","limit":80}
- run: {"argv":["python","-m","pytest","..."]}
- write_file: {"path":"...","content":"..."}
- apply_patch: {"patch":"unified diff"}
- git_diff: {}
- status: {}
- stop: {"status":"complete|blocked|defer","reason":"..."}
Do not claim completion until the objective's acceptance evidence has actually been obtained.
Prefer ordinary work. Do not propose persistent changes to the research machine during an ordinary mission.
'''.strip()


class Worker(ABC):
    @abstractmethod
    def next_action(self, packet: Dict[str, Any]) -> Action:
        raise NotImplementedError

    @abstractmethod
    def report(self, packet: Dict[str, Any]) -> str:
        raise NotImplementedError

    def propose_residue(self, packet: Dict[str, Any]) -> str:
        """One post-mission reusable-lesson proposal.

        Returns raw worker text; the organism parses it (REUSE_CANDIDATE
        object or NO_REUSABLE_LESSON) and admits it only through the
        mechanical admission rule. Workers that cannot propose raise
        NotImplementedError and the organism records residue_skipped.
        """
        raise NotImplementedError


class CommandWorker(Worker):
    """Provider-independent JSON-over-stdin worker.

    The command receives {"mode":"act"|"report", "packet":...} and must emit
    JSON for act mode or plain text for report mode.
    """

    def __init__(self, command: List[str]):
        if not command:
            raise ValueError("command worker requires a command")
        self.command = command

    def _call(self, mode: str, packet: Dict[str, Any]) -> str:
        cp = subprocess.run(
            self.command,
            input=json.dumps({"mode": mode, "packet": packet}, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=300,
            env=os.environ.copy(),
        )
        if cp.returncode != 0:
            raise RuntimeError(f"worker command failed: {cp.stderr[-4000:]}")
        return cp.stdout.strip()

    def next_action(self, packet: Dict[str, Any]) -> Action:
        return Action.from_obj(json.loads(self._call("act", packet)))

    def report(self, packet: Dict[str, Any]) -> str:
        return self._call("report", packet)

    def propose_residue(self, packet: Dict[str, Any]) -> str:
        # The contract and evidence travel inside the packet (single source
        # of truth lives in unified_machine.residue); the shim only formats.
        return self._call("residue_proposal", packet)


class OpenAIWorker(Worker):
    """Optional OpenAI Responses API worker.

    The package stays provider-independent; this adapter is only loaded when
    selected. OPENAI_API_KEY is read by the official SDK.
    """

    def __init__(self, model: str):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("install the optional 'openai' dependency to use OpenAIWorker") from exc
        self.client = OpenAI()
        self.model = model

    def _text(self, prompt: str) -> str:
        response = self.client.responses.create(model=self.model, input=prompt)
        return response.output_text.strip()

    def next_action(self, packet: Dict[str, Any]) -> Action:
        prompt = (
            "You are the cognitive worker inside a persistent autonomous research machine.\n"
            "Choose exactly one next act. You do not own persistent identity; the ledger does.\n\n"
            + ACTION_CONTRACT
            + "\n\nCURRENT PACKET:\n"
            + json.dumps(packet, ensure_ascii=False, indent=2)
        )
        text = self._text(prompt)
        # tolerate fenced JSON from weaker models while keeping the contract strict.
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].lstrip()
        return Action.from_obj(json.loads(text))

    def report(self, packet: Dict[str, Any]) -> str:
        prompt = (
            "Write a concise research/work report from the packet below. Distinguish observed evidence from inference. "
            "Do not invent success. Include final status, evidence, changes made, unresolved blockers, and reusable lessons.\n\n"
            + json.dumps(packet, ensure_ascii=False, indent=2)
        )
        return self._text(prompt)

    def propose_residue(self, packet: Dict[str, Any]) -> str:
        prompt = (
            PROPOSAL_CONTRACT
            + "\n\nPROPOSAL PACKET:\n"
            + json.dumps(
                {k: packet.get(k) for k in ("mission", "outcome", "active_residues", "report", "ledger_events")},
                ensure_ascii=False,
                indent=2,
            )
        )
        return self._text(prompt)


def build_worker(spec: WorkerSpec) -> Worker:
    if spec.kind == "command":
        if not spec.command:
            raise ValueError("command worker requires command")
        return CommandWorker(spec.command)
    if spec.kind == "openai":
        if not spec.model:
            raise ValueError("openai worker requires model")
        return OpenAIWorker(spec.model)
    raise ValueError(spec.kind)
