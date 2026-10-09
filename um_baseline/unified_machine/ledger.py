from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Ledger:
    """Append-only, hash-chained event ledger.

    This is deliberately small enough to audit. If SOVEREIGN is available, this
    can be replaced by an adapter without changing the mission loop.
    """

    GENESIS = "0" * 64

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "ledger.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                parent_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);

            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            """
        )
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(text, content='memory', content_rowid='id')"
            )
            self.conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
                  INSERT INTO memory_fts(rowid, text) VALUES (new.id, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
                  INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.id, old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
                  INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.id, old.text);
                  INSERT INTO memory_fts(rowid, text) VALUES (new.id, new.text);
                END;
                """
            )
        except sqlite3.OperationalError:
            # FTS5 is optional; fallback search uses LIKE.
            pass
        self.conn.commit()

    def last_hash(self) -> str:
        row = self.conn.execute(
            "SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else self.GENESIS

    def append(self, kind: str, payload: Dict[str, Any]) -> str:
        ts = time.time()
        parent = self.last_hash()
        body = {
            "ts": ts,
            "kind": kind,
            "payload": payload,
            "parent_hash": parent,
        }
        event_hash = sha256_text(canonical_json(body))
        with self.conn:
            self.conn.execute(
                "INSERT INTO events(ts, kind, payload_json, parent_hash, event_hash) VALUES(?,?,?,?,?)",
                (ts, kind, canonical_json(payload), parent, event_hash),
            )
        return event_hash

    def events(self, *, kind: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if kind:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE kind=? ORDER BY seq DESC LIMIT ?", (kind, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in reversed(rows):
            out.append(
                {
                    "seq": r["seq"],
                    "ts": r["ts"],
                    "kind": r["kind"],
                    "payload": json.loads(r["payload_json"]),
                    "parent_hash": r["parent_hash"],
                    "event_hash": r["event_hash"],
                }
            )
        return out

    def verify(self) -> None:
        parent = self.GENESIS
        for row in self.conn.execute("SELECT * FROM events ORDER BY seq"):
            if row["parent_hash"] != parent:
                raise RuntimeError(f"ledger parent mismatch at seq {row['seq']}")
            payload = json.loads(row["payload_json"])
            body = {
                "ts": row["ts"],
                "kind": row["kind"],
                "payload": payload,
                "parent_hash": row["parent_hash"],
            }
            expected = sha256_text(canonical_json(body))
            if expected != row["event_hash"]:
                raise RuntimeError(f"ledger hash mismatch at seq {row['seq']}")
            parent = row["event_hash"]

    def remember(self, kind: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO memory(ts, kind, text, metadata_json) VALUES(?,?,?,?)",
                (time.time(), kind, text, canonical_json(metadata or {})),
            )

    def search_memory(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        rows = []
        if query:
            try:
                rows = self.conn.execute(
                    """
                    SELECT m.* FROM memory_fts f
                    JOIN memory m ON m.id=f.rowid
                    WHERE memory_fts MATCH ?
                    ORDER BY bm25(memory_fts)
                    LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            if query:
                pattern = "%" + "%".join(query.split()) + "%"
                rows = self.conn.execute(
                    "SELECT * FROM memory WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                    (pattern, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM memory ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "kind": r["kind"],
                "text": r["text"],
                "metadata": json.loads(r["metadata_json"]),
            }
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()
