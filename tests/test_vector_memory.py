from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mediaforge_p1.api import create_app
from mediaforge_p1.contracts import CreativeBrief
from mediaforge_p1.embeddings import TEIEmbedder
from mediaforge_p1.llm import StoryPlan
from mediaforge_p1.memory import MemorySettings, MemoryUnavailable
from mediaforge_p1.vector_memory import PgVectorMemoryStore, VectorSettings


@pytest.fixture
def encoder():
    state = SimpleNamespace(calls=[], status=200, result=None, dimensions=3)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state.calls.append((self.path, body, self.headers.get('Authorization')))
            self.send_response(state.status)
            if state.status == 307:
                self.send_header('Location', '/redirected')
            self.end_headers()
            result = state.result
            if result is None:
                # Known vectors test transport/ranking, not an actual language model.
                result = [([3, 1, 0] if 'rain' in text or 'storm' in text else [0, 1, 3])[:state.dimensions]
                          for text in body['inputs']]
            self.wfile.write(json.dumps(result).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.embedder = TEIEmbedder(f'http://127.0.0.1:{server.server_port}/embed', 'fixture-model', 3,
                                 token='fixture-secret', allow_data_export=True,
                                 query_prefix='query: ', document_prefix='passage: ')
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_tei_normalization_prefixes_and_export_consent(encoder):
    encoder.result = [[3, 4, 0]]
    assert encoder.embedder.embed(['hello']) == [[0.6, 0.8, 0.0]]
    assert encoder.calls[0] == ('/embed', {'inputs': ['passage: hello'], 'truncate': False}, 'Bearer fixture-secret')
    encoder.embedder.embed(['hello'], query=True)
    assert encoder.calls[-1][1]['inputs'] == ['query: hello']
    assert encoder.embedder.verified
    encoder.embedder.allow_data_export = False
    with pytest.raises(MemoryUnavailable, match='export is disabled'):
        encoder.embedder.embed(['confidential'])
    assert len(encoder.calls) == 2 and not encoder.embedder.verified
    assert 'fixture-secret' not in repr(encoder.embedder)


@pytest.mark.parametrize('result', [[], {}, [[1, 2]], [[0, 0, 0]], [[True, 1, 2]],
                                     [[float('nan'), 1, 2]], [[float('inf'), 1, 2]], [[10**400, 1, 2]],
                                     [['secret', 1, 2]], [[1, 2, 3], [1, 2, 3]]])
def test_tei_rejects_bad_vectors(encoder, result):
    encoder.result = result
    with pytest.raises(MemoryUnavailable, match='invalid vectors') as failure:
        encoder.embedder.embed(['hello'])
    assert not encoder.embedder.verified
    assert 'secret' not in str(failure.value)


def test_tei_redirects_and_error_bodies_are_not_forwarded(encoder):
    encoder.status = 307
    with pytest.raises(MemoryUnavailable):
        encoder.embedder.embed(['secret text'])
    assert len(encoder.calls) == 1
    encoder.status, encoder.result = 500, {'error': 'private provider failure fixture-secret'}
    with pytest.raises(MemoryUnavailable) as failure:
        encoder.embedder.embed(['secret text'])
    assert 'private' not in str(failure.value) and 'fixture-secret' not in str(failure.value)


def test_vector_settings_and_index_identity(encoder, monkeypatch):
    settings = VectorSettings('postgresql://secret', 'one', encoder.embedder, 'revision1')
    assert settings.fingerprint != replace(settings, model_revision='revision2').fingerprint
    assert settings.fingerprint != replace(settings, embedder=replace(encoder.embedder, dimensions=2)).fingerprint
    assert settings.fingerprint != replace(settings, embedder=replace(encoder.embedder, query_prefix='other: ')).fingerprint
    assert settings.fingerprint != replace(settings, chunk_chars=256).fingerprint
    with pytest.raises(ValueError):
        replace(settings, chunk_overlap=settings.chunk_chars)
    assert 'postgresql://secret' not in repr(settings)
    with pytest.raises(ValueError):
        replace(settings, min_similarity=float('nan'))
    with pytest.raises(ValueError):
        replace(encoder.embedder, url='https://user:secret@host/embed')
    monkeypatch.setenv('MEDIAFORGE_RAG_BACKEND', 'unknown')
    with pytest.raises(ValueError):
        MemorySettings.from_env()


@pytest.fixture(scope='module')
def database():
    url = os.getenv('MEDIAFORGE_TEST_VECTOR_URL')
    if not url:
        pytest.skip('set MEDIAFORGE_TEST_VECTOR_URL for real pgvector integration')
    psycopg = pytest.importorskip('psycopg')
    pytest.importorskip('pgvector')
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    role = 'mf_vector_test_' + uuid.uuid4().hex
    password = uuid.uuid4().hex
    with psycopg.connect(url) as connection:
        connection.execute(files('mediaforge_p1').joinpath('sql/vector-memory.sql').read_text(encoding='utf-8'))
        connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS').format(sql.Identifier(role), sql.Literal(password)))
        connection.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON mediaforge_memory_project,mediaforge_memory_chunk TO {}').format(sql.Identifier(role)))
        connection.execute(sql.SQL('GRANT USAGE,SELECT ON SEQUENCE mediaforge_memory_chunk_memory_id_seq TO {}').format(sql.Identifier(role)))
    result = SimpleNamespace(admin=url, runtime=make_conninfo(url, user=role, password=password), namespaces=[])
    try:
        yield result
    finally:
        with psycopg.connect(url) as connection:
            for table in ('mediaforge_memory_chunk', 'mediaforge_memory_project'):
                connection.execute(sql.SQL('DELETE FROM {} WHERE namespace=ANY(%s)').format(sql.Identifier(table)), (result.namespaces,))
            connection.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.fixture
def store(database, encoder):
    namespace = uuid.uuid4().hex
    database.namespaces.append(namespace)
    return PgVectorMemoryStore(VectorSettings(database.runtime, namespace, encoder.embedder, 'revision1', min_similarity=0.8))


def doc(content):
    return {'kind': 'shot_card', 'content': content, 'metadata': {'revision': 1}}


def index(store, documents, tenant='a', project='one'):
    store.replace_project(tenant_id=tenant, project_id=project, documents=documents)


def search(store, query='storm', tenant='a', projects=None, **kwargs):
    return store.search(tenant_id=tenant, project_ids=projects or ['one'], query=query, **kwargs)


def test_pgvector_ranking_restart_threshold_and_context_budget(store, encoder):
    index(store, [doc('rain outside ' * 150), doc('clear sky')])
    assert all(len(text.removeprefix('passage: ')) <= 384 for _, body, _ in encoder.calls for text in body['inputs'])
    hits = search(store, max_context_chars=71)
    assert len(hits) == 1 and len(hits[0]['content']) == 71 and hits[0]['truncated']
    assert hits[0]['score'] == pytest.approx(1) and hits[0]['score_kind'] == 'cosine_similarity'
    assert hits[0]['embedding_fingerprint'] == store.settings.fingerprint
    call_count = len(encoder.calls)
    index(store, [doc('rain outside ' * 150), doc('clear sky')])
    assert len(encoder.calls) == call_count
    restored = PgVectorMemoryStore(store.settings)
    assert search(restored)[0]['memory_id'] == hits[0]['memory_id']
    assert restored.probe()['reachable'] is True


def test_pgvector_rls_and_namespace_tenant_project_isolation(store, database):
    import psycopg
    index(store, [doc('rain owner')])
    index(store, [doc('rain secret')], tenant='foreign')
    index(store, [doc('rain private')], project='private')
    other_namespace = uuid.uuid4().hex
    database.namespaces.append(other_namespace)
    other = PgVectorMemoryStore(replace(store.settings, namespace=other_namespace))
    index(other, [doc('rain other workspace')])
    assert [hit['content'] for hit in search(store)] == ['rain owner']
    assert not search(store, projects=["one'); DROP TABLE mediaforge_memory_chunk; --"])
    with psycopg.connect(database.runtime) as connection:
        assert connection.execute('SELECT count(*) FROM mediaforge_memory_chunk').fetchone()[0] == 0
    with store._connection('a', ['one']) as connection:
        assert connection.execute('SELECT content FROM mediaforge_memory_chunk').fetchall() == [('rain owner',)]
    with pytest.raises(MemoryUnavailable):
        with store._connection('a', ['one']) as connection:
            connection.execute("UPDATE mediaforge_memory_chunk SET tenant_id='foreign'")
    assert len(search(store)) == 1
    unsafe = PgVectorMemoryStore(replace(store.settings, database_url=database.admin))
    assert unsafe.probe()['reachable'] is False
    assert 'superuser' in unsafe.status_view()['last_error']


def test_failed_reindex_keeps_old_index_and_new_model_excludes_old_space(store, encoder):
    index(store, [doc('rain old')])
    original = search(store)[0]
    encoder.status = 503
    with pytest.raises(MemoryUnavailable):
        index(store, [doc('rain changed')])
    encoder.status = 200
    assert search(store)[0]['memory_id'] == original['memory_id']
    changed = PgVectorMemoryStore(replace(store.settings, model_revision='revision2',
                                         embedder=replace(encoder.embedder, dimensions=2)))
    assert not search(changed)
    encoder.dimensions = 2
    index(changed, [doc('rain new')], project='two')
    assert [hit['content'] for hit in search(changed, projects=['one', 'two'])] == ['rain new']
    encoder.dimensions = 3
    assert [hit['content'] for hit in search(store, projects=['one', 'two'])] == ['rain old']
    index(store, [])
    assert not search(store)


def test_concurrent_reindex_cannot_overwrite_newer_index(store, encoder, monkeypatch):
    index(store, [doc('rain initial')])
    started, resume = threading.Event(), threading.Event()
    embed = encoder.embedder.embed

    def delayed(texts, **kwargs):
        if 'rain slow' in texts:
            started.set()
            assert resume.wait(10)
        return embed(texts, **kwargs)

    monkeypatch.setattr(encoder.embedder, 'embed', delayed)
    failures = []

    def slower():
        try:
            index(store, [doc('rain slow')])
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=slower)
    thread.start()
    try:
        assert started.wait(10)
        index(store, [doc('rain newest')])
    finally:
        resume.set()
        thread.join(timeout=15)
    assert not thread.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], MemoryUnavailable)
    assert 'changed during indexing' in str(failures[0])
    assert search(store)[0]['content'] == 'rain newest'


