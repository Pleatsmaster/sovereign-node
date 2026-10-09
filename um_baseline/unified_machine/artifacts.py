from __future__ import annotations

import hashlib
from pathlib import Path


class ArtifactStore:
    """Content-addressed artifact store.

    Artifacts are stored under <state_dir>/artifacts/<sha256><suffix>.
    put_text returns the hex digest, which is what the ledger records.
    """

    def __init__(self, state_dir: Path):
        self.root = Path(state_dir) / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)

    def put_text(self, text: str, suffix: str = ".md") -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        path = self.root / f"{digest}{suffix}"
        if not path.exists():
            path.write_text(text, encoding="utf-8")
        return digest

    def put_bytes(self, data: bytes, suffix: str = ".bin") -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / f"{digest}{suffix}"
        if not path.exists():
            path.write_bytes(data)
        return digest

    def get_text(self, digest: str, suffix: str = ".md") -> str:
        path = self.root / f"{digest}{suffix}"
        return path.read_text(encoding="utf-8")

    def path_for(self, digest: str, suffix: str = ".md") -> Path:
        return self.root / f"{digest}{suffix}"
