from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MemoryUnavailable(RuntimeError):
    """Retrieval failed; callers must not continue with stale or partial context."""


@dataclass(frozen=True)
class MemorySettings:
    enabled: bool = True
    top_k: int = 8
    max_context_chars: int = 8000
    backend: str = "sqlite"

    @classmethod
    def from_env(cls) -> "MemorySettings":
        enabled = os.getenv("MEDIAFORGE_RAG_ENABLED", "true").strip().lower()
        if enabled not in {"true", "false", "1", "0"}:
            raise ValueError("MEDIAFORGE_RAG_ENABLED must be true or false")
        top_k = int(os.getenv("MEDIAFORGE_RAG_TOP_K", "8"))
        max_chars = int(os.getenv("MEDIAFORGE_RAG_MAX_CONTEXT_CHARS", "8000"))
        if not 1 <= top_k <= 50 or not 256 <= max_chars <= 32000:
            raise ValueError("RAG top_k must be 1..50 and max_context_chars 256..32000")
        backend = os.getenv("MEDIAFORGE_RAG_BACKEND", "sqlite").strip().lower()
        if backend not in {"sqlite", "pgvector", "ragflow"}:
            raise ValueError("MEDIAFORGE_RAG_BACKEND must be sqlite, pgvector or ragflow")
        return cls(enabled in {"true", "1"}, top_k, max_chars, backend)


def document_chunks(documents: list[dict[str, Any]], *, size: int = 1200, overlap: int = 150) -> list[dict[str, Any]]:
    if not 1 <= size <= 4000 or not 0 <= overlap < size:
        raise ValueError("invalid memory chunk size or overlap")
    chunks = []
    for document in documents:
        content = str(document["content"]).strip()
        for offset in range(0, len(content), size - overlap):
            chunk = content[offset:offset + size]
            chunks.append({"kind": document["kind"], "content": chunk,
                           "metadata": {**document.get("metadata", {}), "offset": offset},
                           "sha256": hashlib.sha256(chunk.encode("utf-8")).hexdigest()})
    return chunks


def build_memory_store(path: Path, settings: MemorySettings):
    if settings.backend == "pgvector":
        from .vector_memory import PgVectorMemoryStore, VectorSettings
        return PgVectorMemoryStore(VectorSettings.from_env())
    if settings.backend == "ragflow":
        from .ragflow_memory import RAGFlowMemoryStore, RAGFlowSettings
        return RAGFlowMemoryStore(RAGFlowSettings.from_env())
    return StoryMemoryStore(path)


def _tokens(text: str) -> list[str]:
    # Overlapping Han bigrams allow two-character names without a model download.
    tokens = []
    for word in re.findall(r"[\u3400-\u9fff]+|[^\W_]+", text.lower()):
        if all("\u3400" <= char <= "\u9fff" for char in word):
            tokens.extend(word[i:i + 2] for i in range(max(1, len(word) - 1)))
        else:
            tokens.append(word)
    return tokens


class StoryMemoryStore:
    """Rebuildable FTS5 index; persisted project state remains authoritative."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS memory_project (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_document USING fts5(
                    terms, tenant_id UNINDEXED, project_id UNINDEXED, kind UNINDEXED,
                    content UNINDEXED, metadata UNINDEXED, sha256 UNINDEXED, created_at UNINDEXED
                );
            """)

    def replace_project(
        self, *, tenant_id: str, project_id: str, documents: list[dict[str, Any]],
    ) -> None:
        serialized = json.dumps(documents, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT digest FROM memory_project WHERE tenant_id=? AND project_id=?",
                (tenant_id, project_id),
            ).fetchone()
            if old and old[0] == digest:
                return
            connection.execute(
                "DELETE FROM memory_document WHERE tenant_id=? AND project_id=?", (tenant_id, project_id),
            )
            for chunk in document_chunks(documents):
                connection.execute(
                    "INSERT INTO memory_document(terms, tenant_id, project_id, kind, content, metadata, sha256, created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (" ".join(_tokens(chunk["content"])), tenant_id, project_id, chunk["kind"], chunk["content"],
                     json.dumps(chunk["metadata"], ensure_ascii=False), chunk["sha256"], time.time()),
                )
            connection.execute(
                "INSERT INTO memory_project VALUES(?,?,?) ON CONFLICT(tenant_id,project_id) DO UPDATE SET digest=excluded.digest",
                (tenant_id, project_id, digest),
            )

    def search(
        self, *, tenant_id: str, project_ids: list[str], query: str, limit: int = 8,
        max_context_chars: int = 8000,
    ) -> list[dict[str, Any]]:
        tokens = list(dict.fromkeys(_tokens(query)))[:128]
        if not tokens or not project_ids:
            return []
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        placeholders = ",".join("?" for _ in project_ids)
        with sqlite3.connect(self.path, timeout=10) as connection:
            rows = connection.execute(
                f"SELECT rowid, project_id, kind, content, metadata, sha256, created_at, rank FROM memory_document "
                f"WHERE memory_document MATCH ? AND tenant_id=? AND project_id IN ({placeholders}) "
                "ORDER BY rank, rowid LIMIT ?",
                (expression, tenant_id, *project_ids, max(1, min(limit, 50))),
            ).fetchall()
        results = []
        remaining = max_context_chars
        for row in rows:
            if remaining <= 0:
                break
            excerpt = row[3][:remaining]
            results.append({
                "memory_id": row[0], "project_id": row[1], "kind": row[2], "content": excerpt,
                "metadata": json.loads(row[4]), "sha256": row[5], "created_at": float(row[6]),
                "score": -float(row[7]), "truncated": len(excerpt) < len(row[3]),
            })
            remaining -= len(excerpt)
        return results

    def status_view(self) -> dict[str, Any]:
        with sqlite3.connect(self.path, timeout=10) as connection:
            count = connection.execute("SELECT COUNT(*) FROM memory_document").fetchone()[0]
        return {"backend": "sqlite-fts5-bm25", "configured": True, "memory_count": count}

    def probe(self) -> dict[str, Any]:
        return {"reachable": True, "runtime": self.status_view()}
