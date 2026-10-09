from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Action:
    kind: str
    args: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    @classmethod
    def from_obj(cls, obj: Dict[str, Any]) -> "Action":
        if not isinstance(obj, dict):
            raise ValueError("action must be a JSON object")
        kind = obj.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ValueError("action.kind must be a non-empty string")
        args = obj.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("action.args must be an object")
        rationale = obj.get("rationale", "")
        if not isinstance(rationale, str):
            rationale = str(rationale)
        return cls(kind=kind, args=args, rationale=rationale)

    def to_obj(self) -> Dict[str, Any]:
        return {"kind": self.kind, "args": self.args, "rationale": self.rationale}


@dataclass
class WorkerSpec:
    kind: str  # "openai" | "command"
    model: Optional[str] = None
    command: Optional[List[str]] = None

    @classmethod
    def from_obj(cls, obj: Dict[str, Any]) -> "WorkerSpec":
        if not isinstance(obj, dict):
            raise ValueError("worker spec must be an object")
        kind = obj.get("kind")
        if kind not in ("openai", "command"):
            raise ValueError("worker kind must be 'openai' or 'command'")
        model = obj.get("model")
        command = obj.get("command")
        if command is not None:
            if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
                raise ValueError("worker command must be a list of strings")
        return cls(kind=kind, model=model, command=command)

    def to_obj(self) -> Dict[str, Any]:
        return {"kind": self.kind, "model": self.model, "command": self.command}


@dataclass
class ToolResult:
    ok: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_obj(self) -> Dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "data": self.data}


@dataclass
class MissionResult:
    mission_id: str
    status: str
    objective: str
    repo: str
    repo_commit: Optional[str]
    steps: int
    failed_acts: int
    wall_seconds: float
    report: str
    final_event_hash: str

    def to_obj(self) -> Dict[str, Any]:
        return asdict(self)
