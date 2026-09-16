from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from mediaforge_p1.contracts import CreativeBrief
from mediaforge_p1.llm import StoryPlan
from mediaforge_p1.memory import MemorySettings, MemoryUnavailable
from mediaforge_p1.ragflow_memory import RAGFlowMemoryStore, RAGFlowSettings
from mediaforge_p1.service import MediaForgeService


def _settings(monkeypatch, base_url: str, *, allow_export: str = "true") -> None:
    monkeypatch.setenv("MEDIAFORGE_RAGFLOW_BASE_URL", base_url)
    monkeypatch.setenv("MEDIAFORGE_RAGFLOW_API_KEY", "ragflow-test-token")
    monkeypatch.setenv("MEDIAFORGE_RAGFLOW_DATASET_MAP", json.dumps(
        {"tenant_a": {"source": ["dataset-source"], "other": ["dataset-other"]}}
    ))
    monkeypatch.setenv("MEDIAFORGE_RAGFLOW_ALLOW_DATA_EXPORT", allow_export)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in (
        "MEDIAFORGE_RAGFLOW_BASE_URL",
        "MEDIAFORGE_RAGFLOW_API_KEY",
        "MEDIAFORGE_RAGFLOW_DATASET_MAP",
        "MEDIAFORGE_RAGFLOW_ALLOW_DATA_EXPORT",
        "MEDIAFORGE_RAGFLOW_ALLOW_WRITE_SYNC",
        "MEDIAFORGE_RAGFLOW_MAX_SYNC_BYTES",
        "MEDIAFORGE_RAGFLOW_TIMEOUT_SECONDS",
        "MEDIAFORGE_RAGFLOW_SIMILARITY_THRESHOLD",
        "MEDIAFORGE_RAGFLOW_VECTOR_SIMILARITY_WEIGHT",
        "MEDIAFORGE_RAG_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MEDIAFORGE_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("MEDIAFORGE_PROVIDER", "mock")
    monkeypatch.setenv("MEDIAFORGE_AUTH_MODE", "disabled")
    monkeypatch.setenv("MEDIAFORGE_RATE_LIMIT_ENABLED", "false")


def _brief(project_id: str) -> CreativeBrief:
    return CreativeBrief(
        project_id=project_id,
        tenant_id="tenant_a",
        title="午夜来电",
        premise="林夏接到来自未来的电话。",
        genre="悬疑",
        style="电影感",
        characters=["林夏", "周启"],
        duration_seconds=30,
        budget=2,
    )


def test_ragflow_settings_require_explicit_query_export(monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFORGE_RAGFLOW_BASE_URL", "https://ragflow.example.test")
    monkeypatch.setenv("MEDIAFORGE_RAGFLOW_API_KEY", "token")
    monkeypatch.setenv("MEDIAFORGE_RAGFLOW_DATASET_MAP", '{"tenant_a":{"source":["dataset"]}}')
    monkeypatch.setenv("MEDIAFORGE_RAGFLOW_ALLOW_DATA_EXPORT", "false")

    with pytest.raises(ValueError, match="ALLOW_DATA_EXPORT"):
        RAGFlowSettings.from_env()


def test_ragflow_store_retrieves_only_explicitly_mapped_datasets(monkeypatch) -> None:
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            assert self.path == "/api/v1/retrieval"
            assert self.headers["Authorization"] == "Bearer ragflow-test-token"
            size = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(size)))
            body = json.dumps(
                {
                    "code": 0,
                    "data": {
                        "chunks": [
                            {
                                "id": "chunk-source",
                                "dataset_id": "dataset-source",
                                "document_id": "doc-source",
                                "document_name": "角色设定.pdf",
                                "content": "林夏曾经在旧电话亭见过未来的自己。",
                                "similarity": 0.91,
                                "metadata": {"page": 3},
                            },
                            {
                                "id": "chunk-outside",
                                "dataset_id": "dataset-outside",
                                "content": "This must never enter the plan.",
                            },
                        ]
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            assert self.path == "/api/v1/datasets?page=1&page_size=1"
            body = b'{"code":0,"data":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _settings(monkeypatch, f"http://127.0.0.1:{server.server_port}")
        store = RAGFlowMemoryStore(RAGFlowSettings.from_env())
        hits = store.search(
            tenant_id="tenant_a",
            project_ids=["source"],
            query="未来电话",
            limit=4,
            max_context_chars=256,
        )
        probe = store.probe()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert requests[0]["dataset_ids"] == ["dataset-source"]
    assert requests[0]["question"] == "未来电话"
    assert len(hits) == 1
    assert hits[0]["project_id"] == "source"
    assert hits[0]["metadata"] == {
        "page": 3,
        "ragflow_dataset_id": "dataset-source",
        "ragflow_document_id": "doc-source",
        "ragflow_chunk_id": "chunk-source",
    }
    assert hits[0]["score_kind"] == "ragflow_hybrid"
    assert probe["reachable"] is True
    assert store.status_view()["sync_mode"] == "read-only-explicit-dataset-mapping"


def test_ragflow_errors_do_not_expose_server_response(monkeypatch) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            body = b'{"message":"private ragflow error token=secret"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _settings(monkeypatch, f"http://127.0.0.1:{server.server_port}")
        store = RAGFlowMemoryStore(RAGFlowSettings.from_env())
        with pytest.raises(MemoryUnavailable) as failure:
            store.search(
                tenant_id="tenant_a",
                project_ids=["source"],
                query="future",
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert "secret" not in str(failure.value)
    assert "503" in str(failure.value)


def test_ragflow_write_sync_requires_opt_in_and_deduplicates_content_addressed_snapshot(monkeypatch) -> None:
    uploaded: list[bytes] = []
    known_documents: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            assert parsed.path == "/api/v1/datasets/dataset-source/documents"
            name = parse_qs(parsed.query)["name"][0]
            rows = ([{"id": known_documents[name], "name": name}]
                    if name in known_documents else [])
            body = json.dumps({"code": 0, "data": rows}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            assert self.path == "/api/v1/datasets/dataset-source/documents"
            assert self.headers["Authorization"] == "Bearer ragflow-test-token"
            assert self.headers["Content-Type"].startswith("multipart/form-data; boundary=")
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            assert b'Content-Disposition: form-data; name="file";' in raw
            assert b"MediaForge approved story snapshot" in raw
            uploaded.append(raw)
            name = raw.split(b'filename="', 1)[1].split(b'"', 1)[0].decode("ascii")
            known_documents[name] = "document-snapshot"
            body = b'{"code":0,"data":[{"id":"document-snapshot"}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _settings(monkeypatch, f"http://127.0.0.1:{server.server_port}")
        store = RAGFlowMemoryStore(RAGFlowSettings.from_env())
        documents = [{"kind": "story_bible", "content": "林夏在电话亭等待未来来电。", "metadata": {"version": 1}}]
        with pytest.raises(MemoryUnavailable, match="write sync is disabled"):
            store.sync_project(tenant_id="tenant_a", project_id="source", documents=documents)

        monkeypatch.setenv("MEDIAFORGE_RAGFLOW_ALLOW_WRITE_SYNC", "true")
        store = RAGFlowMemoryStore(RAGFlowSettings.from_env())
        first = store.sync_project(tenant_id="tenant_a", project_id="source", documents=documents)
        second = store.sync_project(tenant_id="tenant_a", project_id="source", documents=documents)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert first["uploaded_count"] == 1
    assert first["existing_count"] == 0
    assert second["uploaded_count"] == 0
    assert second["existing_count"] == 1
    assert first["content_sha256"] == second["content_sha256"]
    assert len(uploaded) == 1
    assert store.status_view()["sync_mode"] == "explicit-content-addressed-snapshot-upload"


def test_ragflow_backend_is_used_for_planning_provenance(tmp_path, monkeypatch) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(size))
            assert request["dataset_ids"] == ["dataset-source"]
            body = json.dumps(
                {
                    "code": 0,
                    "data": {
                        "chunks": [
                            {
                                "id": "planning-chunk",
                                "dataset_id": "dataset-source",
                                "content": "旧电话亭是林夏与未来自我相遇的地点。",
                                "similarity": 0.9,
                            }
                        ]
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _settings(monkeypatch, f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("MEDIAFORGE_RAG_BACKEND", "ragflow")
        service = MediaForgeService(tmp_path)
        service.create_project(_brief("source"))
        service.generate_plan("source")
        service.create_project(_brief("target"))
        observed: list[dict] = []

        class Planner:
            def plan_with_context(self, brief, memory):
                observed.extend(memory)
                return StoryPlan({"theme": "未来电话"}, service._build_shots(brief))

        service.story_planner = Planner()
        result = service.generate_plan("target", memory_project_ids=["source"])
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert observed and observed[0]["project_id"] == "source"
    assert result["story_bible"]["memory_retrieval"]["backend"] == "ragflow-retrieval-api"
    assert result["story_bible"]["memory_retrieval"]["sources"][0]["metadata"]["ragflow_chunk_id"] == "planning-chunk"
    assert MemorySettings.from_env().backend == "ragflow"
