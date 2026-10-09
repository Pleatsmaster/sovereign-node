from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .types import ToolResult


class Workspace:
    """Confined real-work tools for one repository/worktree."""

    def __init__(self, root: Path, *, command_timeout: int = 120, max_output: int = 30000):
        self.root = Path(root).resolve()
        if not self.root.exists():
            raise FileNotFoundError(self.root)
        self.command_timeout = command_timeout
        self.max_output = max_output

    def _path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise PermissionError(f"path escapes workspace: {relative}")
        return candidate

    def read_file(self, path: str, start_line: int = 1, end_line: Optional[int] = None) -> ToolResult:
        p = self._path(path)
        if not p.is_file():
            return ToolResult(False, f"not a file: {path}", {})
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line))
        end = len(lines) if end_line is None else min(len(lines), int(end_line))
        text = "\n".join(f"{i+1}: {lines[i]}" for i in range(start - 1, end))
        if len(text) > self.max_output:
            text = text[: self.max_output] + "\n...[truncated]"
        return ToolResult(True, f"read {path}:{start}-{end}", {"text": text, "line_count": len(lines)})

    def search(self, query: str, path: str = ".", limit: int = 80) -> ToolResult:
        base = self._path(path)
        if not base.exists():
            return ToolResult(False, f"path not found: {path}", {})
        hits: List[Dict[str, Any]] = []
        files: Iterable[Path]
        if base.is_file():
            files = [base]
        else:
            files = base.rglob("*")
        for p in files:
            if len(hits) >= limit:
                break
            if not p.is_file() or ".git" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if query.lower() in line.lower():
                    hits.append(
                        {
                            "path": str(p.relative_to(self.root)),
                            "line": i,
                            "text": line[:500],
                        }
                    )
                    if len(hits) >= limit:
                        break
        return ToolResult(True, f"{len(hits)} hits for {query!r}", {"hits": hits})

    def run(self, argv: List[str], timeout: Optional[int] = None) -> ToolResult:
        if not argv or not all(isinstance(x, str) for x in argv):
            return ToolResult(False, "argv must be a non-empty list of strings", {})
        started = time.time()
        try:
            cp = subprocess.run(
                argv,
                cwd=self.root,
                text=True,
                capture_output=True,
                timeout=timeout or self.command_timeout,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(
                False,
                f"command timed out after {timeout or self.command_timeout}s",
                {"argv": argv, "stdout": (exc.stdout or "")[-self.max_output :], "stderr": (exc.stderr or "")[-self.max_output :]},
            )
        except OSError as exc:
            # A worker's bad argv (missing binary, bad cwd) is a failed act,
            # not a mission-ending crash: the worker sees the error and can retry.
            return ToolResult(
                False,
                f"command failed to start: {exc}",
                {"argv": argv, "stdout": "", "stderr": str(exc), "seconds": time.time() - started},
            )
        elapsed = time.time() - started
        stdout = cp.stdout[-self.max_output :]
        stderr = cp.stderr[-self.max_output :]
        return ToolResult(
            cp.returncode == 0,
            f"exit {cp.returncode} in {elapsed:.2f}s",
            {"argv": argv, "returncode": cp.returncode, "stdout": stdout, "stderr": stderr, "seconds": elapsed},
        )

    def write_file(self, path: str, content: str) -> ToolResult:
        p = self._path(path)
        if ".git" in p.parts:
            return ToolResult(False, "direct writes under .git are forbidden", {})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolResult(True, f"wrote {path}", {"bytes": len(content.encode('utf-8'))})

    def apply_patch(self, patch: str) -> ToolResult:
        try:
            cp = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=self.root,
                input=patch,
                text=True,
                capture_output=True,
                timeout=self.command_timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, "git apply timed out", {})
        return ToolResult(
            cp.returncode == 0,
            "patch applied" if cp.returncode == 0 else "patch rejected",
            {"returncode": cp.returncode, "stdout": cp.stdout[-self.max_output :], "stderr": cp.stderr[-self.max_output :]},
        )

    def git_commit(self) -> Optional[str]:
        cp = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True, capture_output=True
        )
        return cp.stdout.strip() if cp.returncode == 0 else None

    def git_diff(self) -> str:
        cp = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--"], cwd=self.root, text=True, capture_output=True
        )
        return cp.stdout[-self.max_output :] if cp.returncode == 0 else ""

    def status(self) -> ToolResult:
        return self.run(["git", "status", "--short"])
