"""Shared artifact store + fetch_evidence narrow interface (W4).

Organism-owned content-addressed store at <life0>/state/shared_artifacts/.
Write-if-absent, immutable once written. Only the organism executor publishes;
cells never write shared state directly (v0.3 section 3.5).

fetch_evidence enforces, in order: digest format validation (closes the
crafted-digest path traversal found in the read-only survey), authorization
against the exchange record, read, content-hash re-verification. Any failure
refuses closed: no bytes, no partial disclosure.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Dict, List, Optional

SHARED_DIRNAME = "shared_artifacts"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceError(Exception):
    pass


def shared_root(life0_dir: str) -> str:
    return os.path.join(life0_dir, "state", SHARED_DIRNAME)


def publish(life0_dir: str, data: bytes) -> str:
    """Organism act: store bytes, return content hash. Write-if-absent."""
    digest = hashlib.sha256(data).hexdigest()
    root = shared_root(life0_dir)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, digest)
    # Resolve-then-check: the digest is format-validated hex, so the resolved
    # path cannot escape root; verify anyway.
    real = os.path.realpath(path)
    if os.path.dirname(real) != os.path.realpath(root):
        raise EvidenceError("artifact path escapes shared store")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return digest


def fetch_evidence(life0_dir: str, cell_id: str, digest: str,
                   exchange_records: List[Dict]) -> bytes:
    """Cell read path. Returns artifact bytes iff authorized; else raises."""
    if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
        raise EvidenceError("refused: digest is not a 64-hex content hash")
    authorized = any(
        x.get("decision") == "permitted"
        and x.get("received_by") == cell_id
        and x.get("artifact_hash") == digest
        for x in exchange_records
    )
    if not authorized:
        raise EvidenceError("refused: no permitted exchange authorizes "
                            "this cell to receive this artifact")
    path = os.path.join(shared_root(life0_dir), digest)
    real = os.path.realpath(path)
    if os.path.dirname(real) != os.path.realpath(shared_root(life0_dir)):
        raise EvidenceError("refused: artifact path escapes shared store")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        raise EvidenceError("refused: artifact not present")
    if hashlib.sha256(data).hexdigest() != digest:
        raise EvidenceError("refused: content hash mismatch (corrupted)")
    return data
