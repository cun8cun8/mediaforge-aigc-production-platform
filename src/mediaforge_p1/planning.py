from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Any, TypedDict
from uuid import uuid4

from .contracts import CreativeBrief, ScriptPackage, ShotCard
from .llm import StoryPlannerError
from .memory import MemoryUnavailable


class PlanningError(ValueError):
    pass


class PlanningNotFound(PlanningError):
    pass


class PlanningBusy(PlanningError):
    pass


class PlanningState(TypedDict, total=False):
    run_id: str
    graph_version: str
    brief: dict
    input_fingerprint: str
    planner_fingerprint: str
    source_project_ids: list[str]
    memory: list[dict]
    bible: dict
    shots: list[dict]
    specs: list[dict]
    policy_reports: list[dict]
    trace: list[dict]
    revision: int
    feedback: str
    decision: str
    reviewer: str


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class PlanningSettings:
    checkpoint_backend: str = 'sqlite'
    database_url: str | None = None

    @classmethod
    def from_env(cls) -> 'PlanningSettings':
        backend = os.getenv('MEDIAFORGE_PLANNING_CHECKPOINT_BACKEND', 'sqlite').strip().lower()
        if backend not in {'sqlite', 'postgres'}:
            raise PlanningError('MEDIAFORGE_PLANNING_CHECKPOINT_BACKEND must be sqlite or postgres')
        database_url = os.getenv('MEDIAFORGE_PLANNING_DATABASE_URL', '').strip() or None
        if backend == 'postgres':
            if not database_url:
                raise PlanningError('MEDIAFORGE_PLANNING_DATABASE_URL is required for postgres checkpoints')
            if not database_url.startswith(('postgres://', 'postgresql://')):
                raise PlanningError('MEDIAFORGE_PLANNING_DATABASE_URL must use a PostgreSQL URL')
        return cls(checkpoint_backend=backend, database_url=database_url)


