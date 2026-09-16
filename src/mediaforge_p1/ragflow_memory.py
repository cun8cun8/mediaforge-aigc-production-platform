from __future__ import annotations

import hashlib
import json
import math
import os
from uuid import uuid4
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .memory import MemoryUnavailable


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _clean_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("MEDIAFORGE_RAGFLOW_BASE_URL must be an HTTP(S) URL without embedded credentials")
    return value.strip().rstrip("/")


def _dataset_map(raw: str) -> dict[str, dict[str, tuple[str, ...]]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("MEDIAFORGE_RAGFLOW_DATASET_MAP must be valid JSON") from exc
    if not isinstance(payload, dict) or not payload:
        raise ValueError("MEDIAFORGE_RAGFLOW_DATASET_MAP must be a non-empty tenant/project mapping")

    result: dict[str, dict[str, tuple[str, ...]]] = {}
    for tenant_id, projects in payload.items():
        tenant = str(tenant_id).strip()
        if not tenant or len(tenant) > 160 or not isinstance(projects, dict):
            raise ValueError("RAGFlow dataset mapping must contain tenant IDs mapped to project objects")
        entries: dict[str, tuple[str, ...]] = {}
        for project_id, dataset_ids in projects.items():
            project = str(project_id).strip()
            if not project or len(project) > 160 or not isinstance(dataset_ids, list):
                raise ValueError("RAGFlow dataset mapping project values must be dataset ID lists")
            ids = tuple(dict.fromkeys(str(item).strip() for item in dataset_ids if str(item).strip()))
            if not ids or len(ids) > 50 or any(len(item) > 160 for item in ids):
                raise ValueError("RAGFlow dataset mapping entries must contain 1..50 dataset IDs")
            entries[project] = ids
        result[tenant] = entries
    return result


@dataclass(frozen=True)
class RAGFlowSettings:
    base_url: str
    api_key: str = field(repr=False)
    dataset_map: dict[str, dict[str, tuple[str, ...]]]
    allow_data_export: bool
    allow_write_sync: bool = False
    timeout_seconds: float = 15.0
    similarity_threshold: float = 0.2
    vector_similarity_weight: float = 0.3
    max_sync_bytes: int = 5_000_000

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("MEDIAFORGE_RAGFLOW_API_KEY is required")
        if not self.allow_data_export:
            raise ValueError("MEDIAFORGE_RAGFLOW_ALLOW_DATA_EXPORT=true is required for the RAGFlow backend")
        if self.allow_write_sync and not self.allow_data_export:
            raise ValueError("RAGFlow write sync requires data export authorization")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120:
            raise ValueError("MEDIAFORGE_RAGFLOW_TIMEOUT_SECONDS must be > 0 and <= 120")
        for value, name in ((self.similarity_threshold, "MEDIAFORGE_RAGFLOW_SIMILARITY_THRESHOLD"),
                            (self.vector_similarity_weight, "MEDIAFORGE_RAGFLOW_VECTOR_SIMILARITY_WEIGHT")):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not 1 <= self.max_sync_bytes <= 20_000_000:
            raise ValueError("MEDIAFORGE_RAGFLOW_MAX_SYNC_BYTES must be 1..20000000")

    @classmethod
    def from_env(cls) -> "RAGFlowSettings":
        export = os.getenv("MEDIAFORGE_RAGFLOW_ALLOW_DATA_EXPORT", "false").strip().lower()
        if export not in {"true", "false", "1", "0"}:
            raise ValueError("MEDIAFORGE_RAGFLOW_ALLOW_DATA_EXPORT must be true or false")
        write_sync = os.getenv("MEDIAFORGE_RAGFLOW_ALLOW_WRITE_SYNC", "false").strip().lower()
        if write_sync not in {"true", "false", "1", "0"}:
            raise ValueError("MEDIAFORGE_RAGFLOW_ALLOW_WRITE_SYNC must be true or false")
        return cls(
            base_url=_clean_url(os.getenv("MEDIAFORGE_RAGFLOW_BASE_URL", "")),
            api_key=os.getenv("MEDIAFORGE_RAGFLOW_API_KEY", "").strip(),
            dataset_map=_dataset_map(os.getenv("MEDIAFORGE_RAGFLOW_DATASET_MAP", "")),
            allow_data_export=export in {"true", "1"},
            allow_write_sync=write_sync in {"true", "1"},
            timeout_seconds=float(os.getenv("MEDIAFORGE_RAGFLOW_TIMEOUT_SECONDS", "15")),
            similarity_threshold=float(os.getenv("MEDIAFORGE_RAGFLOW_SIMILARITY_THRESHOLD", "0.2")),
            vector_similarity_weight=float(os.getenv("MEDIAFORGE_RAGFLOW_VECTOR_SIMILARITY_WEIGHT", "0.3")),
            max_sync_bytes=int(os.getenv("MEDIAFORGE_RAGFLOW_MAX_SYNC_BYTES", "5000000")),
        )

    @property
    def api_url(self) -> str:
        return self.base_url if self.base_url.endswith("/api/v1") else f"{self.base_url}/api/v1"


class RAGFlowMemoryStore:
    """RAGFlow retrieval plus explicitly approved, operator-triggered snapshots."""

    def __init__(self, settings: RAGFlowSettings) -> None:
        self.settings = settings
        self._connectivity_verified = False
        self._last_error: str | None = None

    def replace_project(self, *, tenant_id: str, project_id: str, documents: list[dict[str, Any]]) -> None:
        # MediaForge must not silently export project material to a third-party KB.
        return None

    def sync_project(
        self,
        *,
        tenant_id: str,
        project_id: str,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Upload one content-addressed approved-story snapshot per mapped dataset.

        This is deliberately separate from ``replace_project`` so retrieval and
        planning never create an external data export side effect.
        """
        if not self.settings.allow_write_sync:
            raise MemoryUnavailable("RAGFlow write sync is disabled")
        dataset_ids = self.settings.dataset_map.get(tenant_id, {}).get(project_id, ())
        if not dataset_ids:
            raise MemoryUnavailable("project has no RAGFlow dataset mapping for write sync")
        content = self._snapshot_markdown(tenant_id, project_id, documents)
        body = content.encode("utf-8")
        if len(body) > self.settings.max_sync_bytes:
            raise MemoryUnavailable("RAGFlow snapshot exceeds the configured write-sync size limit")
        digest = hashlib.sha256(body).hexdigest()
        filename = f"mediaforge-approved-{digest[:24]}.md"
        datasets: list[dict[str, Any]] = []
        for dataset_id in dataset_ids:
            existing = self._find_document(dataset_id, filename)
            if existing:
                datasets.append({
                    "dataset_id": dataset_id,
                    "document_id": existing,
                    "uploaded": False,
                })
                continue
            response = self._upload_document(dataset_id, filename, body)
            data = response.get("data")
            rows = data if isinstance(data, list) else []
            document_id = str(rows[0].get("id") or "") if rows and isinstance(rows[0], dict) else ""
            if not document_id:
                self._connectivity_verified, self._last_error = False, "RAGFlow upload returned no document ID"
                raise MemoryUnavailable(self._last_error)
            datasets.append({
                "dataset_id": dataset_id,
                "document_id": document_id,
                "uploaded": True,
            })
        self._connectivity_verified, self._last_error = True, None
        return {
            "schema_version": "mediaforge-ragflow-sync-v1",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "document_name": filename,
            "content_sha256": digest,
            "content_bytes": len(body),
            "datasets": datasets,
            "uploaded_count": sum(1 for item in datasets if item["uploaded"]),
            "existing_count": sum(1 for item in datasets if not item["uploaded"]),
        }

    def search(
        self,
        *,
        tenant_id: str,
        project_ids: list[str],
        query: str,
        limit: int = 8,
        max_context_chars: int = 8000,
    ) -> list[dict[str, Any]]:
        question = str(query or "").strip()
        if not project_ids or not question:
            return []
        if len(project_ids) > 20 or not 1 <= limit <= 50 or not 256 <= max_context_chars <= 32000:
            raise MemoryUnavailable("invalid RAGFlow retrieval limits")
        if len(question) > 4000:
            raise MemoryUnavailable("RAGFlow query exceeds 4000 characters")

        mappings = self.settings.dataset_map.get(tenant_id, {})
        dataset_to_project: dict[str, str] = {}
        for project_id in project_ids:
            for dataset_id in mappings.get(project_id, ()):
                dataset_to_project.setdefault(dataset_id, project_id)
        if not dataset_to_project:
            return []

        payload = {
            "question": question,
            "dataset_ids": list(dataset_to_project),
            "page": 1,
            "page_size": limit,
            "top_k": max(32, limit),
            "similarity_threshold": self.settings.similarity_threshold,
            "vector_similarity_weight": self.settings.vector_similarity_weight,
        }
        response = self._request_json("POST", "/retrieval", body=payload)
        if response.get("code") != 0:
            self._connectivity_verified, self._last_error = False, "RAGFlow retrieval was rejected"
            raise MemoryUnavailable(self._last_error)
        data = response.get("data")
        chunks = data.get("chunks") if isinstance(data, dict) else None
        if not isinstance(chunks, list):
            self._connectivity_verified, self._last_error = False, "RAGFlow retrieval returned invalid chunks"
            raise MemoryUnavailable(self._last_error)
        self._connectivity_verified, self._last_error = True, None
        return self._normalize_chunks(chunks, dataset_to_project, limit, max_context_chars)

    def _normalize_chunks(
        self,
        chunks: list[Any],
        dataset_to_project: dict[str, str],
        limit: int,
        max_context_chars: int,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        remaining = max_context_chars
        for index, raw in enumerate(chunks):
            if len(results) >= limit or remaining <= 0 or not isinstance(raw, dict):
                continue
            content = str(raw.get("content") or raw.get("content_with_weight") or "").strip()
            if not content:
                continue
            dataset_id = str(raw.get("dataset_id") or raw.get("kb_id") or "").strip()
            project_id = dataset_to_project.get(dataset_id)
            if project_id is None:
                # A response from a dataset outside the explicit request scope is never used.
                continue
            excerpt = content[:remaining]
            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            metadata = {
                **metadata,
                "ragflow_dataset_id": dataset_id,
                "ragflow_document_id": raw.get("document_id") or raw.get("doc_id"),
                "ragflow_chunk_id": raw.get("id") or raw.get("chunk_id"),
            }
            chunk_id = str(metadata["ragflow_chunk_id"] or f"{dataset_id}:{index}")
            score_raw = raw.get("similarity", raw.get("score", 0))
            try:
                score = float(score_raw)
            except (TypeError, ValueError):
                score = 0.0
            results.append(
                {
                    "memory_id": f"ragflow:{chunk_id}",
                    "project_id": project_id,
                    "kind": str(raw.get("document_keyword") or raw.get("document_name") or "ragflow_chunk"),
                    "content": excerpt,
                    "metadata": metadata,
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "created_at": raw.get("create_time") or raw.get("created_at"),
                    "score": score,
                    "score_kind": "ragflow_hybrid",
                    "truncated": len(excerpt) < len(content),
                }
            )
            remaining -= len(excerpt)
        return results

    def status_view(self) -> dict[str, Any]:
        return {
            "backend": "ragflow-retrieval-api",
            "configured": True,
            "base_url": self.settings.base_url,
            "tenant_mapping_count": len(self.settings.dataset_map),
            "dataset_count": sum(
                len(dataset_ids)
                for projects in self.settings.dataset_map.values()
                for dataset_ids in projects.values()
            ),
            "allow_data_export": self.settings.allow_data_export,
            "allow_write_sync": self.settings.allow_write_sync,
            "connectivity_verified": self._connectivity_verified,
            "last_error": self._last_error,
            "sync_mode": (
                "explicit-content-addressed-snapshot-upload"
                if self.settings.allow_write_sync
                else "read-only-explicit-dataset-mapping"
            ),
        }

    def probe(self) -> dict[str, Any]:
        try:
            self._request_json("GET", "/datasets", query={"page": "1", "page_size": "1"})
            self._connectivity_verified, self._last_error = True, None
            return {"reachable": True, "runtime": self.status_view()}
        except MemoryUnavailable as exc:
            self._connectivity_verified, self._last_error = False, str(exc)
            return {"reachable": False, "error": str(exc), "runtime": self.status_view()}

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.settings.api_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.settings.api_key}",
            "User-Agent": "mediaforge-ragflow-adapter/0.1",
        }
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            raise MemoryUnavailable(f"RAGFlow request failed with HTTP {exc.code}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise MemoryUnavailable("RAGFlow request failed") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MemoryUnavailable("RAGFlow returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise MemoryUnavailable("RAGFlow returned an invalid response")
        return decoded

    def _find_document(self, dataset_id: str, filename: str) -> str | None:
        response = self._request_json(
            "GET",
            f"/datasets/{dataset_id}/documents",
            query={"name": filename, "page": "1", "page_size": "10"},
        )
        if response.get("code") != 0:
            self._connectivity_verified, self._last_error = False, "RAGFlow document lookup was rejected"
            raise MemoryUnavailable(self._last_error)
        data = response.get("data")
        rows = (
            data.get("docs", data.get("documents", []))
            if isinstance(data, dict)
            else data
        )
        if not isinstance(rows, list):
            self._connectivity_verified, self._last_error = False, "RAGFlow document lookup returned invalid data"
            raise MemoryUnavailable(self._last_error)
        for row in rows:
            if isinstance(row, dict) and str(row.get("name") or "") == filename:
                document_id = str(row.get("id") or "").strip()
                if document_id:
                    return document_id
        return None

    def _upload_document(self, dataset_id: str, filename: str, content: bytes) -> dict[str, Any]:
        boundary = f"----mediaforge-{uuid4().hex}"
        chunks = [
            f"--{boundary}\r\n".encode("ascii"),
            (
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                "Content-Type: text/markdown; charset=utf-8\r\n\r\n"
            ).encode("utf-8"),
            content,
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        ]
        request = Request(
            f"{self.settings.api_url}/datasets/{dataset_id}/documents",
            data=b"".join(chunks),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "mediaforge-ragflow-adapter/0.1",
            },
            method="POST",
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            raise MemoryUnavailable(f"RAGFlow document upload failed with HTTP {exc.code}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise MemoryUnavailable("RAGFlow document upload failed") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MemoryUnavailable("RAGFlow document upload returned invalid JSON") from exc
        if not isinstance(decoded, dict) or decoded.get("code") != 0:
            raise MemoryUnavailable("RAGFlow document upload was rejected")
        return decoded

    @staticmethod
    def _snapshot_markdown(
        tenant_id: str,
        project_id: str,
        documents: list[dict[str, Any]],
    ) -> str:
        if not documents:
            raise MemoryUnavailable("there is no approved story content to sync")
        lines = [
            "# MediaForge approved story snapshot",
            "",
            f"- tenant_id: {tenant_id}",
            f"- project_id: {project_id}",
        ]
        for index, document in enumerate(documents, start=1):
            content = str(document.get("content") or "").strip()
            if not content:
                continue
            kind = str(document.get("kind") or "approved_story_content").strip()[:120]
            metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
            lines.extend([
                "",
                f"## {index}. {kind}",
                "",
                content,
                "",
                "```json",
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                "```",
            ])
        snapshot = "\n".join(lines).strip() + "\n"
        if snapshot.count("## ") == 0:
            raise MemoryUnavailable("there is no approved story content to sync")
        return snapshot