def test_pgvector_api_planning_provenance_access_and_outage(store, encoder, monkeypatch, tmp_path):
    for key, value in {'STATE_BACKEND': 'sqlite', 'PROVIDER': 'mock', 'AUTH_MODE': 'required',
                       'RATE_LIMIT_ENABLED': 'false', 'RAG_BACKEND': 'pgvector',
                       'RAG_DATABASE_URL': store.settings.database_url, 'RAG_NAMESPACE': store.settings.namespace,
                       'EMBEDDING_URL': encoder.embedder.url, 'EMBEDDING_MODEL': encoder.embedder.model,
                       'EMBEDDING_REVISION': 'revision1', 'EMBEDDING_DIMENSIONS': '3',
                       'EMBEDDING_ALLOW_DATA_EXPORT': 'true'}.items():
        monkeypatch.setenv('MEDIAFORGE_' + key, value)
    monkeypatch.setenv('MEDIAFORGE_API_KEYS', json.dumps({
        'editor': {'subject': 'alice', 'role': 'editor', 'tenant_id': 'a'},
        'admin': {'subject': 'admin', 'role': 'admin', 'tenant_id': 'a'},
    }))
    app = create_app(output_root=tmp_path)
    service = app.state.mediaforge
    for project_id, owner in [('source', 'alice'), ('target', 'alice'), ('private', 'bob')]:
        service.create_project(CreativeBrief(project_id=project_id, tenant_id='a', title='rain',
                                             premise='rain at midnight', genre='drama', style='cinema',
                                             characters=['Alice', 'Bob'], duration_seconds=30, budget=2), owner_subject=owner)
        service.generate_plan(project_id)
    observed = []

    class Planner:
        def status_view(self):
            return {'mode': 'http', 'name': 'fixture', 'configured': True}

        def plan_with_context(self, brief, memory):
            observed.extend(memory)
            return StoryPlan({'theme': 'rain'}, service._build_shots(brief))

    service.story_planner = Planner()
    with TestClient(app, headers={'Authorization': 'Bearer editor'}) as client:
        assert client.post('/enterprise/memory/probe').status_code == 403
        assert client.post('/enterprise/memory/probe', headers={'Authorization': 'Bearer admin'}).json()['reachable']
        before_calls = len(encoder.calls)
        assert client.post('/projects/target/plan', json={'memory_project_ids': ['private']}).status_code == 404
        assert len(encoder.calls) == before_calls
        response = client.post('/projects/target/plan', json={'memory_project_ids': ['source']})
        assert response.status_code == 200, response.text
        provenance = response.json()['story_bible']['memory_retrieval']
        assert provenance['backend'] == 'postgres-pgvector' and provenance['context_used']
        assert observed and all(hit['project_id'] == 'source' for hit in observed)
        assert provenance['sources'][0]['embedding_fingerprint'] == observed[0]['embedding_fingerprint']
        assert provenance['sources'][0]['excerpt_sha256']
        before = copy.deepcopy(service.project_view('target')['story_bible'])
        encoder.status = 503
        assert client.post('/projects/target/plan', json={'memory_project_ids': ['source']}).status_code == 503
        assert client.get('/projects/source/memory', params={'query': 'rain'}).status_code == 503
        assert service.project_view('target')['story_bible'] == before
        readiness = service.production_readiness()
        check = next(check for check in readiness['checks'] if check['code'] == 'story_memory')
        assert check['blocking'] and not check['passed']


