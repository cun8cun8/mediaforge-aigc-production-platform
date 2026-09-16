from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

from .embeddings import TEIEmbedder
from .memory import MemoryUnavailable, document_chunks


@dataclass(frozen=True)
class VectorSettings:
    database_url: str = field(repr=False)
    namespace: str
    embedder: TEIEmbedder
    model_revision: str
    min_similarity: float = 0.25
    chunk_chars: int = 384
    chunk_overlap: int = 48

    def __post_init__(self):
        if not self.database_url or not self.namespace.strip() or len(self.namespace) > 128 or not self.model_revision.strip():
            raise ValueError("RAG database URL, namespace and embedding model revision are required")
        if not math.isfinite(self.min_similarity) or not -1 <= self.min_similarity <= 1:
            raise ValueError("RAG minimum similarity must be between -1 and 1")
        if not 64 <= self.chunk_chars <= 3000 or not 0 <= self.chunk_overlap < self.chunk_chars:
            raise ValueError("RAG chunk chars must be 64..3000 and overlap smaller than chunk chars")

    @classmethod
    def from_env(cls):
        export = os.getenv("MEDIAFORGE_EMBEDDING_ALLOW_DATA_EXPORT", "false").strip().lower()
        if export not in {"true", "false", "1", "0"}:
            raise ValueError("MEDIAFORGE_EMBEDDING_ALLOW_DATA_EXPORT must be true or false")
        return cls(
            database_url=os.getenv("MEDIAFORGE_RAG_DATABASE_URL", "").strip(),
            namespace=os.getenv("MEDIAFORGE_RAG_NAMESPACE", "").strip(),
            model_revision=os.getenv("MEDIAFORGE_EMBEDDING_REVISION", "").strip(),
            min_similarity=float(os.getenv("MEDIAFORGE_RAG_MIN_SIMILARITY", "0.25")),
            chunk_chars=int(os.getenv("MEDIAFORGE_RAG_CHUNK_CHARS", "384")),
            chunk_overlap=int(os.getenv("MEDIAFORGE_RAG_CHUNK_OVERLAP", "48")),
            embedder=TEIEmbedder(
                url=os.getenv("MEDIAFORGE_EMBEDDING_URL", "").strip(),
                model=os.getenv("MEDIAFORGE_EMBEDDING_MODEL", "").strip(),
                dimensions=int(os.getenv("MEDIAFORGE_EMBEDDING_DIMENSIONS", "0")),
                token=os.getenv("MEDIAFORGE_EMBEDDING_TOKEN", "").strip(),
                timeout_seconds=float(os.getenv("MEDIAFORGE_EMBEDDING_TIMEOUT_SECONDS", "30")),
                query_prefix=os.getenv("MEDIAFORGE_EMBEDDING_QUERY_PREFIX", ""),
                document_prefix=os.getenv("MEDIAFORGE_EMBEDDING_DOCUMENT_PREFIX", ""),
                allow_data_export=export in {"true", "1"},
            ),
        )

    @property
    def fingerprint(self) -> str:
        encoder = self.embedder
        identity = {"model": encoder.model, "revision": self.model_revision, "dimensions": encoder.dimensions,
                    "query_prefix": encoder.query_prefix, "document_prefix": encoder.document_prefix,
                    "chunker": f"chars-{self.chunk_chars}-overlap-{self.chunk_overlap}-v1", "normalization": "l2-v1"}
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class PgVectorMemoryStore:
    """Exact cosine retrieval with forced RLS and optimistic atomic reindexing."""

    def __init__(self, settings: VectorSettings) -> None:
        try:
            import psycopg
            from pgvector import Vector
            from pgvector.psycopg import register_vector
        except ImportError as exc:
            raise ValueError("pgvector retrieval requires the enterprise dependencies") from exc
        self.settings = settings
        self._psycopg, self._vector, self._register_vector = psycopg, Vector, register_vector
        self._connectivity_verified = False
        self._last_error = None

    @contextmanager
    def _connection(self, tenant_id: str, project_ids: list[str]):
        try:
            with self._psycopg.connect(self.settings.database_url, connect_timeout=5, options="-c statement_timeout=10000 -c lock_timeout=5000 -c search_path=public,pg_catalog") as connection:
                role = connection.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
                if not role or any(role):
                    raise MemoryUnavailable("RAG database role must not be superuser or BYPASSRLS")
                tables = connection.execute("""
                    SELECT c.relrowsecurity, c.relforcerowsecurity,
                           (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid),
                           (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid AND p.polname='mediaforge_memory_scope')
                    FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
                    WHERE n.nspname='public' AND c.relname IN ('mediaforge_memory_project','mediaforge_memory_chunk')
                """).fetchall()
                if len(tables) != 2 or any(not row[0] or not row[1] or row[2:] != (1, 1) for row in tables):
                    raise MemoryUnavailable("RAG schema and forced row security must be initialized")
                self._register_vector(connection)
                for name, value in (("namespace", self.settings.namespace), ("tenant", tenant_id), ("projects", json.dumps(project_ids))):
                    connection.execute("SELECT set_config(%s, %s, true)", (f"mediaforge.memory_{name}", value))
                yield connection
            self._connectivity_verified, self._last_error = True, None
        except MemoryUnavailable as exc:
            self._connectivity_verified, self._last_error = False, str(exc)
            raise
        except self._psycopg.Error as exc:
            self._connectivity_verified, self._last_error = False, "RAG database operation failed"
            raise MemoryUnavailable(self._last_error) from exc

    def _scope(self, tenant_id: str, project_id: str) -> tuple[str, str, str]:
        return self.settings.namespace, tenant_id, project_id

    def replace_project(self, *, tenant_id: str, project_id: str, documents: list[dict[str, Any]]) -> None:
        fingerprint = self.settings.fingerprint
        digest = hashlib.sha256(json.dumps(documents, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        desired = (digest, fingerprint)
        scope = self._scope(tenant_id, project_id)
        query = "SELECT digest, fingerprint FROM mediaforge_memory_project WHERE namespace=%s AND tenant_id=%s AND project_id=%s"
        with self._connection(tenant_id, [project_id]) as connection:
            before = connection.execute(query, scope).fetchone()
        if before == desired:
            return
        chunks = document_chunks(documents, size=self.settings.chunk_chars, overlap=self.settings.chunk_overlap)
        if len(chunks) > 4000:
            raise MemoryUnavailable("project memory exceeds 4000 chunks")
        vectors = []
        # Model calls happen before the write transaction; a failed batch keeps the old index intact.
        for offset in range(0, len(chunks), 16):
            vectors.extend(self.settings.embedder.embed([chunk["content"] for chunk in chunks[offset:offset + 16]]))
        with self._connection(tenant_id, [project_id]) as connection:
            lock_key = int.from_bytes(hashlib.sha256(json.dumps(scope).encode()).digest()[:8], signed=True)
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            current = connection.execute(query, scope).fetchone()
            if current == desired:
                return
            if current != before:
                raise MemoryUnavailable("project memory changed during indexing; retry current state")
            connection.execute("DELETE FROM mediaforge_memory_chunk WHERE namespace=%s AND tenant_id=%s AND project_id=%s", scope)
            for chunk, vector in zip(chunks, vectors, strict=True):
                connection.execute("""
                    INSERT INTO mediaforge_memory_chunk(namespace,tenant_id,project_id,fingerprint,kind,content,metadata,sha256,created_at,embedding)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (*scope, fingerprint, chunk["kind"], chunk["content"], self._psycopg.types.json.Jsonb(chunk["metadata"]), chunk["sha256"], time.time(), self._vector(vector)))
            connection.execute("""
                INSERT INTO mediaforge_memory_project(namespace,tenant_id,project_id,digest,fingerprint) VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(namespace,tenant_id,project_id) DO UPDATE SET digest=excluded.digest,fingerprint=excluded.fingerprint
            """, (*scope, *desired))

    def search(self, *, tenant_id: str, project_ids: list[str], query: str, limit: int = 8,
               max_context_chars: int = 8000) -> list[dict[str, Any]]:
        if not project_ids or not query.strip():
            return []
        if len(project_ids) > 20 or not 1 <= limit <= 50 or not 1 <= max_context_chars <= 32000:
            raise MemoryUnavailable("invalid memory retrieval limits")
        with self._connection(tenant_id, project_ids) as connection:
            count = connection.execute("SELECT count(*) FROM mediaforge_memory_chunk WHERE namespace=%s AND tenant_id=%s AND project_id=ANY(%s) AND fingerprint=%s",
                                       (self.settings.namespace, tenant_id, project_ids, self.settings.fingerprint)).fetchone()[0]
        if not count:
            return []
        vector = self._vector(self.settings.embedder.embed([query], query=True)[0])
        with self._connection(tenant_id, project_ids) as connection:
            rows = connection.execute("""
                WITH scoped AS MATERIALIZED (
                    SELECT * FROM mediaforge_memory_chunk
                    WHERE namespace=%s AND tenant_id=%s AND project_id=ANY(%s) AND fingerprint=%s
                )
                SELECT memory_id,project_id,kind,content,metadata,sha256,created_at,embedding <=> %s AS distance
                FROM scoped WHERE embedding <=> %s <= %s
                ORDER BY distance,memory_id LIMIT %s
            """, (self.settings.namespace, tenant_id, project_ids, self.settings.fingerprint, vector,
                  vector, 1 - self.settings.min_similarity, limit)).fetchall()
        results, remaining = [], max_context_chars
        for row in rows:
            if remaining <= 0:
                break
            excerpt = row[3][:remaining]
            results.append({"memory_id": row[0], "project_id": row[1], "kind": row[2], "content": excerpt,
                            "metadata": row[4], "sha256": row[5], "created_at": row[6],
                            "score": max(-1.0, min(1.0, 1 - float(row[7]))), "score_kind": "cosine_similarity",
                            "truncated": len(excerpt) < len(row[3]), "embedding_fingerprint": self.settings.fingerprint})
            remaining -= len(excerpt)
        return results

    def status_view(self) -> dict[str, Any]:
        return {"backend": "postgres-pgvector", "configured": True, "namespace": self.settings.namespace,
                "connectivity_verified": self._connectivity_verified, "last_error": self._last_error,
                "embedding_model": self.settings.embedder.model, "embedding_revision": self.settings.model_revision,
                "embedding_dimensions": self.settings.embedder.dimensions, "embedding_fingerprint": self.settings.fingerprint,
                "allow_data_export": self.settings.embedder.allow_data_export, "min_similarity": self.settings.min_similarity,
                "embedding_verified": self.settings.embedder.verified,
                "chunk_chars": self.settings.chunk_chars, "chunk_overlap": self.settings.chunk_overlap,
                "row_security": "forced-namespace-tenant-project", "search_mode": "exact-cosine"}

    def probe(self) -> dict[str, Any]:
        try:
            with self._connection("__probe__", []) as connection:
                connection.execute("SELECT count(*) FROM mediaforge_memory_chunk").fetchone()
            self.settings.embedder.embed(["MediaForge embedding connectivity check"], query=True)
            return {"reachable": True, "runtime": self.status_view()}
        except MemoryUnavailable as exc:
            return {"reachable": False, "error": str(exc), "runtime": self.status_view()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the pgvector memory schema with an operator migration connection.")
    parser.add_argument("--init-schema", action="store_true", required=True)
    parser.parse_args()
    url = os.getenv("MEDIAFORGE_RAG_MIGRATION_URL", "")
    if not url:
        parser.error("MEDIAFORGE_RAG_MIGRATION_URL is required; never use the migration role for the API")
    import psycopg
    with psycopg.connect(url) as connection:
        connection.execute(files("mediaforge_p1").joinpath("sql/vector-memory.sql").read_text(encoding="utf-8"))
    print("pgvector memory schema initialized; grant scoped runtime DML privileges to a non-bypass role")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