class PlanningWorkflow:
    """Preproduction drafts, durable graph checkpoints and explicit human adoption."""

    version = 'mediaforge-planning-graph-v3'
    compatible_migrations = {'mediaforge-planning-graph-v2': version}
    checkpoint_nodes = frozenset({'retrieve', 'story', 'storyboard', 'compile', 'review'})

    def __init__(self, service):
        self.service = service
        mode = os.getenv('MEDIAFORGE_PLANNING_MODE', 'legacy').strip().lower()
        if mode not in {'legacy', 'langgraph'}:
            raise PlanningError('MEDIAFORGE_PLANNING_MODE must be legacy or langgraph')
        self.enabled = mode == 'langgraph'
        self.settings = PlanningSettings.from_env()
        self.root = service.output_root / '.planning'
        self.database = self.root / 'checkpoints.sqlite3'
        self.run_table = 'planning_run' if self.settings.checkpoint_backend == 'sqlite' else 'mediaforge_planning_run'
        self._mutation_scope = ContextVar('planning_mutation_scope', default=None)
        self._checkpoint_setup_lock = Lock()
        self._checkpoint_initialized = False
        self._checkpoint_connectivity_verified = False
        self._checkpoint_last_error: str | None = None
        if self.enabled:
            try:
                from filelock import FileLock, Timeout
                from langgraph.checkpoint.sqlite import SqliteSaver
                from langgraph.graph import END, START, StateGraph
                from langgraph.types import Command, interrupt
                from langsmith import tracing_context
                if self.settings.checkpoint_backend == 'postgres':
                    from langgraph.checkpoint.postgres import PostgresSaver
                    import psycopg
                    from psycopg.rows import dict_row
            except ImportError as exc:
                if self.settings.checkpoint_backend == 'postgres':
                    raise PlanningError('PostgreSQL checkpoints require the agents and enterprise dependencies') from exc
                raise PlanningError('LangGraph planning requires the agents dependencies') from exc
            self.FileLock, self.Timeout = FileLock, Timeout
            self.SqliteSaver, self.StateGraph = SqliteSaver, StateGraph
            self.START, self.END, self.Command, self.interrupt = START, END, Command, interrupt
            self.tracing_context = tracing_context
            self.PostgresSaver = PostgresSaver if self.settings.checkpoint_backend == 'postgres' else None
            self.psycopg = psycopg if self.settings.checkpoint_backend == 'postgres' else None
            self._postgres_dict_row = dict_row if self.settings.checkpoint_backend == 'postgres' else None
            # Persistent checkpoints must never permit an ambient process setting to widen decoding.
            os.environ['LANGGRAPH_STRICT_MSGPACK'] = 'true'
            self.root.mkdir(parents=True, exist_ok=True)
            self._initialize_run_registry()

    def status_view(self):
        planner = self.service.story_planner
        capable = planner is None or all(callable(getattr(planner, name, None)) for name in ('plan_bible', 'plan_storyboard'))
        return {'enabled': self.enabled, 'configured': self.enabled and capable,
                'engine': 'langgraph' if self.enabled else 'legacy', 'version': self.version,
                'compatible_graph_versions': [self.version, *self.compatible_migrations],
                'explicit_migrations': dict(self.compatible_migrations),
                'checkpoint_backend': self.settings.checkpoint_backend,
                'shared_checkpoint': self.settings.checkpoint_backend == 'postgres',
                'locking': 'postgres-advisory' if self.settings.checkpoint_backend == 'postgres' else 'local-file',
                'deployment': 'shared-checkpoint-prerequisites-met' if self.settings.checkpoint_backend == 'postgres' else 'single-control-plane',
                'checkpoint_connectivity_verified': self._checkpoint_connectivity_verified,
                'checkpoint_last_error': self._checkpoint_last_error,
                'stages': ['retrieve', 'story', 'storyboard', 'compile', 'review'],
                'max_revisions': 3, 'model_mode': 'deterministic' if planner is None else 'external'}

    def _require_enabled(self):
        if not self.status_view()['configured']:
            raise PlanningError('staged planning is disabled or planner stages are unavailable')

    @contextmanager
    def _database(self):
        if self.settings.checkpoint_backend == 'postgres':
            with self.psycopg.connect(self.settings.database_url, row_factory=self._postgres_dict_row) as conn:
                yield conn
            return
        with closing(sqlite3.connect(self.database, timeout=10)) as conn:
            with conn:
                yield conn

    def _query(self, statement: str) -> str:
        return statement if self.settings.checkpoint_backend == 'sqlite' else statement.replace('?', '%s')

    def _execute(self, conn, statement: str, parameters=()):
        return conn.execute(self._query(statement), parameters)

    def _initialize_run_registry(self):
        statements = [
            f'''CREATE TABLE IF NOT EXISTS {self.run_table} (
                run_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL, status TEXT NOT NULL,
                seed_json TEXT NOT NULL, error TEXT, finished_at DOUBLE PRECISION
            )''',
            f'''CREATE UNIQUE INDEX IF NOT EXISTS {self.run_table}_one_active
                ON {self.run_table}(tenant_id, project_id) WHERE finished_at IS NULL''',
        ]
        with self._database() as conn:
            for statement in statements:
                self._execute(conn, statement)

    def probe(self) -> dict[str, Any]:
        """Verify the active Checkpointer without exposing database details."""
        if not self.enabled:
            return {**self.status_view(), 'reachable': False}
        try:
            if self.settings.checkpoint_backend == 'postgres':
                with self._database() as conn:
                    self._execute(conn, 'SELECT 1').fetchone()
                with self.PostgresSaver.from_conn_string(self.settings.database_url) as saver:
                    saver.setup()
            else:
                with self._database() as conn:
                    self._execute(conn, 'SELECT 1').fetchone()
            self._checkpoint_initialized = True
            self._checkpoint_connectivity_verified = True
            self._checkpoint_last_error = None
        except Exception:
            self._checkpoint_connectivity_verified = False
            self._checkpoint_last_error = 'planning checkpoint probe failed'
        return {**self.status_view(), 'reachable': self._checkpoint_connectivity_verified}

    def _scope(self, project_id):
        try:
            project = self.service._project(project_id)
        except KeyError as exc:
            raise PlanningNotFound('project not found') from exc
        return project.brief.tenant_id, project_id

    def has_active(self, project_id):
        if not self.enabled:
            return False
        with self._database() as conn:
            return bool(self._execute(conn, f'SELECT 1 FROM {self.run_table} WHERE tenant_id=? AND project_id=? AND finished_at IS NULL', self._scope(project_id)).fetchone())

    def _row(self, project_id, run_id):
        self._require_enabled()
        with self._database() as conn:
            if self.settings.checkpoint_backend == 'sqlite':
                conn.row_factory = sqlite3.Row
            row = self._execute(conn, f'SELECT * FROM {self.run_table} WHERE tenant_id=? AND project_id=? AND run_id=?', (*self._scope(project_id), run_id)).fetchone()
        if row is None:
            raise PlanningNotFound('planning run not found')
        return dict(row)

    @contextmanager
    def _lock(self, project_id):
        self._require_enabled()
        scope = fingerprint(self._scope(project_id))
        if self.settings.checkpoint_backend == 'postgres':
            advisory_key = int.from_bytes(bytes.fromhex(scope)[:8], byteorder='big', signed=True)
            connection = self.psycopg.connect(self.settings.database_url, autocommit=True)
            try:
                acquired = connection.execute('SELECT pg_try_advisory_lock(%s)', (advisory_key,)).fetchone()[0]
                if not acquired:
                    raise PlanningBusy('planning operation is already running')
                yield
            finally:
                try:
                    if 'acquired' in locals() and acquired:
                        connection.execute('SELECT pg_advisory_unlock(%s)', (advisory_key,))
                finally:
                    connection.close()
            return
        try:
            with self.FileLock(str(self.root / f'{scope}.lock'), timeout=0):
                yield
        except self.Timeout as exc:
            raise PlanningBusy('planning operation is already running') from exc

    def _input_fingerprint(self, project_id):
        project = self.service._project(project_id)
        value = self.service._project_record(project)
        return fingerprint({key: value[key] for key in ('brief', 'story_bible', 'narrative_events', 'adaptation_scenes', 'shots', 'reference_assets', 'final_mp4', 'release', 'archived_at')})

    @contextmanager
    def mutation_guard(self, project_id):
        scope = self._mutation_scope.get()
        if scope and scope['active'] and scope['project_id'] == project_id:
            yield
            return
        if (not self.status_view()['configured'] or
                any(job.spec.project_id == project_id for job in self.service.jobs.all())):
            yield
            return
        with self._lock(project_id):
            # AnyIO propagates this request context to the sync endpoint thread.
            scope = {'project_id': project_id, 'active': True}
            token = self._mutation_scope.set(scope)
            try:
                yield
            finally:
                scope['active'] = False
                self._mutation_scope.reset(token)

    def _planner_fingerprint(self, graph_version=None):
        status = self.service.planner_status()
        return fingerprint({'graph': graph_version or self.version,
                            **{key: status.get(key) for key in ('mode', 'name', 'model', 'base_url')}})

    def _seed_graph_version(self, seed):
        # Version v2 did not persist this field. Its checkpoint schema is the sole supported legacy shape.
        return str(seed.get('graph_version') or 'mediaforge-planning-graph-v2')

    def _require_current_graph_version(self, seed):
        graph_version = self._seed_graph_version(seed)
        if graph_version != self.version:
            if graph_version in self.compatible_migrations:
                raise PlanningError('planning run requires migration before it can be resumed or reviewed')
            raise PlanningError('planning run uses an unsupported graph version; create a new run')

    def _check_project_current(self, project_id, seed, subject):
        project = self.service._project(project_id)
        self.service._ensure_active(project)
        if project.release or any(runtime.current_artifact for runtime in project.shots.values()) or any(job.spec.project_id == project_id for job in self.service.jobs.all()):
            raise PlanningError('staged planning requires a project with no generation jobs; create a branch')
        if self._input_fingerprint(project_id) != seed['input_fingerprint']:
            raise PlanningError('project changed after planning started; cancel this draft and create a new run')
        self._check_sources(project_id, seed['source_project_ids'], subject)

    def _check_sources(self, project_id, sources, subject):
        project = self.service._project(project_id)
        for source_id in sources:
            candidate = self.service.projects.get(source_id)
            if (not candidate or candidate.brief.tenant_id != project.brief.tenant_id or
                    (subject is not None and self.service.project_member_role(source_id, subject) not in self.service.project_role_levels())):
                raise PlanningNotFound('memory source project not found')

    def _check_current(self, project_id, seed, subject):
        self._require_current_graph_version(seed)
        self._check_project_current(project_id, seed, subject)
        if self._planner_fingerprint() != seed['planner_fingerprint']:
            raise PlanningError('planner configuration changed; cancel this draft and create a new run')

    def _update(self, run_id, status, error=None, terminal=False):
        with self._database() as conn:
            self._execute(conn, f'UPDATE {self.run_table} SET status=?,error=?,updated_at=?,finished_at=? WHERE run_id=?',
                          (status, error, time.time(), time.time() if terminal else None, run_id))

    @staticmethod
    def _trace(state, stage, started):
        return state.get('trace', []) + [{'stage': stage, 'revision': state.get('revision', 0),
                                         'completed_at': time.time(), 'duration_ms': round((time.monotonic() - started) * 1000)}]

    def _builder(self, project_id, subject):
        builder = self.StateGraph(PlanningState)

        def retrieve(state):
            started = time.monotonic()
            brief = CreativeBrief.model_validate(state['brief'])
            query = f'{brief.premise} {brief.genre} {brief.style}'[:3000]
            memory = self.service.story_memory_search(project_id, query, source_project_ids=state['source_project_ids'], subject=subject)['results'] if self.service.memory_settings.enabled and self.service.story_planner is not None else []
            return {'memory': memory, 'trace': self._trace(state, 'retrieve', started)}

        def story(state):
            started = time.monotonic()
            brief = CreativeBrief.model_validate(state['brief'])
            planner = self.service.story_planner
            bible = planner.plan_bible(brief, state['memory']) if planner else {
                'theme': brief.genre, 'logline': brief.premise[:2000],
                'characters': [{'name': name, 'role': '\u4e3b\u89d2', 'constraints': ['\u4fdd\u6301\u5916\u89c2\u4e00\u81f4']} for name in brief.characters],
                'scenes': [{'scene': '\u5f00\u573a', 'summary': brief.premise[:2000]}],
            }
            bible = ScriptPackage.model_validate(bible).model_dump(mode='json')
            if sorted(item['name'] for item in bible['characters']) != sorted(brief.characters):
                raise StoryPlannerError('script characters must match the creative brief')
            bible['narrative_event_context'] = self.service._narrative_event_context(
                self.service._project(project_id)
            )
            bible['adaptation_scene_context'] = self.service._adaptation_scene_context(
                self.service._project(project_id)
            )
            return {'bible': bible, 'trace': self._trace(state, 'story', started)}

        def storyboard(state):
            started = time.monotonic()
            brief = CreativeBrief.model_validate(state['brief'])
            planner = self.service.story_planner
            shots = planner.plan_storyboard(brief, state['bible'], state.get('feedback', '')) if planner else self.service._build_shots(brief)
            narrative_events = self.service.approved_narrative_events(project_id)
            adaptation_scenes = self.service.approved_adaptation_scenes(project_id)
            if narrative_events or adaptation_scenes:
                shots = self.service._build_shots(
                    brief,
                    narrative_events=narrative_events,
                    adaptation_scenes=adaptation_scenes,
                )
            self.service._validate_story_plan(brief, shots)
            return {'shots': [shot.model_dump(mode='json') for shot in shots], 'decision': '',
                    'trace': self._trace(state, 'storyboard', started)}

        def compile_specs(state):
            started = time.monotonic()
            runtimes, reports = self.service._prepare_planned_shots(self.service._project(project_id), [ShotCard.model_validate(shot) for shot in state['shots']])
            if any(not report['passed'] for report in reports):
                raise PlanningError('planned shots failed policy checks')
            return {'specs': [runtime.spec.model_dump(mode='json') for runtime in runtimes.values()], 'policy_reports': reports,
                    'trace': self._trace(state, 'compile', started)}

        def review(state):
            decision = self.interrupt({'stage': 'review', 'run_id': state['run_id'], 'revision': state.get('revision', 0)})
            if not isinstance(decision, dict) or decision.get('decision') not in {'approve', 'revise', 'reject'}:
                raise PlanningError('invalid planning review decision')
            revision = state.get('revision', 0)
            if decision['decision'] == 'revise':
                if revision >= 3 or not decision.get('comment', '').strip():
                    raise PlanningError('revision requires feedback and at most three revision rounds')
                revision += 1
            trace = self._trace(state, 'review', time.monotonic())
            trace[-1].update({'decision': decision['decision'], 'comment': decision.get('comment', ''), 'actor': decision['actor']})
            return {'decision': decision['decision'], 'feedback': decision.get('comment', ''), 'reviewer': decision['actor'],
                    'revision': revision, 'trace': trace}

        for name, node in [('retrieve', retrieve), ('story', story), ('storyboard', storyboard), ('compile', compile_specs), ('review', review)]:
            builder.add_node(name, node)
        builder.add_edge(self.START, 'retrieve')
        for left, right in [('retrieve', 'story'), ('story', 'storyboard'), ('storyboard', 'compile'), ('compile', 'review')]:
            builder.add_edge(left, right)
        builder.add_conditional_edges('review', lambda state: 'storyboard' if state['decision'] == 'revise' else self.END)
        return builder

    @contextmanager
    def _graph(self, project_id, subject=None):
        if self.settings.checkpoint_backend == 'postgres':
            with self.PostgresSaver.from_conn_string(self.settings.database_url) as saver:
                try:
                    with self._checkpoint_setup_lock:
                        if not self._checkpoint_initialized:
                            saver.setup()
                            self._checkpoint_initialized = True
                except Exception:
                    self._checkpoint_connectivity_verified = False
                    self._checkpoint_last_error = 'planning checkpoint operation failed'
                    raise
                self._checkpoint_connectivity_verified = True
                self._checkpoint_last_error = None
                yield self._builder(project_id, subject).compile(checkpointer=saver)
            return
        with closing(sqlite3.connect(self.database, timeout=10, check_same_thread=False)) as conn:
            saver = self.SqliteSaver(conn)
            saver.setup()
            self._checkpoint_connectivity_verified = True
            self._checkpoint_last_error = None
            yield self._builder(project_id, subject).compile(checkpointer=saver)

    def list_runs(self, project_id):
        self._scope(project_id)
        if not self.enabled:
            return {'settings': self.status_view(), 'runs': []}
        with self._database() as conn:
            rows = self._execute(conn, f'SELECT run_id FROM {self.run_table} WHERE tenant_id=? AND project_id=? ORDER BY created_at DESC LIMIT 30', self._scope(project_id)).fetchall()
        return {'settings': self.status_view(), 'runs': [self.view(project_id, row['run_id'] if isinstance(row, dict) else row[0]) for row in rows]}

    def view(self, project_id, run_id):
        row = self._row(project_id, run_id)
        seed = json.loads(row['seed_json'])
        with self._graph(project_id) as graph:
            snapshot = graph.get_state({'configurable': {'thread_id': run_id}})
        state = snapshot.values or seed
        status = row['status']
        if status == 'RUNNING':
            try:
                with self._lock(project_id):
                    status = 'INTERRUPTED'
            except PlanningBusy:
                pass
        return {'run_id': run_id, 'project_id': project_id, 'created_at': row['created_at'], 'updated_at': row['updated_at'],
                'status': status, 'error': row['error'], 'next_stages': list(snapshot.next),
                'graph_version': self._seed_graph_version(seed),
                'migration_required': self._seed_graph_version(seed) != self.version,
                'migration_history': seed.get('migration_history', []),
                'revision': state.get('revision', 0), 'story_bible': state.get('bible'), 'shots': state.get('shots', []),
                'specs': state.get('specs', []), 'trace': state.get('trace', []), 'feedback': state.get('feedback', ''),
                'memory_sources': [{key: hit.get(key) for key in ('project_id', 'kind', 'sha256', 'embedding_fingerprint')} for hit in state.get('memory', [])]}

    def start(self, project_id, sources=None, subject=None):
        self._require_enabled()
        with self._lock(project_id):
            if self.has_active(project_id):
                raise PlanningBusy('an active planning draft already exists')
            sources = list(dict.fromkeys(sources or [project_id]))
            if len(sources) > 20:
                raise PlanningError('at most 20 memory source projects are allowed')
            project = self.service._project(project_id)
            seed = {'run_id': uuid4().hex, 'brief': project.brief.model_dump(mode='json'),
                    'graph_version': self.version,
                    'input_fingerprint': self._input_fingerprint(project_id), 'planner_fingerprint': self._planner_fingerprint(),
                    'source_project_ids': sources, 'revision': 0, 'feedback': '', 'trace': []}
            self._check_current(project_id, seed, subject)
            with self._database() as conn:
                self._execute(conn, f'INSERT INTO {self.run_table} VALUES(?,?,?,?,?,?,?,?,NULL)',
                              (seed['run_id'], *self._scope(project_id), time.time(), time.time(), 'RUNNING', json.dumps(seed), None))
            self._drive(project_id, seed['run_id'], seed, subject)
        return self.view(project_id, seed['run_id'])

    def _drive(self, project_id, run_id, value, subject):
        try:
            self._update(run_id, 'RUNNING')
            with self.tracing_context(enabled=False), self._graph(project_id, subject) as graph:
                graph.invoke(value, {'configurable': {'thread_id': run_id}, 'recursion_limit': 32})
                snapshot = graph.get_state({'configurable': {'thread_id': run_id}})
            if snapshot.next == ('review',):
                self._update(run_id, 'AWAITING_REVIEW')
            elif snapshot.values.get('decision') == 'reject':
                self._update(run_id, 'REJECTED', terminal=True)
            else:
                self._update(run_id, 'READY_TO_APPLY')
        except Exception as exc:
            category = 'retrieval' if isinstance(exc, MemoryUnavailable) else 'model' if isinstance(exc, StoryPlannerError) else 'validation'
            self._update(run_id, 'FAILED', f'planning {category} stage failed')

    def resume(self, project_id, run_id, subject=None):
        with self._lock(project_id):
            row = self._row(project_id, run_id)
            if row['status'] not in {'FAILED', 'RUNNING'}:
                raise PlanningError('only a failed or interrupted planning run can be resumed')
            seed = json.loads(row['seed_json'])
            self._check_current(project_id, seed, subject)
            self._update(run_id, 'RUNNING')
            with self._graph(project_id, subject) as graph:
                saved = graph.get_state({'configurable': {'thread_id': run_id}})
            self._drive(project_id, run_id, None if saved.values else seed, subject)
        return self.view(project_id, run_id)

    def migrate(self, project_id, run_id, actor='planning-operator', subject=None):
        """Migrate a verified legacy v2 checkpoint without re-running completed stages."""
        self._require_enabled()
        with self._lock(project_id):
            row = self._row(project_id, run_id)
            seed = json.loads(row['seed_json'])
            source_version = self._seed_graph_version(seed)
            if source_version == self.version:
                return self.view(project_id, run_id)
            target_version = self.compatible_migrations.get(source_version)
            if target_version != self.version:
                raise PlanningError('planning run uses an unsupported graph version; create a new run')
            if seed.get('planner_fingerprint') != self._planner_fingerprint(source_version):
                raise PlanningError('planner configuration changed; cancel this draft and create a new run')
            self._check_project_current(project_id, seed, subject)
            with self._graph(project_id, subject) as graph:
                snapshot = graph.get_state({'configurable': {'thread_id': run_id}})
            if snapshot.values and snapshot.values.get('run_id') not in {None, run_id}:
                raise PlanningError('planning checkpoint identity is invalid')
            if any(node not in self.checkpoint_nodes for node in snapshot.next):
                raise PlanningError('planning checkpoint contains unsupported stages; create a new run')
            history = list(seed.get('migration_history', []))
            history.append({
                'from_version': source_version,
                'to_version': self.version,
                'migrated_at': time.time(),
                'actor': actor,
            })
            migrated = {
                **seed,
                'graph_version': self.version,
                'planner_fingerprint': self._planner_fingerprint(),
                'migration_history': history[-20:],
            }
            with self._database() as conn:
                self._execute(conn, f'UPDATE {self.run_table} SET seed_json=?,updated_at=? WHERE run_id=?',
                              (json.dumps(migrated), time.time(), run_id))
        return self.view(project_id, run_id)

    def review(self, project_id, run_id, decision, comment, actor, subject=None):
        with self._lock(project_id):
            row = self._row(project_id, run_id)
            if row['status'] == 'APPROVED' and decision == 'approve':
                return self.view(project_id, run_id)
            repairing = row['status'] == 'FAILED' and decision == 'revise'
            if row['status'] not in {'AWAITING_REVIEW', 'READY_TO_APPLY'} and not repairing:
                raise PlanningError('planning run is not awaiting review')
            project = self.service._project(project_id)
            # Project state is the commit receipt if the process stopped before updating the run registry.
            if (project.story_bible or {}).get('planning_run_id') == run_id:
                self._update(run_id, 'APPROVED', terminal=True)
                return self.view(project_id, run_id)
            self._check_current(project_id, json.loads(row['seed_json']), subject)
            with self._graph(project_id, subject) as graph:
                state = graph.get_state({'configurable': {'thread_id': run_id}}).values
            if decision not in {'approve', 'revise', 'reject'}:
                raise PlanningError('invalid planning review decision')
            if decision == 'revise' and self.service.story_planner is None:
                raise PlanningError('automatic draft revision requires an external staged planner')
            if decision == 'revise' and (not comment.strip() or state.get('revision', 0) >= 3):
                raise PlanningError('revision requires feedback and at most three revision rounds')
            if repairing:
                if not state.get('bible'):
                    raise PlanningError('a validated story is required before revising the storyboard')
                trace = self._trace(state, 'review', time.monotonic())
                trace[-1].update({'decision': 'revise', 'comment': comment, 'actor': actor})
                with self._graph(project_id, subject) as graph:
                    graph.update_state({'configurable': {'thread_id': run_id}}, {
                        'feedback': comment, 'decision': 'revise', 'revision': state.get('revision', 0) + 1,
                        'reviewer': actor, 'trace': trace,
                    }, as_node='review')
                self._drive(project_id, run_id, None, subject)
            elif row['status'] == 'AWAITING_REVIEW':
                self._drive(project_id, run_id, self.Command(resume={'decision': decision, 'comment': comment, 'actor': actor}), subject)
            elif decision != 'approve':
                raise PlanningError('approved graph output can only be applied or canceled')
            row = self._row(project_id, run_id)
            if row['status'] == 'READY_TO_APPLY':
                with self._graph(project_id, subject) as graph:
                    state = graph.get_state({'configurable': {'thread_id': run_id}}).values
                try:
                    self.service._adopt_planning_draft(project_id, state, actor)
                except PlanningError:
                    raise
                except Exception as exc:
                    self._update(run_id, 'READY_TO_APPLY', 'planning commit failed')
                    raise PlanningError('planning commit failed; retry approval') from exc
                self._update(run_id, 'APPROVED', terminal=True)
        return self.view(project_id, run_id)

    def cancel(self, project_id, run_id):
        with self._lock(project_id):
            row = self._row(project_id, run_id)
            if row['finished_at'] is not None:
                raise PlanningError('completed planning runs cannot be canceled')
            if (self.service._project(project_id).story_bible or {}).get('planning_run_id') == run_id:
                raise PlanningError('an applied planning run cannot be canceled')
            self._update(run_id, 'CANCELED', terminal=True)
        return self.view(project_id, run_id)