def test_pgvector_browser_controls(store, monkeypatch, tmp_path):
    if not os.getenv('PLAYWRIGHT_MODULE'):
        pytest.skip('set PLAYWRIGHT_MODULE to run isolated browser acceptance')
    import socket
    import subprocess
    import time
    from pathlib import Path

    import uvicorn

    for key, value in {'STATE_BACKEND': 'sqlite', 'PROVIDER': 'mock', 'AUTH_MODE': 'disabled',
                       'RATE_LIMIT_ENABLED': 'false', 'RAG_BACKEND': 'sqlite'}.items():
        monkeypatch.setenv('MEDIAFORGE_' + key, value)
    app = create_app(output_root=tmp_path)
    service = app.state.mediaforge
    service.story_memory = store
    service.memory_settings = replace(service.memory_settings, backend='pgvector')
    service.create_project(CreativeBrief(project_id='vector_browser', title='Semantic browser acceptance',
                                         premise='A future call at midnight.', genre='drama', style='cinema',
                                         characters=['Alice', 'Bob'], duration_seconds=30, budget=2))
    service.generate_plan('vector_browser')
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning', timeout_graceful_shutdown=3))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started
            environment = dict(os.environ, MEDIAFORGE_UI_URL=f'http://127.0.0.1:{listener.getsockname()[1]}')
            result = subprocess.run(['node', 'tests/ui_vector_smoke.cjs'], env=environment,
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=90)
            assert result.returncode == 0, (result.stdout + result.stderr).decode('utf-8', errors='replace')
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            assert not thread.is_alive()
